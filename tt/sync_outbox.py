"""
The outgoing queue of operations waiting to reach the server.

Every change made locally is recorded here as an intention - "set the
priority of task X to 3" - and stays until the server has acknowledged it.
That is what lets the app be used offline and catch up later, and it is why
the queue has to survive a restart.

WHERE IT LIVES
--------------
Beside the credential, in the per-user configuration directory, not beside
data.json. Two reasons. It is per-machine state - "what has THIS machine not
sent yet" means nothing on another one - and the data file's location is a
setting the user can point at a shared or cloud-synced folder, where a queue
would be picked up by a second machine and replayed as if it were its own.

THE SEQUENCE NUMBER
-------------------
Each operation carries an `lc` that must rise strictly and never repeat for
this device: the server treats anything at or below what it has already seen
from a device as a repeat and drops it. That is exactly the behaviour that
makes a lost response harmless - and exactly what turns a duplicate number
into silent data loss. Since the GUI and the MCP/REST/SOAP servers can all be
appending at once, the number is handed out under a lock rather than from a
counter held in one process's memory.

The counter cannot be derived from the queue alone. A successful sync empties
it, and the next number would then start again at one - which the server has
already seen and would discard, silently, for ever after. So the high-water
mark is kept in a file of its own, beside the queue and written under the
same lock, and survives the queue being emptied.
"""

import json
import os

from tt.filelock import locked, LockTimeout
from tt import sync_client


def outbox_path():
    return os.path.join(sync_client.config_dir(), 'sync_outbox.jsonl')


def _lock_path():
    return os.path.join(sync_client.config_dir(), 'sync_outbox.lock')


def _highwater_path():
    return os.path.join(sync_client.config_dir(), 'sync_outbox.hw')


class OutboxFull(RuntimeError):
    """Raised when the queue has grown past the point of being useful."""


# A queue this long means syncing has been failing for a very long time.
# Growing without limit would turn a broken connection into a filled disk,
# and a push that can never fit in one request into a permanent blockage.
MAX_PENDING = 20000


class Outbox:
    """
    Append-only queue of operations awaiting acknowledgement.

    Reading and writing are cheap enough to do per change: the queue only
    holds what has not been sent, which under normal use is a handful of
    lines drained every few minutes.
    """

    def __init__(self, path=None, lock_path=None, highwater_path=None):
        self.path = path or outbox_path()
        self.lock_path = lock_path or _lock_path()
        self.highwater_path = highwater_path or _highwater_path()

    # -- reading ---------------------------------------------------------

    def pending(self):
        """
        Returns the queued operations in the order they were made.

        Sorted by 'lc' rather than left in file order. The server stamps a
        batch in the order it receives it and then refuses anything at or
        below the highest number it has seen from this device - so sending
        5 before 3 would make 3 look like a repeat and lose it.

        A line that will not parse is skipped rather than raising. The queue
        is appended to by several processes and a machine can be switched off
        mid-write; one damaged line should cost that one change, not the
        ability to sync at all.
        """
        out = []
        try:
            with open(self.path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(entry, dict) and 'lc' in entry:
                        out.append(entry)
        except OSError:
            return []
        out.sort(key=lambda e: int(e.get('lc', 0)))
        return out

    def count(self):
        return len(self.pending())

    # -- the counter -----------------------------------------------------

    def _read_highwater(self):
        try:
            with open(self.highwater_path, 'r', encoding='utf-8') as f:
                return int((f.read() or '0').strip() or 0)
        except (OSError, ValueError):
            return 0

    def _write_highwater(self, value):
        tmp = self.highwater_path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            f.write(str(int(value)))
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, self.highwater_path)

    # -- writing ---------------------------------------------------------

    def append(self, op, **fields):
        """
        Adds one operation to the queue and returns the number it was given.

        :param op: One of the operation names the server accepts.
        :raises OutboxFull: when the queue has grown implausibly long.
        :raises LockTimeout: when another process holds the queue too long.
        """
        # The directory of the queue itself, not the configuration directory:
        # the two are the same in the app, but a caller that passed its own
        # path meant that path.
        os.makedirs(os.path.dirname(self.path) or '.', exist_ok=True)
        with locked(self.lock_path):
            existing = self.pending()
            if len(existing) >= MAX_PENDING:
                raise OutboxFull(
                    "%d operations are waiting to be sent" % len(existing))

            # Read from disk, not from memory, because the next append may
            # well come from a different process - and taken from the
            # high-water mark as well as the queue, because a successful sync
            # empties the queue and the number must not start over.
            next_lc = max(
                self._read_highwater(),
                max((int(e.get('lc', 0)) for e in existing), default=0),
            ) + 1

            entry = {'op': op, 'lc': next_lc}
            entry.update({k: v for k, v in fields.items() if v is not None})

            # The mark is raised before the line is written. Should the write
            # fail, a number is skipped - which costs nothing, since the
            # server only requires them to rise. The reverse order could hand
            # the same number out twice.
            self._write_highwater(next_lc)
            with open(self.path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(entry, ensure_ascii=False) + '\n')
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
        return next_lc

    def extend(self, operations, allow_overflow=False):
        """
        Adds many operations at once, numbered consecutively.

        The same work as calling append() in a loop, but the lock is taken
        once and the queue is read once, instead of once per operation. That
        matters for the one caller that has thousands of them - offering an
        existing document to an empty server - where the loop would be
        quadratic and would freeze the interface for minutes.

        :param operations: Dicts with an 'op' key and the operation's fields.
        :param allow_overflow: Accept the batch even if it takes the queue
                    past the limit. The limit exists to stop a queue growing
                    without bound while syncing is broken; describing an
                    existing document for a server that has never seen it is
                    the opposite - a single finite batch that then drains. A
                    long history could exceed the limit, and refusing it would
                    mean that machine's document is never offered at all,
                    silently, with nothing anywhere to say why.
        :return: The numbers handed out.
        :raises OutboxFull: when the queue would grow past the limit.
        """
        operations = list(operations)
        if not operations:
            return []
        os.makedirs(os.path.dirname(self.path) or '.', exist_ok=True)
        with locked(self.lock_path):
            existing = self.pending()
            if not allow_overflow and len(existing) + len(operations) > MAX_PENDING:
                raise OutboxFull(
                    "%d operations would exceed the queue limit" % len(operations))

            next_lc = max(
                self._read_highwater(),
                max((int(e.get('lc', 0)) for e in existing), default=0),
            ) + 1

            lines = []
            numbers = []
            for offset, op in enumerate(operations):
                fields = dict(op)
                name = fields.pop('op')
                entry = {'op': name, 'lc': next_lc + offset}
                entry.update({k: v for k, v in fields.items() if v is not None})
                lines.append(json.dumps(entry, ensure_ascii=False))
                numbers.append(entry['lc'])

            self._write_highwater(numbers[-1])
            with open(self.path, 'a', encoding='utf-8') as f:
                f.write('\n'.join(lines) + '\n')
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
        return numbers

    def drop(self, acknowledged_lcs):
        """
        Removes operations the server has confirmed.

        Rewrites the file rather than truncating it, because acknowledgement
        does not have to arrive in order: a batch can be partly accepted and
        partly reported as already known.
        """
        done = set(int(x) for x in acknowledged_lcs)
        if not done:
            return 0
        with locked(self.lock_path):
            keep = [e for e in self.pending() if int(e.get('lc', 0)) not in done]
            tmp = self.path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                for entry in keep:
                    f.write(json.dumps(entry, ensure_ascii=False) + '\n')
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            os.replace(tmp, self.path)
        return len(done)

    def clear(self):
        """
        Discards everything queued. Used when a machine is re-seeded.

        The high-water mark is deliberately left alone: the server still
        remembers the numbers this device has used, and starting over would
        make everything sent afterwards look like a repeat.
        """
        with locked(self.lock_path):
            try:
                os.remove(self.path)
            except OSError:
                pass


def default_outbox_if_enabled(config):
    """
    Returns an Outbox when synchronisation is switched on, otherwise None.

    Keeping the decision here means TimeTracker does not have to know how the
    setting is spelled, and that an absent 'sync' key - every installation
    that predates this feature - simply means off.
    """
    if not isinstance(config, dict):
        return None
    sync_cfg = config.get('sync')
    if not isinstance(sync_cfg, dict) or not sync_cfg.get('enabled'):
        return None
    return Outbox()
