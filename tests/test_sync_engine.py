import contextlib
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from tt import sync_client, sync_engine
from tt.sync_outbox import Outbox
from tt.TimeTracker import TimeTracker


class FakeServer:
    """
    Stands in for the running server, copying the semantics that matter:
    'since' is exclusive, a push never echoes the caller's own operations
    back, and a number at or below what this device has already sent is
    reported as a repeat rather than recorded again.
    """

    def __init__(self, device='aaaaaaaaaaaaaaaa'):
        self.log = []
        self.max_lc = 0
        self.device = device
        self.page = 500
        self.fail_with = None
        self.calls = []
        # What compaction leaves behind: a document standing for everything
        # up to a point, and a log that no longer reaches back that far.
        self.snapshot = None          # {'seq': int, 'document': dict}
        self.snapshot_error = None    # what put_snapshot should refuse with

    # -- what the log holds ------------------------------------------------

    def add_foreign(self, op, **fields):
        """An operation from the other machine."""
        entry = {'s': len(self.log) + 1, 'op': op, 'dev': 'other'}
        entry.update(fields)
        self.log.append(entry)
        return entry

    @property
    def head(self):
        return len(self.log)

    # -- the endpoints -----------------------------------------------------

    def push(self, base_seq, ops):
        self.calls.append(('push', base_seq, len(ops)))
        if self.fail_with:
            return {'ok': False, 'error': self.fail_with}
        assigned, dups = [], []
        for op in ops:
            lc = int(op['lc'])
            if lc <= self.max_lc:
                dups.append(lc)
                continue
            entry = dict(op, s=len(self.log) + 1, dev=self.device)
            self.log.append(entry)
            assigned.append([lc, entry['s']])
            self.max_lc = max(self.max_lc, lc)
        if self._behind_snapshot(base_seq):
            return {'ok': True, 'head': self.head, 'assigned': assigned, 'dups': dups,
                    'ops': [], 'more': False,
                    'snapshot_seq': self.snapshot['seq'], 'needs_snapshot': True}
        visible = [e for e in self.log if e['s'] > base_seq and e['dev'] != self.device]
        return {'ok': True, 'head': self.head, 'assigned': assigned, 'dups': dups,
                'ops': visible[:self.page], 'more': len(visible) > self.page,
                'snapshot_seq': self._snapshot_seq(), 'needs_snapshot': False}

    def pull(self, since, limit=500):
        self.calls.append(('pull', since, limit))
        if self.fail_with:
            return {'ok': False, 'error': self.fail_with}
        if self._behind_snapshot(since):
            # Nothing at all rather than the part that survives: that part
            # starts in the middle, and a caller applying it would drop
            # almost all of it as naming things it has never heard of.
            return {'ok': True, 'head': self.head, 'ops': [], 'more': False,
                    'snapshot_seq': self.snapshot['seq'], 'needs_snapshot': True}
        visible = [e for e in self.log if e['s'] > since]
        page = min(self.page, limit)
        return {'ok': True, 'head': self.head, 'ops': visible[:page],
                'more': len(visible) > page,
                'snapshot_seq': self._snapshot_seq(), 'needs_snapshot': False}

    # -- the snapshot ------------------------------------------------------

    def _snapshot_seq(self):
        return self.snapshot['seq'] if self.snapshot else 0

    def _behind_snapshot(self, since):
        return bool(self.snapshot) and since < self.snapshot['seq']

    def get_snapshot(self):
        self.calls.append(('get_snapshot',))
        if self.fail_with:
            return {'ok': False, 'error': self.fail_with}
        if not self.snapshot:
            return {'ok': False, 'error': 'no_snapshot'}
        return {'ok': True, 'seq': self.snapshot['seq'], 'head': self.head,
                'document': self.snapshot['document']}

    def put_snapshot(self, seq, document):
        self.calls.append(('put_snapshot', seq))
        if self.fail_with:
            return {'ok': False, 'error': self.fail_with}
        if self.snapshot_error:
            return {'ok': False, 'error': self.snapshot_error,
                    'head': self.head, 'snapshot_seq': self._snapshot_seq()}
        if seq != self.head:
            return {'ok': False, 'error': 'not_at_head',
                    'head': self.head, 'snapshot_seq': self._snapshot_seq()}
        self.snapshot = {'seq': seq, 'document': document}
        return {'ok': True, 'head': self.head, 'snapshot_seq': seq,
                'retired': 1, 'deleted': 0}


class EngineTestCase(unittest.TestCase):
    """Every test gets its own configuration directory and its own server."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._real_config_dir = sync_client.config_dir
        self._real_push = sync_client.push
        self._real_pull = sync_client.pull
        self._real_creds = sync_client.load_credentials
        self._real_get_snapshot = sync_client.get_snapshot
        self._real_put_snapshot = sync_client.put_snapshot

        sync_client.config_dir = lambda: self.tmp
        self.server = FakeServer()
        sync_client.push = self.server.push
        sync_client.pull = self.server.pull
        sync_client.load_credentials = lambda: {'token': 't', 'base_url': 'https://x/index.php'}
        sync_client.get_snapshot = self.server.get_snapshot
        sync_client.put_snapshot = self.server.put_snapshot

        self.outbox = Outbox()

    def tearDown(self):
        sync_engine.stop()
        sync_client.config_dir = self._real_config_dir
        sync_client.push = self._real_push
        sync_client.pull = self._real_pull
        sync_client.load_credentials = self._real_creds
        sync_client.get_snapshot = self._real_get_snapshot
        sync_client.put_snapshot = self._real_put_snapshot
        shutil.rmtree(self.tmp, ignore_errors=True)

    def queue(self, op, **fields):
        return self.outbox.append(op, **fields)


P1, T1, E1 = 'p' * 16, 't' * 16, 'e' * 16


class TestOneCycle(EngineTestCase):

    def test_what_is_queued_is_sent_and_then_forgotten(self):
        self.queue('project.create', uid=P1, f={'name': 'P'})
        result = sync_engine.run_cycle(self.outbox)

        self.assertTrue(result['ok'])
        self.assertEqual(len(self.server.log), 1)
        self.assertEqual(self.outbox.pending(), [], "an acknowledged change stayed queued")

    def test_what_arrives_is_filed_rather_than_applied(self):
        """
        The cycle runs off the interface's thread, so it must not touch the
        document. What it fetches waits on disk until the thread that owns
        the document picks it up.
        """
        self.server.add_foreign('project.create', uid=P1, f={'name': 'Remote'})
        sync_engine.run_cycle(self.outbox)

        records = sync_engine.read_inbox()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['ops'][0]['uid'], P1)
        self.assertEqual(sync_engine.read_state()['base_seq'], 0,
                         "the cursor moved before anything was applied")

    def test_our_own_operations_are_filed_with_the_place_they_were_given(self):
        """
        A push is not told its own operations back, only where they landed.
        They still have to reach the document in that order, or a change made
        here would be overwritten by an older one from elsewhere.
        """
        self.server.add_foreign('task.set', uid=T1, f={'priority': 1})
        self.queue('task.set', uid=T1, f={'priority': 9})
        sync_engine.run_cycle(self.outbox)

        ops = sync_engine.read_inbox()[0]['ops']
        by_seq = sorted(ops, key=lambda o: o['s'])
        self.assertEqual([o['f']['priority'] for o in by_seq], [1, 9])

    def test_a_second_cycle_asks_only_for_what_is_new(self):
        self.server.add_foreign('project.create', uid=P1, f={'name': 'A'})
        sync_engine.run_cycle(self.outbox)
        self.server.add_foreign('project.create', uid='b' * 16, f={'name': 'B'})
        sync_engine.run_cycle(self.outbox)

        second = sync_engine.read_inbox()[1]
        self.assertEqual([o['uid'] for o in second['ops']], ['b' * 16])

    def test_an_ordinary_cycle_does_not_ask_to_be_run_again(self):
        """
        'more' makes the worker come straight back instead of waiting out the
        interval. Reported when there is nothing left, it becomes a loop that
        talks to the server without pause.
        """
        self.queue('project.create', uid=P1, f={'name': 'P'})
        self.server.add_foreign('project.create', uid='c' * 16, f={'name': 'C'})
        result = sync_engine.run_cycle(self.outbox)
        self.assertTrue(result['ok'])
        self.assertFalse(result['more'])

    def test_nothing_to_say_and_nothing_to_hear_files_nothing(self):
        sync_engine.run_cycle(self.outbox)
        self.assertEqual(sync_engine.read_inbox(), [])
        self.assertIsNotNone(sync_engine.read_state()['last_ok'])


class TestRepeatedPush(EngineTestCase):
    """
    The awkward case: the push landed, the answer did not. The operations are
    in the log at positions this machine was never told.
    """

    def test_a_repeat_makes_the_cycle_ask_for_the_whole_order(self):
        self.queue('task.set', uid=T1, f={'priority': 5})
        # The server records it, the client never hears back.
        self.server.push(0, [dict(o) for o in self.outbox.pending()])
        self.server.add_foreign('task.set', uid=T1, f={'priority': 9})

        sync_engine.run_cycle(self.outbox)

        self.assertIn('pull', [c[0] for c in self.server.calls],
                      "the cycle trusted a reply that cannot show its own place")
        ops = sorted(sync_engine.read_inbox()[0]['ops'], key=lambda o: o['s'])
        self.assertEqual([o['f']['priority'] for o in ops], [5, 9],
                         "the two machines would have disagreed about the order")

    def test_the_repeat_is_cleared_from_the_queue(self):
        self.queue('task.set', uid=T1, f={'priority': 5})
        self.server.push(0, [dict(o) for o in self.outbox.pending()])
        sync_engine.run_cycle(self.outbox)
        self.assertEqual(self.outbox.pending(), [])


class TestPartialAnswers(EngineTestCase):
    """
    The server hands back at most one batch. Getting the cursor wrong here
    skips operations permanently, because the log is only ever read forwards.
    """

    def test_a_long_backlog_is_collected_without_a_gap(self):
        self.server.page = 3
        for i in range(10):
            self.server.add_foreign('task.set', uid=T1, f={'priority': i})

        result = sync_engine.run_cycle(self.outbox)

        self.assertTrue(result['ok'])
        collected = [o for r in sync_engine.read_inbox() for o in r['ops']]
        self.assertEqual([o['s'] for o in collected], list(range(1, 11)),
                         "operations were skipped between batches")

    def test_the_cursor_never_runs_ahead_of_what_was_received(self):
        """
        Our own operations are numbered above everything already in the log.
        Recording that number while the middle is still missing would step
        over the gap for good.
        """
        self.server.page = 2
        for i in range(6):
            self.server.add_foreign('task.set', uid=T1, f={'priority': i})
        self.queue('task.set', uid=T1, f={'priority': 99})

        sync_engine.run_cycle(self.outbox)

        collected = [o for r in sync_engine.read_inbox() for o in r['ops']]
        self.assertEqual([o['s'] for o in collected], list(range(1, 8)))

    def test_more_than_one_batch_of_our_own_is_sent_over_several_cycles(self):
        original = sync_client.MAX_OPS_PER_CALL
        sync_client.MAX_OPS_PER_CALL = 3
        try:
            for i in range(7):
                self.queue('task.set', uid=T1, f={'priority': i})

            first = sync_engine.run_cycle(self.outbox)
            self.assertTrue(first['more'], "the cycle did not say there was more to send")
            self.assertEqual(len(self.outbox.pending()), 4)

            sync_engine.run_cycle(self.outbox)
            sync_engine.run_cycle(self.outbox)
            self.assertEqual(self.outbox.pending(), [], "the backlog never cleared")
            self.assertEqual(len(self.server.log), 7)
        finally:
            sync_client.MAX_OPS_PER_CALL = original

    def test_an_answer_that_never_ends_does_not_spin_for_ever(self):
        """A server that always claims more must not trap the worker."""
        self.server.page = 1
        for i in range(5):
            self.server.add_foreign('task.set', uid=T1, f={'priority': i})
        self.server.pull = lambda since, limit=500: {
            'ok': True, 'head': 99, 'ops': [{'s': since + 1, 'op': 'task.set', 'uid': T1}],
            'more': True}
        sync_client.pull = self.server.pull
        self.queue('task.set', uid=T1, f={'priority': 1})

        result = sync_engine.run_cycle(self.outbox)
        self.assertTrue(result['ok'])
        self.assertTrue(result['more'])

        # And it must not pretend it caught up. The server says its head is
        # 99; taking that while the pages in between were never delivered
        # would step over them for good, since the log is only read forwards.
        collected = [o for r in sync_engine.read_inbox() for o in r['ops']]
        highest = max(int(o['s']) for o in collected)
        self.assertEqual(sync_engine.read_inbox()[-1]['base_seq'], highest,
                         "the cursor ran ahead of what was actually received")
        self.assertLess(highest, 99)


class TestABatchTheServerCanActuallyRead(EngineTestCase):
    """
    The failure this was written for, seen in the field: the client bundled
    five hundred operations, the body went past the server's one-megabyte
    read, the server parsed nothing and answered "ok" with an unchanged head.
    The queue never shrank, no error appeared anywhere, and every change made
    on that machine stopped reaching the other one.
    """

    def test_a_cycle_never_sends_more_than_the_server_will_read(self):
        for n in range(60):
            self.queue('task.set', uid=T1, f={'note': 'x' * 20000})

        sent = {}
        real = self.server.push

        def measure(base_seq, ops):
            body = __import__('json').dumps({'base_seq': base_seq, 'ops': ops},
                                            ensure_ascii=False)
            sent['bytes'] = max(sent.get('bytes', 0), len(body.encode('utf-8')))
            return real(base_seq, ops)
        sync_client.push = measure

        sync_engine.run_cycle(self.outbox)
        self.assertLess(sent['bytes'], 1048576,
                        "the body is past what the server reads, and would "
                        "arrive as an empty request")

    def test_a_large_backlog_still_drains_completely(self):
        for n in range(40):
            self.queue('task.set', uid=T1, f={'note': 'x' * 30000})
        for _ in range(20):
            if not self.outbox.pending():
                break
            sync_engine.run_cycle(self.outbox)
        self.assertEqual(self.outbox.pending(), [], "the queue never emptied")
        self.assertEqual(len(self.server.log), 40)

    def test_the_order_survives_being_split(self):
        for n in range(30):
            self.queue('task.set', uid=T1, f={'note': 'x' * 30000, 'priority': n})
        for _ in range(20):
            if not self.outbox.pending():
                break
            sync_engine.run_cycle(self.outbox)
        self.assertEqual([e['f']['priority'] for e in self.server.log], list(range(30)))


class TestFailures(EngineTestCase):

    def test_a_failed_cycle_leaves_the_queue_alone(self):
        self.queue('project.create', uid=P1, f={'name': 'P'})
        self.server.fail_with = 'unreachable'

        result = sync_engine.run_cycle(self.outbox)

        self.assertFalse(result['ok'])
        self.assertEqual(len(self.outbox.pending()), 1,
                         "a change was dropped although it never reached the server")

    def test_repeated_failures_back_off_instead_of_hammering(self):
        self.server.fail_with = 'unreachable'
        delays = []
        for _ in range(4):
            sync_engine.run_cycle(self.outbox)
            state = sync_engine.read_state()
            delays.append(state['next_attempt'] - int(time.time()))

        self.assertTrue(all(b >= a for a, b in zip(delays, delays[1:])),
                        "the wait did not grow: %s" % delays)
        self.assertLessEqual(max(delays), sync_engine.BACKOFF_MAX_SECONDS)

    def test_something_only_the_user_can_fix_is_not_retried_every_minute(self):
        """
        A revoked token or an address that is not a sync server will answer
        the same way for ever. Asking every minute achieves nothing.
        """
        self.server.fail_with = 'invalid_token'
        sync_engine.run_cycle(self.outbox)
        waited = sync_engine.read_state()['next_attempt'] - int(time.time())
        self.assertGreaterEqual(waited, sync_engine.BACKOFF_MAX_SECONDS - 5)

    def test_a_success_clears_the_backoff(self):
        self.server.fail_with = 'unreachable'
        sync_engine.run_cycle(self.outbox)
        self.server.fail_with = None
        sync_engine.run_cycle(self.outbox)

        state = sync_engine.read_state()
        self.assertIsNone(state['last_error'])
        self.assertEqual(state['failures'], 0)
        self.assertEqual(state['next_attempt'], 0)

    def test_a_failure_partway_through_keeps_what_did_arrive(self):
        self.server.page = 2
        for i in range(6):
            self.server.add_foreign('task.set', uid=T1, f={'priority': i})
        self.queue('task.set', uid=T1, f={'priority': 99})

        calls = {'n': 0}
        real_pull = self.server.pull

        def flaky(since, limit=500):
            calls['n'] += 1
            if calls['n'] > 1:
                # A distinct code, so that reporting every failure as a lost
                # connection would show up here rather than passing for right.
                return {'ok': False, 'error': 'tls_failed'}
            return real_pull(since, limit)

        sync_client.pull = flaky
        result = sync_engine.run_cycle(self.outbox)

        self.assertFalse(result['ok'])
        self.assertEqual(result['error'], 'tls_failed')
        self.assertEqual(sync_engine.read_state()['last_error'], 'tls_failed')
        collected = [o for r in sync_engine.read_inbox() for o in r['ops']]
        self.assertEqual([o['s'] for o in collected], [1, 2],
                         "the part that did arrive was thrown away")
        self.assertEqual(len(self.outbox.pending()), 1,
                         "our own change was dropped before its place was known")


class TestOnlyOneCycleAtATime(EngineTestCase):

    def test_a_second_cycle_steps_aside_rather_than_interleaving(self):
        from tt.filelock import locked
        with locked(sync_engine._cycle_lock_path()):
            result = sync_engine.run_cycle(self.outbox)
        self.assertTrue(result['ok'])
        self.assertEqual(result.get('skipped'), 'busy')
        self.assertEqual(self.server.calls, [], "it talked to the server anyway")


class TestApplying(EngineTestCase):
    """
    The fast half, on the thread that owns the document.
    """

    DATA = 'test_engine_data.json'

    def setUp(self):
        super().setUp()
        if os.path.exists(self.DATA):
            os.remove(self.DATA)
        self.tracker = TimeTracker(file_path=self.DATA, op_outbox=self.outbox)

    def tearDown(self):
        if os.path.exists(self.DATA):
            os.remove(self.DATA)
        super().tearDown()

    def test_nothing_pending_does_nothing(self):
        self.assertIsNone(sync_engine.apply_pending(self.tracker))

    def test_what_arrived_reaches_the_document_and_the_file(self):
        self.server.add_foreign('project.create', uid=P1, f={'name': 'Remote'})
        sync_engine.run_cycle(self.outbox)

        summary = sync_engine.apply_pending(self.tracker)

        self.assertEqual(summary['applied'], 1)
        self.assertIsNotNone(self.tracker._get_project('Remote'))

        reopened = TimeTracker(file_path=self.DATA)
        self.assertIsNotNone(reopened._get_project('Remote'),
                             "the change was applied but never saved")

    def test_the_cursor_moves_only_once_the_document_holds_it(self):
        self.server.add_foreign('project.create', uid=P1, f={'name': 'Remote'})
        sync_engine.run_cycle(self.outbox)
        self.assertEqual(sync_engine.read_state()['base_seq'], 0)

        sync_engine.apply_pending(self.tracker)

        self.assertEqual(sync_engine.read_state()['base_seq'], 1)
        self.assertEqual(sync_engine.read_inbox(), [])

    def test_applying_twice_is_harmless(self):
        self.server.add_foreign('project.create', uid=P1, f={'name': 'Remote'})
        sync_engine.run_cycle(self.outbox)
        sync_engine.apply_pending(self.tracker)
        sync_engine.apply_pending(self.tracker)
        self.assertEqual(len(self.tracker.data['projects']), 1)

    def test_a_session_ended_because_work_began_elsewhere_is_reported_back(self):
        """
        That closure is worked out here, from the order alone. Unless it is
        sent, the other machines go on showing the session as running.
        """
        self.tracker.add_main_project('P')
        self.tracker.add_task('P', 'Here')
        self.tracker.start_work('P', 'Here')
        running = self.tracker._get_task('P', 'Here')['time_entries'][0]['uid']
        task_uid = self.tracker._get_task('P', 'Here')['uid']
        self.outbox.clear()

        self.server.add_foreign('entry.add', uid=E1, task=task_uid,
                                start='2030-01-01 10:00:00')
        sync_engine.run_cycle(self.outbox)
        summary = sync_engine.apply_pending(self.tracker)

        self.assertEqual(summary['auto_closed'], 1)
        closes = [o for o in self.outbox.pending()
                  if o['op'] == 'entry.close' and o['uid'] == running]
        self.assertEqual(len(closes), 1, "the other machine is never told it ended")
        self.assertEqual(closes[0]['end'], '2030-01-01 10:00:00')

    def test_discarded_time_is_reported_so_the_user_can_be_told(self):
        """
        The hours are gone, deliberately - but the user did not ask for that
        on this machine, so it cannot happen in silence.
        """
        self.tracker.add_main_project('P')
        self.tracker.add_task('P', 'Doomed')
        task_uid = self.tracker._get_task('P', 'Doomed')['uid']
        self.tracker.delete_task('P', 'Doomed')
        self.outbox.clear()

        self.server.add_foreign('entry.add', uid=E1, task=task_uid,
                                start='2026-08-10 09:00:00')
        sync_engine.run_cycle(self.outbox)
        summary = sync_engine.apply_pending(self.tracker)

        self.assertEqual(summary['discarded_time'], 1)
        self.assertIsNone(self.tracker._get_task('P', 'Doomed'))

    def test_an_unsent_change_still_wins_over_an_older_incoming_one(self):
        self.tracker.add_main_project('P')
        self.tracker.add_task('P', 'T')
        task_uid = self.tracker._get_task('P', 'T')['uid']
        sync_engine.run_cycle(self.outbox)
        sync_engine.apply_pending(self.tracker)

        # Elsewhere first, here second - but ours has not been sent yet.
        self.server.add_foreign('task.set', uid=task_uid, f={'priority': 2})
        self.tracker.update_task('P', 'T', priority=8)

        # Fetch without sending, as a cycle interrupted before its push would.
        fetched = sync_client.pull(sync_engine.read_state()['base_seq'])
        sync_engine._append_inbox({'base_seq': fetched['head'], 'ops': fetched['ops']})
        sync_engine.apply_pending(self.tracker)

        self.assertEqual(self.tracker._get_task('P', 'T')['priority'], 8)


class TestOfferingTheExistingDocument(EngineTestCase):

    DATA = 'test_engine_seed.json'

    def setUp(self):
        super().setUp()
        if os.path.exists(self.DATA):
            os.remove(self.DATA)
        self.tracker = TimeTracker(file_path=self.DATA, op_outbox=self.outbox)

    def tearDown(self):
        if os.path.exists(self.DATA):
            os.remove(self.DATA)
        super().tearDown()

    def test_an_existing_document_is_offered_the_first_time(self):
        self.tracker.add_main_project('Existing')
        self.tracker.add_task('Existing', 'Older work')
        self.outbox.clear()

        queued = sync_engine.offer_document(self.tracker)

        self.assertGreater(queued, 0)
        from tt.sync_apply import seed_operations
        self.assertEqual([o['op'] for o in self.outbox.pending()],
                         [o['op'] for o in seed_operations(self.tracker.data)])

    def test_it_is_offered_only_once(self):
        self.tracker.add_main_project('Existing')
        sync_engine.offer_document(self.tracker)
        self.outbox.clear()

        self.assertEqual(sync_engine.offer_document(self.tracker), 0)
        self.assertEqual(self.outbox.pending(), [])

    def test_nothing_is_offered_before_signing_in(self):
        sync_client.load_credentials = lambda: None
        self.tracker.add_main_project('Existing')
        self.outbox.clear()
        self.assertEqual(sync_engine.offer_document(self.tracker), 0)

    def test_a_document_offered_here_rebuilds_on_the_other_machine(self):
        self.tracker.add_main_project('Website')
        self.tracker.add_task('Website', 'Relaunch', priority=4)
        self.tracker.start_work('Website', 'Relaunch')
        self.tracker.stop_work()
        self.outbox.clear()

        sync_engine.offer_document(self.tracker)
        sync_engine.run_cycle(self.outbox)

        elsewhere = {'projects': [], 'next_id': 1, '_deleted': [], 'schema_version': 2}
        from tt.sync_apply import apply_ops
        apply_ops(elsewhere, self.server.log)

        self.assertEqual([p['main_project_name'] for p in elsewhere['projects']], ['Website'])
        task = elsewhere['projects'][0]['tasks'][0]
        self.assertEqual(task['task_name'], 'Relaunch')
        self.assertEqual(task['priority'], 4)
        self.assertEqual(len(task['time_entries']), 1)
        self.assertIn('end_time', task['time_entries'][0])


class TestTheWorker(EngineTestCase):

    def test_only_one_worker_exists_however_often_it_is_started(self):
        """
        The interface re-runs its whole script on every redraw, so this is
        called constantly. A thread per redraw would be a thread every few
        seconds, all pushing the same queue.
        """
        cfg = {'sync': {'enabled': True}}
        for _ in range(5):
            sync_engine.ensure_started(cfg)
        alive = [t for t in __import__('threading').enumerate() if t.name == 'tc-sync']
        self.assertEqual(len(alive), 1)

    def test_it_does_not_start_when_synchronisation_is_off(self):
        self.assertFalse(sync_engine.ensure_started({'sync': {'enabled': False}}))
        self.assertFalse(sync_engine.ensure_started({}))
        alive = [t for t in __import__('threading').enumerate() if t.name == 'tc-sync']
        self.assertEqual(alive, [])

    def test_a_nudge_makes_it_run_without_waiting_for_the_interval(self):
        self.queue('project.create', uid=P1, f={'name': 'P'})
        sync_engine.ensure_started({'sync': {'enabled': True}})
        sync_engine.nudge()

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not self.server.log:
            time.sleep(0.05)
        self.assertEqual(len(self.server.log), 1, "the nudge never produced a cycle")

    def test_a_nudge_does_not_get_round_the_wait_after_a_failure(self):
        """
        A nudge comes from changing view, which happens constantly. If it
        cancelled the backoff, a server that is down would be contacted on
        every single navigation and the backoff would exist only on paper.
        """
        self.server.fail_with = 'unreachable'
        sync_engine.run_cycle(self.outbox)
        before = len(self.server.calls)

        sync_engine.ensure_started({'sync': {'enabled': True}})
        for _ in range(3):
            sync_engine.nudge()
            time.sleep(0.2)

        self.assertEqual(len(self.server.calls), before,
                         "the wait was ignored and the server was asked again")

    def test_the_user_asking_directly_does_lift_the_wait(self):
        self.server.fail_with = 'unreachable'
        sync_engine.run_cycle(self.outbox)
        self.server.fail_with = None

        sync_engine.ensure_started({'sync': {'enabled': True}})
        sync_engine.nudge(force=True)

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not sync_engine.read_state()['last_ok']:
            time.sleep(0.05)
        self.assertIsNotNone(sync_engine.read_state()['last_ok'])

    def test_a_cycle_that_raises_does_not_kill_the_worker(self):
        def explode(*_a, **_k):
            raise RuntimeError("boom")
        sync_client.push = explode
        sync_engine.ensure_started({'sync': {'enabled': True}})
        sync_engine.nudge()
        time.sleep(0.5)

        sync_client.push = self.server.push
        sync_engine.nudge()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not sync_engine.read_state()['last_ok']:
            time.sleep(0.05)
        self.assertIsNotNone(sync_engine.read_state()['last_ok'],
                             "the worker died on the first error and never came back")


class TestConsumingTheInbox(EngineTestCase):
    """
    The worker files what it fetches; the drawing thread applies it and takes
    it away. Getting the hand-over wrong loses operations with nothing to say
    so, which is the one failure this whole design exists to avoid.
    """

    DATA = 'test_engine_inbox.json'

    def setUp(self):
        super().setUp()
        if os.path.exists(self.DATA):
            os.remove(self.DATA)
        self.tracker = TimeTracker(file_path=self.DATA, op_outbox=self.outbox)

    def tearDown(self):
        if os.path.exists(self.DATA):
            os.remove(self.DATA)
        super().tearDown()

    def test_a_record_filed_while_applying_is_not_swept_away_with_the_rest(self):
        """
        The worker appends between the drawing thread reading the inbox and
        emptying it. Deleting the whole file would destroy that record while
        the cursor moved past it, and the two machines would quietly stop
        agreeing.
        """
        self.server.add_foreign('project.create', uid=P1, f={'name': 'First'})
        sync_engine.run_cycle(self.outbox)

        with sync_engine.taken_inbox() as records:
            self.assertEqual(len(records), 1)
            # Straight to the file, as a worker holding no inbox lock would.
            with open(sync_engine.inbox_path(), 'a', encoding='utf-8') as f:
                f.write('{"base_seq": 9, "ops": [{"s": 9, "op": "project.create", '
                        '"uid": "%s", "f": {"name": "Late"}}]}\n' % ('c' * 16))

        left = sync_engine.read_inbox()
        self.assertEqual(len(left), 1, "the late record was thrown away unapplied")
        self.assertEqual(left[0]['base_seq'], 9)

    def test_a_document_that_cannot_be_saved_keeps_what_arrived(self):
        """
        A full disk or a share gone read-only. Consuming the inbox anyway
        would show the incoming changes on screen and lose them at the next
        restart, with the cursor already past them.
        """
        self.server.add_foreign('project.create', uid=P1, f={'name': 'Remote'})
        sync_engine.run_cycle(self.outbox)

        def refuse():
            raise OSError("read-only file system")
        self.tracker._save_data = refuse

        with self.assertRaises(OSError):
            sync_engine.apply_pending(self.tracker)

        self.assertEqual(len(sync_engine.read_inbox()), 1,
                         "the fetched operations were consumed but never saved")
        self.assertEqual(sync_engine.read_state()['base_seq'], 0,
                         "the cursor moved past operations that were never stored")

    def test_the_end_of_a_session_is_queued_before_the_document_is_committed(self):
        """
        A machine switched off between the two would hold a closure it can
        never re-derive and never pass on, leaving the session open on the
        other machine for good.
        """
        self.tracker.add_main_project('P')
        self.tracker.add_task('P', 'T')
        self.tracker.start_work('P', 'T')
        task_uid = self.tracker._get_task('P', 'T')['uid']
        self.outbox.clear()

        order = []
        real_save = self.tracker._save_data
        self.tracker._save_data = lambda: (order.append('saved'), real_save())[1]
        real_emit = self.tracker._emit
        self.tracker._emit = lambda op, **f: (order.append(op), real_emit(op, **f))[1]

        self.server.add_foreign('entry.add', uid=E1, task=task_uid,
                                start='2030-01-01 10:00:00')
        sync_engine.run_cycle(self.outbox)
        sync_engine.apply_pending(self.tracker)

        self.assertIn('entry.close', order)
        self.assertIn('saved', order)
        self.assertLess(order.index('entry.close'), order.index('saved'))


class TestTheQueueAndTheLogDisagreeing(EngineTestCase):
    """
    The queue and the log drift apart for several dull reasons: a push whose
    reply was lost, a catch-up cut short before the queue drained, a machine
    switched off between filing what arrived and clearing the queue. Whenever
    they do, the same operation exists in both - and replaying it on top of
    what has already been ordered inverts the server's order silently.
    """

    DATA = 'test_engine_placed.json'

    def setUp(self):
        super().setUp()
        if os.path.exists(self.DATA):
            os.remove(self.DATA)
        self.tracker = TimeTracker(file_path=self.DATA, op_outbox=self.outbox)
        self.device = sync_client.device_identity()['device_uid']
        self.server.device = self.device

    def tearDown(self):
        if os.path.exists(self.DATA):
            os.remove(self.DATA)
        super().tearDown()

    def test_an_operation_already_in_the_log_is_not_replayed_on_top(self):
        """
        Ours landed at 100 and theirs at 101, so theirs wins. Lifting ours
        back to the top because it is still queued would leave this machine
        on our value and the other on theirs, for good, with nothing to say
        the two had parted company.
        """
        self.tracker.add_main_project('P')
        self.tracker.add_task('P', 'T')
        task_uid = self.tracker._get_task('P', 'T')['uid']
        sync_engine.run_cycle(self.outbox)
        sync_engine.apply_pending(self.tracker)
        self.outbox.drop([e['lc'] for e in self.outbox.pending()])

        # Ours is recorded by the server, but the reply never arrives, so it
        # stays queued.
        self.tracker.update_task('P', 'T', priority=3)
        self.server.push(0, [dict(o) for o in self.outbox.pending()])
        # Then the other machine changes the same field, after ours, and
        # carries on working - enough that the catch-up needs several pages.
        self.server.add_foreign('task.set', uid=task_uid, f={'priority': 7})
        for n in range(4):
            self.server.add_foreign('project.create', uid='%016x' % (n + 20),
                                    f={'name': 'Other %d' % n})

        # The catch-up has to be cut short, which is what leaves the queue
        # undrained while the record covering it has already been filed.
        # Cutting it short is ordinary: the reply itself caps at 500 and the
        # connection that just lost a push reply is the same one.
        self.server.page = 2
        real_pull = self.server.pull
        calls = {'n': 0}

        def flaky(since, limit=500):
            calls['n'] += 1
            if calls['n'] > 1:
                return {'ok': False, 'error': 'unreachable'}
            return real_pull(since, limit)
        sync_client.pull = flaky

        sync_engine.run_cycle(self.outbox)
        self.assertEqual(len(self.outbox.pending()), 1,
                         "the setup did not reproduce an undrained queue")
        sync_engine.apply_pending(self.tracker)

        self.assertEqual(self.tracker._get_task('P', 'T')['priority'], 7,
                         "our older change was replayed over a newer one - "
                         "this machine and the other now disagree for good")

    def test_an_operation_the_log_has_placed_leaves_the_queue(self):
        self.tracker.add_main_project('P')
        sync_engine.run_cycle(self.outbox)
        sync_engine.apply_pending(self.tracker)
        self.outbox.drop([e['lc'] for e in self.outbox.pending()])

        self.tracker.add_main_project('Q')
        self.server.push(0, [dict(o) for o in self.outbox.pending()])
        for n in range(4):
            self.server.add_foreign('project.create', uid='%016x' % (n + 30),
                                    f={'name': 'Other %d' % n})

        self.server.page = 2
        real_pull = self.server.pull
        calls = {'n': 0}

        def flaky(since, limit=500):
            calls['n'] += 1
            if calls['n'] > 1:
                return {'ok': False, 'error': 'unreachable'}
            return real_pull(since, limit)
        sync_client.pull = flaky

        sync_engine.run_cycle(self.outbox)
        self.assertEqual(len(self.outbox.pending()), 1)
        sync_engine.apply_pending(self.tracker)
        self.assertEqual(self.outbox.pending(), [],
                         "an operation the log already holds stayed queued")

    def test_another_device_s_numbering_does_not_touch_ours(self):
        """
        Every device numbers its own operations from one, so the other
        machine's lc 1 and ours collide constantly. Matching on the number
        alone would throw our unsent work out of the queue on the strength of
        somebody else's - and it would never reach the server at all.
        """
        self.tracker.add_main_project('P')
        sync_engine.run_cycle(self.outbox)
        sync_engine.apply_pending(self.tracker)
        self.outbox.drop([e['lc'] for e in self.outbox.pending()])

        self.tracker.add_main_project('Mine')
        queued = [e['lc'] for e in self.outbox.pending()]
        self.assertTrue(queued)

        # The other machine's operation carries the same number as ours.
        foreign = self.server.add_foreign('project.create', uid='f' * 16,
                                          f={'name': 'Theirs'})
        foreign['lc'] = queued[0]
        sync_engine._append_inbox({'base_seq': foreign['s'], 'ops': [foreign]})

        sync_engine.apply_pending(self.tracker)

        self.assertEqual([e['lc'] for e in self.outbox.pending()], queued,
                         "our unsent work was dropped on the strength of "
                         "another device's numbering")
        self.assertIsNotNone(self.tracker._get_project('Mine'))
        self.assertIsNotNone(self.tracker._get_project('Theirs'))

    def test_the_tracker_s_own_queue_is_the_one_that_is_used(self):
        """
        Not a default one built on the spot. The MCP and REST servers can be
        given a queue of their own, and draining the wrong one would leave
        their changes queued for ever.
        """
        private = Outbox(path=os.path.join(self.tmp, 'private.jsonl'),
                         lock_path=os.path.join(self.tmp, 'private.lock'),
                         highwater_path=os.path.join(self.tmp, 'private.hw'))
        self.tracker.op_outbox = private
        self.tracker.add_main_project('P')
        queued = private.pending()
        self.assertTrue(queued)
        self.assertEqual(self.outbox.pending(), [],
                         "the change went into the default queue instead")

        # The log now shows that operation as ours, at a place of its own -
        # so applying has to take it out of the tracker's queue, which is the
        # private one.
        mine = sync_client.device_identity()['device_uid']
        sync_engine._append_inbox({'base_seq': 1, 'ops': [
            dict(queued[0], s=1, dev=mine)]})
        sync_engine.apply_pending(self.tracker)

        self.assertEqual(private.pending(), [],
                         "the wrong queue was drained")

    def test_unsent_work_is_still_replayed_on_top(self):
        """The guard must not swallow work the server has genuinely not seen."""
        self.tracker.add_main_project('P')
        self.tracker.add_task('P', 'T')
        task_uid = self.tracker._get_task('P', 'T')['uid']
        sync_engine.run_cycle(self.outbox)
        sync_engine.apply_pending(self.tracker)

        self.server.add_foreign('task.set', uid=task_uid, f={'priority': 2})
        self.tracker.update_task('P', 'T', priority=9)

        fetched = sync_client.pull(sync_engine.read_state()['base_seq'])
        sync_engine._append_inbox({'base_seq': fetched['head'], 'ops': fetched['ops']})
        sync_engine.apply_pending(self.tracker)

        self.assertEqual(self.tracker._get_task('P', 'T')['priority'], 9)

    def test_discarded_time_is_counted_once_however_many_batches_it_came_in(self):
        """
        The queued work used to be replayed against every filed batch in turn,
        so one lost entry was counted once per batch. The user reads that
        count as hours.
        """
        self.tracker.add_main_project('P')
        self.tracker.add_task('P', 'Doomed')
        task_uid = self.tracker._get_task('P', 'Doomed')['uid']
        self.tracker.delete_task('P', 'Doomed')
        self.outbox.clear()

        self.outbox.append('entry.add', uid=E1, task=task_uid,
                           start='2026-08-10 09:00:00')
        for seq in (1, 2, 3):
            sync_engine._append_inbox({'base_seq': seq, 'ops': [
                {'s': seq, 'op': 'project.create', 'uid': '%016x' % seq,
                 'dev': 'other', 'f': {'name': 'P%d' % seq}}]})

        summary = sync_engine.apply_pending(self.tracker)
        self.assertEqual(summary['discarded_time'], 1, str(summary))

    def test_the_cursor_is_not_consumed_when_it_cannot_be_recorded(self):
        """
        Consuming what arrived while failing to record how far it reached
        leaves the cursor behind the document, and the same operations are
        fetched again and replayed over newer work.
        """
        self.server.add_foreign('project.create', uid=P1, f={'name': 'Remote'})
        sync_engine.run_cycle(self.outbox)

        real = sync_engine.write_state

        def refuse(changes, required=False):
            if 'base_seq' in changes:
                raise OSError("read-only file system")
            return real(changes)
        sync_engine.write_state = refuse
        try:
            with self.assertRaises(OSError):
                sync_engine.apply_pending(self.tracker)
        finally:
            sync_engine.write_state = real

        self.assertEqual(len(sync_engine.read_inbox()), 1,
                         "the operations were consumed but the cursor was not moved")


class TestTheWorkerStops(EngineTestCase):

    def _alive(self):
        import threading as _t
        return [t for t in _t.enumerate() if t.name == 'tc-sync' and t.is_alive()]

    def test_switching_synchronisation_off_ends_the_worker(self):
        """
        Otherwise it goes on talking to the server with the stored token for
        as long as the app is open, filing operations nobody will read - and
        the user who just switched it off has no way to tell.
        """
        sync_engine.ensure_started({'sync': {'enabled': True}})
        self.assertEqual(len(self._alive()), 1)

        self.assertFalse(sync_engine.ensure_started({'sync': {'enabled': False}}))

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and self._alive():
            time.sleep(0.05)
        self.assertEqual(self._alive(), [], "the worker outlived the setting")

    def test_switching_it_back_on_starts_a_new_one_and_only_one(self):
        sync_engine.ensure_started({'sync': {'enabled': True}})
        sync_engine.ensure_started({'sync': {'enabled': False}})
        time.sleep(0.3)
        sync_engine.ensure_started({'sync': {'enabled': True}})
        time.sleep(0.3)
        self.assertEqual(len(self._alive()), 1,
                         "the stopped worker came back to life alongside the new one")


class TestOfferingIsDoneOnce(EngineTestCase):

    DATA = 'test_engine_offer.json'

    def setUp(self):
        super().setUp()
        if os.path.exists(self.DATA):
            os.remove(self.DATA)
        self.tracker = TimeTracker(file_path=self.DATA, op_outbox=self.outbox)

    def tearDown(self):
        if os.path.exists(self.DATA):
            os.remove(self.DATA)
        super().tearDown()

    def test_the_whole_document_is_queued_in_one_go(self):
        """
        One operation at a time meant taking the lock and re-reading the
        entire queue for each - quadratic, on the thread that draws the
        interface. Years of tracked time froze the app for minutes.
        """
        self.tracker.add_main_project('P')
        for n in range(60):
            self.tracker.add_task('P', 'T%d' % n)
        self.outbox.clear()

        calls = {'n': 0}
        real_append = self.outbox.append

        def counted(op, **fields):
            calls['n'] += 1
            return real_append(op, **fields)
        self.outbox.append = counted

        queued = sync_engine.offer_document(self.tracker)

        from tt.sync_apply import seed_operations
        self.assertEqual(queued, len(seed_operations(self.tracker.data)))
        self.assertEqual(calls['n'], 0, "it still appends one at a time")

        # Numbered consecutively, and above the mark left by the changes that
        # were made and then cleared - the server refuses anything at or
        # below a number it has already seen from this device.
        expected = len(seed_operations(self.tracker.data))
        numbers = [e['lc'] for e in self.outbox.pending()]
        self.assertEqual(len(numbers), expected)
        self.assertEqual(numbers, list(range(numbers[0], numbers[0] + expected)))
        self.assertGreater(numbers[0], 61,
                           "numbering restarted below what the server has seen")

    def test_two_tabs_offering_at_once_queue_one_copy(self):
        """
        Both redraw independently, both see "not offered yet". Without the
        lock each queues the whole document and the other machine has to chew
        through two copies of everything.
        """
        import threading as _t
        self.tracker.add_main_project('P')
        self.tracker.add_task('P', 'T')
        self.outbox.clear()

        counts = []
        barrier = _t.Barrier(2)

        def offer():
            barrier.wait()
            counts.append(sync_engine.offer_document(self.tracker))

        threads = [_t.Thread(target=offer) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)

        from tt.sync_apply import seed_operations
        expected = len(seed_operations(self.tracker.data))
        self.assertEqual(sorted(counts), [0, expected], str(counts))
        self.assertEqual(len(self.outbox.pending()), expected,
                         "the document was queued twice over")


class TestWhenTheDiskItselfMisbehaves(EngineTestCase):
    """
    Every one of these ends with the same requirement: nothing is consumed
    that has not been safely recorded, and nothing crashes the application.
    """

    DATA = 'test_engine_disk.json'

    def setUp(self):
        super().setUp()
        if os.path.exists(self.DATA):
            os.remove(self.DATA)
        self.tracker = TimeTracker(file_path=self.DATA, op_outbox=self.outbox)

    def tearDown(self):
        if os.path.exists(self.DATA):
            os.remove(self.DATA)
        super().tearDown()

    @contextlib.contextmanager
    def _writes_fail(self):
        """
        A full disk, or a folder that has gone read-only.

        Injected at the last step of the atomic write rather than by taking
        the permissions off the directory: chmod does not stop a write on
        Windows, where the test would then pass for the wrong reason - and
        did, until CI said so.
        """
        real = os.replace

        def refuse(src, dst, *a, **k):
            if str(dst).startswith(self.tmp):
                raise OSError(28, "No space left on device")
            return real(src, dst, *a, **k)

        os.replace = refuse
        try:
            yield
        finally:
            os.replace = real

    def test_state_that_cannot_be_written_is_shrugged_off_by_default(self):
        """
        When the cycle ran and what went wrong are a convenience. Losing them
        costs a line on the settings screen, so it must not cost the sync.
        """
        with self._writes_fail():
            state = sync_engine.write_state({'last_error': 'unreachable'})
        self.assertIsInstance(state, dict)

    def test_but_the_cursor_refuses_to_fail_quietly(self):
        with self._writes_fail():
            with self.assertRaises(OSError):
                sync_engine.write_state({'base_seq': 5}, required=True)

    def test_a_cycle_that_cannot_write_locally_says_so(self):
        self.queue('project.create', uid=P1, f={'name': 'P'})
        self.server.add_foreign('project.create', uid='c' * 16, f={'name': 'C'})

        real_append = sync_engine._append_inbox

        def refuse(record):
            raise OSError(28, "No space left on device")
        sync_engine._append_inbox = refuse
        try:
            result = sync_engine.run_cycle(self.outbox)
        finally:
            sync_engine._append_inbox = real_append

        self.assertFalse(result['ok'])
        self.assertEqual(result['error'], 'local_io')
        self.assertEqual(len(self.outbox.pending()), 1,
                         "the change was dropped although nothing was recorded")

    def test_a_damaged_inbox_line_costs_that_line_and_no_more(self):
        self.server.add_foreign('project.create', uid=P1, f={'name': 'Remote'})
        sync_engine.run_cycle(self.outbox)
        with open(sync_engine.inbox_path(), 'a', encoding='utf-8') as f:
            f.write('{"base_seq": 9, "ops": [\n')       # cut short
        self.assertEqual(len(sync_engine.read_inbox()), 1)

    def test_a_queue_that_will_not_take_the_document_costs_a_sync_not_the_app(self):
        class Refusing:
            def pending(self):
                return []

            def extend(self, operations, allow_overflow=False):
                raise OSError("disk full")

            def append(self, op, **fields):
                raise OSError("disk full")

        self.tracker.op_outbox = Refusing()
        self.tracker.data['projects'] = [{'uid': P1, 'main_project_name': 'P',
                                          'status': 'open', 'last_started': None,
                                          'tasks': []}]
        self.assertEqual(sync_engine.offer_document(self.tracker), 0)
        self.assertFalse(sync_engine.read_state()['seeded'],
                         "it recorded the document as offered when it was not")

    def test_applying_steps_aside_when_the_inbox_is_held(self):
        """
        Another process is mid-write. Waiting would stall the redraw, so this
        leaves the records where they are and picks them up next time.
        """
        from tt.filelock import locked
        self.server.add_foreign('project.create', uid=P1, f={'name': 'Remote'})
        sync_engine.run_cycle(self.outbox)

        with locked(sync_engine._inbox_lock_path()):
            self.assertIsNone(sync_engine.apply_pending(self.tracker))

        self.assertEqual(len(sync_engine.read_inbox()), 1,
                         "the records were consumed without being applied")
        self.assertIsNotNone(sync_engine.apply_pending(self.tracker))

    def test_a_line_that_is_not_a_record_is_skipped(self):
        """
        The inbox is appended to while the machine may be switched off, and
        the file is read by a different thread than writes it. A line that
        parses as JSON but is not a record must not reach reconcile, which
        would then iterate over something that has no operations.
        """
        self.server.add_foreign('project.create', uid=P1, f={'name': 'Remote'})
        sync_engine.run_cycle(self.outbox)
        with open(sync_engine.inbox_path(), 'a', encoding='utf-8') as f:
            f.write('[1, 2, 3]\n')                    # valid JSON, not a record
            f.write('{"base_seq": 4}\n')              # a record with no operations
            f.write('"just a string"\n')

        records = sync_engine.read_inbox()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['ops'][0]['uid'], P1)

    def test_clearing_the_inbox_discards_what_was_waiting(self):
        self.server.add_foreign('project.create', uid=P1, f={'name': 'Remote'})
        sync_engine.run_cycle(self.outbox)
        self.assertTrue(sync_engine.read_inbox())
        sync_engine.clear_inbox()
        self.assertEqual(sync_engine.read_inbox(), [])

    def test_the_interface_still_gets_an_answer_when_the_queue_is_unreadable(self):
        original = sync_engine.Outbox
        sync_engine.Outbox = lambda *a, **k: (_ for _ in ()).throw(OSError("gone"))
        try:
            snap = sync_engine.status_summary()
        finally:
            sync_engine.Outbox = original
        self.assertEqual(snap['pending'], 0)


class TestTheCursorAndTheLogComingApart(EngineTestCase):
    """
    The cursor is a position in one particular log, held in one particular
    document. Both can be replaced underneath it, and when that happens
    nothing complains: the cycle keeps reporting success while sending and
    receiving nothing at all.
    """

    DATA = 'test_engine_reset.json'

    def setUp(self):
        super().setUp()
        if os.path.exists(self.DATA):
            os.remove(self.DATA)
        self.tracker = TimeTracker(file_path=self.DATA, op_outbox=self.outbox)

    def tearDown(self):
        if os.path.exists(self.DATA):
            os.remove(self.DATA)
        super().tearDown()

    def test_a_log_shorter_than_our_position_is_a_different_log(self):
        """
        A re-created account, a wiped store, a rebuilt server. A log only ever
        grows, so a head below the cursor cannot be the log the cursor came
        from.
        """
        sync_engine.write_state({'base_seq': 50, 'seeded': True})
        self.tracker.add_main_project('Mine')

        sync_engine.run_cycle(self.outbox)

        state = sync_engine.read_state()
        self.assertEqual(state['base_seq'], 0,
                         "the cursor still points into a log that no longer exists")
        self.assertGreater(len(self.server.log), 0,
                           "nothing was ever sent to the new log")

    def test_and_the_document_is_offered_to_it_again(self):
        sync_engine.write_state({'base_seq': 50, 'seeded': True})
        self.tracker.add_main_project('Mine')
        sync_engine.run_cycle(self.outbox)
        sync_engine.apply_pending(self.tracker)

        self.assertFalse(sync_engine.read_state()['seeded'],
                         "the new log is never told what this machine holds")
        sync_engine.offer_document(self.tracker)
        self.assertTrue(self.outbox.pending())

    def test_signing_in_to_a_different_account_starts_over(self):
        sync_engine.run_cycle(self.outbox)
        sync_engine.apply_pending(self.tracker)
        sync_engine.write_state({'base_seq': 3})

        sync_client.load_credentials = lambda: {'token': 't', 'username': 'someone-else',
                                                'base_url': 'https://x/index.php'}
        sync_engine.run_cycle(self.outbox)

        self.assertEqual(sync_engine.read_state()['base_seq'], 0)

    def test_a_restored_backup_takes_the_cursor_back_with_it(self):
        """
        data.json goes back to yesterday while the state file stays at today.
        Everything in between is already marked as applied, so without this it
        is never fetched again and the restored copy stays incomplete - with a
        healthy-looking sync throughout.
        """
        self.server.add_foreign('project.create', uid=P1, f={'name': 'Remote'})
        sync_engine.run_cycle(self.outbox)
        sync_engine.apply_pending(self.tracker)
        self.assertEqual(sync_engine.read_state()['base_seq'], 1)
        self.assertEqual(self.tracker.data['_sync_seq'], 1)

        # Yesterday's file: it predates that operation.
        self.tracker.data['_sync_seq'] = 0
        self.tracker.data['projects'] = []

        given_up = sync_engine.align_cursor(self.tracker)

        self.assertEqual(given_up, 1)
        self.assertEqual(sync_engine.read_state()['base_seq'], 0)

        sync_engine.run_cycle(self.outbox)
        sync_engine.apply_pending(self.tracker)
        self.assertIsNotNone(self.tracker._get_project('Remote'),
                             "what the restored copy was missing never came back")

    def test_a_cursor_ahead_of_the_document_is_the_only_one_that_moves(self):
        """Going forward over a gap cannot be undone, so it never happens."""
        sync_engine.write_state({'base_seq': 2})
        self.tracker.data['_sync_seq'] = 9
        self.assertEqual(sync_engine.align_cursor(self.tracker), 0)
        self.assertEqual(sync_engine.read_state()['base_seq'], 2)

    def test_a_document_that_carries_no_mark_is_left_alone(self):
        sync_engine.write_state({'base_seq': 4})
        self.tracker.data.pop('_sync_seq', None)
        self.assertEqual(sync_engine.align_cursor(self.tracker), 0)
        self.assertEqual(sync_engine.read_state()['base_seq'], 4)


class TestSwitchingItOffAndOnAgain(EngineTestCase):
    """
    While it is off nothing is recorded - that is what off means. So the
    changes made in between exist nowhere but this machine, and switching it
    back on has to make the machine describe itself again.
    """

    def test_the_gap_is_remembered(self):
        sync_engine.ensure_started({'sync': {'enabled': True}})
        sync_engine.write_state({'seeded': True})

        sync_engine.ensure_started({'sync': {'enabled': False}})
        self.assertTrue(sync_engine.read_state()['was_off'])

        sync_engine.ensure_started({'sync': {'enabled': True}})
        state = sync_engine.read_state()
        self.assertFalse(state['seeded'],
                         "the changes made while it was off reach nobody")
        self.assertFalse(state['was_off'])

    def test_staying_on_does_not_keep_re_offering(self):
        sync_engine.write_state({'seeded': True})
        for _ in range(3):
            sync_engine.ensure_started({'sync': {'enabled': True}})
        self.assertTrue(sync_engine.read_state()['seeded'])


class TestTheInterval(EngineTestCase):

    def test_a_nonsense_interval_falls_back_instead_of_crashing(self):
        sync_engine.ensure_started({'sync': {'enabled': True, 'interval_minutes': 'soon'}})
        self.assertEqual(sync_engine._interval_seconds(),
                         sync_engine.DEFAULT_INTERVAL_MINUTES * 60)

    def test_the_configured_interval_is_used(self):
        sync_engine.ensure_started({'sync': {'enabled': True, 'interval_minutes': 12}})
        self.assertEqual(sync_engine._interval_seconds(), 12 * 60)

    def test_an_absurdly_short_interval_is_raised_to_a_minute(self):
        """A cycle every few seconds would hammer the server for nothing."""
        sync_engine.ensure_started({'sync': {'enabled': True, 'interval_minutes': 0}})
        self.assertGreaterEqual(sync_engine._interval_seconds(), 60)

    def test_a_recent_success_holds_the_next_cycle_back(self):
        sync_engine.run_cycle(self.outbox)
        self.assertFalse(sync_engine._interval_elapsed(sync_engine.read_state()))


class TestWhatTheInterfaceIsTold(EngineTestCase):

    def test_before_anything_has_happened(self):
        snap = sync_engine.status_summary()
        self.assertEqual(snap['state'], 'never')
        self.assertEqual(snap['pending'], 0)

    def test_after_a_good_cycle(self):
        sync_engine.run_cycle(self.outbox)
        snap = sync_engine.status_summary()
        self.assertEqual(snap['state'], 'ok')
        self.assertIsNotNone(snap['last_ok'])
        self.assertIsNone(snap['error'])

    def test_after_a_bad_one(self):
        self.server.fail_with = 'tls_failed'
        sync_engine.run_cycle(self.outbox)
        snap = sync_engine.status_summary()
        self.assertEqual(snap['state'], 'failing')
        self.assertEqual(snap['error'], 'tls_failed')

    def test_it_counts_what_is_waiting_in_both_directions(self):
        self.queue('project.create', uid=P1, f={'name': 'P'})
        self.server.add_foreign('project.create', uid='c' * 16, f={'name': 'C'})
        snap = sync_engine.status_summary()
        self.assertEqual(snap['pending'], 1)

        sync_engine.run_cycle(self.outbox)
        snap = sync_engine.status_summary()
        self.assertEqual(snap['pending'], 0)
        self.assertEqual(snap['incoming'], 1)

    def test_it_asks_the_network_nothing(self):
        def forbidden(*_a, **_k):
            raise AssertionError("snapshot must never make a request")
        sync_client.push = forbidden
        sync_client.pull = forbidden
        sync_engine.status_summary()


class TestTakingTheServersSnapshot(EngineTestCase):
    """
    The log stops reaching all the way back once it has been compacted. A
    machine below that point is sent no operations at all - so unless it
    takes the snapshot, it sits there being told nothing and reporting
    success, which is the failure this whole path exists to avoid.
    """

    def _compacted(self, seq=5, document=None):
        """A server whose early log has been replaced by a snapshot."""
        for i in range(seq):
            self.server.add_foreign('project.create', uid='%016x' % i,
                                    f={'name': 'P%d' % i})
        self.server.snapshot = {'seq': seq, 'document': document or {
            'schema_version': 2, 'next_id': 1, '_deleted': [],
            'projects': [{'uid': P1, 'main_project_name': 'From the snapshot',
                          'status': 'open', 'last_started': None, 'tasks': []}],
        }}

    def test_the_snapshot_is_fetched_and_filed(self):
        self._compacted()

        result = sync_engine.run_cycle(self.outbox)

        self.assertTrue(result['ok'])
        records = sync_engine.read_inbox()
        self.assertEqual(len(records), 1, "the snapshot was not filed on its own")
        self.assertIn('snapshot', records[0])
        self.assertEqual(records[0]['base_seq'], 5)
        self.assertIn(('get_snapshot',), self.server.calls)

    def test_the_tail_after_it_comes_too(self):
        self._compacted()
        self.server.add_foreign('project.create', uid=T1, f={'name': 'Later'})

        sync_engine.run_cycle(self.outbox)

        records = sync_engine.read_inbox()
        arrived = [op['uid'] for record in records for op in record.get('ops') or []]
        self.assertIn(T1, arrived, "the operations after the snapshot were not fetched")
        self.assertEqual(max(int(r['base_seq']) for r in records), self.server.head)

    def test_what_was_already_waiting_below_the_point_is_dropped(self):
        """
        Operations fetched earlier sit below the snapshot. Applying them after
        it would lay older values over newer ones - the snapshot already
        speaks for them, so it replaces the queue rather than joining it.
        """
        sync_engine._append_inbox({'base_seq': 2, 'ops': [
            {'s': 1, 'op': 'project.create', 'uid': P1, 'f': {'name': 'Stale'}}]})
        self._compacted()

        sync_engine.run_cycle(self.outbox)

        records = sync_engine.read_inbox()
        self.assertTrue(all(int(r['base_seq']) >= 5 for r in records),
                        "something from below the snapshot survived")
        stale = [op for r in records for op in r.get('ops') or []
                 if op.get('f', {}).get('name') == 'Stale']
        self.assertEqual(stale, [])

    def test_a_server_that_asks_for_one_and_has_none_is_not_retried_for_ever(self):
        self._compacted()
        self.server.snapshot['seq'] = 5
        real_get = self.server.get_snapshot
        self.server.get_snapshot = lambda: {'ok': False, 'error': 'no_snapshot'}
        sync_client.get_snapshot = self.server.get_snapshot

        result = sync_engine.run_cycle(self.outbox)

        self.assertTrue(result['ok'], "a missing snapshot should not fail the cycle")
        self.assertEqual([], [r for r in sync_engine.read_inbox() if r.get('snapshot')])
        self.server.get_snapshot = real_get

    def test_a_snapshot_that_cannot_be_fetched_stops_the_cycle(self):
        """
        Carrying on would file the tail and move the cursor past a gap the
        log will never offer again.
        """
        self._compacted()
        sync_client.get_snapshot = lambda: {'ok': False, 'error': 'unreachable'}

        result = sync_engine.run_cycle(self.outbox)

        self.assertFalse(result['ok'])
        self.assertEqual(sync_engine.read_inbox(), [])
        self.assertEqual(sync_engine.read_state()['base_seq'], 0)


class TestTheSnapshotReachesTheDocument(EngineTestCase):
    """The other half: what the drawing thread does with what was filed."""

    DATA = 'test_engine_snapshot_data.json'

    def setUp(self):
        super().setUp()
        if os.path.exists(self.DATA):
            os.remove(self.DATA)
        self.tracker = TimeTracker(file_path=self.DATA, op_outbox=self.outbox)

    def tearDown(self):
        if os.path.exists(self.DATA):
            os.remove(self.DATA)
        super().tearDown()

    def test_a_fresh_machine_ends_up_with_the_snapshot_and_the_tail(self):
        """
        Acceptance, end to end: an empty document, a compacted server, and
        the machine has to arrive at what the log describes.
        """
        for i in range(4):
            self.server.add_foreign('project.create', uid='%016x' % i, f={'name': 'gone'})
        self.server.snapshot = {'seq': 4, 'document': {
            'schema_version': 2, 'next_id': 1, '_deleted': [],
            'projects': [{'uid': P1, 'main_project_name': 'Work', 'status': 'open',
                          'last_started': None,
                          'tasks': [{'uid': T1, 'id': 1, 'task_name': 'Existing',
                                     'status': 'open', 'time_entries': [],
                                     'due_date': None, 'today': False, 'note': '',
                                     'recurring': False, 'frequency': 'daily',
                                     'userdefined_days': 1, 'priority': 0,
                                     'last_started': None}]}],
        }}
        # And something that happened after the snapshot was taken.
        self.server.add_foreign('task.set', uid=T1, f={'priority': 7})

        sync_engine.run_cycle(self.outbox)
        summary = sync_engine.apply_pending(self.tracker)

        self.assertIsNotNone(summary)
        project = self.tracker._get_project('Work')
        self.assertIsNotNone(project, "the snapshot did not reach the document")
        task = project['tasks'][0]
        self.assertEqual(task['task_name'], 'Existing')
        self.assertEqual(task['priority'], 7, "the tail did not land on top of the snapshot")
        self.assertEqual(sync_engine.read_state()['base_seq'], self.server.head)
        self.assertEqual(self.tracker.data['_sync_seq'], self.server.head)

    def test_the_snapshot_does_not_undo_what_the_tail_did(self):
        """
        Ordering between the two passes, tested where it can actually go
        wrong: the snapshot unfolds into more operations than the tail's
        sequence numbers, so numbering alone would put it last.
        """
        self.server.add_foreign('project.create', uid=P1, f={'name': 'Work'})
        self.server.snapshot = {'seq': 1, 'document': {
            'schema_version': 2, 'next_id': 1, '_deleted': [],
            'projects': [{'uid': P1, 'main_project_name': 'Old name',
                          'status': 'open', 'last_started': None, 'tasks': []}],
        }}
        self.server.add_foreign('project.set', uid=P1, f={'name': 'New name'})

        sync_engine.run_cycle(self.outbox)
        sync_engine.apply_pending(self.tracker)

        self.assertIsNotNone(self.tracker._get_project('New name'))
        self.assertIsNone(self.tracker._get_project('Old name'))


class TestOfferingASnapshot(EngineTestCase):
    """
    The server never builds one, so a client that has everything has to offer
    it. Every gate here is about the same thing: only a machine that
    demonstrably holds the whole log may describe it to the others.
    """

    DATA = 'test_engine_offer_data.json'

    def setUp(self):
        super().setUp()
        if os.path.exists(self.DATA):
            os.remove(self.DATA)
        self.tracker = TimeTracker(file_path=self.DATA, op_outbox=self.outbox)
        self.tracker.data['projects'] = [
            {'uid': P1, 'main_project_name': 'Work', 'status': 'open',
             'last_started': None, 'tasks': []}]

    def tearDown(self):
        if os.path.exists(self.DATA):
            os.remove(self.DATA)
        super().tearDown()

    def caught_up(self, at=None, snapshot_at=0):
        at = sync_engine.SNAPSHOT_EVERY if at is None else at
        sync_engine.write_state({'base_seq': at, 'server_head': at,
                                 'snapshot_seq': snapshot_at, 'snapshot_tried': 0})
        return at

    def test_a_machine_that_holds_everything_stages_its_document(self):
        at = self.caught_up()

        staged = sync_engine.offer_snapshot(self.tracker)

        self.assertEqual(staged, at)
        self.assertEqual(sync_engine.read_state()['snapshot_staged'], at)
        self.assertTrue(os.path.exists(sync_engine.staged_snapshot_path()))

    def test_a_machine_that_is_not_caught_up_stages_nothing(self):
        sync_engine.write_state({'base_seq': 10, 'server_head': sync_engine.SNAPSHOT_EVERY,
                                 'snapshot_seq': 0, 'snapshot_tried': 0})
        self.assertEqual(sync_engine.offer_snapshot(self.tracker), 0)
        self.assertFalse(os.path.exists(sync_engine.staged_snapshot_path()))

    def test_a_log_that_has_not_run_far_enough_is_left_alone(self):
        self.caught_up(at=10)
        self.assertEqual(sync_engine.offer_snapshot(self.tracker), 0)

    def test_unsent_work_means_head_is_about_to_move(self):
        self.caught_up()
        self.queue('project.create', uid=T1, f={'name': 'Not sent yet'})
        self.assertEqual(sync_engine.offer_snapshot(self.tracker), 0)

    def test_changes_fetched_but_not_applied_mean_the_document_is_behind(self):
        self.caught_up()
        sync_engine._append_inbox({'base_seq': 9999, 'ops': []})
        self.assertEqual(sync_engine.offer_snapshot(self.tracker), 0)

    def test_a_document_with_nothing_in_it_is_never_offered(self):
        """
        The shape a data.json that was emptied or replaced takes. Offered for
        a log that has something in it, it would be handed to every other
        machine as the truth.
        """
        self.caught_up()
        self.tracker.data['projects'] = []
        self.assertEqual(sync_engine.offer_snapshot(self.tracker), 0)

    def test_it_is_not_prepared_again_while_one_is_waiting(self):
        self.caught_up()
        sync_engine.offer_snapshot(self.tracker)
        self.assertEqual(sync_engine.offer_snapshot(self.tracker), 0)

    def test_it_is_not_retried_on_every_redraw(self):
        at = self.caught_up()
        sync_engine.write_state({'snapshot_tried': int(time.time())})
        self.assertEqual(sync_engine.offer_snapshot(self.tracker), 0)

    def test_the_worker_sends_what_was_staged(self):
        at = self.caught_up(at=self.server.head)
        # A log the staged number actually describes.
        for i in range(sync_engine.SNAPSHOT_EVERY):
            self.server.add_foreign('project.create', uid='%016x' % i, f={'name': 'x'})
        sync_engine.write_state({'base_seq': self.server.head,
                                 'server_head': self.server.head})
        sync_engine.offer_snapshot(self.tracker)
        sync_engine.clear_inbox()

        sync_engine.run_cycle(self.outbox)

        self.assertIsNotNone(self.server.snapshot, "nothing reached the server")
        self.assertEqual(self.server.snapshot['seq'], self.server.head)
        self.assertEqual(sync_engine.read_state()['snapshot_staged'], 0)
        self.assertFalse(os.path.exists(sync_engine.staged_snapshot_path()))

    def test_a_staged_snapshot_the_log_has_outgrown_is_thrown_away(self):
        """
        The server takes one only from a machine at head, and rightly - so a
        stale one is discarded here rather than sent to be refused.
        """
        self.caught_up()
        sync_engine.offer_snapshot(self.tracker)
        self.server.add_foreign('project.create', uid=T1, f={'name': 'Moved on'})

        sync_engine.run_cycle(self.outbox)

        self.assertIsNone(self.server.snapshot)
        self.assertEqual(sync_engine.read_state()['snapshot_staged'], 0)
        self.assertFalse(os.path.exists(sync_engine.staged_snapshot_path()))
        # Not merely refused at the far end - never sent. The server would
        # turn it down anyway, so sending it costs a round trip carrying a
        # whole document and puts a refusal in the log for something this
        # machine could see for itself.
        self.assertEqual([c for c in self.server.calls if c[0] == 'put_snapshot'], [],
                         "a snapshot the log had outgrown was sent anyway")

    def test_a_snapshot_refused_on_its_merits_is_not_offered_again(self):
        for i in range(sync_engine.SNAPSHOT_EVERY):
            self.server.add_foreign('project.create', uid='%016x' % i, f={'name': 'x'})
        sync_engine.write_state({'base_seq': self.server.head,
                                 'server_head': self.server.head,
                                 'snapshot_seq': 0, 'snapshot_tried': 0})
        sync_engine.offer_snapshot(self.tracker)
        sync_engine.clear_inbox()
        self.server.snapshot_error = 'snapshot_too_large'

        sync_engine.run_cycle(self.outbox)

        self.assertEqual(sync_engine.read_state()['snapshot_staged'], 0)
        self.assertFalse(os.path.exists(sync_engine.staged_snapshot_path()),
                         "a document the server will never take was kept")

    def test_a_snapshot_that_could_not_be_sent_is_kept_for_the_next_try(self):
        for i in range(sync_engine.SNAPSHOT_EVERY):
            self.server.add_foreign('project.create', uid='%016x' % i, f={'name': 'x'})
        sync_engine.write_state({'base_seq': self.server.head,
                                 'server_head': self.server.head,
                                 'snapshot_seq': 0, 'snapshot_tried': 0})
        sync_engine.offer_snapshot(self.tracker)
        sync_engine.clear_inbox()
        self.server.snapshot_error = 'unreachable'

        sync_engine.run_cycle(self.outbox)

        self.assertEqual(sync_engine.read_state()['snapshot_staged'], self.server.head)
        self.assertTrue(os.path.exists(sync_engine.staged_snapshot_path()),
                        "a network failure threw the document away")


class TestTheAddressTheSettingsAskFor(EngineTestCase):
    """
    The setting and the credential are two addresses that can disagree: one is
    edited in the settings screen, the other was frozen when the token was
    issued. Carrying on regardless is what made changing the address look like
    it did nothing - the app kept talking to the old server while the screen
    showed the new one, and neither end said so.
    """

    # What EngineTestCase's stand-in credential points at.
    SIGNED_IN_TO = 'https://x/index.php'

    def settled_recently(self, **extra):
        """
        A state the worker will leave alone. ensure_started starts the thread,
        and a cycle running mid-assertion would rewrite exactly the fields
        these tests are about; a recent last_ok keeps it waiting out the
        interval instead.
        """
        changes = {'last_ok': int(time.time())}
        changes.update(extra)
        sync_engine.write_state(changes)

    def test_the_configured_address_is_recorded_for_the_cycle_to_check(self):
        self.settled_recently()
        sync_engine.ensure_started({'sync': {'enabled': True,
                                             'base_url': 'https://elsewhere.example/tc/'}})
        self.assertEqual(sync_engine.read_state()['configured_url'],
                         'https://elsewhere.example/tc/')

    def test_changing_the_address_lifts_the_pause_the_old_one_earned(self):
        """
        A wrong address backs off to half an hour, and the setting is the
        thing the user reaches for to correct it. Making them then wait out a
        pause the old address earned would look exactly like the bug they
        just tried to fix.
        """
        self.settled_recently(configured_url='https://old.example/tc/',
                              last_error='unreachable', failures=5,
                              next_attempt=int(time.time()) + 1800)

        sync_engine.ensure_started({'sync': {'enabled': True,
                                             'base_url': 'https://new.example/tc/'}})

        state = sync_engine.read_state()
        self.assertEqual(state['configured_url'], 'https://new.example/tc/')
        self.assertIsNone(state['last_error'])
        self.assertEqual(state['failures'], 0)
        self.assertEqual(state['next_attempt'], 0)

    def test_signing_in_to_the_new_server_settles_it_too(self):
        """
        Found by using it. The other way out of a disagreement leaves the
        setting untouched, so nothing above notices - and a terminal failure
        waits half an hour before a cycle would find out. The interface went
        on telling the user to fix what they had already fixed, and nothing
        tried in the meantime.
        """
        self.settled_recently(configured_url=self.SIGNED_IN_TO,
                              last_error='address_changed', failures=1,
                              next_attempt=int(time.time()) + 1800)

        sync_engine.ensure_started({'sync': {'enabled': True,
                                             'base_url': self.SIGNED_IN_TO}})

        state = sync_engine.read_state()
        self.assertIsNone(state['last_error'])
        self.assertEqual(state['failures'], 0)
        self.assertEqual(state['next_attempt'], 0)

    def test_a_failure_that_has_nothing_to_do_with_the_address_stands(self):
        """
        The clearing above is about one specific stale verdict. A server that
        is genuinely down must keep its backoff, or every redraw would start
        another attempt at it.
        """
        self.settled_recently(configured_url=self.SIGNED_IN_TO,
                              last_error='unreachable', failures=3,
                              next_attempt=int(time.time()) + 300)

        sync_engine.ensure_started({'sync': {'enabled': True,
                                             'base_url': self.SIGNED_IN_TO}})

        state = sync_engine.read_state()
        self.assertEqual(state['last_error'], 'unreachable')
        self.assertEqual(state['failures'], 3)

    def test_a_cycle_stops_when_the_address_is_not_the_one_signed_in_to(self):
        sync_engine.write_state({'configured_url': 'https://elsewhere.example/tc/'})
        self.queue('project.create', uid=P1, f={'name': 'P'})

        result = sync_engine.run_cycle(self.outbox)

        self.assertFalse(result['ok'])
        self.assertEqual(result['error'], 'address_changed')

    def test_nothing_is_sent_to_an_address_the_token_does_not_belong_to(self):
        """
        The point of stopping rather than following the setting. A token is a
        bearer credential, and the address is something a person types - so
        following it means handing that credential to whatever host a typo
        happens to name. The server would refuse it, but by then it has been
        sent.
        """
        sync_engine.write_state({'configured_url': 'https://typo.example/tc/'})
        self.queue('project.create', uid=P1, f={'name': 'P'})

        sync_engine.run_cycle(self.outbox)

        self.assertEqual(self.server.calls, [], "a request went out anyway")
        self.assertEqual(len(self.outbox.pending()), 1,
                         "the queued change was dropped rather than held")

    def test_it_is_not_retried_until_something_changes(self):
        """
        Only signing in again resolves this, so backing off in steps of a
        minute would be a request a minute against a server that will keep
        saying no.
        """
        sync_engine.write_state({'configured_url': 'https://elsewhere.example/tc/'})
        sync_engine.run_cycle(self.outbox)

        state = sync_engine.read_state()
        self.assertEqual(state['last_error'], 'address_changed')
        self.assertGreaterEqual(state['next_attempt'] - int(time.time()),
                                sync_engine.BACKOFF_MAX_SECONDS - 5)

    def test_the_same_address_written_differently_is_not_a_change(self):
        """
        The setting holds what the user typed and the credential holds the
        normalised endpoint, so these differ as strings on every ordinary
        installation. Treating that as a move would stop synchronisation for
        everybody.
        """
        for spelling in ('https://x', 'https://x/', 'https://x/index.php',
                         'https://X/index.php'):
            with self.subTest(spelling=spelling):
                sync_engine.write_state({'configured_url': spelling,
                                         'last_error': None, 'failures': 0,
                                         'next_attempt': 0})
                result = sync_engine.run_cycle(self.outbox)
                self.assertTrue(result['ok'], result.get('error'))

    def test_nothing_configured_yet_is_not_a_change_either(self):
        sync_engine.write_state({'configured_url': None})
        self.assertTrue(sync_engine.run_cycle(self.outbox)['ok'])


if __name__ == '__main__':
    unittest.main()
