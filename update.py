import hashlib
import json
import subprocess
import sys
import os
import threading
from i18n import _
import requests
import zipfile
import shutil
from packaging.version import parse as parse_version
from i18n import _ # Import translation function

UPDATE_ZIP_FILE = "update.zip"
CONFIG_FILE = "config.json"
PROTECTED_FILES = ["data.json", "config.json"] # Files that should not be overwritten during an update if they already exist

# --- Frozen (PyInstaller) build ---------------------------------------------
#
# A frozen build cannot be updated the way a source install is: there are no
# loose .py files to overwrite, and Windows refuses to overwrite an .exe that
# is currently running. What it does allow - established on a real Windows
# runner by a throwaway probe workflow, kept only in the git history as
# .github/workflows/probe-windows-selfupdate.yaml - is renaming
# the running image aside and putting a new one in its place, even with
# several processes started from that same image.
#
# So the update is split in two. The new build is downloaded next to the
# application while it runs, under a name of its own; the swap happens on the
# next start, at the one moment when only a single process is alive (see
# TimeTrackerSL_GUI.py). Nothing about the running installation is touched
# until the download is complete and has been checked.
WINDOWS_ASSET = "TimeControl.exe"       # release asset published by build-windows.yaml
PENDING_EXE = "TimeControl.exe.new"     # verified download, waiting for the next start
PREVIOUS_EXE = "TimeControl.exe.old"    # the image we replaced - our rollback
PARTIAL_EXE = "TimeControl.exe.part"    # download in progress, never swapped in
REJECTED_EXE = "TimeControl.exe.bad"    # a version rolled back by the user
CHECKSUM_SUFFIX = ".sha256"

# A onefile build of this application is tens of megabytes. Anything close to
# this size is a truncated download or a GitHub error page that arrived with a
# 200, not a program - and the whole point of checking is that we are about to
# put the result in the place of the user's working executable.
MIN_EXE_BYTES = 5 * 1024 * 1024

# requests' own `timeout=` only bounds the connect()/read() phases of a
# socket that already knows its target address - it does NOT cover DNS
# resolution (socket.getaddrinfo()), which runs first. With no internet
# connection at all (especially on Windows, or whenever DNS packets are
# silently dropped rather than actively refused), that lookup can hang far
# longer than any requests-level timeout - the classic "app hangs with no
# internet" gotcha. These deadlines are set comfortably above the request's
# own worst case (timeout applies separately to connect and read, so
# ~2x timeout) so they only ever kick in for that pathological hang.
UPDATE_CHECK_DEADLINE = 15   # seconds - hard ceiling for the "is there a new release" request
DOWNLOAD_DEADLINE = 65       # seconds - hard ceiling for opening the download connection


def _call_with_deadline(func, deadline, *args, **kwargs):
    """
    Runs func(*args, **kwargs) in a daemon thread and waits at most
    `deadline` seconds for it. Raises TimeoutError if it's still running
    after that.

    A daemon thread - not concurrent.futures.ThreadPoolExecutor - is
    deliberate: ThreadPoolExecutor registers its worker threads to be
    joined at interpreter exit, so a call that's genuinely stuck (e.g. DNS
    resolution that never gets a response) would still hang the whole app
    at shutdown even though we stopped waiting for it here. A daemon
    thread is hard-killed by the interpreter on exit instead, so a stuck
    lookup can never block the app from closing.
    """
    result = {}

    def _target():
        try:
            result['value'] = func(*args, **kwargs)
        except BaseException as exc:
            result['error'] = exc

    worker = threading.Thread(target=_target, daemon=True)
    worker.start()
    worker.join(deadline)
    if worker.is_alive():
        raise TimeoutError(f"Operation timed out after {deadline}s")
    if 'error' in result:
        raise result['error']
    return result.get('value')


def should_check_for_updates(session_state, current_menu):
    """
    True iff we haven't already checked for updates for this exact
    menu/view since it was last navigated to - i.e. once per view change,
    not once per rerun of the *same* view (a keystroke, a periodic
    auto-refresh tick, ...), which would hit GitHub far more often than
    the view itself actually changes.

    Takes session_state/current_menu as plain arguments (rather than a
    caller reaching into a global session object itself) purely so this
    decision stays testable without any UI framework running - session_state
    only needs a .get(key, default) method, so a Streamlit session_state or
    a plain dict both work.

    :param session_state: mapping-like object with .get(key, default)
    :param current_menu: identifier of the view currently being shown
    :return: True if check_for_updates() should be (re-)run now
    """
    return session_state.get('_update_checked_for_menu') != current_menu

def _get_github_repo_from_config():
    """Reads the GitHub repository slug from config.json."""
    if not os.path.exists(CONFIG_FILE):
        return None
    try:
        with open(CONFIG_FILE, 'r') as f:
            config = json.load(f)
            return config.get("update", {}).get("github_repo")
    except (json.JSONDecodeError, IOError):
        return None

def check_for_updates(current_version_str):
    """
    Checks GitHub for a new release.

    :param current_version_str: The current version of the application (e.g., "1.4").
    :return: A tuple (is_update_available, new_version, download_url) or (False, None, None).
    """
    github_repo = _get_github_repo_from_config()
    if not github_repo:
        print(_("Warning: Update check skipped. 'github_repo' not found in config.json or file is invalid."))
        return False, None, None

    api_url = f"https://api.github.com/repos/{github_repo}/releases/latest"
    try:
        response = _call_with_deadline(requests.get, UPDATE_CHECK_DEADLINE, api_url, timeout=5)
        response.raise_for_status()
        latest_release = response.json()

        latest_version_str = latest_release.get("tag_name", "").lstrip('v')
        current_version = parse_version(current_version_str)
        latest_version = parse_version(latest_version_str)

        if latest_version > current_version:
            print(_("A new version ({version}) is available.").format(version=latest_version_str))
            # A frozen build wants the published executable, not the source
            # zip - unpacking .py files next to an .exe would achieve nothing.
            if is_frozen():
                asset_url = _asset_url(latest_release)
                if asset_url:
                    return True, latest_version_str, asset_url
                print(_("Error: Download URL for the new version not found."))
                return False, None, None
            # Find the asset for the source code zip
            zip_url = latest_release.get("zipball_url")
            if zip_url:
                return True, latest_version_str, zip_url
            else:
                print(_("Error: Download URL for the new version not found."))
                return False, None, None

    except TimeoutError:
        print(_("Warning: Update check timed out (no internet connection?). Skipping."))
    except requests.exceptions.RequestException as e:
        print(_("Error checking for updates: {error}").format(error=e))
    except Exception as e:
        print(_("An unexpected error occurred while checking for updates: {error}").format(error=e))

    return False, None, None

def download_update(url):
    """
    Downloads the update file from the given URL.

    :param url: The URL to the update zip file.
    :return: True if download was successful, False otherwise.
    """
    try:
        print(_("Downloading update..."))
        response = _call_with_deadline(requests.get, DOWNLOAD_DEADLINE, url, stream=True, timeout=30)
        response.raise_for_status()
        with open(UPDATE_ZIP_FILE, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        print(_("Download complete. The update will be installed on the next start."))
        return True
    except TimeoutError:
        print(_("Error: Connecting to the update server timed out (no internet connection?)."))
        if os.path.exists(UPDATE_ZIP_FILE):
            os.remove(UPDATE_ZIP_FILE) # Clean up partial download
        return False
    except requests.exceptions.RequestException as e:
        print(_("Error downloading the update: {error}").format(error=e))
        if os.path.exists(UPDATE_ZIP_FILE):
            os.remove(UPDATE_ZIP_FILE) # Clean up partial download
        return False

# --- Frozen build: fetch, verify, swap, roll back ---------------------------

def is_frozen():
    """True when running from a PyInstaller bundle rather than from source."""
    return getattr(sys, 'frozen', False)


def _app_dir(target=None):
    """
    The directory the application executable lives in - where the downloaded
    build, the rollback copy and the user's data.json all sit side by side.

    `target` stands in for sys.executable so this is testable without an .exe.
    """
    return os.path.dirname(os.path.abspath(target or sys.executable))


def _discard(path):
    """Removes a file we no longer want, without making a fuss if it is gone."""
    try:
        os.remove(path)
    except OSError:
        pass


def _asset_url(release):
    """
    Download URL of the published executable in a GitHub release payload, or
    None when the release carries no such asset - true of every release made
    before build-windows.yaml existed, and of any whose build failed.
    """
    for asset in release.get("assets") or []:
        if asset.get("name") == WINDOWS_ASSET:
            return asset.get("browser_download_url")
    return None


def _published_checksum(url):
    """
    The SHA-256 published next to the executable, or None if this release
    does not carry one.

    Absent is not fatal: releases predating the checksum have none, and
    refusing to update from those would be worse than the size and format
    checks that run either way. A mismatch, on the other hand, is fatal.
    """
    try:
        response = _call_with_deadline(
            requests.get, UPDATE_CHECK_DEADLINE, url + CHECKSUM_SUFFIX, timeout=10)
        if response.status_code != 200:
            return None
        # The published file reads "<hex>  TimeControl.exe".
        return response.text.split()[0].strip().lower()
    except (TimeoutError, requests.exceptions.RequestException, IndexError):
        return None


def _rejection_reason(path, digest, expected):
    """
    Why the downloaded file must not be swapped in, or None if it passes.

    Worth being strict about: whatever comes back from this download is about
    to take the place of the user's working program. A truncated transfer or
    an error page that arrived with a 200 both look like a file, and neither
    is one we should install.

    Phrased to slot into the one sentence the caller prints, and translated
    here rather than left in English: half a message in the user's language
    reads worse than none of it.

    :param path: the downloaded file
    :param digest: its SHA-256, computed while it was being written
    :param expected: the checksum published with the release, or None
    """
    size = os.path.getsize(path)
    if size < MIN_EXE_BYTES:
        return _("the download is only {size} bytes and cannot be a complete "
                 "program").format(size=size)
    with open(path, 'rb') as handle:
        if handle.read(2) != b'MZ':
            return _("the download is not a Windows executable")
    if expected and digest != expected:
        return _("the checksum does not match the one published with the release")
    return None


def download_exe_update(url, target=None):
    """
    Downloads the published executable next to the running one and leaves it
    as PENDING_EXE, ready for the next start to swap in.

    While in flight the file carries a third name, so an interrupted download
    can never be mistaken for a finished one. It is renamed to PENDING_EXE
    only once its size, its format and - when the release publishes one - its
    checksum have been checked. Nothing about the running installation is
    touched here.

    :param url: the asset URL returned by check_for_updates()
    :return: True if a verified update is now waiting for the next start
    """
    here = _app_dir(target)
    partial = os.path.join(here, PARTIAL_EXE)
    pending = os.path.join(here, PENDING_EXE)

    expected = _published_checksum(url)
    if expected is None:
        print(_("Warning: this release publishes no checksum. Checking size and format only."))

    digest = hashlib.sha256()
    try:
        print(_("Downloading update..."))
        response = _call_with_deadline(requests.get, DOWNLOAD_DEADLINE, url, stream=True, timeout=30)
        response.raise_for_status()
        with open(partial, 'wb') as handle:
            for chunk in response.iter_content(chunk_size=65536):
                handle.write(chunk)
                digest.update(chunk)
    except TimeoutError:
        print(_("Error: Connecting to the update server timed out (no internet connection?)."))
        _discard(partial)
        return False
    except (requests.exceptions.RequestException, OSError) as exc:
        print(_("Error downloading the update: {error}").format(error=exc))
        _discard(partial)
        return False

    reason = _rejection_reason(partial, digest.hexdigest(), expected)
    if reason:
        print(_("The downloaded update was rejected: {reason}").format(reason=reason))
        _discard(partial)
        return False

    try:
        os.replace(partial, pending)
    except OSError as exc:
        print(_("Error downloading the update: {error}").format(error=exc))
        _discard(partial)
        return False

    print(_("Download complete. The update will be installed on the next start."))
    return True


def pending_exe_update(target=None):
    """True if a verified download is waiting to be swapped in."""
    return os.path.exists(os.path.join(_app_dir(target), PENDING_EXE))


def clear_update_leftovers(target=None):
    """
    Removes what an earlier session left lying around and nobody needs: the
    build a rollback pushed aside, and any download interrupted mid-transfer.

    Meant for startup. The rolled-back build could not be deleted at the time
    because it was still running; by now it is not. Neither of these is the
    rollback copy, which is kept on purpose until the next update replaces it.
    """
    here = _app_dir(target)
    _discard(os.path.join(here, REJECTED_EXE))
    _discard(os.path.join(here, PARTIAL_EXE))


def _clean_environment():
    """
    A copy of the environment without PyInstaller's handover variables.

    A onefile build unpacks itself into a temporary directory and tells the
    second stage where that is through the environment (_MEIPASS2 on older
    versions, _PYI_* on 6.x). A frozen process that starts its own image
    passes those straight on, so the new process takes itself for that second
    stage, skips unpacking, and reads from a directory belonging to the
    process it is meant to replace - out of an archive the swap has just
    renamed. It dies immediately.

    Measured rather than reasoned: with these left in place the relaunch never
    runs, no matter whether the launcher detaches it or waits around for a
    while afterwards, and stripping them is the only thing that helps. See
    .github/workflows/probe-windows-selfupdate.yaml, in the git history.
    """
    return {name: value for name, value in os.environ.items()
            if not name.startswith('_MEI') and not name.startswith('_PYI')}


def relaunch_frozen(target=None):
    """
    Starts the executable again once its image has been swapped.

    Only for the process that owns the application's lifetime - the one in
    TimeTrackerSL_GUI's __main__, before any subprocess has been spawned.
    Calling this from the Streamlit subprocess would start a second copy of
    the whole application alongside the running one.

    :return: True if the new process was started.
    """
    running = os.path.abspath(target or sys.executable)
    try:
        subprocess.Popen([running], env=_clean_environment())
        return True
    except OSError as exc:
        print(_("Error during update installation: {error}").format(error=exc))
        return False


def install_exe_update(target=None):
    """
    Puts the downloaded build in the place of the running executable.

    Call this at startup, before any child process has been spawned from the
    same image. The running .exe is renamed aside rather than overwritten -
    Windows permits the first and refuses the second - and the copy left
    behind under PREVIOUS_EXE is what restore_previous_exe() rolls back to.

    Between the two moves there is a brief moment in which no file carries
    the application's name. If the second fails the first is undone, so a
    failed update leaves the old version in place rather than nothing at all.

    :return: True if the swap happened.
    """
    running = os.path.abspath(target or sys.executable)
    here = os.path.dirname(running)
    pending = os.path.join(here, PENDING_EXE)
    previous = os.path.join(here, PREVIOUS_EXE)

    if not os.path.exists(pending):
        return False

    try:
        # os.replace overwrites, so the copy kept from the previous update is
        # gone the moment this succeeds: only one step back is ever available.
        os.replace(running, previous)
    except OSError as exc:
        print(_("Error during update installation: {error}").format(error=exc))
        return False

    try:
        os.replace(pending, running)
    except OSError as exc:
        print(_("Error during update installation: {error}").format(error=exc))
        try:
            os.replace(previous, running)
        except OSError:
            # Both moves failed, so the application is sitting under its old
            # name and nothing answers to the one the user double-clicks.
            # Say so plainly: this is the one case they have to fix by hand,
            # and silence would leave them with a program that has vanished.
            print(_("The previous version is still available as {filename}.").format(
                filename=PREVIOUS_EXE))
        return False

    print(_("Update installed successfully."))
    return True


def restore_previous_exe(target=None):
    """
    Rolls a frozen build back to the version it replaced.

    The same manoeuvre as the update, in reverse: the running executable is
    renamed aside and the copy kept from the last update takes its place.
    What was rolled back is kept under REJECTED_EXE rather than deleted,
    because it is still running - discard_rejected_exe() clears it at the
    next start.

    :return: True if the rollback happened.
    """
    running = os.path.abspath(target or sys.executable)
    here = os.path.dirname(running)
    previous = os.path.join(here, PREVIOUS_EXE)
    rejected = os.path.join(here, REJECTED_EXE)

    if not os.path.exists(previous):
        print(_("Error: No previous version backup '{filename}' found.").format(
            filename=PREVIOUS_EXE))
        return False

    _discard(rejected)
    try:
        os.replace(running, rejected)
    except OSError as exc:
        print(_("Error during restoration: {error}").format(error=exc))
        return False

    try:
        os.replace(previous, running)
    except OSError as exc:
        print(_("Error during restoration: {error}").format(error=exc))
        try:
            os.replace(rejected, running)
        except OSError:
            pass
        return False

    print(_("Previous version restored successfully."))
    return True


def apply_update(url):
    """
    Downloads (if not already downloaded) and installs the given update,
    then restarts the application in-place to run the new version.

    :param url: The download URL for the update, as returned by check_for_updates().
    """
    # A frozen build only fetches here. The swap needs a moment when no
    # second process is running from the same image, and that is the next
    # start, not now - see install_exe_update().
    if is_frozen():
        download_exe_update(url)
        return

    if not os.path.exists(UPDATE_ZIP_FILE):
        if not download_update(url):
            return
    install_update()
    print(_("Restarting application to apply the update..."))
    os.execv(sys.executable, ['python'] + sys.argv)

def install_update():
    """
    Installs the downloaded update by extracting the zip file and overwriting old files.
    Protected files (like data.json) will not be overwritten if they already exist.
    """
    if not os.path.exists(UPDATE_ZIP_FILE):
        return

    # --- Create backup of the current version before installing the new update ---
    backup_zip_file = "prev-version.zip"
    # Files/directories to exclude from the backup
    exclude_from_backup = [
        "data.json",          # User data should not be in the backup
        UPDATE_ZIP_FILE,      # The new update file itself
        backup_zip_file,      # The old backup file
        "__pycache__",        # Python cache files
        ".git",               # Git directory
        ".vscode",            # VSCode settings
        "docs/_build"         # Sphinx build output
    ]

    print(_("Creating backup of current version before update..."))
    try:
        with zipfile.ZipFile(backup_zip_file, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk('.'):
                # Exclude specified directories from being walked
                dirs[:] = [d for d in dirs if d not in exclude_from_backup]
                for file in files:
                    if file not in exclude_from_backup and not any(file.startswith(d) for d in exclude_from_backup if d.endswith('/')): # Also check for directory prefixes
                        file_path = os.path.join(root, file)
                        zipf.write(file_path, file_path) # arcname keeps the path structure
        print(_("Backup created successfully as {filename}.").format(filename=backup_zip_file))
    except Exception as e:
        print(_("Warning: Could not create backup. Error: {error}").format(error=e))

    print(_("Installing update..."))
    try:
        with zipfile.ZipFile(UPDATE_ZIP_FILE, 'r') as zip_ref:
            # The files are usually inside a root folder in the zip
            # e.g., FrankFaulstich-TimeControl-12345ab/
            root_folder = zip_ref.namelist()[0]
            
            for member in zip_ref.infolist():
                # Skip directories, extract only files
                if member.is_dir():
                    continue
                
                # Build the source and target paths
                source_path = member.filename
                # Remove the root folder from the path to get the relative path
                relative_path = source_path.replace(root_folder, '', 1)
                target_path = os.path.join(os.getcwd(), relative_path)

                # Check if the file is protected and already exists
                if os.path.basename(target_path) in PROTECTED_FILES and os.path.exists(target_path): # PROTECTED_FILES = ["data.json", "config.json"]
                    print(_("Skipping protected file: {filename}. It will not be overwritten.").format(filename=os.path.basename(target_path)))
                    continue # Skip this file
                # Ensure the target directory exists
                os.makedirs(os.path.dirname(target_path), exist_ok=True)
                
                # Extract and overwrite
                with zip_ref.open(source_path) as source, open(target_path, "wb") as target:
                    shutil.copyfileobj(source, target)

        print(_("Update installed successfully."))
    except Exception as e:
        print(_("Error during update installation: {error}").format(error=e))
    finally:
        # Clean up the zip file regardless of success
        if os.path.exists(UPDATE_ZIP_FILE):
            os.remove(UPDATE_ZIP_FILE)


def restore_previous_version():
    """
    Restores the application to the previous version from 'prev-version.zip'.
    'data.json' (user data) is explicitly NOT overwritten.
    The application restarts after successful restoration.
    """
    backup_zip_file = "prev-version.zip"
    if not os.path.exists(backup_zip_file):
        print(_("Error: No previous version backup '{filename}' found.").format(filename=backup_zip_file))
        return

    print(_("Restoring previous version from '{filename}'...").format(filename=backup_zip_file))
    try:
        with zipfile.ZipFile(backup_zip_file, 'r') as zip_ref:
            # Get the root folder name from the zip, if any (e.g., "FrankFaulstich-TimeControl-12345ab/")
            root_folder = ''
            if zip_ref.namelist():
                first_entry = zip_ref.namelist()[0]
                if '/' in first_entry:
                    root_folder = first_entry.split('/')[0] + '/'

            for member in zip_ref.infolist():
                if member.is_dir():
                    continue

                source_path = member.filename
                # Remove the root folder from the path to get the relative path
                relative_path = source_path.replace(root_folder, '', 1)
                target_path = os.path.join(os.getcwd(), relative_path)

                # Explicitly protect data.json from being overwritten during restore
                if os.path.basename(target_path) == "data.json" and os.path.exists(target_path):
                    print(_("Skipping user data file: {filename}. It will not be overwritten during restore.").format(filename=os.path.basename(target_path)))
                    continue

                os.makedirs(os.path.dirname(target_path), exist_ok=True)
                with zip_ref.open(source_path) as source, open(target_path, "wb") as target:
                    shutil.copyfileobj(source, target)

        print(_("Previous version restored successfully."))
        os.remove(backup_zip_file)
        print(_("Restarting application to apply changes..."))
        os.execv(sys.executable, ['python'] + sys.argv)
    except Exception as e:
        print(_("Error during restoration: {error}").format(error=e))
        print(_("The backup file '{filename}' was not deleted.").format(filename=backup_zip_file))

if __name__ == "__main__":
    try:
        from tt.TimeTracker import TimeTracker
        current_version = TimeTracker.VERSION
    except ImportError:
        print(_("Error: Could not import TimeTracker to get the current version."))
        sys.exit(1)

    print(_("Checking for updates..."))
    is_update, new_version, url = check_for_updates(current_version)

    if is_update and url:
        if download_update(url):
            install_update()
    else:
        print(_("No updates available."))