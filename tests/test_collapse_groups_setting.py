"""
The setting that collapses the project groups of a long list by default, in
Today and in Planning (issue #645).

A closed group has nothing built inside it (issue #644), which pays off with
many tasks - but only for groups that are closed, and every group used to
start open. Switched on, the groups of a list with more tasks than the
setting allows start closed, all but the one with the running task. A group
the user opened or closed keeps that state whatever the default says. Off,
which is what a config.json without the setting means, nothing changes:
tests/test_collapsed_groups.py runs without it.

Clicks on a group's header are sent the way the browser sends them, as in
tests/test_collapsed_groups.py: the expander's new state under its element
id, with the next run.
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

# Four tasks for today in three projects, due today, so that both tabs list
# them as they open: Planning's first filter is the due date, Today's the flag.
TASKS = (('Work', ('Typing', 'Reading')), ('Home', ('Cooking',)), ('Garden', ('Mowing',)))
ROWS = 4


class NoOutbox:
    """The sync queue, switched off whatever config.json says."""

    def append(self, op, **fields):
        pass


def _tracker():
    return TimeTracker('data.json', op_outbox=NoOutbox())


class _Setting:
    """One of the two tabs, the setting as a test gives it, and clicks on the groups."""

    MENU = None
    ROW_KEY = None      # what every task row's start button key begins with
    EDIT_KEY = None     # ... and its edit button key
    OTHER_MENU = None

    def setUp(self):
        self.scratch = tempfile.mkdtemp(prefix='tc-collapse-')
        self.addCleanup(shutil.rmtree, self.scratch, ignore_errors=True)
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(self.scratch)
        environment = mock.patch.dict(
            os.environ, {'TC_CONFIG_DIR': os.path.join(self.scratch, 'config')})
        environment.start()
        self.addCleanup(environment.stop)
        os.makedirs(os.path.join(self.scratch, 'config'))
        self._configure()
        tracker = _tracker()
        for project, tasks in TASKS:
            tracker.add_main_project(project)
            for task in tasks:
                tracker.add_task(project, task, due_date=date.today().isoformat(), today=True)
        self.app = None

    def _configure(self, setting=None):
        config = {'language': 'en', 'data_file': 'data.json',
                  'mcp_server_enabled': False, 'update': {'github_repo': ''}}
        if setting is not None:
            config['collapse_groups'] = setting
        with open('config.json', 'w', encoding='utf-8') as handle:
            json.dump(config, handle)

    def _open(self, setting=None, **session):
        if setting is not None:
            self._configure(setting)
        self.app = AppTest.from_file(VIEW, default_timeout=20)
        self.app.session_state['menu'] = self.MENU
        for name, value in session.items():
            self.app.session_state[name] = value
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

    def _is_open(self, project):
        """Open in the page, and with its rows built - or neither."""
        expanded = self._group(project).proto.expanded
        rows = [b for b in self.app.button
                if b.key and b.key.startswith(self.ROW_KEY + project + '_')]
        self.assertEqual(bool(rows), expanded,
                         '%s is %s but has %d rows' % (
                             project, 'open' if expanded else 'closed', len(rows)))
        return expanded

    def _open_ones(self):
        return sorted(name for name, _ in TASKS if self._is_open(name))

    def _no_groups(self):
        """None of the projects' groups in the page, open or closed."""
        projects = tuple(name + ' (' for name, _ in TASKS)
        self.assertEqual([e.label for e in self.app.expander if e.label.startswith(projects)], [])

    def _click(self, project, open_):
        """The header of a project's group clicked: its new state, and the run that follows."""
        states = self.app._tree.get_widget_states()
        state = states.widgets.add()
        state.id = self._group(project).proto.id
        state.bool_value = open_
        self.app._run(states)
        self._no_exception()

    def _away_and_back(self):
        self.app.session_state['menu'] = 'list_main_projects'
        self._run()
        self.app.session_state['menu'] = self.MENU
        self._run()

    def _row(self, project, key=None):
        """The first row of a project's group, by its start (or other) button."""
        found = [b for b in self.app.button
                 if b.key and b.key.startswith((key or self.ROW_KEY) + project + '_')]
        self.assertTrue(found, project)
        return found[0]

    def _press(self, key):
        """One of the app's own buttons above the groups, and the run it starts."""
        self.app.button(key=key).click().run()
        self._no_exception()

    @staticmethod
    def _task_more(project='Home', task='Washing'):
        """What the MCP server does: its own tracker, its own save."""
        _tracker().add_task(project, task, due_date=date.today().isoformat(), today=True)

    # --- off ---------------------------------------------------------------

    def test_without_the_setting_every_group_starts_open(self):
        self._open()
        self.assertEqual(self._open_ones(), ['Garden', 'Home', 'Work'])

    def test_and_switched_off_whatever_the_number(self):
        self._open({'enabled': False, 'above': 0})
        self.assertEqual(self._open_ones(), ['Garden', 'Home', 'Work'])

    # --- on ----------------------------------------------------------------

    def test_switched_on_the_groups_of_a_longer_list_start_closed(self):
        self._open({'enabled': True, 'above': ROWS - 1})
        self.assertEqual(self._open_ones(), [])

    def test_and_say_how_many_tasks_they_hold(self):
        self._open({'enabled': True, 'above': ROWS - 1})
        self.assertEqual(self._group('Work').label, 'Work (2)')

    def test_a_list_of_just_that_many_is_not_longer(self):
        self._open({'enabled': True, 'above': ROWS})
        self.assertEqual(self._open_ones(), ['Garden', 'Home', 'Work'])

    def test_none_at_all_closes_every_group(self):
        self._open({'enabled': True, 'above': 0})
        self.assertEqual(self._open_ones(), [])

    def test_without_the_switch_it_is_off(self):
        self._open({'above': 0})
        self.assertEqual(self._open_ones(), ['Garden', 'Home', 'Work'])

    def test_no_number_means_the_default(self):
        """Twenty, which four tasks are far from."""
        self._open({'enabled': True})
        self.assertEqual(self._open_ones(), ['Garden', 'Home', 'Work'])

    def test_the_group_with_the_running_task_stays_open(self):
        _tracker().start_work('Work', 'Reading')
        self._open({'enabled': True, 'above': ROWS - 1})
        self.assertEqual(self._open_ones(), ['Work'])

    def test_but_not_one_that_only_shares_its_project(self):
        """
        The running task is not in this list, so no group holds its row:
        Work holds other tasks of the same project, and starts closed.
        """
        tracker = _tracker()
        tracker.add_task('Work', 'Filing')
        tracker.start_work('Work', 'Filing')
        self._open({'enabled': True, 'above': ROWS - 1})
        self.assertEqual(self._open_ones(), [])

    # --- while the tab is up ------------------------------------------------

    def test_a_list_that_grows_past_the_number_stays_as_it_is(self):
        """
        Nothing closes under the user's hand: a closed group's rows are not
        built, and a click on one of them would go with it.
        """
        self._open({'enabled': True, 'above': ROWS})
        self._task_more()
        self._run()
        self.assertEqual(self._open_ones(), ['Garden', 'Home', 'Work'])

    def test_and_closes_its_groups_at_the_next_visit(self):
        self._open({'enabled': True, 'above': ROWS})
        self._task_more()
        self._run()
        self._away_and_back()
        self.assertEqual(self._open_ones(), [])

    def test_a_row_clicked_as_the_list_grows_past_it_still_works(self):
        """
        The list grew elsewhere, and the run that the click on Garden's start
        button brings is the first to see it. The row is still there to take
        the click, and after the next visit only Garden, with the running
        task, is open.
        """
        self._open({'enabled': True, 'above': ROWS})
        self._task_more()
        start = [b for b in self.app.button
                 if b.key and b.key.startswith(self.ROW_KEY + 'Garden_')][0]
        start.click().run()
        self._no_exception()
        self.assertEqual(self.app.session_state.tracker.get_current_work()['task_name'], 'Mowing')
        self.assertEqual(self._open_ones(), ['Garden', 'Home', 'Work'])
        self._away_and_back()
        self.assertEqual(self._open_ones(), ['Garden'])

    def test_a_list_that_shrinks_below_the_number_opens_them_at_the_next_visit(self):
        self._task_more()
        self._open({'enabled': True, 'above': ROWS})
        self.assertEqual(self._open_ones(), [])
        _tracker().delete_task('Home', 'Washing')
        self._run()
        self.assertEqual(self._open_ones(), [])
        self._away_and_back()
        self.assertEqual(self._open_ones(), ['Garden', 'Home', 'Work'])

    def test_the_running_task_stopped_leaves_its_group_open_until_then(self):
        _tracker().start_work('Work', 'Reading')
        self._open({'enabled': True, 'above': ROWS - 1})
        _tracker().stop_work()
        self._run()
        self.assertEqual(self._open_ones(), ['Work'])
        self._away_and_back()
        self.assertEqual(self._open_ones(), [])

    # The app's own buttons above the groups end their run with st.rerun()
    # before the groups are reached, and Streamlit 1.55 drops every
    # expander's key at the end of such a run.

    def test_stopping_the_work_from_the_toolbar_leaves_its_group_open_until_then(self):
        _tracker().start_work('Work', 'Reading')
        self._open({'enabled': True, 'above': ROWS - 1})
        self._press('toolbar_stop_btn')
        self.assertIsNone(self.app.session_state.tracker.get_current_work())
        self.assertEqual(self._open_ones(), ['Work'])
        self._run()
        self.assertEqual(self._open_ones(), ['Work'])
        self._away_and_back()
        self.assertEqual(self._open_ones(), [])

    def test_and_a_row_clicked_right_after_it_still_works(self):
        _tracker().start_work('Work', 'Reading')
        self._open({'enabled': True, 'above': ROWS - 1})
        self._press('toolbar_stop_btn')
        self._row('Work').click().run()
        self._no_exception()
        self.assertEqual(self.app.session_state.tracker.get_current_work()['main_project_name'], 'Work')

    def test_a_list_grown_meanwhile_stays_as_it_is_after_a_stop(self):
        _tracker().start_work('Work', 'Reading')
        self._open({'enabled': True, 'above': ROWS})
        self._task_more()
        self._press('toolbar_stop_btn')
        self.assertEqual(self._open_ones(), ['Garden', 'Home', 'Work'])

    def test_a_round_trip_to_edit_a_task_is_a_new_visit(self):
        """
        Out through a row's edit button and back with Cancel, both of which
        end their run with st.rerun(): the groups take the defaults of the
        list as it is now.
        """
        self._open({'enabled': True, 'above': ROWS})
        self._task_more()
        self._row('Garden', self.EDIT_KEY).click().run()
        self._no_exception()
        self.assertEqual(self.app.session_state['menu'], 'edit_task_form')
        [b for b in self.app.button if b.label == 'Cancel'][0].click().run()
        self._no_exception()
        self.assertEqual(self.app.session_state['menu'], self.MENU)
        self.assertEqual(self._open_ones(), [])

    def test_a_group_that_appears_while_the_tab_is_up_takes_the_default(self):
        self._open({'enabled': True, 'above': ROWS})
        tracker = _tracker()
        tracker.add_main_project('Shed')
        tracker.add_task('Shed', 'Sweeping', due_date=date.today().isoformat(), today=True)
        self._run()
        self.assertFalse(self._is_open('Shed'))
        self.assertEqual(self._open_ones(), ['Garden', 'Home', 'Work'])

    # --- what the user set -------------------------------------------------

    def test_a_group_the_user_opens_stays_open(self):
        self._open({'enabled': True, 'above': ROWS - 1})
        self._click('Home', True)
        self.assertEqual(self._open_ones(), ['Home'])
        self._run()
        self.assertEqual(self._open_ones(), ['Home'])

    def test_and_after_going_away_and_back(self):
        self._open({'enabled': True, 'above': ROWS - 1})
        self._click('Home', True)
        self._away_and_back()
        self.assertEqual(self._open_ones(), ['Home'])

    def test_and_closed_again_stays_closed(self):
        self._open({'enabled': True, 'above': ROWS - 1})
        self._click('Home', True)
        self._click('Home', False)
        self._away_and_back()
        self.assertEqual(self._open_ones(), [])

    def test_a_group_the_user_closes_stays_closed_in_a_short_list(self):
        self._open({'enabled': True, 'above': ROWS})
        self._click('Home', False)
        self._away_and_back()
        self.assertEqual(self._open_ones(), ['Garden', 'Work'])

    def test_and_one_the_user_opened_stays_open_as_the_list_grows(self):
        """Opened by hand while it was open anyway: closed and opened again."""
        self._open({'enabled': True, 'above': ROWS})
        self._click('Home', False)
        self._click('Home', True)
        self._task_more('Garden', 'Raking')
        self._away_and_back()
        self.assertEqual(self._open_ones(), ['Home'])

    def test_even_the_group_with_the_running_task_stays_as_the_user_set_it(self):
        _tracker().start_work('Work', 'Reading')
        self._open({'enabled': True, 'above': ROWS - 1})
        self._click('Work', False)
        self._away_and_back()
        self.assertEqual(self._open_ones(), [])

    def test_the_other_tab_keeps_its_own(self):
        """A group opened here is opened here: the other tab still has its default."""
        self._open({'enabled': True, 'above': ROWS - 1})
        self._click('Home', True)
        self.app.session_state['menu'] = self.OTHER_MENU
        self._run()
        self.assertFalse(self._group('Home').proto.expanded)
        self.app.session_state['menu'] = self.MENU
        self._run()
        self.assertEqual(self._open_ones(), ['Home'])

    def test_and_a_group_set_here_follows_its_default_there(self):
        """
        Set by the user in this tab only. In the other one, which drew it open
        before, it takes the default of a list that has grown since.
        """
        self._open({'enabled': True, 'above': ROWS})
        self.app.session_state['menu'] = self.OTHER_MENU
        self._run()
        self.assertTrue(self._group('Home').proto.expanded)
        self.app.session_state['menu'] = self.MENU
        self._run()
        self._click('Home', False)
        self._task_more('Garden', 'Raking')
        self.app.session_state['menu'] = self.OTHER_MENU
        self._run()
        self.assertFalse(self._group('Home').proto.expanded)

    # --- the setting, changed while the app runs ----------------------------

    def test_switched_on_while_the_app_runs_it_shows_at_once(self):
        self._open()
        self.assertEqual(self._open_ones(), ['Garden', 'Home', 'Work'])
        self._configure({'enabled': True, 'above': ROWS - 1})
        self._away_and_back()
        self.assertEqual(self._open_ones(), [])

    def test_but_not_for_a_group_the_user_set_before(self):
        self._open()
        self._click('Home', False)
        self._click('Home', True)
        self._configure({'enabled': True, 'above': ROWS - 1})
        self._away_and_back()
        self.assertEqual(self._open_ones(), ['Home'])

    def test_switched_off_again_they_open(self):
        self._open({'enabled': True, 'above': ROWS - 1})
        self._configure({'enabled': False, 'above': ROWS - 1})
        self._away_and_back()
        self.assertEqual(self._open_ones(), ['Garden', 'Home', 'Work'])


@unittest.skipIf(AppTest is None, 'streamlit is not installed')
class TestTheTodayTab(_Setting, unittest.TestCase):

    MENU = 'today_view'
    ROW_KEY = 'start_today_task_'
    EDIT_KEY = 'edit_today_task_'
    OTHER_MENU = 'task_planning'

    def test_done_from_the_current_work_leaves_the_groups_as_they_are(self):
        _tracker().start_work('Work', 'Reading')
        self._open({'enabled': True, 'above': ROWS})
        self._task_more()
        self._press('main_done_button')
        self.assertEqual(self._open_ones(), ['Garden', 'Home', 'Work'])

    def test_showing_only_open_tasks_is_another_list(self):
        _tracker().update_task('Garden', 'Mowing', status='done')
        self._open({'enabled': True, 'above': ROWS - 1})
        self.assertEqual(self._open_ones(), [])
        self.app.checkbox(key='today_show_only_open').check().run()
        self._no_exception()
        self.assertTrue(self._is_open('Work'))
        self.assertTrue(self._is_open('Home'))
        self.app.checkbox(key='today_show_only_open').uncheck().run()
        self._no_exception()
        self.assertEqual(self._open_ones(), [])

    def test_even_when_it_has_nothing_in_it(self):
        """
        Every task done, so only the open ones is an empty list, with no
        groups; meanwhile the whole list grew past the number.
        """
        tracker = _tracker()
        for project, tasks in TASKS:
            for task in tasks:
                tracker.update_task(project, task, status='done')
        self._open({'enabled': True, 'above': ROWS})
        self.assertEqual(self._open_ones(), ['Garden', 'Home', 'Work'])
        self.app.checkbox(key='today_show_only_open').check().run()
        self._no_exception()
        self._no_groups()
        self._task_more()
        self.app.checkbox(key='today_show_only_open').uncheck().run()
        self._no_exception()
        self.assertEqual(self._open_ones(), [])

    def test_the_tasks_counted_are_the_ones_shown(self):
        """With only the open ones shown, a done task is not one of the rows."""
        _tracker().update_task('Garden', 'Mowing', status='done')
        self._open({'enabled': True, 'above': ROWS - 1}, today_show_only_open_value=True)
        self.assertEqual([e for e in self.app.expander if e.label.startswith('Garden (')], [])
        self.assertTrue(self._is_open('Work'))
        self.assertTrue(self._is_open('Home'))

    def test_and_done_ones_shown_count_too(self):
        _tracker().update_task('Garden', 'Mowing', status='done')
        self._open({'enabled': True, 'above': ROWS - 1})
        self.assertEqual(self._open_ones(), [])


@unittest.skipIf(AppTest is None, 'streamlit is not installed')
class TestThePlanningTab(_Setting, unittest.TestCase):

    MENU = 'task_planning'
    ROW_KEY = 'start_task_planning_'
    EDIT_KEY = 'edit_task_planning_'
    OTHER_MENU = 'today_view'

    def _filter(self, label):
        self.app.selectbox(key='task_planning_filter_select').set_value(label).run()
        self._no_exception()

    def test_each_filter_is_a_list_of_its_own(self):
        """
        'Today' lists the four tasks due today, 'All' two more of a project
        due later as well: its groups start closed, and going back to the
        short list opens them again.
        """
        tracker = _tracker()
        tracker.add_main_project('Shed')
        later = (date.today() + timedelta(days=10)).isoformat()
        for task in ('Sweeping', 'Painting'):
            tracker.add_task('Shed', task, due_date=later)
        self._open({'enabled': True, 'above': ROWS})
        self.assertEqual(self._open_ones(), ['Garden', 'Home', 'Work'])
        self._filter('All')
        self.assertEqual(self._open_ones(), [])
        self.assertFalse(self._is_open('Shed'))
        self._filter('Today')
        self.assertEqual(self._open_ones(), ['Garden', 'Home', 'Work'])

    def test_so_is_the_weekly_overview_which_has_no_groups(self):
        """
        Work started from the overview, whose rows are not in groups: back
        on 'Today', the group with the running task is open.
        """
        self._open({'enabled': True, 'above': ROWS - 1})
        self.assertEqual(self._open_ones(), [])
        self._filter('Weekly overview')
        self._no_groups()
        self._row('Garden', 'start_task_planning_weekly_').click().run()
        self._no_exception()
        self.assertEqual(self.app.session_state.tracker.get_current_work()['task_name'], 'Mowing')
        self._filter('Today')
        self.assertEqual(self._open_ones(), ['Garden'])

    def test_and_a_filter_with_nothing_in_it(self):
        """'Tomorrow' lists nothing here; meanwhile 'Today' grew past the number."""
        self._open({'enabled': True, 'above': ROWS})
        self.assertEqual(self._open_ones(), ['Garden', 'Home', 'Work'])
        self._filter('Tomorrow')
        self._no_groups()
        self._task_more()
        self._filter('Today')
        self.assertEqual(self._open_ones(), [])


@unittest.skipIf(AppTest is None, 'streamlit is not installed')
class TestTheSettingsForm(unittest.TestCase):

    def setUp(self):
        self.scratch = tempfile.mkdtemp(prefix='tc-collapse-form-')
        self.addCleanup(shutil.rmtree, self.scratch, ignore_errors=True)
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(self.scratch)
        environment = mock.patch.dict(
            os.environ, {'TC_CONFIG_DIR': os.path.join(self.scratch, 'config')})
        environment.start()
        self.addCleanup(environment.stop)
        os.makedirs(os.path.join(self.scratch, 'config'))
        tracker = _tracker()
        for project, tasks in TASKS:
            tracker.add_main_project(project)
            for task in tasks:
                tracker.add_task(project, task, due_date=date.today().isoformat(), today=True)

    def _open(self, **config):
        settings = {'language': 'en', 'data_file': 'data.json',
                    'mcp_server_enabled': False, 'update': {'github_repo': ''}}
        settings.update(config)
        with open('config.json', 'w', encoding='utf-8') as handle:
            json.dump(settings, handle)
        app = AppTest.from_file(VIEW, default_timeout=20)
        app.session_state['menu'] = 'settings'
        app.run()
        self.assertFalse(app.exception, [e.value for e in app.exception])
        return app

    @staticmethod
    def _switch(app):
        found = [c for c in app.checkbox if c.label == 'Collapse project groups by default']
        assert len(found) == 1, [c.label for c in app.checkbox]
        return found[0]

    @staticmethod
    def _number(app):
        return app.number_input(key='settings_collapse_groups_above')

    @staticmethod
    def _save(app):
        found = [b for b in app.button if b.proto.form_id == 'project_groups_form']
        assert len(found) == 1, [b.proto.form_id for b in app.button]
        return found[0]

    @staticmethod
    def _stored():
        with open('config.json', encoding='utf-8') as handle:
            return json.load(handle).get('collapse_groups')

    def test_it_is_off_by_default(self):
        app = self._open()
        self.assertFalse(self._switch(app).value)
        self.assertEqual(self._number(app).value, 20)
        self.assertIsNone(self._stored())

    def test_it_shows_what_config_json_says(self):
        app = self._open(collapse_groups={'enabled': True, 'above': 7})
        self.assertTrue(self._switch(app).value)
        self.assertEqual(self._number(app).value, 7)

    def test_saving_stores_both(self):
        app = self._open()
        self._switch(app).check()
        self._number(app).set_value(3)
        self._save(app).click()
        app.run()
        self.assertFalse(app.exception, [e.value for e in app.exception])
        self.assertEqual(self._stored(), {'enabled': True, 'above': 3})
        self.assertTrue(any('Project group settings saved.' in s.value for s in app.success))

    def test_and_the_lists_follow_it_without_a_restart(self):
        app = self._open()
        self._switch(app).check()
        self._number(app).set_value(3)
        self._save(app).click()
        app.run()
        app.session_state['menu'] = 'today_view'
        app.run()
        self.assertFalse(app.exception, [e.value for e in app.exception])
        self.assertFalse(any(e.proto.expanded for e in app.expander), [e.label for e in app.expander])

    def test_the_ends_of_the_range_are_numbers_like_any_other(self):
        for above in (0, 100000):
            with self.subTest(above=above):
                app = self._open(collapse_groups={'enabled': True, 'above': above})
                self.assertTrue(self._switch(app).value)
                self.assertEqual(self._number(app).value, above)

    def test_a_switch_left_out_is_off_and_a_number_left_out_twenty(self):
        app = self._open(collapse_groups={'above': 3})
        self.assertFalse(self._switch(app).value)
        self.assertEqual(self._number(app).value, 3)
        app = self._open(collapse_groups={'enabled': True})
        self.assertTrue(self._switch(app).value)
        self.assertEqual(self._number(app).value, 20)

    def test_what_is_not_a_switch_or_a_number_is_off_and_the_default(self):
        for setting in ('yes', ['enabled'], {'enabled': 'true', 'above': 3},
                        {'enabled': 1, 'above': 3}):
            with self.subTest(setting=setting):
                app = self._open(collapse_groups=setting)
                self.assertFalse(self._switch(app).value)
        for above in ('7', -1, 2.5, True, None, 100001, 10 ** 20):
            with self.subTest(above=above):
                app = self._open(collapse_groups={'enabled': True, 'above': above})
                self.assertTrue(self._switch(app).value)
                self.assertEqual(self._number(app).value, 20)


class TestItsWordsAreTranslated(unittest.TestCase):
    """
    The settings section's words, in every language the application ships
    besides English, which uses them as they are. Checked against the
    compiled catalogs, which are what the application reads.
    """

    WORDS = (
        "Project Groups",
        "Collapse project groups by default",
        "When the number of tasks in a list exceeds",
        "Applies to Today's Tasks and Task Planning. The group with the "
        "running task stays open, and a group you open or close yourself "
        "stays that way until the page is reloaded or the app is restarted.",
        "Project group settings saved.",
    )

    def test_they_are_the_words_the_settings_use(self):
        """
        Or a changed word would leave this checking one nobody sees. Read
        from the parsed source, where string literals written one after the
        other are already one.
        """
        import ast
        with open(VIEW, encoding='utf-8') as handle:
            tree = ast.parse(handle.read(), VIEW)
        marked = {node.args[0].value for node in ast.walk(tree)
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                  and node.func.id == '_' and node.args
                  and isinstance(node.args[0], ast.Constant)}
        for word in self.WORDS:
            with self.subTest(word=word):
                self.assertIn(word, marked)

    def test_every_language_has_them(self):
        import gettext
        for language in ('de', 'fr', 'es', 'cs'):
            catalog = gettext.translation('timetracker', localedir=os.path.join(REPO, 'locale'),
                                          languages=[language])
            for word in self.WORDS:
                with self.subTest(language=language, word=word):
                    self.assertNotEqual(catalog.gettext(word), word)


if __name__ == '__main__':
    unittest.main()
