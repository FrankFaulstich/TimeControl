# Time Tracker Application ⏱️

A simple, object-oriented Python application for tracking time spent on projects and sub-projects. All data is stored locally in a `data.json` file.

## Table of Contents

- [Time Tracker Application ⏱️](#time-tracker-application-️)
  - [Table of Contents](#table-of-contents)
  - [Features 🚀](#features-)
  - [Prerequisites 📋](#prerequisites-)
  - [Installation 🛠️](#installation-️)
  - [Usage ⚙️](#usage-️)
    - [Running the Application](#running-the-application)
    - [Menu Options](#menu-options)
  - [Data Storage 🗄️](#data-storage-️)
  - [Contributing 🤝](#contributing-)
  - [License 📜](#license-)

---

## Features 🚀

**Project Management:** Create, list, and delete main projects.

**Sub-Project Management:** Add, list, and delete sub-projects within main projects.

**Time Tracking:**

- Start tracking time for a specific sub-project.
- Automatically stops the previous time entry if you start a new one.
- Stop tracking time for the currently active sub-project.

**Reporting & Inactivity Analysis (NEW):**

- **Daily Report:** Generate a daily report for today or a specific date. Time totals are output in decimal hours with a **comma** as the decimal separator (e.g., `1,500 hours`).
- **Inactive Main Projects:** List main projects that have been inactive for a user-specified number of weeks.
- **Inactive Sub-Projects:** List sub-projects that have been inactive for a user-specified number of weeks.
- **Date Range Report:** Generate a report for a user-defined date range. Time totals are shown in hours and a custom "DLP" unit (1 DLP = 40 hours).

**Local Data Storage:** All project data and time entries are saved in a `data.json` file in the application's directory.

**Object-Oriented Design:** Separates data handling logic from the user interface.

**Unit Testing:** Includes comprehensive unit tests in `test_TimeTracker.py` for feature reliability.

---

## Prerequisites 📋

- **Python 3.x:** Ensure you have Python 3 installed on your system. You can download it from [python.org](https://www.python.org/).

---

## Installation 🛠️

Clone the repository:

```bash
git clone [https://github.com/FrankFaulstich/TimeControl.git](https://github.com/FrankFaulstich/TimeControl.git)
cd TimeControl
````

Place the Python files:

Make sure you have the following files in the root of your repository:

- **`TimeTrackerCLI.py`** (containing a simple command-line interface for interacting with the TimeTracker)
- **`TimeTracker.py`** (containing the TimeTracker class)
- **`test_TimeTracker.py`** (containing the unit tests)
- **`data.json`** (this file will be created automatically on the first run if it doesn't exist)

---

## Usage ⚙️

### Running the Application

To start the time tracker, open your terminal or command prompt, navigate to the project directory, and run the main Python script:

```bash
python TimeTrackerCLI.py
```

or

```bash
python3 TimeTrackerCLI.py
```

### Menu Options

Once the application is running, you will see an updated menu structure with the following options:

```Text
--- Time Tracking Menu ---
1  Add new main project
2  List main projects
3  List inactive main-projects
4  Delete main project
------------------------------------------------
5  Add new sub-project
6  List sub-projects
7  List inactive sub-projects
8  Delete sub-project
------------------------------------------------
9  Start work on sub-project
10 Stop current work
------------------------------------------------
11 Generate daily report (Today)
12 Generate a daily report for a specific day
13 Generate report for a date range
------------------------------------------------
0 Exit
```

The application will prompt you for necessary information for each action.

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
