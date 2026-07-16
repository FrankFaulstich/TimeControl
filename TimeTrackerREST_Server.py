import json
import os
import sys
from datetime import datetime
from typing import List, Optional

# Attempt to import FastAPI/uvicorn. These are the libraries used for the
# REST interface, the same way spyne is used for the SOAP interface.
try:
    from fastapi import Depends, FastAPI, HTTPException
    from pydantic import BaseModel
    import uvicorn
except ImportError:
    print("Fehler: Die benötigten Bibliotheken sind nicht installiert.")
    print("Bitte führen Sie folgenden Befehl aus: pip install fastapi uvicorn")
    sys.exit(1)

# Import of TimeTracker logic
# We add the current directory to the path so that tt.TimeTracker can be found
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
try:
    from tt.TimeTracker import TimeTracker
except ImportError as e:
    print(f"Fehler beim Importieren von TimeTracker: {e}")
    sys.exit(1)

CONFIG_FILE = 'config.json'


# --- Data models for REST responses/requests ---
# These mirror the ComplexModel classes in TimeTrackerSOAP_Server.py one for
# one, just expressed as Pydantic models instead of spyne models.

class MainProject(BaseModel):
    main_project_name: str
    status: str


class Task(BaseModel):
    id: int
    main_project_name: str
    task_name: str
    status: str
    due_date: Optional[str] = None
    today: bool
    note: str
    recurring: bool
    frequency: str
    userdefined_days: int


class InactiveProject(BaseModel):
    main_project: str
    task_name: Optional[str] = None
    last_activity: str


class CurrentWork(BaseModel):
    main_project_name: str
    task_name: str
    start_time: str


class OperationResult(BaseModel):
    success: bool
    message: str


class SuccessResult(BaseModel):
    success: bool


class CountResult(BaseModel):
    count: int


class VersionResult(BaseModel):
    version: str


class ReportResult(BaseModel):
    report: str


class AddMainProjectRequest(BaseModel):
    main_project_name: str


class RenameRequest(BaseModel):
    new_name: str


class DemoteRequest(BaseModel):
    new_parent: str


class AddTaskRequest(BaseModel):
    task_name: str
    due_date: Optional[str] = None
    today: bool = False
    note: str = ""
    recurring: bool = False
    frequency: str = "daily"
    userdefined_days: int = 1


class UpdateTaskRequest(BaseModel):
    new_name: Optional[str] = None
    due_date: Optional[str] = None
    today: Optional[bool] = None
    note: Optional[str] = None
    status: Optional[str] = None
    recurring: Optional[bool] = None
    frequency: Optional[str] = None
    userdefined_days: Optional[int] = None


class MoveTaskRequest(BaseModel):
    new_main_project_name: str


class StartWorkRequest(BaseModel):
    main_project_name: str
    task_name: Optional[str] = None
    task_id: Optional[int] = None


def get_tracker():
    """
    Returns a freshly loaded TimeTracker instance.

    A new instance is created for every request instead of reusing one for
    the whole server process, so this always sees the latest state on disk -
    including changes made concurrently through the GUI, the SOAP interface,
    or the MCP server - and so its own changes are picked up by them
    immediately too. Mirrors get_tracker() in TimeTrackerMCP_Server.py and
    the 'method_call' event listener in TimeTrackerSOAP_Server.py.
    """
    return TimeTracker()


def parse_date(date_str, param_name):
    """
    Parses a "YYYY-MM-DD" string into a date object for the report
    endpoints, which expect a real datetime.date rather than a string.
    Raises an HTTP 400 with the same wording as the SOAP interface if the
    format is wrong.
    """
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Fehler: {param_name} muss im Format YYYY-MM-DD sein.")


app = FastAPI(
    title="TimeControl REST API",
    description="REST interface for TimeControl, mirroring the SOAP interface (TimeTrackerSOAP_Server.py).",
)


# --- Main Project Management ---

@app.post("/projects", response_model=SuccessResult)
def add_main_project(body: AddMainProjectRequest, tracker: TimeTracker = Depends(get_tracker)):
    tracker.add_main_project(body.main_project_name)
    return SuccessResult(success=True)


@app.get("/projects", response_model=List[MainProject])
def list_main_projects(status_filter: str = "all", tracker: TimeTracker = Depends(get_tracker)):
    return [MainProject(**p) for p in tracker.list_main_projects(status_filter)]


@app.get("/projects/completed", response_model=List[str])
def list_completed_main_projects(tracker: TimeTracker = Depends(get_tracker)):
    return tracker.list_completed_main_projects()


@app.get("/projects/inactive", response_model=List[InactiveProject])
def list_inactive_main_projects(weeks: int, tracker: TimeTracker = Depends(get_tracker)):
    return [InactiveProject(**p) for p in tracker.list_inactive_main_projects(weeks)]


@app.delete("/projects/{main_project_name}", response_model=SuccessResult)
def delete_main_project(main_project_name: str, tracker: TimeTracker = Depends(get_tracker)):
    return SuccessResult(success=tracker.delete_main_project(main_project_name))


@app.post("/projects/{main_project_name}/rename", response_model=SuccessResult)
def rename_main_project(main_project_name: str, body: RenameRequest, tracker: TimeTracker = Depends(get_tracker)):
    return SuccessResult(success=tracker.rename_main_project(main_project_name, body.new_name))


@app.post("/projects/{main_project_name}/close", response_model=SuccessResult)
def close_main_project(main_project_name: str, tracker: TimeTracker = Depends(get_tracker)):
    return SuccessResult(success=tracker.close_main_project(main_project_name))


@app.post("/projects/{main_project_name}/reopen", response_model=SuccessResult)
def reopen_main_project(main_project_name: str, tracker: TimeTracker = Depends(get_tracker)):
    return SuccessResult(success=tracker.reopen_main_project(main_project_name))


@app.post("/projects/{main_project_name}/demote", response_model=OperationResult)
def demote_main_project(main_project_name: str, body: DemoteRequest, tracker: TimeTracker = Depends(get_tracker)):
    success, message = tracker.demote_main_project(main_project_name, body.new_parent)
    return OperationResult(success=success, message=message)


# --- Task Management ---

@app.post("/projects/{main_project_name}/tasks", response_model=SuccessResult)
def add_task(main_project_name: str, body: AddTaskRequest, tracker: TimeTracker = Depends(get_tracker)):
    created = tracker.add_task(
        main_project_name,
        body.task_name,
        body.due_date,
        body.today,
        body.note,
        body.recurring,
        body.frequency,
        body.userdefined_days,
    )
    return SuccessResult(success=created)


@app.get("/tasks", response_model=List[Task])
def list_tasks(
    main_project_name: Optional[str] = None,
    status_filter: str = "all",
    planning_filter: Optional[str] = None,
    tracker: TimeTracker = Depends(get_tracker),
):
    if planning_filter:
        tasks = tracker.list_tasks(main_project_name, status_filter, planning_filter)
    else:
        tasks = tracker.list_tasks(main_project_name, status_filter)
    return [Task(**t) for t in tasks]


@app.get("/tasks/inactive", response_model=List[InactiveProject])
def list_inactive_tasks(weeks: int, tracker: TimeTracker = Depends(get_tracker)):
    return [InactiveProject(**p) for p in tracker.list_inactive_tasks(weeks)]


@app.post("/tasks/cleanup-overdue-today", response_model=SuccessResult)
def cleanup_overdue_today_tasks(tracker: TimeTracker = Depends(get_tracker)):
    return SuccessResult(success=tracker.cleanup_overdue_today_tasks())


@app.delete("/tasks/closed", response_model=CountResult)
def delete_all_closed_tasks(tracker: TimeTracker = Depends(get_tracker)):
    return CountResult(count=tracker.delete_all_closed_tasks())


@app.delete("/projects/{main_project_name}/tasks/{task_name}", response_model=SuccessResult)
def delete_task(main_project_name: str, task_name: str, task_id: Optional[int] = None, tracker: TimeTracker = Depends(get_tracker)):
    return SuccessResult(success=tracker.delete_task(main_project_name, task_name, task_id=task_id))


@app.post("/projects/{main_project_name}/tasks/{task_name}/close", response_model=SuccessResult)
def close_task(main_project_name: str, task_name: str, task_id: Optional[int] = None, tracker: TimeTracker = Depends(get_tracker)):
    return SuccessResult(success=tracker.close_task(main_project_name, task_name, task_id=task_id))


@app.post("/projects/{main_project_name}/tasks/{task_name}/reopen", response_model=SuccessResult)
def reopen_task(main_project_name: str, task_name: str, task_id: Optional[int] = None, tracker: TimeTracker = Depends(get_tracker)):
    return SuccessResult(success=tracker.reopen_task(main_project_name, task_name, task_id=task_id))


@app.post("/projects/{main_project_name}/tasks/{task_name}/rename", response_model=SuccessResult)
def rename_task(main_project_name: str, task_name: str, body: RenameRequest, task_id: Optional[int] = None, tracker: TimeTracker = Depends(get_tracker)):
    return SuccessResult(success=tracker.rename_task(main_project_name, task_name, body.new_name, task_id=task_id))


@app.patch("/projects/{main_project_name}/tasks/{task_name}", response_model=SuccessResult)
def update_task(main_project_name: str, task_name: str, body: UpdateTaskRequest, task_id: Optional[int] = None, tracker: TimeTracker = Depends(get_tracker)):
    updated = tracker.update_task(
        main_project_name,
        task_name,
        body.new_name,
        body.due_date,
        body.today,
        body.note,
        body.status,
        body.recurring,
        body.frequency,
        body.userdefined_days,
        task_id=task_id,
    )
    return SuccessResult(success=updated)


@app.post("/projects/{main_project_name}/tasks/{task_name}/move", response_model=OperationResult)
def move_task(main_project_name: str, task_name: str, body: MoveTaskRequest, task_id: Optional[int] = None, tracker: TimeTracker = Depends(get_tracker)):
    success, message = tracker.move_task(main_project_name, task_name, body.new_main_project_name, task_id=task_id)
    return OperationResult(success=success, message=message)


@app.post("/projects/{main_project_name}/tasks/{task_name}/promote", response_model=OperationResult)
def promote_task_to_project(main_project_name: str, task_name: str, task_id: Optional[int] = None, tracker: TimeTracker = Depends(get_tracker)):
    success, message = tracker.promote_task_to_project(main_project_name, task_name, task_id=task_id)
    return OperationResult(success=success, message=message)


# --- Time Tracking ---

@app.post("/work/start", response_model=SuccessResult)
def start_work(body: StartWorkRequest, tracker: TimeTracker = Depends(get_tracker)):
    if body.task_id is not None:
        started = tracker.start_work(body.main_project_name, task_id=body.task_id)
    else:
        started = tracker.start_work(body.main_project_name, body.task_name)
    return SuccessResult(success=started)


@app.post("/work/stop", response_model=SuccessResult)
def stop_work(tracker: TimeTracker = Depends(get_tracker)):
    return SuccessResult(success=tracker.stop_work())


@app.get("/work/current", response_model=Optional[CurrentWork])
def get_current_work(tracker: TimeTracker = Depends(get_tracker)):
    work = tracker.get_current_work()
    return CurrentWork(**work) if work else None


# --- Reporting ---

@app.get("/reports/daily", response_model=ReportResult)
def generate_daily_report(report_date: Optional[str] = None, tracker: TimeTracker = Depends(get_tracker)):
    return ReportResult(report=tracker.generate_daily_report(parse_date(report_date, "report_date")))


@app.get("/reports/daily/detailed", response_model=ReportResult)
def generate_detailed_daily_report(report_date: Optional[str] = None, tracker: TimeTracker = Depends(get_tracker)):
    return ReportResult(report=tracker.generate_detailed_daily_report(parse_date(report_date, "report_date")))


@app.get("/reports/range", response_model=ReportResult)
def generate_date_range_report(start_date: str, end_date: str, tracker: TimeTracker = Depends(get_tracker)):
    start = parse_date(start_date, "start_date")
    end = parse_date(end_date, "end_date")
    return ReportResult(report=tracker.generate_date_range_report(start, end))


@app.get("/reports/task", response_model=ReportResult)
def generate_task_report(main_project_name: str, task_name: str, tracker: TimeTracker = Depends(get_tracker)):
    return ReportResult(report=tracker.generate_task_report(main_project_name, task_name))


@app.get("/reports/project", response_model=ReportResult)
def generate_main_project_report(main_project_name: str, tracker: TimeTracker = Depends(get_tracker)):
    return ReportResult(report=tracker.generate_main_project_report(main_project_name))


# --- Misc ---

@app.get("/version", response_model=VersionResult)
def get_version(tracker: TimeTracker = Depends(get_tracker)):
    return VersionResult(version=tracker.get_version())


def load_config():
    """Loads the configuration from the config.json file."""
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    return {}


def main():
    config = load_config()
    port = config.get('rest_port', 8800)

    print(f"Starte REST Server auf Port {port}...")
    print(f"Interaktive API-Dokumentation ist verfügbar unter: http://localhost:{port}/docs")

    uvicorn.run(app, host='0.0.0.0', port=port)


if __name__ == '__main__':
    main()
