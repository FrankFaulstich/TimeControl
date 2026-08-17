"""
A record of what synchronisation actually did, for when it did not.

Synchronisation is deliberately quiet. It runs in the background, reports
almost nothing, and the one thing it must never do is interrupt the user - so
when two machines end up disagreeing, there is nothing on screen that says
why. This is where you look instead.

OFF UNLESS ASKED FOR
--------------------
It writes nothing until it is switched on in the settings, and it defaults to
off. A diagnostic log is a file on disk that keeps a record of what somebody
was doing and when, and nobody should get one they did not ask for.

WHAT IS AND IS NOT WRITTEN
--------------------------
Counts, codes, sequence numbers and uids. Never a project name, a task name,
a note, a username or the address of the server - the log is meant to be
readable by whoever is helping you, and a list of everything you worked on
last month is not part of the question. A uid is opaque and enough to follow
one object through a sync, which is what the log is for.

NEVER AT THE EXPENSE OF THE THING IT WATCHES
--------------------------------------------
Every call swallows its own failures. A log that cannot be written is worth
strictly less than the synchronisation it is watching, and a full disk must
not be able to stop the user tracking time.
"""

import os
import threading
from datetime import datetime

from tt import sync_client

# One rollover, then the older half is discarded. Diagnosing a sync needs the
# recent past, not all of it, and this has to be safe to leave switched on.
MAX_BYTES = 512 * 1024

# The interesting fields are short - counts, codes, sequence numbers. A line
# far longer than this is a sign something unexpected got in, and truncating
# beats filling the disk with it.
MAX_LINE = 2000

_enabled = False
_writing = threading.Lock()


def path():
    """Beside the other per-machine sync state, never in the project folder."""
    return os.path.join(sync_client.config_dir(), 'sync.log')


def _rolled_path():
    return path() + '.1'


def configure(config):
    """
    Switches the log on or off from the stored settings.

    Call it wherever the rest of the sync settings are read, so the flag
    cannot drift from what the settings screen shows.

    :return: Whether logging is now on.
    """
    global _enabled
    sync_cfg = config.get('sync') if isinstance(config, dict) else None
    _enabled = bool(isinstance(sync_cfg, dict) and sync_cfg.get('log_enabled'))
    return _enabled


def is_enabled():
    return _enabled


def log(event, **fields):
    """
    Records one thing that happened.

    :param event: A short stable name - 'cycle.ok', 'apply.done', 'reset'.
                  Stable so a log can be searched rather than read.
    :param fields: Counts, codes, sequence numbers, uids. Anything a person
                   typed belongs nowhere near this.
    """
    if not _enabled:
        return
    try:
        line = '%s  %-18s %s' % (
            datetime.now().isoformat(timespec='milliseconds'),
            event,
            ' '.join('%s=%s' % (k, fields[k]) for k in sorted(fields)),
        )
        _append(line.rstrip()[:MAX_LINE])
    except Exception:
        # Same rule as everywhere else in the sync code: this may cost the
        # log, never the synchronisation and never the application.
        pass


def _append(line):
    with _writing:
        target = path()
        os.makedirs(os.path.dirname(target) or '.', exist_ok=True)
        try:
            if os.path.getsize(target) >= MAX_BYTES:
                os.replace(target, _rolled_path())
        except OSError:
            pass
        # Opened and closed per line rather than held open: several processes
        # write here, and a handle kept across a rollover would go on filling
        # a file that is no longer the log.
        with open(target, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass


def tail(lines=200):
    """
    The most recent entries, oldest first, for showing in the settings screen.

    Reads the rolled-over half too, so the view does not go suddenly empty
    the moment the log rolls.
    """
    out = []
    for candidate in (_rolled_path(), path()):
        try:
            with open(candidate, 'r', encoding='utf-8', errors='replace') as f:
                out.extend(f.read().splitlines())
        except OSError:
            continue
    return out[-lines:]


def clear():
    """Discards the log. Both halves."""
    with _writing:
        for candidate in (path(), _rolled_path()):
            try:
                os.remove(candidate)
            except OSError:
                pass


def size():
    """Total bytes on disk, for telling the user what it is costing them."""
    total = 0
    for candidate in (path(), _rolled_path()):
        try:
            total += os.path.getsize(candidate)
        except OSError:
            pass
    return total
