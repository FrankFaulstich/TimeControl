import json
import os
import subprocess
import sys
import webview
import time

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
    x = None
    y = None
    
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
                port = config.get('streamlit_port', 8501)
                width = config.get('window_width', 400)
                height = config.get('window_height', 800)
                x = config.get('window_x', None)
                y = config.get('window_y', None)
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
        height=height,
        x=x,
        y=y
    )
    
    # Save state when closing the window
    window.events.closing += lambda: save_window_state(window)
    
    webview.start()
    
    process.terminate()

if __name__ == '__main__':
    start_streamlit_server()
    
