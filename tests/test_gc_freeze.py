"""
The document out of the garbage collector's sight (issue #642).

Streamlit runs a full collection after every run of the interface, and on
Python 3.14 that visited every dict and list of the document - 87-147 ms of
each run at the large test size. Now the interface freezes what is alive once
a document has been read, and the collections after each run pass it by.

What has to hold besides: a freeze only when something new was read, nothing
kept alive that would have been freed, and what one freeze did keep collected
by the next.

These tests freeze for real where that is what they are about, and unfreeze
again afterwards - the rest of the suite runs in this same process.
"""

import ast
import gc
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import weakref
from unittest import mock

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
VIEW = os.path.join(REPO, 'sl', 'SL_Menu.py')
sys.path.append(REPO)

try:
    from streamlit.testing.v1 import AppTest
except ImportError:     # pragma: no cover - streamlit is in requirements-ci.txt
    AppTest = None

import tt.gc_freeze
from tt.gc_freeze import freeze_after_read
from tt.TimeTracker import TimeTracker


class NoOutbox:
    """The sync queue, switched off whatever config.json says."""

    def append(self, op, **fields):
        pass


class Probe:
    """Something a weak reference can be taken to, which a dict cannot."""


class _OnOneFile(unittest.TestCase):

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix='tc-gc-freeze-')
        self.addCleanup(shutil.rmtree, self.home, True)
        self.path = os.path.join(self.home, 'data.json')
        TimeTracker(file_path=self.path, op_outbox=NoOutbox()).add_main_project("P")
        self.tracker = TimeTracker(file_path=self.path, op_outbox=NoOutbox())

    def _written_elsewhere(self):
        """What the MCP server does: its own tracker, its own save."""
        TimeTracker(file_path=self.path, op_outbox=NoOutbox()).add_main_project(
            "Q%d" % time.monotonic_ns())


class TestTheTrackerCountsWhatItReads(_OnOneFile):

    def test_one_read_to_begin_with(self):
        self.assertEqual(self.tracker.read_count, 1)

    def test_so_without_a_file_yet(self):
        """An empty document is a new document too."""
        missing = os.path.join(self.home, 'none.json')
        self.assertEqual(TimeTracker(file_path=missing, op_outbox=NoOutbox()).read_count, 1)

    def test_its_own_saves_are_not_reads(self):
        self.tracker.add_main_project("R")
        self.tracker.reload_data()
        self.assertEqual(self.tracker.read_count, 1)

    def test_nor_is_a_look_at_a_file_that_has_not_changed(self):
        for _ in range(5):
            self.tracker.reload_data()
        self.assertEqual(self.tracker.read_count, 1)

    def test_a_file_changed_elsewhere_is(self):
        self._written_elsewhere()
        self.tracker.reload_data()
        self.assertEqual(self.tracker.read_count, 2)

    def test_and_so_is_one_a_write_finds_changed(self):
        self._written_elsewhere()
        self.tracker.add_main_project("R")
        self.assertEqual(self.tracker.read_count, 2)

    def test_and_one_read_again_because_it_was_read_too_long_ago(self):
        self.tracker._read_at -= TimeTracker.TRUST_UNCHANGED_SECONDS + 1
        self.tracker.reload_data()
        self.assertEqual(self.tracker.read_count, 2)

    def test_but_not_one_that_could_not_be_read(self):
        """The document from before is still the one in memory."""
        with open(self.path, 'w', encoding='utf-8') as handle:
            handle.write('{"projects": [')
        with self.assertRaises(json.JSONDecodeError):
            self.tracker.reload_data()
        self.assertEqual(self.tracker.read_count, 1)


class TestFreezingAfterARead(_OnOneFile):

    def setUp(self):
        super().setUp()
        self.addCleanup(gc.unfreeze)

    @staticmethod
    def _visible(thing):
        """Whether the collector still visits it - frozen objects it does not."""
        return any(other is thing for other in gc.get_objects())

    def test_the_first_call_freezes_the_document(self):
        self.assertTrue(freeze_after_read(self.tracker))
        self.assertFalse(self._visible(self.tracker.data))
        self.assertFalse(self._visible(self.tracker.data["projects"]))

    def test_the_next_without_a_read_does_nothing_at_all(self):
        """Every run calls it, and most runs read nothing."""
        freeze_after_read(self.tracker)
        with mock.patch.object(tt.gc_freeze, 'gc') as collector:
            for _ in range(5):
                self.assertFalse(freeze_after_read(self.tracker))
        self.assertEqual(collector.mock_calls, [])

    def test_after_a_read_it_freezes_the_new_document(self):
        freeze_after_read(self.tracker)
        self._written_elsewhere()
        self.tracker.reload_data()
        self.assertTrue(self._visible(self.tracker.data))
        self.assertTrue(freeze_after_read(self.tracker))
        self.assertFalse(self._visible(self.tracker.data))

    def test_it_collects_before_it_freezes(self):
        """Or what is garbage at that moment would be frozen with the rest."""
        with mock.patch.object(tt.gc_freeze, 'gc') as collector:
            freeze_after_read(self.tracker)
        self.assertEqual(collector.mock_calls,
                         [mock.call.unfreeze(), mock.call.collect(), mock.call.freeze()])

    def test_what_a_freeze_kept_the_next_one_collects(self):
        """
        A cycle frozen while in use and dropped afterwards outlives every
        collection after a run - until the next freeze.
        """
        cycle = Probe()
        cycle.itself = cycle
        gone = weakref.ref(cycle)
        freeze_after_read(self.tracker)
        del cycle
        gc.collect()
        self.assertIsNotNone(gone(), "the freeze did not take")
        self._written_elsewhere()
        self.tracker.reload_data()
        freeze_after_read(self.tracker)
        self.assertIsNone(gone())

    def test_freezing_keeps_nothing_alive(self):
        """The document a read replaces goes at once, frozen or not."""
        probe = Probe()
        self.tracker.data["probe"] = probe
        gone = weakref.ref(probe)
        del probe
        freeze_after_read(self.tracker)
        self._written_elsewhere()
        self.tracker.reload_data()
        self.assertIsNone(gone(), "the replaced document is still there")

    def test_each_tracker_for_itself(self):
        """One browser session reading does not stand in for another."""
        other = TimeTracker(file_path=self.path, op_outbox=NoOutbox())
        self.assertTrue(freeze_after_read(self.tracker))
        self.assertTrue(freeze_after_read(other))
        self.assertFalse(freeze_after_read(self.tracker))

    def test_a_closed_sessions_tracker_is_not_kept_for_it(self):
        freeze_after_read(self.tracker)
        gone = weakref.ref(self.tracker)
        del self.tracker
        gc.unfreeze()
        gc.collect()
        self.assertIsNone(gone())

    def test_and_the_next_sessions_tracker_gets_a_freeze_of_its_own(self):
        """It often takes the closed one's place in memory, and so its id()."""
        for _ in range(20):
            tracker = TimeTracker(file_path=self.path, op_outbox=NoOutbox())
            self.assertTrue(freeze_after_read(tracker))
            del tracker
            gc.unfreeze()
            gc.collect()

    def test_nor_is_a_document_frozen_that_the_next_look_reads_again(self):
        """
        A file system without inode numbers: is_current() is never true there,
        so the sweep later in the run reads the file again, and what was
        frozen would be gone before the run's collection came round.
        """
        real = TimeTracker._file_signature

        def without_inode(tracker):
            signature = real(tracker)
            return signature[:1] + (0,) + signature[2:]
        with mock.patch.object(TimeTracker, '_file_signature', without_inode):
            self.tracker.reload_data()
            self.assertEqual(self.tracker.read_count, 2)
            with mock.patch.object(tt.gc_freeze, 'gc') as collector:
                self.assertFalse(freeze_after_read(self.tracker))
        self.assertEqual(collector.mock_calls, [])

    def test_one_freeze_however_many_sessions_ask_at_once(self):
        """Each browser tab runs the interface in a thread of its own."""
        callers = 8
        together = threading.Barrier(callers)
        with mock.patch.object(tt.gc_freeze, 'gc') as collector:
            collector.collect.side_effect = lambda: time.sleep(0.01)

            def run():
                together.wait(5)
                freeze_after_read(self.tracker)
            threads = [threading.Thread(target=run) for _ in range(callers)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(5)
        self.assertEqual(collector.freeze.call_count, 1)


class TestOnlyTheInterfaceFreezes(unittest.TestCase):
    """The servers build a tracker per request; a full collection each would cost them."""

    def test_nothing_else_imports_it(self):
        importers = set()
        for directory, _dirs, files in os.walk(REPO):
            if any(part.startswith('.') or part in ('tests', 'build', 'dist', '__pycache__')
                   for part in os.path.relpath(directory, REPO).split(os.sep)):
                continue
            for name in files:
                if not name.endswith('.py'):
                    continue
                path = os.path.join(directory, name)
                with open(path, encoding='utf-8') as handle:
                    tree = ast.parse(handle.read())
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom) and node.module == 'tt.gc_freeze' or \
                            isinstance(node, ast.Import) and any(
                                alias.name == 'tt.gc_freeze' for alias in node.names):
                        importers.add(os.path.relpath(path, REPO))
        self.assertEqual(importers, {os.path.join('sl', 'SL_Menu.py')})


@unittest.skipIf(AppTest is None, 'streamlit is not installed')
class TestTheInterfaceFreezesWhatItReads(unittest.TestCase):
    """
    Driven with Streamlit's AppTest in a directory of its own, with the
    collector replaced: what is counted is when the interface freezes, and
    nothing of this process is frozen for real.
    """

    def setUp(self):
        self.scratch = tempfile.mkdtemp(prefix='tc-gc-freeze-view-')
        self.addCleanup(shutil.rmtree, self.scratch, ignore_errors=True)
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(self.scratch)
        environment = mock.patch.dict(
            os.environ, {'TC_CONFIG_DIR': os.path.join(self.scratch, 'config')})
        environment.start()
        self.addCleanup(environment.stop)
        with open('config.json', 'w', encoding='utf-8') as handle:
            json.dump({'language': 'en', 'data_file': 'data.json',
                       'mcp_server_enabled': False,
                       'update': {'github_repo': ''}}, handle)
        TimeTracker('data.json', op_outbox=NoOutbox()).add_main_project('Work')
        collector = mock.patch.object(tt.gc_freeze, 'gc')
        self.collector = collector.start()
        self.addCleanup(collector.stop)
        self.app = AppTest.from_file(VIEW, default_timeout=20)

    def _run(self):
        self.app.run()
        self.assertFalse(self.app.exception, [e.value for e in self.app.exception])
        return self.collector.freeze.call_count

    def test_the_first_run_freezes_what_it_read(self):
        self.assertEqual(self._run(), 1)

    def test_the_runs_after_it_do_not(self):
        self._run()
        for menu in ('task_planning', 'reporting', 'today_view'):
            self.app.session_state['menu'] = menu
            self.assertEqual(self._run(), 1, menu)

    def test_until_another_process_has_written(self):
        self._run()
        TimeTracker('data.json', op_outbox=NoOutbox()).add_main_project('From Claude')
        self.assertEqual(self._run(), 2)
        self.assertEqual(self._run(), 2)

    def test_or_the_last_read_is_too_old_to_trust(self):
        self._run()
        self.app.session_state.tracker._read_at -= TimeTracker.TRUST_UNCHANGED_SECONDS + 1
        self.assertEqual(self._run(), 2)


if __name__ == '__main__':
    unittest.main()
