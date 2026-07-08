"""
Example: Create a task inside a main project via the TimeControl SOAP interface.

Prerequisites:
    - The SOAP server is running: python TimeTrackerSOAP_Server.py
    - The 'zeep' package is installed: pip install zeep
    - 01_create_project.py has been run at least once (or the project
      already exists for another reason).

This script is idempotent: running it again will simply report that the
task already exists instead of creating a duplicate.
"""

from zeep import Client

SOAP_URL = "http://localhost:8600/?wsdl"
PROJECT_NAME = "SOAP Example Project"
TASK_NAME = "Write SOAP documentation"


def main():
    client = Client(SOAP_URL)

    # Just like with projects (see 01_create_project.py), the server does
    # not stop you from creating two tasks with the same name in the same
    # project, so we check ourselves first to keep this script safe to
    # re-run.
    # As in 01_create_project.py: zeep represents an *empty* SOAP array as
    # None instead of an empty list, so "or []" keeps this working even
    # before the very first task has been created.
    existing_tasks = client.service.list_tasks(PROJECT_NAME, "all") or []
    if any(t.task_name == TASK_NAME for t in existing_tasks):
        print(f"Task '{TASK_NAME}' already exists in '{PROJECT_NAME}', nothing to do.")
        return

    # Notice that a task is always created *inside* a project by name -
    # TimeControl's data model has exactly two levels (projects and their
    # tasks), so there is no such thing as a "loose" task that doesn't
    # belong to a project. That's why `main_project_name` is the first
    # argument here rather than, say, an ID you'd have to look up first.
    #
    # Every optional argument (everything after task_name) is spelled out
    # explicitly below, even though the server would fall back to sensible
    # defaults if you left them out. For a first example that's deliberate:
    # it shows you, at a glance and without reading the server's source
    # code, every property a task can have. Once you're comfortable with
    # the API, feel free to only pass what you actually need.
    created = client.service.add_task(
        main_project_name=PROJECT_NAME,
        task_name=TASK_NAME,
        due_date=None,          # no deadline
        today=False,            # not marked as "work on this today"
        note="Created via the SOAP interface.",
        recurring=False,        # a one-off task, not a repeating chore
        frequency="daily",      # ignored here because recurring=False
        userdefined_days=1,     # ignored here too; only used for custom recurrence
    )
    print(f"Task '{TASK_NAME}' created in project '{PROJECT_NAME}': {created}")


if __name__ == "__main__":
    main()
