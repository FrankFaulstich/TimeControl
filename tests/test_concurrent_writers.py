"""
Issue #558: five things can write data.json, and none of them used to wait.

_save_data() writes through a temporary file and os.replace(), which is
atomic for readers - nobody ever sees half a document. That is a different
promise from not losing work. Two writers that each read the document, change
their copy and write it back leave only the second one's change; the first is
gone, and nothing anywhere says so.

The writers are real and concurrent: the interface, the MCP server, the REST
server, the SOAP server, and a second browser tab. They are separate
processes, so the tests below use separate processes too - a second
TimeTracker in this one would exercise a different thing.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
import unittest.mock

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(REPO)

from tt.TimeTracker import TimeTracker
from tt.filelock import LockTimeout


def _tasks_of(path, project="P"):
    with open(path, encoding='utf-8') as handle:
        document = json.load(handle)
    for entry in document["projects"]:
        if entry.get("main_project_name") == project:
            return sorted(task["task_name"] for task in entry["tasks"])
    return []


class TestTwoProcessesBothKeepTheirWork(unittest.TestCase):
    """
    The lost update itself, with two processes doing what the servers do.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.data = os.path.join(self.tmp, 'data.json')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        tracker = TimeTracker(file_path=self.data)
        tracker.add_main_project("P")

    def _writer(self, name, hold_first=False):
        """A separate process that adds one task."""
        script = textwrap.dedent("""
            import sys, time
            sys.path.insert(0, %r)
            from tt.TimeTracker import TimeTracker
            tracker = TimeTracker(file_path=%r)
            if %r:
                # Read the document, then dawdle before writing it back. This
                # is the window in which the other writer used to be lost.
                time.sleep(0.6)
            tracker.add_task("P", %r)
            print("done", flush=True)
        """) % (REPO, self.data, hold_first, name)
        return subprocess.Popen([sys.executable, '-c', script],
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)

    def test_neither_writer_loses_the_other(self):
        """
        Both add a task, overlapping. Before the lock the file ended up with
        whichever finished last and no sign of the other.
        """
        children = {"slow": self._writer("slow", hold_first=True),
                    "fast": self._writer("fast")}
        try:
            for name, child in children.items():
                gemeldet = child.stdout.readline().strip()
                if gemeldet != "done":
                    # Without this the failure reads "'' != 'done'", which
                    # says only that the child died, not why. What it died of
                    # is on its stderr and nowhere else.
                    child.wait(10)
                    self.fail("the %s writer did not finish:\n%s"
                              % (name, child.stderr.read().strip() or "(no output)"))
        finally:
            for child in children.values():
                child.stdout.close()
                child.stderr.close()
                child.kill()
                child.wait(10)
        self.assertEqual(_tasks_of(self.data), ["fast", "slow"])

    def test_a_writer_sees_what_the_other_wrote(self):
        """
        The other half: the second writer must be working from the document
        as it is, not as it was when its own process started.
        """
        first = TimeTracker(file_path=self.data)
        second = TimeTracker(file_path=self.data)
        first.add_task("P", "from first")
        second.add_task("P", "from second")
        self.assertEqual(_tasks_of(self.data), ["from first", "from second"])
        # And the loser of the old race can see it in its own memory too.
        self.assertEqual(
            sorted(t["task_name"] for t in second.data["projects"][0]["tasks"]),
            ["from first", "from second"])


class TestWhatTheLockDoesAndDoesNot(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.data = os.path.join(self.tmp, 'data.json')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.tracker = TimeTracker(file_path=self.data)
        self.tracker.add_main_project("P")

    def test_the_lock_sits_beside_the_document_it_guards(self):
        """
        Not the data file itself: _save_data() replaces that file, so a lock
        held on it would be held on an inode nobody writes to any more. And
        beside it rather than in the configuration directory, because the
        data file can be moved and the lock has to move with it.
        """
        self.assertEqual(self.tracker._lock_path(),
                         os.path.abspath(self.data) + '.lock')
        self.tracker.add_task("P", "T")
        self.assertTrue(os.path.exists(self.tracker._lock_path()))

    def test_two_documents_do_not_share_a_lock(self):
        other = TimeTracker(file_path=os.path.join(self.tmp, 'other.json'))
        self.assertNotEqual(self.tracker._lock_path(), other._lock_path())

    def test_reading_does_not_take_the_lock(self):
        """
        os.replace() already means a reader sees a whole document, and the
        interface reloads on every redraw - a lock there would make it fail
        whenever a writer was slow. So reload_data() must not want one.
        """
        with self.tracker.exclusive():
            # Held. A reader must still get through.
            other = TimeTracker(file_path=self.data)
            other.reload_data()
        self.assertEqual(other.data["projects"][0]["main_project_name"], "P")

    def test_a_nested_call_does_not_deadlock_on_itself(self):
        """
        start_work() stops whatever was running, and stop_work() writes too.
        Without the depth count the inner call would queue behind the outer
        one for the whole timeout and then raise.
        """
        self.tracker.add_task("P", "A")
        self.tracker.add_task("P", "B")
        self.tracker.start_work("P", "A")
        self.tracker.start_work("P", "B")     # stops A on the way
        running = [t for t in self.tracker.data["projects"][0]["tasks"]
                   for e in t["time_entries"] if "end_time" not in e]
        self.assertEqual([t["task_name"] for t in running], ["B"])

    def test_the_inner_call_does_not_discard_the_outer_one_s_work(self):
        """
        A nested call must not read the file again: the outer one has changes
        in memory that are not on disk yet.
        """
        with self.tracker.exclusive():
            self.tracker.data["projects"][0]["marker"] = "outer"
            self.tracker.add_task("P", "inner")
            self.assertEqual(self.tracker.data["projects"][0].get("marker"),
                             "outer")

    def test_a_writer_that_cannot_get_in_says_so(self):
        """
        The failure mode the fix introduces, and the right one: an error
        rather than carrying on and overwriting somebody's work.
        """
        held = threading.Event()
        release = threading.Event()

        def holder():
            with self.tracker.exclusive():
                held.set()
                release.wait(5)

        thread = threading.Thread(target=holder)
        thread.start()
        try:
            self.assertTrue(held.wait(5))
            other = TimeTracker(file_path=self.data)
            with self.assertRaises(LockTimeout):
                with other.exclusive(timeout=0.3):
                    pass
        finally:
            release.set()
            thread.join(5)


class TestASaveSurvivesAPassingReader(unittest.TestCase):
    """
    Windows will not replace a file that anybody has open.

    That is not a corner case here: reload_data() runs on every redraw and
    takes no lock, by design, and the three servers read the document too. A
    read lasts milliseconds, but a save landing inside one used to come back
    as PermissionError - "access is denied", the same words a real
    permission problem uses - and the save was lost.

    It only happens on Windows, which is not where these tests run, so the
    refusal is staged rather than provoked. The retry itself is the same code
    on every platform.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.data = os.path.join(self.tmp, 'data.json')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.tracker = TimeTracker(file_path=self.data)
        self.tracker.add_main_project("P")

    def _refuse_then_allow(self, times):
        """os.replace that answers 'access is denied' the first `times` calls."""
        echt = os.replace
        zaehler = {'n': 0}

        def ersatz(src, dst, *args, **kwargs):
            if zaehler['n'] < times:
                zaehler['n'] += 1
                raise PermissionError(5, 'Access is denied')
            return echt(src, dst, *args, **kwargs)

        return ersatz, zaehler

    def test_a_save_refused_once_still_happens(self):
        ersatz, zaehler = self._refuse_then_allow(1)
        with unittest.mock.patch('tt.TimeTracker.os.replace', ersatz):
            self.tracker.add_task("P", "T")
        self.assertEqual(zaehler['n'], 1, 'the refusal was not staged')
        self.assertEqual(_tasks_of(self.data), ["T"])

    def test_and_one_refused_repeatedly_still_gets_through(self):
        ersatz, _ = self._refuse_then_allow(8)
        with unittest.mock.patch('tt.TimeTracker.os.replace', ersatz):
            self.tracker.add_task("P", "T")
        self.assertEqual(_tasks_of(self.data), ["T"])

    def test_a_refusal_that_never_lifts_is_reported(self):
        """
        The other half. Retrying for ever would turn a real permission
        problem into an application that has simply stopped.
        """
        self.tracker.REPLACE_RETRY_SECONDS = 0.2
        with unittest.mock.patch('tt.TimeTracker.os.replace',
                                 side_effect=PermissionError(5, 'Access is denied')):
            with self.assertRaises(PermissionError):
                self.tracker.add_task("P", "T")

    def test_it_gives_up_at_about_the_time_it_says(self):
        self.tracker.REPLACE_RETRY_SECONDS = 0.3
        begonnen = time.monotonic()
        with unittest.mock.patch('tt.TimeTracker.os.replace',
                                 side_effect=PermissionError(5, 'Access is denied')):
            with self.assertRaises(PermissionError):
                self.tracker.add_task("P", "T")
        gedauert = time.monotonic() - begonnen
        self.assertGreaterEqual(gedauert, 0.3)
        self.assertLess(gedauert, 3.0, 'it kept trying far past its deadline')

    def test_nothing_is_left_lying_around_when_it_gives_up(self):
        """
        The temporary file has to go, or a save that keeps failing fills the
        directory with them.
        """
        self.tracker.REPLACE_RETRY_SECONDS = 0.1
        with unittest.mock.patch('tt.TimeTracker.os.replace',
                                 side_effect=PermissionError(5, 'Access is denied')):
            with self.assertRaises(PermissionError):
                self.tracker.add_task("P", "T")
        leftovers = [n for n in os.listdir(self.tmp) if n.startswith('.tmp_data_')]
        self.assertEqual(leftovers, [])

    def test_an_ordinary_save_does_not_wait_at_all(self):
        """The retry must cost nothing when there is nothing to retry."""
        echt = os.replace
        aufrufe = []
        def zaehlend(*args, **kwargs):
            aufrufe.append(1)
            return echt(*args, **kwargs)
        with unittest.mock.patch('tt.TimeTracker.os.replace', zaehlend):
            self.tracker.add_task("P", "T")
        self.assertEqual(len(aufrufe), 1)


if __name__ == '__main__':
    unittest.main()
