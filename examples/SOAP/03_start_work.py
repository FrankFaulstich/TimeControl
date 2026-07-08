"""
Example: Start time tracking on a task via the TimeControl SOAP interface.

Prerequisites:
    - The SOAP server is running: python TimeTrackerSOAP_Server.py
    - The 'zeep' package is installed: pip install zeep
    - 02_create_task.py has been run at least once (or the task already
      exists for another reason).

Starting work automatically stops any other session that might currently
be running, so this script is safe to run more than once.
"""

from zeep import Client

SOAP_URL = "http://localhost:8600/?wsdl"
PROJECT_NAME = "SOAP Example Project"
TASK_NAME = "Write SOAP documentation"


def main():
    client = Client(SOAP_URL)

    # You can only ever be "working on" one task at a time in TimeControl -
    # that's a deliberate business rule, not a limitation of this example.
    # Because of that rule, start_work() doesn't require you to first check
    # whether something else is running and stop it yourself: the server
    # does that for you automatically. That's also *why* this call is safe
    # to make repeatedly, even from a script you might run more than once by
    # accident.
    started = client.service.start_work(PROJECT_NAME, TASK_NAME)
    print(f"Started work on '{TASK_NAME}' ({PROJECT_NAME}): {started}")

    # A SOAP call like start_work() only tells you whether the *request*
    # was accepted - it doesn't echo back the resulting state. If you want
    # to be sure the change actually took effect the way you expected (a
    # good habit in any client code that talks to a remote service), you
    # ask again: get_current_work() reports whatever the server currently
    # considers "the active session", independent of how it got that way.
    current = client.service.get_current_work()
    if current:
        print(f"Currently active: '{current.task_name}' in '{current.main_project_name}' since {current.start_time}")


if __name__ == "__main__":
    main()
