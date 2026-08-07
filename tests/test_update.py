import unittest
import unittest.mock
import os
import sys
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


if __name__ == "__main__":
    unittest.main()
