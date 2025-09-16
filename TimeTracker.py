import json
import os
from datetime import datetime, timedelta

class TimeTracker:
    def __init__(self, file_path='data.json'):
        self.file_path = file_path
        self.data = self._load_data()

    def _load_data(self):
        if os.path.exists(self.file_path):
            with open(self.file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        else:
            return {"projects": []}

    def _save_data(self):
        with open(self.file_path, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, indent=2)

    def add_main_project(self, main_project_name):
        new_project = {
            "main_project_name": main_project_name,
            "sub_projects": []
        }
        self.data["projects"].append(new_project)
        self._save_data()

    def list_main_projects(self):
        return [project["main_project_name"] for project in self.data["projects"]]

    def delete_main_project(self, main_project_name):
        initial_count = len(self.data["projects"])
        self.data["projects"] = [
            project for project in self.data["projects"] if project["main_project_name"] != main_project_name
        ]
        if len(self.data["projects"]) < initial_count:
            self._save_data()
            return True
        return False

    def add_sub_project(self, main_project_name, sub_project_name):
        for project in self.data["projects"]:
            if project["main_project_name"] == main_project_name:
                new_sub_project = {
                    "sub_project_name": sub_project_name,
                    "time_entries": []
                }
                project["sub_projects"].append(new_sub_project)
                self._save_data()
                return True
        return False

    def list_sub_projects(self, main_project_name):
        for project in self.data["projects"]:
            if project["main_project_name"] == main_project_name:
                return [sub_project["sub_project_name"] for sub_project in project["sub_projects"]]
        return None

    def delete_sub_project(self, main_project_name, sub_project_name):
        for project in self.data["projects"]:
            if project["main_project_name"] == main_project_name:
                initial_count = len(project["sub_projects"])
                project["sub_projects"] = [
                    sp for sp in project["sub_projects"] if sp["sub_project_name"] != sub_project_name
                ]
                if len(project["sub_projects"]) < initial_count:
                    self._save_data()
                    return True
        return False

    def start_work(self, main_project_name, sub_project_name):
        """
        Startet die Arbeit an einem Unterprojekt. Beendet automatisch
        ein vorheriges, falls es noch offen ist.
        """
        self.stop_work() # Stoppt vorherige Arbeit, falls vorhanden
        
        for project in self.data["projects"]:
            if project["main_project_name"] == main_project_name:
                for sub_project in project["sub_projects"]:
                    if sub_project["sub_project_name"] == sub_project_name:
                        new_entry = {
                            "start_time": datetime.now().isoformat()
                        }
                        sub_project["time_entries"].append(new_entry)
                        self._save_data()
                        return True
        return False

    def stop_work(self):
        """
        Beendet die Arbeit am zuletzt gestarteten Subprojekt, indem die Endzeit hinzugefügt wird.
        """
        for project in reversed(self.data["projects"]):
            for sub_project in reversed(project["sub_projects"]):
                if sub_project["time_entries"] and "end_time" not in sub_project["time_entries"][-1]:
                    sub_project["time_entries"][-1]["end_time"] = datetime.now().isoformat()
                    self._save_data()
                    return True
        return False

    def generate_daily_report(self):
        """
        Generiert einen täglichen Bericht im Markdown-Format.
        Listet Hauptprojekte und die zugehörigen Unterprojekte auf, an denen gearbeitet wurde.
        Zeigt die benötigte Zeit pro Unterprojekt an.
        """
        report = "# Daily Report\n\n"
        today = datetime.now().date()
        total_project_time = timedelta()

        projects_with_work = {}

        for project in self.data["projects"]:
            main_project_name = project["main_project_name"]
            main_project_total_time = timedelta()
            sub_projects_with_time = []

            for sub_project in project["sub_projects"]:
                sub_project_name = sub_project["sub_project_name"]
                current_sub_project_time = timedelta()

                for entry in sub_project["time_entries"]:
                    start_time_str = entry.get("start_time")
                    end_time_str = entry.get("end_time")

                    if start_time_str:
                        start_time = datetime.fromisoformat(start_time_str)
                        # Überprüfe, ob der Eintrag für den heutigen Tag ist
                        if start_time.date() == today:
                            if end_time_str:
                                end_time = datetime.fromisoformat(end_time_str)
                            else:
                                # Wenn kein Enddatum vorhanden, nehme aktuelle Zeit als Ende
                                end_time = datetime.now() 
                            
                            duration = end_time - start_time
                            current_sub_project_time += duration
                            main_project_total_time += duration
                            total_project_time += duration
                
                # Nur Unterprojekte auflisten, wenn gearbeitet wurde
                if current_sub_project_time > timedelta():
                    hours = current_sub_project_time.total_seconds() / 3600
                    sub_projects_with_time.append(
                        f"- {sub_project_name}: {hours:.3f} hours"
                    )
            
            # Nur Hauptprojekte auflisten, wenn Unterprojekte mit Zeit gefunden wurden
            if sub_projects_with_time:
                projects_with_work[main_project_name] = sub_projects_with_time

        if not projects_with_work:
            report += "No work recorded for today.\n"
        else:
            for main_project_name, sub_entries in projects_with_work.items():
                report += f"## {main_project_name}\n"
                report += "\n".join(sub_entries) + "\n\n"

        return report