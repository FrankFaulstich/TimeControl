import json
import os
import subprocess
import sys

CONFIG_FILE = 'config.json'

def main():
    """
    Reads the configuration from config.json and starts the Streamlit application
    on the configured port.
    """
    port = 8501 # Default Streamlit port
    
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
                port = config.get('streamlit_port', 8501)
        except (json.JSONDecodeError, IOError) as e:
            print(f"Warning: Could not read config.json. Using default port 8501. Error: {e}")

    print(f"Starting TimeControl GUI on port {port}...")
    
    # Use sys.executable to ensure the same python environment is used
    cmd = [sys.executable, "-m", "streamlit", "run", "TimeTrackerSL.py", "--server.port", str(port)]
    
    try:
        subprocess.run(cmd)
    except KeyboardInterrupt:
        print("\nStreamlit server stopped.")

if __name__ == '__main__':
    main()
