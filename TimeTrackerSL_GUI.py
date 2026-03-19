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
    from update import check_for_updates, download_update, install_update
    UPDATE_AVAILABLE = True
except ImportError:
    UPDATE_AVAILABLE = False

CONFIG_FILE = 'config.json'

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
        except (json.JSONDecodeError, IOError) as e:
            print(f"Warning: Could not read config.json. Using default settings. Error: {e}")

    print(f"Starting TimeControl GUI on port {port}...")
    
    # Determine headless mode based on webview availability
    headless_mode = "true"
    
    # Use sys.executable to ensure the same python environment is used
    cmd = [sys.executable, "-m", "streamlit", "run", os.path.join("sl", "SL_Menu.py"), "--server.port", str(port), "--server.headless", headless_mode]
    
    process = subprocess.Popen(cmd)
    
    if webview and view_mode == 'webview':
        time.sleep(2) # Wait for Streamlit to initialize
        
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

if __name__ == '__main__':
    if UPDATE_AVAILABLE and os.path.exists("update.zip"):
        print("Update found. Installing...")
        install_update()
        print("Restarting application...")
        os.execv(sys.executable, [sys.executable] + sys.argv)

    tt = TimeTracker()
    tt.initialize_dependencies()

    start_streamlit_server()
    
    if UPDATE_AVAILABLE:
        print("Checking for updates...")
        try:
            is_update, unused_version, url = check_for_updates(tt.get_version())
            if is_update and url:
                print("New version " + unused_version + " is available. Downloading...")
                download_update(url)
        except Exception as e:
            print(f"Error checking for updates: {e}")
