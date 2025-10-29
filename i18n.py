import gettext
import json
import os

CONFIG_FILE = 'config.json'

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

    try:
        translation = gettext.translation('timetracker', localedir='locale', languages=[lang], fallback=True)
        return translation.gettext
    except FileNotFoundError:
        return gettext.gettext

# The setup runs on import, and '_' is assigned the correct function.
# Any subsequent import of 'from i18n import _' will get this exact function.
_ = _initialize_translator()