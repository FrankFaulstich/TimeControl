"""
The two buttons that decide about updates, run rather than read (issue #597).

A rollback used to be undone at the next start: the launcher of a source
checkout downloads a newer release on the way out and installs it on the way
in, and nothing told it the user had just rolled back from that one. Now the
rollback leaves a note and the launcher leaves that version alone - see
tests/test_TimeTrackerSL_GUI.py for that half.

This is the half in the interface. Restore Previous Version has to say which
version it rolled back from, or there is no note. And the update button is
the one way that version may come back, so it has to take the note away - but
only once the update is actually on its way, or a failed download would leave
the user's decision forgotten for nothing.

Driven with Streamlit's AppTest in a directory of their own, which is also
where the note is kept - it belongs to the installation, beside update.zip.
The update module's network and restart functions are replaced, and
TC_CONFIG_DIR points into the scratch directory too, for the synchronisation
state every run of the interface reads.

The notice comes from an answer the interface asks for in the background and
keeps for an hour (issue #641). Each test starts with none, and _open() waits
for it before the first run, as a user would by clicking on.
"""

import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import zipfile
from unittest import mock

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
VIEW = os.path.join(REPO_ROOT, 'sl', 'SL_Menu.py')
sys.path.append(REPO_ROOT)

try:
    from streamlit.testing.v1 import AppTest
except ImportError:     # pragma: no cover - streamlit is in requirements-ci.txt
    AppTest = None

import update
from tt.TimeTracker import TimeTracker

RUNNING = TimeTracker.VERSION
NEWER = '999.0'


def _wait_for_the_update_check():
    """Until the thread update.known_update() started, if any, is done."""
    for thread in threading.enumerate():
        if thread.name == 'update-check':
            thread.join(10)


@unittest.skipIf(AppTest is None, 'streamlit is not installed')
class TestTheButtonsKeepTheNote(unittest.TestCase):

    def setUp(self):
        self.scratch = tempfile.mkdtemp(prefix='tc-update-buttons-')
        self.addCleanup(shutil.rmtree, self.scratch, ignore_errors=True)
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(self.scratch)

        environment = mock.patch.dict(
            os.environ, {'TC_CONFIG_DIR': os.path.join(self.scratch, 'config')})
        environment.start()
        self.addCleanup(environment.stop)

        with open('config.json', 'w', encoding='utf-8') as handle:
            json.dump({'language': 'en', 'data_file': 'data.json',
                       'mcp_server_enabled': False}, handle)
        TimeTracker('data.json', op_outbox=None).add_main_project('Work')

        # The view imports these from the module on every run, so replacing
        # them on the module is what it sees. Nothing reaches the network,
        # and nothing ends the process.
        self.patched = {}
        for name, value in (
                ('check_for_updates', (True, NEWER, 'https://example.invalid/new.zip')),
                ('apply_update', True),
                ('download_exe_update', True),
                ('request_restart', None)):
            patcher = mock.patch.object(update, name, mock.MagicMock(return_value=value))
            self.patched[name] = patcher.start()
            self.addCleanup(patcher.stop)
        # Nothing known about releases yet - an answer is kept for an hour,
        # and the one an earlier test was given would do for this one.
        fresh = mock.patch.object(update, '_release_check', update._ReleaseCheck())
        fresh.start()
        self.addCleanup(fresh.stop)
        # Before the two above are undone, or a thread still asking would ask
        # the real GitHub, and keep its answer for the tests that follow.
        self.addCleanup(_wait_for_the_update_check)

    def _open(self, menu):
        self.assertTrue(os.environ['TC_CONFIG_DIR'].startswith(self.scratch))
        update.known_update(RUNNING)
        _wait_for_the_update_check()
        app = AppTest.from_file(VIEW, default_timeout=20)
        app.session_state['menu'] = menu
        app.session_state['context'] = {'return_to': 'today_view'}
        app.run()
        self.assertFalse(app.exception, [e.value for e in app.exception])
        return app

    def _click(self, app, key):
        app.button(key=key).click()
        app.run()

    @staticmethod
    def _notice(app):
        return any(button.key == 'update_restart_btn' for button in app.button)

    # --- asking GitHub (issue #641) -----------------------------------------

    def test_changing_views_does_not_ask_github(self):
        app = self._open('today_view')
        for menu in ('task_planning', 'calendar', 'reporting', 'settings', 'today_view'):
            app.session_state['menu'] = menu
            app.run()
            self.assertFalse(app.exception, [e.value for e in app.exception])
        _wait_for_the_update_check()
        self.patched['check_for_updates'].assert_called_once_with(RUNNING, unanswered=None)

    def test_the_first_run_does_not_wait_for_the_answer_and_a_later_one_shows_it(self):
        asked, release = threading.Event(), threading.Event()
        newer = self.patched['check_for_updates'].return_value

        def slow(version, **unused):
            asked.set()
            release.wait(10)
            return newer
        self.patched['check_for_updates'].side_effect = slow
        self.addCleanup(release.set)

        app = AppTest.from_file(VIEW, default_timeout=20)
        app.session_state['menu'] = 'settings'
        app.session_state['context'] = {'return_to': 'today_view'}
        app.run()
        self.assertFalse(app.exception, [e.value for e in app.exception])
        self.assertTrue(asked.wait(5), 'the interface did not ask')
        self.assertFalse(self._notice(app), 'the run waited for GitHub')

        release.set()
        _wait_for_the_update_check()
        app.run()
        self.assertTrue(self._notice(app))
        self.patched['check_for_updates'].assert_called_once_with(RUNNING, unanswered=None)

    def test_a_check_that_gets_no_answer_leaves_the_notice_where_it_is(self):
        """A moment without Wi-Fi at the hourly check, and GitHub fine again."""
        app = self._open('today_view')
        self.assertTrue(self._notice(app))
        self.patched['check_for_updates'].return_value = None
        update._release_check.asked_at -= update.UPDATE_CHECK_INTERVAL
        for menu in ('task_planning', 'settings'):
            app.session_state['menu'] = menu
            app.run()
            _wait_for_the_update_check()
            self.assertTrue(self._notice(app), menu)
        self.assertEqual(self.patched['check_for_updates'].call_count, 2)

    # --- Restore Previous Version -------------------------------------------

    def test_a_rollback_remembers_the_version_it_left(self):
        with zipfile.ZipFile('prev-version.zip', 'w') as archive:
            archive.writestr('TimeControl-old/marker.py', 'old = True\n')
        app = self._open('settings')
        self._click(app, 'settings_restore_btn')
        self.assertTrue(os.path.exists('marker.py'), 'the files were not restored')
        self.patched['request_restart'].assert_called_once_with()
        self.assertEqual(update.declined_version(), RUNNING)

    def test_so_does_one_in_the_windows_build(self):
        here = os.path.join(self.scratch, 'app')
        os.makedirs(here)
        with open(os.path.join(here, update.PREVIOUS_EXE), 'wb') as handle:
            handle.write(b'the build before')
        restore = mock.MagicMock(return_value=True)
        with mock.patch.object(sys, 'frozen', True, create=True), \
             mock.patch.object(sys, 'executable', os.path.join(here, 'TimeControl.exe')), \
             mock.patch.object(update, 'restore_previous_exe', restore):
            app = self._open('settings')
            self._click(app, 'settings_restore_btn')
        restore.assert_called_once_with(rolled_back_from=RUNNING)

    # --- the update button --------------------------------------------------

    def test_asking_for_the_update_takes_the_note_away(self):
        """
        Before the restart, not after: the restart ends the process, and a
        note still there would make the launcher discard what was asked for.
        """
        update.remember_declined_version(NEWER)
        at_restart = []
        self.patched['request_restart'].side_effect = (
            lambda: at_restart.append(update.declined_version()))
        app = self._open('settings')
        self._click(app, 'update_restart_btn')
        self.assertEqual(at_restart, [None])
        self.assertIsNone(update.declined_version())

    def test_a_note_that_will_not_go_stops_the_restart(self):
        update.remember_declined_version(NEWER)
        with mock.patch.object(update, 'forget_declined_version',
                               mock.MagicMock(return_value=False)):
            app = self._open('settings')
            self._click(app, 'update_restart_btn')
        self.patched['request_restart'].assert_not_called()
        self.assertIn(update.DECLINED_UPDATE_FILE,
                      ' '.join(element.value for element in app.error))

    def test_but_not_when_the_download_failed(self):
        """Nothing is on its way; the user's earlier decision still stands."""
        update.remember_declined_version(NEWER)
        self.patched['apply_update'].return_value = False
        app = self._open('settings')
        self._click(app, 'update_restart_btn')
        self.patched['request_restart'].assert_not_called()
        self.assertEqual(update.declined_version(), NEWER)

    def test_the_windows_build_does_the_same(self):
        update.remember_declined_version(NEWER)
        with mock.patch.object(sys, 'frozen', True, create=True):
            app = self._open('settings')
            self.patched['download_exe_update'].return_value = False
            self._click(app, 'update_restart_btn')
            self.assertEqual(update.declined_version(), NEWER)
            self.patched['download_exe_update'].return_value = True
            self._click(app, 'update_restart_btn')
        self.assertIsNone(update.declined_version())


if __name__ == '__main__':
    unittest.main()
