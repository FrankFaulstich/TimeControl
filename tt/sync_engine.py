"""
Keeping the machines in step, without anyone waiting for it.

WHAT RUNS WHERE, AND WHY IT IS SPLIT
------------------------------------
The work divides into a slow half and a fast half, and they must not happen
in the same place.

*The slow half is the network.* It runs in one background thread per process
and touches nothing but the outgoing queue and its own state files. It never
opens data.json and never calls into Streamlit. What it fetches is written to
an inbox on disk and left there.

*The fast half is applying what arrived.* It runs on the thread that draws
the interface, at the top of a redraw, on the document that thread already
holds. That is the whole reason for the split: the interface keeps its
document in memory between redraws, so a background thread writing the file
would be overwritten by the next thing the user did - a lost update that
reports nothing. Applying on the drawing thread, into the document it is
already holding, makes that impossible rather than unlikely.

The inbox is durable, so a machine switched off between the two halves loses
nothing: the operations are on disk and are applied on the next start.

WHY NOT SIMPLY CALL IT WHEN THE VIEW CHANGES
--------------------------------------------
That is how the update check works, and for a once-per-view version check a
pause is tolerable. Here it is not: a sync runs every few minutes, and a
server that has gone away would stall every navigation for the length of the
timeout. Hence a thread, and hence nothing in the interface ever waiting on
it.

ONE CYCLE AT A TIME, ACROSS PROCESSES
-------------------------------------
The GUI, the MCP server and the REST server are separate processes sharing
one queue. Two of them pushing at once would interleave their batches, so a
cycle holds a lock; whoever cannot take it skips that round rather than
waiting.
"""

import contextlib
import json
import os
import threading
import time
from datetime import datetime

from tt import sync_client
from tt.filelock import locked, LockTimeout
from tt import sync_log
from tt.sync_apply import Report, adopt_snapshot, reconcile, seed_operations
from tt.sync_outbox import Outbox

# How long between cycles when everything is working. The user asked for
# "several minutes": long enough that this is invisible, short enough that
# moving between machines over a coffee break does not need a nudge.
DEFAULT_INTERVAL_MINUTES = 5

# After a failure, back off rather than hammering a server that is down or
# a connection that is not there. Doubles each time up to the ceiling.
BACKOFF_START_SECONDS = 60
BACKOFF_MAX_SECONDS = 30 * 60

# How often the worker wakes to see whether anything is due. Short enough to
# react to a nudge promptly, long enough to cost nothing.
TICK_SECONDS = 2.0

# Failures that will not fix themselves by trying again. Retrying an address
# that is not a sync server, or a token the server has revoked, for ever is
# just noise - the user has to do something.
TERMINAL_ERRORS = frozenset((
    'not_signed_in', 'invalid_token', 'https_required',
    'not_installed', 'bad_response', 'tls_failed',
    # The address in the settings is not the one the token belongs to. Only
    # signing in again resolves that, so asking again sooner changes nothing.
    'address_changed',
))


def state_path():
    return os.path.join(sync_client.config_dir(), 'sync_state.json')


def inbox_path():
    return os.path.join(sync_client.config_dir(), 'sync_inbox.jsonl')


def _cycle_lock_path():
    return os.path.join(sync_client.config_dir(), 'sync_cycle.lock')


def _state_lock_path():
    return os.path.join(sync_client.config_dir(), 'sync_state.lock')


def _seed_lock_path():
    return os.path.join(sync_client.config_dir(), 'sync_seed.lock')


def staged_snapshot_path():
    """
    Where the drawing thread leaves a document for the worker to offer.

    The two halves keep their usual division of labour: reading the document
    belongs to the thread that owns it, and talking to the server belongs to
    the worker. A snapshot needs both, so it goes through a file - the same
    arrangement the inbox already uses in the other direction.
    """
    return os.path.join(sync_client.config_dir(), 'sync_snapshot.json')


# ---------------------------------------------------------------------------
# State: what this machine knows about its own position in the log.
# ---------------------------------------------------------------------------

_DEFAULT_STATE = {
    'base_seq': 0,        # the last sequence number applied to data.json
    'seeded': False,      # whether this machine has offered its document
    'last_ok': None,      # epoch seconds of the last successful cycle
    'last_error': None,   # the code from the last failure, or None
    'failures': 0,        # consecutive failures, for the backoff
    'next_attempt': 0,    # epoch seconds before which not to try again
    'server_head': 0,     # how far the log had got when last asked
    'account': None,      # whose log base_seq is measured against
    'was_off': False,     # synchronisation was switched off since the last offer
    'snapshot_seq': 0,      # the point the server's snapshot covers, 0 for none
    'snapshot_staged': 0,   # a document waiting to be offered, by sequence number
    'snapshot_tried': 0,    # epoch seconds of the last offer, successful or not
    # The address the settings ask for, recorded by ensure_started. Kept here
    # rather than read from config.json because this half of the module never
    # opens that file - and because every process that runs a cycle has to
    # reach the same verdict, not just the one holding the configuration.
    'configured_url': None,
}


def read_state():
    """Returns the stored state, filled out with defaults."""
    state = dict(_DEFAULT_STATE)
    try:
        with open(state_path(), 'r', encoding='utf-8') as f:
            stored = json.load(f)
        if isinstance(stored, dict):
            state.update({k: v for k, v in stored.items() if k in _DEFAULT_STATE})
    except (OSError, ValueError):
        pass
    return state


def write_state(changes, required=False):
    """
    Merges changes into the stored state.

    Read-modify-write under a lock, because the worker and the drawing thread
    both update it and they change different fields: the worker owns the
    outcome of a cycle, the drawing thread owns how far the document has been
    brought. A blind overwrite would lose one or the other.

    :param required: Raise instead of shrugging when the write fails. Most of
                     what is kept here is a convenience - when the last cycle
                     ran, what went wrong - and losing it costs nothing. The
                     cursor is not: consuming the fetched operations while
                     failing to record how far they reached would leave the
                     cursor behind what the document already holds, and the
                     same operations would be fetched and replayed over newer
                     work. The caller passes this so that failure aborts the
                     whole step and the operations stay where they are.
    """
    os.makedirs(sync_client.config_dir(), exist_ok=True)
    try:
        with locked(_state_lock_path()):
            state = read_state()
            state.update(changes)
            tmp = state_path() + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(state, f, indent=2)
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            os.replace(tmp, state_path())
            return state
    except (OSError, LockTimeout):
        if required:
            raise
        # State is a convenience, not the truth. The queue and the log are
        # the truth, and both survive this going wrong.
        return read_state()


# ---------------------------------------------------------------------------
# The inbox: fetched operations waiting to be applied to the document.
#
# The worker appends to it and the drawing thread consumes it, so both ends
# go through the same lock. Reading and then removing what was read has to be
# one indivisible step: a record the worker adds in between would be deleted
# without ever being applied, while the cursor moved past it. Nothing would
# report that, and the two machines would simply stop agreeing.
# ---------------------------------------------------------------------------

def _inbox_lock_path():
    return os.path.join(sync_client.config_dir(), 'sync_inbox.lock')


def _read_inbox_unlocked():
    out = []
    try:
        with open(inbox_path(), 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict) and 'ops' in record:
                    out.append(record)
    except OSError:
        return []
    return out


def read_inbox():
    """Returns the pending records, oldest first. Never raises."""
    return _read_inbox_unlocked()


def _append_inbox(record):
    os.makedirs(sync_client.config_dir(), exist_ok=True)
    with locked(_inbox_lock_path()):
        with open(inbox_path(), 'a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')
        try:
            os.chmod(inbox_path(), 0o600)
        except OSError:
            pass


def clear_inbox():
    """Discards everything waiting. Used when a machine is re-seeded."""
    with locked(_inbox_lock_path()):
        _remove_inbox()


def _remove_inbox():
    try:
        os.remove(inbox_path())
    except OSError:
        pass


@contextlib.contextmanager
def taken_inbox():
    """
    Hands over the pending records and removes exactly those.

    The lock is held for the whole block, so a record the worker files while
    the document is being brought up to date survives to the next round
    rather than being swept away with the ones that were applied. The body is
    local work only - never a network call - so the wait is imperceptible.

    If the body raises, nothing is removed and the records are applied again
    next time. Applying twice is harmless; losing them is not.
    """
    with locked(_inbox_lock_path()):
        records = _read_inbox_unlocked()
        yield records
        if not records:
            return
        remaining = _read_inbox_unlocked()[len(records):]
        if remaining:
            tmp = inbox_path() + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                for record in remaining:
                    f.write(json.dumps(record, ensure_ascii=False) + '\n')
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            os.replace(tmp, inbox_path())
        else:
            _remove_inbox()


# ---------------------------------------------------------------------------
# The slow half: one network cycle.
# ---------------------------------------------------------------------------

def run_cycle(outbox=None):
    """
    Sends what is queued, fetches what is not, and files it for applying.

    Deliberately never touches data.json. Everything it learns goes into the
    inbox; the drawing thread puts it into the document.

    :return: A dict with 'ok', and on failure an 'error' code.
    """
    outbox = outbox or Outbox()

    try:
        with locked(_cycle_lock_path(), timeout=0.1):
            return _run_cycle_locked(outbox)
    except LockTimeout:
        # Another process is mid-cycle. Its work is our work.
        sync_log.log('cycle.skipped', why='another process is syncing')
        return {'ok': True, 'skipped': 'busy'}
    except OSError as exc:
        sync_log.log('cycle.local_io', detail=type(exc).__name__)
        return {'ok': False, 'error': 'local_io', 'detail': str(exc)}


def _since(state):
    """
    Where to ask from.

    Not simply the applied position: operations already fetched but not yet
    applied are sitting in the inbox, and asking for them again would fetch
    the same batch every cycle until the interface next redraws.
    """
    highest = int(state.get('base_seq', 0))
    for record in read_inbox():
        highest = max(highest, int(record.get('base_seq', 0)))
    return highest


# A stop on the catch-up loop. Only reachable if the server keeps reporting
# more than it sends; without it a bad answer would spin here for ever.
MAX_PAGES_PER_CYCLE = 40


def _current_account():
    creds = sync_client.load_credentials() or {}
    return creds.get('username')


def _log_is_not_the_one_we_know(state, head):
    """
    Whether the cursor still refers to the log the server is answering from.

    Two ways it stops doing so, and both are quiet:

    The log is shorter than the position we hold. A log only grows, so a head
    below our cursor means this is a different log - the account was
    re-created, the store was wiped, or the server was rebuilt. Left alone,
    every cycle asks for operations after a point the log will never reach,
    the server rightly answers with nothing, and the machine reports success
    for ever while sending and receiving nothing at all.

    The credential belongs to somebody else. Signing in to a second account
    leaves a cursor measured against the first one's log.

    Both are rare, and both are indistinguishable from working correctly from
    the outside, which is exactly why they are worth detecting rather than
    leaving to be noticed months later.
    """
    if head < int(state.get('base_seq', 0)):
        return True
    account = _current_account()
    known = state.get('account')
    return bool(account) and known is not None and account != known


def _drop_inbox_for_reset():
    """Fetched operations numbered against a log that is no longer there."""
    try:
        clear_inbox()
    except (OSError, LockTimeout):
        pass


def _run_cycle_locked(outbox):
    state = read_state()

    # Before anything is sent. The token belongs to the server that issued it,
    # and the setting can have been pointed somewhere else since - at a
    # different machine, or at a typo. Carrying on would keep synchronising
    # with the old address while the settings screen showed the new one, and
    # nothing would say so; following the setting instead would send a bearer
    # credential to whatever host it now names. Neither is acceptable, so this
    # stops and says why. See sync_client.address_changed().
    configured = state.get('configured_url')
    if configured and sync_client.address_changed(configured):
        sync_log.log('address.changed')
        return _record_failure('address_changed')

    since = _since(state)

    sending = outbox.pending()
    # By size, not just by count. The server reads a bounded amount of request
    # body and silently treats anything longer as an empty request - accepted,
    # acknowledged, and carrying nothing - so a batch that is too large does
    # not fail, it disappears.
    wire = [_wire(op) for op in sending]
    fitted = sync_client.fit_batch(wire)
    batch = sending[:len(fitted)]

    sync_log.log('push', since=since, sending=len(batch),
                 queued=len(sending),
                 bytes=sum(len(json.dumps(o, ensure_ascii=False)) for o in fitted))
    result = sync_client.push(since, fitted)
    if not result.get('ok'):
        sync_log.log('push.failed', error=result.get('error') or 'unreachable')
        return _record_failure(result.get('error') or 'unreachable')

    if _log_is_not_the_one_we_know(state, int(result.get('head', 0))):
        # Start again from the beginning against this log. Everything below
        # depends on `since` pointing into the same log the server is
        # answering from, and it no longer does.
        sync_log.log('reset', was=int(state.get('base_seq', 0)),
                     head=int(result.get('head', 0)),
                     why='head_behind_cursor' if int(result.get('head', 0)) <
                         int(state.get('base_seq', 0)) else 'different_account')
        state = write_state({'base_seq': 0, 'seeded': False,
                             'account': _current_account()})
        _drop_inbox_for_reset()
        since = 0

    dups = [int(x) for x in (result.get('dups') or [])]
    seq_of = {int(lc): int(seq) for lc, seq in (result.get('assigned') or [])}
    head = int(result.get('head', 0))
    truncated = bool(result.get('more'))
    failure = None

    # The log no longer reaches back as far as this machine has got: what is
    # missing has been folded into a snapshot and the early segments retired.
    # The server sends no operations at all in that case rather than the part
    # that survives, because that part begins in the middle - so taking the
    # snapshot is the only way anything after it makes sense.
    if result.get('needs_snapshot') or int(result.get('snapshot_seq') or 0) > since:
        taken = _take_server_snapshot(since)
        if taken is None:
            return _record_failure('snapshot_unavailable')
        if taken:
            since = taken
            # Everything this machine needs now sits above the snapshot, and
            # the push reply was written before we had it. Draining below
            # fetches the tail, and unlike push it includes our own work.
            truncated = True

    if dups or truncated:
        # Two situations, one answer.
        #
        # Duplicates mean an earlier push landed but its answer never
        # arrived, so those operations are in the log at positions we were
        # never told. Truncation means the reply left some out, and this
        # machine's own operations sit above the cut - filing them now would
        # move the cursor past everything in the gap, and the log is only
        # ever read forwards, so that gap would never be offered again.
        #
        # In both cases the push reply is not a usable picture of the order,
        # and pull is: unlike push it includes this machine's own work, so
        # what comes back is the whole sequence, as the server has it.
        incoming = []
        cursor = since
        for _page in range(MAX_PAGES_PER_CYCLE):
            fetched = sync_client.pull(cursor)
            sync_log.log('pull', since=cursor,
                         got=len(fetched.get('ops') or []),
                         more=bool(fetched.get('more')),
                         error=fetched.get('error') or '-')
            if not fetched.get('ok'):
                # Keep the contiguous run we did get; the rest comes next
                # time. The cursor below advances only over what is in hand.
                failure = fetched.get('error') or 'unreachable'
                break
            page = fetched.get('ops') or []
            incoming.extend(page)
            head = max(head, int(fetched.get('head', 0)))
            cursor = max([int(op.get('s', 0)) for op in page] or [cursor])
            if not fetched.get('more'):
                truncated = False
                break
        else:
            truncated = True
    else:
        incoming = list(result.get('ops') or [])
        # Our own operations are not echoed back, but we now know where they
        # went - and they belong in the same ordered stream, above everything
        # that was already there.
        for op in batch:
            seq = seq_of.get(int(op.get('lc', 0)))
            if seq:
                incoming.append(dict(_wire(op), s=seq))

    # The cursor may only advance as far as we were actually given.
    highest_seen = max([int(op.get('s', 0)) for op in incoming] or [0])
    complete = not truncated and failure is None
    reached = max(head, highest_seen, since) if complete else max(highest_seen, since)

    if incoming or reached > since:
        _append_inbox({'base_seq': reached, 'ops': incoming})
        sync_log.log('filed', reached=reached, ops=len(incoming),
                     complete=complete)

    # Only now, once what came back is safely on disk - and only for what is
    # covered by it. An operation dropped from the queue before its place in
    # the order is recorded would be gone from both: the queue no longer has
    # it to replay, and the reply that carried it was never filed.
    acknowledged = [lc for lc, seq in seq_of.items() if seq <= reached]
    if complete:
        acknowledged.extend(dups)
    if acknowledged:
        outbox.drop(acknowledged)
        sync_log.log('acknowledged', lcs=len(acknowledged),
                     dups=len(dups), assigned=len(seq_of))

    if failure is not None:
        return _record_failure(failure)

    write_state({
        'last_ok': int(time.time()),
        'last_error': None,
        'failures': 0,
        'next_attempt': 0,
        'server_head': max(head, reached),
        'account': _current_account(),
    })
    # Last, and only after a cycle that got everything: the server takes a
    # snapshot only from a machine that is at head, and this is the one moment
    # this machine knows it was.
    _offer_staged_snapshot(max(head, reached), outbox)
    sync_log.log('cycle.ok', sent=len(batch), received=len(incoming),
                 head=head, reached=reached)
    return {'ok': True, 'sent': len(batch), 'received': len(incoming),
            'more': truncated or len(sending) > len(batch)}


def _wire(op):
    """Strips the queue's own bookkeeping down to what the server accepts."""
    allowed = ('op', 'lc', 'uid', 'f', 'ts', 'project', 'task', 'start', 'end')
    return {k: v for k, v in op.items() if k in allowed}


# ---------------------------------------------------------------------------
# Snapshots: taking one, and offering one.
#
# The server's log only ever grows. A snapshot is the document as it stood at
# one sequence number, so a machine joining late fetches that and then only
# the tail instead of replaying everything ever done.
#
# The server never builds one - it stores operations without understanding
# them, and teaching it to fold them into a document would put the merge rules
# in two languages where they could drift. So a client that has everything
# offers the document it already holds. Which means both halves of this module
# are involved: reading the document belongs to the drawing thread, talking to
# the server belongs to the worker, and a file passes it across.
# ---------------------------------------------------------------------------

def _take_server_snapshot(since):
    """
    Fetches the server's snapshot and files it for the drawing thread.

    :return: The sequence number it covers, 0 when there is nothing usable to
             take, or None when it could not be fetched - which is a failure
             worth backing off on, because without it nothing else this cycle
             would do makes sense.
    """
    reply = sync_client.get_snapshot()
    if not reply.get('ok'):
        if reply.get('error') == 'no_snapshot':
            # Told to fetch one, and there is none. A store rebuilt between
            # the two calls, or an installation in a state it should not be
            # in. Reporting it beats retrying for ever against a server that
            # will keep saying the same thing.
            sync_log.log('snapshot.missing')
            return 0
        sync_log.log('snapshot.fetch_failed', error=reply.get('error') or 'unreachable')
        return None

    seq = int(reply.get('seq') or 0)
    document = reply.get('document')
    if seq <= since or not isinstance(document, dict):
        sync_log.log('snapshot.unusable', seq=seq, since=since)
        return 0

    try:
        # Whatever was already waiting sits below this point, and applying it
        # after the snapshot would put older values on top of newer ones. The
        # snapshot speaks for all of it, so it replaces the queue rather than
        # joining the back of it. Nothing above the point can be in there:
        # this machine would not have been sent any.
        clear_inbox()
        _append_inbox({'base_seq': seq, 'snapshot': document, 'ops': []})
    except (OSError, LockTimeout):
        return None

    write_state({'snapshot_seq': seq})
    sync_log.log('snapshot.taken', seq=seq,
                 projects=len(document.get('projects') or []))
    return seq


def _read_staged_snapshot():
    try:
        with open(staged_snapshot_path(), 'r', encoding='utf-8') as handle:
            document = json.load(handle)
        return document if isinstance(document, dict) else None
    except (OSError, ValueError):
        return None


def _write_staged_snapshot(document):
    path = staged_snapshot_path()
    tmp = path + '.tmp'
    try:
        os.makedirs(sync_client.config_dir(), exist_ok=True)
        with open(tmp, 'w', encoding='utf-8') as handle:
            json.dump(document, handle, ensure_ascii=False)
        os.replace(tmp, path)
        return True
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False


def _discard_staged_snapshot():
    try:
        os.remove(staged_snapshot_path())
    except OSError:
        pass
    write_state({'snapshot_staged': 0})


def _offer_staged_snapshot(head, outbox):
    """
    Sends a document the drawing thread prepared, if it still describes head.

    Called only at the end of a cycle that got everything, and it checks
    again anyway: between the staging and here the log can have moved, and a
    snapshot that does not sit exactly at head is refused by the server -
    rightly, because its uploader cannot then have held all of it.
    """
    state = read_state()
    staged = int(state.get('snapshot_staged') or 0)
    if not staged:
        return

    try:
        still_queued = bool(outbox.pending())
    except Exception:
        still_queued = True
    if staged != head or still_queued:
        # Stale. Thrown away rather than sent, and the drawing thread will
        # prepare a fresh one from the document as it stands then.
        sync_log.log('snapshot.stale', staged=staged, head=head, queued=still_queued)
        _discard_staged_snapshot()
        return

    document = _read_staged_snapshot()
    if document is None:
        _discard_staged_snapshot()
        return

    reply = sync_client.put_snapshot(staged, document)
    if reply.get('ok'):
        write_state({'snapshot_seq': int(reply.get('snapshot_seq') or staged)})
        sync_log.log('snapshot.offered', seq=staged,
                     retired=int(reply.get('retired') or 0),
                     deleted=int(reply.get('deleted') or 0))
        _discard_staged_snapshot()
        return

    error = reply.get('error') or 'unreachable'
    sync_log.log('snapshot.refused', seq=staged, error=error)
    if error not in ('unreachable', 'timeout', 'busy'):
        # Either the timing was wrong - the log moved on, or another machine
        # got there first - or the document was refused on its own account.
        # Neither is worth keeping this file for: sending it again would fail
        # the same way, and the next attempt should describe the log as it
        # will be then. What stops that attempt from coming round on every
        # redraw is the clock in snapshot_tried.
        _discard_staged_snapshot()
    # Anything left is the network. The file stays, and the next complete
    # cycle offers it again as long as the log has not moved in the meantime.


def _record_failure(code):
    state = read_state()
    failures = int(state.get('failures', 0)) + 1
    if code in TERMINAL_ERRORS:
        # Nothing will change by asking again sooner. Wait out the longest
        # interval and let the user's next action - signing in, correcting
        # the address - be what resumes it.
        delay = BACKOFF_MAX_SECONDS
    else:
        delay = min(BACKOFF_START_SECONDS * (2 ** (failures - 1)), BACKOFF_MAX_SECONDS)
    write_state({
        'last_error': code,
        'failures': failures,
        'next_attempt': int(time.time()) + delay,
    })
    sync_log.log('backoff', error=code, failures=failures, wait_s=delay,
                 terminal=code in TERMINAL_ERRORS)
    return {'ok': False, 'error': code}


# ---------------------------------------------------------------------------
# The fast half: putting what arrived into the document.
# ---------------------------------------------------------------------------

def apply_pending(tracker):
    """
    Applies everything fetched so far to the tracker's document and saves it.

    Must be called on the thread that owns the document - in the interface,
    the one drawing it, immediately after it has reloaded from disk. It works
    on that thread's own document object, so the change is visible to the
    rest of the redraw and cannot be overwritten by it.

    :raises OSError: if the document cannot be saved. Nothing is consumed in
                     that case, so the next call tries again - which is why
                     the caller must not simply swallow it.
    :return: A summary dict, or None when there was nothing to do.
    """
    if not read_inbox():
        return None

    outbox = tracker.op_outbox or Outbox()

    try:
        with taken_inbox() as records:
            if not records:
                return None

            # Every record at once, in one pass. They are one stream of
            # operations that happens to have arrived in instalments, and
            # apply_ops orders by sequence number anyway. Applying them
            # record by record would replay this machine's own unsent work
            # once per record - and count the same discarded time entry once
            # per record along with it.
            incoming = [op for record in records for op in (record.get('ops') or [])]
            reached = max([int(record.get('base_seq', 0)) for record in records]
                          + [int(read_state().get('base_seq', 0))])

            local, settled = _split_placed(outbox.pending(), incoming)

            # A snapshot goes in first, and in a pass of its own. It stands
            # for everything up to its own sequence number, so applying it
            # after the operations that come later would lay an older picture
            # over a newer one. The separate pass is what orders it: apply_ops
            # sorts by sequence number within a call and remembers nothing
            # between calls, so being applied first is the ordering.
            report = Report()
            for record in sorted(records, key=lambda r: int(r.get('base_seq', 0))):
                if record.get('snapshot'):
                    report.absorb(adopt_snapshot(tracker.data, record['snapshot']))
            report.absorb(reconcile(tracker.data, incoming, local))

            # A session this machine had left running was ended because work
            # began elsewhere. That was worked out here, from the order alone,
            # so unless it is reported the other machines go on showing it as
            # running. Queued before the document is saved and before the
            # records are consumed: a machine switched off in between would
            # otherwise have the closure in its own file, no way to re-derive
            # it, and no way to pass it on.
            for entry_uid, end in report.auto_closed:
                tracker._emit('entry.close', uid=entry_uid, end=end)

            # Stamped into the document as well as into the state file. The
            # two travel differently: restoring data.json from a backup takes
            # the document back but leaves the state file where it was, and
            # the machine would then never re-fetch what the restored copy is
            # missing. align_cursor() below reads this back.
            tracker.data['_sync_seq'] = reached
            tracker._save_data()
            write_state({'base_seq': reached}, required=True)
            if settled:
                outbox.drop(settled)
    except LockTimeout:
        return None

    sync_log.log('applied', ops=report.applied, ignored=report.ignored,
                 discarded_time=report.discarded_time,
                 auto_closed=len(report.auto_closed), reached=reached)
    for entry_uid, end in report.auto_closed:
        sync_log.log('auto_closed', entry=entry_uid)
    return {'applied': report.applied, 'discarded_time': report.discarded_time,
            'auto_closed': len(report.auto_closed), 'base_seq': reached}


def _split_placed(queued, incoming):
    """
    Separates queued operations the log has already placed from the rest.

    The replay in reconcile() rests on one assumption: that what it is given
    is work the server has NOT yet ordered, so putting it above everything
    incoming matches where the server will put it. An operation that is both
    queued here AND present in what just arrived breaks that assumption - it
    already has a place, further down, and lifting it back to the top inverts
    the order. The other machine, replaying the same log, keeps the other
    value, and the two quietly stop agreeing.

    The queue and the log can drift apart for several dull reasons: a push
    whose reply was lost, a catch-up cut short before the queue was drained, a
    machine switched off between filing what arrived and clearing the queue.
    Rather than trying to close each of those windows, this asks the only
    question that matters - is this operation already in the log? - which the
    entries answer themselves, since each carries the device and number it was
    sent with.

    :return: (still unplaced, numbers now known to be in the log)
    """
    try:
        mine = sync_client.device_identity()['device_uid']
    except Exception:
        return list(queued), []

    placed = {int(op['lc']) for op in incoming
              if op.get('dev') == mine and op.get('lc') is not None}
    if not placed:
        return list(queued), []
    return ([op for op in queued if int(op.get('lc', 0)) not in placed],
            sorted(placed))


def align_cursor(tracker):
    """
    Brings the cursor back to what the document on disk actually contains.

    Call this on the drawing thread, before applying anything.

    The cursor lives in the state file, the document in data.json, and the two
    can be separated: restoring data.json from a backup - or copying yesterday's
    over today's - takes the document back while the state file stays where it
    was. Everything after that point has already been marked as applied, so it
    is never fetched again, and the restored copy silently stays missing
    whatever it was missing. The interface reports a healthy sync throughout.

    So the document carries its own mark, and the lower of the two wins. Going
    back over ground already covered costs nothing - applying an operation
    twice is harmless by design - while going forward over a gap cannot be
    undone.

    :return: The number of sequence positions given up, for the caller to log.
    """
    stamped = tracker.data.get('_sync_seq')
    if stamped is None:
        return 0
    state = read_state()
    behind = int(state.get('base_seq', 0)) - int(stamped)
    if behind <= 0:
        return 0
    write_state({'base_seq': int(stamped)})
    sync_log.log('cursor.rewound', to=int(stamped), gave_up=behind,
                 why='data.json is older than the recorded position')
    return behind


def offer_document(tracker):
    """
    Queues this machine's existing document the first time it reaches a server.

    Whoever gets there first fills an empty account; a machine joining later
    offers what it has too, and the operations settle by uid. That way a
    document built up before synchronisation was switched on is not quietly
    left behind - and re-offering the same objects costs nothing, because
    creating something that already exists does nothing.

    Done here rather than in the cycle because it reads the document, which
    belongs to this thread. Called on every redraw, so the ordinary case -
    already offered - has to be one cheap file read and nothing else.
    """
    if read_state().get('seeded'):
        return 0
    outbox = tracker.op_outbox
    if outbox is None or not sync_client.load_credentials():
        return 0

    try:
        # Re-checked under a lock, and the queueing and the mark made together
        # inside it. Two browser tabs redraw independently and would otherwise
        # both see "not offered yet" and both queue the whole document, which
        # is two copies of every operation the other machine has to chew
        # through. And were the mark written only afterwards, a machine shut
        # down midway would start over from the beginning every time it was
        # opened, until the queue filled up and every later change was
        # dropped in silence.
        with locked(_seed_lock_path()):
            if read_state().get('seeded'):
                return 0
            ops = seed_operations(tracker.data)
            if ops:
                # Over the queue limit if need be: a long history is a big
                # one-off batch, not a runaway, and it drains 500 at a time.
                outbox.extend(ops, allow_overflow=True)
            write_state({'seeded': True})
            sync_log.log('offered', ops=len(ops),
                         projects=len(tracker.data.get('projects', [])))
            return len(ops)
    except Exception:
        # Same reasoning as _emit: a queue that will not take a write costs a
        # sync, never the application. Nothing is marked, so this is tried
        # again on the next redraw.
        return 0


# How far the log may run past the snapshot before a new one is worth making.
# Four pages of catching up for a machine replaying the tail, which is quick,
# and on ordinary use somewhere around a fortnight - often enough that the log
# does not run away, rarely enough that a multi-megabyte upload is not a
# routine event.
SNAPSHOT_EVERY = 2000

# And a floor on how often this machine will try, whatever the numbers say. A
# snapshot that keeps being refused should not have the document written out
# again on every redraw.
SNAPSHOT_RETRY_SECONDS = 6 * 3600


def offer_snapshot(tracker):
    """
    Stages this machine's document for the worker to offer as a snapshot.

    Called on the drawing thread, beside offer_document, and for the same
    reason: it reads the document, which belongs to this thread. It does no
    network work of its own - it leaves a file, and the worker sends it at
    the end of a cycle that reached head.

    Every gate below is about one thing: only a machine that demonstrably
    holds the whole log may describe it to the others. Getting that wrong
    does not fail loudly, it publishes a wrong document as the truth.

    :return: The sequence number staged, or 0 when nothing was.
    """
    state = read_state()
    if int(state.get('snapshot_staged') or 0):
        return 0                                  # one is already waiting

    base = int(state.get('base_seq', 0))
    head = int(state.get('server_head', 0))
    if base <= 0 or base != head:
        return 0                                  # not caught up
    if head - int(state.get('snapshot_seq') or 0) < SNAPSHOT_EVERY:
        return 0                                  # the log has not run far enough
    if int(time.time()) - int(state.get('snapshot_tried') or 0) < SNAPSHOT_RETRY_SECONDS:
        return 0

    if read_inbox():
        return 0        # fetched but not yet applied: the document is behind
    if not sync_client.load_credentials():
        return 0

    outbox = tracker.op_outbox
    if outbox is None:
        return 0
    try:
        if outbox.pending():
            return 0    # unsent work, which will move head out from under this
    except Exception:
        return 0

    document = tracker.data
    if not document.get('projects'):
        # The server refuses this and is right to. A document with nothing in
        # it, offered for a log with something in it, is the shape of a
        # data.json that was emptied or replaced while the cursor stayed put -
        # and it would be handed to every other machine as the truth. Stopping
        # here saves a round trip and a refusal in the log every six hours.
        sync_log.log('snapshot.not_offered', why='no_projects')
        return 0

    if not _write_staged_snapshot(document):
        return 0
    write_state({'snapshot_staged': base, 'snapshot_tried': int(time.time())})
    sync_log.log('snapshot.staged', seq=base,
                 projects=len(document.get('projects') or []))
    return base


# ---------------------------------------------------------------------------
# The worker.
# ---------------------------------------------------------------------------

_worker = None
_worker_guard = threading.Lock()
_wake = threading.Event()

# Each worker gets a stop signal of its own rather than sharing one. A worker
# that is asked to stop while it happens to be inside a request cannot be
# waited for - the interface must not pause for a network timeout - so it is
# left to finish and die on its own. With a shared signal, starting the next
# worker would clear that signal and bring the abandoned one back to life,
# and two of them would then push the same queue.


def _blocked(state):
    """
    Whether a failure is still being waited out.

    This holds against a nudge as well as against the interval. A nudge comes
    from changing view, which happens constantly, and a server that is down
    or a token that has been revoked answers the same way every time - so
    without this, the backoff would exist on paper and every navigation would
    still go and ask.
    """
    return int(state.get('next_attempt', 0)) > time.time()


def _interval_elapsed(state):
    last = state.get('last_ok')
    if not last:
        return True
    return time.time() - float(last) >= _interval_seconds()


_interval_minutes = DEFAULT_INTERVAL_MINUTES


def _interval_seconds():
    return max(60, int(_interval_minutes) * 60)


def _loop(stopping):
    while not stopping.is_set():
        try:
            woken = _wake.is_set()
            _wake.clear()
            state = read_state()
            if not _blocked(state) and (woken or _interval_elapsed(state)):
                outcome = run_cycle()
                if stopping.is_set():
                    return
                if outcome.get('ok') and outcome.get('more'):
                    # A backlog too large for one exchange. Carry straight on
                    # rather than waiting out the interval between each batch,
                    # which would take hours to clear after a long absence.
                    _wake.set()
        except Exception:
            # The worker must outlive anything that goes wrong inside it.
            # A cycle that fails is one missed sync; a worker that dies is
            # no synchronisation at all until the app is restarted, with
            # nothing on screen to say so.
            pass
        stopping.wait(TICK_SECONDS)


def ensure_started(config=None):
    """
    Brings the worker into line with the setting: running, or not.

    Call this on every redraw, and call it whether or not synchronisation is
    switched on - it is what stops the worker as well as what starts it.
    Switching the feature off has to actually stop it: otherwise the thread
    goes on talking to the server with the stored token for as long as the
    application is open, filing operations into an inbox that nobody is
    reading any more, and the user who just turned it off has no way to tell.

    Safe to call constantly. Streamlit re-runs its script from the top on
    every redraw, so a guard placed in that script would start a thread per
    redraw; the guard lives here instead, in a module, which is imported once
    per process however many times the script above it runs.
    """
    global _worker, _interval_minutes

    if config is not None:
        sync_cfg = config.get('sync') if isinstance(config, dict) else None
        if not isinstance(sync_cfg, dict) or not sync_cfg.get('enabled'):
            stop()
            # Remember the gap. While the feature is off nothing is recorded -
            # that is what off means - so the changes made in between exist
            # nowhere but this machine. Switching it back on has to make the
            # machine describe itself again, or those changes are silently
            # absent from the other one for ever, and the other machine's
            # version of the same objects quietly wins.
            if not read_state().get('was_off'):
                write_state({'was_off': True})
            return False
        if read_state().get('was_off'):
            write_state({'was_off': False, 'seeded': False})
        # Recorded on every redraw, so the cycle can tell whether the address
        # it is about to use is still the one being asked for.
        state = read_state()
        wanted = (sync_cfg.get('base_url') or '').strip() or None

        # A disagreement can also end without the setting moving at all -
        # by signing in to the new server, which is the intended way out of
        # it. The credential is only read when there is such a failure on
        # record, so the ordinary redraw costs nothing.
        settled = (state.get('last_error') == 'address_changed'
                   and not sync_client.address_changed(wanted or ''))

        if state.get('configured_url') != wanted or settled:
            # The slate goes with it. What the last cycle concluded was about
            # an address that no longer applies, and for a wrong one that
            # included a pause measured in half hours. Left standing, the
            # interface would go on reporting a problem the user has already
            # corrected, and refuse to try again long after they did.
            write_state({'configured_url': wanted, 'last_error': None,
                         'failures': 0, 'next_attempt': 0})
        try:
            _interval_minutes = int(sync_cfg.get('interval_minutes')
                                    or DEFAULT_INTERVAL_MINUTES)
        except (TypeError, ValueError):
            _interval_minutes = DEFAULT_INTERVAL_MINUTES

    with _worker_guard:
        if _worker is not None and _worker.is_alive():
            return True
        stopping = threading.Event()
        # Daemon, because the interface exits with os._exit() and closing the
        # window terminates the process outright. Nothing here may delay that,
        # and nothing here needs to: every write it makes is atomic on its own.
        _worker = threading.Thread(target=_loop, args=(stopping,),
                                   name='tc-sync', daemon=True)
        _worker.stopping = stopping
        _worker.start()
        return True


def nudge(force=False):
    """
    Asks the worker to run a cycle now rather than at the next interval.

    :param force: Also cancels a failure the worker is waiting out. Only for
                  something the user did that could have fixed the cause -
                  signing in, correcting the address - never for a nudge the
                  interface generates on its own.
    """
    if force:
        write_state({'last_error': None, 'failures': 0, 'next_attempt': 0})
    _wake.set()


def stop():
    """
    Ends the worker if one is running.

    Cheap and safe to call when there is nothing to stop, which is what lets
    ensure_started() call it on every redraw where the feature is off.
    """
    global _worker
    with _worker_guard:
        worker = _worker
        _worker = None
    if worker is None:
        return
    worker.stopping.set()
    _wake.set()
    # Not joined for long. The thread may be inside a request, and the
    # interface must not wait out a network timeout to redraw; it is a daemon,
    # so an abandoned one dies with the process and every write it makes is
    # atomic on its own.
    worker.join(timeout=0.2)


def status_summary():
    """
    What the interface needs to show, without asking the network anything.

    Named for what it is rather than "snapshot", which in this module now
    means the document the server holds in place of the early log.

    :return: dict with 'state' as one of 'off', 'never', 'ok', 'failing';
             plus 'last_ok', 'error', 'pending' and 'incoming'.
    """
    state = read_state()
    try:
        pending = Outbox().count()
    except Exception:
        pending = 0
    incoming = len(read_inbox())

    if state.get('last_error'):
        kind = 'failing'
    elif state.get('last_ok'):
        kind = 'ok'
    else:
        kind = 'never'

    return {
        'state': kind,
        'last_ok': state.get('last_ok'),
        'error': state.get('last_error'),
        'failures': int(state.get('failures', 0)),
        'pending': pending,
        'incoming': incoming,
        'base_seq': state.get('base_seq', 0),
    }
