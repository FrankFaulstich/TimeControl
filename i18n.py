import gettext
import json
import os

CONFIG_FILE = 'config.json'

# File-relative (not cwd-relative, unlike CONFIG_FILE above): a PyInstaller-
# frozen build extracts this module and the locale/ directory together into
# a temporary bundle folder whose path has nothing to do with the process's
# cwd (that's reserved for config.json/data.json instead - see
# TimeTrackerSL_GUI.py's _app_root()), so resolving 'locale' against cwd
# would silently fail to find any translation there.
_LOCALE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'locale')

def _initialize_translator():
    """
    Reads config, sets up gettext, and returns the translation function.
    This logic runs once when the module is first imported.
    """
    lang = 'en'
    config = {}

    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
            lang = config.get('language', 'en')
    except (FileNotFoundError, json.JSONDecodeError):
        pass  # Config will be created if it doesn't exist or is invalid

    if 'language' not in config:
        config['language'] = lang
        try:
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=4)
        except IOError:
            print(f"Warning: Could not write language setting to {CONFIG_FILE}")

    # If the language is English, we use the source strings directly.
    # This avoids issues where a stale or incorrect 'en' translation file (MO)
    # might contain translations from another language (e.g. copied from 'de').
    if lang == 'en':
        return lambda s: s

    try:
        translation = gettext.translation('timetracker', localedir=_LOCALE_DIR, languages=[lang], fallback=True)
        return translation.gettext
    except FileNotFoundError:
        return gettext.gettext

# The setup runs on import, and '_' is assigned the correct function.
# Any subsequent import of 'from i18n import _' will get this exact function.
_ = _initialize_translator()