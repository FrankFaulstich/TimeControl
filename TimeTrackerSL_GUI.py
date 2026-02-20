import json
import os
import subprocess
import sys
import webview
import time
import threading

from TimeTracker import TimeTracker

try:
    from update import check_for_updates, download_update, install_update
    UPDATE_AVAILABLE = True
except ImportError:
    UPDATE_AVAILABLE = False

CONFIG_FILE = 'config.json'

def save_window_state(window):
    """Saves the current window dimensions and position to config.json."""
    try:
        config = {}
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
        
        config['window_width'] = window.width
        config['window_height'] = window.height
        config['window_x'] = window.x
        config['window_y'] = window.y
        
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=4)
    except Exception as e:
        print(f"Error saving window state: {e}")

def start_streamlit_server():
    """
    Reads the configuration from config.json and starts the Streamlit application
    on the configured port.
    """
    port = 8501 # Default Streamlit port
    width = 800
    height = 600
    
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
                port = config.get('streamlit_port', 8501)
                width = config.get('window_width', 400)
                height = config.get('window_height', 800)
        except (json.JSONDecodeError, IOError) as e:
            print(f"Warning: Could not read config.json. Using default port 8501. Error: {e}")

    print(f"Starting TimeControl GUI on port {port}...")
    
    # Use sys.executable to ensure the same python environment is used
    cmd = [sys.executable, "-m", "streamlit", "run", "SL_Menu.py", "--server.port", str(port), "--server.headless", "true"]
    
    process = subprocess.Popen(cmd)
    
    time.sleep(2) # Wait for Streamlit to initialize
    
    window = webview.create_window(
        "Time Control", 
        f"http://localhost:{port}",
        width=width,
        height=height
    )
    
    # Save state when closing the window
    window.events.closing += lambda: save_window_state(window)
    
    # Monitor the Streamlit process and close the window if it exits
    def monitor_streamlit(proc, win):
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

if __name__ == '__main__':
    if UPDATE_AVAILABLE and os.path.exists("update.zip"):
        print("Update found. Installing...")
        install_update()
        print("Restarting application...")
        os.execv(sys.executable, [sys.executable] + sys.argv)

    start_streamlit_server()
    
    if UPDATE_AVAILABLE:
        print("Checking for updates...")
        try:
            tt = TimeTracker()
            is_update, unused_version, url = check_for_updates(tt.get_version())
            if is_update and url:
                print("New version " + unused_version + " is available. Downloading...")
                download_update(url)
        except Exception as e:
            print(f"Error checking for updates: {e}")
