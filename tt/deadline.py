"""
A hard deadline around a call that may never return.

The network calls in this application all have timeouts of their own, and
none of those covers the step that hangs when there is no network at all: the
name lookup. requests' and imaplib's timeouts bound the socket once it knows
which address to talk to; socket.getaddrinfo() runs before that and, with DNS
packets silently dropped, can take minutes - the "the app hangs without an
internet connection" problem. A deadline around the whole call is the only
thing that bounds it.

Kept here, with nothing but the standard library, so that everything that
talks to a server can use the same one: the update check (update.py), the
synchronisation client (tt/sync_client.py, through update.py) and the e-mail
import (tt/TimeTracker.py, issue #667).
"""

import threading


def call_with_deadline(func, deadline, *args, **kwargs):
    """
    Runs ``func(*args, **kwargs)`` in a daemon thread and waits at most
    ``deadline`` seconds for it. Raises TimeoutError if it's still running
    after that.

    A daemon thread - not concurrent.futures.ThreadPoolExecutor - is
    deliberate: ThreadPoolExecutor registers its worker threads to be
    joined at interpreter exit, so a call that's genuinely stuck (e.g. DNS
    resolution that never gets a response) would still hang the whole app
    at shutdown even though we stopped waiting for it here. A daemon
    thread is hard-killed by the interpreter on exit instead, so a stuck
    lookup can never block the app from closing.

    :param func: The call to make.
    :param deadline: Seconds to wait for it at most.
    :return: Whatever func returned.
    :raises TimeoutError: if func had not returned by the deadline. It is
        left running in its thread and its result, should one come, is
        dropped.
    :raises: whatever func raised, if it raised in time.
    """
    result = {}

    def _target():
        try:
            result['value'] = func(*args, **kwargs)
        except BaseException as exc:
            result['error'] = exc

    worker = threading.Thread(target=_target, daemon=True)
    worker.start()
    worker.join(deadline)
    if worker.is_alive():
        raise TimeoutError(f"Operation timed out after {deadline}s")
    if 'error' in result:
        raise result['error']
    return result.get('value')
