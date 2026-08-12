import json
import os
import tempfile
import imaplib
import re
import uuid
import email
from email.header import decode_header
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
    import markdown
except ImportError:
    markdown = None
try:
    # For version comparison in the update mechanism
    from packaging.version import parse as parse_version
except ImportError:
    parse_version = None


def _new_uid():
    """
    Returns a fresh identifier for a project, task or time entry.

    This is deliberately NOT the same thing as a task's integer 'id'.
    That one is a purely local counter (see next_id) and may legitimately
    differ between two machines holding the very same task - it exists so
    the GUI and the MCP/REST/SOAP interfaces have a short handle to pass
    around. This identifier, by contrast, is generated randomly, so two
    machines editing offline never produce the same one for different
    objects. It is what an entity can be addressed by across machines.

    16 hex characters are 64 bits of randomness. For the few tens of
    thousands of entities a personal time tracker accumulates over years,
    the chance of a collision is negligible, while keeping every stored
    record half the length a full uuid4 would add.

    :return: A 16-character hexadecimal identifier.
    :rtype: str
    """
    return uuid.uuid4().hex[:16]


# The task attributes that mean the same thing on every machine. Deliberately
# excluded: 'uid' (it is the address, not a field), 'id' (a local counter that
# is allowed to differ between machines) and 'time_entries' (carried by their
# own operations, so a task and its entries can be reconciled independently).
TASK_SYNC_FIELDS = (
    "task_name", "status", "due_date", "today", "note",
    "recurring", "frequency", "userdefined_days", "priority", "last_started",
)


def _task_fields(task):
    """Returns the syncable attributes of a task."""
    return {k: task.get(k) for k in TASK_SYNC_FIELDS if k in task}


class TimeTracker:
    """
    Manages time tracking for various main and sub-projects.
    
    The data is loaded from and saved to a JSON file.
    """
    VERSION = "4.1"
    STATUS_OPEN = "open"
    STATUS_CLOSED = "closed"
    STATUS_DONE = "done"
    HIDDEN_PROJECT = "hide"
    # Shape of data.json. 1 = the implicit, unstamped original layout;
    # 2 added a 'uid' to every project/task/time entry, a 'last_started'
    # timestamp, and the '_deleted' tombstone list. Stamped so a migration
    # can be recognised as already done rather than re-derived field by
    # field on every start.
    SCHEMA_VERSION = 2
    # How long a tombstone is kept before it is swept up. This has to stay
    # comfortably longer than the longest stretch a copy of the data might
    # plausibly go without being reconciled: forget a deletion here while
    # another copy still holds the object, and the object comes back.
    TOMBSTONE_RETENTION_DAYS = 90
    # Hard ceiling per package for the pip-install subprocess below. pip's own
    # request/retry timeouts only bound its HTTP phase, not the DNS lookup
    # that happens first - with no internet connection that lookup can hang
    # far longer than pip's own timeouts, and subprocess.check_call() has no
    # timeout of its own unless we pass one. Set generously above a normal
    # (even slow) install so this only ever kicks in for that dead-network
    # case, where subprocess's timeout=<N> kills the pip child outright.
    PIP_INSTALL_TIMEOUT = 120

    def __init__(self, file_path=None, op_outbox=None):
        """
        Initializes the TimeTracker, checks for dependencies, and loads data from the JSON file.

        :param file_path: The path to the JSON file where data is stored.
                          If None, the path is read from config.json (key 'data_file').
                          Defaults to 'data.json'.
        :type file_path: str
        :param op_outbox: Where changes are recorded for the sync server. Left
                          as None it is derived from config.json, which for
                          any installation without synchronisation switched on
                          - including every one that predates the feature -
                          means no queue and no recording at all. Passing one
                          explicitly is what the tests do.
        """
        config = {}
        if os.path.exists('config.json'):
            try:
                with open('config.json', 'r', encoding='utf-8') as f:
                    config = json.load(f)
            except (IOError, json.JSONDecodeError):
                config = {}

        if file_path is None:
            file_path = config.get('data_file', 'data.json') if config else 'data.json'

        self.file_path = file_path
        self.op_outbox = op_outbox
        if self.op_outbox is None:
            try:
                from tt.sync_outbox import default_outbox_if_enabled
                self.op_outbox = default_outbox_if_enabled(config)
            except ImportError:
                self.op_outbox = None

        self.data = self._load_data()
        if self._migrate_data_structure():
            # Migration is not a user action - it changes the shape of the
            # document, not its content - so it is deliberately not recorded
            # as operations. The other machine performs the same migration on
            # its own copy.
            self._save_data()

    def _emit(self, op, **fields):
        """
        Records one change for the sync server.

        Does nothing at all when synchronisation is off, which is the default
        and the only state an installation without a configured server can be
        in.

        Failures here are swallowed on purpose. This runs inside every
        mutating operation, and a queue that cannot be written - a full disk,
        a lock another process is sitting on - must not stop the user from
        tracking their time. The cost is that the change is not synced; the
        cost of the alternative is that the app stops working.

        :param op: One of the operation names the server accepts.
        """
        if self.op_outbox is None:
            return
        try:
            self.op_outbox.append(op, **fields)
        except Exception:
            pass

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
            for req in requirements: # e.g., "requests==2.28.1" or "mcp; python_version >= \"3.10\""
                # A simple check for the package name, ignoring version specifiers and
                # environment markers (the part after ';', e.g. 'python_version >= "3.10"').
                req_name = req.split(';')[0].split('==')[0].split('>=')[0].split('<=')[0].split('<')[0].split('>')[0].strip()
                if req_name.lower() not in installed_packages:
                    missing_packages.append(req)

            if missing_packages:
                print(_("Some required packages are missing. Attempting to install them..."))
                failed_packages = []
                for package in missing_packages:
                    print(_("Installing {package}...").format(package=package))
                    try:
                        subprocess.check_call(
                            [sys.executable, "-m", "pip", "install", package],
                            timeout=self.PIP_INSTALL_TIMEOUT,
                        )
                    except subprocess.CalledProcessError:
                        print(_("Failed to install {package}. Continuing without it.").format(package=package))
                        failed_packages.append(package)
                    except subprocess.TimeoutExpired:
                        print(_("Timed out installing {package} (no internet connection?). Continuing without it.").format(package=package))
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

    def reload_data(self):
        """
        Re-reads the data file from disk and brings it up to the current schema.

        Callers that only want to pick up changes another process made used to
        reach for _load_data() directly, which hands back whatever the file
        happens to hold. Migration runs in __init__ alone, so the refreshed
        document skipped it - and the rest of this class then reads fields it
        assumes are present (add_task takes self.data["next_id"] with no
        guard). That holds together while every writer runs this same version.
        It stops holding when the file arrives from somewhere else: a restored
        backup, a copy written by an older version, and in future one
        reconciled with another machine.

        If the file cannot be read the exception propagates and the previously
        loaded data is left untouched, so a caller can treat a transient
        failure as "keep what we have".
        """
        self.data = self._load_data()
        if self._migrate_data_structure():
            self._save_data()

    def _migrate_data_structure(self):
        """
        Ensures that the data structure is up to date.
        - Adds 'status': 'open' to sub-projects if missing.
        - Brings a schema-1 file up to schema 2: a 'uid' on every project,
          task and time entry, a 'last_started' timestamp, and the
          '_deleted' tombstone list (see SCHEMA_VERSION).

        Every step keys off the presence of the individual field rather
        than off the version stamp alone, so the migration stays idempotent
        and a file written by a mix of app versions still converges.

        :return: True if data was changed, otherwise False.
        :rtype: bool
        """
        data_changed = False
        # Ensure self.data and "projects" exist
        if "projects" not in self.data:
            self.data["projects"] = []
            data_changed = True # The data object itself was changed

        # Initialize next_id - or lift it back above the highest id actually
        # in use. This used to run only when the key was missing entirely and
        # never re-validated afterwards, so a file that gained tasks from
        # somewhere else (a restored backup, a hand edit, and in future a
        # sync) could end up with the counter sitting at or below a live id.
        # The next add_task() would then hand out an id a task already has,
        # and since nothing anywhere checks id uniqueness that duplicate
        # stays silent right up until delete_task() removes both of them.
        max_id = 0
        for project in self.data.get("projects", []):
            for task in project.get("tasks", []):
                try:
                    tid = int(task.get("id"))
                except (ValueError, TypeError):
                    continue
                if tid > max_id:
                    max_id = tid
        if self.data.get("next_id") is None or self.data["next_id"] <= max_id:
            self.data["next_id"] = max_id + 1
            data_changed = True

        for project in self.data.get("projects", []):
            # Migration: Add status to main projects
            if "status" not in project:
                project["status"] = self.STATUS_OPEN
                data_changed = True

            # Migration: Rename sub_projects to tasks
            if "sub_projects" in project:
                project["tasks"] = project.pop("sub_projects")
                data_changed = True

            # Schema 2: a machine-independent identity (see _new_uid).
            if not project.get("uid"):
                project["uid"] = _new_uid()
                data_changed = True

            for task in project.get("tasks", []):
                if "sub_project_name" in task:
                    task["task_name"] = task.pop("sub_project_name")
                    data_changed = True
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
                if "priority" not in task:
                    task["priority"] = 0
                    data_changed = True

                # Assign an integer ID if missing or not an integer (e.g. legacy GUID)
                task_id = task.get("id")
                if task_id is None or not isinstance(task_id, int):
                    task["id"] = self.data["next_id"]
                    self.data["next_id"] += 1
                    data_changed = True

                # Schema 2: identity, and the time entries below it.
                if not task.get("uid"):
                    task["uid"] = _new_uid()
                    data_changed = True

                for entry in task.get("time_entries", []):
                    if not entry.get("uid"):
                        entry["uid"] = _new_uid()
                        data_changed = True

                # Schema 2: 'last_started' will replace the implicit
                # most-recently-used ordering that start_work() currently
                # expresses by moving entries to the front of the list -
                # array position is state two machines would otherwise have
                # to agree on. Seeded from the newest time entry so the
                # existing ordering survives the switch instead of every
                # task starting out equal.
                if "last_started" not in task:
                    starts = [e.get("start_time") for e in task.get("time_entries", []) if e.get("start_time")]
                    task["last_started"] = max(starts) if starts else None
                    data_changed = True

            if "last_started" not in project:
                task_starts = [t["last_started"] for t in project.get("tasks", []) if t.get("last_started")]
                project["last_started"] = max(task_starts) if task_starts else None
                data_changed = True

        # Schema 2: deletions have to leave a trace. Without one, a machine
        # receiving an update cannot tell 'this was deleted elsewhere' apart
        # from 'this does not exist here yet', and deleted items come back on
        # every merge. Nothing writes to this list yet - that lands together
        # with the sync layer.
        if "_deleted" not in self.data:
            self.data["_deleted"] = []
            data_changed = True

        # Sweep expired tombstones. They only need to outlive the moment every
        # copy of the data has certainly seen them; keeping them for good would
        # grow the document without bound. Done here rather than on write so a
        # long-idle file is tidied on the next start, and so the sweep cannot
        # run in the middle of a deletion.
        cutoff = (datetime.now() - timedelta(days=self.TOMBSTONE_RETENTION_DAYS)).isoformat()
        still_relevant = [t for t in self.data["_deleted"] if t.get("at", "") >= cutoff]
        if len(still_relevant) != len(self.data["_deleted"]):
            self.data["_deleted"] = still_relevant
            data_changed = True

        # Stamped last, so a run that fails part way through is not recorded
        # as a completed migration.
        if self.data.get("schema_version") != self.SCHEMA_VERSION:
            self.data["schema_version"] = self.SCHEMA_VERSION
            data_changed = True

        return data_changed

    def _save_data(self):
        """
        Saves the current project data to the configured JSON file.

        Writes to a temporary file in the same directory first, then
        atomically swaps it into place with os.replace(). A plain
        open(path, 'w') would truncate the file before writing the new
        content, and several processes can share this same file (the GUI,
        the SOAP/REST/MCP servers) - one of them reloading data.json at that
        exact moment would see a truncated, invalid JSON file and crash.
        os.replace() has no such window: readers always see either the
        complete old file or the complete new one.
        """
        directory = os.path.dirname(os.path.abspath(self.file_path)) or '.'
        fd, tmp_path = tempfile.mkstemp(dir=directory, prefix='.tmp_data_', suffix='.json')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, indent=2)
            os.replace(tmp_path, self.file_path)
        except BaseException:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise

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

    def _markdown_to_rtf(self, text):
        """Converts basic Markdown to RTF format."""
        rtf = r"{\rtf1\ansi\deff0{\fonttbl{\f0\fnil\fcharset0 Arial;}}"
        rtf += r"\viewkind4\uc1\pard\lang1031\f0\fs24 "
        for line in text.split('\n'):
            # Escape RTF control characters
            line = line.replace('\\', '\\\\').replace('{', r'\{').replace('}', r'\}')
            if line.startswith('# '):
                rtf += r"\b\fs32 " + line[2:] + r"\b0\fs24\par "
            elif line.startswith('## '):
                rtf += r"\b\fs28 " + line[3:] + r"\b0\fs24\par "
            elif line.startswith('### '):
                rtf += r"\b\fs26 " + line[4:] + r"\b0\fs24\par "
            elif line.startswith('- '):
                rtf += r"\bullet  " + line[2:] + r"\par "
            else:
                # Basic bold replacement **text** -> \b text \b0
                line = re.sub(r'\*\*(.*?)\*\*', r'\\b \1\\b0', line)
                rtf += line + r"\par "
        rtf += "}"
        return rtf

    def _format_and_copy_report(self, markdown_text):
        """Formats the report based on config and copies it to clipboard."""
        config_format = "markdown"
        if os.path.exists('config.json'):
            try:
                with open('config.json', 'r', encoding='utf-8') as f:
                    config_format = json.load(f).get('report_format', 'markdown')
            except: pass
        final_text = markdown_text
        if config_format == 'html' and markdown:
            final_text = markdown.markdown(markdown_text)
        elif config_format == 'rtf':
            final_text = self._markdown_to_rtf(markdown_text)
        self._copy_to_clipboard(final_text)
        return final_text

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

    def _get_task(self, main_project_name, task_name=None, task_id=None):
        """
        Helper method to find a task by ID or name within a main project.
        If searching by name and duplicates exist, it prioritizes 'open' tasks.
        
        An id, when given, decides on its own: it names exactly one task,
        while names are not unique - nothing stops two tasks in the same
        project from sharing one. Both checks used to sit in the same pass of
        one loop, so a task that merely matched the name could be returned
        ahead of the task that actually carried the requested id, and callers
        pass both together as a matter of course.

        :param main_project_name: The name of the main project.
        :param task_name: The name of the task (optional).
        :param task_id: The unique ID of the task (optional, preferred).
        :return: The task dictionary or None if not found.
        """
        project = self._get_project(main_project_name)
        if not project:
            return None

        if task_id is not None:
            for task in project["tasks"]:
                # Robust comparison handling integer and string IDs
                if str(task.get("id")) == str(task_id):
                    return task
            return None

        if not task_name:
            return None

        fallback_task = None
        for task in project["tasks"]:
            if task["task_name"] == task_name:
                if task.get("status") == self.STATUS_OPEN:
                    return task
                if fallback_task is None:
                    fallback_task = task
        return fallback_task

    def add_main_project(self, main_project_name):
        """
        Adds a new main project.

        :param main_project_name: The name of the main project to add.
        :type main_project_name: str
        """
        new_project = {
            "uid": _new_uid(),
            "main_project_name": main_project_name,
            "tasks": [],
            "status": self.STATUS_OPEN,
            "last_started": None
        }
        self.data["projects"].append(new_project)
        self._emit('project.create', uid=new_project["uid"],
                   f={"name": main_project_name, "status": self.STATUS_OPEN})
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
            if project["main_project_name"] == self.HIDDEN_PROJECT:
                continue
            status = project.get("status", self.STATUS_OPEN)
            if status_filter == 'all' or status == status_filter:
                projects.append({
                    "main_project_name": project["main_project_name"],
                    "status": status
                })
        return projects

    def _record_deletion(self, entity, kind):
        """
        Notes that one project or task was deleted on purpose.

        Deletion is the one change that cannot be recognised from the data
        that is left behind: an object another copy has and this one does not
        is either an object this copy has not been told about yet, or one it
        deleted - and those look identical. This note is what tells them
        apart, so a deleted object is not handed back on the next reconcile.

        Only real deletions belong here. Removing an entry from a list is not
        by itself one: move_task() takes a task out of one project to put it
        into another, and the task lives on. Recording that as a deletion
        would destroy it everywhere else.

        :param entity: The project or task dict being deleted.
        :type entity: dict
        :param kind: Which level it sits on - 'project' or 'task'.
        :type kind: str
        """
        uid = entity.get("uid")
        if not uid:
            # Written by a version that predates uids (a rollback, say).
            # There is no identity to point at, so there is nothing useful
            # to record - better an unrecorded deletion than a note naming
            # nothing.
            return
        at = datetime.now().isoformat()
        self.data.setdefault("_deleted", []).append({
            "uid": uid,
            "kind": kind,
            "at": at
        })
        # Told to the sync server from here rather than from each of the five
        # call sites, so the operations and the tombstones cannot drift apart
        # - they are now the same decision, taken once. In particular the
        # deliberate omissions carry over for free: move_task never reaches
        # this method, and time entries never get a note of their own.
        #
        # The moment is sent along. The other machine keeps a note of its own
        # and expires it after ninety days; with nothing to date it from, that
        # note is swept on the very next start and the deletion it was
        # recording can then be undone by any later edit.
        self._emit(kind + '.delete', uid=uid, ts=at)

    def _record_project_deletion(self, project):
        """
        Notes a deleted project together with every task that went with it.

        Time entries deliberately get no note of their own: nothing in this
        class deletes a single entry, so an entry only ever disappears along
        with the task holding it - and that task's note already accounts for
        it. One note per project plus one per task keeps the list bounded
        while still covering the case where the other copy has meanwhile
        moved a task out of this project, which a project-only note would
        wrongly take down with it.

        :param project: The project dict being deleted.
        :type project: dict
        """
        self._record_deletion(project, "project")
        for task in project.get("tasks", []):
            self._record_deletion(task, "task")

    def delete_main_project(self, main_project_name):
        """
        Deletes a main project along with all associated tasks and time entries.

        :param main_project_name: The name of the main project to delete.
        :type main_project_name: str
        :return: True if the project was successfully deleted, otherwise False.
        :rtype: bool
        """
        initial_count = len(self.data["projects"])
        # Duplicate project names are creatable, and the filter below drops
        # every match - so collect them all rather than assuming there is one.
        removed = [p for p in self.data["projects"] if p["main_project_name"] == main_project_name]
        self.data["projects"] = [
            project for project in self.data["projects"] if project["main_project_name"] != main_project_name
        ]
        if len(self.data["projects"]) < initial_count:
            for project in removed:
                self._record_project_deletion(project)
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
            self._emit('project.set', uid=project.get("uid"), f={"name": new_name})
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
            self._emit('project.set', uid=project.get("uid"), f={"status": self.STATUS_CLOSED})
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
            self._emit('project.set', uid=project.get("uid"), f={"status": self.STATUS_OPEN})
            self._save_data()
            return True
        return False

    def add_task(self, main_project_name, task_name, due_date=None, today=False, note="", recurring=False, frequency="daily", userdefined_days=1, priority=0):
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
        :param priority: Priority from 0 (lowest, default) to 9 (highest).
        :type priority: int
        :return: True if the task was added successfully, otherwise False (if main project not found).
        :rtype: bool
        """
        project = self._get_project(main_project_name)
        if project:
            new_task = {
                "uid": _new_uid(),
                "id": self.data["next_id"],
                "task_name": task_name,
                "time_entries": [],
                "status": self.STATUS_OPEN,
                "due_date": due_date,
                "today": today,
                "note": note,
                "recurring": recurring,
                "frequency": frequency,
                "userdefined_days": userdefined_days,
                "priority": priority,
                "last_started": None
            }
            self.data["next_id"] += 1
            project["tasks"].append(new_task)
            self._emit('task.create', uid=new_task["uid"],
                       project=project.get("uid"), f=_task_fields(new_task))
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
        else:
            # Exclude hidden project when listing all
            projects_to_search = [p for p in projects_to_search if p.get("main_project_name") != self.HIDDEN_PROJECT]

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
                        # Show tasks only if the due date is exactly today.
                        # The 'today' flag is ignored here.
                        if not (due_date == today_str):
                            continue
                    elif planning_filter == 'tomorrow':
                        if due_date != tomorrow_str:
                            continue
                    elif planning_filter == 'weekly':
                        # Zeige exakt 7 Tage ab heute (heute inklusive, heute + 7 exklusive)
                        if not (due_date and today_str <= due_date < next_week_str):
                            continue
                    elif planning_filter == 'overdue':
                        if not (due_date and due_date < today_str):
                            continue
                    elif planning_filter == 'unplanned':
                        # A task is unplanned if it has no due date.
                        # The 'today' flag (star) no longer plays a role here.
                        if due_date:
                            continue

                results.append({
                    "id": task.get("id"),
                    "main_project_name": project["main_project_name"],
                    "task_name": task["task_name"],
                    "status": status,
                    "due_date": task.get("due_date"),
                    "today": task.get("today", False),
                    "note": task.get("note", ""),
                    "recurring": task.get("recurring", False),
                    "frequency": task.get("frequency", "daily"),
                    "userdefined_days": task.get("userdefined_days", 1),
                    "priority": task.get("priority", 0)
                })
        return results

    def cleanup_overdue_today_tasks(self):
        """
        Removes the 'today' flag (⭐) from tasks that have a due date in the past.
        
        Deliberately sends nothing to the sync server. Both machines run this
        same sweep, from the same rule, against the same due dates, so each
        reaches the identical result on its own. Sending it would spend
        traffic saying something the other side already knows, and two
        machines re-deriving and re-sending it could bounce it back and
        forth. What the sweep reads from - the due date - is synced; what it
        concludes is not.

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

    def set_today_flag_for_due_tasks(self):
        """
        Sets the 'today' flag (⭐) for tasks that have today's date as their due date
        and are not yet marked as 'today'.
        
        Like cleanup_overdue_today_tasks above, this sends nothing to the
        sync server: it is derived from the due date, which is synced, so the
        other machine reaches the same conclusion by itself.

        :return: True if any task was updated and saved.
        :rtype: bool
        """
        today_str = date.today().isoformat()
        changed = False
        for project in self.data.get("projects", []):
            for task in project.get("tasks", []):
                # Only consider open tasks
                if task.get('status') == self.STATUS_OPEN:
                    # If due date is today and 'today' flag is not set
                    if task.get('due_date') == today_str and not task.get('today'):
                        task['today'] = True
                        changed = True
        if changed:
            self._save_data()
        return changed

    def delete_task(self, main_project_name, task_name, task_id=None):
        """
        Deletes a task from a main project.

        :param main_project_name: The name of the main project.
        :type main_project_name: str
        :param task_name: The name of the task to delete.
        :type task_name: str
        :param task_id: Unique ID of the task (optional, preferred).
        :return: True if the task was deleted, otherwise False.
        :rtype: bool
        """
        project = self._get_project(main_project_name)
        if project:
            initial_count = len(project["tasks"])
            # Both filters below remove EVERY match, not just the first, so
            # the removed set is collected the same way.
            if task_id is not None:
                removed = [t for t in project["tasks"] if str(t.get("id")) == str(task_id)]
                project["tasks"] = [t for t in project["tasks"] if str(t.get("id")) != str(task_id)]
            else:
                removed = [t for t in project["tasks"] if t["task_name"] == task_name]
                project["tasks"] = [t for t in project["tasks"] if t["task_name"] != task_name]

            if len(project["tasks"]) < initial_count:
                for task in removed:
                    self._record_deletion(task, "task")
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
                    self._record_deletion(tasks[i], "task")
                    del tasks[i]
                    deleted_count += 1

        if deleted_count > 0:
            self._save_data()
        
        return deleted_count

    def close_task(self, main_project_name, task_name, task_id=None):
        """
        Sets the status of a task to 'closed'.

        :param main_project_name: The name of the main project.
        :type main_project_name: str
        :param task_name: The name of the task to close.
        :type task_name: str
        :param task_id: Unique ID of the task (optional).
        :return: True if the task was closed, otherwise False.
        :rtype: bool
        """
        task = self._get_task(main_project_name, task_name, task_id)
        if task:
            task["status"] = self.STATUS_CLOSED
            self._emit('task.set', uid=task.get("uid"), f={"status": self.STATUS_CLOSED})
            self._save_data()
            return True
        return False

    def reopen_task(self, main_project_name, task_name, task_id=None):
        """
        Sets the status of a task to 'open'.

        :param main_project_name: The name of the main project.
        :type main_project_name: str
        :param task_name: The name of the task to reopen.
        :type task_name: str
        :param task_id: Unique ID of the task (optional).
        :return: True if the task was reopened, otherwise False.
        :rtype: bool
        """
        task = self._get_task(main_project_name, task_name, task_id)
        if task:
            task["status"] = self.STATUS_OPEN
            self._emit('task.set', uid=task.get("uid"), f={"status": self.STATUS_OPEN})
            self._save_data()
            return True
        return False

    def rename_task(self, main_project_name, old_task_name, new_task_name, task_id=None):
        """
        Renames a task within a given main project.

        :param main_project_name: The name of the main project containing the task.
        :type main_project_name: str
        :param old_task_name: The current name of the task to rename.
        :type old_task_name: str
        :param new_task_name: The new name for the task.
        :type new_task_name: str
        :param task_id: Unique ID of the task (optional).
        :return: True if renaming was successful, False otherwise (e.g., project not found,
                 or new name already exists).
        :rtype: bool
        """
        project = self._get_project(main_project_name)
        if project:
            task = self._get_task(main_project_name, old_task_name, task_id)
            if task:
                task["task_name"] = new_task_name
                self._emit('task.set', uid=task.get("uid"), f={"task_name": new_task_name})
                self._save_data()
                return True
        return False

    def update_task(self, main_project_name, old_task_name, new_task_name=None, due_date=None, today=None, note=None, status=None, recurring=None, frequency=None, userdefined_days=None, priority=None, task_id=None, clear_due_date=False):
        """
        Updates a task's properties. Every field is left as it is unless a
        value for it is actually passed, the due date included - removing a
        due date is asked for explicitly, via clear_due_date.

        :param main_project_name: Name of the main project.
        :param old_task_name: Current name of the task.
        :param new_task_name: New name (optional).
        :param due_date: New due date (optional, ISO string). None keeps the
                         current one; use clear_due_date to remove it.
        :param today: New today status (optional, bool).
        :param note: New note (optional, str).
        :param status: New status (optional, str).
        :param recurring: Recurring status (optional, bool).
        :param frequency: Frequency (optional, str).
        :param userdefined_days: Days for userdefined frequency (optional, int).
        :param priority: Priority from 0 (lowest) to 9 (highest) (optional, int).
        :param task_id: Unique ID of the task (optional).
        :param clear_due_date: Remove the task's due date (optional, bool).
                               Takes precedence over due_date. Last in the
                               signature so existing positional callers keep
                               working.
        :return: True if successful.
        """
        project = self._get_project(main_project_name)
        if project:
            task = self._get_task(main_project_name, old_task_name, task_id)
            if task:
                # Snapshot taken so the change can be reported as a difference
                # rather than by listing the fields again here. That keeps the
                # two from drifting when a field is added later, and means
                # nothing is sent when a save turns out to change nothing.
                before = _task_fields(task)

                # Handle recurring task generation
                is_completing = (status == self.STATUS_DONE and task.get("status") != self.STATUS_DONE)
                is_recurring = recurring if recurring is not None else task.get("recurring", False)

                if is_completing and is_recurring:
                    self._create_next_recurring_instance(project, task, due_date, recurring, frequency, userdefined_days, note, priority)

                if new_task_name:
                    task["task_name"] = new_task_name

                # An omitted due_date means "unchanged", exactly like every
                # other field here. It used to mean "clear", so a caller
                # updating one unrelated field - a PATCH carrying just a
                # priority, say - wiped the due date as a side effect, and
                # with sync enabled dutifully propagated that to the user's
                # other machines.
                if clear_due_date:
                    task["due_date"] = None
                elif due_date is not None:
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
                if priority is not None:
                    task["priority"] = priority

                after = _task_fields(task)
                changed = {k: v for k, v in after.items() if before.get(k) != v}
                if changed:
                    self._emit('task.set', uid=task.get("uid"), f=changed)

                self._save_data()
                return True
        return False

    def _create_next_recurring_instance(self, project, task, due_date_param, recurring_param, freq_param, ud_days_param, note_param=None, priority_param=None):
        freq = freq_param if freq_param is not None else task.get("frequency", "daily")
        ud_days = ud_days_param if ud_days_param is not None else task.get("userdefined_days", 1)
        base_due = due_date_param if due_date_param is not None else task.get("due_date")
        note = note_param if note_param is not None else task.get("note", "")
        priority = priority_param if priority_param is not None else task.get("priority", 0)

        next_due = self._calculate_next_due_date(base_due, freq, ud_days)

        new_task = {
            "uid": _new_uid(),
            "id": self.data["next_id"],
            "task_name": task["task_name"],
            "time_entries": [], # Start with a fresh, empty list for the new instance
            "status": self.STATUS_OPEN,
            "due_date": next_due,
            "today": False,
            "note": note,
            "recurring": True,
            "frequency": freq,
            "userdefined_days": ud_days,
            "priority": priority,
            "last_started": None
        }
        self.data["next_id"] += 1
        project["tasks"].append(new_task)
        self._emit('task.create', uid=new_task["uid"],
                   project=project.get("uid"), f=_task_fields(new_task))

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

    def move_task(self, old_main_project_name, task_name, new_main_project_name, task_id=None):
        """
        Moves a task from one main project to another.

        :param old_main_project_name: The name of the source main project.
        :type old_main_project_name: str
        :param task_name: The name of the task to move.
        :type task_name: str
        :param new_main_project_name: The name of the destination main project.
        :type new_main_project_name: str
        :param task_id: Unique ID of the task (optional).
        :return: A tuple (bool, str) indicating success and a message.
        :rtype: tuple(bool, str)
        """
        source_project = self._get_project(old_main_project_name)
        dest_project = self._get_project(new_main_project_name)

        if not source_project:
            return False, _("Source main project '{name}' not found.").format(name=old_main_project_name)
        if not dest_project:
            return False, _("Destination main project '{name}' not found.").format(name=new_main_project_name)

        # Find and remove task from source
        task_to_move = None
        for i, t in enumerate(source_project["tasks"]):
            if (task_id is not None and str(t.get("id")) == str(task_id)) or (task_id is None and t["task_name"] == task_name):
                task_to_move = source_project["tasks"].pop(i)
                break

        if task_to_move:
            dest_project["tasks"].append(task_to_move)
            # A move, not a delete-and-recreate: the task keeps its identity,
            # so the other machine re-parents the very same object and its
            # time entries travel with it untouched.
            self._emit('task.move', uid=task_to_move.get("uid"),
                       project=dest_project.get("uid"))
            self._save_data()
            return True, _("Task '{task_name}' moved successfully.").format(task_name=task_name)
        return False, _("Task '{task_name}' not found in '{main_name}'.").format(task_name=task_name, main_name=old_main_project_name)

    def promote_task_to_project(self, main_project_name, task_name_to_promote, task_id=None):
        """
        Promotes a task to a new main project.

        The time entries of the sub-project are preserved and moved to a new sub-project
        named 'General' within the new main project.

        :param main_project_name: The name of the current main project.
        :type main_project_name: str
        :param task_name_to_promote: The name of the task to promote.
        :type task_name_to_promote: str
        :param task_id: Unique ID of the task (optional).
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
            if (task_id is not None and str(t.get("id")) == str(task_id)) or (task_id is None and t["task_name"] == task_name_to_promote):
                task_index = i
                break

        if task_index is None:
            return False, _("Task '{task_name}' not found in '{main_name}'.").format(task_name=task_name_to_promote, main_name=main_project_name)

        # Remove task from old main project and get its data. The task itself
        # does not survive this - its time entries are re-homed under the
        # "General" task created below, but the task object is gone, so it
        # needs a tombstone. The entries do not: they are being moved, and
        # they keep the identity they already carry.
        task_data = source_project["tasks"].pop(task_index)
        time_entries = task_data.get("time_entries", [])
        # The deletion is recorded further down, after the entries have been
        # reported as re-parented. Recording it here would put the operations
        # on the wire in the order "delete this task" then "move its entries
        # somewhere else" - and the receiving machine, applying them in that
        # order, would destroy the entries along with the task before being
        # told where they were going.

        # Create the new main project.
        # The project and its task used to be stored bare - no id, no status,
        # none of the other task fields - and were only completed by the
        # migration on the next start. They are filled in here now, because a
        # uid has to be assigned exactly once at creation; leaving the object
        # half-built means the uid only appears later, on whichever machine
        # happens to restart first. The time entries keep the uids they
        # already carry: they are being moved, not recreated.
        starts = [e.get("start_time") for e in time_entries if e.get("start_time")]
        last_started = max(starts) if starts else None
        new_main_project = {
            "uid": _new_uid(),
            "main_project_name": task_name_to_promote,
            "tasks": [{
                "uid": _new_uid(),
                "id": self.data["next_id"],
                "task_name": _("General"),
                "time_entries": time_entries,
                "status": self.STATUS_OPEN,
                "due_date": None,
                "today": False,
                "note": "",
                "recurring": False,
                "frequency": "daily",
                "userdefined_days": 1,
                "priority": 0,
                "last_started": last_started
            }],
            "status": self.STATUS_OPEN,
            "last_started": last_started
        }
        self.data["next_id"] += 1
        self.data["projects"].append(new_main_project)

        # Reported as its parts rather than as one "promote" verb, so the
        # other machine needs no rule for a compound restructuring: a project
        # appears, a task appears inside it, the entries are re-parented, and
        # the old task is deleted (by _record_deletion above). Each part is an
        # operation the applier already knows, and the entries keep the
        # identities they had, so no tracked time is recreated or lost.
        general = new_main_project["tasks"][0]
        self._emit('project.create', uid=new_main_project["uid"],
                   f={"name": task_name_to_promote, "status": self.STATUS_OPEN,
                      "last_started": last_started})
        self._emit('task.create', uid=general["uid"],
                   project=new_main_project["uid"], f=_task_fields(general))
        for entry in time_entries:
            if entry.get("uid"):
                self._emit('entry.move', uid=entry["uid"], task=general["uid"])

        # Only now: the entries have a new home on both machines.
        self._record_deletion(task_data, "task")

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

        # 2. Consolidate all time entries
        all_time_entries = []
        
        # Iterate through all tasks of the project to be demoted
        if "tasks" in project_to_demote:
            for task in project_to_demote["tasks"]:
                # Extend the list with the time entries of each task
                if "time_entries" in task:
                    all_time_entries.extend(task["time_entries"])

        # Sort entries by start time to maintain chronological order
        all_time_entries.sort(key=lambda x: x['start_time'])

        # 3. Create the new task. Stored complete rather than bare for the
        #    same reason as in promote_task_to_project() above. The moved
        #    time entries keep their existing uids.
        starts = [e.get("start_time") for e in all_time_entries if e.get("start_time")]
        new_task = {
            "uid": _new_uid(),
            "id": self.data["next_id"],
            "task_name": main_project_to_demote_name,
            "time_entries": all_time_entries,
            "status": self.STATUS_OPEN,
            "due_date": None,
            "today": False,
            "note": "",
            "recurring": False,
            "frequency": "daily",
            "userdefined_days": 1,
            "priority": 0,
            "last_started": max(starts) if starts else None
        }
        self.data["next_id"] += 1
        new_parent_project["tasks"].append(new_task)

        # Told to the other machine as its parts, as in promote above: the
        # consolidated task appears, every entry is re-parented onto it, and
        # only then is the old project (with its tasks) deleted. That order
        # matters - re-homing the entries before their old task disappears is
        # what keeps tracked time from being caught by the deletion.
        self._emit('task.create', uid=new_task["uid"],
                   project=new_parent_project.get("uid"), f=_task_fields(new_task))
        for entry in all_time_entries:
            if entry.get("uid"):
                self._emit('entry.move', uid=entry["uid"], task=new_task["uid"])

        # 4. Remove the old main project and save. The project and every task
        #    it held are destroyed here - only their time entries live on, in
        #    the single consolidated task created above - so both levels are
        #    recorded, the entries are not.
        self._record_project_deletion(project_to_demote)
        self.data["projects"].pop(project_to_demote_index)
        self._save_data()
        return True, _("Main project '{demoted_name}' was demoted to a sub-project under '{parent_name}'.").format(demoted_name=main_project_to_demote_name, parent_name=new_parent_main_project_name)

    def _next_started_at(self):
        """
        A start timestamp that is strictly later than every one recorded.

        Most-recently-used ordering is derived by sorting on `last_started`,
        so two stamps that are equal leave the order undecided - and a stable
        sort then keeps the older one in front, which is exactly backwards.

        That is not hypothetical. `datetime.now()` resolves to about 16
        milliseconds on Windows, so two starts in quick succession - a click
        followed by another, or the GUI and an MCP call - genuinely land on
        the same value there. The clock can also step backwards, over a
        daylight-saving change or an NTP correction.

        So the wall clock is used when it is ahead of everything on record,
        and nudged past the highest stamp when it is not. The result is still
        a real timestamp a person can read; it is only ever adjusted by
        microseconds, and it goes back to following the clock as soon as the
        clock has caught up.
        """
        now = datetime.now()
        highest = None
        for project in self.data.get("projects", []):
            for value in [project.get("last_started")] + \
                         [t.get("last_started") for t in project.get("tasks", [])]:
                if value and (highest is None or value > highest):
                    highest = value
        if highest is None:
            return now.isoformat()
        try:
            latest = datetime.fromisoformat(highest)
        except (TypeError, ValueError):
            return now.isoformat()
        if now > latest:
            return now.isoformat()
        return (latest + timedelta(microseconds=1)).isoformat()

    @staticmethod
    def _sort_by_last_started(items):
        """
        Orders a list of projects or tasks most-recently-started first, in place.

        Items that were never started (last_started is None) go to the end.
        Python's sort is stable and stays stable with reverse=True, so among
        those the original order survives - which for a never-started item is
        the order it was created in, exactly where it sits today.

        :param items: The list of project or task dicts to reorder.
        :type items: list[dict]
        """
        items.sort(key=lambda item: item.get("last_started") or "", reverse=True)

    def start_work(self, main_project_name, task_name=None, task_id=None):
        """
        Starts a new time tracking session for a task by saving the start time.
        Any currently active session is stopped before starting the new one.
        The affected task and main project are moved to the top of their respective lists.

        :param main_project_name: The parent main project name.
        :type main_project_name: str
        :param task_name: The name of the task (optional).
        :param task_id: The unique ID of the task (optional, preferred).
        :return: True if work was started successfully, otherwise False.
        :rtype: bool
        """
        # This used to carry its own copy of the project/task lookup - along
        # with its own copy of the bug where a name match could beat the id
        # that was actually asked for. It now defers to _get_task, so the
        # lookup rules live in exactly one place.
        main_project = self._get_project(main_project_name)
        task = self._get_task(main_project_name, task_name, task_id) if main_project else None

        if task and main_project:
            # Only stop the previous session once we know a new one can
            # actually be started - otherwise a typo'd/unknown task would
            # silently end whatever was running without replacing it.
            self.stop_work()

            # Add the new time entry. The uid is what lets a specific entry be
            # referred to at all - "the last element of some array" stops
            # meaning anything once two machines hold their own copy.
            started_at = self._next_started_at()
            new_entry = {
                "uid": _new_uid(),
                "start_time": started_at
            }
            task["time_entries"].append(new_entry)

            # Most-recently-used ordering used to be expressed by physically
            # moving the task and its project to the front of their arrays,
            # which made array position the only record of "what did I work on
            # last". That is state, and state nothing can derive: two machines
            # holding the same projects would have to agree on it, with no
            # field to reconcile it from. It is carried by last_started now,
            # and the arrays are merely kept in that order - so the ordering
            # every caller sees is unchanged, but it is reproducible from the
            # data rather than stored alongside it.
            task["last_started"] = started_at
            main_project["last_started"] = started_at
            self._sort_by_last_started(main_project["tasks"])
            self._sort_by_last_started(self.data["projects"])

            # last_started is sent explicitly rather than left for the other
            # machine to derive from the entry. Deriving it would work only
            # as long as every entry ever reaches the other side, and the
            # ordering the user sees should not depend on that.
            self._emit('entry.add', uid=new_entry["uid"], task=task.get("uid"),
                       start=started_at)
            self._emit('task.set', uid=task.get("uid"), f={"last_started": started_at})
            self._emit('project.set', uid=main_project.get("uid"),
                       f={"last_started": started_at})

            self._save_data()
            return True
            
        return False

    def fetch_emails_to_tasks(self):
        """
        Fetches emails from the configured IMAP account and creates tasks in the 'hide' project.
        """
        config_data = {}
        if os.path.exists('config.json'):
            try:
                with open('config.json', 'r', encoding='utf-8') as f:
                    config_data = json.load(f).get('email', {})
            except: pass
            
        if not config_data.get('enabled'):
            return 0, _("Email import is not enabled.")
            
        server = config_data.get('imap_server')
        port = config_data.get('imap_port', 993)
        user = config_data.get('user')
        password = config_data.get('password')
        use_ssl = config_data.get('use_ssl', True)
        
        if not all([server, user, password]):
            return 0, _("Email settings are incomplete.")

        try:
            if use_ssl:
                mail = imaplib.IMAP4_SSL(server, port)
            else:
                mail = imaplib.IMAP4(server, port)
                
            mail.login(user, password)
            mail.select("inbox")
            
            status, messages = mail.search(None, "ALL")
            if status != "OK":
                return 0, _("Error searching emails.")
                
            mail_ids = messages[0].split()
            count = 0
            
            if not self._get_project(self.HIDDEN_PROJECT):
                self.add_main_project(self.HIDDEN_PROJECT)

            for m_id in mail_ids:
                status, data = mail.fetch(m_id, "(RFC822)")
                if status != "OK": continue
                
                msg = email.message_from_bytes(data[0][1])
                
                # Decode Subject
                subject_header = msg.get("Subject", _("No Subject"))
                decoded_parts = decode_header(subject_header)
                subject = ""
                for part, encoding in decoded_parts:
                    if isinstance(part, bytes):
                        subject += part.decode(encoding or "utf-8", errors="replace")
                    else: subject += part
                
                # Extract Body
                body = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == "text/plain":
                            body = part.get_payload(decode=True).decode(part.get_content_charset() or 'utf-8', errors='replace')
                            break
                else:
                    body = msg.get_payload(decode=True).decode(msg.get_content_charset() or 'utf-8', errors='replace')
                
                self.add_task(self.HIDDEN_PROJECT, subject, due_date=date.today().isoformat(), note=body)
                count += 1
                mail.store(m_id, '+FLAGS', '\\Deleted')
            
            mail.expunge()
            mail.logout()
            return count, None
            
        except Exception as e:
            return 0, str(e)

    def stop_work(self):
        """
        Stops the currently active time tracking session by adding the end time 
        to the most recently started entry.

        :return: True if a session was stopped successfully, otherwise False.
        :rtype: bool
        """
        for project in reversed(self.data["projects"]):
            for task in reversed(project["tasks"]):
                if task["time_entries"] and "end_time" not in task["time_entries"][-1]:
                    entry = task["time_entries"][-1]
                    end_time = datetime.now().isoformat()
                    # An entry must never end before it began. Every duration
                    # in every report is these two subtracted from one another,
                    # so a negative one does not announce itself - it just
                    # quietly makes the numbers wrong. It can happen without
                    # anyone doing something odd: a clock corrected backwards,
                    # the switch off daylight saving, and later a session
                    # closed on the strength of another machine's clock.
                    start_time = entry.get("start_time")
                    if start_time and end_time < start_time:
                        end_time = start_time
                    entry["end_time"] = end_time
                    self._emit('entry.close', uid=entry.get("uid"), end=end_time)
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
                        "task_name": task.get("task_name", task.get("sub_project_name", _("Unknown Task"))),
                        "start_time": task["time_entries"][-1]["start_time"]
                    }
        return None

    def list_inactive_tasks(self, inactive_weeks):
        """
        Lists sub-projects that have not had any activity (completed time entry)
        within the specified number of weeks.
        Currently running sessions are ignored (i.e., not listed as inactive).
        Sub-projects with no time entries are also ignored.
        Closed tasks are excluded, but 'done' tasks are included since they
        may still need to be closed.
        Tasks due today or in the future are excluded, since they are still
        actively scheduled rather than abandoned.

        :param inactive_weeks: The number of weeks defining the inactivity threshold.
        :type inactive_weeks: int
        :return: A list of dictionaries, each containing 'main_project', 'task_name',
                 and the 'last_activity' timestamp (formatted).
        :rtype: list[dict]
        """
        cutoff_date = datetime.now() - timedelta(weeks=inactive_weeks)
        today_str = date.today().isoformat()
        inactive_projects = []

        for project in self.data["projects"]:
            if project["main_project_name"] == self.HIDDEN_PROJECT:
                continue
            for task in project["tasks"]:
                if not task.get("time_entries"):
                    # Ignore sub-projects with no entries
                    continue

                # Check if the task is currently running (active)
                last_entry = task["time_entries"][-1]
                if "end_time" not in last_entry:
                    continue # Skip if currently running

                # Exclude closed tasks, but keep 'done' tasks (they may still need closing).
                # This check is now after the 'running' check to correctly ignore running projects regardless of status.
                if task.get("status", self.STATUS_OPEN) == self.STATUS_CLOSED:
                    continue

                # Exclude tasks due today or in the future - they are still scheduled.
                due_date = task.get("due_date")
                if due_date and due_date >= today_str:
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
                        "id": task.get("id"),
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
            if project["main_project_name"] == self.HIDDEN_PROJECT:
                continue
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
            if project["main_project_name"] == self.HIDDEN_PROJECT:
                continue
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
                                task_total_time += duration
                    except (ValueError, KeyError):
                        continue

                # Add to report only if time was tracked for this sub-project on the specified date
                if task_total_time.total_seconds() > 0:
                    hours = task_total_time.total_seconds() / 3600
                    hours_str = f"{hours:.3f}".replace('.', ',')
                    task_details.append(_("- {name}: {hours} hours").format(name=task['task_name'], hours=hours_str))
                    main_project_total_time += task_total_time

            # Add main project and its sub-projects to report if it has entries for the specified date
            if main_project_total_time.total_seconds() > 0:
                total_daily_time += main_project_total_time
                hours = main_project_total_time.total_seconds() / 3600
                hours_str = f"{hours:.3f}".replace('.', ',')
                report.append(_("## {name} ({hours} hours)\n").format(name=project['main_project_name'], hours=hours_str))
                report.extend(task_details)
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
        
        return self._format_and_copy_report("\n".join(report))

    def generate_task_report(self, main_project_name, task_name):
        """
        Generates a detailed report for a single task.

        :param main_project_name: The name of the main project.
        :type main_project_name: str
        :param task_name: The name of the task.
        :type task_name: str
        :return: The formatted report as a Markdown string, or an error message.
        :rtype: str
        """
        project = self._get_project(main_project_name)
        if not project:
            return _("Main project '{name}' not found.").format(name=main_project_name)

        task = self._get_task(main_project_name, task_name)
        if not task:
            return _("Task '{task_name}' not found in '{main_name}'.").format(task_name=task_name, main_name=main_project_name)

        entries = task.get("time_entries", [])
        if not entries:
            return _("No time entries found for task '{task_name}'.").format(task_name=task_name)

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

        return self._format_and_copy_report("\n".join(report))

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

        return self._format_and_copy_report("\n".join(report))

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
        
        return self._format_and_copy_report("\n".join(report))

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

        return self._format_and_copy_report("\n".join(report))