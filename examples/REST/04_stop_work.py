"""
Example: Stop the currently running time tracking session via the
TimeControl REST interface.

Prerequisites:
    - The REST server is running: python TimeTrackerREST_Server.py
    - The 'requests' package is installed: pip install requests

It is safe to run this script even when no session is currently active;
the server simply reports that there was nothing to stop.
"""

import requests

BASE_URL = "http://localhost:8800"


def main():
    # /work/stop takes no arguments at all. That looks unusual if you're
    # used to APIs where you specify *which* thing to stop by ID or name,
    # but it makes sense once you remember the rule from 03_start_work.py:
    # there can only ever be one active session for the whole data file, so
    # there is nothing to identify - "the" active session is all there is.
    #
    # It also returns a plain true/false instead of an HTTP error status
    # when nothing was running. That is a deliberate design choice on the
    # server's part: "there was nothing to stop" is a completely normal
    # outcome (for example, if this script is run twice in a row), not an
    # exceptional situation your code needs to guard against with a
    # try/except block.
    response = requests.post(f"{BASE_URL}/work/stop")
    response.raise_for_status()
    stopped = response.json()["success"]
    if stopped:
        print("The running time tracking session was stopped.")
    else:
        print("No time tracking session was active.")


if __name__ == "__main__":
    main()
