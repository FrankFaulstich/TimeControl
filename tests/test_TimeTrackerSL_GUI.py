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
    @unittest.mock.patch('TimeTrackerSL_GUI.webview.create_window')
    @unittest.mock.patch('TimeTrackerSL_GUI.webview.start')
    @unittest.mock.patch('TimeTrackerSL_GUI.time.sleep')
    def test_start_streamlit_server_restores_window_position(self, mock_sleep, mock_webview_start, mock_create_window, mock_popen, mock_json_load, mock_exists):
        """
        Regression test: the window's last saved position (window_x/window_y)
        must be restored on startup, not just its size - as long as that
        position is still on a currently connected screen. sys.platform on
        the test runner may or may not be 'darwin'; either way
        _safe_window_position must pass an on-main-screen position through
        unchanged (on non-macOS it's always a no-op passthrough; on macOS
        (0, 0, 1512, 982) is squarely inside the real main screen).
        """
        # --- Setup ---
        mock_exists.return_value = True
        mock_json_load.return_value = {
            'window_width': 1200,
            'window_height': 800,
            'window_x': 100,
            'window_y': 100,
        }

        # --- Action ---
        TimeTrackerSL_GUI.start_streamlit_server()

        # --- Assertions ---
        mock_create_window.assert_called_once()
        _args, kwargs = mock_create_window.call_args
        self.assertEqual(kwargs.get('width'), 1200)
        self.assertEqual(kwargs.get('height'), 800)
        self.assertEqual(kwargs.get('x'), 100)
        self.assertEqual(kwargs.get('y'), 100)

    # --- _safe_window_position: direct unit tests ---
    #
    # pywebview's Cocoa backend expresses x/y relative to the *main*
    # screen's own top-left corner, y increasing downward (see move()/
    # get_position() in webview/platforms/cocoa.py) - not the raw AppKit
    # frame coordinates webview.screens reports (origin at the bottom-left
    # of the whole desktop, y increasing upward). These tests fake out
    # AppKit directly (rather than webview.screens) so they can exercise
    # that exact conversion with real, verified monitor geometry: a 1512x982
    # main screen at (0, 0) and a 2560x1440 second screen at (-415, 982) -
    # i.e. positioned *above* the main screen and offset slightly to the
    # left, which is what produces the legitimate negative y values a naive
    # same-space comparison mistakes for "off-screen".

    @staticmethod
    def _fake_appkit(main_frame, screen_frames):
        """Builds a minimal fake `AppKit` good enough to stand in for NSScreen/NSMakeRect/NSIntersectsRect."""
        def make_rect(x, y, w, h):
            return types.SimpleNamespace(
                origin=types.SimpleNamespace(x=x, y=y),
                size=types.SimpleNamespace(width=w, height=h),
            )

        def intersects(a, b):
            return not (
                a.origin.x + a.size.width <= b.origin.x
                or b.origin.x + b.size.width <= a.origin.x
                or a.origin.y + a.size.height <= b.origin.y
                or b.origin.y + b.size.height <= a.origin.y
            )

        def fake_screen(frame):
            screen = unittest.mock.MagicMock()
            screen.frame.return_value = frame
            return screen

        fake = unittest.mock.MagicMock()
        fake.NSMakeRect.side_effect = make_rect
        fake.NSIntersectsRect.side_effect = intersects
        fake.NSScreen.mainScreen.return_value.frame.return_value = make_rect(*main_frame)
        fake.NSScreen.screens.return_value = [fake_screen(make_rect(*fr)) for fr in screen_frames]
        return fake

    def test_safe_window_position_noop_on_non_darwin(self):
        """Not a macOS-specific concept there, and no AppKit to talk to - always pass x/y through unchanged."""
        with unittest.mock.patch('TimeTrackerSL_GUI.sys.platform', 'win32'):
            result = TimeTrackerSL_GUI._safe_window_position(265, -662, 1200, 800)
        self.assertEqual(result, (265, -662))

    def test_safe_window_position_none_input_passes_through(self):
        result = TimeTrackerSL_GUI._safe_window_position(None, None, 1200, 800)
        self.assertEqual(result, (None, None))

    def test_safe_window_position_on_main_screen(self):
        fake_appkit = self._fake_appkit(
            main_frame=(0, 0, 1512, 982),
            screen_frames=[(0, 0, 1512, 982), (-415, 982, 2560, 1440)],
        )
        with unittest.mock.patch('TimeTrackerSL_GUI.sys.platform', 'darwin'), \
             unittest.mock.patch.dict(sys.modules, {'AppKit': fake_appkit}):
            result = TimeTrackerSL_GUI._safe_window_position(100, 100, 1200, 800)
        self.assertEqual(result, (100, 100))

    def test_safe_window_position_on_second_screen_above_main(self):
        """
        Regression test: this is the exact (265, -662) value from the
        original bug report. A naive comparison against webview.screens'
        raw coordinates rejects it as "off-screen" every time - but given
        this real monitor arrangement (second screen above and slightly
        left of the main one), it is a perfectly valid, currently-visible
        position spanning both screens, and must be preserved.
        """
        fake_appkit = self._fake_appkit(
            main_frame=(0, 0, 1512, 982),
            screen_frames=[(0, 0, 1512, 982), (-415, 982, 2560, 1440)],
        )
        with unittest.mock.patch('TimeTrackerSL_GUI.sys.platform', 'darwin'), \
             unittest.mock.patch.dict(sys.modules, {'AppKit': fake_appkit}):
            result = TimeTrackerSL_GUI._safe_window_position(265, -662, 1200, 800)
        self.assertEqual(result, (265, -662))

    def test_safe_window_position_ignores_offscreen_saved_position(self):
        """
        A position from a monitor arrangement that no longer applies (e.g.
        the second screen above the main one, from the previous test, has
        since been unplugged) must not be handed to pywebview as-is -
        passing an (x, y) that isn't on any currently connected screen
        crashes the Cocoa backend outright on window creation. Falling back
        to (None, None) lets pywebview pick a safe default placement
        instead.

        y=-1800 (deep within where the removed second screen used to be)
        rather than the -662 used above: with only the main screen left, the
        window's bottom edge at -662 would still dip down far enough to
        overlap the top of the main screen (800px is a lot of window), which
        would make it a bad example of "genuinely off-screen".
        """
        fake_appkit = self._fake_appkit(
            main_frame=(0, 0, 1512, 982),
            screen_frames=[(0, 0, 1512, 982)],  # second screen no longer connected
        )
        with unittest.mock.patch('TimeTrackerSL_GUI.sys.platform', 'darwin'), \
             unittest.mock.patch.dict(sys.modules, {'AppKit': fake_appkit}):
            result = TimeTrackerSL_GUI._safe_window_position(265, -1800, 1200, 800)
        self.assertEqual(result, (None, None))

    def test_safe_window_position_handles_no_screens_reported(self):
        """No connected screens at all must also fall back safely, not crash."""
        fake_appkit = self._fake_appkit(main_frame=(0, 0, 1512, 982), screen_frames=[])
        with unittest.mock.patch('TimeTrackerSL_GUI.sys.platform', 'darwin'), \
             unittest.mock.patch.dict(sys.modules, {'AppKit': fake_appkit}):
            result = TimeTrackerSL_GUI._safe_window_position(100, 100, 1200, 800)
        self.assertEqual(result, (None, None))

    def test_safe_window_position_swallows_errors(self):
        """Purely defensive - any AppKit failure must fall back, not crash startup."""
        fake_appkit = unittest.mock.MagicMock()
        fake_appkit.NSScreen.mainScreen.side_effect = RuntimeError("boom")
        with unittest.mock.patch('TimeTrackerSL_GUI.sys.platform', 'darwin'), \
             unittest.mock.patch.dict(sys.modules, {'AppKit': fake_appkit}):
            result = TimeTrackerSL_GUI._safe_window_position(100, 100, 1200, 800)
        self.assertEqual(result, (None, None))

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

    def test_set_macos_dock_icon_noop_on_non_darwin(self):
        """
        The Dock-icon override is a macOS-only concept (AppKit doesn't exist
        elsewhere); it must not even attempt to import AppKit on other
        platforms.
        """
        fake_appkit = unittest.mock.MagicMock()
        with unittest.mock.patch('TimeTrackerSL_GUI.sys.platform', 'win32'), \
             unittest.mock.patch.dict(sys.modules, {'AppKit': fake_appkit}):
            TimeTrackerSL_GUI._set_macos_dock_icon()

        fake_appkit.NSImage.alloc.assert_not_called()
        fake_appkit.NSApplication.sharedApplication.assert_not_called()

    def test_set_macos_dock_icon_sets_icon_on_darwin(self):
        """
        On macOS, the Dock icon shown while this app runs must be replaced
        with our own icon (see _set_macos_dock_icon's docstring for why:
        launching via python.org's python3 otherwise shows the Python.app
        rocket-ship icon instead).
        """
        fake_appkit = unittest.mock.MagicMock()
        fake_image = fake_appkit.NSImage.alloc.return_value.initByReferencingFile_.return_value
        fake_image.isValid.return_value = True

        with unittest.mock.patch('TimeTrackerSL_GUI.sys.platform', 'darwin'), \
             unittest.mock.patch.dict(sys.modules, {'AppKit': fake_appkit}):
            TimeTrackerSL_GUI._set_macos_dock_icon()

        fake_appkit.NSImage.alloc.return_value.initByReferencingFile_.assert_called_once_with(TimeTrackerSL_GUI.ICON_FILE)
        fake_appkit.NSApplication.sharedApplication.return_value.setApplicationIconImage_.assert_called_once_with(fake_image)

    def test_set_macos_dock_icon_swallows_errors(self):
        """Icon setting is purely cosmetic - any failure here must never break startup."""
        fake_appkit = unittest.mock.MagicMock()
        fake_appkit.NSImage.alloc.side_effect = RuntimeError("boom")

        with unittest.mock.patch('TimeTrackerSL_GUI.sys.platform', 'darwin'), \
             unittest.mock.patch.dict(sys.modules, {'AppKit': fake_appkit}):
            TimeTrackerSL_GUI._set_macos_dock_icon()  # must not raise


if __name__ == '__main__':
    unittest.main()