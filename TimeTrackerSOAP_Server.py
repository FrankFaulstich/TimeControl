import json
import os
import sys
import logging
import types
import importlib.util
from wsgiref.simple_server import make_server
from datetime import datetime


def _patch_spyne_vendored_six():
    """
    spyne 2.14.0 (the only release on PyPI) vendors an old copy of `six`
    (spyne/util/six.py) whose meta path importer only implements the legacy
    PEP 302 find_module()/load_module() protocol. Python 3.12 dropped the
    compatibility shim that let the import system fall back to find_module()
    when find_spec() (PEP 451) is missing, so every `spyne.util.six.moves.*`
    import - including one hit while `spyne/__init__.py` itself is still
    running - fails with "ModuleNotFoundError: No module named
    'spyne.util.six.moves'". Upstream issue (open, unreleased as of
    2026-08): https://github.com/arskom/spyne/issues/711

    Fix: load spyne's vendored six.py directly under its real module name
    (without triggering the still-broken `import spyne`) and add a
    find_spec() to its meta path importer, mirroring the fix already present
    in the real `six` package (>=1.15). The importer instance stays
    registered in sys.meta_path for the rest of the process, so the normal
    `import spyne` below - and every later `spyne.util.six.moves` import
    inside spyne - resolves correctly.
    """
    if 'spyne.util.six' in sys.modules:
        return

    spyne_spec = importlib.util.find_spec('spyne')
    if spyne_spec is None or not spyne_spec.submodule_search_locations:
        return

    six_path = os.path.join(spyne_spec.submodule_search_locations[0], 'util', 'six.py')
    if not os.path.isfile(six_path):
        return

    six_spec = importlib.util.spec_from_file_location('spyne.util.six', six_path)
    six_module = importlib.util.module_from_spec(six_spec)
    sys.modules['spyne.util.six'] = six_module
    six_spec.loader.exec_module(six_module)

    importer = getattr(six_module, '_importer', None)
    if importer is not None and not hasattr(importer, 'find_spec'):
        def find_spec(self, fullname, path=None, target=None):
            if fullname in self.known_modules:
                return importlib.util.spec_from_loader(fullname, self)
            return None
        importer.find_spec = find_spec.__get__(importer)


def _patch_missing_cgi_module():
    """
    spyne 2.14.0's SOAP11 protocol and WSGI transport (both used by this
    server) still do `import cgi` to parse the Content-Type header. Python
    3.13 removed the `cgi` module from the standard library (PEP 594), so on
    3.13+ this raises "ModuleNotFoundError: No module named 'cgi'" - a
    second, independent break from the six.moves issue above. Spyne's master
    branch fixes this (unreleased) by parsing Content-Type via
    email.message.EmailMessage instead; provide a minimal `cgi` stand-in
    with the same fix so `import cgi` keeps working until spyne cuts a new
    release.
    """
    if 'cgi' in sys.modules or importlib.util.find_spec('cgi') is not None:
        return

    from email.message import EmailMessage

    def parse_header(line):
        msg = EmailMessage()
        msg['content-type'] = line
        return msg.get_content_type(), dict(msg['content-type'].params)

    cgi_stub = types.ModuleType('cgi')
    cgi_stub.parse_header = parse_header
    sys.modules['cgi'] = cgi_stub


_patch_spyne_vendored_six()
_patch_missing_cgi_module()

# Attempt to import Spyne. This is the standard library for SOAP in Python.
try:
    from spyne import Application, rpc, ServiceBase, Integer, Unicode, Boolean, Array, ComplexModel
    from spyne.protocol.soap import Soap11
    from spyne.server.wsgi import WsgiApplication
except ImportError:
    print("Fehler: Die benötigten Bibliotheken sind nicht installiert.")
    print("Bitte führen Sie folgenden Befehl aus: pip install spyne lxml")
    sys.exit(1)

# Import of TimeTracker logic
# We add the current directory to the path so that tt.TimeTracker can be found
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
try:
    from tt.TimeTracker import TimeTracker
except ImportError as e:
    print(f"Fehler beim Importieren von TimeTracker: {e}")
    sys.exit(1)

CONFIG_FILE = 'config.json'

# --- Data models for SOAP responses ---

class MainProjectModel(ComplexModel):
    main_project_name = Unicode
    status = Unicode

class TaskModel(ComplexModel):
    id = Integer
    main_project_name = Unicode
    task_name = Unicode
    status = Unicode
    due_date = Unicode(min_occurs=0, nillable=True)
    today = Boolean
    note = Unicode
    recurring = Boolean
    frequency = Unicode
    userdefined_days = Integer
    priority = Integer

class InactiveProjectModel(ComplexModel):
    main_project = Unicode
    task_name = Unicode(min_occurs=0, nillable=True)
    last_activity = Unicode

class CurrentWorkModel(ComplexModel):
    main_project_name = Unicode
    task_name = Unicode
    start_time = Unicode

class OperationResultModel(ComplexModel):
    success = Boolean
    message = Unicode

# --- The SOAP Service ---

class TimeControlService(ServiceBase):
    # Spyne dispatches @rpc methods as unbound functions on the service
    # *class* (see spyne.service.ServiceBase.call_wrapper) -- it never
    # instantiates TimeControlService, so there is no per-request `self`
    # and no `ctx.service`. Per-request state has to go through ctx.udc
    # ("user defined context"), which we populate below via the
    # 'method_call' event that spyne fires for every RPC call on this
    # service class.
    @rpc(_returns=Unicode)
    def get_version(ctx):
        return ctx.udc.get_version()

    # --- Main Project Management ---

    @rpc(Unicode, _returns=Boolean)
    def add_main_project(ctx, main_project_name):
        ctx.udc.add_main_project(main_project_name)
        return True

    @rpc(Unicode, _returns=Array(MainProjectModel))
    def list_main_projects(ctx, status_filter='all'):
        projects = ctx.udc.list_main_projects(status_filter)
        return [MainProjectModel(**p) for p in projects]

    @rpc(Unicode, _returns=Boolean)
    def delete_main_project(ctx, main_project_name):
        return ctx.udc.delete_main_project(main_project_name)

    @rpc(Unicode, Unicode, _returns=Boolean)
    def rename_main_project(ctx, old_name, new_name):
        return ctx.udc.rename_main_project(old_name, new_name)

    @rpc(Unicode, _returns=Boolean)
    def close_main_project(ctx, main_project_name):
        return ctx.udc.close_main_project(main_project_name)

    @rpc(Unicode, _returns=Boolean)
    def reopen_main_project(ctx, main_project_name):
        return ctx.udc.reopen_main_project(main_project_name)

    @rpc(Unicode, Unicode, _returns=OperationResultModel)
    def demote_main_project(ctx, main_project_to_demote, new_parent):
        success, msg = ctx.udc.demote_main_project(main_project_to_demote, new_parent)
        return OperationResultModel(success=success, message=msg)

    @rpc(_returns=Array(Unicode))
    def list_completed_main_projects(ctx):
        return ctx.udc.list_completed_main_projects()

    # --- Task Management ---

    @rpc(Unicode, Unicode, Unicode, Boolean, Unicode, Boolean, Unicode, Integer, Integer, _returns=Boolean)
    def add_task(ctx, main_project_name, task_name, due_date=None, today=False, note="", recurring=False, frequency="daily", userdefined_days=1, priority=0):
        return ctx.udc.add_task(main_project_name, task_name, due_date, today, note, recurring, frequency, userdefined_days, priority)

    @rpc(Unicode, Unicode, Unicode, _returns=Array(TaskModel))
    def list_tasks(ctx, main_project_name=None, status_filter='all', planning_filter=None):
        # To avoid breaking existing unit tests that expect only 2 parameters,
        # we only pass planning_filter if it is actually set.
        if planning_filter:
            tasks = ctx.udc.list_tasks(main_project_name, status_filter, planning_filter)
        else:
            tasks = ctx.udc.list_tasks(main_project_name, status_filter)
        return [TaskModel(**t) for t in tasks]

    @rpc(_returns=Boolean)
    def cleanup_overdue_today_tasks(ctx):
        return ctx.udc.cleanup_overdue_today_tasks()

    @rpc(Unicode, Unicode, Integer, _returns=Boolean)
    def delete_task(ctx, main_project_name, task_name, task_id=None):
        if task_id is not None:
            return ctx.udc.delete_task(main_project_name, task_name, task_id=task_id)
        return ctx.udc.delete_task(main_project_name, task_name)

    @rpc(Unicode, Unicode, Integer, _returns=Boolean)
    def close_task(ctx, main_project_name, task_name, task_id=None):
        if task_id is not None:
            return ctx.udc.close_task(main_project_name, task_name, task_id=task_id)
        return ctx.udc.close_task(main_project_name, task_name)

    @rpc(Unicode, Unicode, Integer, _returns=Boolean)
    def reopen_task(ctx, main_project_name, task_name, task_id=None):
        if task_id is not None:
            return ctx.udc.reopen_task(main_project_name, task_name, task_id=task_id)
        return ctx.udc.reopen_task(main_project_name, task_name)

    @rpc(Unicode, Unicode, Unicode, Integer, _returns=Boolean)
    def rename_task(ctx, main_project_name, old_name, new_name, task_id=None):
        if task_id is not None:
            return ctx.udc.rename_task(main_project_name, old_name, new_name, task_id=task_id)
        return ctx.udc.rename_task(main_project_name, old_name, new_name)

    @rpc(Unicode, Unicode, Unicode, Unicode, Boolean, Unicode, Unicode, Boolean, Unicode, Integer, Integer, Integer, _returns=Boolean)
    def update_task(ctx, main_project_name, old_name, new_name=None, due_date=None, today=None, note=None, status=None, recurring=None, frequency=None, userdefined_days=None, task_id=None, priority=None):
        # priority is appended after task_id (rather than grouped with the
        # other content fields before it) so existing positional callers that
        # already pass task_id as the 11th argument aren't shifted - spyne
        # dispatches @rpc args purely by position, so inserting a new
        # parameter anywhere but the end would silently break them.
        if task_id is not None:
            return ctx.udc.update_task(main_project_name, old_name, new_name, due_date, today, note, status, recurring, frequency, userdefined_days, priority=priority, task_id=task_id)
        return ctx.udc.update_task(main_project_name, old_name, new_name, due_date, today, note, status, recurring, frequency, userdefined_days, priority=priority)

    @rpc(Unicode, Unicode, Unicode, Unicode, _returns=OperationResultModel)
    def move_task(ctx, old_main, task_name, new_main, task_id=None):
        if task_id is not None:
            success, msg = ctx.udc.move_task(old_main, task_name, new_main, task_id=task_id)
        else:
            success, msg = ctx.udc.move_task(old_main, task_name, new_main)
        return OperationResultModel(success=success, message=msg)

    @rpc(Unicode, Unicode, Integer, _returns=OperationResultModel)
    def promote_task_to_project(ctx, main_project_name, task_name, task_id=None):
        if task_id is not None:
            success, msg = ctx.udc.promote_task_to_project(main_project_name, task_name, task_id=task_id)
        else:
            success, msg = ctx.udc.promote_task_to_project(main_project_name, task_name)
        return OperationResultModel(success=success, message=msg)

    @rpc(_returns=Integer)
    def delete_all_closed_tasks(ctx):
        return ctx.udc.delete_all_closed_tasks()

    @rpc(Integer, _returns=Array(InactiveProjectModel))
    def list_inactive_tasks(ctx, inactive_weeks):
        res = ctx.udc.list_inactive_tasks(inactive_weeks)
        return [InactiveProjectModel(**p) for p in res]

    @rpc(Integer, _returns=Array(InactiveProjectModel))
    def list_inactive_main_projects(ctx, inactive_weeks):
        res = ctx.udc.list_inactive_main_projects(inactive_weeks)
        # list_inactive_main_projects returns keys 'main_project' and 'last_activity'
        return [InactiveProjectModel(**p) for p in res]

    # --- Work / Time Tracking ---

    @rpc(Unicode, Unicode, Integer, _returns=Boolean)
    def start_work(ctx, main_project_name, task_name=None, task_id=None):
        if task_id is not None:
            return ctx.udc.start_work(main_project_name, task_id=task_id)
        return ctx.udc.start_work(main_project_name, task_name)

    @rpc(_returns=Boolean)
    def stop_work(ctx):
        return ctx.udc.stop_work()

    @rpc(_returns=CurrentWorkModel)
    def get_current_work(ctx):
        work = ctx.udc.get_current_work()
        if work:
            return CurrentWorkModel(**work)
        return None

    # --- Reporting ---

    @rpc(Unicode, _returns=Unicode)
    def generate_daily_report(ctx, report_date_str=None):
        """Generates the daily report. Date format: YYYY-MM-DD or empty for today."""
        date_obj = None
        if report_date_str:
            try:
                date_obj = datetime.strptime(report_date_str, "%Y-%m-%d").date()
            except ValueError:
                return "Fehler: Datum muss im Format YYYY-MM-DD sein."
        return ctx.udc.generate_daily_report(date_obj)

    @rpc(Unicode, _returns=Unicode)
    def generate_detailed_daily_report(ctx, report_date_str=None):
        """Generates the detailed daily report. Date format: YYYY-MM-DD or empty for today."""
        date_obj = None
        if report_date_str:
            try:
                date_obj = datetime.strptime(report_date_str, "%Y-%m-%d").date()
            except ValueError:
                return "Fehler: Datum muss im Format YYYY-MM-DD sein."
        return ctx.udc.generate_detailed_daily_report(date_obj)

    @rpc(Unicode, Unicode, _returns=Unicode)
    def generate_date_range_report(ctx, start_date_str, end_date_str):
        """Generates a report for a date range. Date format: YYYY-MM-DD."""
        try:
            start = datetime.strptime(start_date_str, "%Y-%m-%d").date()
            end = datetime.strptime(end_date_str, "%Y-%m-%d").date()
            return ctx.udc.generate_date_range_report(start, end)
        except ValueError:
            return "Fehler: Datum muss im Format YYYY-MM-DD sein."

    @rpc(Unicode, Unicode, _returns=Unicode)
    def generate_task_report(ctx, main_project_name, task_name):
        return ctx.udc.generate_task_report(main_project_name, task_name)

    @rpc(Unicode, _returns=Unicode)
    def generate_main_project_report(ctx, main_project_name):
        return ctx.udc.generate_main_project_report(main_project_name)


def _init_tracker_context(ctx):
    """Populates ctx.udc with a fresh TimeTracker for the current request."""
    ctx.udc = TimeTracker()


# Registers the handler above for the 'method_call' event, which spyne fires
# for every RPC call dispatched to TimeControlService.
TimeControlService.event_manager.add_listener('method_call', _init_tracker_context)


def load_config():
    """Loads the configuration from the config.json file."""
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    return {}

def main():
    # Load configuration
    config = load_config()
    port = config.get('soap_port', 8600)

    # Enable logging for debugging purposes
    logging.basicConfig(level=logging.INFO)
    logging.getLogger('spyne.protocol.xml').setLevel(logging.INFO)

    # Definition of the SOAP application
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