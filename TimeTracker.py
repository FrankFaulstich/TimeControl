import json
import os
from i18n import _
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
    VERSION = "1.8"

    def __init__(self, file_path='data.json'):
        """
        Initializes the TimeTracker, checks for dependencies, and loads data from the JSON file.

        :param file_path: The path to the JSON file where data is stored. Defaults to 'data.json'.
        :type file_path: str
        """
        self.file_path = file_path
        self.data = self._load_data()

    def initialize_dependencies(self):
        """
        Public method to check and install dependencies.
        This should be called after the language setup is complete.
        """
        self._check_and_install_dependencies()

    def _check_and_install_dependencies(self):
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
            print(_("Warning: Could not read {file}. Error: {error}").format(file=requirements_path, error=e))
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
                print(_("Some required packages are missing. Attempting to install them..."))
                try:
                    for package in missing_packages:
                        print(_("Installing {package}...").format(package=package))
                        subprocess.check_call([sys.executable, "-m", "pip", "install", package])
                    
                    print(_("\nDependencies installed successfully."))
                    print(_("Please restart the application for the changes to take effect."))
                    sys.exit(0)
                except subprocess.CalledProcessError:
                    print(_("\nError: Failed to install dependencies. Please install them manually using: pip install -r {requirements_path}").format(requirements_path=requirements_path))
                    sys.exit(1)
        except Exception as e:
            print(_("An unexpected error occurred during dependency check: {error}").format(error=e))

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
                print(_("Info: Report content has been copied to the clipboard."))
            except pyperclip.PyperclipException as e:
                print(_("Warning: Could not copy to clipboard. Error: {error}").format(error=e))
        else:
            print(_("Warning: Could not copy to clipboard. Please install 'pyperclip' (`pip install pyperclip`)."))

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
        return _("{hours} hours ({dlp} DLP)").format(hours=hours_str, dlp=dlp_str)

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
            return False, _("Source main project '{name}' not found.").format(name=old_main_project_name)
        if not dest_project:
            return False, _("Destination main project '{name}' not found.").format(name=new_main_project_name)

        # Check if sub-project with same name exists in destination
        if any(sp["sub_project_name"] == sub_project_name for sp in dest_project["sub_projects"]):
            return False, _("A sub-project named '{sub_name}' already exists in '{main_name}'.").format(sub_name=sub_project_name, main_name=new_main_project_name)

        # Find and remove sub-project from source
        for i, sp in enumerate(source_project["sub_projects"]):
            if sp["sub_project_name"] == sub_project_name:
                sub_project_to_move = source_project["sub_projects"].pop(i)
                break

        if sub_project_to_move:
            dest_project["sub_projects"].append(sub_project_to_move)
            self._save_data()
            return True, _("Sub-project '{sub_name}' moved successfully.").format(sub_name=sub_project_name)
        return False, _("Sub-project '{sub_name}' not found in '{main_name}'.").format(sub_name=sub_project_name, main_name=old_main_project_name)

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
            return False, _("A main project named '{name}' already exists.").format(name=sub_project_name_to_promote)

        # Find the source project
        source_project = None
        for p in self.data["projects"]:
            if p["main_project_name"] == main_project_name:
                source_project = p
                break

        if not source_project:
            return False, _("Source main project '{name}' not found.").format(name=main_project_name)

        # Find the index of the sub-project to promote
        sub_project_index = None
        for i, sp in enumerate(source_project["sub_projects"]):
            if sp["sub_project_name"] == sub_project_name_to_promote:
                sub_project_index = i
                break

        if sub_project_index is None:
            return False, _("Sub-project '{sub_name}' not found in '{main_name}'.").format(sub_name=sub_project_name_to_promote, main_name=main_project_name)

        # Remove sub-project from old main project and get its data
        sub_project_data = source_project["sub_projects"].pop(sub_project_index)
        time_entries = sub_project_data.get("time_entries", [])

        # Create the new main project
        new_main_project = {
            "main_project_name": sub_project_name_to_promote,
            "sub_projects": [{"sub_project_name": _("General"), "time_entries": time_entries}]
        }
        self.data["projects"].append(new_main_project)
        self._save_data()
        return True, _("Sub-project '{sub_name}' was promoted to a new main project.").format(sub_name=sub_project_name_to_promote)

    def demote_main_project(self, main_project_to_demote_name, new_parent_main_project_name):
        """
        Demotes a main project to a sub-project of another main project.

        All time entries from all sub-projects of the demoted main project are
        consolidated into the new sub-project.

        :param main_project_to_demote_name: The name of the main project to demote.
        :type main_project_to_demote_name: str
        :param new_parent_main_project_name: The name of the main project that will become the parent.
        :type new_parent_main_project_name: str
        :return: A tuple (bool, str) indicating success and a message.
        :rtype: tuple(bool, str)
        """
        # 1. Find projects and handle errors
        project_to_demote = None
        project_to_demote_index = -1
        new_parent_project = None

        for i, p in enumerate(self.data["projects"]):
            if p["main_project_name"] == main_project_to_demote_name:
                project_to_demote = p
                project_to_demote_index = i
            if p["main_project_name"] == new_parent_main_project_name:
                new_parent_project = p

        if not project_to_demote:
            return False, _("Main project to demote '{name}' not found.").format(name=main_project_to_demote_name)
        if not new_parent_project:
            return False, _("New parent main project '{name}' not found.").format(name=new_parent_main_project_name)

        # Check for name conflict in the destination
        if any(sp["sub_project_name"] == main_project_to_demote_name for sp in new_parent_project["sub_projects"]):
            return False, _("A sub-project named '{sub_name}' already exists in '{main_name}'.").format(sub_name=main_project_to_demote_name, main_name=new_parent_main_project_name)

        # 2. Consolidate all time entries
        all_time_entries = []
        
        # Iterate through all sub-projects of the project to be demoted
        if "sub_projects" in project_to_demote:
            for sub_project in project_to_demote["sub_projects"]:
                # Extend the list with the time entries of each sub-project
                if "time_entries" in sub_project:
                    all_time_entries.extend(sub_project["time_entries"])

        # Sort entries by start time to maintain chronological order
        all_time_entries.sort(key=lambda x: x['start_time'])

        # 3. Create the new sub-project
        new_sub_project = {
            "sub_project_name": main_project_to_demote_name,
            "time_entries": all_time_entries
        }
        new_parent_project["sub_projects"].append(new_sub_project)

        # 4. Remove the old main project and save
        self.data["projects"].pop(project_to_demote_index)
        self._save_data()
        return True, _("Main project '{demoted_name}' was demoted to a sub-project under '{parent_name}'.").format(demoted_name=main_project_to_demote_name, parent_name=new_parent_main_project_name)

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
                    sub_project_details.append(_("- {name}: {hours} hours").format(name=sub_project['sub_project_name'], hours=hours_str))
                    main_project_total_time += sub_project_total_time

            # Add main project and its sub-projects to report if it has entries for the specified date
            if main_project_total_time.total_seconds() > 0:
                total_daily_time += main_project_total_time
                hours = main_project_total_time.total_seconds() / 3600
                hours_str = f"{hours:.3f}".replace('.', ',')
                report.append(_("## {name} ({hours} hours)\n").format(name=project['main_project_name'], hours=hours_str))
                report.extend(sub_project_details)
                report.append("\n")
        
        # Add total daily time to the report
        if total_daily_time.total_seconds() > 0:
            total_hours = total_daily_time.total_seconds() / 3600
            total_hours_str = f"{total_hours:.3f}".replace('.', ',')
            
            report.insert(0, _("# Daily Time Report: {date}\n").format(date=today.strftime('%Y-%m-%d')))
            report.append(_("\n**Total Daily Time: {hours} hours**").format(hours=total_hours_str))
            report.append("\nGenerated by TimeControl")
            report.append("https://github.com/frankfaulstich/TimeControl")
        else:
            report.append(_("No time tracked for {date}.").format(date=today.strftime('%Y-%m-%d')))
        
        report_text = "\n".join(report)
        self._copy_to_clipboard(report_text)
        return report_text

    def generate_sub_project_report(self, main_project_name, sub_project_name):
        """
        Generates a detailed report for a single sub-project.

        :param main_project_name: The name of the main project.
        :type main_project_name: str
        :param sub_project_name: The name of the sub-project.
        :type sub_project_name: str
        :return: The formatted report as a Markdown string, or an error message.
        :rtype: str
        """
        project = None
        for p in self.data["projects"]:
            if p["main_project_name"] == main_project_name:
                project = p
                break
        if not project:
            return _("Main project '{name}' not found.").format(name=main_project_name)

        sub_project = None
        for sp in project["sub_projects"]:
            if sp["sub_project_name"] == sub_project_name:
                sub_project = sp
                break
        if not sub_project:
            return _("Sub-project '{sub_name}' not found in '{main_name}'.").format(sub_name=sub_project_name, main_name=main_project_name)

        entries = sub_project.get("time_entries", [])
        if not entries:
            return _("No time entries found for sub-project '{sub_name}'.").format(sub_name=sub_project_name)

        total_duration = timedelta()
        first_start_time = None
        last_activity_time = None
        is_active = False
        daily_breakdown = {}
        weekday_durations = [timedelta() for _ in range(7)] # Mo-So

        for i, entry in enumerate(entries):
            start_time = datetime.fromisoformat(entry["start_time"])
            if first_start_time is None:
                first_start_time = start_time
            
            end_time = None
            duration = timedelta() # Initialize duration here

            if "end_time" in entry:
                end_time = datetime.fromisoformat(entry["end_time"])
                duration = end_time - start_time
                total_duration += duration
                last_activity_time = end_time
                weekday_durations[start_time.weekday()] += duration
            elif i == len(entries) - 1: # Last entry is open
                is_active = True
                duration = datetime.now() - start_time
                weekday_durations[start_time.weekday()] += duration
 
            date_key = start_time.date()
            if date_key not in daily_breakdown:
                daily_breakdown[date_key] = []
            
            duration_str = str(duration).split('.')[0] # Format as H:MM:SS
            time_range_str = f"{start_time.strftime('%H:%M:%S')} - {end_time.strftime('%H:%M:%S') if end_time else _('now')}"
            daily_breakdown[date_key].append(f"  - {time_range_str} ({_('Duration')}: {duration_str})")

        # Build the report string
        report = []
        report.append(_("# Detailed Report for Sub-Project: {name}").format(name=sub_project_name))
        report.append(_("Part of Main Project: {name}").format(name=main_project_name))
        report.append("-" * 30)

        status = _("Active (currently running)") if is_active else _("Inactive")
        report.append(f"**{_('Status')}:** {status}")
        if first_start_time:
            report.append(f"**{_('First entry')}:** {first_start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        if last_activity_time:
            report.append(f"**{_('Last activity')}:** {last_activity_time.strftime('%Y-%m-%d %H:%M:%S')}")
        
        report.append(f"**{_('Total recorded time')}:** {str(total_duration).split('.')[0]}")
        report.append(f"**{_('Total work sessions')}:** {len(entries)}")

        if len(entries) > 0:
            avg_duration = total_duration / len(entries)
            report.append(f"**{_('Average session duration')}:** {str(avg_duration).split('.')[0]}")

        if total_duration.total_seconds() > 0:
            report.append(f"\n## {_('Weekday Distribution')}")
            total_seconds = total_duration.total_seconds()
            weekdays = [_('Monday'), _('Tuesday'), _('Wednesday'), _('Thursday'), _('Friday'), _('Saturday'), _('Sunday')]
            for i, day_name in enumerate(weekdays):
                day_duration = weekday_durations[i]
                if day_duration.total_seconds() > 0:
                    percentage = (day_duration.total_seconds() / total_seconds) * 100
                    duration_str = str(day_duration).split('.')[0]
                    report.append(f"- **{day_name}**: {duration_str} ({percentage:.1f}%)")

        report.append(f"\n## {_('Daily Breakdown')}")
        
        sorted_dates = sorted(daily_breakdown.keys())
        for date in sorted_dates:
            report.append(f"\n### {date.strftime('%Y-%m-%d')}")
            report.extend(daily_breakdown[date])

        report_text = "\n".join(report)
        self._copy_to_clipboard(report_text)
        return report_text

    def generate_main_project_report(self, main_project_name):
        """
        Generates a detailed report for a single main project.

        :param main_project_name: The name of the main project.
        :type main_project_name: str
        :return: The formatted report as a Markdown string, or an error message.
        :rtype: str
        """
        project = next((p for p in self.data["projects"] if p["main_project_name"] == main_project_name), None)
        if not project:
            return _("Main project '{name}' not found.").format(name=main_project_name)

        sub_projects = project.get("sub_projects", [])

        # --- Overall Stats ---
        total_duration = timedelta()
        total_sessions = 0
        first_start_time = None
        last_activity_time = None
        is_active = False
        active_sub_project_name = None

        # --- Sub-Project Specific Stats ---
        sub_project_stats = []
        weekday_durations = [timedelta() for _ in range(7)] # Mo-So

        for sp in sub_projects:
            sp_duration = timedelta()
            entries = sp.get("time_entries", [])
            total_sessions += len(entries)

            for i, entry in enumerate(entries):
                start_time = datetime.fromisoformat(entry["start_time"])
                if first_start_time is None or start_time < first_start_time:
                    first_start_time = start_time
                if last_activity_time is None or start_time > last_activity_time:
                    last_activity_time = start_time

                if "end_time" in entry:
                    end_time = datetime.fromisoformat(entry["end_time"])
                    duration = end_time - start_time
                    sp_duration += duration
                    weekday_durations[start_time.weekday()] += duration
                    if last_activity_time is None or end_time > last_activity_time:
                        last_activity_time = end_time
                elif i == len(entries) - 1:  # Last entry is open
                    is_active = True
                    active_sub_project_name = sp["sub_project_name"]
            
            total_duration += sp_duration
            if len(entries) > 0:
                sub_project_stats.append({
                    "name": sp["sub_project_name"],
                    "duration": sp_duration,
                    "sessions": len(entries)
                })

        # --- Build Report ---
        report = []
        report.append(_("# Detailed Report for Main Project: {name}").format(name=main_project_name))
        report.append("-" * 30)

        status = _("Active (working on '{sub_name}')").format(sub_name=active_sub_project_name) if is_active else _("Inactive")
        report.append(f"**{_('Status')}:** {status}")
        if first_start_time:
            report.append(f"**{_('First entry')}:** {first_start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        if last_activity_time:
            report.append(f"**{_('Last activity')}:** {last_activity_time.strftime('%Y-%m-%d %H:%M:%S')}")
        
        report.append(f"**{_('Total recorded time')}:** {str(total_duration).split('.')[0]}")
        report.append(f"**{_('Number of sub-projects')}:** {len(sub_projects)}")
        report.append(f"**{_('Total work sessions')}:** {total_sessions}")

        if total_sessions > 0:
            avg_duration = total_duration / total_sessions
            report.append(f"**{_('Average session duration')}:** {str(avg_duration).split('.')[0]}")

        if total_duration.total_seconds() > 0:
            report.append(f"\n## {_('Weekday Distribution')}")
            total_seconds = total_duration.total_seconds()
            weekdays = [_('Monday'), _('Tuesday'), _('Wednesday'), _('Thursday'), _('Friday'), _('Saturday'), _('Sunday')]
            for i, day_name in enumerate(weekdays):
                day_duration = weekday_durations[i]
                if day_duration.total_seconds() > 0:
                    percentage = (day_duration.total_seconds() / total_seconds) * 100
                    duration_str = str(day_duration).split('.')[0]
                    report.append(f"- **{day_name}**: {duration_str} ({percentage:.1f}%)")

        if sub_project_stats:
            report.append(f"\n## {_('Sub-Project Breakdown')}")
            # Sort by duration, descending
            sub_project_stats.sort(key=lambda x: x["duration"], reverse=True)
            
            total_seconds = total_duration.total_seconds()
            for stat in sub_project_stats:
                percentage = (stat["duration"].total_seconds() / total_seconds * 100) if total_seconds > 0 else 0
                duration_str = str(stat['duration']).split('.')[0]
                report.append(
                    f"- **{stat['name']}**: {duration_str} ({_('{num_sessions} sessions').format(num_sessions=stat['sessions'])}, {percentage:.1f}%)"
                )

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
                    sub_project_details.append(f"- {sub_project['sub_project_name']}: {formatted_time}") # _format_duration is already translated
                    main_project_total_time += sub_project_total_time

            if main_project_total_time.total_seconds() > 0:
                total_period_time += main_project_total_time
                formatted_time = self._format_duration(main_project_total_time)
                report.append(f"## {project['main_project_name']} ({formatted_time})\n") # _format_duration is already translated
                report.extend(sub_project_details)
                report.append("\n")
        
        if total_period_time.total_seconds() > 0:
            total_hours_str = self._format_duration(total_period_time)
            
            report.insert(0, _("# Time Report: {start_date} to {end_date}\n").format(start_date=start_date.strftime('%Y-%m-%d'), end_date=end_date.strftime('%Y-%m-%d')))
            report.append(_("\n**Total Time in Period: {total_time}**").format(total_time=total_hours_str))
            report.append("\nGenerated by TimeControl")
            report.append("https://github.com/frankfaulstich/TimeControl")
        else:
            report.append(_("No time tracked between {start_date} and {end_date}.").format(start_date=start_date.strftime('%Y-%m-%d'), end_date=end_date.strftime('%Y-%m-%d')))
        
        report_text = "\n".join(report)
        self._copy_to_clipboard(report_text)
        return report_text