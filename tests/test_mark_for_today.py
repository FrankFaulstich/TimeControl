"""
The star in Task Planning, on a task whose due date has passed (issue #676).

Marked for today and left overdue, such a task lost its mark again at the next
redraw of Today's Tasks: cleanup_overdue_today_tasks() takes the mark off every
task whose due date is in the past. So the star now makes an overdue task due
today as well - TimeTracker.mark_task_for_today(), which both stars in Task
Planning call.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import date, timedelta
from unittest import mock

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
VIEW = os.path.join(REPO, 'sl', 'SL_Menu.py')
sys.path.append(REPO)

try:
    from streamlit.testing.v1 import AppTest
except ImportError:     # pragma: no cover - streamlit is in requirements-ci.txt
    AppTest = None

from tt.TimeTracker import TimeTracker

TODAY = date.today()


def _day(offset):
    return (TODAY + timedelta(days=offset)).isoformat()


class Outbox:
    """The sync queue, kept in a list."""

    def __init__(self):
        self.ops = []

    def append(self, op, **fields):
        self.ops.append((op, fields))


class _Scratch:
    """A folder of its own for data.json and config.json, and the config directory."""

    def setUp(self):
        self.scratch = tempfile.mkdtemp(prefix='tc-mark-today-')
        self.addCleanup(shutil.rmtree, self.scratch, ignore_errors=True)
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(self.scratch)
        environment = mock.patch.dict(
            os.environ, {'TC_CONFIG_DIR': os.path.join(self.scratch, 'config')})
        environment.start()
        self.addCleanup(environment.stop)
        os.makedirs(os.path.join(self.scratch, 'config'))
        with open('config.json', 'w', encoding='utf-8') as handle:
            json.dump({'language': 'en', 'data_file': 'data.json',
                       'mcp_server_enabled': False, 'update': {'github_repo': ''}}, handle)

    @staticmethod
    def _tracker(outbox=None):
        return TimeTracker('data.json', op_outbox=outbox or Outbox())

    @staticmethod
    def _stored(project, name):
        """The task as the file holds it now."""
        found = [t for t in TimeTracker('data.json', op_outbox=Outbox()).list_tasks(project)
                 if t['task_name'] == name]
        return found[0]


class TestMarkingATaskForToday(_Scratch, unittest.TestCase):

    def setUp(self):
        super().setUp()
        tracker = self._tracker()
        tracker.add_main_project('Work')
        tracker.add_task('Work', 'Overdue', due_date=_day(-3))
        tracker.add_task('Work', 'Later', due_date=_day(2))
        tracker.add_task('Work', 'Now', due_date=_day(0))
        tracker.add_task('Work', 'Undated')

    def test_an_overdue_task_is_due_today_once_marked(self):
        self.assertTrue(self._tracker().mark_task_for_today('Work', 'Overdue'))
        task = self._stored('Work', 'Overdue')
        self.assertTrue(task['today'])
        self.assertEqual(task['due_date'], _day(0))

    def test_so_is_one_that_was_due_only_yesterday(self):
        """The first day the sweep would take the mark off."""
        tracker = self._tracker()
        tracker.add_task('Work', 'Yesterday', due_date=_day(-1))
        tracker.mark_task_for_today('Work', 'Yesterday')
        self.assertEqual(self._stored('Work', 'Yesterday')['due_date'], _day(0))
        self.assertFalse(self._tracker().cleanup_overdue_today_tasks())

    def test_and_keeps_its_mark_through_the_sweep(self):
        """What the issue is about: the sweep used to take the mark off again."""
        self._tracker().mark_task_for_today('Work', 'Overdue')
        self.assertFalse(self._tracker().cleanup_overdue_today_tasks())
        self.assertTrue(self._stored('Work', 'Overdue')['today'])

    def test_a_task_due_later_keeps_its_due_date(self):
        self._tracker().mark_task_for_today('Work', 'Later')
        task = self._stored('Work', 'Later')
        self.assertTrue(task['today'])
        self.assertEqual(task['due_date'], _day(2))

    def test_so_does_one_due_today(self):
        self._tracker().mark_task_for_today('Work', 'Now')
        self.assertEqual(self._stored('Work', 'Now')['due_date'], _day(0))

    def test_a_task_without_a_due_date_is_not_given_one(self):
        self._tracker().mark_task_for_today('Work', 'Undated')
        task = self._stored('Work', 'Undated')
        self.assertTrue(task['today'])
        self.assertIsNone(task['due_date'])

    def test_one_change_is_sent_with_both_fields(self):
        outbox = Outbox()
        self._tracker(outbox).mark_task_for_today('Work', 'Overdue')
        self.assertEqual([(op, fields['f']) for op, fields in outbox.ops],
                         [('task.set', {'today': True, 'due_date': _day(0)})])

    def test_and_only_the_mark_for_a_task_that_is_not_overdue(self):
        outbox = Outbox()
        self._tracker(outbox).mark_task_for_today('Work', 'Later')
        self.assertEqual([(op, fields['f']) for op, fields in outbox.ops],
                         [('task.set', {'today': True})])

    def test_the_due_date_is_the_one_in_the_file_not_the_one_last_shown(self):
        """
        Moved to the future elsewhere - by the MCP server, say - after this
        tracker last read it: it is not overdue any more, and stays where it is.
        """
        shown = self._tracker()
        self.assertEqual(self._stored('Work', 'Overdue')['due_date'], _day(-3))
        self._tracker().update_task('Work', 'Overdue', due_date=_day(5))
        shown.mark_task_for_today('Work', 'Overdue')
        task = self._stored('Work', 'Overdue')
        self.assertTrue(task['today'])
        self.assertEqual(task['due_date'], _day(5))

    def test_the_id_decides_between_tasks_of_one_name(self):
        tracker = self._tracker()
        tracker.add_task('Work', 'Twin', due_date=_day(-1))
        tracker.add_task('Work', 'Twin', due_date=_day(-2))
        twins = [t for t in tracker.list_tasks('Work') if t['task_name'] == 'Twin']
        tracker.mark_task_for_today('Work', 'Twin', task_id=twins[1]['id'])
        after = {t['id']: t for t in self._tracker().list_tasks('Work') if t['task_name'] == 'Twin'}
        self.assertEqual((after[twins[0]['id']]['due_date'], after[twins[0]['id']]['today']),
                         (_day(-1), False))
        self.assertEqual((after[twins[1]['id']]['due_date'], after[twins[1]['id']]['today']),
                         (_day(0), True))

    def test_a_task_that_is_not_there_changes_nothing(self):
        with open('data.json', 'rb') as handle:
            before = handle.read()
        outbox = Outbox()
        self.assertFalse(self._tracker(outbox).mark_task_for_today('Work', 'Missing'))
        self.assertFalse(self._tracker(outbox).mark_task_for_today('Nowhere', 'Overdue'))
        with open('data.json', 'rb') as handle:
            self.assertEqual(handle.read(), before)
        self.assertEqual(outbox.ops, [])

    def test_a_start_date_is_left_as_it_is(self):
        tracker = self._tracker()
        tracker.add_task('Work', 'Stretch', start_date=_day(-6), due_date=_day(-2))
        tracker.mark_task_for_today('Work', 'Stretch')
        task = self._stored('Work', 'Stretch')
        self.assertEqual((task['start_date'], task['due_date']), (_day(-6), _day(0)))

    def test_a_finished_task_only_gets_the_mark(self):
        """
        Done or closed, its due date is the one it was finished against. The
        list of all in Task Planning shows such tasks, with a star.
        """
        tracker = self._tracker()
        tracker.add_task('Work', 'Finished', due_date=_day(-10))
        tracker.update_task('Work', 'Finished', status='done')
        tracker.add_task('Work', 'Shelved', due_date=_day(-20))
        tracker.update_task('Work', 'Shelved', status='closed')
        for name, due in (('Finished', _day(-10)), ('Shelved', _day(-20))):
            with self.subTest(task=name):
                outbox = Outbox()
                self._tracker(outbox).mark_task_for_today('Work', name)
                task = self._stored('Work', name)
                self.assertEqual((task['today'], task['due_date']), (True, due))
                self.assertEqual([fields['f'] for _, fields in outbox.ops], [{'today': True}])

    def test_a_recurring_task_keeps_the_gap_between_its_start_and_due_date(self):
        """
        The gap is handed on to every later instance, so stretched by the days
        the task was overdue it would make each of them current that much
        earlier.
        """
        tracker = self._tracker()
        tracker.add_task('Work', 'Window', start_date=_day(-6), due_date=_day(-2),
                         recurring=True, frequency='weekly')
        tracker.mark_task_for_today('Work', 'Window')
        task = self._stored('Work', 'Window')
        self.assertEqual((task['start_date'], task['due_date']), (_day(-4), _day(0)))
        self._tracker().update_task('Work', 'Window', status='done', task_id=task['id'])
        upcoming = [t for t in self._tracker().list_tasks('Work')
                    if t['task_name'] == 'Window' and t['status'] == 'open']
        self.assertEqual([(t['start_date'], t['due_date']) for t in upcoming], [(_day(3), _day(7))])

    def test_a_recurring_task_comes_round_again_from_today(self):
        """Its next instance follows from the due date it is completed with."""
        tracker = self._tracker()
        tracker.add_task('Work', 'Weekly', due_date=_day(-3), recurring=True, frequency='weekly')
        tracker.mark_task_for_today('Work', 'Weekly')
        task = self._stored('Work', 'Weekly')
        self.assertEqual((task['due_date'], task['recurring'], task['frequency']),
                         (_day(0), True, 'weekly'))
        self._tracker().update_task('Work', 'Weekly', status='done', task_id=task['id'])
        upcoming = [t for t in self._tracker().list_tasks('Work')
                    if t['task_name'] == 'Weekly' and t['status'] == 'open']
        self.assertEqual([t['due_date'] for t in upcoming], [_day(7)])


@unittest.skipIf(AppTest is None, 'streamlit is not installed')
class TestTheStarInTaskPlanning(_Scratch, unittest.TestCase):

    FILTERS = ('Today', 'Tomorrow', 'Weekly overview', 'Overdue tasks', 'Unplanned tasks', 'All')

    def setUp(self):
        super().setUp()
        tracker = self._tracker()
        tracker.add_main_project('Work')
        tracker.add_task('Work', 'Overdue', due_date=_day(-3))
        tracker.add_task('Work', 'Soon', due_date=_day(3))
        self.app = AppTest.from_file(VIEW, default_timeout=20)
        self.app.session_state['menu'] = 'task_planning'
        self._run()

    def _run(self):
        self.app.run()
        self.assertFalse(self.app.exception, [e.value for e in self.app.exception])

    def _filter(self, label):
        self.app.selectbox(key='task_planning_filter_select').set_value(label).run()
        self.assertFalse(self.app.exception, [e.value for e in self.app.exception])

    def _star(self, prefix, name):
        found = [b for b in self.app.button if b.key and b.key.startswith(prefix + 'Work_' + name + '_')]
        self.assertEqual(len(found), 1, [b.key for b in self.app.button if b.key])
        found[0].click().run()
        self.assertFalse(self.app.exception, [e.value for e in self.app.exception])

    def _listed(self, prefix):
        return sorted(b.key[len(prefix + 'Work_'):].rsplit('_', 1)[0]
                      for b in self.app.button if b.key and b.key.startswith(prefix + 'Work_'))

    def _on_today(self):
        """Today's Tasks, which runs the sweep on every redraw."""
        self.app.session_state['menu'] = 'today_view'
        self._run()
        return sorted(b.key[len('start_today_task_Work_'):].rsplit('_', 1)[0]
                      for b in self.app.button if b.key and b.key.startswith('start_today_task_Work_'))

    def test_starred_among_the_overdue_it_is_due_today(self):
        self._filter('Overdue tasks')
        self._star('today_task_planning_', 'Overdue')
        task = self._stored('Work', 'Overdue')
        self.assertEqual((task['today'], task['due_date']), (True, _day(0)))

    def test_and_moves_from_that_list_to_today_s(self):
        self._filter('Overdue tasks')
        self._star('today_task_planning_', 'Overdue')
        self._run()
        self.assertEqual(self._listed('today_task_planning_'), [])
        self._filter('Today')
        self.assertEqual(self._listed('today_task_planning_'), ['Overdue'])

    def test_and_is_still_there_in_today_s_tasks(self):
        """The sweep that clears overdue marks runs there, and used to take this one off."""
        self._filter('Overdue tasks')
        self._star('today_task_planning_', 'Overdue')
        self.assertEqual(self._on_today(), ['Overdue'])
        self.assertTrue(self._stored('Work', 'Overdue')['today'])

    def test_the_same_when_starred_in_the_list_of_all(self):
        self._filter('All')
        self._star('today_task_planning_', 'Overdue')
        self.assertEqual(self._on_today(), ['Overdue'])
        self.assertEqual(self._stored('Work', 'Overdue')['due_date'], _day(0))

    def test_a_task_that_is_not_overdue_keeps_its_due_date(self):
        self._filter('All')
        self._star('today_task_planning_', 'Soon')
        task = self._stored('Work', 'Soon')
        self.assertEqual((task['today'], task['due_date']), (True, _day(3)))

    def test_a_finished_task_in_the_list_of_all_keeps_its_due_date(self):
        tracker = self._tracker()
        tracker.add_task('Work', 'Finished', due_date=_day(-10))
        tracker.update_task('Work', 'Finished', status='done')
        tracker.add_task('Work', 'Shelved', due_date=_day(-20))
        tracker.update_task('Work', 'Shelved', status='closed')
        self._filter('All')
        self._star('today_task_planning_', 'Finished')
        self._run()
        self._star('today_task_planning_', 'Shelved')
        self.assertEqual(self._stored('Work', 'Finished')['due_date'], _day(-10))
        self.assertEqual(self._stored('Work', 'Shelved')['due_date'], _day(-20))
        self.assertEqual(self._on_today(), [])

    def test_a_mark_given_elsewhere_to_an_overdue_task_does_not_lock_its_star(self):
        """
        Given by the edit form or the MCP server while the list is up. The
        list sweeps it off before it draws the row, as Today's Tasks would,
        so the star can be pressed and make the task due today.
        """
        self._filter('Overdue tasks')
        self._tracker().update_task('Work', 'Overdue', today=True)
        self._run()
        star = [b for b in self.app.button if b.key and b.key.startswith('today_task_planning_Work_Overdue_')]
        self.assertFalse(star[0].disabled)
        self._star('today_task_planning_', 'Overdue')
        self.assertEqual(self._on_today(), ['Overdue'])
        self.assertEqual(self._stored('Work', 'Overdue')['due_date'], _day(0))

    def test_and_so_from_the_weekly_overview(self):
        """
        That list holds no overdue task, so its star only sets the mark and
        leaves a due date in the coming days where it is.
        """
        self._filter('Weekly overview')
        self._star('today_task_planning_weekly_', 'Soon')
        task = self._stored('Work', 'Soon')
        self.assertEqual((task['today'], task['due_date']), (True, _day(3)))
        self.assertEqual(self._on_today(), ['Soon'])

    def test_the_star_of_a_task_marked_already_cannot_be_pressed(self):
        self._tracker().mark_task_for_today('Work', 'Soon')
        self._filter('All')
        star = [b for b in self.app.button if b.key and b.key.startswith('today_task_planning_Work_Soon_')]
        self.assertTrue(star[0].disabled)


if __name__ == '__main__':
    unittest.main()
