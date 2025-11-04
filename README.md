# Time Tracker Application ⏱️

A simple, object-oriented Python application for tracking time spent on projects and sub-projects. All data is stored locally in a `data.json` file.

## Table of Contents

- [Time Tracker Application ⏱️](#time-tracker-application-️)
  - [Table of Contents](#table-of-contents)
  - [Features 🚀](#features-)
  - [Prerequisites 📋](#prerequisites-)
  - [Installation 🛠️](#installation-️)
  - [Configuration ⚙️](#configuration-️)
  - [Usage ⚙️](#usage-️)
    - [Running the Interactive CLI](#running-the-interactive-cli)
    - [Running the MCP / API Server](#running-the-mcp--api-server)
    - [CLI Menu Options](#cli-menu-options)
  - [Building the Documentation 📚](#building-the-documentation-)
  - [Data Storage 🗄️](#data-storage-️)
  - [Contributing 🤝](#contributing-)
  - [License 📜](#license-)

---

## Features 🚀

**Project Management:** Create, list, and delete main projects.

**Sub-Project Management:** Add, list, and delete sub-projects within main projects.

**Time Tracking:** Start, stop, and view the current work session. Automatically stops the previous session when a new one begins.

**Reporting & Analysis:**

- **Daily & Date Range Reports:** Generate detailed reports for specific days or periods.
- **Detailed Project Reports:** Create in-depth reports for individual main or sub-projects, including:
  - Total time, session count, and average session duration.
  - A timeline of first and last activity.
  - A breakdown of time spent per weekday (e.g., Monday: 2.5 hours, 30%).
  - For main projects, a summary of time distribution across its sub-projects.
  - For sub-projects, a day-by-day list of all time entries.
- **Inactivity Tracking:** Identify main and sub-projects that have been inactive for a configurable duration.

**Local Data Storage:** All project data and time entries are saved in a `data.json` file in the application's directory.

**Automatic Updates:** The application can check for new versions on GitHub upon exit and install them on the next start.

**Multiple Interfaces:**

- **Interactive CLI:** A user-friendly command-line interface (`TimeTrackerCLI.py`) for manual operations.
- **MCP & API Server:** An alternative server (`TimeTrackerMCP.py`) provides a machine-controllable interface via stdin/stdout and a full HTTPS REST API for programmatic control.

**Unit Testing:** Includes comprehensive unit tests in `test_TimeTracker.py` for feature reliability.

---

## Prerequisites 📋

- **Python 3.x:** Ensure you have Python 3 installed on your system. You can download it from [python.org](https://www.python.org/).

---

## Installation 🛠️

Clone the repository:

```bash
git clone [https://github.com/FrankFaulstich/TimeControl.git](https://github.com/FrankFaulstich/TimeControl.git)
cd TimeControl```

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
    }
}
```

- **`update.github_repo`**: The GitHub repository (username/reponame) to check for new versions.

---

## Usage ⚙️

### Running the Interactive CLI

To start the interactive command-line interface, run:

```bash
python TimeTrackerCLI.py
```

### Running the MCP / API Server

The server provides programmatic access via standard I/O and a REST API. To start it, run:

```bash
python TimeTrackerMCP.py
```

**Note on HTTPS:** The API server requires an SSL certificate. You can generate a local, self-signed certificate using `openssl`. The server looks for `cert.pem` and `key.pem` in the project directory.

```bash
openssl req -x509 -newkey rsa:4096 -nodes -out cert.pem -keyout key.pem -days 365 -subj "/CN=localhost"
```

- The **MCP interface** accepts JSON commands on `stdin` (e.g., `{"command": "list_main_projects", "params": {}}`).
- The **API interface** runs by default on `https://localhost:8443`. You can access endpoints like `GET /projects` or `GET /version`.

### CLI Menu Options

The interactive CLI provides a structured menu for all operations.

**Main Menu:**

```text
=== Time Control [version] ===
--- Main Menu ---
1. Start work on sub-project
2. Show current work
3. Stop current work
------------------------------------------------
4. Handle projects and sub-projects
5. Reporting
------------------------------------------------
0. Exit
```

**Project Management Submenu (Option 4):**

```text
--- Project Management ---
1.  Add Main Project
2.  List Main Projects
3.  Rename Main Project
4.  Delete Main Project
5.  List Inactive Main Projects
--------------------------------
6.  Add Sub-Project
7.  List Sub-Projects
8.  Rename Sub-Project
9.  Delete Sub-Project
10. Move Sub-Project
11. List Inactive Sub-Projects
--------------------------------
0.  Back to Main Menu
```

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

All your project data, including main projects, sub-projects, and time entries, is automatically saved in a local file named **`data.json`** in the same directory as the script. This file is created upon the first run if it doesn't exist.

The `data.json` file has the following structure:

```json
{
  "projects": [
    {
      "main_project_name": "Example Main Project",
      "sub_projects": [
        {
          "sub_project_name": "Example Sub-Project 1",
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
        // ... other sub-projects
      ]
    },
    // ... other main projects
  ]
}
```

Time entries are stored in **ISO 8601 format** (e.g., `"2025-09-12T09:30:00.123456"`). If an `end_time` is missing for a `time_entry`, it means that time tracking is currently active for that sub-project.

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
