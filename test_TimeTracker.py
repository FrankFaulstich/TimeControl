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
        
    def test_migrate_data_structure_adds_status(self):
        """Tests that the migration logic adds the 'status' field to sub-projects."""
        # 1. Create a data file with an old structure (no 'status' field)
        old_data = {
            "projects": [{
                "main_project_name": "Old Project",
                "sub_projects": [{
                    "sub_project_name": "Sub without status",
                    "time_entries": []
                }]
            }]
        }
        with open(TEST_FILE_PATH, 'w') as f:
            json.dump(old_data, f)

        # 2. Initialize the tracker, which should trigger the migration
        tracker = TimeTracker(file_path=TEST_FILE_PATH)

        # 3. Check if the 'status' field was added and set to 'open'
        sub_project = tracker.data["projects"][0]["sub_projects"][0]
        self.assertIn("status", sub_project)
        self.assertEqual(sub_project["status"], "open")

    def test_format_duration(self):
        """Tests the _format_duration helper method."""
        # Test case 1: 8 hours -> 0,200 DLP
        duration1 = timedelta(hours=8)
        self.assertEqual(self.tracker._format_duration(duration1), "8,000 hour (0,200 DLP)")
        # Test case 2: 40 hours -> 1,000 DLP
        duration2 = timedelta(hours=40)
        self.assertEqual(self.tracker._format_duration(duration2), "40,000 hour (1,000 DLP)")
        # Test case 3: 1.5 hours
        duration3 = timedelta(hours=1.5)
        self.assertEqual(self.tracker._format_duration(duration3), "1,500 hour (0,038 DLP)")

    # --- General Method Tests ---

    def test_get_version(self):
        """Tests retrieving the application version."""
        # Compare the method's output with the class attribute
        self.assertEqual(self.tracker.get_version(), TimeTracker.VERSION)

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

    def test_list_open_sub_projects(self):
        """Tests that only sub-projects with status 'open' are listed."""
        self.tracker.add_main_project("Main")
        self.tracker.add_sub_project("Main", "Open Sub 1") # status: 'open' by default
        self.tracker.add_sub_project("Main", "To Be Closed")
        self.tracker.add_sub_project("Main", "Open Sub 2")

        # Manually close one sub-project
        self.tracker.data["projects"][0]["sub_projects"][1]["status"] = "closed"
        self.tracker._save_data()

        open_subs = self.tracker.list_open_sub_projects("Main")
        self.assertEqual(open_subs, ["Open Sub 1", "Open Sub 2"])
        self.assertNotIn("To Be Closed", open_subs)

    def test_close_sub_project_success(self):
        """Tests setting a sub-project's status to 'closed'."""
        self.tracker.add_main_project("Main")
        self.tracker.add_sub_project("Main", "Task to Close")

        # Verify it's open first
        self.assertIn("Task to Close", self.tracker.list_open_sub_projects("Main"))

        success = self.tracker.close_sub_project("Main", "Task to Close")
        self.assertTrue(success)

        # Verify it's now closed
        sub_project = self.tracker.data["projects"][0]["sub_projects"][0]
        self.assertEqual(sub_project["status"], "closed")

        # Verify it no longer appears in the list of open projects
        self.assertNotIn("Task to Close", self.tracker.list_open_sub_projects("Main"))

    def test_close_sub_project_not_found(self):
        """Tests closing a non-existent sub-project."""
        self.tracker.add_main_project("Main")
        self.assertFalse(self.tracker.close_sub_project("Main", "Non-Existent"))

    def test_reopen_sub_project_success(self):
        """Tests reopening a closed sub-project."""
        self.tracker.add_main_project("Main")
        self.tracker.add_sub_project("Main", "Task to Reopen")

        # First, close it
        self.tracker.close_sub_project("Main", "Task to Reopen")
        self.assertNotIn("Task to Reopen", self.tracker.list_open_sub_projects("Main"))
        self.assertIn("Task to Reopen", self.tracker.list_closed_sub_projects("Main"))

        # Now, reopen it
        success = self.tracker.reopen_sub_project("Main", "Task to Reopen")
        self.assertTrue(success)
        self.assertIn("Task to Reopen", self.tracker.list_open_sub_projects("Main"))

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

    def test_delete_all_closed_sub_projects(self):
        """Tests deleting all closed sub-projects across all main projects."""
        # Setup
        self.tracker.add_main_project("P1")
        self.tracker.add_sub_project("P1", "Open1")
        self.tracker.add_sub_project("P1", "Closed1")
        self.tracker.close_sub_project("P1", "Closed1")
        
        self.tracker.add_main_project("P2")
        self.tracker.add_sub_project("P2", "Closed2")
        self.tracker.close_sub_project("P2", "Closed2")
        self.tracker.add_sub_project("P2", "Closed3")
        self.tracker.close_sub_project("P2", "Closed3")
        
        self.tracker.add_main_project("P3")
        self.tracker.add_sub_project("P3", "Open2")

        # Action
        deleted_count = self.tracker.delete_all_closed_sub_projects()

        # Assertions
        self.assertEqual(deleted_count, 3)
        self.assertEqual(self.tracker.list_sub_projects("P1"), ["Open1"])
        self.assertEqual(self.tracker.list_sub_projects("P2"), [])
        self.assertEqual(self.tracker.list_sub_projects("P3"), ["Open2"])

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

    def test_move_sub_project_success(self):
        """Tests moving a sub-project successfully."""
        self.tracker.add_main_project("Source")
        self.tracker.add_sub_project("Source", "Task 1")
        self.tracker.add_main_project("Destination")

        success, msg = self.tracker.move_sub_project("Source", "Task 1", "Destination")
        self.assertTrue(success)
        self.assertEqual(self.tracker.list_sub_projects("Source"), [])
        self.assertEqual(self.tracker.list_sub_projects("Destination"), ["Task 1"])

    def test_move_sub_project_source_not_found(self):
        """Tests moving from a non-existent source main project."""
        self.tracker.add_main_project("Destination")
        success, msg = self.tracker.move_sub_project("Non-Existent", "Task 1", "Destination")
        self.assertFalse(success)
        self.assertIn("Source main project 'Non-Existent' not found", msg)

    def test_move_sub_project_dest_not_found(self):
        """Tests moving to a non-existent destination main project."""
        self.tracker.add_main_project("Source")
        self.tracker.add_sub_project("Source", "Task 1")
        success, msg = self.tracker.move_sub_project("Source", "Task 1", "Non-Existent")
        self.assertFalse(success)
        self.assertIn("Destination main project 'Non-Existent' not found", msg)

    def test_move_sub_project_name_conflict(self):
        """Tests moving a sub-project when the name exists in the destination."""
        self.tracker.add_main_project("Source")
        self.tracker.add_sub_project("Source", "Task 1")
        self.tracker.add_main_project("Destination")
        self.tracker.add_sub_project("Destination", "Task 1")
        success, msg = self.tracker.move_sub_project("Source", "Task 1", "Destination")
        self.assertFalse(success)
        self.assertIn("already exists in 'Destination'", msg)
        
    def test_promote_sub_project_success(self):
        """Tests promoting a sub-project to a main project successfully."""
        self.tracker.add_main_project("Source Main")
        self.tracker.add_sub_project("Source Main", "Promotable Sub")
        # Add a time entry to ensure it's carried over
        self.tracker.start_work("Source Main", "Promotable Sub")
        self.tracker.stop_work()

        success, msg = self.tracker.promote_sub_project("Source Main", "Promotable Sub")
        
        self.assertTrue(success)
        self.assertIn("was promoted", msg)

        # 1. Check if original sub-project is gone
        self.assertEqual(self.tracker.list_sub_projects("Source Main"), [])

        # 2. Check if new main project exists
        self.assertIn("Promotable Sub", self.tracker.list_main_projects())

        # 3. Check if new main project has a "General" sub-project with the time entries
        new_main_subs = self.tracker.list_sub_projects("Promotable Sub")
        self.assertEqual(new_main_subs, ["General"])
        
        new_main_project_data = next(p for p in self.tracker.data["projects"] if p["main_project_name"] == "Promotable Sub")
        general_sub = new_main_project_data["sub_projects"][0]
        self.assertEqual(len(general_sub["time_entries"]), 1)
        self.assertIn("start_time", general_sub["time_entries"][0])
        self.assertIn("end_time", general_sub["time_entries"][0])

    def test_promote_sub_project_name_conflict(self):
        """Tests that promoting fails if a main project with the same name already exists."""
        self.tracker.add_main_project("Source Main")
        self.tracker.add_sub_project("Source Main", "Existing Name")
        self.tracker.add_main_project("Existing Name") # This is the conflict

        success, msg = self.tracker.promote_sub_project("Source Main", "Existing Name")
        self.assertFalse(success)
        self.assertIn("already exists", msg)

    def test_demote_main_project_success(self):
        """Tests demoting a main project to a sub-project successfully."""
        # Setup: Main project to be demoted with two sub-projects
        self.tracker.add_main_project("Old Main")
        self.tracker.add_sub_project("Old Main", "Sub 1")
        self.tracker.start_work("Old Main", "Sub 1") # Entry 1
        self.tracker.stop_work()
        self.tracker.add_sub_project("Old Main", "Sub 2")
        self.tracker.start_work("Old Main", "Sub 2") # Entry 2
        self.tracker.stop_work()

        # Setup: New parent project
        self.tracker.add_main_project("New Parent")

        success, msg = self.tracker.demote_main_project("Old Main", "New Parent")
        self.assertTrue(success)
        self.assertIn("was demoted", msg)

        # 1. Check if old main project is gone
        self.assertNotIn("Old Main", self.tracker.list_main_projects())

        # 2. Check if new sub-project exists under the new parent
        new_parent_subs = self.tracker.list_sub_projects("New Parent")
        self.assertIn("Old Main", new_parent_subs)

        # 3. Check if all time entries were consolidated
        new_parent_project_data = next(p for p in self.tracker.data["projects"] if p["main_project_name"] == "New Parent")
        newly_demoted_sub = next(sp for sp in new_parent_project_data["sub_projects"] if sp["sub_project_name"] == "Old Main")
        self.assertEqual(len(newly_demoted_sub["time_entries"]), 2)

    def test_demote_main_project_name_conflict(self):
        """Tests that demoting fails if a sub-project with the same name already exists in the parent."""
        self.tracker.add_main_project("To Demote")
        self.tracker.add_main_project("Parent")
        self.tracker.add_sub_project("Parent", "To Demote") # Name conflict
        success, msg = self.tracker.demote_main_project("To Demote", "Parent")
        self.assertFalse(success)
        self.assertIn("already exists", msg)
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
        self.assertNotIn("end_time", self.tracker.data["projects"][1]["sub_projects"][0]["time_entries"][0])

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

    def test_get_current_work(self):
        """Tests retrieving the current working project."""
        # Case 1: No active work
        self.assertIsNone(self.tracker.get_current_work())

        # Case 2: Start work
        self._create_mock_project_with_sub("Main", "Sub")
        self.tracker.start_work("Main", "Sub")
        
        current_work = self.tracker.get_current_work()
        self.assertIsNotNone(current_work)
        self.assertEqual(current_work["main_project_name"], "Main")
        self.assertEqual(current_work["sub_project_name"], "Sub")
        self.assertIn("start_time", current_work)

        # Case 3: Stop work
        self.tracker.stop_work()
        self.assertIsNone(self.tracker.get_current_work())
        
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
        self.assertIn("1,500 hour", report)
        self.assertIn("2,000 hour", report)
        self.assertIn("**Total Daily Time: 3,500 hours**", report)
        
        # Check if P3 was ignored
        self.assertNotIn("R_Sub3_Old", report)

        # Check the overall structure
        self.assertTrue(report.startswith(f"# Daily Time Report: {today_date.strftime('%Y-%m-%d')}"))
        self.assertIn("## Report P1 (1,500 hour)", report)
        self.assertIn("## Report P2 (2,000 hour)", report)
        
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
        self.assertIn("## Range P1 (1,000 hour (0,025 DLP))", report)
        self.assertIn("## Range P2 (2,000 hour (0,050 DLP))", report)
        self.assertNotIn("Range P3", report)
        self.assertIn("**Total Time in Period: 3,000 hour (0,075 DLP)**", report)
        self.assertTrue(report.startswith(f"# Time Report: {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}"))

    def test_generate_sub_project_report(self):
        """Tests the detailed report generation for a single sub-project."""
        main_proj = "Detailed Report Main"
        sub_proj = "Detailed Report Sub"
        self._create_mock_project_with_sub(main_proj, sub_proj)

        # Entry 1: Yesterday
        # Use fixed dates to make weekday predictable. 2025-11-03 is a Monday.
        day1 = datetime(2025, 11, 3, 10, 0, 0) # Monday
        entry1_start = day1
        entry1_end = day1 + timedelta(hours=1, minutes=30) # 1.5 hours
        
        # Entry 2: A Tuesday
        day2 = datetime(2025, 11, 4, 14, 0, 0) # Tuesday
        entry2_start = day2
        entry2_end = day2 + timedelta(hours=1) # 1 hour

        # Manually add entries to control times precisely
        sub_project_data = self.tracker.data["projects"][0]["sub_projects"][0]
        sub_project_data["time_entries"].append({
            "start_time": entry1_start.isoformat(),
            "end_time": entry1_end.isoformat()
        })
        sub_project_data["time_entries"].append({
            "start_time": entry2_start.isoformat(),
            "end_time": entry2_end.isoformat()
        })
        self.tracker._save_data()

        report = self.tracker.generate_sub_project_report(main_proj, sub_proj)

        # Check for key information
        self.assertIn(f"# Detailed Report for Sub-Project: {sub_proj}", report)
        self.assertIn(f"Part of Main Project: {main_proj}", report)
        self.assertIn("Total recorded time:** 2:30:00", report)
        self.assertIn("Total work sessions:** 2", report)
        self.assertIn("Average session duration:** 1:15:00", report)
        self.assertIn("## Weekday Distribution", report)
        self.assertIn("- **Monday**: 1:30:00 (60.0%)", report) # 1.5h of 2.5h total
        self.assertIn("- **Tuesday**: 1:00:00 (40.0%)", report) # 1h of 2.5h total
        self.assertIn(f"### {day1.strftime('%Y-%m-%d')}", report)
        self.assertIn(f"### {day2.strftime('%Y-%m-%d')}", report)
        self.assertIn("10:00:00 - 11:30:00", report)
        self.assertIn("14:00:00 - 15:00:00", report)

    def test_generate_main_project_report(self):
        """Tests the detailed report generation for a single main project."""
        main_proj = "Main Project Report Test"
        sub_proj_1 = "Sub 1"
        sub_proj_2 = "Sub 2"
        self.tracker.add_main_project(main_proj)
        self.tracker.add_sub_project(main_proj, sub_proj_1)
        self.tracker.add_sub_project(main_proj, sub_proj_2)

        # Use fixed dates for predictable weekdays
        # Entry 1 for Sub 1: 1 hour on a Monday
        day1 = datetime(2025, 11, 3, 10, 0, 0)
        self.tracker.data["projects"][0]["sub_projects"][0]["time_entries"].append({
            "start_time": day1.isoformat(),
            "end_time": (day1 + timedelta(hours=1)).isoformat()
        })
        # Entry 2 for Sub 1: 30 minutes on a Tuesday
        day2 = datetime(2025, 11, 4, 14, 0, 0)
        self.tracker.data["projects"][0]["sub_projects"][0]["time_entries"].append({
            "start_time": day2.isoformat(),
            "end_time": (day2 + timedelta(minutes=30)).isoformat()
        })
        # Entry 1 for Sub 2: 2 hours, also on a Monday
        self.tracker.data["projects"][0]["sub_projects"][1]["time_entries"].append({
            "start_time": day1.replace(hour=12).isoformat(),
            "end_time": (day1.replace(hour=12) + timedelta(hours=2)).isoformat()
        })
        self.tracker._save_data()

        report = self.tracker.generate_main_project_report(main_proj)

        # Check for key information
        self.assertIn(f"# Detailed Report for Main Project: {main_proj}", report)
        self.assertIn("Total recorded time:** 3:30:00", report)
        self.assertIn("Number of sub-projects:** 2", report)
        self.assertIn("Total work sessions:** 3", report)
        self.assertIn("Average session duration:** 1:10:00", report)
        self.assertIn("## Weekday Distribution", report)
        self.assertIn("- **Monday**: 3:00:00 (85.7%)", report) # 1h + 2h = 3h of 3.5h total
        self.assertIn("- **Tuesday**: 0:30:00 (14.3%)", report) # 0.5h of 3.5h total
        self.assertIn("## Sub-Project Breakdown", report)
        self.assertIn("- **Sub 2**: 2:00:00 (1 sessions, 57.1%)", report) # 2h of 3.5h total
        self.assertIn("- **Sub 1**: 1:30:00 (2 sessions, 42.9%)", report) # 1.5h of 3.5h total

# Run the tests if the file is called directly
if __name__ == '__main__':
    unittest.main()