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
            try:
                with open(self.file_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except json.JSONDecodeError:
                # Falls die Datei leer oder beschädigt ist, starte mit leerer Struktur
                print(f"Warnung: Die Datei '{self.file_path}' ist leer oder beschädigt. Starte mit leerer Datenstruktur.")
                return {"projects": []}
        else:
            return {"projects": []}

    def _save_data(self):
        """
        Speichert die aktuellen Daten in der data.json-Datei.
        """
        with open(self.file_path, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, indent=4, ensure_ascii=False)

    # Hier werden später weitere Methoden hinzugefügt (z.B. für Projektverwaltung, Zeiterfassung)