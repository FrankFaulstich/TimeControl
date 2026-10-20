"""
Adding a task and editing one, run rather than read (issue #637).

The two forms had grown apart: different labels for the same fields, the
dates side by side in one and below each other in the other, a button to
clear a date in only one, the flags in rows of different widths, and a check
for an empty name in only one. They now draw the same fields with
render_task_fields(), and these tests hold them to it - and to what each does
with what is typed into it.

They also cover what the edit form got wrong on its own: its two dates were
kept in keys of their own that nothing cleared when the form was left by any
other way than Cancel, so the next task opened showed the last one's dates,
and saving wrote them into it.

Driven with Streamlit's AppTest, against a data file of their own in a
directory of their own. TC_CONFIG_DIR points into that directory too - the
synchronisation state belongs to the installed application - and the update
check, which would ask GitHub, is kept from importing.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import date, timedelta
from unittest import mock

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
VIEW = os.path.join(REPO_ROOT, 'sl', 'SL_Menu.py')
sys.path.append(REPO_ROOT)

try:
    from streamlit.testing.v1 import AppTest
except ImportError:     # pragma: no cover - streamlit is in requirements-ci.txt
    AppTest = None

from tt.TimeTracker import TimeTracker

TODAY = date.today()
LATER = TODAY + timedelta(days=30)
EARLIER = TODAY + timedelta(days=10)


@unittest.skipIf(AppTest is None, 'streamlit is not installed')
class TaskFormTestCase(unittest.TestCase):

    def setUp(self):
        self.scratch = tempfile.mkdtemp(prefix='tc-task-forms-')
        self.addCleanup(shutil.rmtree, self.scratch, ignore_errors=True)
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(self.scratch)
        for patcher in (mock.patch.dict(os.environ, {'TC_CONFIG_DIR': os.path.join(self.scratch, 'sync')}),
                        mock.patch.dict(sys.modules, {'update': None})):
            patcher.start()
            self.addCleanup(patcher.stop)

        with open('config.json', 'w', encoding='utf-8') as handle:
            json.dump({'language': 'en', 'data_file': 'data.json',
                       'mcp_server_enabled': False}, handle)
        # i18n settles on a language when it is first imported, from the
        # config.json of whatever directory that happens in - the developer's
        # own, German one, say. Imported afresh here instead, from the one
        # above; sys.modules is put back as it was afterwards.
        sys.modules.pop('i18n', None)
        tracker = TimeTracker('data.json', op_outbox=None)
        tracker.add_main_project('Work')
        tracker.add_task('Work', 'Alpha', due_date=LATER.isoformat(), start_date=EARLIER.isoformat(),
                         today=True, note='Alpha note', recurring=True, frequency='weekly',
                         userdefined_days=3, priority=7)
        tracker.add_task('Work', 'Beta', due_date='2031-02-02', note='Beta note')
        self.ids = {task['task_name']: task['id'] for task in tracker.list_tasks('Work')}
        self.app = AppTest.from_file(VIEW, default_timeout=20)

    # --- driving the app ---------------------------------------------------

    def _run(self):
        self.app.run()
        self.assertFalse(self.app.exception, [e.value for e in self.app.exception])

    def open_add(self, project='Work'):
        self.app.session_state['context'] = {'selected_main': project, 'return_to': 'task_mgmt'}
        self.app.session_state['menu'] = 'add_task_form'
        self._run()

    def open_edit(self, name):
        self.app.session_state['context'] = {'selected_main': 'Work', 'selected_task': name,
                                             'selected_task_id': self.ids[name],
                                             'return_to': 'task_mgmt'}
        self.app.session_state['menu'] = 'edit_task_form'
        self._run()

    def leave_by_the_toolbar(self):
        self.app.session_state['menu'] = 'today_view'
        self._run()

    def click(self, label=None, key=None):
        if key:
            self.app.button(key=key).click()
        else:
            [button] = [b for b in self.app.button if b.label == label]
            button.click()
        self._run()

    def field(self, kind, name, widget):
        return getattr(self.app, widget)(key='%s_task_%s' % (kind, name))

    def stored(self, name=None):
        with open('data.json', encoding='utf-8') as handle:
            tasks = {t['task_name']: t for p in json.load(handle)['projects'] for t in p['tasks']}
        return tasks if name is None else tasks.get(name)

    def change_elsewhere(self, name, **fields):
        """A change made while the form is open - the MCP server, the other machine, another tab."""
        TimeTracker('data.json', op_outbox=None).update_task('Work', name, task_id=self.ids[name], **fields)

    def patch_stored(self, name, **fields):
        """Writes values into the data file as they are, whatever they are."""
        with open('data.json', encoding='utf-8') as handle:
            data = json.load(handle)
        for task in data['projects'][0]['tasks']:
            if task['task_name'] == name:
                task.update(fields)
        with open('data.json', 'w', encoding='utf-8') as handle:
            json.dump(data, handle)

    def errors(self):
        return [element.value for element in self.app.error]

    def labels(self):
        """The task fields the form shows, with their labels, grouped by kind of widget."""
        shown = []
        for widget in ('text_input', 'date_input', 'checkbox', 'number_input', 'selectbox', 'text_area'):
            for element in getattr(self.app, widget):
                key = element.key or ''
                if '_task_' in key and not key.endswith('__for'):
                    shown.append((key.split('_task_', 1)[1], element.label))
        return shown


class TestTheTwoFormsAreTheSame(TaskFormTestCase):

    def test_the_same_fields_with_the_same_labels(self):
        """What differs is what has to: only an existing task can be done."""
        self.open_add()
        adding = self.labels()
        self.leave_by_the_toolbar()
        self.open_edit('Alpha')
        editing = self.labels()
        self.assertEqual([field for field in editing if field[0] != 'done'], adding)
        self.assertEqual([field for field in editing if field[0] == 'done'], [('done', 'Done')])
        self.assertEqual([label for _field, label in adding],
                         ['Task Name', 'Start Date', 'Due Date', 'Today', 'Recurring',
                          'Priority', 'Days', 'Frequency', 'Notes (Markdown)'])

    def test_both_have_a_clear_button_for_each_date(self):
        for opening, kind in ((self.open_add, 'new'), (lambda: self.open_edit('Alpha'), 'edit')):
            with self.subTest(kind=kind):
                opening()
                for which in ('start_date', 'due_date'):
                    self.app.button(key='%s_clear_%s_btn' % (kind, which))
                self.leave_by_the_toolbar()


class TestAddingATask(TaskFormTestCase):

    def test_every_field_is_saved(self):
        self.open_add()
        self.field('new', 'name', 'text_input').input('Gamma')
        self.field('new', 'start_date', 'date_input').set_value(EARLIER)
        self.field('new', 'due_date', 'date_input').set_value(LATER)
        self.field('new', 'today', 'checkbox').check()
        self.field('new', 'recurring', 'checkbox').check()
        self._run()
        self.field('new', 'frequency', 'selectbox').set_value('userdefined')
        self._run()
        self.field('new', 'userdefined_days', 'number_input').set_value(4)
        self.field('new', 'priority', 'number_input').set_value(5)
        self.field('new', 'note', 'text_area').input('Gamma note')
        self.click('Add Task')
        task = self.stored('Gamma')
        self.assertEqual((task['start_date'], task['due_date'], task['today'], task['recurring'],
                          task['frequency'], task['userdefined_days'], task['priority'], task['note']),
                         (EARLIER.isoformat(), LATER.isoformat(), True, True,
                          'userdefined', 4, 5, 'Gamma note'))

    def test_it_starts_due_today_and_otherwise_empty(self):
        # Read around the run, not at import: a test run may cross midnight.
        before = date.today()
        self.open_add()
        self.assertIn(self.field('new', 'due_date', 'date_input').value, {before, date.today()})
        shown = {name: self.field('new', name, widget).value for name, widget in (
            ('name', 'text_input'), ('start_date', 'date_input'), ('today', 'checkbox'),
            ('recurring', 'checkbox'), ('priority', 'number_input'),
            ('userdefined_days', 'number_input'), ('frequency', 'selectbox'), ('note', 'text_area'))}
        self.assertEqual(shown, {'name': '', 'start_date': None, 'today': False, 'recurring': False,
                                 'priority': 0, 'userdefined_days': 1, 'frequency': 'daily',
                                 'note': ''})

    def test_a_cleared_due_date_is_saved_as_none(self):
        self.open_add()
        self.field('new', 'name', 'text_input').input('Gamma')
        self.click(key='new_clear_due_date_btn')
        self.assertIsNone(self.field('new', 'due_date', 'date_input').value)
        self.click('Add Task')
        self.assertIsNone(self.stored('Gamma')['due_date'])

    def test_a_name_is_required(self):
        for name in ('', '   '):
            with self.subTest(name=repr(name)):
                self.open_add()
                self.field('new', 'name', 'text_input').input(name)
                self.click('Add Task')
                self.assertIn('Please enter a name.', self.errors())
                self.assertEqual(sorted(self.stored()), ['Alpha', 'Beta'])
                self.click('Cancel')

    def test_dates_out_of_order_are_refused(self):
        self.open_add()
        self.field('new', 'name', 'text_input').input('Gamma')
        self.field('new', 'start_date', 'date_input').set_value(LATER)
        self.field('new', 'due_date', 'date_input').set_value(EARLIER)
        self.click('Add Task')
        self.assertIn('The start date cannot be after the due date.', self.errors())
        self.assertIsNone(self.stored('Gamma'))

    def test_a_recurring_task_needs_a_due_date(self):
        self.open_add()
        self.field('new', 'name', 'text_input').input('Gamma')
        self.field('new', 'recurring', 'checkbox').check()
        self.click(key='new_clear_due_date_btn')
        self.click('Add Task')
        self.assertIn('A due date is required for recurring tasks.', self.errors())
        self.assertIsNone(self.stored('Gamma'))

    def test_a_form_left_unsaved_starts_empty_next_time(self):
        self.open_add()
        self.field('new', 'name', 'text_input').input('Half typed')
        self._run()
        self.leave_by_the_toolbar()
        self.open_add()
        self.assertEqual(self.field('new', 'name', 'text_input').value, '')

    def test_and_so_does_one_after_a_task_was_added(self):
        self.open_add()
        self.field('new', 'name', 'text_input').input('Gamma')
        self.field('new', 'note', 'text_area').input('Gamma note')
        self.click('Add Task')
        self.open_add()
        self.assertEqual(self.field('new', 'name', 'text_input').value, '')
        self.assertEqual(self.field('new', 'note', 'text_area').value, '')


class TestEditingATask(TaskFormTestCase):

    def test_it_shows_the_task_as_it_is_stored(self):
        self.open_edit('Alpha')
        shown = {name: self.field('edit', name, widget).value for name, widget in (
            ('name', 'text_input'), ('start_date', 'date_input'), ('due_date', 'date_input'),
            ('today', 'checkbox'), ('done', 'checkbox'), ('recurring', 'checkbox'),
            ('frequency', 'selectbox'), ('userdefined_days', 'number_input'),
            ('priority', 'number_input'), ('note', 'text_area'))}
        self.assertEqual(shown, {'name': 'Alpha', 'start_date': EARLIER, 'due_date': LATER,
                                 'today': True, 'done': False, 'recurring': True,
                                 'frequency': 'weekly', 'userdefined_days': 3, 'priority': 7,
                                 'note': 'Alpha note'})

    def test_changes_are_saved(self):
        self.open_edit('Beta')
        self.field('edit', 'name', 'text_input').input('Beta renamed')
        self.field('edit', 'done', 'checkbox').check()
        self.field('edit', 'priority', 'number_input').set_value(2)
        self.field('edit', 'note', 'text_area').input('Changed')
        self.click('Save Changes')
        task = self.stored('Beta renamed')
        self.assertEqual((task['status'], task['priority'], task['note']), ('done', 2, 'Changed'))

    def test_both_dates_can_be_cleared(self):
        self.open_edit('Alpha')
        self.field('edit', 'recurring', 'checkbox').uncheck()
        self.click(key='edit_clear_start_date_btn')
        self.click(key='edit_clear_due_date_btn')
        self.click('Save Changes')
        task = self.stored('Alpha')
        self.assertEqual((task['start_date'], task['due_date']), (None, None))

    def test_an_emptied_name_is_refused_rather_than_ignored(self):
        """update_task() keeps the old name when given none - silently, before."""
        self.open_edit('Beta')
        self.field('edit', 'name', 'text_input').input('')
        self.field('edit', 'note', 'text_area').input('Not saved')
        self.click('Save Changes')
        self.assertIn('Please enter a name.', self.errors())
        self.assertEqual(self.stored('Beta')['note'], 'Beta note')

    def test_the_next_task_does_not_show_the_last_one_s_dates(self):
        """Left by the toolbar rather than by Cancel - the bug."""
        self.open_edit('Alpha')
        self.leave_by_the_toolbar()
        self.open_edit('Beta')
        self.assertEqual(self.field('edit', 'due_date', 'date_input').value, date(2031, 2, 2))
        self.assertIsNone(self.field('edit', 'start_date', 'date_input').value)
        self.click('Save Changes')
        self.assertEqual(self.stored('Beta')['due_date'], '2031-02-02')
        self.assertIsNone(self.stored('Beta')['start_date'])

    def test_nor_after_a_cancel(self):
        self.open_edit('Alpha')
        self.click('Cancel')
        self.open_edit('Beta')
        self.assertEqual(self.field('edit', 'name', 'text_input').value, 'Beta')
        self.assertEqual(self.field('edit', 'priority', 'number_input').value, 0)

    def test_unsaved_changes_are_not_carried_back_into_the_same_task(self):
        self.open_edit('Beta')
        self.field('edit', 'priority', 'number_input').set_value(9)
        self._run()
        self.leave_by_the_toolbar()
        self.open_edit('Beta')
        self.assertEqual(self.field('edit', 'priority', 'number_input').value, 0)

    def test_values_the_form_cannot_show_do_not_break_it(self):
        with open('data.json', encoding='utf-8') as handle:
            data = json.load(handle)
        for task in data['projects'][0]['tasks']:
            if task['task_name'] == 'Beta':
                task.update(priority=None, userdefined_days='often', frequency='hourly',
                            note=None, due_date='not a date')
        with open('data.json', 'w', encoding='utf-8') as handle:
            json.dump(data, handle)
        self.open_edit('Beta')
        self.assertEqual(self.field('edit', 'priority', 'number_input').value, 0)
        self.assertEqual(self.field('edit', 'frequency', 'selectbox').value, 'daily')


class TestTheFieldsThemselves(TaskFormTestCase):

    def test_each_clear_button_is_styled_as_an_icon(self):
        """Without its selectors a clear button is drawn as a full-width button."""
        with open(VIEW, encoding='utf-8') as handle:
            source = handle.read()
        for kind in ('new', 'edit'):
            for which in ('start_date', 'due_date'):
                key = '%s_clear_%s_btn' % (kind, which)
                with self.subTest(key=key):
                    self.assertIn('[class*="st-key-%s"] button' % key, source)
                    self.assertIn(':has([class*="st-key-%s"])' % key, source)

    def test_the_frequencies_offered(self):
        self.open_add()
        self.assertEqual(self.field('new', 'frequency', 'selectbox').options,
                         ['daily', 'on all business days', 'weekly', 'monthly', 'userdefined'])

    def test_and_in_the_interface_s_language(self):
        with open('config.json', 'w', encoding='utf-8') as handle:
            json.dump({'language': 'de', 'data_file': 'data.json', 'mcp_server_enabled': False}, handle)
        sys.modules.pop('i18n', None)
        self.open_add()
        self.assertEqual(self.field('new', 'frequency', 'selectbox').options,
                         ['täglich', 'an allen Geschäftstagen', 'wöchentlich', 'monatlich',
                          'benutzerdefiniert'])

    def test_what_is_disabled_when(self):
        self.open_add()
        frequency = lambda: self.field('new', 'frequency', 'selectbox')
        days = lambda: self.field('new', 'userdefined_days', 'number_input')
        self.assertEqual((frequency().disabled, days().disabled), (True, True))
        self.field('new', 'recurring', 'checkbox').check()
        self._run()
        self.assertEqual((frequency().disabled, days().disabled), (False, True))
        frequency().set_value('userdefined')
        self._run()
        self.assertEqual((frequency().disabled, days().disabled), (False, False))
        self.field('new', 'recurring', 'checkbox').uncheck()
        self._run()
        self.assertEqual((frequency().disabled, days().disabled), (True, True))

    def test_the_bounds(self):
        self.open_add()
        priority = self.field('new', 'priority', 'number_input')
        days = self.field('new', 'userdefined_days', 'number_input')
        self.assertEqual((priority.min, priority.max, days.min), (0, 9, 1))

    def test_the_rows(self):
        """Both dates and their buttons in one row; Done in a column of its own."""
        self.open_edit('Alpha')
        weights = [round(column.weight * 12, 3) for column in self.app.columns]
        self.assertIn([5, 1, 5, 1], [weights[i:i + 4] for i in range(len(weights))])
        holding_done = [[getattr(child, 'key', None) for child in column.children.values()]
                        for column in self.app.columns
                        if 'edit_task_done' in [getattr(child, 'key', None)
                                                for child in column.children.values()]]
        self.assertEqual(len(holding_done), 1)
        self.assertNotIn('edit_task_priority', holding_done[0])

    def test_the_markdown_editor_is_given_each_form_s_field(self):
        for opening, key in ((self.open_add, 'new_task_note'),
                             (lambda: self.open_edit('Alpha'), 'edit_task_note')):
            with self.subTest(key=key):
                opening()
                frames = [element.proto.srcdoc for element in self.app.get('iframe')]
                self.assertTrue(any(key in frame for frame in frames))
                self.leave_by_the_toolbar()


class TestTheChecks(TaskFormTestCase):

    def test_a_start_on_the_due_date_is_fine(self):
        self.open_add()
        self.field('new', 'name', 'text_input').input('Gamma')
        self.field('new', 'start_date', 'date_input').set_value(LATER)
        self.field('new', 'due_date', 'date_input').set_value(LATER)
        self.click('Add Task')
        self.assertEqual(self.stored('Gamma')['start_date'], LATER.isoformat())

    def test_a_start_without_a_due_date_is_fine(self):
        """It becomes the due date as well - see TimeTracker._anchor_start_date()."""
        self.open_add()
        self.field('new', 'name', 'text_input').input('Gamma')
        self.field('new', 'start_date', 'date_input').set_value(EARLIER)
        self.click(key='new_clear_due_date_btn')
        self.click('Add Task')
        self.assertEqual(self.stored('Gamma')['start_date'], EARLIER.isoformat())

    def test_a_recurring_task_is_judged_by_its_due_date(self):
        self.open_add()
        self.field('new', 'name', 'text_input').input('Gamma')
        self.field('new', 'recurring', 'checkbox').check()
        self.field('new', 'start_date', 'date_input').set_value(EARLIER)
        self.click(key='new_clear_due_date_btn')
        self.click('Add Task')
        self.assertIn('A due date is required for recurring tasks.', self.errors())
        self.click(key='new_clear_start_date_btn')
        self.field('new', 'due_date', 'date_input').set_value(LATER)
        self.click('Add Task')
        self.assertTrue(self.stored('Gamma')['recurring'])

    def test_the_dates_are_marked_red(self):
        self.open_add()
        self.field('new', 'start_date', 'date_input').set_value(LATER)
        self.field('new', 'due_date', 'date_input').set_value(EARLIER)
        self._run()
        self.assertTrue(any('stDateInput' in element.value and 'border' in element.value
                            for element in self.app.markdown))

    def test_editing_is_held_to_the_same_checks(self):
        self.open_edit('Beta')
        self.field('edit', 'start_date', 'date_input').set_value(date(2032, 1, 1))
        self.field('edit', 'note', 'text_area').input('Not saved')
        self.click('Save Changes')
        self.assertIn('The start date cannot be after the due date.', self.errors())
        self.assertEqual(self.stored('Beta')['note'], 'Beta note')
        self.click(key='edit_clear_start_date_btn')
        self.field('edit', 'name', 'text_input').input('   ')
        self.click('Save Changes')
        self.assertIn('Please enter a name.', self.errors())
        self.assertIn('Beta', self.stored())

    def test_a_project_that_went_while_the_form_was_open(self):
        """The add form said nothing at all; the edit form already reported its failures."""
        self.open_add()
        self.field('new', 'name', 'text_input').input('Gamma')
        TimeTracker('data.json', op_outbox=None).rename_main_project('Work', 'Renamed')
        self.click('Add Task')
        self.assertIn("Main project 'Work' not found.", self.errors())


class TestWhereEachFormLeadsBack(TaskFormTestCase):

    FIELDS = ('name', 'start_date', 'due_date', 'today', 'done', 'recurring', 'frequency',
              'userdefined_days', 'priority', 'note')

    def _form_keys_left(self, kind):
        """The form's fields, what they last saw stored, and what it was opened for."""
        names = (['%s_task_%s' % (kind, field) for field in self.FIELDS]
                 + ['%s_task_%s__seen' % (kind, field) for field in self.FIELDS]
                 + ['%s_task__for' % kind])
        return [name for name in names if name in self.app.session_state]

    def _open(self, menu, name=None):
        context = {'selected_main': 'Work', 'return_to': 'today_view'}
        if name:
            context.update(selected_task=name, selected_task_id=self.ids[name])
        self.app.session_state['context'] = context
        self.app.session_state['menu'] = menu
        self._run()

    def test_saving_or_cancelling_goes_back_and_forgets_the_form(self):
        for menu, kind, name, button in (('add_task_form', 'new', None, 'Add Task'),
                                         ('add_task_form', 'new', None, 'Cancel'),
                                         ('edit_task_form', 'edit', 'Beta', 'Save Changes'),
                                         ('edit_task_form', 'edit', 'Beta', 'Cancel')):
            with self.subTest(form=kind, button=button):
                # A session of its own each time: AppTest keeps the nodes of
                # widgets whose keys were deleted, and trips over them.
                self.app = AppTest.from_file(VIEW, default_timeout=20)
                self._open(menu, name)
                if kind == 'new':
                    self.field('new', 'name', 'text_input').input('Gamma %s' % button)
                self.click(button)
                self.assertEqual(self.app.session_state['menu'], 'today_view')
                self.assertEqual(self.app.session_state['context'], {})
                self.assertEqual(self._form_keys_left(kind), [])


class TestEditingWhatIsStored(TaskFormTestCase):

    def test_every_field_is_saved_when_editing(self):
        self.open_edit('Beta')
        self.field('edit', 'start_date', 'date_input').set_value(EARLIER)
        self.field('edit', 'due_date', 'date_input').set_value(LATER)
        self.field('edit', 'today', 'checkbox').check()
        self.field('edit', 'recurring', 'checkbox').check()
        self._run()
        self.field('edit', 'frequency', 'selectbox').set_value('userdefined')
        self._run()
        self.field('edit', 'userdefined_days', 'number_input').set_value(4)
        self.click('Save Changes')
        task = self.stored('Beta')
        self.assertEqual((task['start_date'], task['due_date'], task['today'], task['recurring'],
                          task['frequency'], task['userdefined_days']),
                         (EARLIER.isoformat(), LATER.isoformat(), True, True, 'userdefined', 4))

    def test_what_was_not_touched_is_saved_as_it_was(self):
        before = self.stored('Alpha')
        self.open_edit('Alpha')
        self.field('edit', 'today', 'checkbox').uncheck()
        self.field('edit', 'recurring', 'checkbox').uncheck()
        self.click('Save Changes')
        after = self.stored('Alpha')
        for field in ('start_date', 'due_date', 'frequency', 'userdefined_days', 'priority', 'note'):
            with self.subTest(field=field):
                self.assertEqual(after[field], before[field])
        self.assertEqual((after['today'], after['recurring']), (False, False))

    def test_a_done_task_stays_done(self):
        self.patch_stored('Beta', status='done')
        self.open_edit('Beta')
        self.assertTrue(self.field('edit', 'done', 'checkbox').value)
        self.field('edit', 'priority', 'number_input').set_value(3)
        self.click('Save Changes')
        self.assertEqual((self.stored('Beta')['status'], self.stored('Beta')['priority']), ('done', 3))

    def test_every_frequency_survives_a_save(self):
        self.patch_stored('Alpha', frequency='on all business days')
        self.open_edit('Alpha')
        self.assertEqual(self.field('edit', 'frequency', 'selectbox').value, 'on all business days')
        self.click('Save Changes')
        self.assertEqual(self.stored('Alpha')['frequency'], 'on all business days')

    def test_numbers_are_shown_within_what_the_fields_take(self):
        self.patch_stored('Beta', priority=12, userdefined_days=400, recurring=True,
                          frequency='userdefined', due_date=20310202)
        self.open_edit('Beta')
        self.assertEqual(self.field('edit', 'priority', 'number_input').value, 9)
        self.assertEqual(self.field('edit', 'userdefined_days', 'number_input').value, 400)
        self.assertIsNone(self.field('edit', 'due_date', 'date_input').value)

    def test_the_task_opened_is_the_one_edited_when_two_share_a_name(self):
        tracker = TimeTracker('data.json', op_outbox=None)
        tracker.add_task('Work', 'Beta', note='second Beta')
        second = [t['id'] for t in tracker.list_tasks('Work')
                  if t['task_name'] == 'Beta' and t['id'] != self.ids['Beta']][0]
        self.app.session_state['context'] = {'selected_main': 'Work', 'selected_task': 'Beta',
                                             'selected_task_id': second, 'return_to': 'task_mgmt'}
        self.app.session_state['menu'] = 'edit_task_form'
        self._run()
        self.assertEqual(self.field('edit', 'note', 'text_area').value, 'second Beta')
        self.field('edit', 'priority', 'number_input').set_value(6)
        self.click('Save Changes')
        with open('data.json', encoding='utf-8') as handle:
            tasks = [t for p in json.load(handle)['projects'] for t in p['tasks'] if t['task_name'] == 'Beta']
        self.assertEqual({t['id']: t['priority'] for t in tasks}, {self.ids['Beta']: 0, second: 6})

    def test_switching_straight_to_another_task_starts_from_that_one(self):
        """
        Without another view drawn in between - which today every way to the
        form passes through, and which makes Streamlit forget the fields
        anyway. An unsaved priority from Alpha must not reach Gamma, whose
        stored priority is the same as Alpha's was.
        """
        tracker = TimeTracker('data.json', op_outbox=None)
        tracker.add_task('Work', 'Gamma', priority=7)
        self.ids['Gamma'] = [t['id'] for t in tracker.list_tasks('Work') if t['task_name'] == 'Gamma'][0]
        self.open_edit('Alpha')
        self.field('edit', 'priority', 'number_input').set_value(2)
        self._run()
        self.open_edit('Gamma')
        self.assertEqual(self.field('edit', 'priority', 'number_input').value, 7)
        self.assertEqual(self.field('edit', 'name', 'text_input').value, 'Gamma')

    def test_reopening_the_same_task_shows_it_as_stored(self):
        """Not just values that happen to be every field's default."""
        self.open_edit('Alpha')
        self.field('edit', 'priority', 'number_input').set_value(1)
        self._run()
        self.leave_by_the_toolbar()
        self.open_edit('Alpha')
        shown = {name: self.field('edit', name, widget).value for name, widget in (
            ('name', 'text_input'), ('start_date', 'date_input'), ('due_date', 'date_input'),
            ('recurring', 'checkbox'), ('frequency', 'selectbox'), ('priority', 'number_input'),
            ('note', 'text_area'))}
        self.assertEqual(shown, {'name': 'Alpha', 'start_date': EARLIER, 'due_date': LATER,
                                 'recurring': True, 'frequency': 'weekly', 'priority': 7,
                                 'note': 'Alpha note'})


class TestAChangeMadeElsewhereWhileTheFormIsOpen(TaskFormTestCase):
    """
    The MCP server, the other machine, another tab: the task changes while the
    form shows it. A field nobody touched follows the change, so saving does
    not put the old value back - and send it on to the other machines as an
    edit. One that was changed keeps what was typed.
    """

    def test_the_flags_changed_elsewhere_are_kept(self):
        self.open_edit('Beta')
        self.change_elsewhere('Beta', status='done', priority=6, today=True)
        self.field('edit', 'note', 'text_area').input('Edited here')
        self.click('Save Changes')
        task = self.stored('Beta')
        self.assertEqual((task['status'], task['priority'], task['today'], task['note']),
                         ('done', 6, True, 'Edited here'))

    def test_and_so_are_the_dates(self):
        self.open_edit('Beta')
        self.change_elsewhere('Beta', due_date='2032-03-03')
        self.field('edit', 'priority', 'number_input').set_value(4)
        self.click('Save Changes')
        self.assertEqual((self.stored('Beta')['due_date'], self.stored('Beta')['priority']),
                         ('2032-03-03', 4))

    def test_a_recurring_task_finished_elsewhere_is_not_reopened(self):
        """Reopened, it would stand beside the next one the other side created."""
        self.open_edit('Alpha')
        self.change_elsewhere('Alpha', status='done')
        self.field('edit', 'note', 'text_area').input('Edited here')
        self.click('Save Changes')
        with open('data.json', encoding='utf-8') as handle:
            alphas = [t for p in json.load(handle)['projects'] for t in p['tasks']
                      if t['task_name'] == 'Alpha']
        self.assertEqual(sorted(t['status'] for t in alphas), ['done', 'open'])

    def test_a_field_changed_here_keeps_what_was_typed(self):
        self.open_edit('Beta')
        self.field('edit', 'priority', 'number_input').set_value(3)
        self._run()
        self.change_elsewhere('Beta', today=True)
        self.click('Save Changes')
        self.assertEqual((self.stored('Beta')['priority'], self.stored('Beta')['today']), (3, True))


if __name__ == '__main__':
    unittest.main()
