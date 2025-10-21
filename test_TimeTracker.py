import unittest
import json
import os
from datetime import datetime, timedelta
from TimeTracker import TimeTracker # Gehe davon aus, dass TimeTracker.py im selben Verzeichnis liegt

# Der temporäre Dateipfad für Tests
TEST_FILE_PATH = 'test_data.json'

class TestTimeTracker(unittest.TestCase):
    """Unit tests for the TimeTracker class."""

    def setUp(self):
        """Wird vor jedem Test ausgeführt, um eine saubere TimeTracker-Instanz zu erstellen."""
        self.tracker = TimeTracker(file_path=TEST_FILE_PATH)
        # Stellt sicher, dass die Datei vor dem Test nicht existiert
        if os.path.exists(TEST_FILE_PATH):
            os.remove(TEST_FILE_PATH)
        self.tracker = TimeTracker(file_path=TEST_FILE_PATH)
        
    def tearDown(self):
        """Wird nach jedem Test ausgeführt, um die temporäre Datei zu löschen."""
        if os.path.exists(TEST_FILE_PATH):
            os.remove(TEST_FILE_PATH)

    # --- Hilfsmethoden Tests (Private Methods) ---

    def test_load_data_initial_empty(self):
        """Testet, ob _load_data ein leeres Dictionary zurückgibt, wenn keine Datei existiert."""
        self.assertEqual(self.tracker.data, {"projects": []})

    def test_save_and_load_data(self):
        """Testet das Zusammenspiel von _save_data und _load_data."""
        # Manuelle Änderung der Daten und Speichern
        self.tracker.data["projects"].append({"main_project_name": "Test"})
        self.tracker._save_data()
        
        # Erneutes Laden der Daten in einer neuen Instanz
        new_tracker = TimeTracker(file_path=TEST_FILE_PATH)
        self.assertEqual(len(new_tracker.data["projects"]), 1)
        self.assertEqual(new_tracker.data["projects"][0]["main_project_name"], "Test")
        
    # --- Hauptprojekt-Methoden Tests ---

    def test_add_main_project(self):
        """Testet das Hinzufügen eines Hauptprojekts."""
        self.tracker.add_main_project("Project A")
        self.assertEqual(len(self.tracker.data["projects"]), 1)
        self.assertEqual(self.tracker.data["projects"][0]["main_project_name"], "Project A")
        
    def test_list_main_projects(self):
        """Testet das Auflisten von Hauptprojekten."""
        self.tracker.add_main_project("Project Alpha")
        self.tracker.add_main_project("Project Beta")
        projects = self.tracker.list_main_projects()
        self.assertEqual(projects, ["Project Alpha", "Project Beta"])
        
    def test_delete_main_project_success(self):
        """Testet das erfolgreiche Löschen eines Hauptprojekts."""
        self.tracker.add_main_project("To Delete")
        self.tracker.add_main_project("To Keep")
        success = self.tracker.delete_main_project("To Delete")
        self.assertTrue(success)
        self.assertEqual(self.tracker.list_main_projects(), ["To Keep"])

    def test_delete_main_project_not_found(self):
        """Testet das Löschen eines nicht existierenden Hauptprojekts."""
        self.tracker.add_main_project("Project 1")
        success = self.tracker.delete_main_project("Non Existent")
        self.assertFalse(success)
        self.assertEqual(len(self.tracker.data["projects"]), 1)

    # --- Unterprojekt-Methoden Tests ---

    def test_add_sub_project_success(self):
        """Testet das erfolgreiche Hinzufügen eines Unterprojekts."""
        self.tracker.add_main_project("Main Test")
        success = self.tracker.add_sub_project("Main Test", "Sub Task 1")
        self.assertTrue(success)
        sub_projects = self.tracker.data["projects"][0]["sub_projects"]
        self.assertEqual(len(sub_projects), 1)
        self.assertEqual(sub_projects[0]["sub_project_name"], "Sub Task 1")

    def test_add_sub_project_main_not_found(self):
        """Testet das Hinzufügen eines Unterprojekts zu einem nicht existierenden Hauptprojekt."""
        success = self.tracker.add_sub_project("Non Existent", "Sub Task 1")
        self.assertFalse(success)

    def test_list_sub_projects(self):
        """Testet das Auflisten von Unterprojekten."""
        self.tracker.add_main_project("Main List")
        self.tracker.add_sub_project("Main List", "Sub A")
        self.tracker.add_sub_project("Main List", "Sub B")
        subs = self.tracker.list_sub_projects("Main List")
        self.assertEqual(subs, ["Sub A", "Sub B"])
        self.assertIsNone(self.tracker.list_sub_projects("Unknown Project"))

    def test_delete_sub_project_success(self):
        """Testet das erfolgreiche Löschen eines Unterprojekts."""
        self.tracker.add_main_project("Main Test")
        self.tracker.add_sub_project("Main Test", "Sub To Delete")
        self.tracker.add_sub_project("Main Test", "Sub To Keep")
        
        success = self.tracker.delete_sub_project("Main Test", "Sub To Delete")
        self.assertTrue(success)
        self.assertEqual(self.tracker.list_sub_projects("Main Test"), ["Sub To Keep"])

    def test_delete_sub_project_not_found(self):
        """Testet das Löschen eines nicht existierenden Unterprojekts."""
        self.tracker.add_main_project("Main Test")
        self.tracker.add_sub_project("Main Test", "Sub 1")
        
        success = self.tracker.delete_sub_project("Main Test", "Non Existent Sub")
        self.assertFalse(success)
        self.assertEqual(len(self.tracker.list_sub_projects("Main Test")), 1)

    def test_rename_sub_project_success(self):
        """Testet das erfolgreiche Umbenennen eines Unterprojekts."""
        self.tracker.add_main_project("Main")
        self.tracker.add_sub_project("Main", "Old Name")
        success = self.tracker.rename_sub_project("Main", "Old Name", "New Name")
        self.assertTrue(success)
        self.assertEqual(self.tracker.list_sub_projects("Main"), ["New Name"])

    def test_rename_sub_project_main_not_found(self):
        """Testet das Umbenennen, wenn das Hauptprojekt nicht existiert."""
        self.assertFalse(self.tracker.rename_sub_project("Non-Existent", "Old", "New"))

    def test_rename_sub_project_sub_not_found(self):
        """Testet das Umbenennen, wenn das Unterprojekt nicht existiert."""
        self.tracker.add_main_project("Main")
        self.assertFalse(self.tracker.rename_sub_project("Main", "Non-Existent", "New"))

    def test_rename_sub_project_new_name_exists(self):
        """Testet, dass ein Umbenennen fehlschlägt, wenn der neue Name bereits existiert."""
        self.tracker.add_main_project("Main")
        self.tracker.add_sub_project("Main", "Sub A")
        self.tracker.add_sub_project("Main", "Sub B")
        # Versuch, "Sub A" in "Sub B" umzubenennen
        success = self.tracker.rename_sub_project("Main", "Sub A", "Sub B")
        self.assertFalse(success)
        self.assertEqual(self.tracker.list_sub_projects("Main"), ["Sub A", "Sub B"])
        
    # --- Zeiterfassungs-Methoden Tests ---
    
    def _create_mock_project_with_sub(self, main_name, sub_name):
        """Hilfsfunktion zum Einrichten eines Projekts für Zeiterfassungstests."""
        self.tracker.add_main_project(main_name)
        self.tracker.add_sub_project(main_name, sub_name)

    def test_start_work_success(self):
        """Testet das erfolgreiche Starten einer Arbeitssitzung."""
        self._create_mock_project_with_sub("Work Test", "Task X")
        success = self.tracker.start_work("Work Test", "Task X")
        self.assertTrue(success)
        
        entries = self.tracker.data["projects"][0]["sub_projects"][0]["time_entries"]
        self.assertEqual(len(entries), 1)
        self.assertIn("start_time", entries[0])
        self.assertNotIn("end_time", entries[0])
        
    def test_start_work_main_not_found(self):
        """Testet Starten der Arbeit bei nicht existierendem Hauptprojekt."""
        self.assertFalse(self.tracker.start_work("Non Existent", "Task"))
        
    def test_start_work_stops_previous(self):
        """Testet, ob eine laufende Sitzung gestoppt wird, wenn eine neue gestartet wird."""
        self._create_mock_project_with_sub("P1", "T1")
        self._create_mock_project_with_sub("P2", "T2") # Führt zu index 1
        
        # Start T1
        self.tracker.start_work("P1", "T1")
        # Start T2 (sollte T1 stoppen)
        self.tracker.start_work("P2", "T2")
        
        # Prüfen, ob T1 einen end_time Eintrag hat
        t1_entry = self.tracker.data["projects"][0]["sub_projects"][0]["time_entries"][0]
        self.assertIn("end_time", t1_entry)
        
        # Prüfen, ob T2 nur start_time hat
        t2_entry = self.tracker.data["projects"][1]["sub_projects"][0]["time_entries"][0]
        self.assertNotIn("end_time", t2_entry)

    def test_stop_work_success(self):
        """Testet das erfolgreiche Stoppen der Arbeit."""
        self._create_mock_project_with_sub("P1", "T1")
        self.tracker.start_work("P1", "T1")
        
        success = self.tracker.stop_work()
        self.assertTrue(success)
        
        entry = self.tracker.data["projects"][0]["sub_projects"][0]["time_entries"][0]
        self.assertIn("end_time", entry)
        
    def test_stop_work_no_active_session(self):
        """Testet das Stoppen, wenn keine aktive Sitzung läuft."""
        self._create_mock_project_with_sub("P1", "T1")
        
        # Stoppen, ohne vorher gestartet zu haben
        success = self.tracker.stop_work()
        self.assertFalse(success)
        
        # Starten und Stoppen
        self.tracker.start_work("P1", "T1")
        self.tracker.stop_work()
        
        # Erneut stoppen
        success = self.tracker.stop_work()
        self.assertFalse(success)
        
    # --- Inaktivitäts-Methode Test ---

    def test_list_inactive_sub_projects(self):
        """Testet das Auflisten inaktiver Unterprojekte."""
        now = datetime.now()
        
        # P1: Aktives Projekt (vor 1 Woche gestartet, vor 1 Tag gestoppt) -> sollte NICHT gelistet werden
        self._create_mock_project_with_sub("P1_Active", "T1_Recent")
        self.tracker.data["projects"][0]["sub_projects"][0]["time_entries"].append({
            "start_time": (now - timedelta(weeks=1)).isoformat(),
            "end_time": (now - timedelta(days=1)).isoformat()
        })

        # P2: Inaktives Projekt (vor 5 Wochen gestoppt) -> sollte gelistet werden (bei 4 Wochen Schwelle)
        self._create_mock_project_with_sub("P2_Inactive", "T2_Old")
        self.tracker.data["projects"][1]["sub_projects"][0]["time_entries"].append({
            "start_time": (now - timedelta(weeks=5, days=1)).isoformat(),
            "end_time": (now - timedelta(weeks=5)).isoformat()
        })

        # P3: Laufendes Projekt (sollte ignoriert werden)
        self._create_mock_project_with_sub("P3_Running", "T3_Open")
        self.tracker.data["projects"][2]["sub_projects"][0]["time_entries"].append({
            "start_time": (now - timedelta(days=1)).isoformat()
        })
        
        # Speichern, um Datenkonsistenz sicherzustellen
        self.tracker._save_data()
        
        # Testen mit 4 Wochen Inaktivitätsschwelle
        inactive_list = self.tracker.list_inactive_sub_projects(inactive_weeks=4)
        
        # Erwartung: Nur P2_Inactive sollte gelistet werden
        self.assertEqual(len(inactive_list), 1)
        self.assertEqual(inactive_list[0]['sub_project'], "T2_Old")
        self.assertEqual(inactive_list[0]['main_project'], "P2_Inactive")
        
        # Testen mit 6 Wochen Inaktivitätsschwelle (sollte leer sein)
        inactive_list_6w = self.tracker.list_inactive_sub_projects(inactive_weeks=6)
        self.assertEqual(len(inactive_list_6w), 0)

    def test_list_inactive_main_projects(self):
        """Testet das Auflisten inaktiver Hauptprojekte."""
        now = datetime.now()
        
        # P1: Aktives Main-Projekt (letzte Aktivität vor 1 Tag) -> sollte NICHT gelistet werden
        self.tracker.add_main_project("P1_Active")
        self.tracker.add_sub_project("P1_Active", "T1_Recent")
        self.tracker.data["projects"][0]["sub_projects"][0]["time_entries"].append({
            "start_time": (now - timedelta(days=2)).isoformat(),
            "end_time": (now - timedelta(days=1)).isoformat()
        })

        # P2: Inaktives Main-Projekt (letzte Aktivität vor 5 Wochen) -> sollte gelistet werden (bei 4 Wochen Schwelle)
        self.tracker.add_main_project("P2_Inactive")
        self.tracker.add_sub_project("P2_Inactive", "T2_Old")
        self.tracker.data["projects"][1]["sub_projects"][0]["time_entries"].append({
            "start_time": (now - timedelta(weeks=5, days=1)).isoformat(),
            "end_time": (now - timedelta(weeks=5)).isoformat()
        })

        # P3: Laufendes Main-Projekt (enthält einen offenen Sub-Eintrag) -> sollte ignoriert werden
        self.tracker.add_main_project("P3_Running")
        self.tracker.add_sub_project("P3_Running", "T3_Open")
        self.tracker.data["projects"][2]["sub_projects"][0]["time_entries"].append({
            "start_time": (now - timedelta(days=1)).isoformat()
        })
        
        # P4: Leeres Main-Projekt (keine Aktivität) -> sollte ignoriert werden
        self.tracker.add_main_project("P4_Empty")

        # Speichern, um Datenkonsistenz sicherzustellen
        self.tracker._save_data()
        
        # Testen mit 4 Wochen Inaktivitätsschwelle
        inactive_list = self.tracker.list_inactive_main_projects(inactive_weeks=4)
        
        # Erwartung: Nur P2_Inactive sollte gelistet werden
        self.assertEqual(len(inactive_list), 1)
        self.assertEqual(inactive_list[0]['main_project'], "P2_Inactive")
        
        # Testen mit 6 Wochen Inaktivitätsschwelle (sollte leer sein)
        inactive_list_6w = self.tracker.list_inactive_main_projects(inactive_weeks=6)
        self.assertEqual(len(inactive_list_6w), 0)
    # --- Berichts-Methode Test ---

    def test_generate_daily_report_formatting(self):
        """Testet die Berichtsgenerierung, insbesondere die Komma-Formatierung und die Summierung."""
        
        now = datetime.now()
        today_date = now.date()
        
        # P1: 1.5 Stunden heute
        self._create_mock_project_with_sub("Report P1", "R_Sub1")
        self.tracker.data["projects"][0]["sub_projects"][0]["time_entries"].append({
            "start_time": now.replace(hour=8, minute=0, second=0, microsecond=0).isoformat(),
            "end_time": now.replace(hour=9, minute=30, second=0, microsecond=0).isoformat()
        })
        
        # P2: 2.0 Stunden heute
        self._create_mock_project_with_sub("Report P2", "R_Sub2")
        self.tracker.data["projects"][1]["sub_projects"][0]["time_entries"].append({
            "start_time": now.replace(hour=10, minute=0, second=0, microsecond=0).isoformat(),
            "end_time": now.replace(hour=12, minute=0, second=0, microsecond=0).isoformat()
        })
        
        # P3: Eintrag für gestern (sollte ignoriert werden)
        yesterday = now - timedelta(days=1)
        self._create_mock_project_with_sub("Report P3", "R_Sub3_Old")
        self.tracker.data["projects"][2]["sub_projects"][0]["time_entries"].append({
            "start_time": yesterday.replace(hour=8, minute=0, second=0, microsecond=0).isoformat(),
            "end_time": yesterday.replace(hour=9, minute=0, second=0, microsecond=0).isoformat()
        })
        
        report = self.tracker.generate_daily_report(today_date)
        
        # Erwartete Gesamtzeit: 1.5 + 2.0 = 3.5 Stunden
        
        # Prüfen auf korrekte Komma-Formatierung der Stunden
        self.assertIn("1,500 hours", report)
        self.assertIn("2,000 hours", report)
        self.assertIn("**Total Daily Time: 3,500 hours**", report)
        
        # Prüfen, ob P3 ignoriert wurde
        self.assertNotIn("R_Sub3_Old", report)
        
        # Prüfen auf die Gesamtstruktur
        self.assertTrue(report.startswith(f"# Daily Time Report: {today_date.strftime('%Y-%m-%d')}"))
        self.assertIn("## Report P1 (1,500 hours)", report)
        self.assertIn("## Report P2 (2,000 hours)", report)
        
    def test_generate_date_range_report(self):
        """Testet die Berichtsgenerierung für einen Datumsbereich."""
        
        # Setup: Erstelle Einträge an verschiedenen Tagen
        day1 = datetime(2025, 10, 20)
        day2 = datetime(2025, 10, 22)
        day_outside = datetime(2025, 10, 25)

        # P1: 1 Stunde an Tag 1
        self._create_mock_project_with_sub("Range P1", "R_Sub1")
        self.tracker.data["projects"][0]["sub_projects"][0]["time_entries"].append({
            "start_time": day1.replace(hour=9).isoformat(),
            "end_time": day1.replace(hour=10).isoformat()
        })
        
        # P2: 2 Stunden an Tag 2
        self._create_mock_project_with_sub("Range P2", "R_Sub2")
        self.tracker.data["projects"][1]["sub_projects"][0]["time_entries"].append({
            "start_time": day2.replace(hour=11).isoformat(),
            "end_time": day2.replace(hour=13).isoformat()
        })
        
        # P3: Eintrag außerhalb des Bereichs (sollte ignoriert werden)
        self._create_mock_project_with_sub("Range P3", "R_Sub3_Outside")
        self.tracker.data["projects"][2]["sub_projects"][0]["time_entries"].append({
            "start_time": day_outside.replace(hour=9).isoformat(),
            "end_time": day_outside.replace(hour=10).isoformat()
        })
        
        # Test: Generiere Bericht für den Bereich von Tag 1 bis Tag 2
        start_date = day1.date()
        end_date = day2.date()
        report = self.tracker.generate_date_range_report(start_date, end_date)
        
        # Erwartete Gesamtzeit: 1 + 2 = 3 Stunden
        # 1h = 0.025 DLP; 2h = 0.050 DLP; 3h = 0.075 DLP
        self.assertIn("## Range P1 (1,000 hours (0,025 DLP))", report)
        self.assertIn("## Range P2 (2,000 hours (0,050 DLP))", report)
        self.assertNotIn("Range P3", report)
        self.assertIn("**Total Time in Period: 3,000 hours (0,075 DLP)**", report)
        self.assertTrue(report.startswith(f"# Time Report: {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}"))

# Führt die Tests aus, wenn die Datei direkt aufgerufen wird
if __name__ == '__main__':
    unittest.main()