"""
Example: Create a main project via the TimeControl REST interface.

Prerequisites:
    - The REST server is running: python TimeTrackerREST_Server.py
    - The 'requests' package is installed: pip install requests

This script is idempotent: running it again will simply report that the
project already exists instead of creating a duplicate.
"""

import requests

# The port itself comes from config.json ("rest_port"); 8800 is only the
# default the server falls back to if that key is missing.
BASE_URL = "http://localhost:8800"

# Using a constant instead of typing the name twice means the "does it
# already exist?" check below and the actual creation call are guaranteed
# to talk about the very same project.
PROJECT_NAME = "REST Example Project"


def main():
    # Why bother checking first? Because add_main_project on the server side
    # does not check for an existing project with the same name - it always
    # appends a new one. If this script just called it unconditionally every
    # time you ran it, you would quietly end up with several projects that
    # all have the exact same name. Listing the existing projects and
    # looking for a match first is the standard "look before you leap"
    # pattern you should use for any operation that isn't naturally safe to
    # repeat.
    existing_projects = requests.get(f"{BASE_URL}/projects", params={"status_filter": "all"}).json()
    if any(p["main_project_name"] == PROJECT_NAME for p in existing_projects):
        print(f"Project '{PROJECT_NAME}' already exists, nothing to do.")
        return

    # Many of the endpoints on this REST interface (this one included)
    # report success as a simple true/false field rather than an HTTP error
    # status when something goes wrong (e.g. trying to close a project that
    # doesn't exist). That means *you* are responsible for checking - and
    # printing - the result to notice a failure, which is why we don't just
    # call the endpoint and stop there.
    response = requests.post(f"{BASE_URL}/projects", json={"main_project_name": PROJECT_NAME})
    response.raise_for_status()
    created = response.json()["success"]
    print(f"Project '{PROJECT_NAME}' created: {created}")


if __name__ == "__main__":
    main()
