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

    @unittest.mock.patch('TimeTrackerSL_GUI.os.path.exists')
    @unittest.mock.patch('TimeTrackerSL_GUI.json.load')
    @unittest.mock.patch('TimeTrackerSL_GUI.subprocess.Popen')
    @unittest.mock.patch('TimeTrackerSL_GUI.webview.create_window')
    @unittest.mock.patch('TimeTrackerSL_GUI.webview.start')
    @unittest.mock.patch('TimeTrackerSL_GUI.time.sleep')
    def test_start_streamlit_server_restores_window_position(self, mock_sleep, mock_webview_start, mock_create_window, mock_popen, mock_json_load, mock_exists):
        """
        Regression test: the window's last saved position (window_x/window_y)
        must be restored on startup, not just its size.
        """
        # --- Setup ---
        mock_exists.return_value = True
        mock_json_load.return_value = {
            'window_width': 1200,
            'window_height': 800,
            'window_x': 265,
            'window_y': 130,
        }

        # --- Action ---
        TimeTrackerSL_GUI.start_streamlit_server()

        # --- Assertions ---
        mock_create_window.assert_called_once()
        _args, kwargs = mock_create_window.call_args
        self.assertEqual(kwargs.get('width'), 1200)
        self.assertEqual(kwargs.get('height'), 800)
        self.assertEqual(kwargs.get('x'), 265)
        self.assertEqual(kwargs.get('y'), 130)

    def test_save_window_state_persists_position(self):
        """
        Regression test: closing the window must persist both size AND
        position to config.json (a prior refactor accidentally dropped
        window_x/window_y, so only the size was ever restored).
        """
        mock_window = unittest.mock.MagicMock()
        mock_window.width = 1024
        mock_window.height = 768
        mock_window.x = 50
        mock_window.y = 75

        with unittest.mock.patch('TimeTrackerSL_GUI.os.path.exists', return_value=True), \
             unittest.mock.patch('builtins.open', unittest.mock.mock_open()), \
             unittest.mock.patch('TimeTrackerSL_GUI.json.load', return_value={}), \
             unittest.mock.patch('TimeTrackerSL_GUI.json.dump') as mock_json_dump:
            TimeTrackerSL_GUI.save_window_state(mock_window)

        saved_config = mock_json_dump.call_args[0][0]
        self.assertEqual(saved_config['window_width'], 1024)
        self.assertEqual(saved_config['window_height'], 768)
        self.assertEqual(saved_config['window_x'], 50)
        self.assertEqual(saved_config['window_y'], 75)


if __name__ == '__main__':
    unittest.main()