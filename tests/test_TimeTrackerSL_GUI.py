import unittest
import unittest.mock
import os
import sys
import types

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
    @unittest.mock.patch('TimeTrackerSL_GUI.webview.screens', [types.SimpleNamespace(x=0, y=0, width=1920, height=1080)], create=True)
    @unittest.mock.patch('TimeTrackerSL_GUI.webview.create_window')
    @unittest.mock.patch('TimeTrackerSL_GUI.webview.start')
    @unittest.mock.patch('TimeTrackerSL_GUI.time.sleep')
    def test_start_streamlit_server_restores_window_position(self, mock_sleep, mock_webview_start, mock_create_window, mock_popen, mock_json_load, mock_exists):
        """
        Regression test: the window's last saved position (window_x/window_y)
        must be restored on startup, not just its size - as long as that
        position is still on a currently connected screen.
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

    @unittest.mock.patch('TimeTrackerSL_GUI.os.path.exists')
    @unittest.mock.patch('TimeTrackerSL_GUI.json.load')
    @unittest.mock.patch('TimeTrackerSL_GUI.subprocess.Popen')
    @unittest.mock.patch('TimeTrackerSL_GUI.webview.screens', [
        types.SimpleNamespace(x=0, y=0, width=1512, height=982),
        types.SimpleNamespace(x=0, y=982, width=1920, height=1080),
    ], create=True)
    @unittest.mock.patch('TimeTrackerSL_GUI.webview.create_window')
    @unittest.mock.patch('TimeTrackerSL_GUI.webview.start')
    @unittest.mock.patch('TimeTrackerSL_GUI.time.sleep')
    def test_start_streamlit_server_ignores_offscreen_saved_position(self, mock_sleep, mock_webview_start, mock_create_window, mock_popen, mock_json_load, mock_exists):
        """
        Regression test: a saved window position from a monitor arrangement
        that no longer applies (e.g. an external display that was unplugged)
        must not be handed to pywebview as-is. Passing an (x, y) that isn't on
        any currently connected screen crashes the Cocoa backend outright -
        it can't resolve an NSScreen for the point and dies with
        "AttributeError: 'NoneType' object has no attribute 'frame'" inside
        its own window-move handler. Falling back to (None, None) lets
        pywebview pick a safe default placement instead.
        """
        mock_exists.return_value = True
        mock_json_load.return_value = {
            'window_width': 1200,
            'window_height': 800,
            'window_x': 265,
            'window_y': -662,
        }

        TimeTrackerSL_GUI.start_streamlit_server()

        mock_create_window.assert_called_once()
        _args, kwargs = mock_create_window.call_args
        self.assertIsNone(kwargs.get('x'))
        self.assertIsNone(kwargs.get('y'))

    @unittest.mock.patch('TimeTrackerSL_GUI.os.path.exists')
    @unittest.mock.patch('TimeTrackerSL_GUI.json.load')
    @unittest.mock.patch('TimeTrackerSL_GUI.subprocess.Popen')
    @unittest.mock.patch('TimeTrackerSL_GUI.webview.screens', [], create=True)
    @unittest.mock.patch('TimeTrackerSL_GUI.webview.create_window')
    @unittest.mock.patch('TimeTrackerSL_GUI.webview.start')
    @unittest.mock.patch('TimeTrackerSL_GUI.time.sleep')
    def test_start_streamlit_server_handles_no_screens_reported(self, mock_sleep, mock_webview_start, mock_create_window, mock_popen, mock_json_load, mock_exists):
        """No connected screens at all must also fall back safely, not crash."""
        mock_exists.return_value = True
        mock_json_load.return_value = {'window_x': 265, 'window_y': 130}

        TimeTrackerSL_GUI.start_streamlit_server()

        mock_create_window.assert_called_once()
        _args, kwargs = mock_create_window.call_args
        self.assertIsNone(kwargs.get('x'))
        self.assertIsNone(kwargs.get('y'))

    @unittest.mock.patch('TimeTrackerSL_GUI.os.path.exists')
    @unittest.mock.patch('TimeTrackerSL_GUI.json.load')
    @unittest.mock.patch('TimeTrackerSL_GUI.subprocess.Popen')
    @unittest.mock.patch('TimeTrackerSL_GUI.webview.create_window')
    @unittest.mock.patch('TimeTrackerSL_GUI.webview.start')
    @unittest.mock.patch('TimeTrackerSL_GUI.time.sleep')
    def test_start_streamlit_server_starts_mcp_server_when_enabled(self, mock_sleep, mock_webview_start, mock_create_window, mock_popen, mock_json_load, mock_exists):
        """
        The optional MCP server must only be started as a second subprocess
        when explicitly enabled in config.json, using the configured port.
        """
        mock_exists.return_value = True
        mock_json_load.return_value = {'mcp_server_enabled': True, 'mcp_port': 8765}

        TimeTrackerSL_GUI.start_streamlit_server()

        self.assertEqual(mock_popen.call_count, 2)
        mcp_call_args = mock_popen.call_args_list[1][0][0]
        self.assertIn('TimeTrackerMCP_Server.py', mcp_call_args)

    @unittest.mock.patch('TimeTrackerSL_GUI.os.path.exists')
    @unittest.mock.patch('TimeTrackerSL_GUI.json.load')
    @unittest.mock.patch('TimeTrackerSL_GUI.subprocess.Popen')
    @unittest.mock.patch('TimeTrackerSL_GUI.webview.create_window')
    @unittest.mock.patch('TimeTrackerSL_GUI.webview.start')
    @unittest.mock.patch('TimeTrackerSL_GUI.time.sleep')
    def test_start_streamlit_server_skips_mcp_server_by_default(self, mock_sleep, mock_webview_start, mock_create_window, mock_popen, mock_json_load, mock_exists):
        """The MCP server must not be started unless explicitly enabled."""
        mock_exists.return_value = True
        mock_json_load.return_value = {}

        TimeTrackerSL_GUI.start_streamlit_server()

        mock_popen.assert_called_once()

    @unittest.mock.patch('TimeTrackerSL_GUI.os.path.exists')
    @unittest.mock.patch('TimeTrackerSL_GUI.json.load')
    @unittest.mock.patch('TimeTrackerSL_GUI.subprocess.Popen')
    @unittest.mock.patch('TimeTrackerSL_GUI.webview.create_window')
    @unittest.mock.patch('TimeTrackerSL_GUI.webview.start')
    @unittest.mock.patch('TimeTrackerSL_GUI.time.sleep')
    def test_start_streamlit_server_skips_mcp_server_for_stdio_transport(self, mock_sleep, mock_webview_start, mock_create_window, mock_popen, mock_json_load, mock_exists):
        """
        With the stdio transport, an MCP client (e.g. Claude Desktop) spawns
        TimeTrackerMCP_Server.py itself and talks to it over stdin/stdout, so
        the GUI must not also launch it as a background subprocess - even if
        'mcp_server_enabled' is true.
        """
        mock_exists.return_value = True
        mock_json_load.return_value = {'mcp_server_enabled': True, 'mcp_transport': 'stdio', 'mcp_port': 8765}

        TimeTrackerSL_GUI.start_streamlit_server()

        mock_popen.assert_called_once()

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