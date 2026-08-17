import os
import shutil
import sys
import tempfile
import threading
import unittest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from tt import sync_client, sync_engine, sync_log
from tt.sync_outbox import Outbox
from tt.TimeTracker import TimeTracker


class LogTestCase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._real_dir = sync_client.config_dir
        sync_client.config_dir = lambda: self.tmp
        self._was = sync_log.is_enabled()

    def tearDown(self):
        sync_log.configure({'sync': {'log_enabled': self._was}})
        sync_client.config_dir = self._real_dir
        shutil.rmtree(self.tmp, ignore_errors=True)

    def on(self):
        sync_log.configure({'sync': {'enabled': True, 'log_enabled': True}})


class TestItStaysQuietUnlessAskedFor(LogTestCase):
    """
    A diagnostic log is a record of what somebody was doing and when. Nobody
    should end up with one they did not ask for.
    """

    def test_off_by_default(self):
        self.assertFalse(sync_log.configure({}))
        self.assertFalse(sync_log.configure({'sync': {'enabled': True}}))
        self.assertFalse(sync_log.configure({'sync': None}))
        self.assertFalse(sync_log.configure('nonsense'))

    def test_nothing_is_written_while_it_is_off(self):
        sync_log.configure({'sync': {'enabled': True}})
        sync_log.log('cycle.ok', sent=3)
        self.assertFalse(os.path.exists(sync_log.path()),
                         "a log file appeared without being asked for")

    def test_switching_it_on_and_off_again(self):
        self.on()
        sync_log.log('cycle.ok', sent=1)
        sync_log.configure({'sync': {'enabled': True, 'log_enabled': False}})
        sync_log.log('cycle.ok', sent=2)
        self.assertEqual(len(sync_log.tail()), 1)

    def test_it_lives_beside_the_other_sync_state(self):
        """Never in the project directory, which the user may share or sync."""
        self.assertTrue(sync_log.path().startswith(self.tmp))
        project = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        self.assertFalse(sync_log.path().startswith(project + os.sep))


class TestWhatEndsUpInIt(LogTestCase):

    def test_an_entry_carries_the_time_the_event_and_the_fields(self):
        self.on()
        sync_log.log('cycle.ok', sent=3, received=1)
        line = sync_log.tail()[0]
        self.assertIn('cycle.ok', line)
        self.assertIn('sent=3', line)
        self.assertIn('received=1', line)
        self.assertRegex(line, r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}\s')

    def test_fields_are_ordered_so_two_runs_can_be_compared(self):
        self.on()
        sync_log.log('x', b=2, a=1, c=3)
        self.assertIn('a=1 b=2 c=3', sync_log.tail()[0])

    def test_an_absurdly_long_value_is_cut_rather_than_written(self):
        self.on()
        sync_log.log('x', detail='y' * 10000)
        self.assertLessEqual(len(sync_log.tail()[0]), sync_log.MAX_LINE)

    def test_the_engine_records_what_it_did(self):
        """
        The point of the whole thing: after a sync, the log says what was
        sent, what came back and where the cursor got to.
        """
        self.on()
        log = []

        def push(base_seq, ops):
            return {'ok': True, 'head': 1, 'assigned': [], 'dups': [],
                    'ops': [{'s': 1, 'op': 'project.create', 'dev': 'other',
                             'uid': 'p' * 16, 'f': {'name': 'Remote'}}],
                    'more': False}
        real_push, real_creds = sync_client.push, sync_client.load_credentials
        sync_client.push = push
        sync_client.load_credentials = lambda: {'token': 't', 'username': 'u',
                                                'base_url': 'https://x/index.php'}
        try:
            sync_engine.run_cycle(Outbox())
        finally:
            sync_client.push, sync_client.load_credentials = real_push, real_creds

        written = '\n'.join(sync_log.tail())
        self.assertIn('push', written)
        self.assertIn('filed', written)
        self.assertIn('cycle.ok', written)

    def test_a_failure_says_what_went_wrong_and_how_long_it_will_wait(self):
        self.on()
        real_push = sync_client.push
        sync_client.push = lambda b, o: {'ok': False, 'error': 'tls_failed'}
        try:
            sync_engine.run_cycle(Outbox())
        finally:
            sync_client.push = real_push

        written = '\n'.join(sync_log.tail())
        self.assertIn('tls_failed', written)
        self.assertIn('backoff', written)
        self.assertIn('terminal=True', written)


class TestItGivesNothingAwayThatWasTypedIn(LogTestCase):
    """
    The log exists to be sent to whoever is helping. A list of everything the
    user worked on last month is not part of the question, and neither is
    where their server lives.
    """

    DATA = 'test_synclog_data.json'

    def setUp(self):
        super().setUp()
        if os.path.exists(self.DATA):
            os.remove(self.DATA)

    def tearDown(self):
        if os.path.exists(self.DATA):
            os.remove(self.DATA)
        super().tearDown()

    def test_a_whole_sync_names_nothing_the_user_typed(self):
        self.on()
        outbox = Outbox()
        tracker = TimeTracker(file_path=self.DATA, op_outbox=outbox)
        tracker.add_main_project("Takeover of Northwind Ltd")
        tracker.add_task("Takeover of Northwind Ltd", "Call the lawyers",
                         note="ring Sabine about the escrow")

        secrets = ["Northwind", "lawyers", "Sabine", "escrow",
                   "s3cret-passphrase", "frank@example.com",
                   "https://private.example.com/tc/"]

        log = []
        real_push, real_creds = sync_client.push, sync_client.load_credentials
        sync_client.push = lambda b, o: {'ok': True, 'head': len(o), 'assigned': [],
                                         'dups': [], 'ops': [], 'more': False}
        sync_client.load_credentials = lambda: {
            'token': 's3cret-passphrase', 'username': 'frank@example.com',
            'base_url': 'https://private.example.com/tc/index.php'}
        try:
            sync_engine.offer_document(tracker)
            sync_engine.run_cycle(outbox)
            sync_engine.apply_pending(tracker)
        finally:
            sync_client.push, sync_client.load_credentials = real_push, real_creds

        written = '\n'.join(sync_log.tail())
        self.assertTrue(written, "nothing was logged, so this proves nothing")
        for secret in secrets:
            self.assertNotIn(secret, written,
                             "%r reached the diagnostic log" % secret)


class TestItCannotGrowWithoutBound(LogTestCase):

    def test_it_rolls_over_and_discards_the_older_half(self):
        self.on()
        original = sync_log.MAX_BYTES
        sync_log.MAX_BYTES = 2000
        try:
            for n in range(400):
                sync_log.log('filler', n=n, padding='x' * 60)
        finally:
            sync_log.MAX_BYTES = original

        self.assertLessEqual(sync_log.size(), 2 * 2000 + 4000,
                             "the log grew past one rollover")
        self.assertIn('n=399', '\n'.join(sync_log.tail()),
                      "the most recent entries are what it is for")

    def test_the_tail_spans_the_rollover(self):
        self.on()
        original = sync_log.MAX_BYTES
        sync_log.MAX_BYTES = 400
        try:
            for n in range(40):
                sync_log.log('filler', n=n)
        finally:
            sync_log.MAX_BYTES = original
        entries = sync_log.tail()
        self.assertGreater(len(entries), 5,
                           "the view empties the moment the log rolls")

    def test_clearing_removes_both_halves(self):
        self.on()
        original = sync_log.MAX_BYTES
        sync_log.MAX_BYTES = 200
        try:
            for n in range(40):
                sync_log.log('filler', n=n)
        finally:
            sync_log.MAX_BYTES = original
        sync_log.clear()
        self.assertEqual(sync_log.tail(), [])
        self.assertEqual(sync_log.size(), 0)


class TestItNeverCostsMoreThanItself(LogTestCase):

    def test_a_log_that_cannot_be_written_is_swallowed(self):
        """
        A full disk must not be able to stop somebody tracking time.
        """
        self.on()
        real = sync_log._append

        def refuse(line):
            raise OSError(28, "No space left on device")
        sync_log._append = refuse
        try:
            sync_log.log('cycle.ok', sent=1)      # must not raise
        finally:
            sync_log._append = real

    def test_an_unserialisable_field_costs_the_line_not_the_sync(self):
        class Awkward:
            def __repr__(self):
                raise RuntimeError("no")
        self.on()
        sync_log.log('x', thing=Awkward())        # must not raise
        self.assertEqual(sync_log.tail(), [])

    def test_two_threads_writing_at_once_produce_whole_lines(self):
        """
        The worker and the thread drawing the interface both write here.
        Interleaved halves would make the log unreadable exactly when it is
        being read.
        """
        self.on()
        errors = []

        def spam(marker):
            try:
                for n in range(120):
                    sync_log.log('busy', who=marker, n=n)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=spam, args=(m,)) for m in ('a', 'b')]
        for t in threads:
            t.start()
        for t in threads:
            t.join(20)

        self.assertEqual(errors, [])
        lines = sync_log.tail(1000)
        self.assertEqual(len(lines), 240)
        for line in lines:
            self.assertRegex(line, r'^\d{4}-\d{2}-\d{2}T[\d:.]+\s+busy\s+n=\d+ who=[ab]$')


if __name__ == '__main__':
    unittest.main()
