import unittest
import unittest.mock
import os
import sys

# Add parent directory to path to import modules from root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import TimeTrackerSL_GUI

class TestStreamlitGUI(unittest.TestCase):
    """
    Unit tests for the Streamlit GUI startup script.
    These tests use mocks to avoid actually starting servers or opening windows.
    """

    @unittest.mock.patch('TimeTrackerSL_GUI.os.path.exists')
    @unittest.mock.patch('TimeTrackerSL_GUI.json.load')
    @unittest.mock.patch('TimeTrackerSL_GUI.subprocess.Popen')
    @unittest.mock.patch('TimeTrackerSL_GUI.webview.create_window')
    @unittest.mock.patch('TimeTrackerSL_GUI.webview.start')
    @unittest.mock.patch('TimeTrackerSL_GUI.time.sleep')
    def test_start_streamlit_server_default_port(self, mock_sleep, mock_webview_start, mock_create_window, mock_popen, mock_json_load, mock_exists):
        """
        Tests if the server and window are created with default values when config is empty.
        """
        # --- Setup ---
        # Simulate that config.json exists but is empty or doesn't contain the port
        mock_exists.return_value = True
        mock_json_load.return_value = {}

        # --- Action ---
        TimeTrackerSL_GUI.start_streamlit_server()

        # --- Assertions ---
        # 1. Check if Streamlit process was started with the default port 8501
        mock_popen.assert_called_once()
        args, _ = mock_popen.call_args
        self.assertIn('8501', args[0])

        # 2. Check if the webview window was created
        mock_create_window.assert_called_once()
        _args, kwargs = mock_create_window.call_args
        self.assertEqual('http://localhost:8501', _args[1])
        self.assertEqual(kwargs.get('width'), 800) # Check default width

        # 3. Check if the webview event loop was started
        mock_webview_start.assert_called_once()


if __name__ == '__main__':
    unittest.main()