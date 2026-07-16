"""
Example: Generate today's daily report (as Markdown) via the TimeControl
REST interface.

Prerequisites:
    - The REST server is running: python TimeTrackerREST_Server.py
    - The 'requests' package is installed: pip install requests

Passing no date makes the server generate the report for today. To request
a specific day instead, pass a "YYYY-MM-DD" string as the report_date query
parameter, e.g.:
    requests.get(f"{BASE_URL}/reports/daily", params={"report_date": "2026-01-31"})
"""

import requests

BASE_URL = "http://localhost:8800"


def main():
    # Why does a "report" come back as a single block of Markdown text
    # instead of, say, a structured list of {task, hours} entries? Because
    # that keeps the interface simple and leaves the choice of what to do
    # with it up to you, the caller: print it to a terminal (as we do
    # below), write it to a file, paste it into a chat message, or convert
    # it to HTML/PDF for a nicer-looking report. The server doesn't have to
    # know or care which of those you want.
    #
    # Omitting report_date is what makes the server default to "today"
    # using its own clock - the same way you'd expect the feature to behave
    # if you clicked a "Today's report" button in the GUI, without you
    # having to compute today's date on the client side first.
    response = requests.get(f"{BASE_URL}/reports/daily")
    response.raise_for_status()
    report = response.json()["report"]
    print(report)


if __name__ == "__main__":
    main()
