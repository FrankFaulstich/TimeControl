"""
A closed project group in Today and in Planning has nothing built inside it
(issue #644).

st.expander builds all of its content whether it is open or not, and only
hides it - every task row's widgets, and some 94 nodes in the page for each.
Now the rows are left out of a closed group, and the run that opening it
starts is the one that draws them. Which groups are open is remembered as
before: the expander's own state while the screen is up, the plain mirror in
session state across a trip away from it.

The clicks below are what the browser sends: the expander's new state under
its element id, with the next run. AppTest has nothing of its own for an
expander - see _Groups._click(). Some tests send a click together with a
row's button or a priority in one run, as the server gets them when the
second comes while the run of the first is still on its way.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import date
from unittest import mock

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
VIEW = os.path.join(REPO, 'sl', 'SL_Menu.py')
sys.path.append(REPO)

try:
    from streamlit.testing.v1 import AppTest
except ImportError:     # pragma: no cover - streamlit is in requirements-ci.txt
    AppTest = None

from tt.TimeTracker import TimeTracker


class NoOutbox:
    """The sync queue, switched off whatever config.json says."""

    def append(self, op, **fields):
        pass


class _Groups:
    """One of the two tabs, two projects with tasks for today, and clicks on their groups."""

    MENU = None
    ROW_KEY = None      # what every task row's start button key begins with
    EDIT_KEY = None     # ... and its edit button key
    OTHER_MENU = None   # the other of the two tabs
    OTHER_ROW_KEY = None

    def setUp(self):
        self.scratch = tempfile.mkdtemp(prefix='tc-groups-')
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

        # Due today and marked for today, so that both tabs list them as they
        # open: Planning's first filter is the due date, Today's the flag.
        tracker = TimeTracker('data.json', op_outbox=NoOutbox())
        for project, tasks in (('Work', ('Typing', 'Reading')), ('Home', ('Cooking',))):
            tracker.add_main_project(project)
            for task in tasks:
                tracker.add_task(project, task, due_date=date.today().isoformat(), today=True)

        self.app = AppTest.from_file(VIEW, default_timeout=20)
        self.app.session_state['menu'] = self.MENU
        self._run()

    def _run(self):
        self.app.run()
        self._no_exception()

    def _no_exception(self):
        self.assertFalse(self.app.exception, [e.value for e in self.app.exception])

    def _group(self, project):
        found = [e for e in self.app.expander if e.label.startswith(project + ' (')]
        self.assertEqual(len(found), 1, [e.label for e in self.app.expander])
        return found[0]

    def _click(self, project, open_, *also):
        """
        The header of a project's group clicked: its new state, and the run
        that follows - with whatever else the page holds, and with `also`,
        (element id, field, value) of widgets that are not on it any more.
        """
        states = self.app._tree.get_widget_states()
        sent = ((self._group(project).proto.id, 'bool_value', open_),) + also
        for element_id, field, value in sent:
            state = states.widgets.add()
            state.id = element_id
            setattr(state, field, value)
        self.app._run(states)
        self._no_exception()

    def _rows(self, project, row_key=None):
        """The task rows drawn for a project, by their start buttons, wherever they are."""
        prefix = (row_key or self.ROW_KEY) + project + '_'
        return [b for b in self.app.button if b.key and b.key.startswith(prefix)]

    def _button(self, key):
        found = [b for b in self.app.button if b.key and b.key.startswith(key)]
        self.assertTrue(found, key)
        return found[0]

    @staticmethod
    def _task_more():
        """What the MCP server does: its own tracker, its own save."""
        TimeTracker('data.json', op_outbox=NoOutbox()).add_task(
            'Work', 'Writing', due_date=date.today().isoformat(), today=True)

    def _away_and_back(self):
        self.app.session_state['menu'] = 'list_main_projects'
        self._run()
        self.app.session_state['menu'] = self.MENU
        self._run()

    def test_an_open_group_has_its_rows(self):
        self.assertEqual(len(self._rows('Work')), 2)
        self.assertEqual(len(self._rows('Home')), 1)

    def test_a_closed_group_has_none(self):
        self._click('Work', False)
        self.assertEqual(self._rows('Work'), [])
        self.assertEqual(self._group('Work').children, {})
        self.assertFalse(any('Typing' in m.value for m in self.app.markdown))

    def test_and_says_how_many_it_holds(self):
        self._click('Work', False)
        self.assertEqual(self._group('Work').label, 'Work (2)')

    def test_while_the_others_keep_theirs(self):
        self._click('Work', False)
        self.assertEqual(len(self._rows('Home')), 1)

    def test_opening_it_again_draws_them(self):
        self._click('Work', False)
        self._click('Work', True)
        self.assertEqual(len(self._rows('Work')), 2)
        self.assertTrue(any('Typing' in m.value for m in self.app.markdown))

    def test_and_it_stays_open_after_that(self):
        """Not just its rows for one run: the group itself has to be open."""
        self._click('Work', False)
        self._click('Work', True)
        self.assertTrue(self._group('Work').proto.expanded)
        self._run()
        self.assertTrue(self._group('Work').proto.expanded)
        self.assertEqual(len(self._rows('Work')), 2)

    def test_and_it_stays_the_same_group_all_along(self):
        """
        Its state used to be handed to it as expanded=, which is part of an
        element's identity: the run after a click on its header drew it as a
        new element, and the next click on that header, with no other run in
        between, went to the old one and was lost.
        """
        before = self._group('Work').proto.id
        self._click('Work', False)
        self._click('Work', True)
        self._click('Work', False)
        self.assertEqual(self._group('Work').proto.id, before)

    def test_and_a_run_of_anything_else_leaves_it_closed(self):
        self._click('Work', False)
        self._run()
        self._run()
        self.assertEqual(self._rows('Work'), [])
        self.assertFalse(self._group('Work').proto.expanded)

    def test_it_is_still_closed_after_going_away_and_back(self):
        self._click('Work', False)
        self._away_and_back()
        self.assertFalse(self._group('Work').proto.expanded)
        self.assertEqual(self._rows('Work'), [])
        self.assertEqual(len(self._rows('Home')), 1)

    def test_and_can_be_opened_from_there(self):
        self._click('Work', False)
        self._away_and_back()
        self._click('Work', True)
        self.assertEqual(len(self._rows('Work')), 2)
        self._away_and_back()
        self.assertTrue(self._group('Work').proto.expanded)
        self.assertEqual(len(self._rows('Work')), 2)

    def test_and_after_its_count_changed_while_away(self):
        """
        Away, the group's key is gone, and only the mirror knows it was
        closed - under the project's name, not the label, which now says 3.
        """
        self._click('Work', False)
        self.app.session_state['menu'] = 'list_main_projects'
        self._run()
        self._task_more()
        self.app.session_state['menu'] = self.MENU
        self._run()
        self.assertEqual(self._group('Work').label, 'Work (3)')
        self.assertFalse(self._group('Work').proto.expanded)
        self.assertEqual(self._rows('Work'), [])

    def test_the_other_tab_keeps_its_own(self):
        self._click('Work', False)
        self.app.session_state['menu'] = self.OTHER_MENU
        self._run()
        self.assertTrue(self._group('Work').proto.expanded)
        self.assertEqual(len(self._rows('Work', self.OTHER_ROW_KEY)), 2)
        self.app.session_state['menu'] = self.MENU
        self._run()
        self.assertFalse(self._group('Work').proto.expanded)

    def test_a_task_more_leaves_it_closed(self):
        """A new count is a new label and so a new element, told its state all the same."""
        self._click('Work', False)
        self._task_more()
        self._run()
        self.assertEqual(self._group('Work').label, 'Work (3)')
        self.assertFalse(self._group('Work').proto.expanded)
        self.assertEqual(self._rows('Work'), [])
        self._click('Work', True)
        self.assertEqual(len(self._rows('Work')), 3)

    def test_and_an_open_one_open(self):
        self._task_more()
        self._run()
        self.assertTrue(self._group('Work').proto.expanded)
        self.assertEqual(len(self._rows('Work')), 3)

    def test_a_button_in_an_open_group_still_works(self):
        """A row's own rerun leaves the other groups as they were."""
        self._click('Home', False)
        self._rows('Work')[0].click().run()
        self._no_exception()
        self.assertEqual(self.app.session_state.tracker.get_current_work()['task_name'], 'Typing')
        self.assertFalse(self._group('Home').proto.expanded)
        self.assertEqual(self._rows('Home'), [])

    def test_a_click_that_opens_a_group_outlasts_a_rerun_from_a_row_above(self):
        """
        A row's button in Work and the click that opens Home below it, in one
        run. The row ends the run with st.rerun() before Home is reached;
        Streamlit 1.55 then dropped the opening click as stale, and Home
        stayed open in the browser with nothing in it.
        """
        labels = [e.label for e in self.app.expander]
        self.assertLess(labels.index('Work (2)'), labels.index('Home (1)'))
        self._click('Home', False)
        self._rows('Work')[0].click()
        self._click('Home', True)
        self.assertEqual(self.app.session_state.tracker.get_current_work()['task_name'], 'Typing')
        self.assertTrue(self._group('Home').proto.expanded)
        self.assertEqual(len(self._rows('Home')), 1)
        self._run()
        self.assertTrue(self._group('Home').proto.expanded)
        self.assertEqual(len(self._rows('Home')), 1)

    def test_a_run_that_opens_a_group_and_ends_inside_it_still_keeps_it_open(self):
        """
        The `finally` around the rows. Here the run that opens Work ends in
        one of Work's edit buttons, pressed as the rows came in, and the
        mirror has to learn that Work is open all the same: the edit form
        drops the group's key, and the mirror is all that is left.
        """
        edit = self._button(self.EDIT_KEY + 'Work_').proto.id
        self._click('Work', False)
        self._click('Work', True, (edit, 'trigger_value', True))
        self.assertEqual(self.app.session_state['menu'], 'edit_task_form')
        self._run()
        self.app.session_state['menu'] = self.MENU
        self._run()
        self.assertTrue(self._group('Work').proto.expanded)
        self.assertEqual(len(self._rows('Work')), 2)


@unittest.skipIf(AppTest is None, 'streamlit is not installed')
class TestTheTodayTab(_Groups, unittest.TestCase):

    MENU = 'today_view'
    ROW_KEY = 'start_today_task_'
    EDIT_KEY = 'edit_today_task_'
    OTHER_MENU = 'task_planning'
    OTHER_ROW_KEY = 'start_task_planning_'

    def _priority_field(self, project, task):
        key = 'today_priority_%d_%s_%s_' % (len(project), project, task)
        found = [n for n in self.app.number_input if n.key and n.key.startswith(key)]
        self.assertEqual(len(found), 1, key)
        return found[0]

    @staticmethod
    def _stored_priority(project, task):
        tasks = TimeTracker('data.json', op_outbox=NoOutbox()).list_tasks(main_project_name=project)
        return [t for t in tasks if t['task_name'] == task][0].get('priority', 0)

    def test_a_priority_typed_as_its_group_closes_is_kept(self):
        """
        A priority field hands in its value as it loses focus, which the click
        on the group's header makes it do. When both reach the server in one
        run, the group is closed before its rows - and the field - are built.
        """
        self._priority_field('Work', 'Typing').set_value(7)
        self._click('Work', False)
        self.assertEqual(self._stored_priority('Work', 'Typing'), 7)
        self._click('Work', True)
        self.assertEqual(self._priority_field('Work', 'Typing').value, 7)

    def test_and_one_typed_while_the_tasks_are_sorted(self):
        """
        A field's key holds its row's place in the group, so the closed group
        has to be looked through in the order it was drawn in: alphabetical
        here, Reading before Typing, the other way round from how they were
        added.
        """
        self.app.selectbox(key='today_task_order').set_value('alphabetical')
        self._run()
        fields = [n.key for n in self.app.number_input if n.key and '_Work_' in n.key]
        self.assertEqual([k.rsplit('_', 2)[1] for k in fields], ['Reading', 'Typing'])
        self._priority_field('Work', 'Typing').set_value(7)
        self._click('Work', False)
        self.assertEqual(self._stored_priority('Work', 'Typing'), 7)
        self.assertEqual(self._stored_priority('Work', 'Reading'), 0)

    def test_and_closing_one_with_nothing_typed_stores_nothing(self):
        """Every field of the group closed in this run is looked at; only a new value is a reason to write."""
        with mock.patch.object(TimeTracker, 'update_task', autospec=True,
                               side_effect=TimeTracker.update_task) as update:
            self._click('Work', False)
            self._run()
        self.assertEqual(update.call_count, 0)

    def test_not_even_with_its_rows_sorted_anew_in_that_run(self):
        """Sorted anew, the rows' places - and so their keys - are ones no field has had yet."""
        with mock.patch.object(TimeTracker, 'update_task', autospec=True,
                               side_effect=TimeTracker.update_task) as update:
            self.app.selectbox(key='today_task_order').set_value('alphabetical')
            self._click('Work', False)
            self._run()
        self.assertEqual(update.call_count, 0)

    def test_and_one_typed_into_an_open_group_is_stored_once(self):
        """By its own field: the rescue for closed groups leaves the open ones alone."""
        with mock.patch.object(TimeTracker, 'update_task', autospec=True,
                               side_effect=TimeTracker.update_task) as update:
            self._priority_field('Work', 'Typing').set_value(7)
            self._run()
        self.assertEqual(update.call_count, 1)
        self.assertEqual(self._stored_priority('Work', 'Typing'), 7)

    def test_but_one_changed_elsewhere_while_it_is_closed_is_left_alone(self):
        """By the MCP server, say: no field of the closed group puts back the old one."""
        self._click('Work', False)
        TimeTracker('data.json', op_outbox=NoOutbox()).update_task('Work', 'Typing', priority=5)
        self._run()
        self._run()
        self.assertEqual(self._stored_priority('Work', 'Typing'), 5)

    def test_nor_is_another_projects_priority_taken_for_its_own(self):
        """
        Project 'a' with task 'b_c' next to project 'a_b' with task 'c'. With
        only the names in a field's key, both rows were a_b_c_0, and the
        closed group 'a' was looked through on every run, read the open
        one's field as its own row's and stored its 5 on b_c.
        """
        tracker = TimeTracker('data.json', op_outbox=NoOutbox())
        tracker.add_main_project('a')
        tracker.add_task('a', 'b_c', due_date=date.today().isoformat(), today=True)
        self._run()
        self._click('a', False)
        tracker = TimeTracker('data.json', op_outbox=NoOutbox())
        tracker.add_main_project('a_b')
        tracker.add_task('a_b', 'c', due_date=date.today().isoformat(), today=True)
        tracker.update_task('a_b', 'c', priority=5)
        self._run()
        self._run()
        self.assertEqual(self._stored_priority('a', 'b_c'), 0)
        self.assertEqual(self._stored_priority('a_b', 'c'), 5)
        self.assertEqual(self._priority_field('a_b', 'c').value, 5)

    def test_and_no_field_takes_over_the_value_of_another_projects(self):
        """
        The same two rows one after the other, at the same place: b_c leaves
        Today and c, priority 5, comes in. With only the names in the key,
        c's field was b_c's to Streamlit, kept its 0 and stored that on c.
        """
        tracker = TimeTracker('data.json', op_outbox=NoOutbox())
        tracker.add_main_project('a')
        tracker.add_task('a', 'b_c', due_date=date.today().isoformat(), today=True)
        self._run()
        self.assertEqual(self._priority_field('a', 'b_c').value, 0)
        tracker = TimeTracker('data.json', op_outbox=NoOutbox())
        tracker.delete_task('a', 'b_c')
        tracker.add_main_project('a_b')
        tracker.add_task('a_b', 'c', due_date=date.today().isoformat(), today=True)
        tracker.update_task('a_b', 'c', priority=5)
        self._run()
        self._run()
        self.assertEqual(self._stored_priority('a_b', 'c'), 5)
        self.assertEqual(self._priority_field('a_b', 'c').value, 5)


@unittest.skipIf(AppTest is None, 'streamlit is not installed')
class TestThePlanningTab(_Groups, unittest.TestCase):

    MENU = 'task_planning'
    ROW_KEY = 'start_task_planning_'
    EDIT_KEY = 'edit_task_planning_'
    OTHER_MENU = 'today_view'
    OTHER_ROW_KEY = 'start_today_task_'


if __name__ == '__main__':
    unittest.main()
