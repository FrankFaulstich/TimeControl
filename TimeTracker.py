import json
import os
from datetime import datetime, timedelta

import sys
import subprocess
try:
    # Python 3.8+ for importlib.metadata
    from importlib.metadata import distributions, PackageNotFoundError
except ImportError:
    from importlib_metadata import distributions, PackageNotFoundError
try:
    import pyperclip
except ImportError:
    pyperclip = None  # Set to None if the library is not installed
try:
    # For version comparison in the update mechanism
    from packaging.version import parse as parse_version
except ImportError:
    parse_version = None


class TimeTracker:
    """
    Manages time tracking for various main and sub-projects.
    
    The data is loaded from and saved to a JSON file.
    """
    VERSION = "1.5"

    def __init__(self, file_path='data.json'):
        """
        Initializes the TimeTracker, checks for dependencies, and loads data from the JSON file.

        :param file_path: The path to the JSON file where data is stored. Defaults to 'data.json'.
        :type file_path: str
        """
        self._check_dependencies()
        self.file_path = file_path
        self.data = self._load_data()

    def _check_dependencies(self):
        """
        Checks if all packages from requirements.txt are installed.
        If not, it attempts to install them and then exits the program.
        """
        requirements_path = 'requirements.txt'
        if not os.path.exists(requirements_path):
            return # requirements.txt not found, skip check

        try:
            with open(requirements_path, 'r') as f:
                requirements = [line.strip() for line in f if line.strip() and not line.startswith('#')]
        except IOError as e:
            print(f"Warning: Could not read {requirements_path}. Error: {e}")
            return

        try:
            installed_packages_dist = distributions()
            installed_packages = {dist.metadata['Name'].lower() for dist in installed_packages_dist}
            
            missing_packages = []
            for req in requirements: # e.g., "requests==2.28.1"
                # A simple check for the package name, ignoring version specifiers for now
                req_name = req.split('==')[0].split('>=')[0].split('<=')[0].split('<')[0].split('>')[0].strip()
                if req_name.lower() not in installed_packages:
                    missing_packages.append(req)

            if missing_packages:
                print("Some required packages are missing. Attempting to install them...")
                try:
                    for package in missing_packages:
                        print(f"Installing {package}...")
                        subprocess.check_call([sys.executable, "-m", "pip", "install", package])
                    
                    print("\nDependencies installed successfully.")
                    print("Please restart the application for the changes to take effect.")
                    sys.exit(0)
                except subprocess.CalledProcessError:
                    print(f"\nError: Failed to install dependencies. Please install them manually using: pip install -r {requirements_path}")
                    sys.exit(1)
        except Exception as e:
            print(f"An unexpected error occurred during dependency check: {e}")

    def _load_data(self):
        """
        Loads the project data from the configured JSON file.
        If the file does not exist, an empty data dictionary is returned.

        :return: A dictionary containing the loaded project data.
        :rtype: dict
        """
        if os.path.exists(self.file_path):
            with open(self.file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        else:
            return {"projects": []}

    def _save_data(self):
        """
        Saves the current project data to the configured JSON file.
        """
        with open(self.file_path, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, indent=2)

    def _copy_to_clipboard(self, text):
        """
        Copies the given text to the system clipboard.
        Handles cases where the 'pyperclip' library is not installed.

        :param text: The text to be copied.
        :type text: str
        """
        if pyperclip:
            try:
                pyperclip.copy(text)
                print("Info: Report content has been copied to the clipboard.")
            except pyperclip.PyperclipException as e:
                print(f"Warning: Could not copy to clipboard. Error: {e}")
        else:
            print("Warning: Could not copy to clipboard. Please install 'pyperclip' (`pip install pyperclip`).")

    def _format_duration(self, duration_td):
        """
        Formats a timedelta duration into a string with hours and DLP.
        1 DLP = 40 hours.

        :param duration_td: The timedelta object to format.
        :type duration_td: timedelta
        :return: The formatted string, e.g., "8,000 hours (0,200 DLP)".
        :rtype: str
        """
        hours = duration_td.total_seconds() / 3600
        dlp = hours / 40
        hours_str = f"{hours:.3f}".replace('.', ',')
        dlp_str = f"{dlp:.3f}".replace('.', ',')
        return f"{hours_str} hours ({dlp_str} DLP)"

    def get_version(self):
        """
        Returns the current version of the TimeTracker application.

        :return: The application version string.
        :rtype: str
        """
        return self.VERSION

    def add_main_project(self, main_project_name):
        """
        Adds a new main project.

        :param main_project_name: The name of the main project to add.
        :type main_project_name: str
        """
        new_project = {
            "main_project_name": main_project_name,
            "sub_projects": []
        }
        self.data["projects"].append(new_project)
        self._save_data()

    def list_main_projects(self):
        """
        Returns a list of all main project names.

        :return: A list of main project names.
        :rtype: list[str]
        """
        return [project["main_project_name"] for project in self.data["projects"]]

    def delete_main_project(self, main_project_name):
        """
        Deletes a main project along with all associated sub-projects and time entries.

        :param main_project_name: The name of the main project to delete.
        :type main_project_name: str
        :return: True if the project was successfully deleted, otherwise False.
        :rtype: bool
        """
        initial_count = len(self.data["projects"])
        self.data["projects"] = [
            project for project in self.data["projects"] if project["main_project_name"] != main_project_name
        ]
        if len(self.data["projects"]) < initial_count:
            self._save_data()
            return True
        return False

    def rename_main_project(self, old_name, new_name):
        """
        Renames a main project.

        :param old_name: The current name of the main project to rename.
        :type old_name: str
        :param new_name: The new name for the main project.
        :type new_name: str
        :return: True if renaming was successful, False otherwise (e.g., project not found,
                 or new name already exists).
        :rtype: bool
        """
        # Check if the new name already exists to avoid duplicates
        if any(p["main_project_name"] == new_name for p in self.data["projects"]):
            return False

        for project in self.data["projects"]:
            if project["main_project_name"] == old_name:
                project["main_project_name"] = new_name
                self._save_data()
                return True
        return False # Old name not found

    def add_sub_project(self, main_project_name, sub_project_name):
        """
        Adds a new sub-project to a specified main project.

        :param main_project_name: The name of the main project to add the sub-project to.
        :type main_project_name: str
        :param sub_project_name: The name of the sub-project to add.
        :type sub_project_name: str
        :return: True if the sub-project was added successfully, otherwise False (if main project not found).
        :rtype: bool
        """
        for project in self.data["projects"]:
            if project["main_project_name"] == main_project_name:
                new_sub_project = {
                    "sub_project_name": sub_project_name,
                    "time_entries": []
                }
                project["sub_projects"].append(new_sub_project)
                self._save_data()
                return True
        return False

    def list_sub_projects(self, main_project_name):
        """
        Returns a list of all sub-project names for a given main project.

        :param main_project_name: The name of the main project.
        :type main_project_name: str
        :return: A list of sub-project names or None if the main project was not found.
        :rtype: list[str] or None
        """
        for project in self.data["projects"]:
            if project["main_project_name"] == main_project_name:
                return [sub_project["sub_project_name"] for sub_project in project["sub_projects"]]
        return None

    def delete_sub_project(self, main_project_name, sub_project_name):
        """
        Deletes a sub-project from a main project.

        :param main_project_name: The name of the main project.
        :type main_project_name: str
        :param sub_project_name: The name of the sub-project to delete.
        :type sub_project_name: str
        :return: True if the sub-project was deleted, otherwise False.
        :rtype: bool
        """
        for project in self.data["projects"]:
            if project["main_project_name"] == main_project_name:
                initial_count = len(project["sub_projects"])
                project["sub_projects"] = [
                    sp for sp in project["sub_projects"] if sp["sub_project_name"] != sub_project_name
                ]
                if len(project["sub_projects"]) < initial_count:
                    self._save_data()
                    return True
        return False

    def rename_sub_project(self, main_project_name, old_sub_project_name, new_sub_project_name):
        """
        Renames a sub-project within a given main project.

        :param main_project_name: The name of the main project containing the sub-project.
        :type main_project_name: str
        :param old_sub_project_name: The current name of the sub-project to rename.
        :type old_sub_project_name: str
        :param new_sub_project_name: The new name for the sub-project.
        :type new_sub_project_name: str
        :return: True if renaming was successful, False otherwise (e.g., project not found,
                 or new name already exists).
        :rtype: bool
        """
        for project in self.data["projects"]:
            if project["main_project_name"] == main_project_name:
                # Check if the new name already exists
                if any(sp["sub_project_name"] == new_sub_project_name for sp in project["sub_projects"]):
                    return False # New name is already in use

                # Find the sub-project to rename
                for sub_project in project["sub_projects"]:
                    if sub_project["sub_project_name"] == old_sub_project_name:
                        sub_project["sub_project_name"] = new_sub_project_name
                        self._save_data()
                        return True
                return False # Old sub-project not found
        return False # Main project not found

    def move_sub_project(self, old_main_project_name, sub_project_name, new_main_project_name):
        """
        Moves a sub-project from one main project to another.

        :param old_main_project_name: The name of the source main project.
        :type old_main_project_name: str
        :param sub_project_name: The name of the sub-project to move.
        :type sub_project_name: str
        :param new_main_project_name: The name of the destination main project.
        :type new_main_project_name: str
        :return: A tuple (bool, str) indicating success and a message.
        :rtype: tuple(bool, str)
        """
        source_project = None
        dest_project = None
        sub_project_to_move = None

        for p in self.data["projects"]:
            if p["main_project_name"] == old_main_project_name:
                source_project = p
            if p["main_project_name"] == new_main_project_name:
                dest_project = p

        if not source_project:
            return False, f"Source main project '{old_main_project_name}' not found."
        if not dest_project:
            return False, f"Destination main project '{new_main_project_name}' not found."

        # Check if sub-project with same name exists in destination
        if any(sp["sub_project_name"] == sub_project_name for sp in dest_project["sub_projects"]):
            return False, f"A sub-project named '{sub_project_name}' already exists in '{new_main_project_name}'."

        # Find and remove sub-project from source
        for i, sp in enumerate(source_project["sub_projects"]):
            if sp["sub_project_name"] == sub_project_name:
                sub_project_to_move = source_project["sub_projects"].pop(i)
                break

        if sub_project_to_move:
            dest_project["sub_projects"].append(sub_project_to_move)
            self._save_data()
            return True, f"Sub-project '{sub_project_name}' moved successfully."
        return False, f"Sub-project '{sub_project_name}' not found in '{old_main_project_name}'."

    def promote_sub_project(self, main_project_name, sub_project_name_to_promote):
        """
        Promotes a sub-project to a new main project.

        The time entries of the sub-project are preserved and moved to a new sub-project
        named 'General' within the new main project.

        :param main_project_name: The name of the current main project.
        :type main_project_name: str
        :param sub_project_name_to_promote: The name of the sub-project to promote.
        :type sub_project_name_to_promote: str
        :return: A tuple (bool, str) indicating success and a message.
        :rtype: tuple(bool, str)
        """
        # Check if a main project with the new name already exists
        if any(p["main_project_name"] == sub_project_name_to_promote for p in self.data["projects"]):
            return False, f"A main project named '{sub_project_name_to_promote}' already exists."

        # Find the source project in a more readable way
        source_project = None
        for p in self.data["projects"]:
            if p["main_project_name"] == main_project_name:
                source_project = p
                break
        if not source_project:
            return False, f"Source main project '{main_project_name}' not found."

        # Find the index of the sub-project to promote
        sub_project_index = None
        for i, sp in enumerate(source_project["sub_projects"]):
            if sp["sub_project_name"] == sub_project_name_to_promote:
                sub_project_index = i
                break
        if sub_project_index is None:
            return False, f"Sub-project '{sub_project_name_to_promote}' not found in '{main_project_name}'."

        # Remove sub-project from old main project and get its data
        sub_project_data = source_project["sub_projects"].pop(sub_project_index)
        time_entries = sub_project_data.get("time_entries", [])

        # Create the new main project
        new_main_project = {
            "main_project_name": sub_project_name_to_promote,
            "sub_projects": [{"sub_project_name": "General", "time_entries": time_entries}]
        }
        self.data["projects"].append(new_main_project)
        self._save_data()
        return True, f"Sub-project '{sub_project_name_to_promote}' was promoted to a new main project."

    def start_work(self, main_project_name, sub_project_name):
        """
        Starts a new time tracking session for a sub-project by saving the start time.
        Any currently active session is stopped before starting the new one.

        :param main_project_name: The parent main project name.
        :type main_project_name: str
        :param sub_project_name: The sub-project for which work is being started.
        :type sub_project_name: str
        :return: True if work was started successfully, otherwise False.
        :rtype: bool
        """
        self.stop_work()
        
        for project in self.data["projects"]:
            if project["main_project_name"] == main_project_name:
                for sub_project in project["sub_projects"]:
                    if sub_project["sub_project_name"] == sub_project_name:
                        new_entry = {
                            "start_time": datetime.now().isoformat()
                        }
                        sub_project["time_entries"].append(new_entry)
                        self._save_data()
                        return True
        return False

    def stop_work(self):
        """
        Stops the currently active time tracking session by adding the end time 
        to the most recently started entry.

        :return: True if a session was stopped successfully, otherwise False.
        :rtype: bool
        """
        for project in reversed(self.data["projects"]):
            for sub_project in reversed(project["sub_projects"]):
                if sub_project["time_entries"] and "end_time" not in sub_project["time_entries"][-1]:
                    sub_project["time_entries"][-1]["end_time"] = datetime.now().isoformat()
                    self._save_data()
                    return True
        return False

    def get_current_work(self):
        """
        Finds and returns the currently active sub-project, if any.

        An active sub-project is one with a time entry that has a 'start_time' but no 'end_time'.

        :return: A dictionary containing 'main_project_name', 'sub_project_name', and 'start_time'
                 of the active session, or None if no session is active.
        :rtype: dict or None
        """
        for project in reversed(self.data["projects"]):
            for sub_project in reversed(project["sub_projects"]):
                if sub_project["time_entries"] and "end_time" not in sub_project["time_entries"][-1]:
                    return {
                        "main_project_name": project["main_project_name"],
                        "sub_project_name": sub_project["sub_project_name"],
                        "start_time": sub_project["time_entries"][-1]["start_time"]
                    }
        return None

    def list_inactive_sub_projects(self, inactive_weeks):
        """
        Lists sub-projects that have not had any activity (completed time entry) 
        within the specified number of weeks.
        Currently running sessions are ignored (i.e., not listed as inactive).
        Sub-projects with no time entries are also ignored.

        :param inactive_weeks: The number of weeks defining the inactivity threshold.
        :type inactive_weeks: int
        :return: A list of dictionaries, each containing 'main_project', 'sub_project', 
                 and the 'last_activity' timestamp (formatted).
        :rtype: list[dict]
        """
        cutoff_date = datetime.now() - timedelta(weeks=inactive_weeks)
        inactive_projects = []

        for project in self.data["projects"]:
            for sub_project in project["sub_projects"]:
                if not sub_project.get("time_entries"):
                    # Ignore sub-projects with no entries
                    continue
                
                # Check if the project is currently running (active)
                last_entry = sub_project["time_entries"][-1]
                if "end_time" not in last_entry:
                    continue # Skip if currently running

                latest_timestamp = None
                
                # Find the latest timestamp from all completed entries
                for entry in sub_project["time_entries"]:
                    time_to_check = None
                    if "end_time" in entry:
                        time_to_check = datetime.fromisoformat(entry["end_time"])
                    elif "start_time" in entry:
                        # Fallback: use start_time if no end_time exists (for edge cases, though not ideal)
                        time_to_check = datetime.fromisoformat(entry["start_time"])
                    
                    if time_to_check:
                        if latest_timestamp is None or time_to_check > latest_timestamp:
                            latest_timestamp = time_to_check

                # Check for inactivity
                if latest_timestamp and latest_timestamp < cutoff_date:
                    inactive_projects.append({
                        "main_project": project["main_project_name"],
                        "sub_project": sub_project["sub_project_name"],
                        "last_activity": latest_timestamp.strftime("%Y-%m-%d %H:%M:%S")
                    })

        return inactive_projects

    def list_inactive_main_projects(self, inactive_weeks):
        """
        Lists main projects that have not had any activity (completed time entry 
        in any contained sub-project) within the specified number of weeks.
        Main projects with currently running sub-projects are ignored.

        :param inactive_weeks: The number of weeks defining the inactivity threshold.
        :type inactive_weeks: int
        :return: A list of dictionaries, each containing 'main_project' and the 
                 'last_activity' timestamp (formatted).
        :rtype: list[dict]
        """
        cutoff_date = datetime.now() - timedelta(weeks=inactive_weeks)
        inactive_main_projects = []

        for project in self.data["projects"]:
            latest_activity = None
            is_active = False

            for sub_project in project["sub_projects"]:
                for entry in sub_project.get("time_entries", []):
                    # 1. Check for running session (activity right now)
                    if "start_time" in entry and "end_time" not in entry:
                        is_active = True
                        break # Break from time entries loop
                    
                    # 2. Find the latest completed activity
                    if "end_time" in entry:
                        activity_time = datetime.fromisoformat(entry["end_time"])
                        if latest_activity is None or activity_time > latest_activity:
                            latest_activity = activity_time
                
                if is_active:
                    break # Break from sub-projects loop

            # If any sub-project is running, the main project is active, so skip it.
            if is_active:
                continue
            
            # If no activities were found at all, skip.
            if latest_activity is None:
                continue

            # Check if the latest activity is older than the cutoff date
            if latest_activity < cutoff_date:
                inactive_main_projects.append({
                    "main_project": project["main_project_name"],
                    "last_activity": latest_activity.strftime("%Y-%m-%d %H:%M:%S")
                })

        return inactive_main_projects

    def generate_daily_report(self, report_date=None):
        """
        Generates a daily report in Markdown format, listing only projects 
        with time entries for the specified day.

        Time durations are formatted as decimal numbers using a comma as the decimal separator.

        :param report_date: Optional. The date (as a datetime.date object) for which the report should be generated. 
                            If None, today's date is used.
        :type report_date: datetime.date or None
        :return: The formatted daily report as a Markdown string.
        :rtype: str
        """
        report = []
        today = report_date if report_date else datetime.now().date()
        total_daily_time = timedelta()

        for project in self.data["projects"]:
            main_project_total_time = timedelta()
            sub_project_details = []

            for sub_project in project["sub_projects"]:
                sub_project_total_time = timedelta()
                
                for entry in sub_project["time_entries"]:
                    try:
                        start_time = datetime.fromisoformat(entry["start_time"])
                        if "end_time" in entry:
                            end_time = datetime.fromisoformat(entry["end_time"])
                            # Check if the entry is for the specified date
                            if start_time.date() == today:
                                duration = end_time - start_time
                                sub_project_total_time += duration
                    except (ValueError, KeyError):
                        continue

                # Add to report only if time was tracked for this sub-project on the specified date
                if sub_project_total_time.total_seconds() > 0:
                    hours = sub_project_total_time.total_seconds() / 3600
                    hours_str = f"{hours:.3f}".replace('.', ',')
                    sub_project_details.append(f"- {sub_project['sub_project_name']}: {hours_str} hours")
                    main_project_total_time += sub_project_total_time

            # Add main project and its sub-projects to report if it has entries for the specified date
            if main_project_total_time.total_seconds() > 0:
                total_daily_time += main_project_total_time
                hours = main_project_total_time.total_seconds() / 3600
                hours_str = f"{hours:.3f}".replace('.', ',')
                report.append(f"## {project['main_project_name']} ({hours_str} hours)\n")
                report.extend(sub_project_details)
                report.append("\n")
        
        # Add total daily time to the report
        if total_daily_time.total_seconds() > 0:
            total_hours = total_daily_time.total_seconds() / 3600
            total_hours_str = f"{total_hours:.3f}".replace('.', ',')
            
            report.insert(0, f"# Daily Time Report: {today.strftime('%Y-%m-%d')}\n")
            report.append(f"\n**Total Daily Time: {total_hours_str} hours**")
            report.append("\nGenerated by TimeControl")
            report.append("https://github.com/frankfaulstich/TimeControl")
        else:
            report.append(f"No time tracked for {today.strftime('%Y-%m-%d')}.")
        
        report_text = "\n".join(report)
        self._copy_to_clipboard(report_text)
        return report_text

    def generate_date_range_report(self, start_date, end_date):
        """
        Generates a report for a specific date range in Markdown format.

        :param start_date: The start date of the report period (datetime.date object).
        :type start_date: datetime.date
        :param end_date: The end date of the report period (datetime.date object).
        :type end_date: datetime.date
        :return: The formatted report as a Markdown string.
        :rtype: str
        """
        report = []
        total_period_time = timedelta()

        for project in self.data["projects"]:
            main_project_total_time = timedelta()
            sub_project_details = []

            for sub_project in project["sub_projects"]:
                sub_project_total_time = timedelta()
                
                for entry in sub_project["time_entries"]:
                    try:
                        start_time = datetime.fromisoformat(entry["start_time"])
                        if "end_time" in entry:
                            end_time = datetime.fromisoformat(entry["end_time"])
                            # Check if the entry is within the specified date range
                            if start_date <= start_time.date() <= end_date:
                                duration = end_time - start_time
                                sub_project_total_time += duration
                    except (ValueError, KeyError):
                        continue

                if sub_project_total_time.total_seconds() > 0:
                    formatted_time = self._format_duration(sub_project_total_time)
                    sub_project_details.append(f"- {sub_project['sub_project_name']}: {formatted_time}")
                    main_project_total_time += sub_project_total_time

            if main_project_total_time.total_seconds() > 0:
                total_period_time += main_project_total_time
                formatted_time = self._format_duration(main_project_total_time)
                report.append(f"## {project['main_project_name']} ({formatted_time})\n")
                report.extend(sub_project_details)
                report.append("\n")
        
        if total_period_time.total_seconds() > 0:
            total_hours_str = self._format_duration(total_period_time)
            
            report.insert(0, f"# Time Report: {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}\n")
            report.append(f"\n**Total Time in Period: {total_hours_str}**")
            report.append("\nGenerated by TimeControl")
            report.append("https://github.com/frankfaulstich/TimeControl")
        else:
            report.append(f"No time tracked between {start_date.strftime('%Y-%m-%d')} and {end_date.strftime('%Y-%m-%d')}.")
        
        report_text = "\n".join(report)
        self._copy_to_clipboard(report_text)
        return report_text