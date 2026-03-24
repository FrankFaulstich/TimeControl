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
        
        # Spyne performs introspection on service class attributes.
        # MagicMock objects can confuse Spyne (e.g., by having __get__, looking like descriptors).
        # We use a simple wrapper to hide the mock from Spyne's introspection.
        class TrackerWrapper:
            def __init__(self):
                self._mock = MagicMock()
            def __getattr__(self, name):
                return getattr(self._mock, name)
        
        cls.MockTimeTrackerClass.return_value = TrackerWrapper()
        
        # Attempt to import the server module
        try:
            import TimeTrackerSOAP_Server
            cls.soap_server = TimeTrackerSOAP_Server
        except ImportError:
            cls.tt_patcher.stop()
            raise unittest.SkipTest("Konnte TimeTrackerSOAP_Server nicht importieren.")
        except SystemExit:
            cls.tt_patcher.stop()
            raise unittest.SkipTest("Spyne nicht installiert oder Importfehler in TimeTrackerSOAP_Server")

    @classmethod
    def tearDownClass(cls):
        cls.tt_patcher.stop()

    def setUp(self):
        # Reset mock for every test
        # TimeControlService.tracker is an instance of our MockTimeTrackerClass
        self.service = self.soap_server.TimeControlService
        # The tracker is now our Wrapper. Access the underlying mock for assertions.
        self.mock_tracker = self.service.tracker._mock
        self.mock_tracker.reset_mock()
        
        # Context object for Spyne methods (often needed, mocked here)
        self.ctx = MagicMock()

    def test_get_version(self):
        self.mock_tracker.get_version.return_value = "1.2.3"
        result = self.service.get_version(self.ctx)
        self.assertEqual(result, "1.2.3")
        self.mock_tracker.get_version.assert_called_once()

    def test_add_main_project(self):
        result = self.service.add_main_project(self.ctx, "Test Project")
        self.mock_tracker.add_main_project.assert_called_with("Test Project")
        self.assertTrue(result)

    def test_list_main_projects(self):
        # Setup mock return values
        self.mock_tracker.list_main_projects.return_value = [
            {'main_project_name': 'Project A', 'status': 'open'},
            {'main_project_name': 'Project B', 'status': 'closed'}
        ]
        
        # Call the SOAP method
        results = self.service.list_main_projects(self.ctx, 'all')
        
        # Verify the call
        self.mock_tracker.list_main_projects.assert_called_with('all')
        
        # Verify conversion to Spyne models
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].main_project_name, 'Project A')
        self.assertEqual(results[0].status, 'open')
        self.assertIsInstance(results[0], self.soap_server.MainProjectModel)

    def test_demote_main_project(self):
        self.mock_tracker.demote_main_project.return_value = (True, "Demoted successfully")
        
        result = self.service.demote_main_project(self.ctx, "Old", "NewParent")
        
        self.mock_tracker.demote_main_project.assert_called_with("Old", "NewParent")
        self.assertIsInstance(result, self.soap_server.OperationResultModel)
        self.assertTrue(result.success)
        self.assertEqual(result.message, "Demoted successfully")

    def test_list_sub_projects(self):
        self.mock_tracker.list_sub_projects.return_value = [
            {'main_project_name': 'Main', 'sub_project_name': 'Sub 1', 'status': 'open'}
        ]
        
        results = self.service.list_sub_projects(self.ctx, 'Main', 'open')
        
        self.mock_tracker.list_sub_projects.assert_called_with('Main', 'open')
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].sub_project_name, 'Sub 1')
        self.assertIsInstance(results[0], self.soap_server.SubProjectModel)

    def test_start_work(self):
        self.mock_tracker.start_work.return_value = True
        self.assertTrue(self.service.start_work(self.ctx, "Main", "Sub"))
        self.mock_tracker.start_work.assert_called_with("Main", "Sub")

    def test_stop_work(self):
        self.mock_tracker.stop_work.return_value = False
        self.assertFalse(self.service.stop_work(self.ctx))
        self.mock_tracker.stop_work.assert_called_once()

    def test_get_current_work_active(self):
        self.mock_tracker.get_current_work.return_value = {
            'main_project_name': 'M',
            'sub_project_name': 'S',
            'start_time': '2023-01-01'
        }
        
        result = self.service.get_current_work(self.ctx)
        
        self.assertIsInstance(result, self.soap_server.CurrentWorkModel)
        self.assertEqual(result.main_project_name, 'M')
        self.assertEqual(result.sub_project_name, 'S')

    def test_get_current_work_none(self):
        self.mock_tracker.get_current_work.return_value = None
        result = self.service.get_current_work(self.ctx)
        self.assertIsNone(result)

    def test_generate_daily_report_valid_date(self):
        self.mock_tracker.generate_daily_report.return_value = "Report"
        self.service.generate_daily_report(self.ctx, "2025-10-20")
        self.mock_tracker.generate_daily_report.assert_called_with(date(2025, 10, 20))

    def test_generate_daily_report_invalid_date(self):
        result = self.service.generate_daily_report(self.ctx, "not-a-date")
        self.assertTrue(result.startswith("Fehler"))
        self.mock_tracker.generate_daily_report.assert_not_called()

    def test_generate_date_range_report(self):
        self.mock_tracker.generate_date_range_report.return_value = "Range Report"
        self.service.generate_date_range_report(self.ctx, "2025-01-01", "2025-01-31")
        self.mock_tracker.generate_date_range_report.assert_called_with(date(2025, 1, 1), date(2025, 1, 31))

if __name__ == '__main__':
    unittest.main()