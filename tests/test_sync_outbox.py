import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(REPO)

from tt.sync_outbox import Outbox, OutboxFull
from tt.filelock import locked, LockTimeout


class TestOutbox(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.box = Outbox(path=os.path.join(self.tmp, 'q.jsonl'),
                          lock_path=os.path.join(self.tmp, 'q.lock'),
                          highwater_path=os.path.join(self.tmp, 'q.hw'))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_empty_queue_reads_as_empty(self):
        self.assertEqual(self.box.pending(), [])
        self.assertEqual(self.box.count(), 0)

    def test_appending_numbers_operations_from_one_upwards(self):
        self.assertEqual(self.box.append('task.set', uid='a' * 16, f={'p': 1}), 1)
        self.assertEqual(self.box.append('task.set', uid='a' * 16, f={'p': 2}), 2)
        self.assertEqual([e['lc'] for e in self.box.pending()], [1, 2])

    def test_numbering_continues_after_a_restart(self):
        """
        The counter cannot live in one process's memory: the number must never
        repeat for this device, and the next change may well be made by a
        different process - or after the machine was switched off.
        """
        self.box.append('task.set', uid='a' * 16)
        self.box.append('task.set', uid='a' * 16)
        reopened = Outbox(path=self.box.path, lock_path=self.box.lock_path,
                          highwater_path=self.box.highwater_path)
        self.assertEqual(reopened.append('task.set', uid='a' * 16), 3)

    def test_numbering_continues_after_the_middle_is_acknowledged(self):
        """Dropping entry 2 must not let 3 be handed out twice."""
        for _ in range(3):
            self.box.append('task.set', uid='a' * 16)
        self.box.drop([2])
        self.assertEqual([e['lc'] for e in self.box.pending()], [1, 3])
        self.assertEqual(self.box.append('task.set', uid='a' * 16), 4)

    def test_numbering_continues_after_the_queue_has_been_emptied(self):
        """
        The case that ends synchronisation altogether if the counter is read
        from the queue alone. A successful sync drains it; if the next number
        then started again at one, the server - which refuses anything at or
        below what it has already seen from this device - would discard every
        change from that point on, silently and for ever.
        """
        for _ in range(3):
            self.box.append('task.set', uid='a' * 16)
        self.box.drop([1, 2, 3])
        self.assertEqual(self.box.pending(), [])

        self.assertEqual(self.box.append('task.set', uid='a' * 16), 4)

    def test_the_counter_survives_a_restart_with_an_empty_queue(self):
        self.box.append('task.set', uid='a' * 16)
        self.box.drop([1])
        reopened = Outbox(path=self.box.path, lock_path=self.box.lock_path,
                          highwater_path=self.box.highwater_path)
        self.assertEqual(reopened.append('task.set', uid='a' * 16), 2)

    def test_clearing_does_not_reset_the_counter(self):
        """
        clear() is for re-seeding a machine. The server still remembers the
        numbers this device has used, so starting over would make everything
        sent afterwards look like a repeat.
        """
        self.box.append('task.set', uid='a' * 16)
        self.box.append('task.set', uid='a' * 16)
        self.box.clear()
        self.assertEqual(self.box.append('task.set', uid='a' * 16), 3)

    def test_operations_are_read_back_in_numbered_order(self):
        """
        The server stamps a batch in the order it receives it, then refuses
        anything at or below the highest number it has seen. Sending 3 after
        5 would therefore lose 3.
        """
        self.box.append('task.set', uid='a' * 16, f={'p': 1})
        self.box.append('task.set', uid='a' * 16, f={'p': 2})
        with open(self.box.path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        with open(self.box.path, 'w', encoding='utf-8') as f:
            f.writelines(reversed(lines))       # as a concurrent append could
        self.assertEqual([e['lc'] for e in self.box.pending()], [1, 2])

    def test_none_valued_fields_are_left_out(self):
        """Sending an absent value as null would overwrite a real one."""
        self.box.append('task.set', uid='a' * 16, project=None, f={'p': 1})
        entry = self.box.pending()[0]
        self.assertNotIn('project', entry)
        self.assertEqual(entry['f'], {'p': 1})

    def test_dropping_removes_only_what_was_acknowledged(self):
        for _ in range(4):
            self.box.append('task.set', uid='a' * 16)
        self.box.drop([1, 3])
        self.assertEqual([e['lc'] for e in self.box.pending()], [2, 4])

    def test_clearing_empties_the_queue(self):
        self.box.append('task.set', uid='a' * 16)
        self.box.clear()
        self.assertEqual(self.box.pending(), [])

    def test_a_damaged_line_costs_one_change_not_the_whole_queue(self):
        """
        Several processes append here and a machine can be switched off
        mid-write. One unreadable line must not make everything else
        unsendable.
        """
        self.box.append('task.set', uid='a' * 16, f={'p': 1})
        with open(self.box.path, 'a', encoding='utf-8') as f:
            f.write('{"op": "task.set", "lc": 2, "f": {"p"\n')      # cut short
        self.box.append('task.set', uid='a' * 16, f={'p': 3})

        surviving = self.box.pending()
        self.assertEqual([e['f']['p'] for e in surviving], [1, 3])

    def test_a_line_that_is_not_an_operation_is_skipped(self):
        """
        Parses as JSON but is not one of ours - a stray line, or a file that
        was something else. Without the number it cannot be sent or
        acknowledged, so it must not reach the batch.
        """
        self.box.append('task.set', uid='a' * 16, f={'p': 1})
        with open(self.box.path, 'a', encoding='utf-8') as f:
            f.write('[1, 2, 3]\n')
            f.write('{"op": "task.set"}\n')        # no number
            f.write('"a string"\n')

        surviving = self.box.pending()
        self.assertEqual([e['lc'] for e in surviving], [1])

    def test_a_batch_that_exactly_fills_the_queue_is_accepted(self):
        """The boundary: at the limit is allowed, past it is not."""
        import tt.sync_outbox as mod
        original = mod.MAX_PENDING
        mod.MAX_PENDING = 3
        try:
            self.assertEqual(len(self.box.extend([{'op': 'task.set'}] * 3)), 3)
        finally:
            mod.MAX_PENDING = original

    def test_the_queue_refuses_to_grow_without_limit(self):
        """
        A queue that grew for ever would turn a long outage into a full disk,
        and a batch that can never fit into one request.
        """
        import tt.sync_outbox as mod
        original = mod.MAX_PENDING
        mod.MAX_PENDING = 3
        try:
            for _ in range(3):
                self.box.append('task.set', uid='a' * 16)
            with self.assertRaises(OutboxFull):
                self.box.append('task.set', uid='a' * 16)
        finally:
            mod.MAX_PENDING = original


    def test_a_bulk_batch_refuses_to_overflow_the_queue_by_default(self):
        import tt.sync_outbox as mod
        original = mod.MAX_PENDING
        mod.MAX_PENDING = 3
        try:
            with self.assertRaises(OutboxFull):
                self.box.extend([{'op': 'task.set', 'uid': 'a' * 16}] * 4)
            self.assertEqual(self.box.pending(), [],
                             "a refused batch left part of itself behind")
        finally:
            mod.MAX_PENDING = original

    def test_but_an_existing_document_may_exceed_it(self):
        """
        The limit is there to stop a queue growing without bound while syncing
        is broken. Describing a document the server has never seen is a single
        finite batch that then drains - and refusing it would mean that
        machine is never offered at all, silently.
        """
        import tt.sync_outbox as mod
        original = mod.MAX_PENDING
        mod.MAX_PENDING = 3
        try:
            numbers = self.box.extend([{'op': 'task.set', 'uid': 'a' * 16}] * 5,
                                      allow_overflow=True)
            self.assertEqual(numbers, [1, 2, 3, 4, 5])
        finally:
            mod.MAX_PENDING = original

    def test_an_empty_batch_does_nothing_at_all(self):
        self.assertEqual(self.box.extend([]), [])
        self.assertEqual(self.box.pending(), [])

    def test_bulk_and_single_appends_share_one_counter(self):
        self.box.append('task.set', uid='a' * 16)
        self.assertEqual(self.box.extend([{'op': 'task.set', 'uid': 'a' * 16}] * 2), [2, 3])
        self.assertEqual(self.box.append('task.set', uid='a' * 16), 4)


class TestTheQueueMakesItsOwnDirectory(unittest.TestCase):
    """
    On a machine that has never synced there is no configuration directory
    yet, and the first change recorded has to create it rather than fail.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = os.path.join(self.tmp, 'never', 'been', 'here')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _box(self):
        return Outbox(path=os.path.join(self.root, 'q.jsonl'),
                      lock_path=os.path.join(self.root, 'q.lock'),
                      highwater_path=os.path.join(self.root, 'q.hw'))

    def test_a_single_append_creates_it(self):
        self.assertEqual(self._box().append('task.set', uid='a' * 16), 1)
        self.assertTrue(os.path.isdir(self.root))

    def test_a_bulk_append_creates_it(self):
        self.assertEqual(self._box().extend([{'op': 'task.set'}]), [1])
        self.assertTrue(os.path.isdir(self.root))


class TestWhetherTheQueueIsUsedAtAll(unittest.TestCase):
    """
    Every installation without synchronisation configured - which is all of
    them until somebody switches it on - must get no queue whatsoever.
    """

    def test_no_queue_unless_it_is_switched_on(self):
        from tt.sync_outbox import default_outbox_if_enabled
        for config in ({}, None, 'nonsense', {'sync': None}, {'sync': 'yes'},
                       {'sync': {}}, {'sync': {'enabled': False}}):
            self.assertIsNone(default_outbox_if_enabled(config), repr(config))

    def test_a_queue_once_it_is(self):
        from tt.sync_outbox import default_outbox_if_enabled, Outbox
        self.assertIsInstance(default_outbox_if_enabled({'sync': {'enabled': True}}), Outbox)


class TestFileLock(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.lock = os.path.join(self.tmp, 'x.lock')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_the_lock_can_be_taken_and_released(self):
        with locked(self.lock):
            pass
        with locked(self.lock):
            pass

    def test_two_threads_of_one_process_exclude_each_other(self):
        """
        Not the same guarantee as between processes, and the one the sync
        worker actually depends on: it runs in a thread beside the one drawing
        the interface, and both reach for the queue and the inbox. On POSIX
        flock attaches to the open file description, and locked() opens a
        fresh handle each time, so this holds - but it holds by accident of
        that detail rather than by design, which is why it is pinned here.
        """
        import threading
        holding = threading.Event()
        release = threading.Event()
        refused = []

        def hold():
            with locked(self.lock):
                holding.set()
                release.wait(5)

        keeper = threading.Thread(target=hold, daemon=True)
        keeper.start()
        self.assertTrue(holding.wait(5), "the first thread never took the lock")
        try:
            with locked(self.lock, timeout=0.3):
                refused.append(False)
        except LockTimeout:
            refused.append(True)
        finally:
            release.set()
            keeper.join(5)

        self.assertEqual(refused, [True],
                         "a second thread walked straight into a held lock")

    def test_the_lock_is_free_again_once_the_holder_lets_go(self):
        with locked(self.lock):
            pass
        with locked(self.lock, timeout=0.3):
            pass

    def test_a_lock_in_a_directory_that_does_not_exist_yet(self):
        """The configuration directory on a machine that has never synced."""
        nested = os.path.join(self.tmp, 'not', 'yet', 'there.lock')
        with locked(nested):
            pass
        self.assertTrue(os.path.exists(nested))

    def test_waiting_gives_up_instead_of_hanging_for_ever(self):
        """
        Blocking indefinitely on a lock some crashed process appears to hold
        would freeze the interface this runs behind. Failing lets the caller
        try again on the next cycle.
        """
        helper = textwrap.dedent("""
            import sys, time
            sys.path.insert(0, %r)
            from tt.filelock import locked
            with locked(%r):
                print("held", flush=True)
                time.sleep(5)
        """) % (REPO, self.lock)
        child = subprocess.Popen([sys.executable, '-c', helper], stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), "held")
            with self.assertRaises(LockTimeout):
                with locked(self.lock, timeout=0.5):
                    pass
        finally:
            child.kill()
            child.wait()
            child.stdout.close()


class TestConcurrentAppends(unittest.TestCase):
    """
    The reason the lock exists. The GUI and the MCP, REST and SOAP servers can
    all be recording changes at the same moment. Two of them handing the same
    number to two different operations would make the server treat the second
    as a repeat and discard it - a change lost without any error anywhere.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, 'q.jsonl')
        self.lock = os.path.join(self.tmp, 'q.lock')
        self.hw = os.path.join(self.tmp, 'q.hw')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_four_processes_never_reuse_a_number(self):
        per_process = 25
        workers = 4
        helper = textwrap.dedent("""
            import sys
            sys.path.insert(0, %r)
            from tt.sync_outbox import Outbox
            box = Outbox(path=%r, lock_path=%r, highwater_path=%r)
            for i in range(%d):
                box.append('task.set', uid='a'*16, f={'i': i})
        """) % (REPO, self.path, self.lock, self.hw, per_process)

        children = [subprocess.Popen([sys.executable, '-c', helper]) for _ in range(workers)]
        for child in children:
            self.assertEqual(child.wait(timeout=60), 0)

        entries = Outbox(path=self.path, lock_path=self.lock,
                         highwater_path=self.hw).pending()
        numbers = [e['lc'] for e in entries]

        self.assertEqual(len(numbers), workers * per_process,
                         "an append was lost entirely")
        self.assertEqual(len(set(numbers)), len(numbers),
                         "the same number was handed out twice - a change would be dropped")
        self.assertEqual(sorted(numbers), list(range(1, workers * per_process + 1)),
                         "the numbering has gaps or does not start at one")


if __name__ == '__main__':
    unittest.main()
