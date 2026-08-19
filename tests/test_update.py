import unittest
import unittest.mock
import hashlib
import os
import sys
import tempfile
import threading
import time

# Add parent directory to path to import modules from root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import requests
import update


class TestUpdate(unittest.TestCase):
    """
    Unit tests for update.py's check_for_updates() and download_update().

    These cover both the pre-existing "no internet" behavior (a raised
    requests exception is caught and turned into a safe default return
    value) and today's fix: a network call that never returns at all
    (simulating a DNS black hole) must still make check_for_updates()/
    download_update() return within their configured deadline instead of
    hanging the whole process.
    """

    def setUp(self):
        """Runs before each test to make sure no leftover update.zip exists."""
        if os.path.exists(update.UPDATE_ZIP_FILE):
            os.remove(update.UPDATE_ZIP_FILE)

    def tearDown(self):
        """Runs after each test to clean up any update.zip created by a test."""
        if os.path.exists(update.UPDATE_ZIP_FILE):
            os.remove(update.UPDATE_ZIP_FILE)

    # --- check_for_updates() ---

    @unittest.mock.patch('update._get_github_repo_from_config')
    @unittest.mock.patch('update.requests.get')
    def test_check_for_updates_happy_path_new_version_available(self, mock_get, mock_get_repo):
        """A newer tag_name on GitHub should be reported as an available update."""
        mock_get_repo.return_value = "someuser/somerepo"
        mock_response = unittest.mock.MagicMock()
        mock_response.raise_for_status = unittest.mock.MagicMock()
        mock_response.json.return_value = {"tag_name": "v99.0", "zipball_url": "http://x/z"}
        mock_get.return_value = mock_response

        result = update.check_for_updates("1.0")

        self.assertEqual(result, (True, "99.0", "http://x/z"))

    @unittest.mock.patch('update._get_github_repo_from_config')
    @unittest.mock.patch('update.requests.get')
    def test_check_for_updates_no_update_available(self, mock_get, mock_get_repo):
        """A tag_name older than the current version should report no update."""
        mock_get_repo.return_value = "someuser/somerepo"
        mock_response = unittest.mock.MagicMock()
        mock_response.raise_for_status = unittest.mock.MagicMock()
        mock_response.json.return_value = {"tag_name": "v0.5", "zipball_url": "http://x/z"}
        mock_get.return_value = mock_response

        result = update.check_for_updates("1.0")

        self.assertEqual(result, (False, None, None))

    @unittest.mock.patch('update._get_github_repo_from_config')
    @unittest.mock.patch('update.requests.get')
    def test_check_for_updates_request_exception_returns_safely(self, mock_get, mock_get_repo):
        """
        Existing behavior (predates today's fix): a network error raised by
        requests.get must be caught and turned into the safe "no update"
        default, never propagated to the caller.
        """
        mock_get_repo.return_value = "someuser/somerepo"
        mock_get.side_effect = requests.exceptions.ConnectionError("no route to host")

        result = update.check_for_updates("1.0")

        self.assertEqual(result, (False, None, None))

    @unittest.mock.patch('update.UPDATE_CHECK_DEADLINE', 1)
    @unittest.mock.patch('update._get_github_repo_from_config')
    @unittest.mock.patch('update.requests.get')
    def test_check_for_updates_hung_network_call_returns_within_deadline(self, mock_get, mock_get_repo):
        """
        Regression test for today's fix: even if requests.get NEVER returns
        (simulating a true DNS black-hole, not just a slow response),
        check_for_updates() must still come back within UPDATE_CHECK_DEADLINE
        instead of hanging forever.
        """
        mock_get_repo.return_value = "someuser/somerepo"
        mock_get.side_effect = lambda *args, **kwargs: threading.Event().wait()

        start = time.monotonic()
        result = update.check_for_updates("1.0")
        elapsed = time.monotonic() - start

        self.assertEqual(result, (False, None, None))
        self.assertLess(elapsed, 5, "check_for_updates() did not return within its deadline")

    # --- download_update() ---

    @unittest.mock.patch('update.DOWNLOAD_DEADLINE', 1)
    @unittest.mock.patch('update.requests.get')
    def test_download_update_hung_network_call_returns_within_deadline(self, mock_get):
        """
        Regression test for today's fix: even if requests.get NEVER returns,
        download_update() must still come back within DOWNLOAD_DEADLINE
        instead of hanging forever.
        """
        mock_get.side_effect = lambda *args, **kwargs: threading.Event().wait()

        start = time.monotonic()
        result = update.download_update("http://x/z")
        elapsed = time.monotonic() - start

        self.assertFalse(result)
        self.assertLess(elapsed, 5, "download_update() did not return within its deadline")

    @unittest.mock.patch('update.requests.get')
    def test_download_update_request_exception_returns_safely(self, mock_get):
        """
        Existing behavior (predates today's fix): a network error raised by
        requests.get must be caught and turned into a safe False return,
        never propagated to the caller.
        """
        mock_get.side_effect = requests.exceptions.ConnectionError("no route to host")

        result = update.download_update("http://x/z")

        self.assertFalse(result)

    # --- should_check_for_updates() ---

    def test_should_check_for_updates_first_time_for_a_menu(self):
        """No prior check recorded at all - a fresh session_state - must check."""
        self.assertTrue(update.should_check_for_updates({}, "today_view"))

    def test_should_check_for_updates_same_menu_as_last_check_skips(self):
        """
        Regression test: rerunning the *same* view (a keystroke, a periodic
        auto-refresh tick, ...) must NOT re-trigger a check - only an actual
        view change should.
        """
        session_state = {"_update_checked_for_menu": "today_view"}
        self.assertFalse(update.should_check_for_updates(session_state, "today_view"))

    def test_should_check_for_updates_different_menu_triggers_recheck(self):
        """Navigating to a different view than the one last checked must check again."""
        session_state = {"_update_checked_for_menu": "today_view"}
        self.assertTrue(update.should_check_for_updates(session_state, "reporting"))

    def test_should_check_for_updates_returning_to_a_previous_menu_rechecks(self):
        """
        Only the single most-recently-checked menu is remembered (not a set
        of every menu ever visited this session) - navigating back to a view
        checked earlier, but not immediately before this one, must check
        again too. This matches "check on every view change" literally,
        including revisits, while still skipping repeated reruns of the
        view you're currently on.
        """
        session_state = {"_update_checked_for_menu": "reporting"}
        self.assertTrue(update.should_check_for_updates(session_state, "today_view"))


def _fake_github(exe_bytes=b"", checksum=None, checksum_status=200):
    """
    Stands in for requests.get across both calls a frozen update makes: the
    small one for the published checksum and the streamed one for the
    executable itself. Dispatches on the URL, so a test can hand back a
    mismatching checksum or a 404 without caring about call order.
    """
    def fake_get(url, *unused_args, **unused_kwargs):
        response = unittest.mock.MagicMock()
        if url.endswith(update.CHECKSUM_SUFFIX):
            response.status_code = checksum_status
            response.text = "%s  TimeControl.exe\n" % checksum if checksum else ""
            return response
        response.status_code = 200
        response.raise_for_status = unittest.mock.MagicMock()
        response.iter_content = lambda chunk_size=1: [exe_bytes]
        return response
    return fake_get


class TestFrozenAssetSelection(unittest.TestCase):
    """
    A frozen build has to be offered the published .exe, not the source zip
    that a checkout would unpack. Everything else about check_for_updates()
    stays as it was - the tests above cover the non-frozen path and must keep
    passing unchanged.
    """

    RELEASE = {
        "tag_name": "v99.0",
        "zipball_url": "http://x/source.zip",
        "assets": [
            {"name": "SomethingElse.txt",
             "browser_download_url": "http://x/other.txt"},
            {"name": "TimeControl.exe",
             "browser_download_url": "http://x/TimeControl.exe"},
        ],
    }

    def _release_response(self, payload):
        response = unittest.mock.MagicMock()
        response.raise_for_status = unittest.mock.MagicMock()
        response.json.return_value = payload
        return response

    @unittest.mock.patch('update.is_frozen', return_value=True)
    @unittest.mock.patch('update._get_github_repo_from_config', return_value="u/r")
    @unittest.mock.patch('update.requests.get')
    def test_frozen_build_is_offered_the_executable(self, mock_get, *unused):
        mock_get.return_value = self._release_response(self.RELEASE)

        self.assertEqual(update.check_for_updates("1.0"),
                         (True, "99.0", "http://x/TimeControl.exe"))

    @unittest.mock.patch('update.is_frozen', return_value=False)
    @unittest.mock.patch('update._get_github_repo_from_config', return_value="u/r")
    @unittest.mock.patch('update.requests.get')
    def test_source_install_still_gets_the_zip(self, mock_get, *unused):
        """The asset is there, but a checkout must not be handed an .exe."""
        mock_get.return_value = self._release_response(self.RELEASE)

        self.assertEqual(update.check_for_updates("1.0"),
                         (True, "99.0", "http://x/source.zip"))

    @unittest.mock.patch('update.is_frozen', return_value=True)
    @unittest.mock.patch('update._get_github_repo_from_config', return_value="u/r")
    @unittest.mock.patch('update.requests.get')
    def test_release_without_the_asset_offers_nothing(self, mock_get, *unused):
        """
        Releases made before the Windows build existed carry no .exe, and one
        whose build failed carries none either. Offering the source zip to a
        frozen build instead would download something it cannot install.
        """
        payload = dict(self.RELEASE, assets=[
            {"name": "notes.txt", "browser_download_url": "http://x/notes.txt"}])
        mock_get.return_value = self._release_response(payload)

        self.assertEqual(update.check_for_updates("1.0"), (False, None, None))

    @unittest.mock.patch('update.is_frozen', return_value=True)
    @unittest.mock.patch('update._get_github_repo_from_config', return_value="u/r")
    @unittest.mock.patch('update.requests.get')
    def test_release_with_no_assets_key_at_all(self, mock_get, *unused):
        payload = {"tag_name": "v99.0", "zipball_url": "http://x/source.zip"}
        mock_get.return_value = self._release_response(payload)

        self.assertEqual(update.check_for_updates("1.0"), (False, None, None))


class TestDownloadingTheExecutable(unittest.TestCase):
    """
    Whatever this download produces is about to take the place of the user's
    working program, so the checks around it are the point of the exercise -
    not the download itself.
    """

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.here = self._dir.name
        self.target = os.path.join(self.here, "TimeControl.exe")
        # A stand-in for a real build: the two bytes every Windows executable
        # starts with, padded past the size floor.
        self.build = b"MZ" + b"\0" * (update.MIN_EXE_BYTES + 10)
        self.digest = hashlib.sha256(self.build).hexdigest()
        self.addCleanup(self._dir.cleanup)

    def _path(self, name):
        return os.path.join(self.here, name)

    def _exists(self, name):
        return os.path.exists(self._path(name))

    @unittest.mock.patch('update.requests.get')
    def test_verified_download_is_left_ready_for_the_next_start(self, mock_get):
        mock_get.side_effect = _fake_github(self.build, checksum=self.digest)

        self.assertTrue(update.download_exe_update("http://x/TimeControl.exe", self.target))

        self.assertTrue(self._exists(update.PENDING_EXE))
        with open(self._path(update.PENDING_EXE), 'rb') as handle:
            self.assertEqual(handle.read(), self.build)
        self.assertFalse(self._exists(update.PARTIAL_EXE),
                         "the in-flight name must not survive a finished download")
        self.assertTrue(update.pending_exe_update(self.target))

    @unittest.mock.patch('update.requests.get')
    def test_a_checksum_that_does_not_match_is_refused(self, mock_get):
        mock_get.side_effect = _fake_github(self.build, checksum="0" * 64)

        self.assertFalse(update.download_exe_update("http://x/TimeControl.exe", self.target))

        self.assertFalse(self._exists(update.PENDING_EXE))
        self.assertFalse(self._exists(update.PARTIAL_EXE))

    @unittest.mock.patch('update.requests.get')
    def test_a_truncated_download_is_refused(self, mock_get):
        """
        The give-away for a transfer that stopped early, and the case a
        checksum alone would not catch on a release that publishes none.
        """
        short = b"MZ" + b"\0" * 100
        mock_get.side_effect = _fake_github(short, checksum=hashlib.sha256(short).hexdigest())

        self.assertFalse(update.download_exe_update("http://x/TimeControl.exe", self.target))

        self.assertFalse(self._exists(update.PENDING_EXE))
        self.assertFalse(self._exists(update.PARTIAL_EXE))

    @unittest.mock.patch('update.requests.get')
    def test_something_that_is_not_a_program_is_refused(self, mock_get):
        """
        An error page or a redirect notice can arrive with a 200 and the right
        sort of size. It just does not start the way an executable does.
        """
        page = b"<!DOCTYPE html><html>Not Found" + b" " * update.MIN_EXE_BYTES
        mock_get.side_effect = _fake_github(page, checksum=hashlib.sha256(page).hexdigest())

        self.assertFalse(update.download_exe_update("http://x/TimeControl.exe", self.target))

        self.assertFalse(self._exists(update.PENDING_EXE))

    @unittest.mock.patch('update.requests.get')
    def test_a_release_without_a_checksum_still_updates(self, mock_get):
        """
        Releases predating the published checksum have none. Refusing those
        would strand anyone on an older build; the size and format checks
        still run.
        """
        mock_get.side_effect = _fake_github(self.build, checksum_status=404)

        self.assertTrue(update.download_exe_update("http://x/TimeControl.exe", self.target))
        self.assertTrue(self._exists(update.PENDING_EXE))

    @unittest.mock.patch('update.requests.get')
    def test_a_failed_transfer_leaves_nothing_behind(self, mock_get):
        def fake_get(url, *unused_args, **unused_kwargs):
            if url.endswith(update.CHECKSUM_SUFFIX):
                response = unittest.mock.MagicMock()
                response.status_code = 404
                return response
            raise requests.exceptions.ConnectionError("no route to host")
        mock_get.side_effect = fake_get

        self.assertFalse(update.download_exe_update("http://x/TimeControl.exe", self.target))

        self.assertFalse(self._exists(update.PENDING_EXE))
        self.assertFalse(self._exists(update.PARTIAL_EXE))

    @unittest.mock.patch('update.requests.get')
    def test_a_transfer_that_dies_midway_leaves_nothing_behind(self, mock_get):
        """
        The partial file is written before anything goes wrong, so this is the
        case where cleanup actually has work to do.
        """
        def dying_chunks(chunk_size=1):
            yield b"MZ" + b"\0" * 4096
            raise requests.exceptions.ChunkedEncodingError("connection reset")

        def fake_get(url, *unused_args, **unused_kwargs):
            response = unittest.mock.MagicMock()
            if url.endswith(update.CHECKSUM_SUFFIX):
                response.status_code = 404
                return response
            response.raise_for_status = unittest.mock.MagicMock()
            response.iter_content = dying_chunks
            return response
        mock_get.side_effect = fake_get

        self.assertFalse(update.download_exe_update("http://x/TimeControl.exe", self.target))

        self.assertFalse(self._exists(update.PARTIAL_EXE),
                         "a half-written download must not be left lying around")
        self.assertFalse(self._exists(update.PENDING_EXE))

    @unittest.mock.patch('update.requests.get')
    def test_the_pending_name_appears_only_once_the_file_has_been_checked(self, mock_get):
        """
        The reason the download carries a name of its own while in flight.
        Cleanup covers the errors we can catch, but not a machine losing power
        or a process being killed - and whatever is sitting under the pending
        name at the next start gets swapped in without further questions.
        """
        midway = []

        def watched_chunks(chunk_size=1):
            yield b"MZ" + b"\0" * 4096
            midway.append(sorted(os.listdir(self.here)))
            yield b"\0" * update.MIN_EXE_BYTES

        def fake_get(url, *unused_args, **unused_kwargs):
            response = unittest.mock.MagicMock()
            if url.endswith(update.CHECKSUM_SUFFIX):
                response.status_code = 404
                return response
            response.raise_for_status = unittest.mock.MagicMock()
            response.iter_content = watched_chunks
            return response
        mock_get.side_effect = fake_get

        self.assertTrue(update.download_exe_update("http://x/TimeControl.exe", self.target))

        self.assertEqual(len(midway), 1, "the watcher never ran")
        self.assertIn(update.PARTIAL_EXE, midway[0])
        self.assertNotIn(update.PENDING_EXE, midway[0],
                         "an unchecked download must never carry the name the "
                         "next start swaps in")
        self.assertTrue(self._exists(update.PENDING_EXE))


class TestSwappingTheExecutable(unittest.TestCase):
    """
    The swap itself: rename the running image aside, move the download into
    its place. Windows refuses the overwrite this avoids, and permits the
    rename it relies on - established on a real runner, see
    .github/workflows/probe-windows-selfupdate.yaml.
    """

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.here = self._dir.name
        self.target = os.path.join(self.here, "TimeControl.exe")
        self._write(self.target, b"the running build")
        self.addCleanup(self._dir.cleanup)

    def _write(self, path, content):
        with open(path, 'wb') as handle:
            handle.write(content)

    def _path(self, name):
        return os.path.join(self.here, name)

    def _read(self, name):
        with open(self._path(name), 'rb') as handle:
            return handle.read()

    def _stage(self, content=b"the new build"):
        self._write(self._path(update.PENDING_EXE), content)

    def test_nothing_waiting_means_nothing_happens(self):
        """
        This runs at every single start, so "nothing to do" has to be free of
        side effects. Without the check up front the executable would be
        renamed aside and back again on each launch, opening the one window
        in which a crash leaves the user with no program at all - for an
        update that was never even downloaded.
        """
        with unittest.mock.patch('update.os.replace') as replace:
            self.assertFalse(update.install_exe_update(self.target))
            replace.assert_not_called()

        self.assertEqual(self._read("TimeControl.exe"), b"the running build")

    def test_the_download_takes_the_place_of_the_running_build(self):
        self._stage()

        self.assertTrue(update.install_exe_update(self.target))

        self.assertEqual(self._read("TimeControl.exe"), b"the new build")
        self.assertEqual(self._read(update.PREVIOUS_EXE), b"the running build")
        self.assertFalse(os.path.exists(self._path(update.PENDING_EXE)))

    def test_only_one_step_back_is_kept(self):
        """
        Keeping a chain of old builds would fill the user's folder with
        forty-megabyte files nobody asked for.
        """
        self._write(self._path(update.PREVIOUS_EXE), b"a much older build")
        self._stage()

        self.assertTrue(update.install_exe_update(self.target))

        self.assertEqual(self._read(update.PREVIOUS_EXE), b"the running build")

    def test_a_failed_swap_leaves_the_old_version_in_place(self):
        """
        Between the two moves nothing carries the application's name. If the
        second fails the first has to be undone - a failed update must leave
        the user with their old program, not with no program.
        """
        self._stage()
        calls = []
        real_replace = os.replace

        def replace_that_fails_the_second_time(src, dst):
            calls.append((src, dst))
            if len(calls) == 2:
                raise OSError("the volume went away")
            return real_replace(src, dst)

        with unittest.mock.patch('update.os.replace',
                                 side_effect=replace_that_fails_the_second_time):
            self.assertFalse(update.install_exe_update(self.target))

        self.assertTrue(os.path.exists(self.target),
                        "the application must still answer to its own name")
        self.assertEqual(self._read("TimeControl.exe"), b"the running build")

    def test_rolling_back_restores_the_previous_build(self):
        self._write(self._path(update.PREVIOUS_EXE), b"the build before")

        self.assertTrue(update.restore_previous_exe(self.target))

        self.assertEqual(self._read("TimeControl.exe"), b"the build before")
        self.assertEqual(self._read(update.REJECTED_EXE), b"the running build")
        self.assertFalse(os.path.exists(self._path(update.PREVIOUS_EXE)))

    def test_rolling_back_with_nothing_to_roll_back_to(self):
        """
        Nothing to restore has to mean nothing moves - not a shuffle that
        happens to end up where it started by way of an error path, which
        would rename the running executable for no reason and report the
        miss as a filesystem error rather than as the plain absence it is.
        """
        with unittest.mock.patch('update.os.replace') as replace:
            self.assertFalse(update.restore_previous_exe(self.target))
            replace.assert_not_called()

        self.assertEqual(self._read("TimeControl.exe"), b"the running build")

    def test_leftovers_are_cleared_but_the_rollback_is_kept(self):
        """
        The rolled-back build is kept at the time because it is still running,
        and a download can be cut off mid-transfer; by the next start neither
        is any use. The copy the last update renamed aside is a different
        matter - that one is the rollback, and clearing it here would quietly
        remove the user's way back.
        """
        self._write(self._path(update.REJECTED_EXE), b"a build we rejected")
        self._write(self._path(update.PARTIAL_EXE), b"half a download")
        self._write(self._path(update.PREVIOUS_EXE), b"the version before")

        update.clear_update_leftovers(self.target)

        self.assertFalse(os.path.exists(self._path(update.REJECTED_EXE)))
        self.assertFalse(os.path.exists(self._path(update.PARTIAL_EXE)))
        self.assertEqual(self._read(update.PREVIOUS_EXE), b"the version before",
                         "the rollback must survive the tidying up")

    def test_discarding_what_is_not_there_is_not_an_error(self):
        update.clear_update_leftovers(self.target)


class TestRelaunchingAfterTheSwap(unittest.TestCase):
    """
    The last step, and the one that took three rounds on a Windows runner to
    get right. A onefile build unpacks itself into a temporary directory and
    names it to the second stage through the environment; a frozen process
    starting its own image hands those variables on, so the new process skips
    unpacking and reads from a directory belonging to the process it is
    replacing - out of an archive the swap has just renamed. It dies at once.

    Measured on the runner: plain, detached, and waiting around before exiting
    all failed. Stripping the variables was the only thing that worked, with
    or without detaching. See probe-windows-selfupdate.yaml.
    """

    def test_pyinstaller_handover_variables_are_stripped(self):
        environment = {"_MEIPASS2": "/tmp/_MEI123",
                       "_PYI_ARCHIVE_FILE": "C:\\TimeControl.exe",
                       "_PYI_APPLICATION_HOME_DIR": "/tmp/_MEI123",
                       "PATH": "/usr/bin",
                       "HOME": "/home/somebody"}

        with unittest.mock.patch.dict('update.os.environ', environment, clear=True):
            clean = update._clean_environment()

        self.assertEqual(clean, {"PATH": "/usr/bin", "HOME": "/home/somebody"})

    def test_the_replacement_is_started_without_them(self):
        environment = {"_MEIPASS2": "/tmp/_MEI123", "PATH": "/usr/bin"}

        with unittest.mock.patch.dict('update.os.environ', environment, clear=True):
            with unittest.mock.patch('update.subprocess.Popen') as popen:
                self.assertTrue(update.relaunch_frozen("/apps/TimeControl.exe"))

        popen.assert_called_once()
        argv, keywords = popen.call_args
        self.assertEqual(argv[0], ["/apps/TimeControl.exe"])
        self.assertNotIn("_MEIPASS2", keywords["env"],
                         "the new process must not be told it is already unpacked")
        self.assertEqual(keywords["env"]["PATH"], "/usr/bin",
                         "the rest of the environment has to survive")

    def test_a_relaunch_that_cannot_start_says_so(self):
        """
        The caller exits on True, so a quiet failure here would close the
        application with nothing to take its place.
        """
        with unittest.mock.patch('update.subprocess.Popen',
                                 side_effect=OSError("no such file")):
            self.assertFalse(update.relaunch_frozen("/apps/TimeControl.exe"))


if __name__ == "__main__":
    unittest.main()
