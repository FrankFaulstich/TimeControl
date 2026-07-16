import unittest
from unittest.mock import MagicMock, patch
import sys
import os

# Add parent directory to path to import modules from root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


class TestTimeTrackerREST_Server(unittest.TestCase):
    """
    Unlike TimeTrackerSOAP_Server.py's tests (which have to mock a hand-built
    `ctx` because spyne dispatches @rpc methods as unbound functions on the
    service class), FastAPI's TestClient already exercises the real routing,
    request-parsing and dependency-injection machinery for every call below -
    there is no separate "real dispatch" test class needed the way SOAP has
    one, since this already *is* that.  TimeTracker itself is swapped out via
    FastAPI's dependency_overrides so no real file I/O happens.
    """

    @classmethod
    def setUpClass(cls):
        cls.tt_patcher = patch('tt.TimeTracker.TimeTracker')
        cls.MockTimeTrackerClass = cls.tt_patcher.start()

        try:
            import TimeTrackerREST_Server
            from fastapi.testclient import TestClient
        except ImportError:
            cls.tt_patcher.stop()
            raise unittest.SkipTest("Could not import TimeTrackerREST_Server (are 'fastapi'/'uvicorn' installed?).")
        except SystemExit:
            cls.tt_patcher.stop()
            raise unittest.SkipTest("fastapi/uvicorn not installed or import error in TimeTrackerREST_Server")

        cls.rest_server = TimeTrackerREST_Server
        cls.client = TestClient(TimeTrackerREST_Server.app)

    @classmethod
    def tearDownClass(cls):
        cls.tt_patcher.stop()

    def setUp(self):
        self.MockTimeTrackerClass.reset_mock()
        # get_tracker() creates TimeTracker() fresh for every request; since
        # the class itself is mocked, every such call returns this same mock
        # instance, so tests can set expectations on it directly.
        self.mock_tracker = self.MockTimeTrackerClass.return_value

    # --- Main Project Management ---

    def test_get_version(self):
        self.mock_tracker.get_version.return_value = "1.2.3"
        r = self.client.get("/version")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"version": "1.2.3"})

    def test_add_main_project(self):
        r = self.client.post("/projects", json={"main_project_name": "Test Project"})
        self.assertEqual(r.status_code, 200)
        self.mock_tracker.add_main_project.assert_called_once_with("Test Project")
        self.assertEqual(r.json(), {"success": True})

    def test_list_main_projects(self):
        self.mock_tracker.list_main_projects.return_value = [
            {'main_project_name': 'Project A', 'status': 'open'},
            {'main_project_name': 'Project B', 'status': 'closed'},
        ]
        r = self.client.get("/projects", params={"status_filter": "all"})
        self.assertEqual(r.status_code, 200)
        self.mock_tracker.list_main_projects.assert_called_once_with("all")
        self.assertEqual(r.json(), [
            {'main_project_name': 'Project A', 'status': 'open'},
            {'main_project_name': 'Project B', 'status': 'closed'},
        ])

    def test_list_main_projects_default_status_filter(self):
        self.mock_tracker.list_main_projects.return_value = []
        r = self.client.get("/projects")
        self.assertEqual(r.status_code, 200)
        self.mock_tracker.list_main_projects.assert_called_once_with("all")

    def test_list_completed_main_projects(self):
        self.mock_tracker.list_completed_main_projects.return_value = ["Done Project"]
        r = self.client.get("/projects/completed")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), ["Done Project"])

    def test_list_inactive_main_projects(self):
        self.mock_tracker.list_inactive_main_projects.return_value = [
            {'main_project': 'Old', 'task_name': None, 'last_activity': '2020-01-01'}
        ]
        r = self.client.get("/projects/inactive", params={"weeks": 4})
        self.assertEqual(r.status_code, 200)
        self.mock_tracker.list_inactive_main_projects.assert_called_once_with(4)
        self.assertEqual(r.json()[0]['main_project'], 'Old')

    def test_delete_main_project_success(self):
        self.mock_tracker.delete_main_project.return_value = True
        r = self.client.delete("/projects/Acme")
        self.assertEqual(r.status_code, 200)
        self.mock_tracker.delete_main_project.assert_called_once_with("Acme")
        self.assertEqual(r.json(), {"success": True})

    def test_delete_main_project_not_found(self):
        self.mock_tracker.delete_main_project.return_value = False
        r = self.client.delete("/projects/Nope")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"success": False})

    def test_rename_main_project(self):
        self.mock_tracker.rename_main_project.return_value = True
        r = self.client.post("/projects/Old/rename", json={"new_name": "New"})
        self.assertEqual(r.status_code, 200)
        self.mock_tracker.rename_main_project.assert_called_once_with("Old", "New")
        self.assertEqual(r.json(), {"success": True})

    def test_close_main_project(self):
        self.mock_tracker.close_main_project.return_value = True
        r = self.client.post("/projects/Acme/close")
        self.assertEqual(r.status_code, 200)
        self.mock_tracker.close_main_project.assert_called_once_with("Acme")

    def test_reopen_main_project(self):
        self.mock_tracker.reopen_main_project.return_value = True
        r = self.client.post("/projects/Acme/reopen")
        self.assertEqual(r.status_code, 200)
        self.mock_tracker.reopen_main_project.assert_called_once_with("Acme")

    def test_demote_main_project(self):
        self.mock_tracker.demote_main_project.return_value = (True, "Demoted successfully")
        r = self.client.post("/projects/Old/demote", json={"new_parent": "NewParent"})
        self.assertEqual(r.status_code, 200)
        self.mock_tracker.demote_main_project.assert_called_once_with("Old", "NewParent")
        self.assertEqual(r.json(), {"success": True, "message": "Demoted successfully"})

    # --- Task Management ---

    def test_add_task(self):
        self.mock_tracker.add_task.return_value = True
        r = self.client.post("/projects/Main/tasks", json={
            "task_name": "Sub",
            "due_date": "2025-12-24",
            "today": True,
            "note": "Note",
        })
        self.assertEqual(r.status_code, 200)
        self.mock_tracker.add_task.assert_called_once_with(
            "Main", "Sub", "2025-12-24", True, "Note", False, "daily", 1
        )
        self.assertEqual(r.json(), {"success": True})

    def test_list_tasks(self):
        self.mock_tracker.list_tasks.return_value = [
            {
                'id': 1, 'main_project_name': 'Main', 'task_name': 'Sub 1', 'status': 'open',
                'due_date': None, 'today': False, 'note': '', 'recurring': False,
                'frequency': 'daily', 'userdefined_days': 1,
            }
        ]
        r = self.client.get("/tasks", params={"main_project_name": "Main", "status_filter": "open"})
        self.assertEqual(r.status_code, 200)
        self.mock_tracker.list_tasks.assert_called_once_with("Main", "open")
        self.assertEqual(r.json()[0]['task_name'], 'Sub 1')

    def test_list_tasks_with_planning_filter(self):
        self.mock_tracker.list_tasks.return_value = []
        r = self.client.get("/tasks", params={"planning_filter": "today"})
        self.assertEqual(r.status_code, 200)
        self.mock_tracker.list_tasks.assert_called_once_with(None, "all", "today")

    def test_list_inactive_tasks(self):
        self.mock_tracker.list_inactive_tasks.return_value = [
            {'main_project': 'Main', 'task_name': 'Sub', 'last_activity': '2020-01-01'}
        ]
        r = self.client.get("/tasks/inactive", params={"weeks": 2})
        self.assertEqual(r.status_code, 200)
        self.mock_tracker.list_inactive_tasks.assert_called_once_with(2)

    def test_cleanup_overdue_today_tasks(self):
        self.mock_tracker.cleanup_overdue_today_tasks.return_value = True
        r = self.client.post("/tasks/cleanup-overdue-today")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"success": True})

    def test_delete_all_closed_tasks(self):
        self.mock_tracker.delete_all_closed_tasks.return_value = 3
        r = self.client.delete("/tasks/closed")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"count": 3})

    def test_delete_task(self):
        self.mock_tracker.delete_task.return_value = True
        r = self.client.delete("/projects/Main/tasks/Sub")
        self.assertEqual(r.status_code, 200)
        self.mock_tracker.delete_task.assert_called_once_with("Main", "Sub", task_id=None)
        self.assertEqual(r.json(), {"success": True})

    def test_delete_task_with_task_id(self):
        self.mock_tracker.delete_task.return_value = True
        r = self.client.delete("/projects/Main/tasks/Sub", params={"task_id": 7})
        self.assertEqual(r.status_code, 200)
        self.mock_tracker.delete_task.assert_called_once_with("Main", "Sub", task_id=7)

    def test_close_task(self):
        self.mock_tracker.close_task.return_value = True
        r = self.client.post("/projects/Main/tasks/Sub/close")
        self.assertEqual(r.status_code, 200)
        self.mock_tracker.close_task.assert_called_once_with("Main", "Sub", task_id=None)

    def test_reopen_task(self):
        self.mock_tracker.reopen_task.return_value = True
        r = self.client.post("/projects/Main/tasks/Sub/reopen")
        self.assertEqual(r.status_code, 200)
        self.mock_tracker.reopen_task.assert_called_once_with("Main", "Sub", task_id=None)

    def test_rename_task(self):
        self.mock_tracker.rename_task.return_value = True
        r = self.client.post("/projects/Main/tasks/Old/rename", json={"new_name": "New"})
        self.assertEqual(r.status_code, 200)
        self.mock_tracker.rename_task.assert_called_once_with("Main", "Old", "New", task_id=None)

    def test_update_task(self):
        self.mock_tracker.update_task.return_value = True
        r = self.client.patch("/projects/Main/tasks/Old", json={
            "new_name": "New", "due_date": "2025-01-01", "today": True, "note": "Note", "status": "done",
        })
        self.assertEqual(r.status_code, 200)
        self.mock_tracker.update_task.assert_called_once_with(
            "Main", "Old", "New", "2025-01-01", True, "Note", "done", None, None, None, task_id=None
        )
        self.assertEqual(r.json(), {"success": True})

    def test_move_task(self):
        self.mock_tracker.move_task.return_value = (True, "Moved successfully")
        r = self.client.post("/projects/Main/tasks/Sub/move", json={"new_main_project_name": "Other"})
        self.assertEqual(r.status_code, 200)
        self.mock_tracker.move_task.assert_called_once_with("Main", "Sub", "Other", task_id=None)
        self.assertEqual(r.json(), {"success": True, "message": "Moved successfully"})

    def test_promote_task_to_project(self):
        self.mock_tracker.promote_task_to_project.return_value = (True, "Promoted successfully")
        r = self.client.post("/projects/Main/tasks/Sub/promote")
        self.assertEqual(r.status_code, 200)
        self.mock_tracker.promote_task_to_project.assert_called_once_with("Main", "Sub", task_id=None)
        self.assertEqual(r.json(), {"success": True, "message": "Promoted successfully"})

    # --- Time Tracking ---

    def test_start_work(self):
        self.mock_tracker.start_work.return_value = True
        r = self.client.post("/work/start", json={"main_project_name": "Main", "task_name": "Sub"})
        self.assertEqual(r.status_code, 200)
        self.mock_tracker.start_work.assert_called_once_with("Main", "Sub")
        self.assertEqual(r.json(), {"success": True})

    def test_start_work_with_task_id(self):
        self.mock_tracker.start_work.return_value = True
        r = self.client.post("/work/start", json={"main_project_name": "Main", "task_id": 5})
        self.assertEqual(r.status_code, 200)
        self.mock_tracker.start_work.assert_called_once_with("Main", task_id=5)

    def test_stop_work(self):
        self.mock_tracker.stop_work.return_value = False
        r = self.client.post("/work/stop")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"success": False})
        self.mock_tracker.stop_work.assert_called_once()

    def test_get_current_work_active(self):
        self.mock_tracker.get_current_work.return_value = {
            'main_project_name': 'M', 'task_name': 'S', 'start_time': '2023-01-01',
        }
        r = self.client.get("/work/current")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['main_project_name'], 'M')

    def test_get_current_work_none(self):
        self.mock_tracker.get_current_work.return_value = None
        r = self.client.get("/work/current")
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.json())

    # --- Reporting ---

    def test_generate_daily_report_today(self):
        self.mock_tracker.generate_daily_report.return_value = "# Report"
        r = self.client.get("/reports/daily")
        self.assertEqual(r.status_code, 200)
        self.mock_tracker.generate_daily_report.assert_called_once_with(None)
        self.assertEqual(r.json(), {"report": "# Report"})

    def test_generate_daily_report_specific_date(self):
        from datetime import date
        self.mock_tracker.generate_daily_report.return_value = "# Report"
        r = self.client.get("/reports/daily", params={"report_date": "2025-10-20"})
        self.assertEqual(r.status_code, 200)
        self.mock_tracker.generate_daily_report.assert_called_once_with(date(2025, 10, 20))

    def test_generate_daily_report_invalid_date(self):
        r = self.client.get("/reports/daily", params={"report_date": "not-a-date"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("report_date", r.json()["detail"])
        self.mock_tracker.generate_daily_report.assert_not_called()

    def test_generate_detailed_daily_report(self):
        self.mock_tracker.generate_detailed_daily_report.return_value = "# Detailed Report"
        r = self.client.get("/reports/daily/detailed")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"report": "# Detailed Report"})

    def test_generate_date_range_report(self):
        from datetime import date
        self.mock_tracker.generate_date_range_report.return_value = "# Range Report"
        r = self.client.get("/reports/range", params={"start_date": "2025-01-01", "end_date": "2025-01-31"})
        self.assertEqual(r.status_code, 200)
        self.mock_tracker.generate_date_range_report.assert_called_once_with(date(2025, 1, 1), date(2025, 1, 31))

    def test_generate_date_range_report_invalid_start(self):
        r = self.client.get("/reports/range", params={"start_date": "nope", "end_date": "2025-01-31"})
        self.assertEqual(r.status_code, 400)
        self.mock_tracker.generate_date_range_report.assert_not_called()

    def test_generate_task_report(self):
        self.mock_tracker.generate_task_report.return_value = "# Task Report"
        r = self.client.get("/reports/task", params={"main_project_name": "Main", "task_name": "Sub"})
        self.assertEqual(r.status_code, 200)
        self.mock_tracker.generate_task_report.assert_called_once_with("Main", "Sub")
        self.assertEqual(r.json(), {"report": "# Task Report"})

    def test_generate_main_project_report(self):
        self.mock_tracker.generate_main_project_report.return_value = "# Project Report"
        r = self.client.get("/reports/project", params={"main_project_name": "Main"})
        self.assertEqual(r.status_code, 200)
        self.mock_tracker.generate_main_project_report.assert_called_once_with("Main")


if __name__ == '__main__':
    unittest.main()
