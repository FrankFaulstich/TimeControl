import unittest
from unittest.mock import MagicMock, patch
import sys
import os
from datetime import date

# Add parent directory to path to import modules from root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

class TestTimeTrackerSOAP_Server(unittest.TestCase):
    
    @classmethod
    def setUpClass(cls):
        """
        Prepares the environment for testing the SOAP server.
        We patch TimeTracker to avoid file I/O during import and testing.
        """
        # Patch the TimeTracker class where it is defined
        cls.tt_patcher = patch('tt.TimeTracker.TimeTracker')
        cls.MockTimeTrackerClass = cls.tt_patcher.start()
        
        # Attempt to import the server module
        try:
            import TimeTrackerSOAP_Server
            cls.soap_server = TimeTrackerSOAP_Server
        except ImportError:
            cls.tt_patcher.stop()
            raise unittest.SkipTest("Could not import TimeTrackerSOAP_Server.")
        except SystemExit:
            cls.tt_patcher.stop()
            raise unittest.SkipTest("Spyne not installed or import error in TimeTrackerSOAP_Server")

    @classmethod
    def tearDownClass(cls):
        cls.tt_patcher.stop()

    def setUp(self):
        # Reset mock for every test
        self.MockTimeTrackerClass.reset_mock()

        # Create a service instance. Its __init__ will create an instance of our mocked TimeTracker.
        self.service_instance = self.soap_server.TimeControlService()
        
        # This is the mock instance we want to configure and assert against.
        self.mock_tracker = self.service_instance.tracker
        
        # Context object for Spyne methods (often needed, mocked here)
        # The context's 'service' attribute must point to our instance.
        self.ctx = MagicMock()
        self.ctx.service = self.service_instance

    def test_get_version(self):
        self.mock_tracker.get_version.return_value = "1.2.3"
        result = self.soap_server.TimeControlService.get_version(self.ctx)
        self.assertEqual(result, "1.2.3")
        self.mock_tracker.get_version.assert_called_once()

    def test_add_main_project(self):
        result = self.soap_server.TimeControlService.add_main_project(self.ctx, "Test Project")
        self.mock_tracker.add_main_project.assert_called_with("Test Project")
        self.assertTrue(result)

    def test_list_main_projects(self):
        # Setup mock return values
        self.mock_tracker.list_main_projects.return_value = [
            {'main_project_name': 'Project A', 'status': 'open'},
            {'main_project_name': 'Project B', 'status': 'closed'}
        ]
        
        # Call the SOAP method
        results = self.soap_server.TimeControlService.list_main_projects(self.ctx, 'all')
        
        # Verify the call
        self.mock_tracker.list_main_projects.assert_called_with('all')
        
        # Verify conversion to Spyne models
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].main_project_name, 'Project A')
        self.assertEqual(results[0].status, 'open')
        self.assertIsInstance(results[0], self.soap_server.MainProjectModel)

    def test_demote_main_project(self):
        self.mock_tracker.demote_main_project.return_value = (True, "Demoted successfully")
        
        result = self.soap_server.TimeControlService.demote_main_project(self.ctx, "Old", "NewParent")
        
        self.mock_tracker.demote_main_project.assert_called_with("Old", "NewParent")
        self.assertIsInstance(result, self.soap_server.OperationResultModel)
        self.assertTrue(result.success)
        self.assertEqual(result.message, "Demoted successfully")

    def test_list_sub_projects(self):
        self.mock_tracker.list_sub_projects.return_value = [
            {'main_project_name': 'Main', 'sub_project_name': 'Sub 1', 'status': 'open'}
        ]
        
        results = self.soap_server.TimeControlService.list_sub_projects(self.ctx, 'Main', 'open')
        
        self.mock_tracker.list_sub_projects.assert_called_with('Main', 'open')
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].sub_project_name, 'Sub 1')
        self.assertIsInstance(results[0], self.soap_server.SubProjectModel)

    def test_start_work(self):
        self.mock_tracker.start_work.return_value = True
        self.assertTrue(self.soap_server.TimeControlService.start_work(self.ctx, "Main", "Sub"))
        self.mock_tracker.start_work.assert_called_with("Main", "Sub")

    def test_stop_work(self):
        self.mock_tracker.stop_work.return_value = False
        self.assertFalse(self.soap_server.TimeControlService.stop_work(self.ctx))
        self.mock_tracker.stop_work.assert_called_once()

    def test_get_current_work_active(self):
        self.mock_tracker.get_current_work.return_value = {
            'main_project_name': 'M',
            'sub_project_name': 'S',
            'start_time': '2023-01-01'
        }
        
        result = self.soap_server.TimeControlService.get_current_work(self.ctx)
        
        self.assertIsInstance(result, self.soap_server.CurrentWorkModel)
        self.assertEqual(result.main_project_name, 'M')
        self.assertEqual(result.sub_project_name, 'S')

    def test_get_current_work_none(self):
        self.mock_tracker.get_current_work.return_value = None
        result = self.soap_server.TimeControlService.get_current_work(self.ctx)
        self.assertIsNone(result)

    def test_generate_daily_report_valid_date(self):
        self.mock_tracker.generate_daily_report.return_value = "Report"
        self.soap_server.TimeControlService.generate_daily_report(self.ctx, "2025-10-20")
        self.mock_tracker.generate_daily_report.assert_called_with(date(2025, 10, 20))

    def test_generate_daily_report_invalid_date(self):
        result = self.soap_server.TimeControlService.generate_daily_report(self.ctx, "not-a-date")
        self.assertTrue(result.startswith("Fehler"))
        self.mock_tracker.generate_daily_report.assert_not_called()

    def test_generate_date_range_report(self):
        self.mock_tracker.generate_date_range_report.return_value = "Range Report"
        self.soap_server.TimeControlService.generate_date_range_report(self.ctx, "2025-01-01", "2025-01-31")
        self.mock_tracker.generate_date_range_report.assert_called_with(date(2025, 1, 1), date(2025, 1, 31))

if __name__ == '__main__':
    unittest.main()