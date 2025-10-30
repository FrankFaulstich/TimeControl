import json
import sys
import os
from i18n import _
import requests
import zipfile
import shutil
from packaging.version import parse as parse_version

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
                if os.path.basename(target_path) in PROTECTED_FILES and os.path.exists(target_path):
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