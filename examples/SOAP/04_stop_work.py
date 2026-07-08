"""
Example: Stop the currently running time tracking session via the
TimeControl SOAP interface.

Prerequisites:
    - The SOAP server is running: python TimeTrackerSOAP_Server.py
    - The 'zeep' package is installed: pip install zeep

It is safe to run this script even when no session is currently active;
the server simply reports that there was nothing to stop.
"""

from zeep import Client

SOAP_URL = "http://localhost:8600/?wsdl"


def main():
    client = Client(SOAP_URL)

    # stop_work() takes no arguments at all. That looks unusual if you're
    # used to APIs where you specify *which* thing to stop by ID or name,
    # but it makes sense once you remember the rule from 03_start_work.py:
    # there can only ever be one active session for the whole data file, so
    # there is nothing to identify - "the" active session is all there is.
    #
    # It also returns a plain True/False instead of raising an error when
    # nothing was running. That is a deliberate design choice on the
    # server's part: "there was nothing to stop" is a completely normal
    # outcome (for example, if this script is run twice in a row), not an
    # exceptional situation your code needs to guard against with a
    # try/except block.
    stopped = client.service.stop_work()
    if stopped:
        print("The running time tracking session was stopped.")
    else:
        print("No time tracking session was active.")


if __name__ == "__main__":
    main()
