"""
Keeps the garbage collector's hands off the document the interface holds
(issue #642).

Streamlit runs a full collection after every run of the script, fragment runs
included (its hidden option runner.postScriptGC), and a full collection visits
every object the collector tracks. On Python 3.14 that is every dict, so most
of them are the document - every project, task and time entry: some 245,000
of 330,000 at the large test size, and 87-147 ms of every run, where a click
is often two runs. Earlier versions do not track a dict that holds only
strings and numbers, every time entry among them; there the document is the
smaller part, and the collection took 27-32 ms.

Switching the collection off would save that, and cost 100-120 MB that kept
growing. So instead what is alive is taken out of its sight: once a tracker
has read a document, everything is collected once and then frozen.
gc.freeze() moves it all out of the collector's generations, and the
collection after each run only visits what came after - under 5 ms.

Reference counting goes on as before: an object is freed the moment the last
reference to it goes, frozen or not. The document has no reference cycles, so
the one a new read replaces is freed at once. What a freeze does keep is
cyclic garbage - something frozen while in use that later becomes part of a
cycle nobody reaches, such as the module Streamlit ran the freezing run in,
or a closed browser session - and with it whatever that still refers to, even
what was made after the freeze. All of it waits for the next freeze, which
begins by unfreezing and collecting everything. So what can pile up is what
one freeze kept, and it goes at the next; the interface reads at least every
TimeTracker.TRUST_UNCHANGED_SECONDS while it is in use. Two hours of a full
run every five seconds, with a write from another process every minute, ended
at 305 MB against 308 MB without freezing.

Only the interface does this. The servers build a tracker per request, and a
full collection for each would cost them what it saves here. And it is kept
here rather than in sl/SL_Menu.py, which Streamlit runs as a new module every
time: what this remembers has to last as long as the process.
"""

import gc
import threading
import weakref

_lock = threading.Lock()
# Each tracker's read_count when it was last frozen. Weak, so that a closed
# session's tracker is not kept for the sake of a number - and keyed by the
# tracker itself, not by its id(), which the next session's may reuse.
_frozen_at = weakref.WeakKeyDictionary()


def freeze_after_read(tracker):
    """
    Freezes what is alive if `tracker` has read a document since it was last
    called with it, and does nothing otherwise.

    Called on every run of the interface, so that is the usual case, and a
    cheap one. When it does freeze, it pays for one full collection - the one
    the end of the run then no longer has to.

    Nor does it freeze a document the tracker cannot vouch for, which the
    next look reads again anyway: on a file system that reports no inode
    numbers, is_current() is never true, and the sweep later in the same run
    would replace what was frozen before the run's collection came round.

    :param tracker: the TimeTracker of the session that is running.
    :return: True if it froze.
    """
    with _lock:
        if _frozen_at.get(tracker) == tracker.read_count:
            return False
        if not tracker.is_current():
            return False
        gc.unfreeze()
        gc.collect()
        gc.freeze()
        _frozen_at[tracker] = tracker.read_count
        return True
