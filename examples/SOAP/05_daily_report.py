"""
Example: Generate today's daily report (as Markdown) via the TimeControl
SOAP interface.

Prerequisites:
    - The SOAP server is running: python TimeTrackerSOAP_Server.py
    - The 'zeep' package is installed: pip install zeep

Passing no date (None) makes the server generate the report for today.
To request a specific day instead, pass a "YYYY-MM-DD" string, e.g.:
    client.service.generate_daily_report("2026-01-31")
"""

from zeep import Client

SOAP_URL = "http://localhost:8600/?wsdl"


def main():
    client = Client(SOAP_URL)

    # Why does a "report" come back as a single block of Markdown text
    # instead of, say, a structured list of {task, hours} entries? Because
    # that keeps the interface simple and leaves the choice of what to do
    # with it up to you, the caller: print it to a terminal (as we do
    # below), write it to a file, paste it into a chat message, or convert
    # it to HTML/PDF for a nicer-looking report. The server doesn't have to
    # know or care which of those you want.
    #
    # Passing None for the date is what makes the server default to
    # "today" using its own clock - the same way you'd expect the feature
    # to behave if you clicked a "Today's report" button in the GUI,
    # without you having to compute today's date on the client side first.
    report = client.service.generate_daily_report(None)
    print(report)


if __name__ == "__main__":
    main()
