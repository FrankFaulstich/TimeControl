import json
import os
import subprocess
import sys
import webbrowser
try:
    import webview
except ImportError:
    webview = None
import time
import threading

from tt.TimeTracker import TimeTracker

try:
    from update import (check_for_updates, download_update, install_update,
                        clear_update_leftovers, install_exe_update,
                        pending_exe_update, relaunch_frozen)
    UPDATE_AVAILABLE = True
except ImportError:
    UPDATE_AVAILABLE = False

CONFIG_FILE = 'config.json'
ICON_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sl', 'icon.png')


def _is_frozen():
    """Whether this is running as a PyInstaller-bundled executable."""
    return getattr(sys, 'frozen', False)


def _app_root():
    """
    Directory config.json/data.json should be read from and written to.

    In a normal source checkout this is simply this file's own directory -
    today's implicit assumption everywhere in this app, since every
    relative path to these two files already resolves against the
    process's cwd, which is the repo root whenever this script is launched
    the usual way. A PyInstaller-frozen build has no such checkout: this
    script and its bundled resources (sl/, locale/) live inside a temporary
    extraction folder that's recreated (and wiped) on every run, so
    config.json/data.json have to live next to the actual .exe instead, or
    the user's tasks would reset every time they start the app.
    """
    if _is_frozen():
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _run_streamlit_subprocess(port, headless):
    """
    Runs Streamlit in-process for sl/SL_Menu.py.

    Only used when this exe is re-invoked with the '--tc-streamlit-subprocess'
    flag (see start_streamlit_server() below) - a frozen build has no
    separate Python interpreter or 'streamlit' command to hand to
    subprocess.Popen, only this one .exe, so it re-launches itself with this
    flag instead and takes this path rather than the normal GUI flow. A
    non-frozen run is unaffected: it still spawns 'python -m streamlit run'
    as a real subprocess, same as before.
    """
    # Streamlit defaults global.developmentMode to True unless its own
    # config.py's __file__ path contains "site-packages"/"dist-packages" -
    # true for a normal pip install, but never true for a PyInstaller-frozen
    # bundle (its modules live inside an archive, not a real site-packages
    # directory). Left alone, that misdetection makes Streamlit itself
    # reject the --server.port flag below with "server.port does not work
    # when global.developmentMode is true." Overriding it via this env var
    # (Streamlit's own documented STREAMLIT_<SECTION>_<OPTION> convention)
    # is the one override that's read before that check runs, regardless of
    # how the config option would otherwise resolve.
    os.environ['STREAMLIT_GLOBAL_DEVELOPMENT_MODE'] = 'false'
    from streamlit.web import cli as stcli
    base_dir = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    sys.argv = [
        'streamlit', 'run', os.path.join(base_dir, 'sl', 'SL_Menu.py'),
        '--server.port', port, '--server.headless', headless,
    ]
    stcli.main()

def _set_macos_dock_icon():
    """
    Replaces the Dock icon shown while this app runs.

    Launching a script with python.org's python3 goes through its bundled
    "Python.app" as soon as GUI features (like pywebview's Cocoa backend)
    are used, which shows that bundle's own icon - a rocket ship - in the
    Dock. pywebview doesn't offer any way to override this itself (its
    `icon=` option is GTK/QT only), but AppKit does, directly.
    """
    if sys.platform != 'darwin':
        return
    try:
        import AppKit
        image = AppKit.NSImage.alloc().initByReferencingFile_(ICON_FILE)
        if image and image.isValid():
            AppKit.NSApplication.sharedApplication().setApplicationIconImage_(image)
    except Exception:
        pass  # Cosmetic only - never let icon issues break startup.

def save_window_state(window):
    """Saves the current window dimensions and position to config.json.

    This function is typically called when the window is closing. It reads the
    existing configuration, updates it with the window's geometry, and writes
    it back to the file.

    Args:
        window: The pywebview window object whose state is to be saved.

    """
    try:
        config = {}
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)

        config['window_width'] = int(window.width)
        config['window_height'] = int(window.height)
        config['window_x'] = int(window.x)
        config['window_y'] = int(window.y)

        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=4)
    except Exception as e:
        print(f"Error saving window state: {e}")

def _safe_window_position(x, y, width, height):
    """
    Returns (x, y) unchanged if a window of the given size at that position
    would land on (at least partially overlap) a currently connected screen,
    otherwise (None, None) so pywebview falls back to its own default
    placement.

    A position saved from a monitor arrangement that no longer matches -
    most commonly, an external display that has since been disconnected -
    otherwise crashes the Cocoa pywebview backend outright on window
    creation: it can't resolve an NSScreen for a point that isn't on any
    screen, and its own move handler dies with
    "AttributeError: 'NoneType' object has no attribute 'frame'".

    This only does anything on macOS, and deliberately talks to AppKit
    directly rather than using webview.screens: pywebview's Cocoa backend
    expresses x/y relative to the *main* screen's own top-left corner, with
    y increasing downward (see its move()/get_position() in
    webview/platforms/cocoa.py), but webview.screens reports each screen's
    raw AppKit frame - origin at the bottom-left of the whole desktop, y
    increasing upward. Comparing the two directly (as an earlier version of
    this function did) silently rejects perfectly valid positions on any
    monitor other than the main one - in particular, a second monitor placed
    above the main one legitimately produces a *negative* y here, which is
    indistinguishable from "off-screen" without converting coordinate
    spaces first. On any other platform this just returns (x, y) unchanged;
    there's no evidence of the equivalent crash happening there.
    """
    if x is None or y is None:
        return None, None
    if sys.platform != 'darwin':
        return x, y
    try:
        import AppKit
        main = AppKit.NSScreen.mainScreen().frame()
        raw_x = main.origin.x + x
        raw_top = main.origin.y + main.size.height - y
        target = AppKit.NSMakeRect(raw_x, raw_top - height, width, height)
        for screen in AppKit.NSScreen.screens():
            if AppKit.NSIntersectsRect(target, screen.frame()):
                return x, y
        return None, None
    except Exception:
        return None, None

def start_streamlit_server():
    """
    Initializes and runs the pywebview GUI for the Streamlit app.

    This function performs the following steps:
    1. Reads `config.json` to get the configured port and last window geometry.
    2. Starts the Streamlit server as a background subprocess.
    3. Creates a `pywebview` window pointing to the local Streamlit URL.
    4. Sets up an event handler to save the window state upon closing.
    5. Starts a monitoring thread that closes the window if the Streamlit
       server process terminates unexpectedly (e.g., via the Exit button).
    6. Starts the main `pywebview` event loop.
    7. Terminates the Streamlit subprocess when the `pywebview` window is closed.
    """

    port = 8501 # Default Streamlit port
    width = 800
    height = 600
    x = None
    y = None
    view_mode = 'webview'
    mcp_server_enabled = False
    mcp_transport = 'http'
    mcp_port = 8700

    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
                port = config.get('streamlit_port', 8501)
                width = config.get('window_width', 800)
                height = config.get('window_height', 600)
                x = config.get('window_x', None)
                y = config.get('window_y', None)
                view_mode = config.get('view_mode', 'webview')
                mcp_server_enabled = config.get('mcp_server_enabled', False)
                mcp_transport = config.get('mcp_transport', 'http')
                mcp_port = config.get('mcp_port', 8700)
        except (json.JSONDecodeError, IOError) as e:
            print(f"Warning: Could not read config.json. Using default settings. Error: {e}")

    print(f"Starting TimeControl GUI on port {port}...")

    # Determine headless mode based on webview availability
    headless_mode = "true"

    # Use sys.executable to ensure the same python environment is used. A
    # frozen build has no separate interpreter/script to point at here -
    # sys.executable IS this .exe - so it re-invokes itself with a sentinel
    # flag instead (handled at the top of __main__ below) rather than
    # "python -m streamlit run ...".
    if _is_frozen():
        cmd = [sys.executable, '--tc-streamlit-subprocess', str(port), headless_mode]
    else:
        cmd = [sys.executable, "-m", "streamlit", "run", os.path.join("sl", "SL_Menu.py"), "--server.port", str(port), "--server.headless", headless_mode]

    process = subprocess.Popen(cmd)

    # The MCP server is a separate, optional long-running process (like the
    # SOAP server), started here only if the user opted in via config.json.
    # With the stdio transport there is nothing useful to launch here at
    # all - an MCP client (e.g. Claude Desktop) spawns that process itself
    # and talks to it over its stdin/stdout directly.
    mcp_process = None
    if mcp_server_enabled and mcp_transport != 'stdio':
        print(f"Starting TimeControl MCP server on port {mcp_port}...")
        if _is_frozen():
            mcp_process = subprocess.Popen([sys.executable, '--tc-mcp-subprocess'])
        else:
            mcp_process = subprocess.Popen([sys.executable, "TimeTrackerMCP_Server.py"])

    def stop_mcp_server():
        if mcp_process and mcp_process.poll() is None:
            mcp_process.terminate()

    if webview and view_mode == 'webview':
        _set_macos_dock_icon()
        time.sleep(2) # Wait for Streamlit to initialize

        x, y = _safe_window_position(x, y, int(width), int(height))

        window = webview.create_window(
            "Time Control",
            f"http://localhost:{port}",
            width=int(width),
            height=int(height),
            x=x,
            y=y,
            frameless=False
        )
        
        # Save state when closing the window
        window.events.closing += lambda: save_window_state(window)
        
        # Monitor the Streamlit process and close the window if it exits
        def monitor_streamlit(proc, win):
            """Monitors the Streamlit process and closes the window if it exits.

            This function runs in a separate thread. It blocks until the Streamlit
            subprocess terminates, and then destroys the pywebview window. This
            ensures the GUI window closes when the server is stopped from within
            the Streamlit app (e.g., by clicking an 'Exit' button).

            Args:
                proc: The subprocess object for the Streamlit server.
                win: The pywebview window object.

            """
            proc.wait()
            try:
                win.destroy()
            except Exception:
                pass

        monitor_thread = threading.Thread(target=monitor_streamlit, args=(process, window))
        monitor_thread.daemon = True
        monitor_thread.start()

        webview.start()

        if process.poll() is None:
            process.terminate()
        stop_mcp_server()
    else:
        if not webview:
            print("Warning: 'webview' module not found. Opening in system browser instead.")

        time.sleep(2)
        webbrowser.open(f"http://localhost:{port}")
        try:
            process.wait()
        except KeyboardInterrupt:
            if process.poll() is None:
                process.terminate()
        finally:
            stop_mcp_server()

if __name__ == '__main__':
    # Sentinel re-launches: see start_streamlit_server()'s cmd construction
    # and _run_streamlit_subprocess()'s docstring above for why a frozen
    # build needs this instead of a plain subprocess invocation. When
    # present, this process IS that subprocess - run just that and exit,
    # skipping the normal GUI flow below entirely.
    if len(sys.argv) > 1 and sys.argv[1] == '--tc-streamlit-subprocess':
        _run_streamlit_subprocess(sys.argv[2], sys.argv[3])
        sys.exit(0)
    if len(sys.argv) > 1 and sys.argv[1] == '--tc-mcp-subprocess':
        import TimeTrackerMCP_Server
        TimeTrackerMCP_Server.main()
        sys.exit(0)

    if _is_frozen():
        os.chdir(_app_root())

    # A frozen build cannot overwrite the .exe it is running from, so an
    # update downloaded during an earlier session is swapped in here instead.
    # This is the one moment it can be: the sentinel re-launches above have
    # already returned, so no second process has been started from that image
    # yet. Renaming it aside is what Windows does allow - see update.py and
    # .github/workflows/probe-windows-selfupdate.yaml, in the git history.
    #
    # If the relaunch does not come off, carry on rather than exit: the code
    # already in memory is the old version, which is a worse outcome than a
    # restart but a far better one than a program that refuses to open.
    if UPDATE_AVAILABLE and _is_frozen():
        clear_update_leftovers()
        if pending_exe_update() and install_exe_update():
            print("Update installed. Restarting...")
            if relaunch_frozen():
                sys.exit(0)
            print("Could not restart. The update takes effect the next time "
                  "you start the application.")

    # The update mechanism downloads and unpacks a source-code zip over the
    # existing .py files (see update.py) - meaningless for a frozen build,
    # which has no loose source files to overwrite and would not pick up
    # the change anyway. Skip it entirely rather than have it silently do
    # nothing (or write stray files next to the .exe).
    if UPDATE_AVAILABLE and not _is_frozen() and os.path.exists("update.zip"):
        print("Update found. Installing...")
        install_update()
        print("Restarting application...")
        os.execv(sys.executable, [sys.executable] + sys.argv)

    tt = TimeTracker()
    tt.initialize_dependencies()

    start_streamlit_server()

    if UPDATE_AVAILABLE and not _is_frozen():
        print("Checking for updates...")
        try:
            is_update, unused_version, url = check_for_updates(tt.get_version())
            if is_update and url:
                print("New version " + unused_version + " is available. Downloading...")
                download_update(url)
        except Exception as e:
            print(f"Error checking for updates: {e}")
