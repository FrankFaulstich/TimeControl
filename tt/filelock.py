"""
A cross-platform advisory file lock.

This exists because several processes write TimeControl's data at the same
time - the Streamlit GUI plus whichever of the MCP, REST and SOAP servers are
running - and they all share one outgoing operation queue. Two of them
handing the same sequence number to two different operations would make the
server treat the second as a repeat and drop it, losing a change without a
word.

Python has no portable file lock in its standard library, so this is a thin
shim over the two platform mechanisms. Deliberately thin: the alternative was
another dependency, and the surface needed here is one context manager.

Advisory means it only works between cooperating processes. That is enough:
every writer goes through this module.

Threads of one process count as separate writers here, because they are: the
sync worker runs in a background thread while the interface draws in another,
and both write the queue. On POSIX that falls out of flock, which attaches to
the open file description, and each caller below opens its own. Windows byte
range locks are not described that way, and the one place it matters is the
one platform this could not be tried on - so the promise is not left to the
operating system at all. See _in_process_lock().
"""

import os
import threading
import time
from contextlib import contextmanager

if os.name == 'nt':
    import msvcrt
else:
    import fcntl


class LockTimeout(RuntimeError):
    """Raised when the lock could not be taken within the deadline."""


# One lock per lock file, for the threads of this process. The registry has to
# be guarded itself, or two threads arriving at once each make their own.
_in_process_locks = {}
_registry_guard = threading.Lock()


def _in_process_lock(path):
    """
    The lock the threads of this process queue on before touching the file.

    Keyed so that two callers spelling the same lock file differently still
    meet on the same lock. abspath alone is not enough for that: it does not
    follow symbolic links, and on macOS the temporary directory is reached
    through one - /var is a link to /private/var - so a relative and an
    absolute name for one file come out as two keys, and the two threads
    holding them would not see each other. normcase then settles Windows,
    where the same file may be named in any case.
    """
    key = os.path.normcase(os.path.realpath(path))
    with _registry_guard:
        lock = _in_process_locks.get(key)
        if lock is None:
            lock = _in_process_locks[key] = threading.Lock()
        return lock


@contextmanager
def locked(path, timeout=5.0):
    """
    Holds an exclusive lock on `path` for the duration of the block.

    Never blocks indefinitely. A caller that waits forever on a lock some
    crashed process appears to hold would freeze the interface it runs
    behind; failing lets the caller retry on the next cycle instead.

    :param path: Lock file. Created if absent; never deleted, because
                 removing it would let another process take a lock on a file
                 this one still holds open.
    :param timeout: Seconds to keep trying before giving up.
    :raises LockTimeout: if the lock could not be acquired in time.
    """
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)

    deadline = time.monotonic() + timeout

    # The threads of this process first. Held for as long as the file lock is,
    # so the file lock below only ever has one thread of ours to consider and
    # the two mechanisms cannot disagree.
    mine = _in_process_lock(path)
    if not mine.acquire(timeout=max(0.0, deadline - time.monotonic())):
        raise LockTimeout("could not lock %s within %.1fs" % (path, timeout))

    try:
        handle = open(path, 'a+b')
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

        while True:
            try:
                if os.name == 'nt':
                    # Windows locks byte ranges rather than whole files, so
                    # one fixed byte stands in for the file. LK_NBLCK is the
                    # non-blocking form.
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    handle.close()
                    raise LockTimeout("could not lock %s within %.1fs"
                                      % (path, timeout))
                time.sleep(0.02)

        try:
            yield
        finally:
            try:
                if os.name == 'nt':
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                # Closing the handle releases it anyway, on both platforms.
                pass
            handle.close()
    finally:
        # Every way out of the block above passes here, including the timeout
        # on the file lock: a thread that gave up must not leave the other
        # threads of this process queueing behind it for ever.
        mine.release()
