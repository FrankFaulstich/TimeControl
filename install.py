import sys
import subprocess
import os

# Attempt to import importlib.metadata to check installed packages (Python 3.8+)
try:
    from importlib.metadata import distributions
except ImportError:
    try:
        from importlib_metadata import distributions
    except ImportError:
        distributions = None

def check_and_install_requirements():
    """
    Reads requirements.txt, checks for missing packages and installs them.
    """
    requirements_file = 'requirements.txt'
    
    if not os.path.exists(requirements_file):
        print(f"Fehler: '{requirements_file}' wurde nicht gefunden.")
        return

    print(f"Prüfe Abhängigkeiten aus {requirements_file}...")

    requirements_to_install = []
    
    # Read file
    try:
        with open(requirements_file, 'r', encoding='utf-8') as f:
            lines = []
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    lines.append(line)
    except Exception as e:
        print(f"Fehler beim Lesen der Datei: {e}")
        sys.exit(1)

    if distributions:
        # Determine installed packages (names normalized to lowercase)
        installed_packages = {
            dist.metadata['Name'].lower() 
            for dist in distributions() 
            if dist.metadata and dist.metadata['Name']
        }
        
        for req in lines:
            # Extract package names (e.g. from 'requests==2.28.1' -> 'requests')
            pkg_name = req.split('==')[0].split('>=')[0].split('<=')[0].split('<')[0].split('>')[0].strip()
            
            if pkg_name.lower() not in installed_packages:
                requirements_to_install.append(req)
    else:
        # Fallback: If we cannot check installed packages, we let pip handle the check
        requirements_to_install = lines

    if requirements_to_install:
        print(f"Fehlende Pakete werden installiert: {', '.join(requirements_to_install)}")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install"] + requirements_to_install)
            print("\nAlle Abhängigkeiten wurden erfolgreich installiert.")
        except subprocess.CalledProcessError:
            print("\nFehler bei der Installation der Abhängigkeiten.")
            sys.exit(1)
    else:
        print("Alle Abhängigkeiten sind bereits erfüllt.")

if __name__ == "__main__":
    check_and_install_requirements()