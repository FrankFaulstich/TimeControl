"""
Example: Create a task inside a main project via the TimeControl REST interface.

Prerequisites:
    - The REST server is running: python TimeTrackerREST_Server.py
    - The 'requests' package is installed: pip install requests
    - 01_create_project.py has been run at least once (or the project
      already exists for another reason).

This script is idempotent: running it again will simply report that the
task already exists instead of creating a duplicate.
"""

from urllib.parse import quote

import requests

BASE_URL = "http://localhost:8800"
PROJECT_NAME = "REST Example Project"
TASK_NAME = "Write REST documentation"


def main():
    # Just like with projects (see 01_create_project.py), the server does
    # not stop you from creating two tasks with the same name in the same
    # project, so we check ourselves first to keep this script safe to
    # re-run.
    existing_tasks = requests.get(f"{BASE_URL}/tasks", params={
        "main_project_name": PROJECT_NAME,
        "status_filter": "all",
    }).json()
    if any(t["task_name"] == TASK_NAME for t in existing_tasks):
        print(f"Task '{TASK_NAME}' already exists in '{PROJECT_NAME}', nothing to do.")
        return

    # Notice that a task is always created *inside* a project by name -
    # TimeControl's data model has exactly two levels (projects and their
    # tasks), so there is no such thing as a "loose" task that doesn't
    # belong to a project. That's why the project name is part of the URL
    # path here rather than, say, an ID you'd have to look up first.
    #
    # Project and task names can contain spaces (as this one does), which
    # are not valid as-is inside a URL path segment - quote() percent-encodes
    # them (and anything else that needs it) the way HTTP expects.
    #
    # Every optional field (everything after task_name) is spelled out
    # explicitly below, even though the server would fall back to sensible
    # defaults if you left them out. For a first example that's deliberate:
    # it shows you, at a glance and without reading the server's source
    # code, every property a task can have. Once you're comfortable with
    # the API, feel free to only pass what you actually need.
    response = requests.post(
        f"{BASE_URL}/projects/{quote(PROJECT_NAME, safe='')}/tasks",
        json={
            "task_name": TASK_NAME,
            "due_date": None,          # no deadline
            "today": False,            # not marked as "work on this today"
            "note": "Created via the REST interface.",
            "recurring": False,        # a one-off task, not a repeating chore
            "frequency": "daily",      # ignored here because recurring=False
            "userdefined_days": 1,     # ignored here too; only used for custom recurrence
        },
    )
    response.raise_for_status()
    created = response.json()["success"]
    print(f"Task '{TASK_NAME}' created in project '{PROJECT_NAME}': {created}")


if __name__ == "__main__":
    main()
