# SOAP Interface Examples

This folder contains small, self-contained Python scripts that demonstrate
how to talk to TimeControl's SOAP interface (`TimeTrackerSOAP_Server.py`).
Each script performs exactly one SOAP call and prints the result — none of
them require any user interaction.

| Script | Demonstrates |
| --- | --- |
| [`01_create_project.py`](01_create_project.py) | Creating a main project (`add_main_project`) |
| [`02_create_task.py`](02_create_task.py) | Creating a task inside a project (`add_task`) |
| [`03_start_work.py`](03_start_work.py) | Starting time tracking on a task (`start_work`) |
| [`04_stop_work.py`](04_stop_work.py) | Stopping the active time tracking session (`stop_work`) |
| [`05_daily_report.py`](05_daily_report.py) | Generating today's report as Markdown (`generate_daily_report`) |

Together they walk through a complete, realistic day: create a project,
add a task to it, start working on it, stop working on it, and finally
generate a report of the tracked time.

## Prerequisites

1. Install the SOAP client library used by these examples:

   ```bash
   pip install zeep
   ```

   (`zeep` is only needed to *consume* the SOAP interface from a client;
   the server side already ships with `spyne`, see the main
   [requirements.txt](../../requirements.txt).)

2. Start the SOAP server from the repository root:

   ```bash
   python TimeTrackerSOAP_Server.py
   ```

   By default it listens on port `8600` (configurable via `soap_port` in
   `config.json`) and serves its WSDL at `http://localhost:8600/?wsdl`.
   If you changed the port, update the `SOAP_URL` constant at the top of
   each script accordingly.

## Running the examples

Run the scripts in order, from the repository root:

```bash
python examples/SOAP/01_create_project.py
python examples/SOAP/02_create_task.py
python examples/SOAP/03_start_work.py
python examples/SOAP/04_stop_work.py
python examples/SOAP/05_daily_report.py
```

`01_create_project.py` and `02_create_task.py` check first whether the
project/task already exists, so it is safe to run the whole sequence more
than once (e.g. on the next day, to add more tracked time before
generating another report).

## Writing your own client

All of the operations exposed via the GUI and CLI are available as SOAP
methods on `TimeControlService` in `TimeTrackerSOAP_Server.py` — project
and task management, reporting, and more. The WSDL at
`http://localhost:8600/?wsdl` is self-describing and can be browsed
directly, or loaded into any SOAP-capable client library or tool (e.g.
`zeep` for Python, or SoapUI for manual exploration).
