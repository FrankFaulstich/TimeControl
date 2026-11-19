"""
A theme whose file was renamed keeps working under its old name.

config.json names the theme by its file. The warm theme's file was renamed,
and a config.json written before that still names the old one. A new
installation does not have the old file: without a way back from the old
name, the interface would find no file to load and lose its theme
altogether, and the settings would show a current file that is not in their
list. An installation updated from source still has it beside the new one,
because an update unpacks the new release over the old files and removes
none: the settings would offer both, and choosing the old one would lead
back to the new one.

Driven with Streamlit's AppTest in a directory of its own, with
TC_CONFIG_DIR pointing into it too, for the synchronisation state every run
of the interface reads.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
VIEW = os.path.join(REPO_ROOT, 'sl', 'SL_Menu.py')
SL_DIR = os.path.join(REPO_ROOT, 'sl')
sys.path.append(REPO_ROOT)

try:
    from streamlit.testing.v1 import AppTest
except ImportError:     # pragma: no cover - streamlit is in requirements-ci.txt
    AppTest = None

from tt.TimeTracker import TimeTracker

OLD_NAME = 'style_claude.css'
NEW_NAME = 'style_warm.css'
LEFTOVER = '/* the old file an update left behind */\n'


class NoOutbox:
    """The sync queue, switched off whatever config.json says."""

    def append(self, op, **fields):
        pass


def _theme(name):
    with open(os.path.join(SL_DIR, name), encoding='utf-8') as handle:
        return handle.read()


@unittest.skipIf(AppTest is None, 'streamlit is not installed')
class TestARenamedThemeIsFoundUnderItsOldName(unittest.TestCase):

    def setUp(self):
        self.scratch = tempfile.mkdtemp(prefix='tc-theme-')
        self.addCleanup(shutil.rmtree, self.scratch, ignore_errors=True)
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(self.scratch)
        environment = mock.patch.dict(
            os.environ, {'TC_CONFIG_DIR': os.path.join(self.scratch, 'config')})
        environment.start()
        self.addCleanup(environment.stop)
        os.makedirs(os.path.join(self.scratch, 'config'))
        TimeTracker('data.json', op_outbox=NoOutbox()).add_main_project('Work')

    def _open(self, menu, view=VIEW, **config):
        settings = {'language': 'en', 'data_file': 'data.json',
                    'mcp_server_enabled': False, 'update': {'github_repo': ''}}
        settings.update(config)
        with open('config.json', 'w', encoding='utf-8') as handle:
            json.dump(settings, handle)
        app = AppTest.from_file(view, default_timeout=20)
        app.session_state['menu'] = menu
        app.run()
        self.assertFalse(app.exception, [e.value for e in app.exception])
        return app

    @staticmethod
    def _loaded(app, name):
        """Whether the run put the whole of that theme's file on the page."""
        theme = _theme(name)
        return any(theme in element.value for element in app.markdown)

    @staticmethod
    def _css_choice(app):
        found = [box for box in app.selectbox if box.label == 'Select CSS File']
        assert len(found) == 1, [box.label for box in app.selectbox]
        return found[0]

    def _updated_installation(self):
        """
        The interface from a copy of sl/ that still has the old file, as an
        update leaves it. The script finds the rest of the application
        through the path this module put the repository on.
        """
        copy = os.path.join(self.scratch, 'sl')
        os.makedirs(copy)
        for name in os.listdir(SL_DIR):
            if name.endswith(('.py', '.css', '.png')):
                shutil.copy(os.path.join(SL_DIR, name), copy)
        with open(os.path.join(copy, OLD_NAME), 'w', encoding='utf-8') as handle:
            handle.write(LEFTOVER)
        return os.path.join(copy, 'SL_Menu.py')

    @staticmethod
    def _stored():
        with open('config.json', encoding='utf-8') as handle:
            return json.load(handle).get('css_file')

    def test_the_old_file_is_gone_and_the_new_one_is_there(self):
        """Or the tests below would prove nothing about the rename."""
        self.assertFalse(os.path.exists(os.path.join(SL_DIR, OLD_NAME)))
        self.assertTrue(os.path.exists(os.path.join(SL_DIR, NEW_NAME)))

    def test_the_old_name_loads_the_renamed_file(self):
        app = self._open('today_view', css_file=OLD_NAME)
        self.assertTrue(self._loaded(app, NEW_NAME))

    def test_the_settings_show_it_under_its_new_name(self):
        app = self._open('settings', css_file=OLD_NAME)
        choice = self._css_choice(app)
        self.assertIn(NEW_NAME, choice.options)
        self.assertNotIn(OLD_NAME, choice.options)
        self.assertEqual(choice.value, NEW_NAME)
        self.assertTrue(any('`%s`' % NEW_NAME in element.value for element in app.markdown))

    def test_and_saving_them_stores_the_new_name(self):
        app = self._open('settings', css_file=OLD_NAME)
        save = [button for button in app.button if button.proto.form_id == 'css_form']
        self.assertEqual(len(save), 1, [button.proto.form_id for button in app.button])
        save[0].click()
        app.run()
        self.assertFalse(app.exception, [e.value for e in app.exception])
        self.assertEqual(self._stored(), NEW_NAME)

    def test_an_old_file_left_by_an_update_is_not_offered(self):
        view = self._updated_installation()
        app = self._open('settings', view=view, css_file=OLD_NAME)
        choice = self._css_choice(app)
        self.assertNotIn(OLD_NAME, choice.options)
        self.assertEqual(choice.value, NEW_NAME)
        self.assertTrue(self._loaded(app, NEW_NAME))
        self.assertFalse(any(LEFTOVER in element.value for element in app.markdown))

    def test_nor_beside_a_theme_chosen_under_the_new_name(self):
        view = self._updated_installation()
        app = self._open('settings', view=view, css_file=NEW_NAME)
        choice = self._css_choice(app)
        self.assertNotIn(OLD_NAME, choice.options)
        self.assertEqual(choice.value, NEW_NAME)

    def test_the_new_name_loads_it_too(self):
        app = self._open('today_view', css_file=NEW_NAME)
        self.assertTrue(self._loaded(app, NEW_NAME))

    def test_another_theme_is_left_as_it_is(self):
        app = self._open('settings', css_file='style_dark.css')
        self.assertTrue(self._loaded(app, 'style_dark.css'))
        self.assertFalse(self._loaded(app, NEW_NAME))
        self.assertEqual(self._css_choice(app).value, 'style_dark.css')

    def test_no_theme_chosen_is_the_plain_one(self):
        app = self._open('today_view')
        self.assertTrue(self._loaded(app, 'style.css'))
        self.assertFalse(self._loaded(app, NEW_NAME))


if __name__ == '__main__':
    unittest.main()
