import unittest
import json
import os
from datetime import datetime, timedelta
from TimeTracker import TimeTracker # Assume TimeTracker.py is in the same directory

# The temporary file path for tests
TEST_FILE_PATH = 'test_data.json'

class TestTimeTracker(unittest.TestCase):
    """Unit tests for the TimeTracker class."""

    def setUp(self):
        """Runs before each test to create a clean TimeTracker instance."""
        self.tracker = TimeTracker(file_path=TEST_FILE_PATH)
        # Ensure the file does not exist before the test
        if os.path.exists(TEST_FILE_PATH):
            os.remove(TEST_FILE_PATH)
        self.tracker = TimeTracker(file_path=TEST_FILE_PATH)
        
    def tearDown(self):
        """Runs after each test to delete the temporary file."""
        if os.path.exists(TEST_FILE_PATH):
            os.remove(TEST_FILE_PATH)

    # --- Hilfsmethoden Tests (Private Methods) ---

    def test_load_data_initial_empty(self):
        """Tests if _load_data returns an empty dictionary when no file exists."""
        self.assertEqual(self.tracker.data, {"projects": []})

    def test_save_and_load_data(self):
        """Tests the interaction of _save_data and _load_data."""
        # Manual data modification and saving
        self.tracker.data["projects"].append({"main_project_name": "Test"})
        self.tracker._save_data()
        
        # Reloading the data in a new instance
        new_tracker = TimeTracker(file_path=TEST_FILE_PATH)
        self.assertEqual(len(new_tracker.data["projects"]), 1)
        self.assertEqual(new_tracker.data["projects"][0]["main_project_name"], "Test")
        
    # --- Main Project Method Tests ---

    def test_add_main_project(self):
        """Tests adding a main project."""
        self.tracker.add_main_project("Project A")
        self.assertEqual(len(self.tracker.data["projects"]), 1)
        self.assertEqual(self.tracker.data["projects"][0]["main_project_name"], "Project A")
        
    def test_list_main_projects(self):
        """Tests listing main projects."""
        self.tracker.add_main_project("Project Alpha")
        self.tracker.add_main_project("Project Beta")
        projects = self.tracker.list_main_projects()
        self.assertEqual(projects, ["Project Alpha", "Project Beta"])
        
    def test_delete_main_project_success(self):
        """Tests the successful deletion of a main project."""
        self.tracker.add_main_project("To Delete")
        self.tracker.add_main_project("To Keep")
        success = self.tracker.delete_main_project("To Delete")
        self.assertTrue(success)
        self.assertEqual(self.tracker.list_main_projects(), ["To Keep"])

    def test_delete_main_project_not_found(self):
        """Tests deleting a non-existent main project."""
        self.tracker.add_main_project("Project 1")
        success = self.tracker.delete_main_project("Non Existent")
        self.assertFalse(success)
        self.assertEqual(len(self.tracker.data["projects"]), 1)

    def test_rename_main_project_success(self):
        """Tests the successful renaming of a main project."""
        self.tracker.add_main_project("Old Project Name")
        success = self.tracker.rename_main_project("Old Project Name", "New Project Name")
        self.assertTrue(success)
        self.assertEqual(self.tracker.list_main_projects(), ["New Project Name"])

    def test_rename_main_project_not_found(self):
        """Tests renaming a non-existent main project."""
        self.tracker.add_main_project("Existing Project")
        success = self.tracker.rename_main_project("Non-Existent", "New Name")
        self.assertFalse(success)

    def test_rename_main_project_new_name_exists(self):
        """Tests that renaming fails if the new name already exists."""
        self.tracker.add_main_project("Project A")
        self.tracker.add_main_project("Project B")
        self.assertFalse(self.tracker.rename_main_project("Project A", "Project B"))

    # --- Sub-Project Method Tests ---

    def test_add_sub_project_success(self):
        """Tests the successful addition of a sub-project."""
        self.tracker.add_main_project("Main Test")
        success = self.tracker.add_sub_project("Main Test", "Sub Task 1")
        self.assertTrue(success)
        sub_projects = self.tracker.data["projects"][0]["sub_projects"]
        self.assertEqual(len(sub_projects), 1)
        self.assertEqual(sub_projects[0]["sub_project_name"], "Sub Task 1")

    def test_add_sub_project_main_not_found(self):
        """Tests adding a sub-project to a non-existent main project."""
        success = self.tracker.add_sub_project("Non Existent", "Sub Task 1")
        self.assertFalse(success)

    def test_list_sub_projects(self):
        """Tests listing sub-projects."""
        self.tracker.add_main_project("Main List")
        self.tracker.add_sub_project("Main List", "Sub A")
        self.tracker.add_sub_project("Main List", "Sub B")
        subs = self.tracker.list_sub_projects("Main List")
        self.assertEqual(subs, ["Sub A", "Sub B"])
        self.assertIsNone(self.tracker.list_sub_projects("Unknown Project"))

    def test_delete_sub_project_success(self):
        """Tests the successful deletion of a sub-project."""
        self.tracker.add_main_project("Main Test")
        self.tracker.add_sub_project("Main Test", "Sub To Delete")
        self.tracker.add_sub_project("Main Test", "Sub To Keep")
        
        success = self.tracker.delete_sub_project("Main Test", "Sub To Delete")
        self.assertTrue(success)
        self.assertEqual(self.tracker.list_sub_projects("Main Test"), ["Sub To Keep"])

    def test_delete_sub_project_not_found(self):
        """Tests deleting a non-existent sub-project."""
        self.tracker.add_main_project("Main Test")
        self.tracker.add_sub_project("Main Test", "Sub 1")
        
        success = self.tracker.delete_sub_project("Main Test", "Non Existent Sub")
        self.assertFalse(success)
        self.assertEqual(len(self.tracker.list_sub_projects("Main Test")), 1)

    def test_rename_sub_project_success(self):
        """Tests the successful renaming of a sub-project."""
        self.tracker.add_main_project("Main")
        self.tracker.add_sub_project("Main", "Old Name")
        success = self.tracker.rename_sub_project("Main", "Old Name", "New Name")
        self.assertTrue(success)
        self.assertEqual(self.tracker.list_sub_projects("Main"), ["New Name"])

    def test_rename_sub_project_main_not_found(self):
        """Tests renaming when the main project does not exist."""
        self.assertFalse(self.tracker.rename_sub_project("Non-Existent", "Old", "New"))

    def test_rename_sub_project_sub_not_found(self):
        """Tests renaming when the sub-project does not exist."""
        self.tracker.add_main_project("Main")
        self.assertFalse(self.tracker.rename_sub_project("Main", "Non-Existent", "New"))

    def test_rename_sub_project_new_name_exists(self):
        """Tests that renaming fails if the new name already exists."""
        self.tracker.add_main_project("Main")
        self.tracker.add_sub_project("Main", "Sub A")
        self.tracker.add_sub_project("Main", "Sub B")
        # Attempt to rename "Sub A" to "Sub B"
        success = self.tracker.rename_sub_project("Main", "Sub A", "Sub B")
        self.assertFalse(success)
        self.assertEqual(self.tracker.list_sub_projects("Main"), ["Sub A", "Sub B"])
        
    # --- Time Tracking Method Tests ---
    
    def _create_mock_project_with_sub(self, main_name, sub_name):
        """Helper function to set up a project for time tracking tests."""
        self.tracker.add_main_project(main_name)
        self.tracker.add_sub_project(main_name, sub_name)

    def test_start_work_success(self):
        """Tests the successful start of a work session."""
        self._create_mock_project_with_sub("Work Test", "Task X")
        success = self.tracker.start_work("Work Test", "Task X")
        self.assertTrue(success)
        
        entries = self.tracker.data["projects"][0]["sub_projects"][0]["time_entries"]
        self.assertEqual(len(entries), 1)
        self.assertIn("start_time", entries[0])
        self.assertNotIn("end_time", entries[0])
        
    def test_start_work_main_not_found(self):
        """Tests starting work on a non-existent main project."""
        self.assertFalse(self.tracker.start_work("Non Existent", "Task"))
        
    def test_start_work_stops_previous(self):
        """Tests if a running session is stopped when a new one is started."""
        self._create_mock_project_with_sub("P1", "T1")
        self._create_mock_project_with_sub("P2", "T2") # Leads to index 1
        
        # Start T1
        self.tracker.start_work("P1", "T1")
        # Start T2 (should stop T1)
        self.tracker.start_work("P2", "T2")
        
        # Check if T1 has an end_time entry
        t1_entry = self.tracker.data["projects"][0]["sub_projects"][0]["time_entries"][0]
        self.assertIn("end_time", t1_entry)
        
        # Check if T2 only has a start_time
        t2_entry = self.tracker.data["projects"][1]["sub_projects"][0]["time_entries"][0]
        self.assertNotIn("end_time", t2_entry)

    def test_stop_work_success(self):
        """Tests the successful stopping of work."""
        self._create_mock_project_with_sub("P1", "T1")
        self.tracker.start_work("P1", "T1")
        
        success = self.tracker.stop_work()
        self.assertTrue(success)
        
        entry = self.tracker.data["projects"][0]["sub_projects"][0]["time_entries"][0]
        self.assertIn("end_time", entry)
        
    def test_stop_work_no_active_session(self):
        """Tests stopping when no active session is running."""
        self._create_mock_project_with_sub("P1", "T1")
        
        # Stop without having started before
        success = self.tracker.stop_work()
        self.assertFalse(success)
        
        # Start and stop
        self.tracker.start_work("P1", "T1")
        self.tracker.stop_work()
        
        # Stop again
        success = self.tracker.stop_work()
        self.assertFalse(success)
        
    # --- Inactivity Method Tests ---

    def test_list_inactive_sub_projects(self):
        """Tests listing inactive sub-projects."""
        now = datetime.now()
        
        # P1: Active project (started 1 week ago, stopped 1 day ago) -> should NOT be listed
        self._create_mock_project_with_sub("P1_Active", "T1_Recent")
        self.tracker.data["projects"][0]["sub_projects"][0]["time_entries"].append({
            "start_time": (now - timedelta(weeks=1)).isoformat(),
            "end_time": (now - timedelta(days=1)).isoformat()
        })

        # P2: Inactive project (stopped 5 weeks ago) -> should be listed (at 4 weeks threshold)
        self._create_mock_project_with_sub("P2_Inactive", "T2_Old")
        self.tracker.data["projects"][1]["sub_projects"][0]["time_entries"].append({
            "start_time": (now - timedelta(weeks=5, days=1)).isoformat(),
            "end_time": (now - timedelta(weeks=5)).isoformat()
        })

        # P3: Running project (should be ignored)
        self._create_mock_project_with_sub("P3_Running", "T3_Open")
        self.tracker.data["projects"][2]["sub_projects"][0]["time_entries"].append({
            "start_time": (now - timedelta(days=1)).isoformat()
        })
        
        # Save to ensure data consistency
        self.tracker._save_data()
        
        # Test with 4 weeks inactivity threshold
        inactive_list = self.tracker.list_inactive_sub_projects(inactive_weeks=4)
        
        # Expectation: Only P2_Inactive should be listed
        self.assertEqual(len(inactive_list), 1)
        self.assertEqual(inactive_list[0]['sub_project'], "T2_Old")
        self.assertEqual(inactive_list[0]['main_project'], "P2_Inactive")
        
        # Test with 6 weeks inactivity threshold (should be empty)
        inactive_list_6w = self.tracker.list_inactive_sub_projects(inactive_weeks=6)
        self.assertEqual(len(inactive_list_6w), 0)

    def test_list_inactive_main_projects(self):
        """Tests listing inactive main projects."""
        now = datetime.now()
        
        # P1: Active main project (last activity 1 day ago) -> should NOT be listed
        self.tracker.add_main_project("P1_Active")
        self.tracker.add_sub_project("P1_Active", "T1_Recent")
        self.tracker.data["projects"][0]["sub_projects"][0]["time_entries"].append({
            "start_time": (now - timedelta(days=2)).isoformat(),
            "end_time": (now - timedelta(days=1)).isoformat()
        })

        # P2: Inactive main project (last activity 5 weeks ago) -> should be listed (at 4 weeks threshold)
        self.tracker.add_main_project("P2_Inactive")
        self.tracker.add_sub_project("P2_Inactive", "T2_Old")
        self.tracker.data["projects"][1]["sub_projects"][0]["time_entries"].append({
            "start_time": (now - timedelta(weeks=5, days=1)).isoformat(),
            "end_time": (now - timedelta(weeks=5)).isoformat()
        })

        # P3: Running main project (contains an open sub-entry) -> should be ignored
        self.tracker.add_main_project("P3_Running")
        self.tracker.add_sub_project("P3_Running", "T3_Open")
        self.tracker.data["projects"][2]["sub_projects"][0]["time_entries"].append({
            "start_time": (now - timedelta(days=1)).isoformat()
        })
        
        # P4: Empty main project (no activity) -> should be ignored
        self.tracker.add_main_project("P4_Empty")

        # Save to ensure data consistency
        self.tracker._save_data()
        
        # Test with 4 weeks inactivity threshold
        inactive_list = self.tracker.list_inactive_main_projects(inactive_weeks=4)
        
        # Expectation: Only P2_Inactive should be listed
        self.assertEqual(len(inactive_list), 1)
        self.assertEqual(inactive_list[0]['main_project'], "P2_Inactive")
        
        # Test with 6 weeks inactivity threshold (should be empty)
        inactive_list_6w = self.tracker.list_inactive_main_projects(inactive_weeks=6)
        self.assertEqual(len(inactive_list_6w), 0)
    # --- Report Method Tests ---

    def test_generate_daily_report_formatting(self):
        """Tests report generation, especially comma formatting and summation."""
        
        now = datetime.now()
        today_date = now.date()
        
        # P1: 1.5 hours today
        self._create_mock_project_with_sub("Report P1", "R_Sub1")
        self.tracker.data["projects"][0]["sub_projects"][0]["time_entries"].append({
            "start_time": now.replace(hour=8, minute=0, second=0, microsecond=0).isoformat(),
            "end_time": now.replace(hour=9, minute=30, second=0, microsecond=0).isoformat()
        })
        
        # P2: 2.0 hours today
        self._create_mock_project_with_sub("Report P2", "R_Sub2")
        self.tracker.data["projects"][1]["sub_projects"][0]["time_entries"].append({
            "start_time": now.replace(hour=10, minute=0, second=0, microsecond=0).isoformat(),
            "end_time": now.replace(hour=12, minute=0, second=0, microsecond=0).isoformat()
        })
        
        # P3: Entry for yesterday (should be ignored)
        yesterday = now - timedelta(days=1)
        self._create_mock_project_with_sub("Report P3", "R_Sub3_Old")
        self.tracker.data["projects"][2]["sub_projects"][0]["time_entries"].append({
            "start_time": yesterday.replace(hour=8, minute=0, second=0, microsecond=0).isoformat(),
            "end_time": yesterday.replace(hour=9, minute=0, second=0, microsecond=0).isoformat()
        })
        
        report = self.tracker.generate_daily_report(today_date)
        
        # Expected total time: 1.5 + 2.0 = 3.5 hours
        
        # Check for correct comma formatting of hours
        self.assertIn("1,500 hours", report)
        self.assertIn("2,000 hours", report)
        self.assertIn("**Total Daily Time: 3,500 hours**", report)
        
        # Check if P3 was ignored
        self.assertNotIn("R_Sub3_Old", report)
        
        # Check the overall structure
        self.assertTrue(report.startswith(f"# Daily Time Report: {today_date.strftime('%Y-%m-%d')}"))
        self.assertIn("## Report P1 (1,500 hours)", report)
        self.assertIn("## Report P2 (2,000 hours)", report)
        
    def test_generate_date_range_report(self):
        """Tests report generation for a date range."""
        
        # Setup: Create entries on different days
        day1 = datetime(2025, 10, 20)
        day2 = datetime(2025, 10, 22)
        day_outside = datetime(2025, 10, 25)

        # P1: 1 hour on day 1
        self._create_mock_project_with_sub("Range P1", "R_Sub1")
        self.tracker.data["projects"][0]["sub_projects"][0]["time_entries"].append({
            "start_time": day1.replace(hour=9).isoformat(),
            "end_time": day1.replace(hour=10).isoformat()
        })
        
        # P2: 2 hours on day 2
        self._create_mock_project_with_sub("Range P2", "R_Sub2")
        self.tracker.data["projects"][1]["sub_projects"][0]["time_entries"].append({
            "start_time": day2.replace(hour=11).isoformat(),
            "end_time": day2.replace(hour=13).isoformat()
        })
        
        # P3: Entry outside the range (should be ignored)
        self._create_mock_project_with_sub("Range P3", "R_Sub3_Outside")
        self.tracker.data["projects"][2]["sub_projects"][0]["time_entries"].append({
            "start_time": day_outside.replace(hour=9).isoformat(),
            "end_time": day_outside.replace(hour=10).isoformat()
        })
        
        # Test: Generate report for the range from day 1 to day 2
        start_date = day1.date()
        end_date = day2.date()
        report = self.tracker.generate_date_range_report(start_date, end_date)
        
        # Expected total time: 1 + 2 = 3 hours
        # 1h = 0.025 DLP; 2h = 0.050 DLP; 3h = 0.075 DLP
        self.assertIn("## Range P1 (1,000 hours (0,025 DLP))", report)
        self.assertIn("## Range P2 (2,000 hours (0,050 DLP))", report)
        self.assertNotIn("Range P3", report)
        self.assertIn("**Total Time in Period: 3,000 hours (0,075 DLP)**", report)
        self.assertTrue(report.startswith(f"# Time Report: {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}"))

# Run the tests if the file is called directly
if __name__ == '__main__':
    unittest.main()