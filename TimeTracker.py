import json
import os
from datetime import datetime

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
        # Beende zuerst ein eventuell noch offenes Projekt
        self.stop_work()
        
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