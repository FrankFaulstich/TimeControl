"""
Example: Create a main project via the TimeControl SOAP interface.

Prerequisites:
    - The SOAP server is running: python TimeTrackerSOAP_Server.py
    - The 'zeep' package is installed: pip install zeep

This script is idempotent: running it again will simply report that the
project already exists instead of creating a duplicate.
"""

# SOAP itself is just XML sent over HTTP, but nobody wants to write that XML
# by hand. 'zeep' does that work for us: it reads the server's WSDL (a
# machine-readable description of every available method and its
# parameters) and turns each one into an ordinary-looking Python function
# under `client.service`. That is the whole point of using a SOAP *client
# library* instead of a plain HTTP library like `requests`.
from zeep import Client

# The trailing "?wsdl" is not part of the "real" service address - it is a
# special query parameter that tells the server "don't run a request for
# me, just send back your self-description". zeep uses that description to
# build the `client.service.*` methods, so it always needs this URL, not
# the bare "http://localhost:8600/".
#
# The port itself comes from config.json ("soap_port"); 8600 is only the
# default the server falls back to if that key is missing.
SOAP_URL = "http://localhost:8600/?wsdl"

# Using a constant instead of typing the name twice means the "does it
# already exist?" check below and the actual creation call are guaranteed
# to talk about the very same project.
PROJECT_NAME = "SOAP Example Project"


def main():
    # Creating the Client is the point where zeep actually downloads and
    # parses the WSDL, so this is also where you would first notice a typo
    # in SOAP_URL or a server that isn't running.
    client = Client(SOAP_URL)

    # Why bother checking first? Because `add_main_project` on the server
    # side does not check for an existing project with the same name - it
    # always appends a new one. If this script just called it unconditionally
    # every time you ran it, you would quietly end up with several projects
    # that all have the exact same name. Listing the existing projects and
    # looking for a match first is the standard "look before you leap"
    # pattern you should use for any operation that isn't naturally safe to
    # repeat.
    existing_projects = client.service.list_main_projects("all")
    if any(p.main_project_name == PROJECT_NAME for p in existing_projects):
        print(f"Project '{PROJECT_NAME}' already exists, nothing to do.")
        return

    # Many of the methods on this SOAP interface (this one included) report
    # success as a simple True/False value rather than raising an exception
    # when something goes wrong. That means *you* are responsible for
    # checking - and printing - the result to notice a failure, which is
    # why we don't just call add_main_project() and stop there.
    created = client.service.add_main_project(PROJECT_NAME)
    print(f"Project '{PROJECT_NAME}' created: {created}")


# This guard is a common Python convention: it lets other code (for example
# a test, or one of the other example scripts) import this file to reuse
# PROJECT_NAME without immediately triggering a network call to the SOAP
# server as a side effect of the import.
if __name__ == "__main__":
    main()
