import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(REPO)

from tt.filelock import locked, LockTimeout, _in_process_lock


class TestTwoThreadsOfOneProcess(unittest.TestCase):
    """
    Issue #556: the lock is used between threads, and that had only ever been
    tried on POSIX.

    The sync worker runs in a background thread while the interface draws in
    another, and both write the outgoing queue. On POSIX the exclusion falls
    out of flock, which attaches to the open file description - each call
    opens its own, so two threads exclude each other. Windows byte range locks
    are documented in terms of processes and handles, not open file
    descriptions, and the machine that would settle it is the one nobody could
    run this on.

    So the module no longer leaves it to the operating system: threads queue
    on a lock of their own first. These tests run on every platform CI covers,
    Windows included, which is what closes the gap the issue names.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.lock = os.path.join(self.tmp, 'q.lock')
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_a_second_thread_cannot_take_a_lock_that_is_held(self):
        holding = threading.Event()
        release = threading.Event()
        result = {}

        def halter():
            with locked(self.lock):
                holding.set()
                release.wait(5)

        thread = threading.Thread(target=halter)
        thread.start()
        try:
            self.assertTrue(holding.wait(5), 'the first thread never got the lock')
            with self.assertRaises(LockTimeout):
                with locked(self.lock, timeout=0.3):
                    result['taken'] = True
        finally:
            release.set()
            thread.join(5)
        self.assertNotIn('taken', result)

    def test_and_can_take_it_once_the_first_lets_go(self):
        """
        The other half. Without it, a lock that is never released would pass
        the test above and fail every user of the module.
        """
        holding = threading.Event()
        release = threading.Event()

        def halter():
            with locked(self.lock):
                holding.set()
                release.wait(5)

        thread = threading.Thread(target=halter)
        thread.start()
        self.assertTrue(holding.wait(5))
        release.set()
        thread.join(5)

        with locked(self.lock, timeout=2):
            pass          # no LockTimeout is the assertion

    def test_only_one_thread_at_a_time_is_ever_inside(self):
        """
        The property the queue actually depends on, put under contention:
        ten threads, and never two of them in the block together.
        """
        inside = []
        overlapped = []
        guard = threading.Lock()

        def arbeiten():
            for _ in range(5):
                with locked(self.lock, timeout=10):
                    with guard:
                        inside.append(1)
                        if len(inside) > 1:
                            overlapped.append(len(inside))
                    time.sleep(0.001)
                    with guard:
                        inside.pop()

        threads = [threading.Thread(target=arbeiten) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        self.assertEqual(overlapped, [], 'two threads held the lock at once')
        self.assertEqual(inside, [], 'a thread left the block without leaving')

    def test_a_thread_that_gave_up_does_not_block_the_others(self):
        """
        A timeout has to release what it took on the way in. If it did not,
        the first refusal would wedge every later caller in this process -
        and the interface behind them.
        """
        holding = threading.Event()
        release = threading.Event()

        def halter():
            with locked(self.lock):
                holding.set()
                release.wait(5)

        thread = threading.Thread(target=halter)
        thread.start()
        self.assertTrue(holding.wait(5))
        for _ in range(3):
            with self.assertRaises(LockTimeout):
                with locked(self.lock, timeout=0.1):
                    pass
        release.set()
        thread.join(5)

        with locked(self.lock, timeout=2):
            pass

    def test_the_same_file_spelled_differently_is_the_same_lock(self):
        """
        Two callers naming one lock file by different paths must still meet.
        The queue is reached by a relative path from one place and an absolute
        one from another.
        """
        holding = threading.Event()
        release = threading.Event()

        def halter():
            with locked(self.lock):
                holding.set()
                release.wait(5)

        thread = threading.Thread(target=halter)
        thread.start()
        try:
            self.assertTrue(holding.wait(5))
            umweg = os.path.join(self.tmp, '.', 'q.lock')
            with self.assertRaises(LockTimeout):
                with locked(umweg, timeout=0.3):
                    pass
        finally:
            release.set()
            thread.join(5)


class TestTwoProcesses(unittest.TestCase):
    """
    The case the module was written for, kept here beside the thread one so
    neither guarantee can be removed without the other being noticed.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.lock = os.path.join(self.tmp, 'q.lock')
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_a_second_process_cannot_take_a_lock_that_is_held(self):
        helper = textwrap.dedent("""
            import sys, time
            sys.path.insert(0, %r)
            from tt.filelock import locked
            with locked(%r):
                print("held", flush=True)
                time.sleep(30)
        """) % (REPO, self.lock)
        with subprocess.Popen([sys.executable, '-c', helper],
                              stdout=subprocess.PIPE, text=True) as child:
            try:
                self.assertEqual(child.stdout.readline().strip(), "held")
                with self.assertRaises(LockTimeout):
                    with locked(self.lock, timeout=0.5):
                        pass
            finally:
                child.kill()

class TestTheLockThreadsQueueOn(unittest.TestCase):
    """
    The registry itself, checked directly.

    It has to be, because on POSIX no test through locked() can tell this
    mechanism apart from flock: both exclude the two threads, so both make
    the same test pass. The one platform where the difference shows is the
    one that cannot be run here. Asking the registry straight out is the
    only way to know it works before Windows says so.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_one_file_gets_one_lock_however_often_it_is_asked_for(self):
        pfad = os.path.join(self.tmp, 'q.lock')
        self.assertIs(_in_process_lock(pfad), _in_process_lock(pfad))

    def test_the_same_file_spelled_differently_gets_the_same_lock(self):
        gerade = os.path.join(self.tmp, 'q.lock')
        umweg = os.path.join(self.tmp, '.', 'q.lock')
        self.assertNotEqual(gerade, umweg, 'the two spellings must differ')
        self.assertIs(_in_process_lock(gerade), _in_process_lock(umweg))

    def test_a_relative_path_meets_the_absolute_one(self):
        hier = os.getcwd()
        os.chdir(self.tmp)
        self.addCleanup(os.chdir, hier)
        self.assertIs(_in_process_lock('q.lock'),
                      _in_process_lock(os.path.join(self.tmp, 'q.lock')))

    def test_two_files_do_not_share_a_lock(self):
        """
        The queue and the cursor are locked separately on purpose; sharing
        one lock between them would serialise work that need not wait.
        """
        self.assertIsNot(_in_process_lock(os.path.join(self.tmp, 'a.lock')),
                         _in_process_lock(os.path.join(self.tmp, 'b.lock')))

    def test_it_is_the_lock_that_locked_actually_takes(self):
        """
        Ties the registry to the context manager. Without this the tests
        above would pass a registry nothing uses.
        """
        pfad = os.path.join(self.tmp, 'q.lock')
        with locked(pfad, timeout=2):
            self.assertTrue(_in_process_lock(pfad).locked())
        self.assertFalse(_in_process_lock(pfad).locked())


if __name__ == '__main__':
    unittest.main()
