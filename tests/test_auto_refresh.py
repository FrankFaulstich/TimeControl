"""
The automatic refresh while MCP is in use redraws only when there is
something new to show (issue #643).

Every five seconds a tick of a run_every fragment used to turn into a full run
of the script, whether anything had changed or not: a tenth to nearly half a
second of the server's time at the large test size, every time, and a redraw
in the browser of a screen that looked the same afterwards. Now a tick asks first. A full
run follows when the data file has changed, when synchronisation has fetched
something that waits to be applied, when the failure on record is not the
one the last full run saw, or when the view shows a running clock. And the
calendar, whose toolbar popovers a rerun closes, is not refreshed at all.

The ticks below are real ones: a fragment-scoped run through Streamlit's own
ScriptRunner, which runs nothing of the script but the fragment. AppTest has
no way of its own to ask for one, so the run it starts is given the
fragment's id - see _Ticking._tick(). Full runs are counted by
freeze_after_read(), which every full run calls once at the top.
"""

import builtins
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
VIEW = os.path.join(REPO, 'sl', 'SL_Menu.py')
sys.path.append(REPO)

try:
    from streamlit.testing.v1 import AppTest
    from streamlit.testing.v1 import local_script_runner
    from streamlit.runtime.fragment import MemoryFragmentStorage
except ImportError:     # pragma: no cover - streamlit is in requirements-ci.txt
    AppTest = None

import tt.gc_freeze
from tt import sync_engine
from tt.TimeTracker import TimeTracker


class NoOutbox:
    """The sync queue, switched off whatever config.json says."""

    def append(self, op, **fields):
        pass


class _InAScratchDirectory(unittest.TestCase):

    def setUp(self):
        self.scratch = tempfile.mkdtemp(prefix='tc-refresh-')
        self.addCleanup(shutil.rmtree, self.scratch, ignore_errors=True)
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(self.scratch)
        environment = mock.patch.dict(
            os.environ, {'TC_CONFIG_DIR': os.path.join(self.scratch, 'config')})
        environment.start()
        self.addCleanup(environment.stop)
        os.makedirs(os.path.join(self.scratch, 'config'))

    @staticmethod
    def _written_elsewhere(name='From Claude'):
        """What the MCP server does: its own tracker, its own save."""
        TimeTracker('data.json', op_outbox=NoOutbox()).add_main_project(name)

    @staticmethod
    def _cut_off_in_the_inbox():
        """What a full disk or a crash leaves of an append: a line that ends half way through 'Ü'."""
        line = json.dumps({'base_seq': 2, 'ops': [
            {'s': 2, 'op': 'project.create', 'uid': 'p-cut', 'f': {'name': 'Übersicht'}}]},
            ensure_ascii=False).encode('utf-8')
        with open(sync_engine.inbox_path(), 'ab') as handle:
            handle.write(line[:line.index('Ü'.encode('utf-8')) + 1])


class TestTheDataFileLooksUnchanged(_InAScratchDirectory):
    """TimeTracker.looks_unchanged(): is_current() without its time limit."""

    def setUp(self):
        super().setUp()
        TimeTracker('data.json', op_outbox=NoOutbox()).add_main_project('Work')
        self.tracker = TimeTracker('data.json', op_outbox=NoOutbox())

    def test_right_after_a_read(self):
        self.assertTrue(self.tracker.looks_unchanged())

    def test_and_after_its_own_save(self):
        self.tracker.add_main_project('More')
        self.assertTrue(self.tracker.looks_unchanged())

    def test_not_once_another_process_has_written(self):
        self._written_elsewhere()
        self.assertFalse(self.tracker.looks_unchanged())

    def test_still_after_the_time_is_current_trusts_a_read(self):
        """The choice made for #643: a full run every half minute is what the tick stopped."""
        self.tracker._read_at -= TimeTracker.TRUST_UNCHANGED_SECONDS + 1
        self.assertFalse(self.tracker.is_current())
        self.assertTrue(self.tracker.looks_unchanged())

    def test_not_without_inode_numbers(self):
        """What such a file system says about a file cannot be relied on."""
        signature = self.tracker._signature
        self.tracker._signature = signature[:1] + (0,) + signature[2:]
        with mock.patch.object(TimeTracker, '_file_signature',
                               return_value=self.tracker._signature):
            self.assertFalse(self.tracker.looks_unchanged())

    def test_nor_when_the_file_has_gone(self):
        os.remove('data.json')
        self.assertFalse(self.tracker.looks_unchanged())

    def test_nor_when_nothing_was_noted(self):
        self.tracker._signature = None
        self.assertFalse(self.tracker.looks_unchanged())


class TestWhatSynchronisationHasToSay(_InAScratchDirectory):
    """The two things a tick asks synchronisation, cheaply and without a lock."""

    def test_nothing_waits_without_an_inbox(self):
        self.assertFalse(sync_engine.inbox_waiting())

    def test_a_record_waits(self):
        sync_engine._append_inbox({'base_seq': 1, 'ops': []})
        self.assertTrue(sync_engine.inbox_waiting())

    def test_and_goes_on_waiting_until_it_is_taken(self):
        """A failed apply leaves it in place - waiting, not merely changed."""
        sync_engine._append_inbox({'base_seq': 1, 'ops': []})
        for _ in range(3):
            self.assertTrue(sync_engine.inbox_waiting())
        sync_engine.clear_inbox()
        self.assertFalse(sync_engine.inbox_waiting())

    def test_but_not_a_line_nobody_can_read(self):
        """apply_pending() skips it, so it would ask for a full run for ever."""
        with open(sync_engine.inbox_path(), 'w', encoding='utf-8') as handle:
            handle.write('{"base_seq": 1, "ops": [\n\n')
        self.assertFalse(sync_engine.inbox_waiting())

    def test_nor_one_cut_off_in_the_middle_of_a_character(self):
        """Not an error either: the tick would show it every five seconds."""
        self._cut_off_in_the_inbox()
        self.assertFalse(sync_engine.inbox_waiting())

    def test_while_what_came_before_it_still_waits(self):
        sync_engine._append_inbox({'base_seq': 1, 'ops': []})
        self._cut_off_in_the_inbox()
        self.assertTrue(sync_engine.inbox_waiting())
        self.assertEqual(sync_engine.read_inbox(), [{'base_seq': 1, 'ops': []}])

    def test_no_failure_on_record_to_begin_with(self):
        self.assertEqual(sync_engine.failure_on_record(), (None, 0))

    def test_a_failure_and_how_many_in_a_row(self):
        sync_engine.write_state({'last_error': 'invalid_token', 'failures': 3})
        self.assertEqual(sync_engine.failure_on_record(), ('invalid_token', 3))

    def test_a_quiet_cycle_changes_nothing_of_it(self):
        """Every cycle writes the state; only a failure is drawn from it."""
        sync_engine.write_state({'last_error': None, 'failures': 0})
        before = sync_engine.failure_on_record()
        sync_engine.write_state({'last_ok': int(time.time()), 'server_head': 7})
        self.assertEqual(sync_engine.failure_on_record(), before)

    def test_a_count_that_is_not_one_counts_as_none(self):
        sync_engine.write_state({'last_error': 'offline', 'failures': 'many'})
        self.assertEqual(sync_engine.failure_on_record(), ('offline', 0))


@unittest.skipIf(AppTest is None, 'streamlit is not installed')
class _Ticking(_InAScratchDirectory):
    """An interface session with MCP on, its full runs counted, and ticks on demand."""

    MENU = 'list_main_projects'
    SYNC = None

    def setUp(self):
        super().setUp()
        config = {'language': 'en', 'data_file': 'data.json',
                  'mcp_server_enabled': True, 'update': {'github_repo': ''}}
        if self.SYNC:
            config['sync'] = self.SYNC
        with open('config.json', 'w', encoding='utf-8') as handle:
            json.dump(config, handle)
        TimeTracker('data.json', op_outbox=NoOutbox()).add_main_project('Work')

        # Every full run calls it once, at the top; a tick that stays a tick
        # does not run that far.
        counting = mock.patch.object(tt.gc_freeze, 'freeze_after_read',
                                     mock.MagicMock(return_value=False))
        self.full_runs = counting.start()
        self.addCleanup(counting.stop)

        # Streamlit 1.55's AppTest gives every run a fragment store of its
        # own, so the fragment the last run registered would be gone by the
        # tick; later releases keep one per AppTest and never ask for this.
        self._fragments_kept = MemoryFragmentStorage()
        keeping = mock.patch.object(local_script_runner, 'MemoryFragmentStorage',
                                    lambda: self._fragments_kept)
        keeping.start()
        self.addCleanup(keeping.stop)

        self.app = AppTest.from_file(VIEW, default_timeout=20)
        self.app.session_state['menu'] = self.MENU
        self.app.session_state['context'] = {'return_to': 'today_view'}
        self.app.run()
        self._no_exception()

    def _no_exception(self):
        self.assertFalse(self.app.exception, [e.value for e in self.app.exception])

    def _fragments(self):
        storage = getattr(self.app, '_fragment_storage', None) or self._fragments_kept
        return list(storage._fragments)

    def _tick(self):
        """One timer tick of the refresh, as the browser asks for it every five seconds."""
        fragments = self._fragments()
        self.assertEqual(len(fragments), 1, 'the refresh, and nothing else, should be registered')
        plain = local_script_runner.RerunData

        def as_tick(**fields):
            return plain(fragment_id_queue=fragments, is_fragment_scoped_rerun=True,
                         is_auto_rerun=True, **fields)
        with mock.patch.object(local_script_runner, 'RerunData', as_tick):
            self.app.run()
        self._no_exception()

    def _shown(self, text):
        return any(text in element.value for element in self.app.markdown)


class TestAnIdleViewIsLeftAlone(_Ticking):

    def test_several_ticks_and_no_full_run(self):
        for _ in range(5):
            self._tick()
        self.assertEqual(self.full_runs.call_count, 1)

    def test_nor_once_the_last_read_is_too_old_to_trust(self):
        """Nothing changed; the next click reads the file again anyway."""
        self.app.session_state.tracker._read_at -= TimeTracker.TRUST_UNCHANGED_SECONDS + 1
        self._tick()
        self.assertEqual(self.full_runs.call_count, 1)


class TestAChangeIsShownAtTheNextTick(_Ticking):

    def test_another_process_writing_the_file(self):
        self._written_elsewhere('From Claude')
        self._tick()
        self.assertEqual(self.full_runs.call_count, 2)
        self.assertTrue(self._shown('From Claude'))

    def test_and_after_that_it_is_quiet_again(self):
        self._written_elsewhere()
        self._tick()
        self._tick()
        self._tick()
        self.assertEqual(self.full_runs.call_count, 2)


class TestWithSynchronisationOff(_Ticking):
    """What another time left behind is not this session's business."""

    def test_a_leftover_inbox_or_failure_brings_no_full_run(self):
        sync_engine._append_inbox({'base_seq': 1, 'ops': []})
        sync_engine.write_state({'last_error': 'invalid_token', 'failures': 9})
        for _ in range(3):
            self._tick()
        self.assertEqual(self.full_runs.call_count, 1)


class TestWithoutStreamlitsRunContext(_Ticking):
    """
    The fallback for a Streamlit that no longer has get_script_run_ctx(): it
    tells a tick by the time since the last run, and asks the same question.
    """

    def setUp(self):
        # From SL_Menu.py alone - Streamlit itself cannot do without it.
        real_import = builtins.__import__

        def without_it_for_the_view(name, globals=None, locals=None, fromlist=(), level=0):
            if (name.endswith('script_run_context') and 'get_script_run_ctx' in (fromlist or ())
                    and (globals or {}).get('__file__') == VIEW):
                raise ImportError('get_script_run_ctx has gone')
            return real_import(name, globals, locals, fromlist, level)
        hidden = mock.patch.object(builtins, '__import__', without_it_for_the_view)
        hidden.start()
        self.addCleanup(hidden.stop)
        super().setUp()

    def _five_seconds_on(self):
        self.app.session_state['_last_auto_refresh'] -= 5

    def test_an_idle_view_is_left_alone(self):
        for _ in range(3):
            self._five_seconds_on()
            self._tick()
        self.assertEqual(self.full_runs.call_count, 1)

    def test_a_change_is_shown(self):
        self._written_elsewhere('From Claude')
        self._five_seconds_on()
        self._tick()
        self.assertEqual(self.full_runs.call_count, 2)
        self.assertTrue(self._shown('From Claude'))


class TestTheRunningClock(_Ticking):

    MENU = 'show_current_work'

    def setUp(self):
        super().setUp()
        tracker = TimeTracker('data.json', op_outbox=NoOutbox())
        tracker.add_task('Work', 'Typing')
        tracker.start_work('Work', task_id=tracker.list_tasks()[0]['id'])
        self._tick()        # the change, shown
        self.shown_after_start = self.full_runs.call_count

    def test_keeps_going(self):
        for _ in range(3):
            self._tick()
        self.assertEqual(self.full_runs.call_count, self.shown_after_start + 3)


class TestTheCalendar(_Ticking):

    MENU = 'calendar'

    def test_is_not_refreshed_at_all(self):
        """A rerun closes its toolbar's popovers, as on the other two tabs."""
        self.assertEqual(self._fragments(), [])


@unittest.skipIf(AppTest is None, 'streamlit is not installed')
class _Synchronising(_Ticking):
    """The same, with synchronisation switched on and its worker held still."""

    SYNC = {'enabled': True, 'base_url': 'https://example.invalid/tc'}

    def setUp(self):
        for name in ('ensure_started', 'nudge'):
            patcher = mock.patch.object(sync_engine, name, mock.MagicMock(return_value=True))
            patcher.start()
            self.addCleanup(patcher.stop)
        super().setUp()

    @staticmethod
    def _arrives(name):
        """What the worker files when the other machine created a project."""
        sync_engine._append_inbox({'base_seq': 1, 'ops': [
            {'s': 1, 'op': 'project.create', 'uid': 'p-elsewhere', 'f': {'name': name}}]})


class TestWhatArrivedIsApplied(_Synchronising):

    def test_at_the_next_tick(self):
        self._arrives('From the other machine')
        self._tick()
        self.assertEqual(self.full_runs.call_count, 2)
        self.assertTrue(self._shown('From the other machine'))
        self.assertFalse(sync_engine.inbox_waiting())

    def test_and_then_nothing_more(self):
        self._arrives('From the other machine')
        for _ in range(3):
            self._tick()
        self.assertEqual(self.full_runs.call_count, 2)

    def test_and_a_line_cut_off_after_it_goes_with_it(self):
        """_tick() also checks that no tick shows an error."""
        self._arrives('From the other machine')
        self._cut_off_in_the_inbox()
        self._tick()
        self.assertTrue(self._shown('From the other machine'))
        self.assertFalse(os.path.exists(sync_engine.inbox_path()))
        self._tick()
        self.assertEqual(self.full_runs.call_count, 2)

    def test_tried_again_while_it_cannot_be_saved(self):
        """A full disk, a share gone read-only: the inbox stays, and so does the reason to redraw."""
        real = sync_engine.apply_pending
        tries = []

        def twice_read_only(tracker):
            tries.append(tracker)
            if len(tries) <= 2:
                raise OSError('read-only')
            return real(tracker)
        failing = mock.patch.object(sync_engine, 'apply_pending', side_effect=twice_read_only)
        failing.start()
        self.addCleanup(failing.stop)
        self._arrives('From the other machine')
        for _ in range(3):
            self._tick()
        self.assertEqual(self.full_runs.call_count, 4)
        self.assertTrue(self._shown('From the other machine'))
        self._tick()
        self.assertEqual(self.full_runs.call_count, 4)


class TestThePausedNotice(_Synchronising):

    def test_comes_with_the_next_tick(self):
        sync_engine.write_state({'last_error': 'invalid_token', 'failures': 1})
        self._tick()
        self.assertEqual(self.full_runs.call_count, 2)
        self.assertTrue(any('Synchronisation is paused' in element.value
                            for element in self.app.warning))

    def test_and_once_shown_is_not_drawn_again(self):
        sync_engine.write_state({'last_error': 'invalid_token', 'failures': 1})
        for _ in range(3):
            self._tick()
        self.assertEqual(self.full_runs.call_count, 2)

    def test_while_a_cycle_that_went_well_brings_no_full_run(self):
        sync_engine.write_state({'last_ok': int(time.time()), 'server_head': 9})
        self._tick()
        self.assertEqual(self.full_runs.call_count, 1)


if __name__ == '__main__':
    unittest.main()
