"""
Saving data.json quickly, and once per change (issue #639).

Every change writes the whole document. json.dump() streamed it through the
pure-Python encoder in many small writes - most of a second per save on a
large document, with the lock held - and a task switch saved twice, because
start_work() stopped the running session through stop_work(), which saved on
its own. Now the document is encoded in one piece and written in one call,
and a switch saves once.

What must not change is the file itself: byte for byte what json.dump()
wrote, with indent 2, every non-ASCII character escaped, and - on Windows -
CR LF line endings from the text-mode file. And the changes sent to the other
machines must be the same ones, in the same order.
"""

import glob
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import tt.TimeTracker
from tt.TimeTracker import TimeTracker


class RecordingOutbox:
    """Stands in for the sync queue: what each operation reports, in order."""

    def __init__(self):
        self.ops = []

    def append(self, op, **fields):
        self.ops.append(dict(op=op, **fields))
        return len(self.ops)

    def names(self):
        return [o['op'] for o in self.ops]


class SavingTestCase(unittest.TestCase):

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix='tc-saving-')
        self.addCleanup(shutil.rmtree, self.home, True)
        self.path = os.path.join(self.home, 'data.json')
        self.outbox = RecordingOutbox()
        self.tracker = TimeTracker(file_path=self.path, op_outbox=self.outbox)

    def _saves_during(self, action):
        """Runs action() and returns how often the document was saved meanwhile."""
        real = TimeTracker._save_data
        with mock.patch.object(TimeTracker, '_save_data', autospec=True, side_effect=real) as save:
            action()
        return save.call_count

    def _stored(self):
        with open(self.path, encoding='utf-8') as handle:
            return json.load(handle)


class TestTheFileIsWhatItWas(SavingTestCase):

    def _fill(self):
        tracker = self.tracker
        tracker.add_main_project('Größe & "Quotes" \\ back')
        tracker.add_task('Größe & "Quotes" \\ back', 'Rechnung \N{EURO SIGN} 100',
                         note='Zeile 1\nZeile 2\r\n\ttab \N{SMILING FACE WITH SMILING EYES} '
                              '\N{ZERO WIDTH JOINER} </script>',
                         due_date='2026-11-02', today=True, priority=7, recurring=True,
                         frequency='weekly')
        tracker.add_main_project('Empty')
        tracker.start_work('Größe & "Quotes" \\ back', 'Rechnung \N{EURO SIGN} 100')
        tracker.stop_work()

    def test_byte_for_byte_what_json_dump_wrote(self):
        """
        The reference is written the way every earlier version wrote it, so
        this holds on every platform: on Windows both carry CR LF.
        """
        self._fill()
        self.tracker._save_data()
        reference = os.path.join(self.home, 'reference.json')
        with open(reference, 'w', encoding='utf-8') as handle:
            json.dump(self.tracker.data, handle, indent=2)
        with open(self.path, 'rb') as saved, open(reference, 'rb') as expected:
            self.assertEqual(saved.read(), expected.read())

    def test_still_ascii_with_two_spaces(self):
        self._fill()
        with open(self.path, 'rb') as handle:
            raw = handle.read()
        raw.decode('ascii')
        self.assertIn(b'\\u20ac', raw)
        self.assertIn(b'\n  "projects": [', raw)

    def test_written_in_one_call(self):
        """Many small writes were half the cost of a save on a large document."""
        self._fill()
        writes = []
        real_fdopen = os.fdopen

        def counting_fdopen(*args, **kwargs):
            handle = real_fdopen(*args, **kwargs)
            real_write = handle.write

            def write(text):
                writes.append(len(text))
                return real_write(text)
            handle.write = write
            return handle
        # Far larger than any buffer a chunked write would use.
        self.tracker.add_task('Empty', 'Long note', note='x' * 300000)
        with mock.patch.object(tt.TimeTracker.os, 'fdopen', side_effect=counting_fdopen):
            self.tracker._save_data()
        self.assertEqual(len(writes), 1)
        self.assertGreater(writes[0], 300000)

    def test_the_file_is_complete_and_closed_before_it_is_swapped_in(self):
        """A file still open, or not yet flushed, would arrive empty - or not at all on Windows."""
        self._fill()
        seen = []
        real = TimeTracker._replace_when_windows_lets_go

        def look_first(tracker, tmp_path):
            with open(tmp_path, 'rb') as handle:
                seen.append(handle.read())
            return real(tracker, tmp_path)
        with mock.patch.object(TimeTracker, '_replace_when_windows_lets_go', autospec=True,
                               side_effect=look_first):
            self.tracker._save_data()
        with open(self.path, 'rb') as handle:
            self.assertEqual(seen, [handle.read()])

    def test_interrupted_while_swapping_leaves_no_temporary_file(self):
        self._fill()
        with mock.patch.object(TimeTracker, '_replace_when_windows_lets_go',
                               side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.tracker._save_data()
        self.assertEqual(glob.glob(os.path.join(self.home, '.tmp_data_*')), [])

    def test_a_document_that_cannot_be_written_leaves_everything_as_it_was(self):
        """Encoded before the temporary file exists: nothing to clean up, nothing replaced."""
        self._fill()
        with open(self.path, 'rb') as handle:
            before = handle.read()
        self.tracker.data['projects'][0]['not json'] = {1, 2}
        with mock.patch.object(tt.TimeTracker.tempfile, 'mkstemp',
                               side_effect=AssertionError('temporary file made first')):
            with self.assertRaises(TypeError):
                self.tracker._save_data()
        with open(self.path, 'rb') as handle:
            self.assertEqual(handle.read(), before)
        self.assertEqual(glob.glob(os.path.join(self.home, '.tmp_data_*')), [])


class TestSwitchingTasksSavesOnce(SavingTestCase):

    def setUp(self):
        super().setUp()
        self.tracker.add_main_project('P')
        self.tracker.add_task('P', 'A')
        self.tracker.add_task('P', 'B')

    def _entries(self, task_name):
        for project in self._stored()['projects']:
            for task in project['tasks']:
                if task['task_name'] == task_name:
                    return task['time_entries']
        self.fail('no task %s' % task_name)

    def test_a_switch_saves_once_and_reports_what_it_always_did(self):
        self.tracker.start_work('P', 'A')
        running = self._entries('A')[-1]['uid']
        self.outbox.ops.clear()

        saves = self._saves_during(lambda: self.tracker.start_work('P', 'B'))

        self.assertEqual(saves, 1)
        self.assertEqual(self.outbox.names(),
                         ['entry.close', 'entry.add', 'task.set', 'project.set'])
        self.assertEqual(self.outbox.ops[0]['uid'], running)
        # The one save holds both halves of the switch.
        self.assertIn('end_time', self._entries('A')[-1])
        self.assertNotIn('end_time', self._entries('B')[-1])
        self.assertEqual(self.outbox.ops[1]['uid'], self._entries('B')[-1]['uid'])

    def test_starting_with_nothing_running_saves_once(self):
        saves = self._saves_during(lambda: self.tracker.start_work('P', 'A'))
        self.assertEqual(saves, 1)
        self.assertEqual(self.outbox.names()[-3:], ['entry.add', 'task.set', 'project.set'])
        self.assertNotIn('entry.close', self.outbox.names())

    def test_stopping_still_saves(self):
        self.tracker.start_work('P', 'A')
        self.outbox.ops.clear()
        result = []
        saves = self._saves_during(lambda: result.append(self.tracker.stop_work()))
        self.assertEqual((result, saves, self.outbox.names()), ([True], 1, ['entry.close']))
        self.assertIn('end_time', self._entries('A')[-1])

    def test_stopping_with_nothing_running_saves_nothing(self):
        self.outbox.ops.clear()
        result = []
        saves = self._saves_during(lambda: result.append(self.tracker.stop_work()))
        self.assertEqual((result, saves, self.outbox.names()), ([False], 0, []))

    def test_stopping_works_on_the_file_as_it_is_now(self):
        """
        stop_work() reads the document again under the lock, as before: a copy
        that has gone stale in memory must not undo a switch made elsewhere.
        """
        self.tracker.start_work('P', 'A')
        elsewhere = TimeTracker(file_path=self.path)
        elsewhere.start_work('P', 'B')
        self.assertTrue(self.tracker.stop_work())
        a_entries, b_entries = self._entries('A'), self._entries('B')
        self.assertEqual((len(a_entries), len(b_entries)), (1, 1))
        self.assertTrue(all('end_time' in entry for entry in a_entries + b_entries))

    def test_an_unknown_task_stops_nothing(self):
        self.tracker.start_work('P', 'A')
        self.outbox.ops.clear()
        result = []
        saves = self._saves_during(lambda: result.append(self.tracker.start_work('P', 'Nope')))
        self.assertEqual((result, saves, self.outbox.names()), ([False], 0, []))
        self.assertNotIn('end_time', self._entries('A')[-1])


if __name__ == '__main__':
    unittest.main()
