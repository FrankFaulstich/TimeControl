import json
import os
from datetime import datetime

class TimeTracker:
    def __init__(self, file_path='data.json'):
        """
        Initialisiert die Klasse und lädt die Daten aus der JSON-Datei.
        Wenn die Datei nicht existiert, wird eine leere Struktur erstellt.
        """
        self.file_path = file_path
        self.data = self._load_data()

    def _load_data(self):
        """
        Liest die Daten aus der data.json-Datei.
        """
        if os.path.exists(self.file_path):
            with open(self.file_path, 'r', encoding='utf-8') as f:
                try:
                    return json.load(f)
                except json.JSONDecodeError:
                    # Falls die Datei leer oder beschädigt ist
                    return {"projects": []}
        else:
            return {"projects": []}

    def _save_data(self):
        """
        Speichert die aktuellen Daten in der data.json-Datei.
        """
        with open(self.file_path, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, indent=4, ensure_ascii=False)

    def add_main_project(self, main_project_name):
        """
        Fügt ein neues Hauptprojekt hinzu.
        """
        # Prüfen, ob das Projekt bereits existiert
        if any(p['main_project_name'] == main_project_name for p in self.data.get('projects', [])):
            print(f"Fehler: Hauptprojekt '{main_project_name}' existiert bereits.")
            return

        new_project = {
            "main_project_name": main_project_name,
            "sub_projects": []
        }
        self.data.get('projects', []).append(new_project)
        self._save_data()
        print(f"Hauptprojekt '{main_project_name}' erfolgreich hinzugefügt.")

    def list_main_projects(self):
        """
        Listet alle Hauptprojekte auf.
        Gibt eine Liste der Namen der Hauptprojekte zurück.
        """
        projects = self.data.get('projects', [])
        if not projects:
            return "Keine Hauptprojekte vorhanden."
        return [p['main_project_name'] for p in projects]

    def delete_main_project(self, main_project_name):
        """
        Löscht ein Hauptprojekt anhand seines Namens.
        """
        projects = self.data.get('projects', [])
        initial_count = len(projects)
        # Filtern, um das Projekt mit dem gegebenen Namen zu entfernen
        self.data['projects'] = [p for p in projects if p['main_project_name'] != main_project_name]
        
        if len(self.data['projects']) < initial_count:
            self._save_data()
            print(f"Hauptprojekt '{main_project_name}' erfolgreich gelöscht.")
        else:
            print(f"Fehler: Hauptprojekt '{main_project_name}' nicht gefunden.")

    # --- Methoden für Unterprojekte ---

    def add_sub_project(self, main_project_name, sub_project_name):
        """
        Fügt ein neues Unterprojekt zu einem bestehenden Hauptprojekt hinzu.
        """
        for project in self.data.get('projects', []):
            if project['main_project_name'] == main_project_name:
                # Prüfen, ob das Unterprojekt bereits existiert
                if any(sp['sub_project_name'] == sub_project_name for sp in project.get('sub_projects', [])):
                    print(f"Fehler: Unterprojekt '{sub_project_name}' existiert bereits in '{main_project_name}'.")
                    return

                new_sub_project = {
                    "sub_project_name": sub_project_name,
                    "time_entries": []
                }
                project.get('sub_projects', []).append(new_sub_project)
                self._save_data()
                print(f"Unterprojekt '{sub_project_name}' erfolgreich zu '{main_project_name}' hinzugefügt.")
                return
        print(f"Fehler: Hauptprojekt '{main_project_name}' nicht gefunden.")

    def list_sub_projects(self, main_project_name):
        """
        Listet alle Unterprojekte eines bestimmten Hauptprojekts auf.
        """
        for project in self.data.get('projects', []):
            if project['main_project_name'] == main_project_name:
                sub_projects = project.get('sub_projects', [])
                if not sub_projects:
                    return f"Keine Unterprojekte für '{main_project_name}' vorhanden."
                return [sp['sub_project_name'] for sp in sub_projects]
        return f"Fehler: Hauptprojekt '{main_project_name}' nicht gefunden."

    def delete_sub_project(self, main_project_name, sub_project_name):
        """
        Löscht ein Unterprojekt aus einem bestimmten Hauptprojekt.
        """
        for project in self.data.get('projects', []):
            if project['main_project_name'] == main_project_name:
                sub_projects = project.get('sub_projects', [])
                initial_count = len(sub_projects)
                project['sub_projects'] = [sp for sp in sub_projects if sp['sub_project_name'] != sub_project_name]

                if len(project['sub_projects']) < initial_count:
                    self._save_data()
                    print(f"Unterprojekt '{sub_project_name}' erfolgreich aus '{main_project_name}' gelöscht.")
                else:
                    print(f"Fehler: Unterprojekt '{sub_project_name}' nicht in '{main_project_name}' gefunden.")
                return
        print(f"Fehler: Hauptprojekt '{main_project_name}' nicht gefunden.")

    # --- Methode zum Erfassen von Zeiten ---

    def add_time_entry(self, main_project_name, sub_project_name, start_time_str, end_time_str):
        """
        Fügt einen Zeiteintrag zu einem Unterprojekt hinzu.
        Zeiten werden als ISO 8601 Strings erwartet.
        """
        try:
            # Versuchen, die Strings in datetime-Objekte zu parsen, um das Format zu validieren
            start_time = datetime.fromisoformat(start_time_str)
            end_time = datetime.fromisoformat(end_time_str)
            if start_time >= end_time:
                print("Fehler: Startzeit muss vor Endzeit liegen.")
                return
        except ValueError:
            print("Fehler: Ungültiges Zeitformat. Bitte verwenden Sie das ISO 8601 Format (z.B. YYYY-MM-DDTHH:MM:SS).")
            return

        for project in self.data.get('projects', []):
            if project['main_project_name'] == main_project_name:
                for sub_project in project.get('sub_projects', []):
                    if sub_project['sub_project_name'] == sub_project_name:
                        time_entry = {
                            "start_time": start_time_str,
                            "end_time": end_time_str
                        }
                        sub_project.get('time_entries', []).append(time_entry)
                        self._save_data()
                        print(f"Zeiteintrag für '{sub_project_name}' erfolgreich hinzugefügt.")
                        return
                print(f"Fehler: Unterprojekt '{sub_project_name}' nicht in '{main_project_name}' gefunden.")
                return
        print(f"Fehler: Hauptprojekt '{main_project_name}' nicht gefunden.")

    def list_time_entries(self, main_project_name, sub_project_name):
        """
        Listet alle Zeiteinträge für ein bestimmtes Unterprojekt auf.
        """
        for project in self.data.get('projects', []):
            if project['main_project_name'] == main_project_name:
                for sub_project in project.get('sub_projects', []):
                    if sub_project['sub_project_name'] == sub_project_name:
                        time_entries = sub_project.get('time_entries', [])
                        if not time_entries:
                            return f"Keine Zeiteinträge für '{sub_project_name}' in '{main_project_name}' vorhanden."
                        return time_entries
                print(f"Fehler: Unterprojekt '{sub_project_name}' nicht in '{main_project_name}' gefunden.")
                return
        print(f"Fehler: Hauptprojekt '{main_project_name}' nicht gefunden.")
    