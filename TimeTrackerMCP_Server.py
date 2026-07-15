import json
import os
import sys
from datetime import datetime

# Attempt to import the MCP SDK. This is the standard library for building
# Model Context Protocol servers in Python.
try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    print("Error: The required library is not installed.", file=sys.stderr)
    print("Please run: pip install mcp", file=sys.stderr)
    sys.exit(1)

# Import of TimeTracker logic
# We add the current directory to the path so that tt.TimeTracker can be found
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
try:
    from tt.TimeTracker import TimeTracker
except ImportError as e:
    print(f"Error importing TimeTracker: {e}", file=sys.stderr)
    sys.exit(1)

CONFIG_FILE = 'config.json'


def load_config():
    """Loads the configuration from the config.json file."""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (IOError, json.JSONDecodeError):
            pass
    return {}


# The port has to be known before the FastMCP instance (and its decorators
# below) are created, so the config is read once at import time here.
_config = load_config()
mcp = FastMCP("TimeControl", port=_config.get('mcp_port', 8700))

# stdio uses stdout as the JSON-RPC message channel itself, so nothing else
# may write to it - unlike the HTTP transport, where stray console output is
# harmless. Report generation can print a "copied to clipboard" notice (see
# TimeTracker._copy_to_clipboard), which would otherwise corrupt every report
# tool's response when running over stdio.
_STDIO_MODE = _config.get('mcp_transport', 'http') == 'stdio'


def get_tracker():
    """
    Returns a freshly loaded TimeTracker instance.

    A new instance is created for every call instead of reusing one for the
    whole server process, so this always sees the latest state on disk -
    including changes made concurrently through the GUI or the SOAP
    interface - and so its own changes are picked up by them immediately too.
    """
    return TimeTracker()


def _call_protecting_stdio(fn, *args, **kwargs):
    """
    Runs fn, redirecting stdout to stderr for the duration of the call if the
    server is running over the stdio transport (see _STDIO_MODE above).
    """
    if not _STDIO_MODE:
        return fn(*args, **kwargs)
    old_stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        return fn(*args, **kwargs)
    finally:
        sys.stdout = old_stdout


def parse_date(date_str, param_name):
    """
    Parses a "YYYY-MM-DD" string into a date object for the report methods,
    which expect a real datetime.date rather than a string.

    :return: (date_obj, error_message). Exactly one of the two is None.
    """
    if not date_str:
        return None, None
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date(), None
    except ValueError:
        return None, f"Error: {param_name} must be in YYYY-MM-DD format."


# --- Main Project Management ---

@mcp.tool()
def add_main_project(main_project_name: str) -> str:
    """Creates a new main project, unless one with that name already exists."""
    tracker = get_tracker()
    existing_names = [p['main_project_name'] for p in tracker.list_main_projects(status_filter='all')]
    if main_project_name in existing_names:
        return f"Project '{main_project_name}' already exists."
    tracker.add_main_project(main_project_name)
    return f"Project '{main_project_name}' created."


@mcp.tool()
def list_main_projects(status_filter: str = "open") -> list:
    """
    Lists main projects.

    :param status_filter: 'open', 'closed', or 'all'.
    """
    tracker = get_tracker()
    return tracker.list_main_projects(status_filter=status_filter)


@mcp.tool()
def rename_main_project(old_name: str, new_name: str) -> str:
    """Renames a main project. Fails if a project with new_name already exists."""
    tracker = get_tracker()
    if tracker.rename_main_project(old_name, new_name):
        return f"Project '{old_name}' renamed to '{new_name}'."
    return f"Error: Could not rename '{old_name}' - it may not exist, or '{new_name}' may already be taken."


@mcp.tool()
def close_main_project(main_project_name: str) -> str:
    """Archives a main project (marks it 'closed') without deleting it."""
    tracker = get_tracker()
    if tracker.close_main_project(main_project_name):
        return f"Project '{main_project_name}' closed."
    return f"Error: Project '{main_project_name}' not found."


@mcp.tool()
def reopen_main_project(main_project_name: str) -> str:
    """Reopens a previously closed main project."""
    tracker = get_tracker()
    if tracker.reopen_main_project(main_project_name):
        return f"Project '{main_project_name}' reopened."
    return f"Error: Project '{main_project_name}' not found."


@mcp.tool()
def delete_main_project(main_project_name: str) -> str:
    """
    Permanently deletes a main project, all of its tasks, and all of their
    time entries. This cannot be undone - confirm with the user before
    calling this.
    """
    tracker = get_tracker()
    if tracker.delete_main_project(main_project_name):
        return f"Project '{main_project_name}' permanently deleted."
    return f"Error: Project '{main_project_name}' not found."


@mcp.tool()
def demote_main_project(main_project_to_demote: str, new_parent_main_project: str) -> str:
    """
    Converts a main project into a task under another main project. All of
    its tasks' time entries are consolidated into that one new task.
    """
    tracker = get_tracker()
    success, message = tracker.demote_main_project(main_project_to_demote, new_parent_main_project)
    return message


@mcp.tool()
def list_completed_main_projects() -> list:
    """Lists main projects that have no tasks, or only closed tasks."""
    tracker = get_tracker()
    return tracker.list_completed_main_projects()


@mcp.tool()
def list_inactive_main_projects(inactive_weeks: int) -> list:
    """Lists main projects with no activity in the last `inactive_weeks` weeks."""
    tracker = get_tracker()
    return tracker.list_inactive_main_projects(inactive_weeks)


# --- Task Management ---

@mcp.tool()
def add_task(
    main_project_name: str,
    task_name: str,
    due_date: str | None = None,
    today: bool = False,
    note: str = "",
    recurring: bool = False,
    frequency: str = "daily",
    userdefined_days: int = 1,
) -> str:
    """
    Creates a new task inside an existing main project.

    The main project must already exist; use add_main_project first if it
    does not.

    :param main_project_name: The main project the task should belong to.
    :param task_name: The name of the new task.
    :param due_date: Optional due date in YYYY-MM-DD format.
    :param today: Whether to mark the task for today.
    :param note: Optional note/description for the task (Markdown).
    :param recurring: Whether the task repeats after it's marked done.
    :param frequency: 'daily', 'business_days', 'weekly', 'monthly', or 'userdefined'. Only used if recurring is true.
    :param userdefined_days: Number of days between occurrences. Only used if frequency is 'userdefined'.
    """
    tracker = get_tracker()
    existing_project_names = [p['main_project_name'] for p in tracker.list_main_projects(status_filter='all')]
    if main_project_name not in existing_project_names:
        return f"Error: Main project '{main_project_name}' not found."

    existing_tasks = tracker.list_tasks(main_project_name=main_project_name, status_filter='all')
    if any(t['task_name'] == task_name for t in existing_tasks):
        return f"Task '{task_name}' already exists in project '{main_project_name}'."

    tracker.add_task(
        main_project_name,
        task_name,
        due_date=due_date,
        today=today,
        note=note,
        recurring=recurring,
        frequency=frequency,
        userdefined_days=userdefined_days,
    )
    return f"Task '{task_name}' created in project '{main_project_name}'."


@mcp.tool()
def list_tasks(main_project_name: str | None = None, status_filter: str = "open") -> list:
    """
    Lists tasks, optionally restricted to a single main project.

    :param main_project_name: Restrict to this main project; omit to list tasks across all projects.
    :param status_filter: 'open', 'closed', or 'all'.
    """
    tracker = get_tracker()
    return tracker.list_tasks(main_project_name=main_project_name, status_filter=status_filter)


@mcp.tool()
def rename_task(main_project_name: str, old_task_name: str, new_task_name: str) -> str:
    """Renames a task within a main project."""
    tracker = get_tracker()
    if tracker.rename_task(main_project_name, old_task_name, new_task_name):
        return f"Task '{old_task_name}' renamed to '{new_task_name}'."
    return f"Error: Could not rename '{old_task_name}' in project '{main_project_name}'."


@mcp.tool()
def close_task(main_project_name: str, task_name: str) -> str:
    """Archives a task (marks it 'closed') without deleting it."""
    tracker = get_tracker()
    if tracker.close_task(main_project_name, task_name):
        return f"Task '{task_name}' closed."
    return f"Error: Task '{task_name}' not found in project '{main_project_name}'."


@mcp.tool()
def reopen_task(main_project_name: str, task_name: str) -> str:
    """Reopens a previously closed task."""
    tracker = get_tracker()
    if tracker.reopen_task(main_project_name, task_name):
        return f"Task '{task_name}' reopened."
    return f"Error: Task '{task_name}' not found in project '{main_project_name}'."


@mcp.tool()
def delete_task(main_project_name: str, task_name: str) -> str:
    """
    Permanently deletes a task and all of its time entries. This cannot be
    undone - confirm with the user before calling this.
    """
    tracker = get_tracker()
    if tracker.delete_task(main_project_name, task_name):
        return f"Task '{task_name}' permanently deleted."
    return f"Error: Task '{task_name}' not found in project '{main_project_name}'."


@mcp.tool()
def delete_all_closed_tasks() -> str:
    """
    Permanently deletes every closed task (in every project) and its time
    entries. This cannot be undone - confirm with the user before calling
    this.
    """
    tracker = get_tracker()
    count = tracker.delete_all_closed_tasks()
    return f"Deleted {count} closed task(s)."


@mcp.tool()
def move_task(main_project_name: str, task_name: str, new_main_project_name: str) -> str:
    """Moves a task from one main project to another."""
    tracker = get_tracker()
    success, message = tracker.move_task(main_project_name, task_name, new_main_project_name)
    return message


@mcp.tool()
def promote_task_to_project(main_project_name: str, task_name: str) -> str:
    """
    Promotes a task to become its own new main project. Its time entries are
    preserved under a new 'General' task in that new project.
    """
    tracker = get_tracker()
    success, message = tracker.promote_task_to_project(main_project_name, task_name)
    return message


@mcp.tool()
def update_task(
    main_project_name: str,
    task_name: str,
    new_task_name: str | None = None,
    due_date: str | None = None,
    clear_due_date: bool = False,
    today: bool | None = None,
    note: str | None = None,
    status: str | None = None,
    recurring: bool | None = None,
    frequency: str | None = None,
    userdefined_days: int | None = None,
) -> str:
    """
    Updates one or more properties of an existing task in one call. Only the
    parameters you actually pass are changed; everything you omit (including
    the due date) is left exactly as it is.

    :param new_task_name: Rename the task to this.
    :param due_date: New due date in YYYY-MM-DD format. Omit to keep the current one.
    :param clear_due_date: Set to true to remove the due date entirely (overrides due_date).
    :param today: Mark/unmark the task for today.
    :param note: Replace the task's note (Markdown).
    :param status: 'open', 'done', or 'closed'. Prefer mark_task_done for simply finishing a task.
    :param recurring: Whether the task repeats after it's marked done.
    :param frequency: 'daily', 'business_days', 'weekly', 'monthly', or 'userdefined'.
    :param userdefined_days: Number of days between occurrences, for 'userdefined' frequency.
    """
    tracker = get_tracker()
    tasks = tracker.list_tasks(main_project_name=main_project_name, status_filter='all')
    current_task = next((t for t in tasks if t['task_name'] == task_name), None)
    if current_task is None:
        return f"Error: Task '{task_name}' not found in project '{main_project_name}'."

    # update_task() always overwrites due_date with whatever is passed, even
    # if that's None - so an explicit omission has to be resolved to the
    # task's current value here rather than left as None, or it would be
    # silently cleared as a side effect of changing something unrelated.
    if clear_due_date:
        final_due_date = None
    elif due_date is not None:
        final_due_date = due_date
    else:
        final_due_date = current_task.get('due_date')

    success = tracker.update_task(
        main_project_name,
        task_name,
        new_task_name=new_task_name,
        due_date=final_due_date,
        today=today,
        note=note,
        status=status,
        recurring=recurring,
        frequency=frequency,
        userdefined_days=userdefined_days,
        task_id=current_task.get('id'),
    )
    if success:
        return f"Task '{task_name}' updated."
    return f"Error: Could not update task '{task_name}' in project '{main_project_name}'."


@mcp.tool()
def mark_task_done(main_project_name: str, task_name: str) -> str:
    """
    Marks a task as done. This just flags it as finished (it still shows up
    until explicitly closed/archived via close_task) and, if the task is
    recurring, creates its next occurrence.
    """
    return update_task(main_project_name, task_name, status="done")


@mcp.tool()
def list_inactive_tasks(inactive_weeks: int) -> list:
    """
    Lists tasks with no activity in the last `inactive_weeks` weeks. Closed
    tasks are excluded, but tasks already marked 'done' are included since
    they may still need to be closed.
    """
    tracker = get_tracker()
    return tracker.list_inactive_tasks(inactive_weeks)


@mcp.tool()
def cleanup_overdue_today_tasks() -> str:
    """Removes the 'today' flag from tasks whose due date is now in the past."""
    tracker = get_tracker()
    changed = tracker.cleanup_overdue_today_tasks()
    return "Removed the 'today' flag from overdue tasks." if changed else "Nothing to clean up."


@mcp.tool()
def set_today_flag_for_due_tasks() -> str:
    """Marks open tasks whose due date is today as 'today' tasks."""
    tracker = get_tracker()
    changed = tracker.set_today_flag_for_due_tasks()
    return "Marked tasks due today as 'today' tasks." if changed else "No tasks needed updating."


# --- Time Tracking ---

@mcp.tool()
def start_work(main_project_name: str, task_name: str) -> str:
    """
    Starts time tracking on a task. Both the project and the task must
    already exist. Automatically stops any other session that might
    currently be running.
    """
    tracker = get_tracker()
    if tracker.start_work(main_project_name, task_name):
        return f"Started working on '{task_name}' in project '{main_project_name}'."
    return (
        f"Error: Could not start work on '{task_name}' in project "
        f"'{main_project_name}'. Check that both exist (use list_main_projects "
        f"/ list_tasks) - they are not created automatically."
    )


@mcp.tool()
def stop_work() -> str:
    """Stops the currently running time tracking session, if any."""
    tracker = get_tracker()
    if tracker.stop_work():
        return "Stopped the running time tracking session."
    return "No time tracking session was active."


@mcp.tool()
def get_current_work() -> dict | None:
    """Returns the task currently being worked on, or None if no session is active."""
    tracker = get_tracker()
    return tracker.get_current_work()


# --- Email Import ---

@mcp.tool()
def fetch_emails_to_tasks() -> str:
    """
    Fetches emails from the IMAP account configured in config.json and turns
    each one into an unassigned task (subject as the task name, body as its
    note). Requires email import to be enabled and configured first - this
    cannot be set up from here.
    """
    tracker = get_tracker()
    count, error = tracker.fetch_emails_to_tasks()
    if error:
        return f"Error fetching emails: {error}"
    if count:
        return f"{count} new task(s) created from emails."
    return "No new emails found."


# --- Reporting ---

@mcp.tool()
def generate_daily_report(report_date: str | None = None) -> str:
    """
    Generates a daily time report as Markdown. Omit report_date for today.

    :param report_date: Optional date in YYYY-MM-DD format.
    """
    date_obj, error = parse_date(report_date, "report_date")
    if error:
        return error
    return _call_protecting_stdio(get_tracker().generate_daily_report, date_obj)


@mcp.tool()
def generate_detailed_daily_report(report_date: str | None = None) -> str:
    """
    Generates a detailed daily report as Markdown, listing individual time
    entries rather than just totals. Omit report_date for today.

    :param report_date: Optional date in YYYY-MM-DD format.
    """
    date_obj, error = parse_date(report_date, "report_date")
    if error:
        return error
    return _call_protecting_stdio(get_tracker().generate_detailed_daily_report, date_obj)


@mcp.tool()
def generate_date_range_report(start_date: str, end_date: str) -> str:
    """
    Generates a time report as Markdown for a date range (inclusive).

    :param start_date: Start date in YYYY-MM-DD format.
    :param end_date: End date in YYYY-MM-DD format.
    """
    start_obj, error = parse_date(start_date, "start_date")
    if error:
        return error
    end_obj, error = parse_date(end_date, "end_date")
    if error:
        return error
    return _call_protecting_stdio(get_tracker().generate_date_range_report, start_obj, end_obj)


@mcp.tool()
def generate_task_report(main_project_name: str, task_name: str) -> str:
    """Generates a detailed report as Markdown for a single task."""
    return _call_protecting_stdio(get_tracker().generate_task_report, main_project_name, task_name)


@mcp.tool()
def generate_main_project_report(main_project_name: str) -> str:
    """Generates a detailed report as Markdown for a single main project, including a breakdown across its tasks."""
    return _call_protecting_stdio(get_tracker().generate_main_project_report, main_project_name)


# --- Misc ---

@mcp.tool()
def get_version() -> str:
    """Returns the TimeControl application version."""
    return get_tracker().get_version()


def main():
    if _STDIO_MODE:
        # No prints here: stdout is the JSON-RPC channel for this transport,
        # and Claude Desktop (the typical client) spawns this process itself
        # and reads its stdout directly.
        mcp.run(transport="stdio")
    else:
        print(f"Starting TimeControl MCP server on http://{mcp.settings.host}:{mcp.settings.port}{mcp.settings.streamable_http_path} ...")
        mcp.run(transport="streamable-http")


if __name__ == '__main__':
    main()
