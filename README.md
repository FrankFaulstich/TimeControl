# Time Tracker Application ⏱️

A simple, object-oriented Python application for tracking time spent on projects and tasks. All data is stored locally in a `data.json` file.

## Table of Contents

- [Time Tracker Application ⏱️](#time-tracker-application-️)
  - [Table of Contents](#table-of-contents)
  - [Features 🚀](#features-)
  - [Screenshots 📸](#screenshots-)
  - [Prerequisites 📋](#prerequisites-)
  - [Installation 🛠️](#installation-️)
  - [Configuration ⚙️](#configuration-️)
  - [Usage ⚙️](#usage-️)
    - [Running the Streamlit GUI](#running-the-streamlit-gui)
  - [MCP Server 🤖](#mcp-server-)
  - [Building the Documentation 📚](#building-the-documentation-)
  - [Data Storage 🗄️](#data-storage-️)
  - [Contributing 🤝](#contributing-)
  - [License 📜](#license-)

---

## Features 🚀

**Project Management:** Create, list, rename, delete, close, re-open, move, promote, and demote main projects and tasks.

**Time Tracking:** Start, stop, and view the current work session. Automatically stops the previous session when a new one begins.

**Task Priorities:** Every task carries a priority from 0 (lowest, the default) to 9 (highest). Set it when adding or editing a task, adjust it inline right from the Today's Tasks list, and sort that list by priority with a single checkbox.

**Today's Tasks:** The app's default view. Shows every task marked for today, grouped by project, alongside the currently active work session with quick "done"/"edit" actions - no separate main menu to go through first.

**Reporting & Analysis:**

- **Daily & Date Range Reports:** Generate detailed reports for specific days or periods.
- **Detailed Project Reports:** Create in-depth reports for individual main projects or tasks, including:
  - Total time, session count, and average session duration.
  - A timeline of first and last activity.
  - A breakdown of time spent per weekday (e.g., Monday: 2.5 hours, 30%).
  - For main projects, a summary of time distribution across its tasks.
  - For tasks, a day-by-day list of all time entries.
- **Inactivity Tracking:** Identify main projects and tasks that have been inactive for a configurable duration (tasks due today or in the future are never counted as inactive, since they're still actively scheduled).

**Local Data Storage:** All project data and time entries are saved in a `data.json` file in the application's directory.

**Automatic Updates:** The app checks GitHub for a new version once per session and, if one is available, shows a notification right under the version number on every screen, with a one-click button that downloads, installs, and restarts into it.

**Interface:**

- **Streamlit GUI:** A graphical, browser-based user interface (`TimeTrackerSL_GUI.py`).

**SOAP API:** A full-featured SOAP web service (`TimeTrackerSOAP_Server.py`) to integrate TimeControl with other tools or dashboards. See [examples/SOAP](examples/SOAP) for runnable client examples.

**REST API:** A REST web service (`TimeTrackerREST_Server.py`) covering the same operations as the SOAP API, for tools or dashboards that prefer JSON over SOAP/XML. See [examples/REST](examples/REST) for runnable client examples.

**MCP Server (optional):** An [MCP](https://modelcontextprotocol.io/) server (`TimeTrackerMCP_Server.py`) that exposes the app's entire functionality — project/task management, time tracking, reporting, and email import — to an MCP client such as Claude Desktop, so you can manage TimeControl in natural language while keeping the GUI in sync — see [MCP Server](#mcp-server-) below.

**Unit Testing:** Includes comprehensive unit tests in `tests/test_TimeTracker.py` for feature reliability.

---

## Screenshots 📸

The Streamlit GUI groups all actions behind a compact icon toolbar — hover over an icon to see what it does, or click it to open the corresponding menu.

**Today's Tasks:** the default view on every start. The toolbar, the currently active work session (if any), and every task marked for today, grouped by project and showing its priority. The **Show only open tasks** and **Sort by priority** checkboxes filter and reorder the list.

![Today's Tasks view](screenshots/today-view.png)
![Today's Tasks view filtered to open tasks](screenshots/today-view-filtered.png)

**Adding a task:** set a due date, mark it for today, set a priority, and optionally make it recurring.

![Add task dialog](screenshots/add-task.png)

**Reports:** generated as Markdown and automatically copied to the clipboard.

![Daily report](screenshots/daily-report.png)

---

## Prerequisites 📋

- **Python 3.10 - 3.14:** Ensure you have Python 3 installed on your system. You can download it from [python.org](https://www.python.org/).
  *(Note: Python 3.14 is currently not supported on Windows due to dependency issues.)*

---

## Installation 🛠️

Clone the repository:

```bash
git clone https://github.com/FrankFaulstich/TimeControl.git
cd TimeControl
```

Install the required Python packages:

```bash
pip install -r requirements.txt
```

The application will also attempt to self-install missing dependencies on first run.

---

## Configuration ⚙️

The application can be configured via the `config.json` file.

```json
{
    "update": {
        "github_repo": "FrankFaulstich/TimeControl"
    },
    "language": "de",
    "streamlit_port": 8501,
    "soap_port": 8600,
    "rest_port": 8800,
    "mcp_server_enabled": false,
    "mcp_transport": "http",
    "mcp_port": 8700,
    "data_file": "data.json",
    "css_file": "style.css"
}
```

- **`update.github_repo`**: The GitHub repository (username/reponame) to check for new versions.
- **`language`**: The user interface language ("en", "de", "fr", "es", "cs").
- **`soap_port`**: The port on which the SOAP server listens (default: 8600).
- **`rest_port`**: The port on which the REST server listens (default: 8800). See [REST API](#rest-api-).
- **`mcp_server_enabled`**: Whether `TimeTrackerSL_GUI.py` also starts the MCP server when `mcp_transport` is `"http"` (default: `false`). See [MCP Server](#mcp-server-).
- **`mcp_transport`**: `"http"` or `"stdio"` (default: `"http"`). See [MCP Server](#mcp-server-).
- **`mcp_port`**: The port on which the MCP server listens when using the `"http"` transport (default: 8700).

All of these MCP settings can also be changed from the GUI, under **Settings → MCP Server Settings**.

---

## Usage ⚙️

### Running the Streamlit GUI

The GUI is the way to use TimeControl (see [Screenshots](#screenshots-) above). To start it, run:

```bash
python TimeTrackerSL_GUI.py
```

or

```bash
python3 TimeTrackerSL_GUI.py
```

---

## MCP Server 🤖

TimeControl can optionally run a [Model Context Protocol](https://modelcontextprotocol.io/) server (`TimeTrackerMCP_Server.py`), letting an MCP client such as **Claude Desktop** talk to it directly — e.g. *"start work on task X"*, *"create a new project called Y"*, or *"stop what I'm working on"* — while you keep using the GUI at the same time. Both sides read and write the same `data.json`, and the GUI reloads its data on every interaction. It also refreshes itself automatically every few seconds — whenever `mcp_server_enabled` is `true`, and always when `mcp_transport` is `"stdio"` (since a stdio client can be talking to the server independently of that flag, see below) — so you can freely switch back and forth between Claude and the GUI.

It supports two transports, chosen via `mcp_transport` in `config.json`:

- **`http`** (default): a Streamable HTTP server the GUI starts and stops for you as a background process, the same way it already can run the SOAP server. Multiple clients can connect to it at once.
- **`stdio`**: the MCP client (e.g. Claude Desktop) launches `TimeTrackerMCP_Server.py` itself and talks to it over its stdin/stdout - nothing needs to be running beforehand. This is the transport Claude Desktop supports most reliably, so prefer it when connecting Claude Desktop.

**Enabling it:** install the extra dependency and configure it either through the GUI (**Settings → MCP Server Settings**) or directly in `config.json` (see [Configuration](#configuration-️)):

```bash
pip install mcp
```

```json
{
    "mcp_server_enabled": true,
    "mcp_transport": "http",
    "mcp_port": 8700
}
```

With `mcp_server_enabled` set to `true` and `mcp_transport` set to `"http"`, `TimeTrackerSL_GUI.py` starts the MCP server automatically alongside the Streamlit GUI (and stops it again on exit). With `mcp_transport` set to `"stdio"`, the GUI does *not* start it - there is nothing useful for it to start, since a stdio server only makes sense spawned directly by its client - and `mcp_server_enabled`/`mcp_port` are then not used. `mcp` requires Python 3.10+; on older versions the feature is simply unavailable.

You can also run it stand-alone instead (it reads `mcp_transport` from `config.json` the same way):

```bash
python TimeTrackerMCP_Server.py
```

**Available tools:** the server exposes the full functional scope of the app as 33 tools:

- **Main project management:** `add_main_project`, `list_main_projects`, `rename_main_project`, `close_main_project`, `reopen_main_project`, `delete_main_project`, `demote_main_project`, `list_completed_main_projects`, `list_inactive_main_projects`.
- **Task management:** `add_task`, `list_tasks`, `update_task`, `mark_task_done`, `rename_task`, `close_task`, `reopen_task`, `delete_task`, `delete_all_closed_tasks`, `move_task`, `promote_task_to_project`, `list_inactive_tasks`, `cleanup_overdue_today_tasks`, `set_today_flag_for_due_tasks`.
- **Time tracking:** `start_work`, `stop_work`, `get_current_work`. Both the target project and task must already exist for `start_work` — it does not create them for you.
- **Reporting:** `generate_daily_report`, `generate_detailed_daily_report`, `generate_date_range_report`, `generate_task_report`, `generate_main_project_report`.
- **Email import:** `fetch_emails_to_tasks` (requires email import to be configured, see above).
- **Misc:** `get_version`.

`update_task` only changes the fields you actually pass — in particular, a task's due date is preserved automatically if you don't specify one, since updating it always requires re-sending the current value under the hood.

> ⚠️ **Destructive tools:** `delete_task`, `delete_all_closed_tasks`, and `delete_main_project` permanently delete data and cannot be undone. An MCP client should always confirm with you before calling them.

**Connecting Claude Desktop:** the recommended way is the **stdio** transport - set `"mcp_transport": "stdio"` (via the GUI or `config.json`) and add an entry to Claude Desktop's MCP server configuration that launches the script directly:

```json
{
    "mcpServers": {
        "timecontrol": {
            "command": "python3",
            "args": ["/absolute/path/to/TimeTrackerMCP_Server.py"]
        }
    }
}
```

Use the absolute path to the script - Claude Desktop (like most MCP clients) launches it with an undefined working directory, not necessarily the repo root, and does not reliably support a `cwd` override for that even though some setups suggest one. The server accounts for this itself: it always resolves `config.json`/`data.json` relative to its own location on disk, not the process's working directory, so no `cwd` entry is needed.

Claude Desktop then starts and stops the server itself - it does not need to be running beforehand, and the GUI does not start a second copy of it (see above). Alternatively, with `"mcp_transport": "http"` and the server running (either via the GUI or stand-alone), point Claude Desktop at the Streamable HTTP endpoint, `http://127.0.0.1:8700/mcp` (adjust the port to match `mcp_port`), instead. Consult Claude Desktop's current documentation for the exact configuration steps, since these have changed between versions.

## Building the Documentation 📚

This project uses Sphinx to generate documentation from the docstrings in the source code.

1. **Install dependencies:**
    Make sure you have installed the required packages for building the docs:

    ```bash
    pip install -r requirements.txt
    ```

2. **Build the HTML documentation:**
    Navigate to the `docs` directory and use the `make` command:

    ```bash
    cd docs
    make html
    ```

    The generated documentation can be found in `docs/_build/html/index.html`.

---

## Data Storage 🗄️

All your project data, including main projects, tasks, and time entries, is automatically saved in a local file named **`data.json`** in the same directory as the script. This file is created upon the first run if it doesn't exist.

The `data.json` file has the following structure:

```json
{
  "projects": [
    {
      "main_project_name": "Example Main Project",
      "tasks": [
        {
          "task_name": "Example Task 1",
          "status": "open",
          "time_entries": [
            {
              "start_time": "YYYY-MM-DDTHH:MM:SS.ffffff",
              "end_time": "YYYY-MM-DDTHH:MM:SS.ffffff"
            },
            {
              "start_time": "YYYY-MM-DDTHH:MM:SS.ffffff"
              // "end_time" is missing if the entry is still active
            }
          ]
        },
        // ... other tasks
      ]
    },
    // ... other main projects
  ]
}
```

Time entries are stored in **ISO 8601 format** (e.g., `"2025-09-12T09:30:00.123456"`). If an `end_time` is missing for a `time_entry`, it means that time tracking is currently active for that task.

---

## Contributing 🤝

Contributions are welcome\! If you have any suggestions for improvements or new features, please feel free to:

- Fork the repository.
- Create a new branch (`git checkout -b feature/your-feature-name`).
- Make your changes.
- Commit your changes (`git commit -m 'Add some Feature'`).
- Push to the branch (`git push origin feature/your-feature-name`).
- Open a Pull Request.

Please ensure your code follows the existing style and **includes relevant unit tests** for new functionality.

---

## License 📜

This project is licensed under the **MIT License** - see the `LICENSE.md` file for details. (You might want to create a `LICENSE.md` file in your repository.)
