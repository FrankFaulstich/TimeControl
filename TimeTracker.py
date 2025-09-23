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
        for project in reversed(self.data["projects"]):
            for sub_project in reversed(project["sub_projects"]):
                if sub_project["time_entries"] and "end_time" not in sub_project["time_entries"][-1]:
                    sub_project["time_entries"][-1]["end_time"] = datetime.now().isoformat()
                    self._save_data()
                    return True
        return False

    def generate_daily_report(self):
        report = []
        today = datetime.now().date()
        total_daily_time = timedelta()

        for project in self.data["projects"]:
            main_project_total_time = timedelta()
            sub_project_details = []

            for sub_project in project["sub_projects"]:
                sub_project_total_time = timedelta()
                
                for entry in sub_project["time_entries"]:
                    try:
                        start_time = datetime.fromisoformat(entry["start_time"])
                        if "end_time" in entry:
                            end_time = datetime.fromisoformat(entry["end_time"])
                            # Check if the entry is from today
                            if start_time.date() == today:
                                duration = end_time - start_time
                                sub_project_total_time += duration
                    except (ValueError, KeyError):
                        continue

                # Add to report only if time was tracked for this sub-project today
                if sub_project_total_time.total_seconds() > 0:
                    hours = sub_project_total_time.total_seconds() / 3600
                    sub_project_details.append(f"- {sub_project['sub_project_name']}: {hours:.3f} hours")
                    main_project_total_time += sub_project_total_time

            # Add main project and its sub-projects to report if it has entries for today
            if main_project_total_time.total_seconds() > 0:
                total_daily_time += main_project_total_time
                hours = main_project_total_time.total_seconds() / 3600
                report.append(f"## {project['main_project_name']} ({hours:.3f} hours)\n")
                report.extend(sub_project_details)
                report.append("\n")
        
        # Add total daily time to the report
        if total_daily_time.total_seconds() > 0:
            total_hours = total_daily_time.total_seconds() / 3600
            report.insert(0, f"# Daily Time Report: {today.strftime('%Y-%m-%d')}\n")
            report.append(f"\n**Total Daily Time: {total_hours:.3f} hours**")
            report.append("\nGenerated by TimeControl")
            report.append("https://github.com/frankfaulstich/TimeControl")
            return "\n".join(report)
        else:
            report.append(f"No time tracked for {today.strftime('%Y-%m-%d')}.")
        
        return "\n".join(report)