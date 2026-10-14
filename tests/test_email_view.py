"""
The e-mail assignment view, run rather than read (issue #662).

From version 4.18 on, imported mails lost their notes - not in the import,
but seconds later, in this view. Assigning the first task emptied the note of
the next one, and so on down the list, so that of a morning's mails only the
first kept its text. The cause lay in how Streamlit treats a run that ends in
st.rerun(): it threw away the state of every widget the run had not got round
to drawing. follow_stored_value() did not expect a widget's key to vanish
while its own record of the stored value stayed, left the field unseeded, and
the form that saves whatever differs wrote the empty default over the note.

tests/test_email_task_form.py checks follow_stored_value() against a plain
dict, where keys never vanish - which is why it could not see this. Only the
real session state does that, so these tests drive the real script with
Streamlit's AppTest, against a data file of their own in a directory of their
own.

Newer Streamlit versions keep the widgets of a run that stopped for a rerun.
requirements.txt does not pin one, and the installation this was found on ran
an older one, so the form has to hold up either way - and these tests make
every version behave like the older ones, or they would test nothing on a
machine with a newer one.

What they must never touch is the real installation: the synchronisation
state lives in the user's configuration directory, so TC_CONFIG_DIR points
into the scratch directory before the script runs, and the update check, which
would ask GitHub, is kept from importing.
"""

import inspect
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import date
from unittest import mock

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
VIEW = os.path.join(REPO_ROOT, 'sl', 'SL_Menu.py')
sys.path.append(REPO_ROOT)

try:
    from streamlit.runtime.state.session_state import SessionState
    from streamlit.testing.v1 import AppTest
except ImportError:     # pragma: no cover - streamlit is in requirements-ci.txt
    AppTest = None

from tt.TimeTracker import TimeTracker

# What an Outlook forward looks like once imported: CRLF line endings and all.
# Invented, like everything else here - the repository is public.
NOTE = ("________________________________\r\n"
        "From: tracker@example.com\r\n"
        "Subject: Bug %d was modified\r\n\r\n"
        "Status: Modified\r\nBug ID  %d\r\n")


def _dropping_stale_widgets():
    """
    A patch that makes Streamlit drop the state of undrawn widgets after
    every run, as the versions this was found on did - or None where it does
    so anyway.
    """
    original = SessionState.on_script_finished
    if 'remove_stale_widgets' not in inspect.signature(original).parameters:
        return None

    def on_script_finished(self, widget_ids_this_run, **_ignored):
        return original(self, widget_ids_this_run, remove_stale_widgets=True)
    return mock.patch.object(SessionState, 'on_script_finished', on_script_finished)


@unittest.skipIf(AppTest is None, 'streamlit is not installed')
class TestTheViewKeepsWhatItShows(unittest.TestCase):

    TASKS = 4

    def setUp(self):
        # Every change is undone by a cleanup registered before it is made,
        # so that a failure half way through still leaves nothing behind -
        # least of all a blocked update module for the tests that follow.
        self.scratch = tempfile.mkdtemp(prefix='tc-email-view-')
        self.addCleanup(shutil.rmtree, self.scratch, ignore_errors=True)
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(self.scratch)

        environment = mock.patch.dict(os.environ, {'TC_CONFIG_DIR': os.path.join(self.scratch, 'sync')})
        environment.start()
        self.addCleanup(environment.stop)
        # None in sys.modules makes `import update` fail, which the view
        # takes to mean "no update module" - no request to GitHub.
        modules = mock.patch.dict(sys.modules, {'update': None})
        modules.start()
        self.addCleanup(modules.stop)

        dropping = _dropping_stale_widgets()
        if dropping:
            dropping.start()
            self.addCleanup(dropping.stop)

        with open('config.json', 'w', encoding='utf-8') as handle:
            json.dump({'language': 'en', 'data_file': 'data.json',
                       'mcp_server_enabled': False,
                       'email': {'enabled': False}}, handle)

        self.today = date.today().isoformat()
        tracker = TimeTracker('data.json', op_outbox=None)
        tracker.add_main_project(TimeTracker.HIDDEN_PROJECT)
        tracker.add_main_project('Work')
        for n in range(1, self.TASKS + 1):
            tracker.add_task(TimeTracker.HIDDEN_PROJECT, 'Bug %d was modified' % n,
                             due_date=self.today, today=True, note=NOTE % (n, n))
        self.ids = [t['id'] for t in tracker.list_tasks(TimeTracker.HIDDEN_PROJECT)]

    def _open(self):
        # The guard the docstring promises: never against the real directory.
        self.assertTrue(os.environ['TC_CONFIG_DIR'].startswith(self.scratch))
        # A form that keeps saving runs into this rather than into the
        # minutes a test run would otherwise wait.
        app = AppTest.from_file(VIEW, default_timeout=20)
        app.session_state['menu'] = 'email_assignment'
        app.session_state['context'] = {'return_to': 'today_view'}
        self._run(app)
        return app

    def _run(self, app):
        app.run()
        self.assertFalse(app.exception, [e.value for e in app.exception])

    def _stored(self):
        """Every task as it is on disk, by id, with the project it is in."""
        with open('data.json', encoding='utf-8') as handle:
            data = json.load(handle)
        return {task['id']: dict(task, project=project['main_project_name'])
                for project in data['projects'] for task in project['tasks']}

    def assertUntouched(self, task_id):
        n = self.ids.index(task_id) + 1
        task = self._stored()[task_id]
        self.assertEqual(task['note'], NOTE % (n, n), 'the note of task %d was changed' % n)
        self.assertTrue(task['today'], 'the Today flag of task %d was cleared' % n)
        self.assertEqual(task['due_date'], self.today)

    def _clear_date(self, app, task_id):
        app.button(key='clear_email_due_date_%s' % task_id).click()
        self._run(app)

    def test_opening_the_view_changes_nothing(self):
        app = self._open()
        self._run(app)
        for task_id in self.ids:
            self.assertUntouched(task_id)

    def test_assigning_from_the_top_down_keeps_every_note(self):
        """
        The way the list is actually worked through, and exactly what went
        wrong: the first task kept its note, every later one lost it.
        """
        app = self._open()
        for task_id in self.ids:
            app.selectbox(key='move_email_%s' % task_id).set_value('Work')
            self._run(app)
        for task_id in self.ids:
            self.assertEqual(self._stored()[task_id]['project'], 'Work')
            self.assertUntouched(task_id)

    def test_the_delete_button_does_not_empty_its_own_task(self):
        """
        It reruns before the task's own fields are drawn, so it was the task
        about to be deleted - or not - that lost its note.
        """
        app = self._open()
        first = self.ids[0]
        app.button(key='del_email_%s' % first).click()
        self._run(app)
        app.button(key='confirm_del_no_%s' % first).click()
        self._run(app)
        for task_id in self.ids:
            self.assertUntouched(task_id)

    def test_an_edit_is_saved_and_the_rest_left_alone(self):
        app = self._open()
        first = self.ids[0]
        app.text_area(key='email_task_note_%s' % first).input('Typed by hand')
        self._run(app)
        self.assertEqual(self._stored()[first]['note'], 'Typed by hand')
        for task_id in self.ids[1:]:
            self.assertUntouched(task_id)

    def test_the_clear_button_empties_the_date_and_nothing_else(self):
        """
        It raised StreamlitAPIException instead: it set the date field's value
        after the field had been drawn, which Streamlit refuses.
        """
        app = self._open()
        second = self.ids[1]
        self._clear_date(app, second)
        task = self._stored()[second]
        self.assertIsNone(task['due_date'])
        self.assertEqual(task['note'], NOTE % (2, 2))
        for task_id in [self.ids[0]] + self.ids[2:]:
            self.assertUntouched(task_id)

    def test_a_cleared_date_does_not_come_back(self):
        """Not as today's when the form is drawn again, nor when its fields were dropped."""
        app = self._open()
        first, second = self.ids[0], self.ids[1]
        self._clear_date(app, second)
        self._run(app)
        app.button(key='del_email_%s' % second).click()
        self._run(app)
        app.button(key='confirm_del_no_%s' % second).click()
        self._run(app)
        app.selectbox(key='move_email_%s' % first).set_value('Work')
        self._run(app)
        self._run(app)
        self.assertIsNone(self._stored()[second]['due_date'], 'the cleared date came back')

    def test_and_a_date_can_be_set_again(self):
        app = self._open()
        first = self.ids[0]
        self._clear_date(app, first)
        app.date_input(key='email_task_due_date_%s' % first).set_value(date(2030, 1, 2))
        self._run(app)
        self.assertEqual(self._stored()[first]['due_date'], '2030-01-02')

    def test_a_note_typed_just_before_clearing_is_kept(self):
        """
        A quick click reaches the server together with the note it ended the
        editing of. The button's own rerun used to drop that note.
        """
        app = self._open()
        first = self.ids[0]
        app.text_area(key='email_task_note_%s' % first).input('Typed before the click')
        app.button(key='clear_email_due_date_%s' % first).click()
        self._run(app)
        task = self._stored()[first]
        self.assertEqual(task['note'], 'Typed before the click')
        self.assertIsNone(task['due_date'])

    def test_an_emptied_name_is_put_back_rather_than_saved_for_ever(self):
        """
        update_task() keeps a task's name when given none, so the field stayed
        empty, still differed, and the form saved again on every run.
        """
        app = self._open()
        first = self.ids[0]
        app.text_input(key='email_task_name_%s' % first).input('')
        self._run(app)
        self.assertEqual(self._stored()[first]['task_name'], 'Bug 1 was modified')
        self.assertEqual(app.text_input(key='email_task_name_%s' % first).value,
                         'Bug 1 was modified')

    def test_mails_arriving_while_the_view_is_open(self):
        """
        Fetched through the MCP server, or sent by the other machine. Measured
        against the total the view opened with, the progress bar went negative
        and Streamlit refused to draw it, on every run until Back was pressed.
        """
        app = self._open()
        tracker = TimeTracker('data.json', op_outbox=None)
        for name in ('Late one', 'Later one'):
            tracker.add_task(TimeTracker.HIDDEN_PROJECT, name, due_date=self.today)
        self._run(app)
        app.selectbox(key='move_email_%s' % self.ids[0]).set_value('Work')
        self._run(app)


if __name__ == '__main__':
    unittest.main()
