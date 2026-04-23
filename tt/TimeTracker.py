import json
import os
from i18n import _
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timedelta, date
import calendar

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
    VERSION = "3.4.2"
    STATUS_OPEN = "open"
    STATUS_CLOSED = "closed"
    STATUS_DONE = "done"

    def __init__(self, file_path=None):
        """
        Initializes the TimeTracker, checks for dependencies, and loads data from the JSON file.

        :param file_path: The path to the JSON file where data is stored.
                          If None, the path is read from config.json (key 'data_file').
                          Defaults to 'data.json'.
        :type file_path: str
        """
        if file_path is None:
            file_path = 'data.json'
            if os.path.exists('config.json'):
                try:
                    with open('config.json', 'r', encoding='utf-8') as f:
                        config = json.load(f)
                        file_path = config.get('data_file', 'data.json')
                except (IOError, json.JSONDecodeError):
                    pass

        self.file_path = file_path
        self.data = self._load_data()
        if self._migrate_data_structure():
            self._save_data()

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
            installed_packages = {dist.metadata['Name'].lower() for dist in installed_packages_dist if dist.metadata and dist.metadata['Name']}
            
            missing_packages = []
            for req in requirements: # e.g., "requests==2.28.1"
                # A simple check for the package name, ignoring version specifiers for now
                req_name = req.split('==')[0].split('>=')[0].split('<=')[0].split('<')[0].split('>')[0].strip()
                if req_name.lower() not in installed_packages:
                    missing_packages.append(req)

            if missing_packages:
                print(_("Some required packages are missing. Attempting to install them..."))
                failed_packages = []
                for package in missing_packages:
                    print(_("Installing {package}...").format(package=package))
                    try:
                        subprocess.check_call([sys.executable, "-m", "pip", "install", package])
                    except subprocess.CalledProcessError:
                        print(_("Failed to install {package}. Continuing without it.").format(package=package))
                        failed_packages.append(package)

                if not failed_packages:
                    print(_("\nDependencies installed successfully."))
                    print(_("Please restart the application for the changes to take effect."))
                    sys.exit(0)
                else:
                    print(_("\nWarning: Some dependencies could not be installed: {packages}").format(packages=", ".join(failed_packages)))
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

    def _migrate_data_structure(self):
        """
        Ensures that the data structure is up to date.
        - Adds 'status': 'open' to sub-projects if missing.
        
        :return: True if data was changed, otherwise False.
        :rtype: bool
        """
        data_changed = False
        # Ensure self.data and "projects" exist
        if "projects" not in self.data:
            self.data["projects"] = []
            data_changed = True # The data object itself was changed

        for project in self.data.get("projects", []):
            # Migration: Add status to main projects
            if "status" not in project:
                project["status"] = self.STATUS_OPEN
                data_changed = True

            # Migration: Rename sub_projects to tasks
            if "sub_projects" in project:
                project["tasks"] = project.pop("sub_projects")
                data_changed = True

            for task in project.get("tasks", []):
                if "status" not in task:
                    task["status"] = self.STATUS_OPEN
                    data_changed = True
                if "due_date" not in task:
                    task["due_date"] = None
                    data_changed = True
                if "today" not in task:
                    task["today"] = False
                    data_changed = True
                if "note" not in task:
                    task["note"] = ""
                    data_changed = True
                if "recurring" not in task:
                    task["recurring"] = False
                    data_changed = True
                if "frequency" not in task:
                    task["frequency"] = "daily"
                    data_changed = True
                if "userdefined_days" not in task:
                    task["userdefined_days"] = 1
                    data_changed = True
        return data_changed

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
        # Use Decimal for precise calculations and rounding
        hours_decimal = Decimal(str(duration_td.total_seconds())) / Decimal('3600')
        dlp_decimal = hours_decimal / Decimal('40')
        
        # Quantize to 3 decimal places using standard rounding (away from zero)
        quantizer = Decimal('0.001')
        hours_str = str(hours_decimal.quantize(quantizer, rounding=ROUND_HALF_UP)).replace('.', ',')
        dlp_str = str(dlp_decimal.quantize(quantizer, rounding=ROUND_HALF_UP)).replace('.', ',')
        return _("{hours} hours ({dlp} DLP)").format(hours=hours_str, dlp=dlp_str)

    def get_version(self):
        """
        Returns the current version of the TimeTracker application.

        :return: The application version string.
        :rtype: str
        """
        return self.VERSION

    def _get_project(self, main_project_name):
        """
        Helper method to find a main project by name.
        
        :param main_project_name: The name of the main project.
        :return: The project dictionary or None if not found.
        """
        for project in self.data["projects"]:
            if project["main_project_name"] == main_project_name:
                return project
        return None

    def _get_task(self, main_project_name, task_name):
        """
        Helper method to find a task by name within a main project.
        
        :param main_project_name: The name of the main project.
        :param task_name: The name of the task.
        :return: The task dictionary or None if not found.
        """
        project = self._get_project(main_project_name)
        if project:
            for task in project["tasks"]:
                if task["task_name"] == task_name:
                    return task
        return None

    def add_main_project(self, main_project_name):
        """
        Adds a new main project.

        :param main_project_name: The name of the main project to add.
        :type main_project_name: str
        """
        new_project = {
            "main_project_name": main_project_name,
            "tasks": [],
            "status": self.STATUS_OPEN
        }
        self.data["projects"].append(new_project)
        self._save_data()

    def list_main_projects(self, status_filter='all'):
        """
        Returns a list of main projects based on the status filter.

        :param status_filter: 'open', 'closed', or 'all'. Defaults to 'all'.
        :return: A list of dictionaries containing 'main_project_name' and 'status'.
        :rtype: list[dict]
        """
        projects = []
        for project in self.data["projects"]:
            status = project.get("status", self.STATUS_OPEN)
            if status_filter == 'all' or status == status_filter:
                projects.append({
                    "main_project_name": project["main_project_name"],
                    "status": status
                })
        return projects

    def delete_main_project(self, main_project_name):
        """
        Deletes a main project along with all associated tasks and time entries.

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

        project = self._get_project(old_name)
        if project:
            project["main_project_name"] = new_name
            self._save_data()
            return True
        return False

    def close_main_project(self, main_project_name):
        """
        Sets the status of a main project to 'closed'.

        :param main_project_name: The name of the main project.
        :type main_project_name: str
        :return: True if the main project was closed, otherwise False.
        :rtype: bool
        """
        project = self._get_project(main_project_name)
        if project:
            project["status"] = self.STATUS_CLOSED
            self._save_data()
            return True
        return False

    def reopen_main_project(self, main_project_name):
        """
        Sets the status of a main project to 'open'.

        :param main_project_name: The name of the main project.
        :type main_project_name: str
        :return: True if the main project was reopened, otherwise False.
        :rtype: bool
        """
        project = self._get_project(main_project_name)
        if project:
            project["status"] = self.STATUS_OPEN
            self._save_data()
            return True
        return False

    def add_task(self, main_project_name, task_name, due_date=None, today=False, note="", recurring=False, frequency="daily", userdefined_days=1):
        """
        Adds a new task to a specified main project.

        :param main_project_name: The name of the main project to add the task to.
        :type main_project_name: str
        :param task_name: The name of the task to add.
        :type task_name: str
        :param due_date: Optional due date for the task (ISO string YYYY-MM-DD).
        :type due_date: str or None
        :param today: Whether the task is for today.
        :type today: bool
        :param note: Notes for the task (Markdown format).
        :type note: str
        :param recurring: Whether the task is recurring.
        :param frequency: Freq (daily, on all business days, weekly, monthly, userdefined).
        :param userdefined_days: Number of days for userdefined frequency.
        :return: True if the task was added successfully, otherwise False (if main project not found).
        :rtype: bool
        """
        project = self._get_project(main_project_name)
        if project:
            new_task = {
                "task_name": task_name,
                "time_entries": [],
                "status": self.STATUS_OPEN,
                "due_date": due_date,
                "today": today,
                "note": note,
                "recurring": recurring,
                "frequency": frequency,
                "userdefined_days": userdefined_days
            }
            project["tasks"].append(new_task)
            self._save_data()
            return True
        return False
    
    def list_tasks(self, main_project_name=None, status_filter='all', planning_filter=None):
        """
        Lists tasks based on specified filters.

        This method serves as a unified way to retrieve tasks.

        :param main_project_name: Optional. The name of the main project to search in.
                                  If None, all main projects are considered.
        :type main_project_name: str or None
        :param status_filter: Optional. Filter by status. Can be 'open', 'closed', or 'all'.
                              Defaults to 'all'.
        :type status_filter: str
        :return: A list of dictionaries, where each dictionary contains details
                 of a task, including 'main_project_name', 'task_name',
                 and 'status'.
        :rtype: list[dict]
        """
        results = []
        projects_to_search = self.data["projects"]

        # If a specific main project is given, filter the list of projects to search
        if main_project_name:
            projects_to_search = [p for p in projects_to_search if p.get("main_project_name") == main_project_name]

        today_dt = date.today()
        today_str = today_dt.isoformat()
        tomorrow_str = (today_dt + timedelta(days=1)).isoformat()
        next_week_str = (today_dt + timedelta(days=7)).isoformat()

        for project in projects_to_search:
            for task in project.get("tasks", []):
                status = task.get("status", self.STATUS_OPEN)
                
                # Default status filter logic
                if not (status_filter == 'all' or status == status_filter or (status_filter == self.STATUS_OPEN and status == self.STATUS_DONE)):
                    continue

                # Planning filter logic
                if planning_filter:
                    # In planning views, we usually exclude 'done' and 'closed' tasks
                    if status in [self.STATUS_DONE, self.STATUS_CLOSED]:
                        continue
                        
                    due_date = task.get("due_date")
                    is_today = task.get("today", False)
                    
                    if planning_filter == 'today':
                        if not (is_today or due_date == today_str):
                            continue
                    elif planning_filter == 'tomorrow':
                        if due_date != tomorrow_str:
                            continue
                    elif planning_filter == 'weekly':
                        if not (due_date and today_str <= due_date <= next_week_str):
                            continue
                    elif planning_filter == 'overdue':
                        if not (due_date and due_date < today_str):
                            continue
                    elif planning_filter == 'unplanned':
                        if due_date or is_today:
                            continue

                results.append({
                    "main_project_name": project["main_project_name"],
                    "task_name": task["task_name"],
                    "status": status,
                    "due_date": task.get("due_date"),
                    "today": task.get("today", False),
                    "note": task.get("note", ""),
                    "recurring": task.get("recurring", False),
                    "frequency": task.get("frequency", "daily"),
                    "userdefined_days": task.get("userdefined_days", 1)
                })
        return results

    def cleanup_overdue_today_tasks(self):
        """
        Removes the 'today' flag (⭐) from tasks that have a due date in the past.
        
        :return: True if any task was updated and saved.
        :rtype: bool
        """
        today_str = date.today().isoformat()
        changed = False
        for project in self.data.get("projects", []):
            for task in project.get("tasks", []):
                if task.get('today') and task.get('due_date') and task.get('due_date') < today_str:
                    task['today'] = False
                    changed = True
        if changed:
            self._save_data()
        return changed

    def delete_task(self, main_project_name, task_name):
        """
        Deletes a task from a main project.

        :param main_project_name: The name of the main project.
        :type main_project_name: str
        :param task_name: The name of the task to delete.
        :type task_name: str
        :return: True if the task was deleted, otherwise False.
        :rtype: bool
        """
        project = self._get_project(main_project_name)
        if project:
            initial_count = len(project["tasks"])
            project["tasks"] = [
                t for t in project["tasks"] if t["task_name"] != task_name
            ]
            if len(project["tasks"]) < initial_count:
                self._save_data()
                return True
        return False

    def delete_all_closed_tasks(self):
        """
        Permanently deletes all tasks that have the status 'closed'.

        :return: The number of deleted tasks.
        :rtype: int
        """
        deleted_count = 0
        for project in self.data["projects"]:
            tasks = project.get("tasks", [])
            for i in range(len(tasks) - 1, -1, -1):
                if tasks[i].get("status") == self.STATUS_CLOSED:
                    del tasks[i]
                    deleted_count += 1

        if deleted_count > 0:
            self._save_data()
        
        return deleted_count

    def close_task(self, main_project_name, task_name):
        """
        Sets the status of a task to 'closed'.

        :param main_project_name: The name of the main project.
        :type main_project_name: str
        :param task_name: The name of the task to close.
        :type task_name: str
        :return: True if the task was closed, otherwise False.
        :rtype: bool
        """
        task = self._get_task(main_project_name, task_name)
        if task:
            task["status"] = self.STATUS_CLOSED
            self._save_data()
            return True
        return False

    def reopen_task(self, main_project_name, task_name):
        """
        Sets the status of a task to 'open'.

        :param main_project_name: The name of the main project.
        :type main_project_name: str
        :param task_name: The name of the task to reopen.
        :type task_name: str
        :return: True if the task was reopened, otherwise False.
        :rtype: bool
        """
        task = self._get_task(main_project_name, task_name)
        if task:
            task["status"] = self.STATUS_OPEN
            self._save_data()
            return True
        return False

    def rename_task(self, main_project_name, old_task_name, new_task_name):
        """
        Renames a task within a given main project.

        :param main_project_name: The name of the main project containing the task.
        :type main_project_name: str
        :param old_task_name: The current name of the task to rename.
        :type old_task_name: str
        :param new_task_name: The new name for the task.
        :type new_task_name: str
        :return: True if renaming was successful, False otherwise (e.g., project not found,
                 or new name already exists).
        :rtype: bool
        """
        project = self._get_project(main_project_name)
        if project:
            # Check if the new name already exists
            if any(t["task_name"] == new_task_name for t in project["tasks"]):
                return False # New name is already in use

            # Find the task to rename
            for task in project["tasks"]:
                if task["task_name"] == old_task_name:
                    task["task_name"] = new_task_name
                    self._save_data()
                    return True
        return False

    def update_task(self, main_project_name, old_task_name, new_task_name=None, due_date=None, today=None, note=None, status=None, recurring=None, frequency=None, userdefined_days=None):
        """
        Updates a task's properties.

        :param main_project_name: Name of the main project.
        :param old_task_name: Current name of the task.
        :param new_task_name: New name (optional).
        :param due_date: New due date (optional, ISO string or None).
        :param today: New today status (optional, bool).
        :param note: New note (optional, str).
        :param status: New status (optional, str).
        :param recurring: Recurring status (optional, bool).
        :param frequency: Frequency (optional, str).
        :param userdefined_days: Days for userdefined frequency (optional, int).
        :return: True if successful.
        """
        project = self._get_project(main_project_name)
        if project:
            if new_task_name and new_task_name != old_task_name:
                if any(t["task_name"] == new_task_name for t in project["tasks"]):
                    return False

            task = self._get_task(main_project_name, old_task_name)
            if task:
                # Handle recurring task generation
                is_completing = (status == self.STATUS_DONE and task.get("status") != self.STATUS_DONE)
                is_recurring = recurring if recurring is not None else task.get("recurring", False)
                
                if is_completing and is_recurring:
                    self._create_next_recurring_instance(project, task, due_date, recurring, frequency, userdefined_days)

                if new_task_name:
                    task["task_name"] = new_task_name
                
                # Update due_date (always update to what's provided)
                task["due_date"] = due_date
                
                # Update today status if provided
                if today is not None:
                    task["today"] = today
                
                # Update note if provided
                if note is not None:
                    task["note"] = note
                
                # Update status if provided
                if status is not None:
                    task["status"] = status
                
                if recurring is not None:
                    task["recurring"] = recurring
                if frequency is not None:
                    task["frequency"] = frequency
                if userdefined_days is not None:
                    task["userdefined_days"] = userdefined_days
                
                self._save_data()
                return True
        return False

    def _create_next_recurring_instance(self, project, task, due_date_param, recurring_param, freq_param, ud_days_param):
        freq = freq_param if freq_param is not None else task.get("frequency", "daily")
        ud_days = ud_days_param if ud_days_param is not None else task.get("userdefined_days", 1)
        base_due = due_date_param if due_date_param is not None else task.get("due_date")
        
        next_due = self._calculate_next_due_date(base_due, freq, ud_days)
        
        new_task = {
            "task_name": task["task_name"],
            "time_entries": [], # Start with a fresh, empty list for the new instance
            "status": self.STATUS_OPEN,
            "due_date": next_due,
            "today": task.get("today", False),
            "note": task.get("note", ""),
            "recurring": True,
            "frequency": freq,
            "userdefined_days": ud_days
        }
        project["tasks"].append(new_task)

    def _calculate_next_due_date(self, base_due_str, frequency, ud_days):
        if base_due_str:
            try:
                base_date = datetime.fromisoformat(base_due_str).date()
            except ValueError:
                base_date = date.today()
        else:
            base_date = date.today()
            
        if frequency == "daily":
            next_date = base_date + timedelta(days=1)
        elif frequency == "on all business days":
            next_date = base_date + timedelta(days=1)
            while next_date.weekday() >= 5: # 5=Sat, 6=Sun
                next_date += timedelta(days=1)
        elif frequency == "weekly":
            next_date = base_date + timedelta(weeks=1)
        elif frequency == "monthly":
            month = base_date.month % 12 + 1
            year = base_date.year + (base_date.month // 12)
            last_day = calendar.monthrange(year, month)[1]
            next_date = date(year, month, min(base_date.day, last_day))
        elif frequency == "userdefined":
            next_date = base_date + timedelta(days=ud_days)
        else:
            next_date = base_date + timedelta(days=1)
            
        return next_date.isoformat()

    def move_task(self, old_main_project_name, task_name, new_main_project_name):
        """
        Moves a task from one main project to another.

        :param old_main_project_name: The name of the source main project.
        :type old_main_project_name: str
        :param task_name: The name of the task to move.
        :type task_name: str
        :param new_main_project_name: The name of the destination main project.
        :type new_main_project_name: str
        :return: A tuple (bool, str) indicating success and a message.
        :rtype: tuple(bool, str)
        """
        source_project = self._get_project(old_main_project_name)
        dest_project = self._get_project(new_main_project_name)

        if not source_project:
            return False, _("Source main project '{name}' not found.").format(name=old_main_project_name)
        if not dest_project:
            return False, _("Destination main project '{name}' not found.").format(name=new_main_project_name)

        # Check if task with same name exists in destination
        if any(t["task_name"] == task_name for t in dest_project["tasks"]):
            return False, _("A task named '{task_name}' already exists in '{main_name}'.").format(task_name=task_name, main_name=new_main_project_name)

        # Find and remove task from source
        task_to_move = None
        for i, t in enumerate(source_project["tasks"]):
            if t["task_name"] == task_name:
                task_to_move = source_project["tasks"].pop(i)
                break

        if task_to_move:
            dest_project["tasks"].append(task_to_move)
            self._save_data()
            return True, _("Task '{task_name}' moved successfully.").format(task_name=task_name)
        return False, _("Task '{task_name}' not found in '{main_name}'.").format(task_name=task_name, main_name=old_main_project_name)

    def promote_task_to_project(self, main_project_name, task_name_to_promote):
        """
        Promotes a task to a new main project.

        The time entries of the sub-project are preserved and moved to a new sub-project
        named 'General' within the new main project.

        :param main_project_name: The name of the current main project.
        :type main_project_name: str
        :param sub_project_name_to_promote: The name of the sub-project to promote.
        :type sub_project_name_to_promote: str
        :return: A tuple (bool, str) indicating success and a message.
        :rtype: tuple(bool, str)
        """
        # Check if a main project with the task's name already exists
        if any(p["main_project_name"] == task_name_to_promote for p in self.data["projects"]):
            return False, _("A main project named '{name}' already exists.").format(name=task_name_to_promote)

        # Find the source project
        source_project = self._get_project(main_project_name)

        if not source_project:
            return False, _("Source main project '{name}' not found.").format(name=main_project_name)

        # Find the index of the task to promote
        task_index = None
        for i, t in enumerate(source_project["tasks"]):
            if t["task_name"] == task_name_to_promote:
                task_index = i
                break

        if task_index is None:
            return False, _("Task '{task_name}' not found in '{main_name}'.").format(task_name=task_name_to_promote, main_name=main_project_name)

        # Remove task from old main project and get its data
        task_data = source_project["tasks"].pop(task_index)
        time_entries = task_data.get("time_entries", [])

        # Create the new main project
        new_main_project = {
            "main_project_name": task_name_to_promote,
            "tasks": [{"task_name": _("General"), "time_entries": time_entries}]
        }
        self.data["projects"].append(new_main_project)
        self._save_data()
        return True, _("Task '{task_name}' was promoted to a new main project.").format(task_name=task_name_to_promote)

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
        new_parent_project = self._get_project(new_parent_main_project_name)

        for i, p in enumerate(self.data["projects"]):
            if p["main_project_name"] == main_project_to_demote_name:
                project_to_demote = p
                project_to_demote_index = i

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
        The affected sub-project and main project are moved to the top of their respective lists.

        :param main_project_name: The parent main project name.
        :type main_project_name: str
        :param sub_project_name: The sub-project for which work is being started.
        :type sub_project_name: str
        :return: True if work was started successfully, otherwise False.
        :rtype: bool
        """
        self.stop_work()
        
        main_project = None
        main_project_index = -1
        sub_project = None
        sub_project_index = -1

        # Find the main project and sub-project along with their indices
        for i, p in enumerate(self.data["projects"]):
            if p["main_project_name"] == main_project_name:
                main_project_index = i
                main_project = p
                for j, sp in enumerate(p["sub_projects"]):
                    if sp["sub_project_name"] == sub_project_name:
                        sub_project_index = j
                        sub_project = sp
                        break
                break

        if sub_project and main_project:
            # Add the new time entry
            new_entry = {
                "start_time": datetime.now().isoformat()
            }
            sub_project["time_entries"].append(new_entry)

            # Move the sub-project to the top of the list
            if sub_project_index > 0:
                moved_sub_project = main_project["sub_projects"].pop(sub_project_index)
                main_project["sub_projects"].insert(0, moved_sub_project)

            # Move the main project to the top of the list
            if main_project_index > 0:
                moved_main_project = self.data["projects"].pop(main_project_index)
                self.data["projects"].insert(0, moved_main_project)

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
            for task in reversed(project["tasks"]):
                if task["time_entries"] and "end_time" not in task["time_entries"][-1]:
                    return {
                        "main_project_name": project["main_project_name"],
                        "task_name": task["task_name"],
                        "start_time": task["time_entries"][-1]["start_time"]
                    }
        return None

    def list_inactive_tasks(self, inactive_weeks):
        """
        Lists sub-projects that have not had any activity (completed time entry) 
        within the specified number of weeks.
        Currently running sessions are ignored (i.e., not listed as inactive).
        Sub-projects with no time entries are also ignored.
        Only sub-projects with status 'open' are considered.

        :param inactive_weeks: The number of weeks defining the inactivity threshold.
        :type inactive_weeks: int
        :return: A list of dictionaries, each containing 'main_project', 'task_name', 
                 and the 'last_activity' timestamp (formatted).
        :rtype: list[dict]
        """
        cutoff_date = datetime.now() - timedelta(weeks=inactive_weeks)
        inactive_projects = []

        for project in self.data["projects"]:
            for task in project["tasks"]:
                if not task.get("time_entries"):
                    # Ignore sub-projects with no entries
                    continue
                
                # Check if the task is currently running (active)
                last_entry = task["time_entries"][-1]
                if "end_time" not in last_entry:
                    continue # Skip if currently running

                # Only consider sub-projects that are currently 'open'
                # This check is now after the 'running' check to correctly ignore running projects regardless of status.
                if task.get("status", self.STATUS_OPEN) != self.STATUS_OPEN:
                    continue

                latest_timestamp = None
                
                # Find the latest timestamp from all completed entries
                for entry in task["time_entries"]:
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
                        "task_name": task["task_name"],
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
            # Skip closed main projects or those where all sub-projects are closed
            if project.get("status", self.STATUS_OPEN) == self.STATUS_CLOSED:
                continue
            tasks = project.get("tasks", [])
            if tasks and all(t.get("status", self.STATUS_OPEN) == self.STATUS_CLOSED for t in tasks):
                continue

            latest_activity = None
            is_active = False

            for task in project["tasks"]:
                for entry in task.get("time_entries", []):
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

    def list_completed_main_projects(self):
        """
        Lists main projects that have either no sub-projects or only closed sub-projects.

        :return: A list of main project names.
        :rtype: list[str]
        """
        completed_projects = []
        for project in self.data["projects"]:
            tasks = project.get("tasks", [])
            
            # If no sub-projects, it is considered completed/inactive in this context
            if not tasks:
                completed_projects.append(project["main_project_name"])
                continue
            
            # Check if all sub-projects are closed
            if all(t.get("status", self.STATUS_OPEN) == self.STATUS_CLOSED for t in tasks):
                completed_projects.append(project["main_project_name"])
        
        return completed_projects

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
            task_details = []

            for task in project["tasks"]:
                task_total_time = timedelta()
                
                for entry in task["time_entries"]:
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
        project = self._get_project(main_project_name)
        if not project:
            return _("Main project '{name}' not found.").format(name=main_project_name)

        sub_project = self._get_sub_project(main_project_name, sub_project_name)
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
        weekday_durations = [timedelta() for _ in range(7)] # Mon-Sun

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
        report.append(_("# Detailed Report for Task: {name}").format(name=task_name))
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
        project = self._get_project(main_project_name)
        if not project:
            return _("Main project '{name}' not found.").format(name=main_project_name)

        tasks = project.get("tasks", [])

        # --- Overall Stats ---
        total_duration = timedelta()
        total_sessions = 0
        first_start_time = None
        last_activity_time = None
        is_active = False
        active_task_name = None

        # --- Task Specific Stats ---
        task_stats = []
        weekday_durations = [timedelta() for _ in range(7)] # Mon-Sun

        for t in tasks:
            t_duration = timedelta()
            entries = t.get("time_entries", [])
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
                    t_duration += duration
                    weekday_durations[start_time.weekday()] += duration
                    if last_activity_time is None or end_time > last_activity_time:
                        last_activity_time = end_time
                elif i == len(entries) - 1:  # Last entry is open
                    is_active = True
                    active_task_name = t["task_name"]
            
            total_duration += t_duration
            if len(entries) > 0:
                task_stats.append({
                    "name": t["task_name"],
                    "duration": t_duration,
                    "sessions": len(entries)
                })

        # --- Build Report ---
        report = []
        report.append(_("# Detailed Report for Main Project: {name}").format(name=main_project_name))
        report.append("-" * 30)

        status = _("Active (working on '{task_name}')").format(task_name=active_task_name) if is_active else _("Inactive")
        report.append(f"**{_('Status')}:** {status}")
        if first_start_time:
            report.append(f"**{_('First entry')}:** {first_start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        if last_activity_time:
            report.append(f"**{_('Last activity')}:** {last_activity_time.strftime('%Y-%m-%d %H:%M:%S')}")
        
        report.append(f"**{_('Total recorded time')}:** {str(total_duration).split('.')[0]}")
        report.append(f"**{_('Number of tasks')}:** {len(tasks)}")
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

        if task_stats:
            report.append(f"\n## {_('Task Breakdown')}")
            # Sort by duration, descending
            task_stats.sort(key=lambda x: x["duration"], reverse=True)
            
            total_seconds = total_duration.total_seconds()
            for stat in task_stats:
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
            task_details = []

            for task in project["tasks"]:
                task_total_time = timedelta()
                
                for entry in task["time_entries"]:
                    try:
                        start_time = datetime.fromisoformat(entry["start_time"])
                        if "end_time" in entry:
                            end_time = datetime.fromisoformat(entry["end_time"])
                            # Check if the entry is within the specified date range
                            if start_date <= start_time.date() <= end_date:
                                duration = end_time - start_time
                                task_total_time += duration
                    except (ValueError, KeyError):
                        continue

                if task_total_time.total_seconds() > 0:
                    formatted_time = self._format_duration(task_total_time)
                    task_details.append(f"- {task['task_name']}: {formatted_time}") # _format_duration is already translated
                    main_project_total_time += task_total_time

            if main_project_total_time.total_seconds() > 0:
                total_period_time += main_project_total_time
                formatted_time = self._format_duration(main_project_total_time)
                report.append(f"## {project['main_project_name']} ({formatted_time})\n") # _format_duration is already translated
                report.extend(task_details)
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

    def generate_detailed_daily_report(self, report_date=None):
        """
        Generates a detailed daily report listing specific time ranges for each task.

        :param report_date: Optional. The date for which the report should be generated.
        :type report_date: datetime.date or None
        :return: The formatted report as a Markdown string.
        :rtype: str
        """
        report = []
        today = report_date if report_date else datetime.now().date()
        
        report.append(_("# Detailed Daily Report: {date}").format(date=today.strftime('%Y-%m-%d')))
        report.append("")
        
        daily_entries = []

        for project in self.data["projects"]:
            main_project_name = project['main_project_name']
            for task in project["tasks"]:
                task_name = task['task_name']
                
                for entry in task["time_entries"]:
                    try:
                        start_time = datetime.fromisoformat(entry["start_time"])
                        if start_time.date() == today:
                            end_time_str = _("now")
                            
                            if "end_time" in entry:
                                end_time = datetime.fromisoformat(entry["end_time"])
                                end_time_str = end_time.strftime('%H:%M:%S')
                                duration = end_time - start_time
                            else:
                                duration = datetime.now() - start_time

                            duration_str = str(duration).split('.')[0]
                            start_time_str = start_time.strftime('%H:%M:%S')
                            
                            # Format as a list item for proper Markdown rendering
                            line = f"- {start_time_str}, {end_time_str}, {duration_str}, {main_project_name}, {task_name}"
                            daily_entries.append((start_time, line))
                    except (ValueError, KeyError):
                        continue
        
        # Sort entries by start time
        daily_entries.sort(key=lambda x: x[0])

        if daily_entries:
            for _timestamp, line in daily_entries:
                report.append(line)
        else:
            report.append(_("No time tracked for {date}.").format(date=today.strftime('%Y-%m-%d')))

        report.append("")
        report.append("Generated by TimeControl")
        report.append("https://github.com/frankfaulstich/TimeControl")

        report_text = "\n".join(report)
        self._copy_to_clipboard(report_text)
        return report_text