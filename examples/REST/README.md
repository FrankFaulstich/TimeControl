# REST Interface Examples

This folder contains small, self-contained Python scripts that demonstrate
how to talk to TimeControl's REST interface (`TimeTrackerREST_Server.py`).
Each script performs exactly one REST call and prints the result — none of
them require any user interaction. It mirrors [examples/SOAP](../SOAP)
step for step, using the REST interface instead of the SOAP one.

| Script | Demonstrates |
| --- | --- |
| [`01_create_project.py`](01_create_project.py) | Creating a main project (`POST /projects`) |
| [`02_create_task.py`](02_create_task.py) | Creating a task inside a project (`POST /projects/{name}/tasks`) |
| [`03_start_work.py`](03_start_work.py) | Starting time tracking on a task (`POST /work/start`) |
| [`04_stop_work.py`](04_stop_work.py) | Stopping the active time tracking session (`POST /work/stop`) |
| [`05_daily_report.py`](05_daily_report.py) | Generating today's report as Markdown (`GET /reports/daily`) |

Together they walk through a complete, realistic day: create a project,
add a task to it, start working on it, stop working on it, and finally
generate a report of the tracked time.

## Prerequisites

1. Install the HTTP client library used by these examples:

   ```bash
   pip install requests
   ```

   (`requests` is only needed to *consume* the REST interface from a
   client; the server side needs `fastapi` and `uvicorn` instead, see the
   main [requirements.txt](../../requirements.txt).)

2. Start the REST server from the repository root:

   ```bash
   python TimeTrackerREST_Server.py
   ```

   By default it listens on port `8800` (configurable via `rest_port` in
   `config.json`) and serves interactive API documentation at
   `http://localhost:8800/docs`. If you changed the port, update the
   `BASE_URL` constant at the top of each script accordingly.

## Running the examples

Run the scripts in order, from the repository root:

```bash
python examples/REST/01_create_project.py
python examples/REST/02_create_task.py
python examples/REST/03_start_work.py
python examples/REST/04_stop_work.py
python examples/REST/05_daily_report.py
```

`01_create_project.py` and `02_create_task.py` check first whether the
project/task already exists, so it is safe to run the whole sequence more
than once (e.g. on the next day, to add more tracked time before
generating another report).

## Writing your own client

All of the operations exposed via the GUI and CLI are available as REST
endpoints on `TimeTrackerREST_Server.py` — project and task management,
reporting, and more. The interactive documentation at
`http://localhost:8800/docs` (Swagger UI) lists every endpoint together
with its parameters and can be used to try them out directly from the
browser; the underlying machine-readable description is served as OpenAPI
JSON at `http://localhost:8800/openapi.json` and can be loaded into any
REST-aware client generator or tool (e.g. Postman, Insomnia, or `openapi-python-client`).
