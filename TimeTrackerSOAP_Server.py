import json
import os
import sys
import logging
from wsgiref.simple_server import make_server
from datetime import datetime

# Versuch, Spyne zu importieren. Dies ist die Standard-Bibliothek für SOAP in Python.
try:
    from spyne import Application, rpc, ServiceBase, Integer, Unicode, Boolean, Array, ComplexModel
    from spyne.protocol.soap import Soap11
    from spyne.server.wsgi import WsgiApplication
except ImportError:
    print("Fehler: Die benötigten Bibliotheken sind nicht installiert.")
    print("Bitte führen Sie folgenden Befehl aus: pip install spyne lxml")
    sys.exit(1)

# Import der TimeTracker Logik
# Wir fügen das aktuelle Verzeichnis zum Pfad hinzu, damit tt.TimeTracker gefunden wird
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
try:
    from tt.TimeTracker import TimeTracker
except ImportError as e:
    print(f"Fehler beim Importieren von TimeTracker: {e}")
    sys.exit(1)

CONFIG_FILE = 'config.json'

# --- Datenmodelle für SOAP Antworten ---

class MainProjectModel(ComplexModel):
    main_project_name = Unicode
    status = Unicode

class SubProjectModel(ComplexModel):
    main_project_name = Unicode
    sub_project_name = Unicode
    status = Unicode
    due_date = Unicode(min_occurs=0, nillable=True)
    today = Boolean
    note = Unicode

class InactiveProjectModel(ComplexModel):
    main_project = Unicode
    sub_project = Unicode(min_occurs=0, nillable=True)
    last_activity = Unicode

class CurrentWorkModel(ComplexModel):
    main_project_name = Unicode
    sub_project_name = Unicode
    start_time = Unicode

class OperationResultModel(ComplexModel):
    success = Boolean
    message = Unicode

# --- Der SOAP Service ---

class TimeControlService(ServiceBase):
    # Wir initialisieren den Tracker in der Instanz.
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.tracker = TimeTracker()

    @rpc(_returns=Unicode)
    def get_version(ctx):
        return ctx.service.tracker.get_version()

    # --- Main Project Management ---

    @rpc(Unicode, _returns=Boolean)
    def add_main_project(ctx, main_project_name):
        ctx.service.tracker.add_main_project(main_project_name)
        return True

    @rpc(Unicode, _returns=Array(MainProjectModel))
    def list_main_projects(ctx, status_filter='all'):
        projects = ctx.service.tracker.list_main_projects(status_filter)
        return [MainProjectModel(**p) for p in projects]

    @rpc(Unicode, _returns=Boolean)
    def delete_main_project(ctx, main_project_name):
        return ctx.service.tracker.delete_main_project(main_project_name)

    @rpc(Unicode, Unicode, _returns=Boolean)
    def rename_main_project(ctx, old_name, new_name):
        return ctx.service.tracker.rename_main_project(old_name, new_name)

    @rpc(Unicode, _returns=Boolean)
    def close_main_project(ctx, main_project_name):
        return ctx.service.tracker.close_main_project(main_project_name)

    @rpc(Unicode, _returns=Boolean)
    def reopen_main_project(ctx, main_project_name):
        return ctx.service.tracker.reopen_main_project(main_project_name)

    @rpc(Unicode, Unicode, _returns=OperationResultModel)
    def demote_main_project(ctx, main_project_to_demote, new_parent):
        success, msg = ctx.service.tracker.demote_main_project(main_project_to_demote, new_parent)
        return OperationResultModel(success=success, message=msg)

    @rpc(_returns=Array(Unicode))
    def list_completed_main_projects(ctx):
        return ctx.service.tracker.list_completed_main_projects()

    # --- Sub Project Management ---

    @rpc(Unicode, Unicode, _returns=Boolean)
    def add_sub_project(ctx, main_project_name, sub_project_name, due_date=None, today=False, note=""):
        return ctx.service.tracker.add_sub_project(main_project_name, sub_project_name, due_date, today, note)

    @rpc(Unicode, Unicode, _returns=Array(SubProjectModel))
    def list_sub_projects(ctx, main_project_name=None, status_filter='all'):
        projects = ctx.service.tracker.list_sub_projects(main_project_name, status_filter)
        return [SubProjectModel(**p) for p in projects]

    @rpc(Unicode, Unicode, _returns=Boolean)
    def delete_sub_project(ctx, main_project_name, sub_project_name):
        return ctx.service.tracker.delete_sub_project(main_project_name, sub_project_name)

    @rpc(Unicode, Unicode, _returns=Boolean)
    def close_sub_project(ctx, main_project_name, sub_project_name):
        return ctx.service.tracker.close_sub_project(main_project_name, sub_project_name)

    @rpc(Unicode, Unicode, _returns=Boolean)
    def reopen_sub_project(ctx, main_project_name, sub_project_name):
        return ctx.service.tracker.reopen_sub_project(main_project_name, sub_project_name)

    @rpc(Unicode, Unicode, Unicode, _returns=Boolean)
    def rename_sub_project(ctx, main_project_name, old_name, new_name):
        return ctx.service.tracker.rename_sub_project(main_project_name, old_name, new_name)

    @rpc(Unicode, Unicode, Unicode, Unicode, Boolean, Unicode, _returns=Boolean)
    def update_sub_project(ctx, main_project_name, old_name, new_name=None, due_date=None, today=None, note=None):
        return ctx.service.tracker.update_sub_project(main_project_name, old_name, new_name, due_date, today, note)

    @rpc(Unicode, Unicode, Unicode, _returns=OperationResultModel)
    def move_sub_project(ctx, old_main, sub_name, new_main):
        success, msg = ctx.service.tracker.move_sub_project(old_main, sub_name, new_main)
        return OperationResultModel(success=success, message=msg)

    @rpc(Unicode, Unicode, _returns=OperationResultModel)
    def promote_sub_project(ctx, main_project_name, sub_project_name):
        success, msg = ctx.service.tracker.promote_sub_project(main_project_name, sub_project_name)
        return OperationResultModel(success=success, message=msg)

    @rpc(_returns=Integer)
    def delete_all_closed_sub_projects(ctx):
        return ctx.service.tracker.delete_all_closed_sub_projects()

    @rpc(Integer, _returns=Array(InactiveProjectModel))
    def list_inactive_sub_projects(ctx, inactive_weeks):
        res = ctx.service.tracker.list_inactive_sub_projects(inactive_weeks)
        return [InactiveProjectModel(**p) for p in res]

    @rpc(Integer, _returns=Array(InactiveProjectModel))
    def list_inactive_main_projects(ctx, inactive_weeks):
        res = ctx.service.tracker.list_inactive_main_projects(inactive_weeks)
        # list_inactive_main_projects returns keys 'main_project' and 'last_activity'
        return [InactiveProjectModel(**p) for p in res]

    # --- Work / Time Tracking ---

    @rpc(Unicode, Unicode, _returns=Boolean)
    def start_work(ctx, main_project_name, sub_project_name):
        return ctx.service.tracker.start_work(main_project_name, sub_project_name)

    @rpc(_returns=Boolean)
    def stop_work(ctx):
        return ctx.service.tracker.stop_work()

    @rpc(_returns=CurrentWorkModel)
    def get_current_work(ctx):
        work = ctx.service.tracker.get_current_work()
        if work:
            return CurrentWorkModel(**work)
        return None

    # --- Reporting ---

    @rpc(Unicode, _returns=Unicode)
    def generate_daily_report(ctx, report_date_str=None):
        """Generiert den Tagesbericht. Datum Format: YYYY-MM-DD oder leer für Heute."""
        date_obj = None
        if report_date_str:
            try:
                date_obj = datetime.strptime(report_date_str, "%Y-%m-%d").date()
            except ValueError:
                return "Fehler: Datum muss im Format YYYY-MM-DD sein."
        return ctx.service.tracker.generate_daily_report(date_obj)

    @rpc(Unicode, _returns=Unicode)
    def generate_detailed_daily_report(ctx, report_date_str=None):
        """Generiert den detaillierten Tagesbericht. Datum Format: YYYY-MM-DD oder leer für Heute."""
        date_obj = None
        if report_date_str:
            try:
                date_obj = datetime.strptime(report_date_str, "%Y-%m-%d").date()
            except ValueError:
                return "Fehler: Datum muss im Format YYYY-MM-DD sein."
        return ctx.service.tracker.generate_detailed_daily_report(date_obj)

    @rpc(Unicode, Unicode, _returns=Unicode)
    def generate_date_range_report(ctx, start_date_str, end_date_str):
        """Generiert Bericht für Zeitraum. Datum Format: YYYY-MM-DD."""
        try:
            start = datetime.strptime(start_date_str, "%Y-%m-%d").date()
            end = datetime.strptime(end_date_str, "%Y-%m-%d").date()
            return ctx.service.tracker.generate_date_range_report(start, end)
        except ValueError:
            return "Fehler: Datum muss im Format YYYY-MM-DD sein."

    @rpc(Unicode, Unicode, _returns=Unicode)
    def generate_sub_project_report(ctx, main_project_name, sub_project_name):
        return ctx.service.tracker.generate_sub_project_report(main_project_name, sub_project_name)

    @rpc(Unicode, _returns=Unicode)
    def generate_main_project_report(ctx, main_project_name):
        return ctx.service.tracker.generate_main_project_report(main_project_name)

def load_config():
    """Lädt die Konfiguration aus der config.json Datei."""
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    return {}

def main():
    # Konfiguration laden
    config = load_config()
    port = config.get('soap_port', 8600)

    # Logging für Debugging-Zwecke aktivieren
    logging.basicConfig(level=logging.INFO)
    logging.getLogger('spyne.protocol.xml').setLevel(logging.INFO)

    # Definition der SOAP-Anwendung
    application = Application(
        [TimeControlService],
        tns='spyne.examples.timecontrol',
        in_protocol=Soap11(validator='lxml'),
        out_protocol=Soap11()
    )

    wsgi_application = WsgiApplication(application)

    print(f"Starte SOAP Server auf Port {port}...")
    print(f"WSDL ist verfügbar unter: http://localhost:{port}/?wsdl")
    
    server = make_server('0.0.0.0', port, wsgi_application)
    server.serve_forever()

if __name__ == '__main__':
    main()