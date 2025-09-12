# Time Tracker Application ⏱️

A simple, object-oriented Python application for tracking time spent on projects and sub-projects. All data is stored locally in a data.json file.

## Table of Contents

1. Features
2. Prerequisites
3. Installation
4. Usage
    1. Running the Application
    2. Menu Options
5. Data Storage
6. Contributing
7. License

## Features 🚀

Project Management: Create, list, and delete main projects.

Sub-Project Management: Add, list, and delete sub-projects within main projects.

Time Tracking:

- Start tracking time for a specific sub-project.
- Automatically stops the previous time entry if you start a new one.
- Stop tracking time for the currently active sub-project.

Local Data Storage: All project data and time entries are saved in a data.json file in the application's directory.

Object-Oriented Design: Separates data handling logic from the user interface.

## Prerequisites 📋

    Python 3.x: Ensure you have Python 3 installed on your system. You can download it from python.org.

## Installation 🛠️

Clone the repository:

```bash
git clone https://github.com/FrankFaulstich/TimeControl.git
cd TimeControl
```

Place the Python files:

Make sure you have the following files in the root of your repository:

- TimeTrackerCLI.py (containing a simple command-line interface for interacting with the TimeTracker)
- TimeTracker.py (containing the TimeTracker class)
- data.json (this file will be created automatically on the first run if it doesn't exist)

## Usage ⚙️

### Running the Application

To start the time tracker, open your terminal or command prompt, navigate to the project directory, and run the main Python script:

```Bash
python TimeTrackerCLI.py
```
or

```Bash
python3 TimeTrackerCLI.py
```

### Menu Options

Once the application is running, you will see a menu with the following options:
```
1 Add new main project
2 List main projects
3 Delete main project
4 Add new sub-project
5 List sub-projects
6 Delete sub-project
7 Start tracking time
8 Stop tracking time
0 Exit
````

The application will prompt you for necessary information for each action.

## Data Storage 🗄️

All your project data, including main projects, sub-projects, and time entries, is automatically saved in a local file named data.json in the same directory as the script. This file is created upon the first run if it doesn't exist.

The data.json file has the following structure:
JSON
```JSON
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

    Time entries are stored in ISO 8601 format (e.g., "2025-09-12T09:30:00.123456").

    If an end_time is missing for a time_entry, it means that time tracking is currently active for that sub-project.
````

Contributing 🤝

Contributions are welcome! If you have any suggestions for improvements or new features, please feel free to:

- Fork the repository.
- Create a new branch (git checkout -b feature/your-feature-name).
- Make your changes.
- Commit your changes (git commit -m 'Add some Feature').
- Push to the branch (git push origin feature/your-feature-name).
- Open a Pull Request.

Please ensure your code follows the existing style and includes relevant tests if applicable.

## License 📜

This project is licensed under the MIT License - see the LICENSE.md file for details. (You might want to create a LICENSE.md file in your repository.)
