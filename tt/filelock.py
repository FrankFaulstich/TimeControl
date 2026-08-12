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
"""

import os
import time
from contextlib import contextmanager

if os.name == 'nt':
    import msvcrt
else:
    import fcntl


class LockTimeout(RuntimeError):
    """Raised when the lock could not be taken within the deadline."""


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

    handle = open(path, 'a+b')
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass

    deadline = time.monotonic() + timeout
    while True:
        try:
            if os.name == 'nt':
                # Windows locks byte ranges rather than whole files, so one
                # fixed byte stands in for the file. LK_NBLCK is the
                # non-blocking form.
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except OSError:
            if time.monotonic() >= deadline:
                handle.close()
                raise LockTimeout("could not lock %s within %.1fs" % (path, timeout))
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
