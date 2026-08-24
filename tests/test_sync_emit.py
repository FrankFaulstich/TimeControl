import os
import sys
import unittest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from tt.TimeTracker import TimeTracker

TEST_FILE_PATH = 'test_emit_data.json'


class RecordingOutbox:
    """
    Stands in for the real queue, so these tests say nothing about files and
    everything about which intentions each operation reports.
    """

    def __init__(self):
        self.ops = []

    def append(self, op, **fields):
        self.ops.append(dict(op=op, **fields))
        return len(self.ops)

    # -- helpers the tests read with ------------------------------------

    def names(self):
        return [o['op'] for o in self.ops]

    def of(self, op):
        return [o for o in self.ops if o['op'] == op]

    def reset(self):
        self.ops.clear()


class TestOperationsAreReported(unittest.TestCase):

    def setUp(self):
        if os.path.exists(TEST_FILE_PATH):
            os.remove(TEST_FILE_PATH)
        self.outbox = RecordingOutbox()
        self.tracker = TimeTracker(file_path=TEST_FILE_PATH, op_outbox=self.outbox)

    def tearDown(self):
        if os.path.exists(TEST_FILE_PATH):
            os.remove(TEST_FILE_PATH)

    def _project_uid(self, name):
        return self.tracker._get_project(name)['uid']

    def _task_uid(self, project, task):
        return self.tracker._get_task(project, task)['uid']

    # -- projects --------------------------------------------------------

    def test_adding_a_project(self):
        self.tracker.add_main_project("P")
        op = self.outbox.of('project.create')[0]
        self.assertEqual(op['uid'], self._project_uid("P"))
        self.assertEqual(op['f']['name'], "P")

    def test_renaming_reports_a_change_not_a_replacement(self):
        """
        The uid stays the same, so the other machine renames the project it
        already has. Reporting this as delete-plus-create would take the
        project's tasks and tracked time down with it.
        """
        self.tracker.add_main_project("Old")
        uid = self._project_uid("Old")
        self.outbox.reset()

        self.tracker.rename_main_project("Old", "New")

        self.assertEqual(self.outbox.names(), ['project.set'])
        self.assertEqual(self.outbox.ops[0]['uid'], uid)
        self.assertEqual(self.outbox.ops[0]['f'], {"name": "New"})

    def test_closing_and_reopening_a_project(self):
        self.tracker.add_main_project("P")
        self.outbox.reset()
        self.tracker.close_main_project("P")
        self.tracker.reopen_main_project("P")
        self.assertEqual([o['f']['status'] for o in self.outbox.of('project.set')],
                         ['closed', 'open'])

    def test_deleting_a_project_reports_its_tasks_too(self):
        self.tracker.add_main_project("P")
        self.tracker.add_task("P", "A")
        self.tracker.add_task("P", "B")
        project_uid = self._project_uid("P")
        task_uids = {self._task_uid("P", "A"), self._task_uid("P", "B")}
        self.outbox.reset()

        self.tracker.delete_main_project("P")

        self.assertEqual([o['uid'] for o in self.outbox.of('project.delete')], [project_uid])
        self.assertEqual({o['uid'] for o in self.outbox.of('task.delete')}, task_uids)

    # -- tasks -----------------------------------------------------------

    def test_adding_a_task_names_its_project(self):
        self.tracker.add_main_project("P")
        self.outbox.reset()
        self.tracker.add_task("P", "T", priority=5)

        op = self.outbox.of('task.create')[0]
        self.assertEqual(op['project'], self._project_uid("P"))
        self.assertEqual(op['f']['task_name'], "T")
        self.assertEqual(op['f']['priority'], 5)
        # Local-only bookkeeping must not travel: the integer id is a
        # per-machine counter and the entries have operations of their own.
        self.assertNotIn('id', op['f'])
        self.assertNotIn('time_entries', op['f'])
        self.assertNotIn('uid', op['f'])

    def test_update_reports_only_what_changed(self):
        self.tracker.add_main_project("P")
        self.tracker.add_task("P", "T", priority=1, note="x")
        self.outbox.reset()

        self.tracker.update_task("P", "T", priority=7)

        self.assertEqual(self.outbox.names(), ['task.set'])
        self.assertEqual(self.outbox.ops[0]['f'], {"priority": 7})

    def test_an_untouched_due_date_is_not_reported_as_removed(self):
        """
        An omitted due_date used to be written as None, so changing one
        unrelated field reported the due date as cleared too - and the other
        machine faithfully cleared it.
        """
        self.tracker.add_main_project("P")
        self.tracker.add_task("P", "T", due_date="2026-08-09", priority=1)
        self.outbox.reset()

        self.tracker.update_task("P", "T", priority=7)

        self.assertEqual(self.outbox.ops[0]['f'], {"priority": 7})

    def test_clearing_a_due_date_is_reported(self):
        self.tracker.add_main_project("P")
        self.tracker.add_task("P", "T", due_date="2026-08-09")
        self.outbox.reset()

        self.tracker.update_task("P", "T", clear_due_date=True)

        self.assertEqual(self.outbox.names(), ['task.set'])
        self.assertEqual(self.outbox.ops[0]['f'], {"due_date": None})

    def test_a_save_that_changes_nothing_reports_nothing(self):
        self.tracker.add_main_project("P")
        self.tracker.add_task("P", "T", due_date="2026-08-09", priority=3)
        self.outbox.reset()

        self.tracker.update_task("P", "T", due_date="2026-08-09", priority=3)

        self.assertEqual(self.outbox.ops, [])

    def test_moving_a_task_keeps_its_identity(self):
        """
        move_task takes the task out of one list and puts it in another. It is
        the same task, so it must be reported as moved - reported as a
        deletion it would be destroyed on the other machine.
        """
        self.tracker.add_main_project("From")
        self.tracker.add_main_project("To")
        self.tracker.add_task("From", "T")
        uid = self._task_uid("From", "T")
        self.outbox.reset()

        self.tracker.move_task("From", "T", "To")

        self.assertEqual(self.outbox.names(), ['task.move'])
        self.assertEqual(self.outbox.ops[0]['uid'], uid)
        self.assertEqual(self.outbox.ops[0]['project'], self._project_uid("To"))
        self.assertEqual(self.outbox.of('task.delete'), [])

    # -- time ------------------------------------------------------------

    def test_starting_and_stopping_work(self):
        self.tracker.add_main_project("P")
        self.tracker.add_task("P", "T")
        self.outbox.reset()

        self.tracker.start_work("P", "T")
        entry_uid = self.tracker._get_task("P", "T")['time_entries'][0]['uid']
        add = self.outbox.of('entry.add')[0]
        self.assertEqual(add['uid'], entry_uid)
        self.assertEqual(add['task'], self._task_uid("P", "T"))
        # The ordering is sent, not left to be inferred from the entry.
        self.assertIn('last_started', self.outbox.of('task.set')[0]['f'])
        self.assertIn('last_started', self.outbox.of('project.set')[0]['f'])

        self.outbox.reset()
        self.tracker.stop_work()
        close = self.outbox.of('entry.close')[0]
        self.assertEqual(close['uid'], entry_uid)
        self.assertTrue(close['end'])

    # -- restructuring ---------------------------------------------------

    def test_promoting_moves_the_entries_before_the_task_is_deleted(self):
        """
        Reported as parts the other machine already understands. Crucially
        the entries are re-parented rather than recreated, so no tracked time
        is duplicated - and their move is reported before the old task's
        deletion, so the deletion cannot sweep them up.
        """
        self.tracker.add_main_project("P")
        self.tracker.add_task("P", "Rising")
        self.tracker.start_work("P", "Rising")
        self.tracker.stop_work()
        task = self.tracker._get_task("P", "Rising")
        old_task_uid, entry_uid = task['uid'], task['time_entries'][0]['uid']
        self.outbox.reset()

        ok, _msg = self.tracker.promote_task_to_project("P", "Rising")
        self.assertTrue(ok)

        names = self.outbox.names()
        self.assertIn('project.create', names)
        self.assertIn('task.create', names)

        moved = self.outbox.of('entry.move')[0]
        self.assertEqual(moved['uid'], entry_uid)
        # Same entry, not a new one.
        self.assertEqual(self.outbox.of('entry.add'), [])

        deleted = self.outbox.of('task.delete')[0]
        self.assertEqual(deleted['uid'], old_task_uid)
        self.assertLess(names.index('entry.move'), names.index('task.delete'))

    def test_demoting_reports_the_same_shape(self):
        self.tracker.add_main_project("Parent")
        self.tracker.add_main_project("Sinking")
        self.tracker.add_task("Sinking", "Inner")
        self.tracker.start_work("Sinking", "Inner")
        self.tracker.stop_work()
        entry_uid = self.tracker._get_task("Sinking", "Inner")['time_entries'][0]['uid']
        project_uid = self._project_uid("Sinking")
        self.outbox.reset()

        ok, _msg = self.tracker.demote_main_project("Sinking", "Parent")
        self.assertTrue(ok)

        names = self.outbox.names()
        self.assertEqual(self.outbox.of('entry.move')[0]['uid'], entry_uid)
        self.assertEqual(self.outbox.of('project.delete')[0]['uid'], project_uid)
        self.assertLess(names.index('entry.move'), names.index('project.delete'))

    # -- deliberate silences ---------------------------------------------

    def test_migration_reports_nothing(self):
        """
        Adding uids and defaults changes the shape of the document, not its
        content, and the other machine performs the same migration itself.
        """
        import json
        legacy = {"projects": [{"main_project_name": "Old",
                                "sub_projects": [{"task_name": "T", "time_entries": []}]}]}
        with open(TEST_FILE_PATH, 'w') as f:
            json.dump(legacy, f)

        outbox = RecordingOutbox()
        TimeTracker(file_path=TEST_FILE_PATH, op_outbox=outbox)
        self.assertEqual(outbox.ops, [])


class TestSyncOffByDefault(unittest.TestCase):

    def setUp(self):
        if os.path.exists(TEST_FILE_PATH):
            os.remove(TEST_FILE_PATH)

    def tearDown(self):
        if os.path.exists(TEST_FILE_PATH):
            os.remove(TEST_FILE_PATH)

    def test_no_queue_means_no_recording_and_no_errors(self):
        """
        Every installation without synchronisation configured - which is all
        of them until somebody switches it on - must behave exactly as before.
        """
        tracker = TimeTracker(file_path=TEST_FILE_PATH)
        self.assertIsNone(tracker.op_outbox)
        tracker.add_main_project("P")
        tracker.add_task("P", "T")
        tracker.start_work("P", "T")
        tracker.stop_work()
        self.assertTrue(tracker.delete_main_project("P"))

    def test_a_broken_queue_never_breaks_the_app(self):
        """
        Recording a change must not be able to stop the user tracking time.
        A queue that cannot be written costs a sync, not the application.
        """
        class ExplodingOutbox:
            def append(self, op, **fields):
                raise OSError("disk full")

        tracker = TimeTracker(file_path=TEST_FILE_PATH, op_outbox=ExplodingOutbox())
        tracker.add_main_project("P")
        tracker.add_task("P", "T")
        self.assertIsNotNone(tracker._get_task("P", "T"))


OTHER_FILE_PATH = 'test_emit_other_data.json'


class TestTheDailyTodaySweeps(unittest.TestCase):
    """
    These two sweeps used to change the 'today' flag without telling the
    server, on the reasoning that both machines run the same rule against the
    same synced due dates and so reach the same answer unaided.

    Comparing two real installations disproved it. The flag is not a function
    of the due date alone but of the due date *and the moment the sweep ran*,
    and the machines do not sweep at the same moment. Eleven tasks had drifted
    apart, and because neither machine ever sent its conclusion, they would
    have stayed apart for good.
    """

    def setUp(self):
        for path in (TEST_FILE_PATH, OTHER_FILE_PATH):
            if os.path.exists(path):
                os.remove(path)
        self.outbox = RecordingOutbox()
        self.tracker = TimeTracker(file_path=TEST_FILE_PATH, op_outbox=self.outbox)

    def tearDown(self):
        for path in (TEST_FILE_PATH, OTHER_FILE_PATH):
            if os.path.exists(path):
                os.remove(path)

    def today_str(self):
        from datetime import date
        return date.today().isoformat()

    def test_clearing_an_overdue_flag_is_reported(self):
        self.tracker.add_main_project("P")
        self.tracker.add_task("P", "Overdue", due_date="2020-01-01", today=True)
        self.outbox.reset()

        self.tracker.cleanup_overdue_today_tasks()

        self.assertEqual([o['f'] for o in self.outbox.of('task.set')], [{'today': False}])

    def test_setting_a_due_flag_is_reported(self):
        self.tracker.add_main_project("P")
        self.tracker.add_task("P", "DueToday", due_date=self.today_str())
        self.outbox.reset()

        self.tracker.set_today_flag_for_due_tasks()

        self.assertEqual([o['f'] for o in self.outbox.of('task.set')], [{'today': True}])

    def test_a_sweep_with_nothing_to_do_stays_silent(self):
        """
        Which is also what stops this bouncing between the machines: a sweep
        only speaks when it actually changes a flag, so one that has just been
        told the answer says nothing back.
        """
        self.tracker.add_main_project("P")
        self.tracker.add_task("P", "DueToday", due_date=self.today_str())
        self.tracker.set_today_flag_for_due_tasks()
        self.outbox.reset()

        self.tracker.set_today_flag_for_due_tasks()
        self.tracker.cleanup_overdue_today_tasks()

        self.assertEqual(self.outbox.ops, [])

    def test_the_other_machine_stops_disagreeing(self):
        """
        The drift itself, reproduced. One machine sweeps while the task is
        still open and marks it; the task is then completed, which the other
        machine learns about. Its own sweep will not mark a task that is no
        longer open, so without the operation the two disagree - permanently,
        since neither would ever mention it again.
        """
        import shutil
        from tt.sync_apply import apply_ops

        self.tracker.add_main_project("P")
        self.tracker.add_task("P", "Recurring", due_date=self.today_str())
        shutil.copyfile(TEST_FILE_PATH, OTHER_FILE_PATH)

        other_outbox = RecordingOutbox()
        other = TimeTracker(file_path=OTHER_FILE_PATH, op_outbox=other_outbox)
        # What the second machine already knows: the task has been completed.
        other.data['projects'][0]['tasks'][0]['status'] = TimeTracker.STATUS_DONE

        # First machine's morning sweep, while it was still open there.
        self.outbox.reset()
        self.tracker.set_today_flag_for_due_tasks()
        emitted = self.outbox.of('task.set')
        self.assertEqual(len(emitted), 1)

        # Left to itself, the second machine would never reach that answer.
        other_outbox.reset()
        other.set_today_flag_for_due_tasks()
        self.assertFalse(other.data['projects'][0]['tasks'][0]['today'],
                         "the sweep alone would have agreed, and the drift is not reproduced")

        apply_ops(other.data, [dict(emitted[0], s=1)])

        self.assertTrue(other.data['projects'][0]['tasks'][0]['today'],
                        "the operation did not carry the flag across")
        other_outbox.reset()
        other.set_today_flag_for_due_tasks()
        other.cleanup_overdue_today_tasks()
        self.assertEqual(other_outbox.ops, [],
                         "the second machine answered back, which would bounce")


if __name__ == '__main__':
    unittest.main()
