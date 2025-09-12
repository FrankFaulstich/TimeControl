import json
import os

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
                    # Handle case where file is empty or not valid JSON
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
        Ruft nach dem Hinzufügen automatisch _save_data() auf.
        """
        # Prüfen, ob das Hauptprojekt bereits existiert
        for project in self.data.get("projects", []):
            if project.get("main_project_name") == main_project_name:
                print(f"Hauptprojekt '{main_project_name}' existiert bereits.")
                return

        new_project = {
            "main_project_name": main_project_name,
            "sub_projects": []
        }
        self.data.setdefault("projects", []).append(new_project)
        self._save_data()
        print(f"Hauptprojekt '{main_project_name}' wurde hinzugefügt.")

    def list_main_projects(self):
        """
        Listet alle vorhandenen Hauptprojekte auf.
        Gibt eine Liste der Projektnamen zurück oder eine Meldung, wenn keine Projekte vorhanden sind.
        """
        projects = self.data.get("projects", [])
        if not projects:
            print("Es sind keine Hauptprojekte vorhanden.")
            return []
        
        project_names = [project.get("main_project_name") for project in projects]
        print("Vorhandene Hauptprojekte:")
        for name in project_names:
            print(f"- {name}")
        return project_names

    def delete_main_project(self, main_project_name):
        """
        Löscht ein Hauptprojekt anhand seines Namens.
        Ruft nach dem Löschen automatisch _save_data() auf.
        """
        projects = self.data.get("projects", [])
        initial_count = len(projects)
        
        # Erstellt eine neue Liste, die das zu löschende Projekt nicht enthält
        self.data["projects"] = [
            project for project in projects 
            if project.get("main_project_name") != main_project_name
        ]
        
        if len(self.data["projects"]) < initial_count:
            self._save_data()
            print(f"Hauptprojekt '{main_project_name}' wurde gelöscht.")
        else:
            print(f"Hauptprojekt '{main_project_name}' wurde nicht gefunden.")
    