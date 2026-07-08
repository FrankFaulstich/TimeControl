import io
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

        # Real spyne dispatches @rpc methods as unbound functions on the
        # service *class*; it never instantiates TimeControlService and
        # never sets a 'service' attribute on ctx. Per-request state is
        # carried via ctx.udc instead (see TimeTrackerSOAP_Server.py's
        # 'method_call' event listener), so we mock that directly here.
        self.mock_tracker = self.MockTimeTrackerClass.return_value

        # Context object for Spyne methods (often needed, mocked here)
        self.ctx = MagicMock()
        self.ctx.udc = self.mock_tracker

    def test_get_version(self):
        self.mock_tracker.get_version.return_value = "1.2.3"
        result = self.soap_server.TimeControlService.get_version(self.ctx)
        self.assertEqual(result, "1.2.3")
        self.mock_tracker.get_version.assert_called_once()

    def test_add_main_project(self):
        result = self.soap_server.TimeControlService.add_main_project(self.ctx, "Test Project")
        self.mock_tracker.add_main_project.assert_called_with("Test Project")
        self.assertTrue(result)

    def test_add_task(self):
        self.mock_tracker.add_task.return_value = True
        result = self.soap_server.TimeControlService.add_task(
            self.ctx, "Main", "Sub", "2025-12-24", True, "Note"
        )
        self.mock_tracker.add_task.assert_called_with(
            "Main", "Sub", "2025-12-24", True, "Note", False, "daily", 1
        )
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

    def test_list_tasks(self):
        self.mock_tracker.list_tasks.return_value = [
            {'main_project_name': 'Main', 'task_name': 'Sub 1', 'status': 'open'}
        ]
        
        results = self.soap_server.TimeControlService.list_tasks(self.ctx, 'Main', 'open')
        
        self.mock_tracker.list_tasks.assert_called_with('Main', 'open')
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].task_name, 'Sub 1')
        self.assertIsInstance(results[0], self.soap_server.TaskModel)

    def test_update_task(self):
        self.mock_tracker.update_task.return_value = True
        result = self.soap_server.TimeControlService.update_task(
            self.ctx, "Main", "Old", "New", "2025-01-01", True, "Note", "done"
        )
        self.mock_tracker.update_task.assert_called_with(
            "Main", "Old", "New", "2025-01-01", True, "Note", "done", None, None, None
        )
        self.assertTrue(result)

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
            'task_name': 'S',
            'start_time': '2023-01-01'
        }
        
        result = self.soap_server.TimeControlService.get_current_work(self.ctx)
        
        self.assertIsInstance(result, self.soap_server.CurrentWorkModel)
        self.assertEqual(result.main_project_name, 'M')
        self.assertEqual(result.task_name, 'S')

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


class TestTimeTrackerSOAP_ServerEndToEnd(unittest.TestCase):
    """
    Drives a real spyne WsgiApplication end-to-end with real SOAP envelopes,
    instead of mocking `ctx`. This exercises spyne's actual context/dispatch
    machinery (spyne.service.ServiceBase.call_wrapper calling the unbound
    @rpc functions), which is what caught that TimeControlService relied on
    a per-request `ctx.service` instance spyne never creates. The
    TestTimeTrackerSOAP_Server tests above call methods directly with a
    hand-built ctx and would not catch a regression of that bug.
    """

    @classmethod
    def setUpClass(cls):
        try:
            import TimeTrackerSOAP_Server
            from spyne import Application
            from spyne.protocol.soap import Soap11
            from spyne.server.wsgi import WsgiApplication
        except ImportError:
            raise unittest.SkipTest("Could not import TimeTrackerSOAP_Server or spyne.")
        except SystemExit:
            raise unittest.SkipTest("Spyne not installed or import error in TimeTrackerSOAP_Server")

        cls.soap_server = TimeTrackerSOAP_Server
        cls.wsgi_app = WsgiApplication(Application(
            [TimeTrackerSOAP_Server.TimeControlService],
            tns='spyne.examples.timecontrol',
            in_protocol=Soap11(validator='lxml'),
            out_protocol=Soap11(),
        ))

    def setUp(self):
        # Patch the name as bound inside TimeTrackerSOAP_Server (it was
        # imported there via `from tt.TimeTracker import TimeTracker`), so
        # the real TimeTracker (with its file I/O) never runs.
        patcher = patch('TimeTrackerSOAP_Server.TimeTracker')
        self.MockTimeTrackerClass = patcher.start()
        self.addCleanup(patcher.stop)
        self.mock_tracker = self.MockTimeTrackerClass.return_value

    def _post_soap(self, body_xml):
        envelope = (
            '<soap11env:Envelope '
            'xmlns:soap11env="http://schemas.xmlsoap.org/soap/envelope/" '
            'xmlns:tns="spyne.examples.timecontrol">'
            '<soap11env:Body>%s</soap11env:Body>'
            '</soap11env:Envelope>' % body_xml
        ).encode('utf-8')

        environ = {
            'REQUEST_METHOD': 'POST',
            'PATH_INFO': '/',
            'CONTENT_TYPE': 'text/xml; charset=utf-8',
            'CONTENT_LENGTH': str(len(envelope)),
            'wsgi.input': io.BytesIO(envelope),
            'wsgi.url_scheme': 'http',
            'SERVER_NAME': 'localhost',
            'SERVER_PORT': '8600',
        }

        captured = {}
        def start_response(status, headers):
            captured['status'] = status
            captured['headers'] = headers

        response_body = b''.join(self.wsgi_app(environ, start_response))
        return captured['status'], response_body

    def test_get_version_via_real_wsgi_dispatch(self):
        self.mock_tracker.get_version.return_value = "9.9.9"

        status, body = self._post_soap('<tns:get_version/>')

        self.assertTrue(status.startswith('200'), "status=%r body=%r" % (status, body))
        self.assertNotIn(b'Fault', body)
        self.assertIn(b'9.9.9', body)
        # Proves the 'method_call' event actually constructed a tracker for
        # this request -- under the old ctx.service-based code, spyne never
        # instantiated TimeControlService, so TimeTracker() was never called.
        self.MockTimeTrackerClass.assert_called_once()
        self.mock_tracker.get_version.assert_called_once()

    def test_add_main_project_via_real_wsgi_dispatch(self):
        status, body = self._post_soap(
            '<tns:add_main_project>'
            '<tns:main_project_name>Acme</tns:main_project_name>'
            '</tns:add_main_project>'
        )

        self.assertTrue(status.startswith('200'), "status=%r body=%r" % (status, body))
        self.assertNotIn(b'Fault', body)
        self.mock_tracker.add_main_project.assert_called_once_with("Acme")


if __name__ == '__main__':
    unittest.main()