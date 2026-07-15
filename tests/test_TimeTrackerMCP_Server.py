import unittest
from unittest.mock import patch
import sys
import os

# Add parent directory to path to import modules from root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


class TestTimeTrackerMCP_Server(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        """
        Prepares the environment for testing the MCP server.
        We patch TimeTracker to avoid file I/O during import and testing.
        """
        cls.tt_patcher = patch('tt.TimeTracker.TimeTracker')
        cls.MockTimeTrackerClass = cls.tt_patcher.start()

        try:
            import TimeTrackerMCP_Server
            cls.mcp_server = TimeTrackerMCP_Server
        except ImportError:
            cls.tt_patcher.stop()
            raise unittest.SkipTest("Could not import TimeTrackerMCP_Server (is 'mcp' installed?).")
        except SystemExit:
            cls.tt_patcher.stop()
            raise unittest.SkipTest("mcp not installed or import error in TimeTrackerMCP_Server")

    @classmethod
    def tearDownClass(cls):
        cls.tt_patcher.stop()

    def setUp(self):
        self.MockTimeTrackerClass.reset_mock()
        # get_tracker() calls TimeTracker() fresh for every tool call; since
        # the class itself is mocked, every such call returns this same
        # mock instance, so tests can set expectations on it directly.
        self.mock_tracker = self.MockTimeTrackerClass.return_value

    # --- add_main_project ---

    def test_add_main_project_creates_new(self):
        self.mock_tracker.list_main_projects.return_value = []
        result = self.mcp_server.add_main_project("Acme")
        self.mock_tracker.add_main_project.assert_called_once_with("Acme")
        self.assertIn("created", result)

    def test_add_main_project_already_exists(self):
        self.mock_tracker.list_main_projects.return_value = [{"main_project_name": "Acme", "status": "open"}]
        result = self.mcp_server.add_main_project("Acme")
        self.mock_tracker.add_main_project.assert_not_called()
        self.assertIn("already exists", result)

    # --- add_task ---

    def test_add_task_creates_new(self):
        self.mock_tracker.list_main_projects.return_value = [{"main_project_name": "Acme", "status": "open"}]
        self.mock_tracker.list_tasks.return_value = []
        result = self.mcp_server.add_task("Acme", "Write docs")
        self.mock_tracker.add_task.assert_called_once_with(
            "Acme", "Write docs", due_date=None, today=False, note="",
            recurring=False, frequency="daily", userdefined_days=1,
        )
        self.assertIn("created", result)

    def test_add_task_missing_project(self):
        self.mock_tracker.list_main_projects.return_value = []
        result = self.mcp_server.add_task("Nonexistent", "Write docs")
        self.mock_tracker.add_task.assert_not_called()
        self.assertIn("not found", result)

    def test_add_task_already_exists(self):
        self.mock_tracker.list_main_projects.return_value = [{"main_project_name": "Acme", "status": "open"}]
        self.mock_tracker.list_tasks.return_value = [{"task_name": "Write docs"}]
        result = self.mcp_server.add_task("Acme", "Write docs")
        self.mock_tracker.add_task.assert_not_called()
        self.assertIn("already exists", result)

    # --- start_work / stop_work ---

    def test_start_work_success(self):
        self.mock_tracker.start_work.return_value = True
        result = self.mcp_server.start_work("Acme", "Write docs")
        self.mock_tracker.start_work.assert_called_once_with("Acme", "Write docs")
        self.assertIn("Started working", result)

    def test_start_work_failure(self):
        self.mock_tracker.start_work.return_value = False
        result = self.mcp_server.start_work("Acme", "Nope")
        self.assertIn("Error", result)

    def test_stop_work_success(self):
        self.mock_tracker.stop_work.return_value = True
        result = self.mcp_server.stop_work()
        self.assertIn("Stopped", result)

    def test_stop_work_nothing_active(self):
        self.mock_tracker.stop_work.return_value = False
        result = self.mcp_server.stop_work()
        self.assertIn("No time tracking session was active", result)

    # --- read-only lookups ---

    def test_list_main_projects(self):
        expected = [{"main_project_name": "Acme", "status": "open"}]
        self.mock_tracker.list_main_projects.return_value = expected
        result = self.mcp_server.list_main_projects(status_filter="open")
        self.mock_tracker.list_main_projects.assert_called_once_with(status_filter="open")
        self.assertEqual(result, expected)

    def test_list_tasks(self):
        expected = [{"task_name": "Write docs", "main_project_name": "Acme"}]
        self.mock_tracker.list_tasks.return_value = expected
        result = self.mcp_server.list_tasks(main_project_name="Acme", status_filter="open")
        self.mock_tracker.list_tasks.assert_called_once_with(main_project_name="Acme", status_filter="open")
        self.assertEqual(result, expected)

    def test_get_current_work_active(self):
        expected = {"main_project_name": "Acme", "task_name": "Write docs", "start_time": "2026-01-01T10:00:00"}
        self.mock_tracker.get_current_work.return_value = expected
        result = self.mcp_server.get_current_work()
        self.assertEqual(result, expected)

    def test_get_current_work_none(self):
        self.mock_tracker.get_current_work.return_value = None
        result = self.mcp_server.get_current_work()
        self.assertIsNone(result)

    # --- main project lifecycle ---

    def test_rename_main_project_success(self):
        self.mock_tracker.rename_main_project.return_value = True
        result = self.mcp_server.rename_main_project("Acme", "Acme Inc")
        self.mock_tracker.rename_main_project.assert_called_once_with("Acme", "Acme Inc")
        self.assertIn("renamed", result)

    def test_rename_main_project_failure(self):
        self.mock_tracker.rename_main_project.return_value = False
        result = self.mcp_server.rename_main_project("Acme", "Acme Inc")
        self.assertIn("Error", result)

    def test_close_main_project_success(self):
        self.mock_tracker.close_main_project.return_value = True
        result = self.mcp_server.close_main_project("Acme")
        self.assertIn("closed", result)

    def test_close_main_project_not_found(self):
        self.mock_tracker.close_main_project.return_value = False
        result = self.mcp_server.close_main_project("Acme")
        self.assertIn("Error", result)

    def test_reopen_main_project_success(self):
        self.mock_tracker.reopen_main_project.return_value = True
        result = self.mcp_server.reopen_main_project("Acme")
        self.assertIn("reopened", result)

    def test_delete_main_project_success(self):
        self.mock_tracker.delete_main_project.return_value = True
        result = self.mcp_server.delete_main_project("Acme")
        self.assertIn("deleted", result)

    def test_delete_main_project_not_found(self):
        self.mock_tracker.delete_main_project.return_value = False
        result = self.mcp_server.delete_main_project("Acme")
        self.assertIn("Error", result)

    def test_demote_main_project_relays_message(self):
        self.mock_tracker.demote_main_project.return_value = (True, "Main project 'Acme' was demoted to a sub-project under 'Globex'.")
        result = self.mcp_server.demote_main_project("Acme", "Globex")
        self.mock_tracker.demote_main_project.assert_called_once_with("Acme", "Globex")
        self.assertIn("demoted", result)

    def test_list_completed_main_projects(self):
        self.mock_tracker.list_completed_main_projects.return_value = ["Old Project"]
        result = self.mcp_server.list_completed_main_projects()
        self.assertEqual(result, ["Old Project"])

    def test_list_inactive_main_projects(self):
        expected = [{"main_project": "Acme", "last_activity": "2026-01-01 00:00:00"}]
        self.mock_tracker.list_inactive_main_projects.return_value = expected
        result = self.mcp_server.list_inactive_main_projects(4)
        self.mock_tracker.list_inactive_main_projects.assert_called_once_with(4)
        self.assertEqual(result, expected)

    # --- task lifecycle ---

    def test_rename_task_success(self):
        self.mock_tracker.rename_task.return_value = True
        result = self.mcp_server.rename_task("Acme", "Old", "New")
        self.assertIn("renamed", result)

    def test_rename_task_failure(self):
        self.mock_tracker.rename_task.return_value = False
        result = self.mcp_server.rename_task("Acme", "Old", "New")
        self.assertIn("Error", result)

    def test_close_task_success(self):
        self.mock_tracker.close_task.return_value = True
        result = self.mcp_server.close_task("Acme", "Write docs")
        self.assertIn("closed", result)

    def test_reopen_task_success(self):
        self.mock_tracker.reopen_task.return_value = True
        result = self.mcp_server.reopen_task("Acme", "Write docs")
        self.assertIn("reopened", result)

    def test_delete_task_success(self):
        self.mock_tracker.delete_task.return_value = True
        result = self.mcp_server.delete_task("Acme", "Write docs")
        self.assertIn("deleted", result)

    def test_delete_task_not_found(self):
        self.mock_tracker.delete_task.return_value = False
        result = self.mcp_server.delete_task("Acme", "Write docs")
        self.assertIn("Error", result)

    def test_delete_all_closed_tasks(self):
        self.mock_tracker.delete_all_closed_tasks.return_value = 3
        result = self.mcp_server.delete_all_closed_tasks()
        self.assertIn("3", result)

    def test_move_task_relays_message(self):
        self.mock_tracker.move_task.return_value = (True, "Task 'Write docs' moved successfully.")
        result = self.mcp_server.move_task("Acme", "Write docs", "Globex")
        self.mock_tracker.move_task.assert_called_once_with("Acme", "Write docs", "Globex")
        self.assertIn("moved", result)

    def test_promote_task_to_project_relays_message(self):
        self.mock_tracker.promote_task_to_project.return_value = (True, "Task 'Write docs' was promoted to a new main project.")
        result = self.mcp_server.promote_task_to_project("Acme", "Write docs")
        self.assertIn("promoted", result)

    def test_list_inactive_tasks(self):
        expected = [{"main_project": "Acme", "task_name": "Write docs", "last_activity": "2026-01-01 00:00:00"}]
        self.mock_tracker.list_inactive_tasks.return_value = expected
        result = self.mcp_server.list_inactive_tasks(4)
        self.mock_tracker.list_inactive_tasks.assert_called_once_with(4)
        self.assertEqual(result, expected)

    def test_cleanup_overdue_today_tasks_changed(self):
        self.mock_tracker.cleanup_overdue_today_tasks.return_value = True
        result = self.mcp_server.cleanup_overdue_today_tasks()
        self.assertIn("Removed", result)

    def test_cleanup_overdue_today_tasks_nothing(self):
        self.mock_tracker.cleanup_overdue_today_tasks.return_value = False
        result = self.mcp_server.cleanup_overdue_today_tasks()
        self.assertIn("Nothing", result)

    def test_set_today_flag_for_due_tasks_changed(self):
        self.mock_tracker.set_today_flag_for_due_tasks.return_value = True
        result = self.mcp_server.set_today_flag_for_due_tasks()
        self.assertIn("Marked", result)

    # --- update_task (and its due-date preservation safeguard) ---

    def test_update_task_not_found(self):
        self.mock_tracker.list_tasks.return_value = []
        result = self.mcp_server.update_task("Acme", "Write docs", note="Updated note")
        self.mock_tracker.update_task.assert_not_called()
        self.assertIn("not found", result)

    def test_update_task_preserves_due_date_when_unspecified(self):
        """
        Regression-style test for the due-date footgun: TimeTracker.update_task
        always overwrites due_date with whatever is passed (defaulting to
        None), so omitting it here must resolve to the task's *current*
        due date instead of silently clearing it.
        """
        self.mock_tracker.list_tasks.return_value = [
            {"id": 1, "task_name": "Write docs", "due_date": "2026-02-01"}
        ]
        self.mock_tracker.update_task.return_value = True

        result = self.mcp_server.update_task("Acme", "Write docs", note="Updated note")

        self.mock_tracker.update_task.assert_called_once_with(
            "Acme", "Write docs",
            new_task_name=None, due_date="2026-02-01", today=None,
            note="Updated note", status=None, recurring=None,
            frequency=None, userdefined_days=None, task_id=1,
        )
        self.assertIn("updated", result)

    def test_update_task_clear_due_date(self):
        self.mock_tracker.list_tasks.return_value = [
            {"id": 1, "task_name": "Write docs", "due_date": "2026-02-01"}
        ]
        self.mock_tracker.update_task.return_value = True

        self.mcp_server.update_task("Acme", "Write docs", clear_due_date=True)

        _args, kwargs = self.mock_tracker.update_task.call_args
        self.assertIsNone(kwargs["due_date"])

    def test_update_task_explicit_due_date_overrides_current(self):
        self.mock_tracker.list_tasks.return_value = [
            {"id": 1, "task_name": "Write docs", "due_date": "2026-02-01"}
        ]
        self.mock_tracker.update_task.return_value = True

        self.mcp_server.update_task("Acme", "Write docs", due_date="2026-03-15")

        _args, kwargs = self.mock_tracker.update_task.call_args
        self.assertEqual(kwargs["due_date"], "2026-03-15")

    def test_mark_task_done_sets_status(self):
        self.mock_tracker.list_tasks.return_value = [
            {"id": 1, "task_name": "Write docs", "due_date": None}
        ]
        self.mock_tracker.update_task.return_value = True

        self.mcp_server.mark_task_done("Acme", "Write docs")

        _args, kwargs = self.mock_tracker.update_task.call_args
        self.assertEqual(kwargs["status"], "done")

    # --- email import ---

    def test_fetch_emails_to_tasks_created(self):
        self.mock_tracker.fetch_emails_to_tasks.return_value = (2, None)
        result = self.mcp_server.fetch_emails_to_tasks()
        self.assertIn("2", result)

    def test_fetch_emails_to_tasks_none_found(self):
        self.mock_tracker.fetch_emails_to_tasks.return_value = (0, None)
        result = self.mcp_server.fetch_emails_to_tasks()
        self.assertIn("No new emails", result)

    def test_fetch_emails_to_tasks_error(self):
        self.mock_tracker.fetch_emails_to_tasks.return_value = (0, "Email import is not enabled.")
        result = self.mcp_server.fetch_emails_to_tasks()
        self.assertIn("Error", result)

    # --- reporting ---

    def test_generate_daily_report_today(self):
        self.mock_tracker.generate_daily_report.return_value = "# Daily Time Report"
        result = self.mcp_server.generate_daily_report()
        self.mock_tracker.generate_daily_report.assert_called_once_with(None)
        self.assertEqual(result, "# Daily Time Report")

    def test_generate_daily_report_specific_date(self):
        import datetime as dt
        self.mock_tracker.generate_daily_report.return_value = "# Daily Time Report"
        self.mcp_server.generate_daily_report("2026-02-01")
        self.mock_tracker.generate_daily_report.assert_called_once_with(dt.date(2026, 2, 1))

    def test_generate_daily_report_invalid_date(self):
        result = self.mcp_server.generate_daily_report("not-a-date")
        self.mock_tracker.generate_daily_report.assert_not_called()
        self.assertIn("Error", result)

    def test_generate_detailed_daily_report(self):
        self.mock_tracker.generate_detailed_daily_report.return_value = "# Detailed Daily Report"
        result = self.mcp_server.generate_detailed_daily_report()
        self.assertEqual(result, "# Detailed Daily Report")

    def test_generate_date_range_report(self):
        import datetime as dt
        self.mock_tracker.generate_date_range_report.return_value = "# Time Report"
        result = self.mcp_server.generate_date_range_report("2026-01-01", "2026-01-31")
        self.mock_tracker.generate_date_range_report.assert_called_once_with(dt.date(2026, 1, 1), dt.date(2026, 1, 31))
        self.assertEqual(result, "# Time Report")

    def test_generate_date_range_report_invalid_start(self):
        result = self.mcp_server.generate_date_range_report("nope", "2026-01-31")
        self.mock_tracker.generate_date_range_report.assert_not_called()
        self.assertIn("start_date", result)

    def test_generate_task_report(self):
        self.mock_tracker.generate_task_report.return_value = "# Task Report"
        result = self.mcp_server.generate_task_report("Acme", "Write docs")
        self.mock_tracker.generate_task_report.assert_called_once_with("Acme", "Write docs")
        self.assertEqual(result, "# Task Report")

    def test_generate_main_project_report(self):
        self.mock_tracker.generate_main_project_report.return_value = "# Project Report"
        result = self.mcp_server.generate_main_project_report("Acme")
        self.mock_tracker.generate_main_project_report.assert_called_once_with("Acme")
        self.assertEqual(result, "# Project Report")

    # --- misc ---

    def test_get_version(self):
        self.mock_tracker.get_version.return_value = "3.18"
        result = self.mcp_server.get_version()
        self.assertEqual(result, "3.18")

    # --- transport selection (http vs stdio) ---

    def test_main_runs_http_transport_by_default(self):
        with patch.object(self.mcp_server, '_STDIO_MODE', False), \
             patch.object(self.mcp_server, 'mcp') as mock_mcp:
            self.mcp_server.main()
        mock_mcp.run.assert_called_once_with(transport="streamable-http")

    def test_main_runs_stdio_transport_when_configured(self):
        with patch.object(self.mcp_server, '_STDIO_MODE', True), \
             patch.object(self.mcp_server, 'mcp') as mock_mcp:
            self.mcp_server.main()
        mock_mcp.run.assert_called_once_with(transport="stdio")

    def test_main_stdio_does_not_print_to_stdout(self):
        """
        stdout is the JSON-RPC message channel for the stdio transport, so
        main() must not print anything to it in that mode (unlike the HTTP
        branch, which announces its URL on stdout).
        """
        import io
        captured = io.StringIO()
        with patch.object(self.mcp_server, '_STDIO_MODE', True), \
             patch.object(self.mcp_server, 'mcp'), \
             patch('sys.stdout', captured):
            self.mcp_server.main()
        self.assertEqual(captured.getvalue(), "")

    # --- stdout protection for report tools under stdio ---

    def test_call_protecting_stdio_passes_through_for_http(self):
        with patch.object(self.mcp_server, '_STDIO_MODE', False):
            result = self.mcp_server._call_protecting_stdio(lambda a, b: a + b, 1, 2)
        self.assertEqual(result, 3)

    def test_call_protecting_stdio_redirects_stdout_for_stdio(self):
        """
        Regression test: TimeTracker's report methods can print a
        clipboard-copy notice (see TimeTracker._copy_to_clipboard). Under the
        stdio transport that print would corrupt the JSON-RPC stream, so it
        must land on stderr instead while stdio is active.
        """
        def prints_and_returns(value):
            print("Info: Report content has been copied to the clipboard.")
            return value

        import io
        captured_stdout = io.StringIO()
        captured_stderr = io.StringIO()
        with patch.object(self.mcp_server, '_STDIO_MODE', True), \
             patch('sys.stdout', captured_stdout), \
             patch('sys.stderr', captured_stderr):
            result = self.mcp_server._call_protecting_stdio(prints_and_returns, "# Report")

        self.assertEqual(result, "# Report")
        self.assertEqual(captured_stdout.getvalue(), "")
        self.assertIn("copied to the clipboard", captured_stderr.getvalue())

    def test_call_protecting_stdio_restores_stdout_after_call(self):
        real_stdout = sys.stdout
        with patch.object(self.mcp_server, '_STDIO_MODE', True):
            self.mcp_server._call_protecting_stdio(lambda: None)
        self.assertIs(sys.stdout, real_stdout)


if __name__ == '__main__':
    unittest.main()
