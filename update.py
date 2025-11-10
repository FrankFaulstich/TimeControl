import json
import sys
import os
from i18n import _
import requests
import zipfile
import shutil
from packaging.version import parse as parse_version
from i18n import _ # Import translation function

UPDATE_ZIP_FILE = "update.zip"
CONFIG_FILE = "config.json"
PROTECTED_FILES = ["data.json", "config.json"] # Files that should not be overwritten during an update if they already exist

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
        response = requests.get(api_url, timeout=5)
        response.raise_for_status()
        latest_release = response.json()
        
        latest_version_str = latest_release.get("tag_name", "").lstrip('v')
        current_version = parse_version(current_version_str)
        latest_version = parse_version(latest_version_str)

        if latest_version > current_version:
            print(_("A new version ({version}) is available.").format(version=latest_version_str))
            # Find the asset for the source code zip
            zip_url = latest_release.get("zipball_url")
            if zip_url:
                return True, latest_version_str, zip_url
            else:
                print(_("Error: Download URL for the new version not found."))
                return False, None, None

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
        response = requests.get(url, stream=True, timeout=30)
        response.raise_for_status()
        with open(UPDATE_ZIP_FILE, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        print(_("Download complete. The update will be installed on the next start."))
        return True
    except requests.exceptions.RequestException as e:
        print(_("Error downloading the update: {error}").format(error=e))
        if os.path.exists(UPDATE_ZIP_FILE):
            os.remove(UPDATE_ZIP_FILE) # Clean up partial download
        return False

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