"""
Issue #640: data.json is read again only when it has changed.

The interface read the whole document on every click, and every write read it
once more under the lock - on a document of a few tens of megabytes a fifth of
a second each time, to arrive at what was in memory already. The Today and
Planning views paid it twice per redraw, because the sweep that clears overdue
flags is a write and took the lock even when there was nothing to clear. Now
the file's identity, size and times are noted when it is read or written, and
an unchanged file is not read again.

What must not happen in exchange is what the lock was added for (#558): a
writer working from a copy that is no longer what the file holds, and saving
it over somebody else's change. Most of what follows is about that.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import types
import unittest
from datetime import date, timedelta
from unittest import mock

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(REPO)

import tt.TimeTracker
from tt.TimeTracker import TimeTracker
from tt.filelock import LockTimeout


class NoOutbox:
    """The sync queue, switched off whatever config.json says."""

    def append(self, op, **fields):
        pass


def _tasks_of(path, project="P"):
    with open(path, encoding='utf-8') as handle:
        document = json.load(handle)
    for entry in document["projects"]:
        if entry.get("main_project_name") == project:
            return sorted(task["task_name"] for task in entry["tasks"])
    return []


def _names(tracker):
    return sorted(task["task_name"] for task in tracker.data["projects"][0]["tasks"])


class _OnOneFile(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = os.path.join(self.tmp, 'data.json')
        self.tracker = self.new_tracker()
        self.tracker.add_main_project("P")

    def new_tracker(self):
        """Another process's view of the same file - what the MCP server builds per call."""
        return TimeTracker(file_path=self.path, op_outbox=NoOutbox())

    def reads(self, tracker):
        """Counts the times the file is actually read, not merely looked at."""
        return mock.patch.object(tracker, '_load_data', wraps=tracker._load_data)

    def rewrite(self, change, keep_identity=False):
        """
        Changes the file the way something outside this application might:
        the same number of bytes, and the modification time put back to what
        it was. Either as a new file moved into place, which is what every
        writer here does, or into the existing one.
        """
        before = os.stat(self.path)
        with open(self.path, encoding='utf-8') as handle:
            text = handle.read()
        changed = change(text)
        self.assertNotEqual(changed, text)
        self.assertEqual(len(changed.encode('utf-8')), len(text.encode('utf-8')))
        if keep_identity:
            with open(self.path, 'r+', encoding='utf-8') as handle:
                handle.write(changed)
            target = self.path
        else:
            target = self.path + '.new'
            with open(target, 'w', encoding='utf-8') as handle:
                handle.write(changed)
        os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))
        if not keep_identity:
            os.replace(target, self.path)
        after = os.stat(self.path)
        # The test is only worth something if this is true.
        self.assertEqual((after.st_size, after.st_mtime_ns),
                         (before.st_size, before.st_mtime_ns))
        return before, after


class TestAnUnchangedFileIsNotReadAgain(_OnOneFile):
    """What the change is for."""

    def test_a_redraw_reads_nothing_when_nothing_has_changed(self):
        with self.reads(self.tracker) as load:
            for _ in range(3):
                self.tracker.reload_data()
        self.assertEqual(load.call_count, 0)

    def test_nor_does_a_write_read_again_what_it_has(self):
        with self.reads(self.tracker) as load:
            self.tracker.add_task("P", "A")
            self.tracker.add_task("P", "B")
        self.assertEqual(load.call_count, 0)
        self.assertEqual(_tasks_of(self.path), ["A", "B"])

    def test_its_own_save_is_known_for_what_it_is(self):
        """Or every click after a change would read the file once more."""
        self.tracker.add_task("P", "A")
        self.assertTrue(self.tracker.is_current())
        with self.reads(self.tracker) as load:
            self.tracker.reload_data()
        self.assertEqual(load.call_count, 0)

    def test_the_today_sweeps_do_not_lock_when_there_is_nothing_to_do(self):
        """
        They run on every redraw of the Today and Planning views. Taking the
        lock for nothing also left the document unvouched-for, so the next
        redraw read it again.
        """
        self.tracker.add_task("P", "Later", due_date=(date.today() + timedelta(days=3)).isoformat())
        taken = []
        real = tt.TimeTracker.locked

        def counting(*args, **kwargs):
            taken.append(args)
            return real(*args, **kwargs)
        with mock.patch('tt.TimeTracker.locked', counting), self.reads(self.tracker) as load:
            self.assertFalse(self.tracker.cleanup_overdue_today_tasks())
            self.assertFalse(self.tracker.set_today_flag_for_due_tasks())
            self.tracker.reload_data()
        self.assertEqual(taken, [])
        self.assertEqual(load.call_count, 0)

    def test_but_they_do_their_work_when_there_is_some(self):
        """
        And only that work: a task marked by hand ahead of its due date, or
        with none, stays marked; one not yet due stays unmarked.
        """
        today = date.today()
        yesterday = (today - timedelta(days=1)).isoformat()
        later = (today + timedelta(days=2)).isoformat()
        self.tracker.add_task("P", "Overdue", due_date=yesterday, today=True)
        self.tracker.add_task("P", "Ahead", due_date=later, today=True)
        self.tracker.add_task("P", "Undated", today=True)
        self.tracker.add_task("P", "Due", due_date=today.isoformat())
        self.tracker.add_task("P", "Later", due_date=later)
        self.assertTrue(self.tracker.cleanup_overdue_today_tasks())
        self.assertTrue(self.tracker.set_today_flag_for_due_tasks())
        with open(self.path, encoding='utf-8') as handle:
            today_flags = {t["task_name"]: t["today"]
                           for t in json.load(handle)["projects"][0]["tasks"]}
        self.assertEqual(today_flags, {"Overdue": False, "Ahead": True, "Undated": True,
                                       "Due": True, "Later": False})


class TestAChangeElsewhereIsSeen(_OnOneFile):
    """The other side: nothing written by anyone else may be missed."""

    def test_another_writer_s_change_shows_up_on_the_next_redraw(self):
        server = self.new_tracker()
        server.add_task("P", "from MCP")
        self.tracker.reload_data()
        self.assertEqual(_names(self.tracker), ["from MCP"])

    def test_and_a_write_here_does_not_overwrite_it(self):
        """The case the lock is for: no reload in between, as from a callback."""
        server = self.new_tracker()
        server.add_task("P", "from MCP")
        self.tracker.add_task("P", "from GUI")
        self.assertEqual(_tasks_of(self.path), ["from GUI", "from MCP"])

    def test_two_writers_taking_turns_both_keep_everything(self):
        other = self.new_tracker()
        for i in range(10):
            self.tracker.add_task("P", "here %02d" % i)
            other.add_task("P", "there %02d" % i)
        self.assertEqual(len(_tasks_of(self.path)), 20)
        self.tracker.reload_data()
        self.assertEqual(_names(self.tracker), _tasks_of(self.path))

    def test_a_new_file_of_the_same_size_with_the_time_put_back(self):
        """
        A backup restored with its times, or a copy from another machine: the
        size and the modification time say nothing has changed. The file is
        a different one, though, and that is noticed.
        """
        self.tracker.add_task("P", "AAAA")
        self.rewrite(lambda text: text.replace('"AAAA"', '"BBBB"'))
        self.tracker.reload_data()
        self.assertEqual(_names(self.tracker), ["BBBB"])

    def test_and_a_write_does_not_lose_it_either(self):
        self.tracker.add_task("P", "AAAA")
        self.rewrite(lambda text: text.replace('"AAAA"', '"BBBB"'))
        self.tracker.add_task("P", "C")
        self.assertEqual(_tasks_of(self.path), ["BBBB", "C"])

    @unittest.skipUnless(os.name == 'posix', "Windows' st_ctime is the creation time, "
                         "which writing into the file leaves alone. There only the "
                         "trust window covers this - see the test after next.")
    def test_the_same_file_rewritten_in_place_with_the_time_put_back(self):
        """
        Same file, same size, same modification time. What gives it away is
        st_ctime, which nothing can set back. On some systems it moves in
        steps of a few milliseconds, hence the pause.
        """
        self.tracker.add_task("P", "AAAA")
        time.sleep(0.05)
        before, after = self.rewrite(lambda text: text.replace('"AAAA"', '"BBBB"'),
                                     keep_identity=True)
        self.assertEqual(after.st_ino, before.st_ino)
        self.assertNotEqual(after.st_ctime_ns, before.st_ctime_ns)
        self.tracker.reload_data()
        self.assertEqual(_names(self.tracker), ["BBBB"])

    def test_every_part_of_the_signature_counts(self):
        """Any one of them differing, alone, is a changed file."""
        for field in ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns'):
            with self.subTest(field=field):
                tracker = self.new_tracker()
                self.assertTrue(tracker.is_current())
                real = os.stat

                def shifted(path, *args, **kwargs):
                    found = real(path, *args, **kwargs)
                    fields = {name: getattr(found, name) for name in
                              ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')}
                    fields[field] += 1
                    return types.SimpleNamespace(**fields)
                with mock.patch('tt.TimeTracker.os.stat', shifted), self.reads(tracker) as load:
                    tracker.reload_data()
                self.assertEqual(load.call_count, 1)

    def test_what_the_comparison_cannot_see_is_seen_within_the_trust_window(self):
        """
        FAT and exFAT keep coarse times and make up inode numbers, and on
        Windows nothing in the signature moves when a file is written into
        in place with its time set back. For all of that there is the
        window: past it, the file is read whatever it looks like.
        """
        self.tracker.add_task("P", "AAAA")
        with open(self.path, encoding='utf-8') as handle:
            text = handle.read()
        with open(self.path, 'w', encoding='utf-8') as handle:
            handle.write(text.replace('"AAAA"', '"BBBB"'))
        # Made invisible: the tracker is told the file looks as it noted.
        self.tracker._signature = self.tracker._file_signature()
        self.tracker.reload_data()
        self.assertEqual(_names(self.tracker), ["AAAA"])

        self.tracker._read_at -= TimeTracker.TRUST_UNCHANGED_SECONDS + 1
        self.tracker.reload_data()
        self.assertEqual(_names(self.tracker), ["BBBB"])

    def test_and_inside_it_a_write_reads_again_once_it_has_passed(self):
        self.tracker.add_task("P", "AAAA")
        with open(self.path, encoding='utf-8') as handle:
            text = handle.read()
        with open(self.path, 'w', encoding='utf-8') as handle:
            handle.write(text.replace('"AAAA"', '"BBBB"'))
        self.tracker._signature = self.tracker._file_signature()
        self.tracker._read_at -= TimeTracker.TRUST_UNCHANGED_SECONDS + 1
        self.tracker.add_task("P", "C")
        self.assertEqual(_tasks_of(self.path), ["BBBB", "C"])

    def test_saving_here_does_not_restart_the_window(self):
        """
        It runs from the last time the file was read. A session that writes
        every few seconds would otherwise never read it again, and a change
        the comparison cannot see would stay unseen for as long as it kept
        writing - and be written over.
        """
        self.tracker._read_at -= 20
        self.tracker.add_task("P", "AAAA")
        with open(self.path, encoding='utf-8') as handle:
            text = handle.read()
        with open(self.path, 'w', encoding='utf-8') as handle:
            handle.write(text.replace('"AAAA"', '"BBBB"'))
        self.tracker._signature = self.tracker._file_signature()
        self.tracker._read_at -= TimeTracker.TRUST_UNCHANGED_SECONDS - 20 + 1
        self.tracker.reload_data()
        self.assertEqual(_names(self.tracker), ["BBBB"])

    def test_a_file_system_without_inode_numbers_is_never_trusted(self):
        """Without an identity, a new file of the same size and time is invisible."""
        real = TimeTracker._file_signature

        def no_inode(tracker):
            signature = real(tracker)
            return signature[:1] + (0,) + signature[2:]
        with mock.patch.object(TimeTracker, '_file_signature', no_inode):
            tracker = self.new_tracker()
            self.assertFalse(tracker.is_current())
            with self.reads(tracker) as load:
                tracker.reload_data()
                tracker.reload_data()
        self.assertEqual(load.call_count, 2)

    def test_a_stale_copy_does_not_hide_an_overdue_flag(self):
        """
        The sweeps look at memory before they lock. Memory that is not
        known to be current proves nothing, and then they go to the file.
        """
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        server = self.new_tracker()
        server.add_task("P", "Overdue", due_date=yesterday, today=True)
        self.assertTrue(self.tracker.cleanup_overdue_today_tasks())
        with open(self.path, encoding='utf-8') as handle:
            self.assertFalse(json.load(handle)["projects"][0]["tasks"][0]["today"])

    def test_nor_a_task_that_has_become_due(self):
        server = self.new_tracker()
        server.add_task("P", "Due", due_date=date.today().isoformat())
        self.assertTrue(self.tracker.set_today_flag_for_due_tasks())
        with open(self.path, encoding='utf-8') as handle:
            self.assertTrue(json.load(handle)["projects"][0]["tasks"][0]["today"])


class TestNothingStaleIsTakenForCurrent(_OnOneFile):
    """
    Memory that differs from the file must never be taken for it. Before
    this change every read went to the file and dropped such differences;
    they must still be dropped.
    """

    def junk(self):
        self.tracker.data["projects"].append(
            {"main_project_name": "junk", "status": "open", "tasks": []})

    def has_junk(self):
        return any(p["main_project_name"] == "junk" for p in self.tracker.data["projects"])

    def test_after_an_exception_inside_exclusive_the_file_is_read_again(self):
        with self.assertRaises(ValueError):
            with self.tracker.exclusive():
                self.junk()
                raise ValueError("stop")
        self.assertFalse(self.tracker.is_current())
        self.tracker.reload_data()
        self.assertFalse(self.has_junk())
        self.tracker.add_task("P", "T")
        with open(self.path, encoding='utf-8') as handle:
            names = [p["main_project_name"] for p in json.load(handle)["projects"]]
        self.assertNotIn("junk", names)

    def test_also_when_the_block_had_saved_before_it_raised(self):
        with self.assertRaises(ValueError):
            with self.tracker.exclusive():
                self.tracker.add_task("P", "saved")
                self.junk()
                raise ValueError("stop")
        self.tracker.reload_data()
        self.assertFalse(self.has_junk())
        self.assertEqual(_names(self.tracker), ["saved"])

    def test_and_when_an_inner_call_raised_and_the_outer_one_carried_on(self):
        with self.tracker.exclusive():
            self.tracker.add_task("P", "saved")
            try:
                with self.tracker.exclusive():
                    self.junk()
                    raise ValueError("stop")
            except ValueError:
                pass
        self.tracker.reload_data()
        self.assertFalse(self.has_junk())

    def test_a_block_that_does_not_save_leaves_nothing_behind(self):
        with self.tracker.exclusive():
            self.junk()
        self.tracker.reload_data()
        self.assertFalse(self.has_junk())

    def test_a_save_that_did_not_go_through_is_not_taken_for_one(self):
        self.junk()
        self.tracker.REPLACE_RETRY_SECONDS = 0.05
        with mock.patch('tt.TimeTracker.os.replace',
                        side_effect=PermissionError(5, 'Access is denied')):
            with self.assertRaises(PermissionError):
                self.tracker._save_data()
        self.assertFalse(self.tracker.is_current())
        self.tracker.reload_data()
        self.assertFalse(self.has_junk())

    def test_a_file_put_in_place_of_the_one_just_saved_is_not_taken_for_it(self):
        """Something outside the application, between the save and the look."""
        real = self.tracker._replace_when_windows_lets_go

        def and_then_someone_else(tmp_path):
            real(tmp_path)
            other = self.path + '.other'
            with open(other, 'w', encoding='utf-8') as handle:
                json.dump({"projects": [{"main_project_name": "P", "tasks": []},
                                        {"main_project_name": "foreign", "tasks": []}]},
                          handle)
            os.replace(other, self.path)
        with mock.patch.object(self.tracker, '_replace_when_windows_lets_go',
                               and_then_someone_else):
            self.tracker.add_task("P", "T")
        self.assertFalse(self.tracker.is_current())
        self.tracker.reload_data()
        self.assertIn("foreign", [p["main_project_name"] for p in self.tracker.data["projects"]])

    def test_a_file_replaced_between_the_look_and_the_read_is_read_again(self):
        """
        The look comes first, so what is noted is the file that was there
        before - whichever one the read then found. Noted the other way round,
        an old document would be held under the new file's description and
        never read again.

        The other writer comes in before the file is opened rather than
        while it is open: Windows does not let a file that is open be
        replaced, so there the save would wait for a read that waits for it.
        """
        real = self.tracker._file_signature
        writer = self.new_tracker()
        looked = []

        def look_then_someone_writes():
            signature = real()
            if not looked:
                looked.append(signature)
                writer.add_task("P", "meanwhile")
            return signature
        self.tracker._signature = None
        with mock.patch.object(self.tracker, '_file_signature', look_then_someone_writes):
            self.tracker.reload_data()
        self.assertEqual(len(looked), 1, 'the other writer did not come in')
        self.assertFalse(self.tracker.is_current())
        with self.reads(self.tracker) as load:
            self.tracker.reload_data()
        self.assertEqual(load.call_count, 1)
        self.assertEqual(_names(self.tracker), ["meanwhile"])
        self.assertTrue(self.tracker.is_current())

    def test_nor_one_replaced_just_after_the_read(self):
        """
        The case that decides the order. Looked at after the read instead,
        the new file's description would be noted over the old file's
        content, and that old document held as current for good.

        Written once the file is closed again, for the same reason as above.
        """
        writer = self.new_tracker()
        real_load = json.load
        real_look = self.tracker._file_signature
        state = {'read': False, 'written': False}

        def load(handle, *args, **kwargs):
            document = real_load(handle, *args, **kwargs)
            state['read'] = True
            return document

        def look():
            if state['read'] and not state['written']:
                state['written'] = True
                writer.add_task("P", "meanwhile")
            return real_look()
        self.tracker._signature = None
        with mock.patch('tt.TimeTracker.json.load', load), \
                mock.patch.object(self.tracker, '_file_signature', look):
            self.tracker.reload_data()
        if not state['written']:
            # No look after the read, as it should be: the other writer
            # comes in now, at the same point.
            writer.add_task("P", "meanwhile")
        self.assertEqual(_names(self.tracker), [])
        self.assertFalse(self.tracker.is_current())
        self.tracker.reload_data()
        self.assertEqual(_names(self.tracker), ["meanwhile"])

    def test_a_read_that_fails_leaves_nothing_noted(self):
        """The previous document stays in memory - and is not taken for the file."""
        with open(self.path, 'w', encoding='utf-8') as handle:
            handle.write("{ not json")
        with self.assertRaises(json.JSONDecodeError):
            self.tracker.reload_data()
        self.assertFalse(self.tracker.is_current())

    def test_nor_does_one_that_fails_while_bringing_the_document_up_to_date(self):
        """Read, then half migrated: memory is neither the file nor what it should become."""
        self.new_tracker().add_task("P", "elsewhere")
        with mock.patch.object(self.tracker, '_migrate_data_structure',
                               side_effect=RuntimeError("half way")):
            with self.assertRaises(RuntimeError):
                self.tracker.reload_data()
        self.assertFalse(self.tracker.is_current())


class TestTwoRedrawsOfOneSessionAtOnce(_OnOneFile):
    """
    Streamlit starts the next redraw of a session as soon as there is a
    click, without waiting for the one under way - that one carries on until
    its next call into Streamlit. So the interface's one TimeTracker can be
    reloading in one thread while a write the other began is still going.

    Found in the review of this change: the reload could put the document
    from before the write in place just as the write noted the file as
    matching memory. Memory then lacked the write and was taken for current,
    and the next write saved it over the file - a lost update.
    """

    def test_a_reload_during_a_write_waits_for_it(self):
        real = self.tracker._replace_when_windows_lets_go
        seen = {}

        def reload_meanwhile(tmp_path):
            # The write has its document ready and is about to put it in place.
            thread = threading.Thread(target=self.tracker.reload_data)
            thread.start()
            thread.join(0.3)
            seen['waited'] = thread.is_alive()
            seen['thread'] = thread
            real(tmp_path)
        with mock.patch.object(self.tracker, '_replace_when_windows_lets_go', reload_meanwhile):
            self.tracker.add_task("P", "written")
        seen['thread'].join(5)
        self.assertTrue(seen['waited'], "the reload did not wait for the write")
        self.assertEqual(_names(self.tracker), ["written"])
        self.tracker.add_task("P", "next")
        self.assertEqual(_tasks_of(self.path), ["next", "written"])

    def _hold_in_another_thread(self, after_saving=False):
        """
        Another thread inside exclusive() until released - having saved
        once already, if asked, so that its document is noted as current
        while it is still at work on it.
        """
        held, release = threading.Event(), threading.Event()

        def writer():
            with self.tracker.exclusive(timeout=5):
                if after_saving:
                    self.tracker.add_task("P", "saved half way")
                held.set()
                release.wait(5)
        thread = threading.Thread(target=writer)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(release.set)
        self.assertTrue(held.wait(5))
        return release

    def test_a_today_sweep_waits_for_a_write_under_way(self):
        """
        Its look through memory too: the document is noted as current once
        the other thread has saved, but that thread is not done with it.
        """
        release = self._hold_in_another_thread(after_saving=True)
        sweep = threading.Thread(target=self.tracker.cleanup_overdue_today_tasks)
        sweep.start()
        sweep.join(0.3)
        waited = sweep.is_alive()
        release.set()
        sweep.join(5)
        self.assertTrue(waited, "the sweep looked while the other thread was at work")

    def test_a_reload_kept_waiting_too_long_says_so_and_keeps_what_it_has(self):
        """
        Rather than wait for ever behind a write that does not end - the
        interface catches this and draws what it had.
        """
        self._hold_in_another_thread()
        self.tracker.SAVE_LOCK_TIMEOUT = 0.2
        before = self.tracker.data
        with self.assertRaises(LockTimeout):
            self.tracker.reload_data()
        self.assertIs(self.tracker.data, before)

    def test_a_deadline_already_past_is_one_try_and_no_more(self):
        """
        Found in the second review: the lock of this object takes -1 as no
        limit at all, and anything else below zero as an error.
        """
        self._hold_in_another_thread()
        for timeout in (-1, -0.5, 0):
            started = time.monotonic()
            with self.assertRaises(LockTimeout):
                with self.tracker.exclusive(timeout=timeout):
                    pass
            self.assertLess(time.monotonic() - started, 1)

    def test_writes_and_reloads_from_two_threads_lose_nothing(self):
        """The same, left to chance: one thread writing, one redrawing."""
        done = threading.Event()
        errors = []

        def redraw():
            try:
                while not done.is_set():
                    self.tracker.reload_data()
                    self.tracker.cleanup_overdue_today_tasks()
            except BaseException as error:
                errors.append(error)
        thread = threading.Thread(target=redraw)
        thread.start()
        try:
            for i in range(60):
                self.tracker.add_task("P", "%02d" % i)
        finally:
            done.set()
            thread.join(10)
        self.assertEqual(errors, [])
        self.assertEqual(_tasks_of(self.path), ["%02d" % i for i in range(60)])
        self.tracker.reload_data()
        self.assertEqual(_names(self.tracker), _tasks_of(self.path))


class TestReadingLeavesTheDocumentAlone(_OnOneFile):
    """
    A change made outside exclusive() would now stay in memory unseen and be
    written out by whatever saved next, without the operation the other
    machines need to hear of it. So nothing that only reads may change it.
    """

    def test_the_reading_methods_change_nothing(self):
        today = date.today()
        yesterday = (today - timedelta(days=1)).isoformat()
        self.tracker.add_main_project("Q")
        self.tracker.add_task("P", "A", due_date=today.isoformat(), today=True,
                              recurring=True, start_date=yesterday)
        self.tracker.add_task("P", "B", due_date=yesterday)
        self.tracker.add_task("Q", "C")
        self.tracker.start_work("P", "A")
        self.tracker.stop_work()
        self.tracker.start_work("Q", "C")
        self.tracker.close_task("P", "B")
        before = json.dumps(self.tracker.data, sort_keys=True)

        calls = [
            lambda t: t.list_main_projects(),
            lambda t: t.list_tasks(),
            lambda t: t.list_tasks(status_filter='open'),
            lambda t: t.list_tasks(main_project_name="P", status_filter='closed'),
        ] + [
            (lambda f: lambda t: t.list_tasks(planning_filter=f))(f)
            for f in ('today', 'tomorrow', 'weekly', 'overdue', 'unplanned')
        ] + [
            lambda t: t.get_current_work(),
            lambda t: t.list_inactive_tasks(0),
            lambda t: t.list_inactive_main_projects(0),
            lambda t: t.list_completed_main_projects(),
            lambda t: t.generate_daily_report(today),
            lambda t: t.generate_detailed_daily_report(today),
            lambda t: t.generate_date_range_report(today - timedelta(days=7), today),
            lambda t: t.generate_task_report("P", "A"),
            lambda t: t.generate_main_project_report("P"),
            lambda t: t.cleanup_overdue_today_tasks(),
            lambda t: t.set_today_flag_for_due_tasks(),
        ]
        # The reports copy themselves to the clipboard. Not in a test run.
        with mock.patch.object(TimeTracker, '_copy_to_clipboard'):
            for index, call in enumerate(calls):
                with self.subTest(call=index):
                    call(self.tracker)
                    self.assertEqual(json.dumps(self.tracker.data, sort_keys=True), before)


class TestAMigrationOnReadingKeepsOtherWrites(_OnOneFile):
    """
    A read that migrates the document saves it - in __init__, which the
    servers run on every call, and in reload_data(). That save used to go
    without the file lock: a writer that saved between the read and it lost
    its change to the migrated document from before, without a word. Found
    in the second review of #640, older than it.

    Set off here the way it happens: a tombstone passing its retention. For
    the reader below that moment has come, for the writer's copy not yet.
    The reader is held between migrating and saving while the writer saves.
    """

    def setUp(self):
        super().setUp()
        self.tracker.add_task("P", "doomed")
        self.tracker.delete_task("P", "doomed")
        self.migrated = threading.Event()
        self.go_on = threading.Event()
        self.addCleanup(self.go_on.set)
        self.armed = False
        test = self

        class Expired(TimeTracker):
            TOMBSTONE_RETENTION_DAYS = -1

            def _migrate_data_structure(self):
                changed = super()._migrate_data_structure()
                if changed and test.armed and not test.migrated.is_set():
                    test.migrated.set()
                    test.go_on.wait(10)
                return changed

        self.Expired = Expired

    def held_after_migrating(self, read):
        """Runs `read` in a thread, and returns once it has migrated and is held."""
        self.armed = True
        errors = []

        def run():
            try:
                read()
            except BaseException as error:
                errors.append(error)

        thread = threading.Thread(target=run)
        thread.start()
        self.assertTrue(self.migrated.wait(10))
        return thread, errors

    def let_go(self, thread, errors):
        self.go_on.set()
        thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])

    def _deleted_in_file(self):
        with open(self.path, encoding='utf-8') as handle:
            return json.load(handle)["_deleted"]

    def test_a_write_while_a_new_tracker_migrates_is_kept(self):
        """What the MCP/REST/SOAP servers build on every call."""
        readers = []
        thread, errors = self.held_after_migrating(
            lambda: readers.append(self.Expired(file_path=self.path, op_outbox=NoOutbox())))
        self.new_tracker().add_task("P", "meanwhile")
        self.let_go(thread, errors)
        self.assertEqual(_tasks_of(self.path), ["meanwhile"])
        # And the migration did get saved, on top of the write.
        self.assertEqual(self._deleted_in_file(), [])
        self.assertEqual(_names(readers[0]), ["meanwhile"])
        self.assertTrue(readers[0].is_current())

    def test_a_write_while_a_reload_migrates_is_kept(self):
        """What the interface does on every redraw."""
        reader = self.Expired(file_path=self.path, op_outbox=NoOutbox())
        self.tracker.add_task("P", "doomed too")
        self.tracker.delete_task("P", "doomed too")
        thread, errors = self.held_after_migrating(reader.reload_data)
        self.new_tracker().add_task("P", "meanwhile")
        self.let_go(thread, errors)
        self.assertEqual(_tasks_of(self.path), ["meanwhile"])
        self.assertEqual(self._deleted_in_file(), [])
        self.assertEqual(_names(reader), ["meanwhile"])
        self.assertTrue(reader.is_current())

    def test_with_nothing_in_between_the_migration_is_saved_as_before(self):
        reader = self.Expired(file_path=self.path, op_outbox=NoOutbox())
        self.assertEqual(self._deleted_in_file(), [])
        self.assertTrue(reader.is_current())
        with self.reads(reader) as load:
            reader.reload_data()
        load.assert_not_called()

    def test_a_reader_does_not_wait_for_the_lock_to_save(self):
        """Left to the next read, or the next write - nothing is lost."""
        with tt.TimeTracker.locked(self.tracker._lock_path(), timeout=1):
            started = time.monotonic()
            reader = self.Expired(file_path=self.path, op_outbox=NoOutbox())
            self.assertLess(time.monotonic() - started, 1.0)
            self.assertEqual(len(self._deleted_in_file()), 1)
            # Migrated in memory, but not the file: not to be taken for it.
            self.assertEqual(reader.data["_deleted"], [])
            self.assertFalse(reader.is_current())
        reader.reload_data()
        self.assertEqual(self._deleted_in_file(), [])
        self.assertTrue(reader.is_current())

    def test_a_write_brings_the_file_up_to_date_too(self):
        with tt.TimeTracker.locked(self.tracker._lock_path(), timeout=1):
            reader = self.Expired(file_path=self.path, op_outbox=NoOutbox())
        reader.add_task("P", "later")
        self.assertEqual(self._deleted_in_file(), [])
        self.assertEqual(_tasks_of(self.path), ["later"])

    def test_a_new_document_is_still_written_on_first_use(self):
        path = os.path.join(self.tmp, 'fresh.json')
        TimeTracker(file_path=path, op_outbox=NoOutbox())
        self.assertTrue(os.path.exists(path))


class TestTwoWritersAtOnce(unittest.TestCase):
    """
    Two processes, each keeping one TimeTracker for all of its writes - the
    way the interface keeps one for a whole session - and each writing while
    the other does. Every write has to notice the other's.

    That they really did write at the same time is checked, not assumed. Two
    processes started together can still run one after the other - a slow
    start, a busy machine - and then nothing here would have been tested.
    Each writer counts the other's tasks after each of its own writes; seeing
    some but not all of them while it is still writing is proof they took
    turns. A run without that is repeated, a few times at most.
    """

    UPDATES = 30
    ATTEMPTS = 3

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _writer(self, path, start, name):
        script = textwrap.dedent("""
            import os, sys, time
            sys.path.insert(0, %r)
            from tt.TimeTracker import TimeTracker

            class NoOutbox:
                def append(self, op, **fields):
                    pass

            tracker = TimeTracker(file_path=%r, op_outbox=NoOutbox())
            print("ready", flush=True)
            while not os.path.exists(%r):
                time.sleep(0.001)
            interleaved = False
            for i in range(%d):
                tracker.reload_data()
                tracker.add_task("P", "%%s %%02d" %% (%r, i))
                others = sum(1 for task in tracker.data["projects"][0]["tasks"]
                             if not task["task_name"].startswith(%r + " "))
                interleaved = interleaved or (0 < others < %d and i < %d - 1)
                # A pause, as between two clicks. The lock is tried again
                # every 20 ms, so a writer in a tight loop keeps it to itself
                # and the two would take turns only by chance.
                time.sleep(0.025)
            print("done", int(interleaved), flush=True)
        """) % (REPO, path, start, self.UPDATES, name, name, self.UPDATES, self.UPDATES)
        return subprocess.Popen([sys.executable, '-c', script],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def _fail_with(self, name, child, what):
        child.kill()
        _, err = child.communicate()
        self.fail("the %s writer %s:\n%s" % (name, what, err.strip() or "(no output)"))

    def _run(self, attempt):
        """One round; True if the two writers' turns interleaved."""
        path = os.path.join(self.tmp, 'data-%d.json' % attempt)
        start = os.path.join(self.tmp, 'start-%d' % attempt)
        TimeTracker(file_path=path, op_outbox=NoOutbox()).add_main_project("P")
        children = {name: self._writer(path, start, name) for name in ("left", "right")}
        interleaved = False
        try:
            # Both have loaded the document and are waiting before either may
            # begin. Starting them by creating the file straight after
            # launching them, as this test first did, gave neither the chance.
            for name, child in children.items():
                if child.stdout.readline().strip() != "ready":
                    self._fail_with(name, child, "did not get ready")
            with open(start, 'w'):
                pass
            for name, child in children.items():
                try:
                    out, err = child.communicate(timeout=120)
                except subprocess.TimeoutExpired:
                    self._fail_with(name, child, "did not finish in time")
                report = out.split()
                if report[:1] != ["done"]:
                    self.fail("the %s writer did not finish:\n%s"
                              % (name, err.strip() or "(no output)"))
                interleaved = interleaved or report[1:] == ["1"]
        finally:
            for child in children.values():
                if child.poll() is None:
                    child.kill()
                    child.wait(10)
        expected = sorted("%s %02d" % (name, i)
                          for name in ("left", "right") for i in range(self.UPDATES))
        self.assertEqual(_tasks_of(path), expected)
        return interleaved

    def test_thirty_updates_each_and_none_is_lost(self):
        for attempt in range(self.ATTEMPTS):
            if self._run(attempt):
                return
        self.fail("in %d rounds the two writers never wrote at the same time, "
                  "so nothing was tested" % self.ATTEMPTS)

if __name__ == '__main__':
    unittest.main()
