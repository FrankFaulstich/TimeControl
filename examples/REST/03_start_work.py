"""
Example: Start time tracking on a task via the TimeControl REST interface.

Prerequisites:
    - The REST server is running: python TimeTrackerREST_Server.py
    - The 'requests' package is installed: pip install requests
    - 02_create_task.py has been run at least once (or the task already
      exists for another reason).

Starting work automatically stops any other session that might currently
be running, so this script is safe to run more than once.
"""

import requests

BASE_URL = "http://localhost:8800"
PROJECT_NAME = "REST Example Project"
TASK_NAME = "Write REST documentation"


def main():
    # You can only ever be "working on" one task at a time in TimeControl -
    # that's a deliberate business rule, not a limitation of this example.
    # Because of that rule, /work/start doesn't require you to first check
    # whether something else is running and stop it yourself: the server
    # does that for you automatically. That's also *why* this call is safe
    # to make repeatedly, even from a script you might run more than once by
    # accident.
    response = requests.post(f"{BASE_URL}/work/start", json={
        "main_project_name": PROJECT_NAME,
        "task_name": TASK_NAME,
    })
    response.raise_for_status()
    started = response.json()["success"]
    print(f"Started work on '{TASK_NAME}' ({PROJECT_NAME}): {started}")

    # A call to /work/start only tells you whether the *request* was
    # accepted - it doesn't echo back the resulting state. If you want to be
    # sure the change actually took effect the way you expected (a good
    # habit in any client code that talks to a remote service), you ask
    # again: /work/current reports whatever the server currently considers
    # "the active session", independent of how it got that way.
    current = requests.get(f"{BASE_URL}/work/current").json()
    if current:
        print(f"Currently active: '{current['task_name']}' in '{current['main_project_name']}' since {current['start_time']}")


if __name__ == "__main__":
    main()
