import streamlit as st
import json
import os
import sys
import re
from datetime import datetime, timedelta
import shutil

# Add parent directory to path to import modules from root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from tt.TimeTracker import TimeTracker
from i18n import _

try:
    from update import restore_previous_version
    UPDATE_MODULE_AVAILABLE = True
except (ImportError, ModuleNotFoundError):
    UPDATE_MODULE_AVAILABLE = False

# --- Configuration & Setup ---
CONFIG_FILE = 'config.json'
SL_DIR = os.path.dirname(os.path.abspath(__file__))

st.set_page_config(
    page_title="Time Control",
    page_icon="⏱️",
    layout="centered",
    initial_sidebar_state="collapsed"
)

def get_config():
    """
    Reads the configuration from config.json.

    :return: A dictionary containing the configuration, or an empty dict if reading fails.
    """
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return {}

def save_config(config):
    """
    Saves the given configuration dictionary to config.json.

    :param config: The configuration dictionary to save.
    """
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=4)

def local_css(file_name):
    """
    Loads a local CSS file and injects it into the Streamlit app.

    :param file_name: The path to the CSS file.
    """
    file_path = os.path.join(SL_DIR, file_name)
    if os.path.exists(file_path):
        with open(file_path, 'r', encoding='utf-8') as f:
            st.markdown(f'<style>{f.read()}</style>', unsafe_allow_html=True)

config = get_config()
local_css(config.get('css_file', 'style.css'))

# --- State Management ---

if 'tracker' not in st.session_state:
    st.session_state.tracker = TimeTracker()
    st.session_state.tracker.initialize_dependencies()

if 'menu' not in st.session_state:
    st.session_state.menu = 'main'

if 'feedback' not in st.session_state:
    st.session_state.feedback = None

# Context for multi-step operations (e.g. selecting main then sub project)
if 'context' not in st.session_state:
    st.session_state.context = {}

# --- Helper Functions ---

def navigate_to(menu_name):
    """
    Updates the session state to navigate to a different menu view.

    :param menu_name: The key of the menu to navigate to (must exist in menu_map).
    """
    st.session_state.menu = menu_name
    st.session_state.feedback = None
    st.rerun()
    
def set_feedback(message, type='success'):
    """
    Sets a feedback message to be displayed in the header of the next view.

    :param message: The message text.
    :param type: The type of message ('success', 'info', 'error'). Defaults to 'success'.
    """
    st.session_state.feedback = {'message': message, 'type': type}

# --- UI Components ---

def render_header(title, subtitle=None):
    """
    Renders the standard header for a view, including title, optional subtitle, and feedback messages.

    :param title: The main title of the view.
    :param subtitle: An optional subtitle.
    """
    st.title(title)
    if subtitle:
        st.caption(subtitle)
    if st.session_state.feedback:
        f = st.session_state.feedback
        if f['type'] == 'success': st.success(f['message'])
        elif f['type'] == 'info': st.info(f['message'])
        elif f['type'] == 'error': st.error(f['message'])
        st.session_state.feedback = None # Clear after showing

def t_label(key):
    """Translates the key and removes leading numbering."""
    match = re.match(r'^(\d+\.?\s*)(.*)', key)
    if match:
        _numbering, text = match.groups()
        return _(text)
    return _(key)

# --- Views ---

def view_main():
    """
    Renders the main menu view.
    """
    render_header(_("Time Control"), f"Version {st.session_state.tracker.get_version()}")
    
    current_work = st.session_state.tracker.get_current_work()
    task_details = {}
    is_done = False
    if current_work:
        all_tasks = st.session_state.tracker.list_tasks(main_project_name=current_work['main_project_name'], status_filter='all')
        task_details = next((t for t in all_tasks if t['task_name'] == current_work['task_name']), {})
        is_done = task_details.get('status') == 'done'

    # Custom CSS to keep the info box and edit button side-by-side and the button square
    st.markdown("""
        <style>
        /* Target the first horizontal block (columns) in the main view */
        [data-testid="stMainView"] [data-testid="stHorizontalBlock"]:first-of-type {
            display: flex !important;
            flex-direction: row !important;
            flex-wrap: nowrap !important;
            align-items: flex-start !important;
            gap: 0.5rem !important;
        }
        /* Ensure the info column takes remaining space */
        [data-testid="stMainView"] [data-testid="stHorizontalBlock"]:first-of-type [data-testid="column"]:nth-child(1) {
            flex: 1 1 auto !important;
            min-width: 0 !important;
        }
        /* Fixed width for the button columns and make buttons square */
        [data-testid="stMainView"] [data-testid="stHorizontalBlock"]:first-of-type [data-testid="column"]:nth-child(2),
        [data-testid="stMainView"] [data-testid="stHorizontalBlock"]:first-of-type [data-testid="column"]:nth-child(3) {
            flex: 0 0 50px !important;
            width: 50px !important;
            min-width: 50px !important;
        }
        [data-testid="stMainView"] [data-testid="stHorizontalBlock"]:first-of-type [data-testid="column"]:nth-child(2) button,
        [data-testid="stMainView"] [data-testid="stHorizontalBlock"]:first-of-type [data-testid="column"]:nth-child(3) button {
            width: 50px !important;
            height: 48px !important;
            padding: 0 !important;
            margin-top: 4px !important;
        }
        </style>
    """, unsafe_allow_html=True)

    col_info, col_done, col_edit = st.columns([10, 1, 1])
    with col_info:
        if current_work:
            display_name = f"{current_work['task_name']} (done)" if is_done else current_work['task_name']
            st.info(f"**{_('Current Active Work')}:** {display_name} ({current_work['main_project_name']})")
        else:
            st.info(_("No active work session."))
            
    with col_done:
        if st.button("✔", help=_("Done"), disabled=not current_work or is_done):
            st.session_state.tracker.update_task(
                current_work['main_project_name'],
                current_work['task_name'],
                status='done',
                recurring=task_details.get('recurring'),
                frequency=task_details.get('frequency'),
                userdefined_days=task_details.get('userdefined_days')
            )
            st.rerun()

    with col_edit:
        if st.button("✎", help=_("Aktuellen Task bearbeiten"), disabled=not current_work):
            st.session_state.context['selected_main'] = current_work['main_project_name']
            st.session_state.context['selected_task'] = current_work['task_name']
            st.session_state.context['return_to'] = 'main'
            navigate_to('edit_task_form')

    if st.button(t_label("1. Start work on task"), use_container_width=True):
        navigate_to('start_work')
    if st.button(t_label("2. Show current work"), use_container_width=True):
        navigate_to('show_current_work')
    if st.button(t_label("3. Stop current work"), use_container_width=True):
        if st.session_state.tracker.stop_work():
            set_feedback(_("Work session stopped successfully."))
        else:
            set_feedback(_("No active work session to stop."), 'info')
        st.rerun()

    if st.button(_("Aufgabenplanung"), use_container_width=True):
        navigate_to('task_planning')

    if st.button(_("Today View"), use_container_width=True):
        navigate_to('today_view')

    st.divider()

    if st.button(t_label("4. Handle projects and tasks"), use_container_width=True):
        navigate_to('project_management')
    if st.button(t_label("5. Reporting"), use_container_width=True):
        navigate_to('reporting')
    if st.button(t_label("6. Settings"), use_container_width=True):
        navigate_to('settings')

    st.divider()
    
    if st.button(t_label("0. Exit"), use_container_width=True):
        os._exit(0)

def view_task_planning():
    """
    Renders the task planning view, showing all tasks that are not closed.
    """
    render_header(_("Aufgabenplanung"))

    filter_options = [
        _("Today"), 
        _("Tomorrow"), 
        _("Weekly overview"), 
        _("Overdue tasks"), 
        _("Unplanned tasks"), 
        _("All")
    ]
    selected_filter = st.selectbox(_("Filter"), options=filter_options, index=0)

    filter_map = {
        _("Today"): 'today',
        _("Tomorrow"): 'tomorrow',
        _("Weekly overview"): 'weekly',
        _("Overdue tasks"): 'overdue',
        _("Unplanned tasks"): 'unplanned'
    }
    planning_filter = filter_map.get(selected_filter)

    if planning_filter == 'today':
        st.session_state.tracker.cleanup_overdue_today_tasks()

    current_work = st.session_state.tracker.get_current_work()
    tasks = st.session_state.tracker.list_tasks(planning_filter=planning_filter)

    if tasks:
        if planning_filter == 'weekly':
            weekday_names = [_("Monday"), _("Tuesday"), _("Wednesday"), _("Thursday"), _("Friday"), _("Saturday"), _("Sunday")]
            tasks_by_day = {}
            for task in tasks:
                due = task.get('due_date')
                if due:
                    try:
                        day_idx = datetime.fromisoformat(due).weekday()
                        tasks_by_day.setdefault(day_idx, []).append(task)
                    except (ValueError, TypeError):
                        continue
            
            for i in range(7):
                if i in tasks_by_day:
                    st.subheader(weekday_names[i])
                    # Layout für jede Aufgabe mit Bearbeiten-Button
                    for t_idx, task in enumerate(tasks_by_day[i]):
                        col_task, col_start_btn, col_edit_btn = st.columns([10, 1, 1])
                        with col_task:
                            name = task['task_name']
                            is_active = current_work and current_work['main_project_name'] == task['main_project_name'] and current_work['task_name'] == task['task_name']
                            display_name = f"{name} (done)" if task.get('status') == 'done' else name
                            if is_active: display_name = f"**{display_name}**"
                            today_info = " ⭐" if task.get('today') else ""
                            st.markdown(f"- **{task['main_project_name']}**: {display_name}{today_info}")
                        with col_start_btn:
                            if st.button("▶", key=f"start_task_planning_weekly_{task['main_project_name']}_{task['task_name']}_{t_idx}", help=_("Start work on task"), disabled=is_active or task.get('status') == 'done'):
                                st.session_state.tracker.start_work(task['main_project_name'], task['task_name'])
                                navigate_to('main')
                        with col_edit_btn:
                            if st.button("✎", key=f"edit_task_planning_weekly_{task['main_project_name']}_{task['task_name']}_{t_idx}", help=_("Edit Task")):
                                st.session_state.context['selected_main'] = task['main_project_name']
                                st.session_state.context['selected_task'] = task['task_name']
                                st.session_state.context['return_to'] = 'task_planning'
                                navigate_to('edit_task_form')
        else:
            current_main = None
            for t_idx, task in enumerate(tasks):
                if task['main_project_name'] != current_main:
                    current_main = task['main_project_name']
                    st.subheader(current_main)
                
                col_task, col_start_btn, col_edit_btn = st.columns([10, 1, 1])
                with col_task:
                    name = task['task_name']
                    status = task.get('status')
                    is_active = current_work and current_work['main_project_name'] == task['main_project_name'] and current_work['task_name'] == task['task_name']
                    display_name = f"{name} (done)" if status == 'done' else name
                    if is_active: display_name = f"**{display_name}**"
                    due_info = f" ({_('Due')}: {task['due_date']})" if task.get('due_date') else ""
                    today_info = " ⭐" if task.get('today') else ""
                    st.markdown(f"- {display_name}{due_info}{today_info}")
                with col_start_btn:
                    if st.button("▶", key=f"start_task_planning_{task['main_project_name']}_{task['task_name']}_{t_idx}", help=_("Start work on task"), disabled=is_active or status == 'done'):
                        st.session_state.tracker.start_work(task['main_project_name'], task['task_name'])
                        navigate_to('main')
                with col_edit_btn:
                    if st.button("✎", key=f"edit_task_planning_{task['main_project_name']}_{task['task_name']}_{t_idx}", help=_("Edit Task")):
                        st.session_state.context['selected_main'] = task['main_project_name']
                        st.session_state.context['selected_task'] = task['task_name']
                        st.session_state.context['return_to'] = 'task_planning'
                        navigate_to('edit_task_form')
    else:
        st.info(_("No tasks found."))
        
    if st.button(_("Back"), use_container_width=True):
        navigate_to('main')

def view_today_tasks():
    """
    Renders the view showing all tasks marked as 'today' and not closed.
    """
    render_header(_("Today's Tasks"))

    st.session_state.tracker.cleanup_overdue_today_tasks()
    current_work = st.session_state.tracker.get_current_work()
    
    # Here we show tasks that are explicitly marked as 'today' (⭐)
    all_open = st.session_state.tracker.list_tasks(status_filter='open')
    today_tasks = [t for t in all_open if t.get('today')]

    if today_tasks:
        # Group tasks by main project for better organization
        today_tasks_grouped = {}
        for task in today_tasks:
            main_proj = task['main_project_name']
            if main_proj not in today_tasks_grouped:
                today_tasks_grouped[main_proj] = []
            today_tasks_grouped[main_proj].append(task)
        
        for main_proj_name, sub_tasks in today_tasks_grouped.items(): # Grouped by main project
            st.subheader(main_proj_name)
            for t_idx, task in enumerate(sub_tasks): # Iterate through tasks in the group
                col_task, col_start_btn, col_edit_btn = st.columns([10, 1, 1])
                with col_task:
                    name = task['task_name']
                    status = task.get('status')
                    is_active = current_work and current_work['main_project_name'] == task['main_project_name'] and current_work['task_name'] == task['task_name']
                    display_name = f"{name} (done)" if status == 'done' else name
                    if is_active: display_name = f"**{display_name}**"
                    due_info = f" ({_('Due')}: {task['due_date']})" if task.get('due_date') else ""
                    st.markdown(f"- {display_name}{due_info}")
                with col_start_btn:
                    if st.button("▶", key=f"start_today_task_{task['main_project_name']}_{task['task_name']}_{t_idx}", help=_("Start work on task"), disabled=is_active or status == 'done'):
                        st.session_state.tracker.start_work(task['main_project_name'], task['task_name'])
                        navigate_to('main')
                with col_edit_btn:
                    if st.button("✎", key=f"edit_today_task_{task['main_project_name']}_{task['task_name']}_{t_idx}", help=_("Edit Task")):
                        st.session_state.context['selected_main'] = task['main_project_name']
                        st.session_state.context['selected_task'] = task['task_name']
                        st.session_state.context['return_to'] = 'today_view'
                        navigate_to('edit_task_form')
    else:
        st.info(_("No tasks for today."))

    if st.button(_("Back"), use_container_width=True):
        navigate_to('main')

def view_project_management():
    """
    Renders the project management submenu view.
    """
    render_header(_("Project Management"))
    
    if st.button(t_label("1. Project Management"), use_container_width=True):
        navigate_to('main_project_mgmt')
    if st.button(t_label("2. Task Management"), use_container_width=True):
        navigate_to('task_mgmt')
    
    st.divider()
    
    if st.button(t_label("0.  Back to Main Menu"), use_container_width=True):
        navigate_to('main')

def view_main_project_mgmt():
    """
    Renders the main project management submenu view.
    """
    render_header(_("Project Management"))
    
    if st.button(t_label("1.  Add Project"), use_container_width=True): navigate_to('add_main_project')
    if st.button(t_label("2.  List Projects"), use_container_width=True): navigate_to('list_main_projects')
    if st.button(t_label("3.  Rename Project"), use_container_width=True): navigate_to('rename_main_project')
    if st.button(t_label("4.  Close Project"), use_container_width=True): navigate_to('close_main_project')
    if st.button(t_label("5.  Re-open Project"), use_container_width=True): navigate_to('reopen_main_project')
    if st.button(t_label("6.  Delete Project"), use_container_width=True): navigate_to('delete_main_project')
    if st.button(t_label("7.  List Inactive Projects"), use_container_width=True): navigate_to('list_inactive_main')
    if st.button(t_label("8.  Demote Project to Task"), use_container_width=True): navigate_to('demote_main_project')
    if st.button(t_label("9.  List Completed Projects"), use_container_width=True): navigate_to('list_completed_main')
    
    st.divider()
    
    if st.button(t_label("0.  Back"), use_container_width=True):
        navigate_to('project_management')

def view_task_mgmt():
    """
    Renders the task management submenu view.
    """
    render_header(_("Task Management"))
    
    if st.button(t_label("1.  Add Task"), use_container_width=True): navigate_to('add_task')
    if st.button(t_label("2.  List Tasks"), use_container_width=True): navigate_to('list_tasks')
    if st.button(t_label("3.  Rename Task"), use_container_width=True): navigate_to('rename_task')
    if st.button(t_label("4.  Close Task"), use_container_width=True): navigate_to('close_task')
    if st.button(t_label("5.  Re-open Task"), use_container_width=True): navigate_to('reopen_task')
    if st.button(t_label("6.  Delete Task"), use_container_width=True): navigate_to('delete_task')
    if st.button(t_label("7.  Move Task"), use_container_width=True): navigate_to('move_task')
    if st.button(t_label("8.  List Inactive Tasks"), use_container_width=True): navigate_to('list_inactive_tasks')
    if st.button(t_label("9.  List All Closed Tasks"), use_container_width=True): navigate_to('list_closed_tasks')
    if st.button(_("Edit Task"), use_container_width=True): navigate_to('edit_task')
    if st.button(t_label("10. Delete All Closed Tasks"), use_container_width=True): navigate_to('delete_all_closed_tasks')
    if st.button(t_label("11. Promote Task to Project"), use_container_width=True): navigate_to('promote_task_to_project')
    
    st.divider()
    
    if st.button(t_label("0.  Back"), use_container_width=True):
        navigate_to('project_management')

def view_reporting():
    """
    Renders the reporting submenu view.
    """
    render_header(_("Reporting"))
    tt = st.session_state.tracker
    
    if st.button(t_label("1. Daily Report (Today)"), use_container_width=True):
        report = tt.generate_daily_report()
        st.session_state.context['report'] = report
        navigate_to('view_report')
        st.rerun()
    if st.button(t_label("2. Daily Report (Specific Day)"), use_container_width=True): navigate_to('report_specific_day')
    if st.button(t_label("3. Date Range Report"), use_container_width=True): navigate_to('report_date_range')
    if st.button(t_label("4. Detailed Task Report"), use_container_width=True): navigate_to('report_detailed_task')
    if st.button(t_label("5. Detailed Project Report"), use_container_width=True): navigate_to('report_detailed_main')
    if st.button(t_label("6. Detailed Daily Report"), use_container_width=True): navigate_to('report_detailed_daily')
    
    st.divider()
    
    if st.button(t_label("0. Back to Main Menu"), use_container_width=True):
        navigate_to('main')

def view_settings():
    """
    Renders the settings submenu view.
    """
    render_header(_("Settings"))
    
    if st.button(t_label("1. Change Language"), use_container_width=True): navigate_to('settings_language')
    if st.button(t_label("2. Restore Previous Version"), use_container_width=True): navigate_to('settings_restore')
    if st.button(t_label("3. Change Data Storage Location"), use_container_width=True): navigate_to('settings_storage')
    if st.button(t_label("4. Change Streamlit Port"), use_container_width=True): navigate_to('settings_port')
    if st.button(_("Change CSS Style"), use_container_width=True): navigate_to('settings_css')
    if st.button(_("Change View Mode"), use_container_width=True): navigate_to('settings_view_mode')
    
    st.divider()
    
    if st.button(t_label("0. Back to Main Menu"), use_container_width=True):
        navigate_to('main')

# --- Action Views (Forms) ---

def view_add_main_project():
    """
    Renders the form to add a new main project.
    """
    render_header(_("Add New Project"))
    with st.form("add_main_form"):
        name = st.text_input(_("Name of the project"))
        submitted = st.form_submit_button(_("Add Project"), use_container_width=True)
        if submitted and name:
            st.session_state.tracker.add_main_project(name)
            set_feedback(_("Project '{name}' added.").format(name=name))
            st.session_state.menu = 'main_project_mgmt'
            st.rerun()
    if st.button(_("Cancel"), use_container_width=True):
        navigate_to('main_project_mgmt')

def view_close_task():
    """
    Renders the form to close a task.
    """
    render_header(_("Close Task"))
    
    main_projects = st.session_state.tracker.list_main_projects(status_filter='open')
    if not main_projects:
        st.info(_("No open projects found."))
        if st.button(_("Back"), use_container_width=True):
            navigate_to('task_mgmt')
        return

    main_options = [p['main_project_name'] for p in main_projects]
    selected_main = st.selectbox(_("Select Project"), main_options)
    
    sub_projects = st.session_state.tracker.list_tasks(main_project_name=selected_main, status_filter='open')
    
    if not sub_projects:
        st.info(_("No open tasks to close in '{name}'.").format(name=selected_main))
        if st.button(_("Back"), use_container_width=True):
            navigate_to('task_mgmt')
        return

    sub_options = [sp['task_name'] for sp in sub_projects]
    sub_status = {sp['task_name']: sp['status'] for sp in sub_projects}

    with st.form("close_sub_form"):
        selected_sub = st.selectbox(_("Select Task"), sub_options, format_func=lambda x: f"{x} (done)" if sub_status.get(x) == 'done' else x)
        submitted = st.form_submit_button(_("Close Task"), use_container_width=True)
        
        if submitted:
            if st.session_state.tracker.close_task(selected_main, selected_sub):
                set_feedback(_("Task '{sub_name}' in '{main_name}' has been closed.").format(sub_name=selected_sub, main_name=selected_main))
                navigate_to('task_mgmt')
                st.rerun()
            else:
                st.error(_("Error: Main project or task not found."))

    if st.button(_("Cancel"), use_container_width=True):
        navigate_to('task_mgmt')

def view_reopen_task():
    """
    Renders the form to re-open a closed task.
    """
    render_header(_("Re-open Task"))
    
    main_projects = st.session_state.tracker.list_main_projects(status_filter='open')
    if not main_projects:
        st.info(_("No open projects found."))
        if st.button(_("Back"), use_container_width=True):
            navigate_to('task_mgmt')
        return

    main_options = [p['main_project_name'] for p in main_projects]
    selected_main = st.selectbox(_("Select Project"), main_options)
    
    sub_projects = st.session_state.tracker.list_tasks(main_project_name=selected_main, status_filter='closed')
    
    if not sub_projects:
        st.info(_("No closed tasks to reopen in '{name}'.").format(name=selected_main))
        if st.button(_("Back"), use_container_width=True):
            navigate_to('task_mgmt')
        return

    sub_options = [sp['task_name'] for sp in sub_projects]
    sub_status = {sp['task_name']: sp['status'] for sp in sub_projects}

    with st.form("reopen_sub_form"):
        selected_sub = st.selectbox(_("Select Task"), sub_options, format_func=lambda x: f"{x} (done)" if sub_status.get(x) == 'done' else x)
        submitted = st.form_submit_button(_("Re-open Task"), use_container_width=True)
        
        if submitted:
            if st.session_state.tracker.reopen_task(selected_main, selected_sub):
                set_feedback(_("Task '{sub_name}' in '{main_name}' has been reopened.").format(sub_name=selected_sub, main_name=selected_main))
                navigate_to('task_mgmt')
                st.rerun()
            else:
                st.error(_("Error: Main project or task not found."))

    if st.button(_("Cancel"), use_container_width=True):
        navigate_to('task_mgmt')

def view_delete_task():
    """
    Renders the form to delete a task.
    """
    render_header(_("Delete Task"))
    
    main_projects = st.session_state.tracker.list_main_projects(status_filter='open')
    if not main_projects:
        st.info(_("No open projects found."))
        if st.button(_("Back"), use_container_width=True):
            navigate_to('task_mgmt')
        return

    main_options = [p['main_project_name'] for p in main_projects]
    selected_main = st.selectbox(_("Select Project"), main_options)
    
    sub_projects = st.session_state.tracker.list_tasks(main_project_name=selected_main, status_filter='open')
    
    if not sub_projects:
        st.info(_("No open tasks to delete in '{name}'.").format(name=selected_main))
        if st.button(_("Back"), use_container_width=True):
            navigate_to('task_mgmt')
        return

    sub_options = [sp['task_name'] for sp in sub_projects]
    sub_status = {sp['task_name']: sp['status'] for sp in sub_projects}

    with st.form("delete_sub_form"):
        selected_sub = st.selectbox(_("Select Task"), sub_options, format_func=lambda x: f"{x} (done)" if sub_status.get(x) == 'done' else x)
        st.warning(_("This action cannot be undone."))
        submitted = st.form_submit_button(_("Delete Task"), use_container_width=True)
        
        if submitted:
            if st.session_state.tracker.delete_task(selected_main, selected_sub):
                set_feedback(_("Task '{sub_name}' deleted from '{main_name}'.").format(sub_name=selected_sub, main_name=selected_main))
                navigate_to('task_mgmt')
                st.rerun()
            else:
                st.error(_("Error: Main project or task not found."))

    if st.button(_("Cancel"), use_container_width=True):
        navigate_to('task_mgmt')

def view_move_task():
    """
    Renders the form to move a task to another main project.
    """
    render_header(_("Move Task"))
    
    main_projects = st.session_state.tracker.list_main_projects(status_filter='open')
    if not main_projects:
        st.info(_("No open projects found."))
        if st.button(_("Back"), use_container_width=True):
            navigate_to('task_mgmt')
        return

    main_options = [p['main_project_name'] for p in main_projects]
    source_main = st.selectbox(_("Select Source Project"), main_options)
    
    sub_projects = st.session_state.tracker.list_tasks(main_project_name=source_main, status_filter='all')
    
    if not sub_projects:
        st.info(_("No tasks found in '{name}'.").format(name=source_main))
        if st.button(_("Back"), use_container_width=True):
            navigate_to('task_mgmt')
        return

    sub_options = [sp['task_name'] for sp in sub_projects]
    sub_status = {sp['task_name']: sp['status'] for sp in sub_projects}
    target_options = [p for p in main_options if p != source_main]
    
    if not target_options:
        st.warning(_("No other projects available to move to."))
        if st.button(_("Back"), use_container_width=True):
            navigate_to('task_mgmt')
        return

    with st.form("move_sub_form"):
        selected_sub = st.selectbox(_("Select Task"), sub_options, format_func=lambda x: f"{x} (done)" if sub_status.get(x) == 'done' else x)
        target_main = st.selectbox(_("Select Target Project"), target_options)
        
        submitted = st.form_submit_button(_("Move Task"), use_container_width=True)
        
        if submitted:
            if st.session_state.tracker.move_task(source_main, selected_sub, target_main):
                set_feedback(_("Task '{sub}' moved from '{src}' to '{dst}'.").format(sub=selected_sub, src=source_main, dst=target_main))
                navigate_to('task_mgmt')
                st.rerun()
            else:
                st.error(_("Error: Could not move task."))

    if st.button(_("Cancel"), use_container_width=True):
        navigate_to('task_mgmt')

def view_list_inactive_tasks():
    """
    Renders the view to list inactive tasks based on a configurable threshold.
    """
    render_header(_("List Inactive Tasks"))
    
    weeks = st.number_input(_("Weeks of inactivity"), min_value=1, value=4, step=1)
    
    inactive_list = st.session_state.tracker.list_inactive_tasks(weeks)
    
    if inactive_list:
        st.markdown(_("Inactive Tasks (> {weeks} weeks):").format(weeks=weeks))
        for item in inactive_list:
            st.markdown(f"- **{item['main_project']}** / {item['task_name']}")
            st.caption(f"{_('Last Activity')}: {item['last_activity']}")
    else:
        st.info(_("No tasks found inactive for more than {weeks} weeks.").format(weeks=weeks))
        
    if st.button(_("Back"), use_container_width=True):
        navigate_to('task_mgmt')

def view_list_closed_tasks():
    """
    Renders the view listing all closed tasks.
    """
    render_header(_("List All Closed Tasks"))
    
    main_projects = st.session_state.tracker.list_main_projects(status_filter='all')
    found_any = False
    
    if main_projects:
        for mp in main_projects:
            mp_name = mp['main_project_name']
            closed_subs = st.session_state.tracker.list_tasks(main_project_name=mp_name, status_filter='closed')
            if closed_subs:
                found_any = True
                st.markdown(f"**{mp_name}**")
                for sp in closed_subs:
                    st.markdown(f"- {sp['task_name']}")
    
    if not found_any:
        st.info(_("No closed tasks found."))
        
    if st.button(_("Back"), use_container_width=True):
        navigate_to('task_mgmt')

def view_delete_all_closed_tasks():
    """
    Renders the view to delete all closed tasks at once.
    """
    render_header(_("Delete All Closed Tasks"))
    
    main_projects = st.session_state.tracker.list_main_projects(status_filter='all')
    to_delete = []
    
    if main_projects:
        for mp in main_projects:
            mp_name = mp['main_project_name']
            closed_subs = st.session_state.tracker.list_tasks(main_project_name=mp_name, status_filter='closed')
            if closed_subs:
                for sp in closed_subs:
                    to_delete.append((mp_name, sp['task_name']))
    
    if not to_delete:
        st.info(_("No closed tasks found."))
        if st.button(_("Back"), use_container_width=True):
            navigate_to('task_mgmt')
        return

    st.warning(_("Are you sure you want to delete {count} closed tasks? This action cannot be undone.").format(count=len(to_delete)))
    
    with st.expander(_("Show projects to delete")):
        for mp, sp in to_delete:
            st.markdown(f"- **{mp}** / {sp}")

    if st.button(_("Delete All"), type="primary", use_container_width=True):
        deleted_count = 0
        for mp, sp in to_delete:
            if st.session_state.tracker.delete_task(mp, sp):
                deleted_count += 1
        
        set_feedback(_("Successfully deleted {count} tasks.").format(count=deleted_count))
        navigate_to('task_mgmt')
        st.rerun()
        
    if st.button(_("Cancel"), use_container_width=True):
        navigate_to('task_mgmt')

def view_promote_task():
    """
    Renders the form to promote a task to a project.
    """
    render_header(_("Promote Task to Project"))
    
    main_projects = st.session_state.tracker.list_main_projects(status_filter='open')
    if not main_projects:
        st.info(_("No open projects found."))
        if st.button(_("Back"), use_container_width=True):
            navigate_to('task_mgmt')
        return

    main_options = [p['main_project_name'] for p in main_projects]
    selected_main = st.selectbox(_("Select Project"), main_options)
    
    sub_projects = st.session_state.tracker.list_tasks(main_project_name=selected_main, status_filter='open')
    
    if not sub_projects:
        st.info(_("No open tasks to promote in '{name}'.").format(name=selected_main))
        if st.button(_("Back"), use_container_width=True):
            navigate_to('task_mgmt')
        return

    sub_options = [sp['task_name'] for sp in sub_projects]
    sub_status = {sp['task_name']: sp['status'] for sp in sub_projects}

    with st.form("promote_sub_form"):
        selected_sub = st.selectbox(_("Select Task"), sub_options, format_func=lambda x: f"{x} (done)" if sub_status.get(x) == 'done' else x)
        st.info(_("This will create a new Project with the task's name and move all time entries to a 'General' task within it."))
        
        submitted = st.form_submit_button(_("Promote to Project"), use_container_width=True)
        
        if submitted:
            success, message = st.session_state.tracker.promote_task_to_project(selected_main, selected_sub)
            if success:
                set_feedback(message)
                navigate_to('task_mgmt')
                st.rerun()
            else:
                st.error(f"Error: {message}")

    if st.button(_("Cancel"), use_container_width=True):
        navigate_to('task_mgmt')

def view_list_main_projects():
    """
    Renders the list of all main projects.
    """
    render_header(_("List Projects"))
    projects = st.session_state.tracker.list_main_projects(status_filter='all')
    if projects:
        for p in projects:
            status = f"({_('closed')})" if p['status'] == 'closed' else ""
            st.markdown(f"- **{p['main_project_name']}** {status}")
    else:
        st.info(_("No projects found."))
    if st.button(_("Back"), use_container_width=True):
        navigate_to('main_project_mgmt')

def view_rename_main_project():
    """
    Renders the form to rename a project.
    """
    render_header(_("Rename Project"))
    projects = st.session_state.tracker.list_main_projects(status_filter='open')
    
    if not projects:
        st.info(_("No open projects to rename."))
        if st.button(_("Back"), use_container_width=True):
            navigate_to('main_project_mgmt')
        return

    options = [p['main_project_name'] for p in projects]
    selected_project = st.selectbox(_("Select Project"), options)
    
    with st.form("rename_main_form"):
        new_name = st.text_input(_("New Name"), value=selected_project)
        submitted = st.form_submit_button(_("Rename"), use_container_width=True)
        
        if submitted:
            if not new_name:
                st.error(_("Please enter a new name."))
            elif new_name == selected_project:
                st.warning(_("New name is the same as the old name."))
            elif st.session_state.tracker.rename_main_project(selected_project, new_name):
                set_feedback(_("Project '{old_name}' successfully renamed to '{new_name}'.").format(old_name=selected_project, new_name=new_name))
                navigate_to('main_project_mgmt')
                st.rerun()
            else:
                st.error(_("Error: Could not rename. The new name '{new_name}' might already exist.").format(new_name=new_name))

    if st.button(_("Cancel"), use_container_width=True):
        navigate_to('main_project_mgmt')

def view_list_tasks():
    """
    Renders the list of tasks for a selected main project.
    """
    render_header(_("List Tasks"))
    
    main_projects = st.session_state.tracker.list_main_projects(status_filter='all')
    if not main_projects:
        st.info(_("No projects found."))
        if st.button(_("Back"), use_container_width=True):
            navigate_to('task_mgmt')
        return

    options = [p['main_project_name'] for p in main_projects]
    selected_main = st.selectbox(_("Select Project"), options)
    
    tasks = st.session_state.tracker.list_tasks(main_project_name=selected_main, status_filter='all')
    
    if tasks:
        st.markdown(_("Tasks for '{name}':").format(name=selected_main))
        for t in tasks:
            name = t['task_name']
            status_text = f"({_('closed')})" if t['status'] == 'closed' else ""
            display_name = f"{name} (done)" if t['status'] == 'done' else name
            st.markdown(f"- {display_name} {status_text}")
    else:
        st.info(_("No tasks found for '{name}'.").format(name=selected_main))
        
    if st.button(_("Back"), use_container_width=True):
        navigate_to('task_mgmt')

def view_rename_task():
    """
    Renders the form to rename a task.
    """
    render_header(_("Rename Task"))
    
    main_projects = st.session_state.tracker.list_main_projects(status_filter='open')
    if not main_projects:
        st.info(_("No open projects found."))
        if st.button(_("Back"), use_container_width=True):
            navigate_to('task_mgmt')
        return

    main_options = [p['main_project_name'] for p in main_projects]
    selected_main = st.selectbox(_("Select Project"), main_options)
    
    tasks = st.session_state.tracker.list_tasks(main_project_name=selected_main, status_filter='open')
    
    if not tasks:
        st.info(_("No open tasks to rename in '{name}'.").format(name=selected_main))
        if st.button(_("Back"), use_container_width=True):
            navigate_to('task_mgmt')
        return

    task_options = [t['task_name'] for t in tasks]
    task_status = {t['task_name']: t['status'] for t in tasks}
<<<<<<< FrankFaulstich/issue364
    selected_task = st.selectbox(_("Select Task"), task_options, format_func=lambda x: f"{x} (done)" if task_status.get(x) == 'done' else x)
=======
    selected_task = st.selectbox(_("Select Task"), task_options)
>>>>>>> main

    with st.form("rename_task_form"):
        new_name = st.selectbox(_("Select Task"), task_options, format_func=lambda x: f"{x} (done)" if task_status.get(x) == 'done' else x)
        submitted = st.form_submit_button(_("Rename"), use_container_width=True)
        
        if submitted:
            if not new_name:
                st.error(_("Please enter a new name."))
            elif new_name == selected_task:
                st.warning(_("New name is the same as the old name."))
            elif st.session_state.tracker.rename_task(selected_main, selected_task, new_name):
                set_feedback(_("Task '{old_name}' renamed to '{new_name}'.").format(old_name=selected_task, new_name=new_name))
                navigate_to('task_mgmt')
                st.rerun()
            else:
                st.error(_("Error: Could not rename. The new name might already exist."))

    if st.button(_("Cancel"), use_container_width=True):
        navigate_to('task_mgmt')
        
def view_close_main_project():
    """
    Renders the form to close a project.
    """
    render_header(_("Close Project"))
    projects = st.session_state.tracker.list_main_projects(status_filter='open')
    
    if not projects:
        st.info(_("No open projects to close."))
        if st.button(_("Back"), use_container_width=True):
            navigate_to('main_project_mgmt')
        return

    options = [p['main_project_name'] for p in projects]
    
    with st.form("close_main_form"):
        selected_project = st.selectbox(_("Select Project"), options)
        submitted = st.form_submit_button(_("Close Project"), use_container_width=True)
        
        if submitted:
            if st.session_state.tracker.close_main_project(selected_project):
                set_feedback(_("Project '{name}' has been closed.").format(name=selected_project))
                navigate_to('main_project_mgmt')
                st.rerun()
            else:
                st.error(_("Error: Project not found."))

    if st.button(_("Cancel"), use_container_width=True):
        navigate_to('main_project_mgmt')

def view_reopen_main_project():
    """
    Renders the form to re-open a closed project.
    """
    render_header(_("Re-open Project"))
    projects = st.session_state.tracker.list_main_projects(status_filter='closed')
    
    if not projects:
        st.info(_("No closed projects to reopen."))
        if st.button(_("Back"), use_container_width=True):
            navigate_to('main_project_mgmt')
        return

    options = [p['main_project_name'] for p in projects]
    
    with st.form("reopen_main_form"):
        selected_project = st.selectbox(_("Select Project"), options)
        submitted = st.form_submit_button(_("Re-open Project"), use_container_width=True)
        
        if submitted:
            if st.session_state.tracker.reopen_main_project(selected_project):
                set_feedback(_("Project '{name}' has been reopened.").format(name=selected_project))
                navigate_to('main_project_mgmt')
                st.rerun()
            else:
                st.error(_("Error: Project not found."))

    if st.button(_("Cancel"), use_container_width=True):
        navigate_to('main_project_mgmt')

def view_delete_main_project():
    """
    Renders the form to delete a project.
    """
    render_header(_("Delete Project"))
    projects = st.session_state.tracker.list_main_projects(status_filter='all')
    
    if not projects:
        st.info(_("No projects found."))
        if st.button(_("Back"), use_container_width=True):
            navigate_to('main_project_mgmt')
        return

    options = [p['main_project_name'] for p in projects]
    
    with st.form("delete_main_form"):
        selected_project = st.selectbox(_("Select Project"), options)
        st.warning(_("This action cannot be undone. All associated tasks and time entries will be deleted."))
        submitted = st.form_submit_button(_("Delete Project"), use_container_width=True)
        
        if submitted:
            if st.session_state.tracker.delete_main_project(selected_project):
                set_feedback(_("Project '{name}' has been deleted.").format(name=selected_project))
                navigate_to('main_project_mgmt')
                st.rerun()
            else:
                st.error(_("Error: Project not found."))

    if st.button(_("Cancel"), use_container_width=True):
        navigate_to('main_project_mgmt')

def view_list_inactive_main_projects():
    """
    Renders the view to list inactive projects based on a configurable threshold.
    """
    render_header(_("List Inactive Projects"))
    
    weeks = st.number_input(_("Weeks of inactivity"), min_value=1, value=4, step=1)
    
    inactive_list = st.session_state.tracker.list_inactive_main_projects(weeks)
    
    if inactive_list:
        st.markdown(_("Inactive Projects (> {weeks} weeks):").format(weeks=weeks))
        for item in inactive_list:
            st.markdown(f"- **{item['main_project']}**")
            st.caption(f"{_('Last Activity')}: {item['last_activity']}")
    else:
        st.info(_("No projects found inactive for more than {weeks} weeks.").format(weeks=weeks))
        
    if st.button(_("Back"), use_container_width=True):
        navigate_to('main_project_mgmt')

def view_demote_main_project():
    """
    Renders the form to demote a project to a task.
    """
    render_header(_("Demote Project"))
    
    projects = st.session_state.tracker.list_main_projects(status_filter='open')
    if not projects:
        st.info(_("No open projects found."))
        if st.button(_("Back"), use_container_width=True):
            navigate_to('main_project_mgmt')
        return

    options = [p['main_project_name'] for p in projects]
    
    with st.form("demote_main_form"):
        selected_project = st.selectbox(_("Select Project to Demote"), options)
        
        target_options = [p for p in options if p != selected_project]
        target_project = None
        
        if not target_options:
            st.warning(_("No other projects available to demote into."))
        else:
            target_project = st.selectbox(_("Select Target Project"), target_options)
            st.info(_("This will convert '{src}' into a task of '{dst}'.").format(src=selected_project, dst=target_project))
            
        submitted = st.form_submit_button(_("Demote Project"), disabled=not target_options, use_container_width=True)
        
        if submitted and target_project:
            success, message = st.session_state.tracker.demote_main_project(selected_project, target_project)
            if success:
                set_feedback(message)
                navigate_to('main_project_mgmt')
                st.rerun()
            else:
                st.error(f"Error: {message}")

    if st.button(_("Cancel"), use_container_width=True):
        navigate_to('main_project_mgmt')

def view_list_completed_main():
    """
    Renders the list of completed projects (those with only closed or no tasks).
    """
    render_header(_("List Completed Projects"))
    
    completed_projects = st.session_state.tracker.list_completed_main_projects()
    
    if completed_projects:
        st.markdown(_("Projects with only closed or no tasks:"))
        for project_name in completed_projects:
            st.markdown(f"- **{project_name}**")
    else:
        st.info(_("No completed projects found."))
        
    if st.button(_("Back"), use_container_width=True):
        navigate_to('main_project_mgmt')

def view_add_task():
    """
    Renders the first step of adding a task: selecting the project.
    """
    render_header(_("Add Task"), _("Step 1: Select Project"))
    projects = st.session_state.tracker.list_main_projects(status_filter='open')
    if not projects:
        st.warning(_("No open projects found. Please add one first."))
        if st.button(_("Back"), use_container_width=True): navigate_to('task_mgmt')
        return

    options = [p['main_project_name'] for p in projects]
    selected = st.selectbox(_("Project"), options)
    
    if st.button(_("Next"), use_container_width=True):
        st.session_state.context['selected_main'] = selected
        navigate_to('add_task_form')
        st.rerun()
    if st.button(_("Cancel"), use_container_width=True): navigate_to('task_mgmt')

def view_add_task_form():
    """
    Renders the second step of adding a task: entering the name.
    """
    main_project = st.session_state.context.get('selected_main')
    if not main_project:
        set_feedback(_("No project selected. Please start again."), "error")
        navigate_to('add_task')
        st.rerun()
        return

    render_header(_("Add Task"), f"{_('To Project:')} {main_project}")
    
    # Inject CSS for full-height responsive layout and styled preview
    st.markdown("""
        <style>
        div[data-testid="stTabPanel"], div[data-baseweb='textarea'] {
            height: calc(100vh - 560px) !important;
            min-height: 250px;
        }
        div[data-baseweb='textarea'] textarea, .note-preview-box {
            height: 100% !important;
        }
        .note-preview-box {
            border: 1px solid rgba(49, 51, 63, 0.2);
            border-radius: 0.5rem;
            padding: 1rem;
            overflow-y: auto;
        }
        </style>
        """, unsafe_allow_html=True)

    existing_tasks = [t['task_name'].lower() for t in st.session_state.tracker.list_tasks(main_project_name=main_project)]

    name = st.text_input(_("Name of the new task"))
    if "new_task_note" not in st.session_state:
        st.session_state.new_task_note = ""
    
    col_date, col_today, col_rec = st.columns([2, 1, 1])
    with col_date:
        due_date = st.date_input(_("Due date"), value=None, format="YYYY-MM-DD")
    with col_today:
        st.markdown("<div style='padding-top: 28px;'></div>", unsafe_allow_html=True)
        today = st.checkbox(_("Today"))
    with col_rec:
        st.markdown("<div style='padding-top: 28px;'></div>", unsafe_allow_html=True)
        is_recurring = st.checkbox(_("Recurring"))

    # Frequency options for recurring tasks
    freq_options = ["daily", "on all business days", "weekly", "monthly", "userdefined"]
    freq_labels = [_("daily"), _("on all business days"), _("weekly"), _("monthly"), _("userdefined")]
    
    col_freq, col_ud = st.columns([2, 1])
    with col_freq:
        selected_label = st.selectbox(_("Frequency"), options=freq_labels, index=0, disabled=not is_recurring)
        final_freq = freq_options[freq_labels.index(selected_label)]
    with col_ud:
        ud_days = st.number_input(
            _("Days"), min_value=1, value=1, step=1, 
            disabled=not (is_recurring and final_freq == "userdefined")
        )

    tab_edit, tab_preview = st.tabs([_("Edit"), _("Preview")])
    with tab_edit:
        st.text_area(_("Notes (Markdown)"), key="new_task_note", label_visibility="collapsed")
    with tab_preview:
        st.markdown('<div class="note-preview-box">', unsafe_allow_html=True)
        st.markdown(st.session_state.new_task_note if st.session_state.new_task_note else f"*{_('No notes provided.')}*")
        st.markdown('</div>', unsafe_allow_html=True)
    
    st.divider()
    col_btn1, col_btn2 = st.columns(2)
    with col_btn1:
        if st.button(_("Add Task"), type="primary", use_container_width=True):
            if not name:
                st.error(_("Please enter a name."))
            elif name.lower() in existing_tasks:
                set_feedback(_("A task with this name already exists in this project."), 'error')
            elif st.session_state.tracker.add_task(
                main_project, 
                name, 
                due_date.isoformat() if due_date else None, 
                today, 
                st.session_state.new_task_note,
                recurring=is_recurring,
                frequency=final_freq,
                userdefined_days=ud_days
            ):
                set_feedback(_("Task '{sub_name}' added to '{main_name}'.").format(sub_name=name, main_name=main_project))
                if "new_task_note" in st.session_state: del st.session_state.new_task_note
                st.session_state.context = {}
                navigate_to('task_mgmt')
    with col_btn2:
        if st.button(_("Cancel"), use_container_width=True): 
            if "new_task_note" in st.session_state: del st.session_state.new_task_note
            st.session_state.context = {}
            navigate_to('task_mgmt')

def view_edit_task():
    """Step 1 of editing a task: Select the main project."""
    render_header(_("Edit Task"), _("Step 1: Select Project"))
    projects = st.session_state.tracker.list_main_projects(status_filter='open')
    if not projects:
        st.warning(_("No open projects found."))
        if st.button(_("Back"), use_container_width=True): navigate_to('task_mgmt')
        return
    selected = st.selectbox(_("Project"), [p['main_project_name'] for p in projects])
    if st.button(_("Next"), use_container_width=True):
        st.session_state.context['selected_main'] = selected
        navigate_to('edit_task_select_task')
        st.rerun()
    if st.button(_("Cancel"), use_container_width=True): navigate_to('task_mgmt')

def view_edit_task_select_task():
    """Step 2 of editing a task: Select the task from the chosen project."""
    main_project = st.session_state.context.get('selected_main')
    render_header(_("Edit Task"), f"{_('Step 2: Select Task from')} {main_project}")
    tasks = st.session_state.tracker.list_tasks(main_project_name=main_project, status_filter='open')
    if not tasks:
        st.info(_("No open tasks found."))
        if st.button(_("Back"), use_container_width=True): navigate_to('edit_task')
        return
    task_names = [t['task_name'] for t in tasks]
    task_status = {t['task_name']: t['status'] for t in tasks}
    selected_task = st.selectbox(_("Select Task"), task_names, format_func=lambda x: f"{x} (done)" if task_status.get(x) == 'done' else x)
    if st.button(_("Next"), use_container_width=True):
        st.session_state.context['selected_task'] = selected_task
        navigate_to('edit_task_form')
        st.rerun()
    if st.button(_("Back"), use_container_width=True): navigate_to('edit_task')

def view_edit_task_form():
    """Step 3 of editing a task: Change name and due date."""
    main_project = st.session_state.context.get('selected_main')
    task_name = st.session_state.context.get('selected_task')
    return_to = st.session_state.context.get('return_to', 'task_mgmt')
    
    # Find current details to pre-fill the form
    all_tasks = st.session_state.tracker.list_tasks(main_project_name=main_project)
    task_details = next((t for t in all_tasks if t['task_name'] == task_name), {})
    
    render_header(_("Edit Task"), f"{main_project} / {task_name}")
    
    # Inject CSS for full-height responsive layout and styled preview
    st.markdown("""
        <style>
        div[data-testid="stTabPanel"], div[data-baseweb='textarea'] {
            height: calc(100vh - 560px) !important;
            min-height: 200px;
        }
        div[data-baseweb='textarea'] textarea, .note-preview-box {
            height: 100% !important;
        }
        .note-preview-box {
            border: 1px solid rgba(49, 51, 63, 0.2);
            border-radius: 0.5rem;
            padding: 1rem;
            overflow-y: auto;
        }
        </style>
        """, unsafe_allow_html=True)

    # Initialisiere Session-State für das Datum, um interaktives Leeren zu ermöglichen
    if 'edit_due_date' not in st.session_state:
        curr_due = task_details.get('due_date')
        st.session_state.edit_due_date = datetime.fromisoformat(curr_due).date() if curr_due else None

    if 'edit_task_note' not in st.session_state:
        st.session_state.edit_task_note = task_details.get('note', '')

    new_name = st.text_input(_("Task Name"), value=task_name)

    col_date, col_clear = st.columns([5, 1])
    with col_date:
        new_due = st.date_input(_("Due Date"), value=st.session_state.edit_due_date, format="YYYY-MM-DD")
        st.session_state.edit_due_date = new_due
    with col_clear:
        st.markdown("<div style='padding-top: 28px;'></div>", unsafe_allow_html=True)
        if st.button("✖️", use_container_width=True, help=_("Clear")):
            st.session_state.edit_due_date = None
            st.rerun()

    col_today, col_done, col_rec = st.columns(3)
    with col_today:
        is_today = st.checkbox(_("Today"), value=task_details.get('today', False))
    with col_done:
        is_done = st.checkbox(_("Done"), value=(task_details.get('status') == 'done'))
    with col_rec:
        is_recurring = st.checkbox(_("Recurring"), value=task_details.get('recurring', False))

    freq_options = ["daily", "on all business days", "weekly", "monthly", "userdefined"]
    freq_labels = [_("daily"), _("on all business days"), _("weekly"), _("monthly"), _("userdefined")]
    curr_freq = task_details.get('frequency', 'daily')
    try:
        freq_idx = freq_options.index(curr_freq)
    except ValueError:
        freq_idx = 0

    col_freq, col_ud = st.columns([2, 1])
    with col_freq:
        selected_label = st.selectbox(_("Frequency"), options=freq_labels, index=freq_idx, disabled=not is_recurring)
        final_freq = freq_options[freq_labels.index(selected_label)]
    with col_ud:
        ud_days = st.number_input(_("Days"), min_value=1, value=task_details.get('userdefined_days', 1), step=1, disabled=not (is_recurring and final_freq == "userdefined"))

    tab_edit, tab_preview = st.tabs([_("Edit"), _("Preview")])
    with tab_edit:
        st.text_area(_("Notes (Markdown)"), key="edit_task_note", label_visibility="collapsed")
    with tab_preview:
        st.markdown('<div class="note-preview-box">', unsafe_allow_html=True)
        st.markdown(st.session_state.edit_task_note if st.session_state.edit_task_note else f"*{_('No notes provided.')}*")
        st.markdown('</div>', unsafe_allow_html=True)
    
    st.divider()
    col_save, col_cancel = st.columns(2)
    with col_save:
        if st.button(_("Save Changes"), type="primary", use_container_width=True):
            final_due = st.session_state.edit_due_date.isoformat() if st.session_state.edit_due_date else None
            new_status = 'done' if is_done else 'open'
            
            if st.session_state.tracker.update_task(
                main_project, 
                task_name, 
                new_name, 
                final_due, 
                is_today, 
                st.session_state.edit_task_note, 
                new_status,
                recurring=is_recurring,
                frequency=final_freq,
                userdefined_days=ud_days
            ):
                set_feedback(_("Task updated successfully."))
                if 'edit_due_date' in st.session_state: del st.session_state.edit_due_date
                if 'edit_task_note' in st.session_state: del st.session_state.edit_task_note
                st.session_state.context = {}
                navigate_to(return_to)
            else:
                st.error(_("Error: Could not update task."))

    with col_cancel:
        if st.button(_("Cancel"), use_container_width=True):
            if 'edit_due_date' in st.session_state: del st.session_state.edit_due_date
            if 'edit_task_note' in st.session_state: del st.session_state.edit_task_note
            st.session_state.context = {}
            navigate_to(return_to)

def view_start_work():
    """
    Renders the form to start work on a task.
    """
    render_header(_("Start Work on Task"))
    
    projects = st.session_state.tracker.list_main_projects(status_filter='open')
    if not projects:
        st.info(_("No open projects found. Please add one first."))
        if st.button(_("Back"), use_container_width=True):
            navigate_to('main')
        return

    options = [p['main_project_name'] for p in projects]
    selected_main = st.selectbox(_("Select Project"), options)
    
    tasks = st.session_state.tracker.list_tasks(main_project_name=selected_main, status_filter='open')
    if not tasks:
        st.info(_("No open tasks to start work on in '{name}'.").format(name=selected_main))
        if st.button(_("Back"), use_container_width=True):
            navigate_to('main')
        return

    task_options = [t['task_name'] for t in tasks]
    task_status = {t['task_name']: t['status'] for t in tasks}

    with st.form("start_work_form"):
        selected_task = st.selectbox(_("Select Task"), task_options, format_func=lambda x: f"{x} (done)" if task_status.get(x) == 'done' else x)
        submitted = st.form_submit_button(_("Start Work"), use_container_width=True)
        
        if submitted:
            if st.session_state.tracker.start_work(selected_main, selected_task):
                set_feedback(_("Work started on '{task_name}' in project '{main_name}'.").format(task_name=selected_task, main_name=selected_main))
                navigate_to('main')
                st.rerun()
            else:
                st.error(_("Error starting work."))

    if st.button(_("Cancel"), use_container_width=True):
        navigate_to('main')

def view_show_current_work():
    """
    Renders the view showing the currently active work session.
    """
    render_header(_("Current Active Work"))
    current = st.session_state.tracker.get_current_work()
    if current:
        start_time = datetime.fromisoformat(current['start_time'])
        duration = datetime.now() - start_time
        hours, remainder = divmod(duration.total_seconds(), 3600)
        minutes, seconds = divmod(remainder, 60)
        
        st.markdown(f"**{_('Project')}:** {current['main_project_name']}")
        st.markdown(f"**{_('Task')}:** {current['task_name']}")
        st.markdown(f"**{_('Started at')}:** {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        st.markdown(f"**{_('Duration')}:** {int(hours):02}:{int(minutes):02}:{int(seconds):02}")
    else:
        st.info(_("No active work session."))
    
    if st.button(_("Back"), use_container_width=True): navigate_to('main')

def view_settings_port():
    """
    Renders the form to change the Streamlit server port.
    """
    render_header(_("Streamlit Port Settings"))
    config = get_config()
    current_port = config.get('streamlit_port', 8501)
    
    st.markdown(f"**{_('Current Streamlit Port')}:** {current_port}")
    
    with st.form("port_form"):
        new_port = st.number_input(_("New Port"), min_value=1024, max_value=65535, value=current_port)
        submitted = st.form_submit_button(_("Save"), use_container_width=True)
        if submitted:
            config['streamlit_port'] = new_port
            save_config(config)
            set_feedback(_("Port updated to {port}. Please restart Streamlit.").format(port=new_port))
            navigate_to('settings')
            st.rerun()
            
    if st.button(_("Cancel"), use_container_width=True): navigate_to('settings')

def view_settings_storage():
    """
    Renders the form to change the data storage location.
    """
    render_header(_("Change Data Storage Location"))
    
    current_path = st.session_state.tracker.file_path
    st.markdown(f"**{_('Current data file')}:** `{current_path}`")

    with st.form("storage_form"):
        new_path_input = st.text_input(_("New Path for data file"), placeholder="data.json").strip()
        
        # Offer to move data only if the old file exists and a new path is provided
        can_move = os.path.exists(current_path) and new_path_input and os.path.abspath(new_path_input) != os.path.abspath(current_path)
        
        move_data = st.checkbox(
            _("Move existing data to the new location"), 
            value=True, 
            disabled=not can_move,
            help=_("If unchecked, the old data file will remain, and a new empty one might be created at the new location on restart.")
        )

        submitted = st.form_submit_button(_("Save"), use_container_width=True)

        if submitted:
            if not new_path_input:
                st.error(_("Please enter a new path."))
            else:
                # --- Security Fix: Path Traversal ---
                # Normalize the path and ensure it's within the application's directory.
                app_root = os.path.abspath(os.getcwd())
                safe_path = os.path.abspath(new_path_input)

                if not safe_path.startswith(app_root):
                    st.error(_("Error: For security, the data file must be located within the application directory."))
                else:
                    directory = os.path.dirname(safe_path)
                    if directory and not os.path.exists(directory):
                        st.error(_("Error: The directory '{dir}' does not exist.").format(dir=directory))
                    else:
                        config = get_config()
                        config['data_file'] = new_path_input # Store user's relative/simple path
                        save_config(config)
                        
                        feedback_message = _("Storage location updated. Please restart the application for the changes to take effect.")
                        
                        if move_data and can_move:
                            try:
                                shutil.move(current_path, safe_path)
                                feedback_message += " " + _("Data moved successfully.")
                            except Exception as e:
                                st.error(_("Error moving data: {error}").format(error=e))
                                feedback_message = None # Prevent navigation on error

                        if feedback_message:
                            set_feedback(feedback_message)
                            navigate_to('settings')
                            st.rerun()

    if st.button(_("Cancel"), use_container_width=True):
        navigate_to('settings')

def view_settings_css():
    """
    Renders the form to change the CSS file.
    """
    render_header(_("Change CSS Style"))
    
    config = get_config()
    current_css = config.get('css_file', 'style.css')
    st.markdown(f"**{_('Current CSS file')}:** `{current_css}`")
    
    # Find .css files in the current directory
    css_files = [f for f in os.listdir(SL_DIR) if f.endswith('.css')]
    if not css_files:
        css_files = ['style.css']
    
    # Ensure current_css is in the list for the selectbox index
    if current_css not in css_files and os.path.exists(os.path.join(SL_DIR, current_css)):
        css_files.append(current_css)
    
    try:
        current_index = css_files.index(current_css)
    except ValueError:
        current_index = 0

    with st.form("css_form"):
        selected_css = st.selectbox(_("Select CSS File"), css_files, index=current_index)
        submitted = st.form_submit_button(_("Save"), use_container_width=True)
        
        if submitted:
            config['css_file'] = selected_css
            save_config(config)
            set_feedback(_("CSS style updated. Please restart the application for the changes to take effect."))
            navigate_to('settings')
            st.rerun()

    if st.button(_("Cancel"), use_container_width=True):
        navigate_to('settings')

def view_settings_view_mode():
    """
    Renders the form to change the application view mode (Webview vs Browser).
    """
    render_header(_("Change View Mode"))
    config = get_config()
    current_mode = config.get('view_mode', 'webview')
    
    # Map internal values to display labels
    modes = {'webview': _('App Window (Webview)'), 'browser': _('System Browser')}
    mode_keys = list(modes.keys())
    mode_labels = list(modes.values())
    
    try:
        current_index = mode_keys.index(current_mode)
    except ValueError:
        current_index = 0

    with st.form("view_mode_form"):
        selected_label = st.selectbox(_("Select View Mode"), mode_labels, index=current_index)
        submitted = st.form_submit_button(_("Save"), use_container_width=True)
        
        if submitted:
            new_mode = mode_keys[mode_labels.index(selected_label)]
            config['view_mode'] = new_mode
            save_config(config)
            set_feedback(_("View mode updated. Please restart the application for the changes to take effect."))
            navigate_to('settings')
            st.rerun()

    if st.button(_("Cancel"), use_container_width=True):
        navigate_to('settings')

def view_settings_restore():
    """
    Renders the view to restore the application to a previous version.
    """
    render_header(_("Restore Previous Version"))
    
    backup_zip_file = "prev-version.zip"

    if not UPDATE_MODULE_AVAILABLE:
        st.error(_("The 'update' module is not available. This feature is disabled."))
        if st.button(_("Back"), use_container_width=True): navigate_to('settings')
        return

    if not os.path.exists(backup_zip_file):
        st.info(_("No previous version backup '{filename}' found.").format(filename=backup_zip_file))
        if st.button(_("Back"), use_container_width=True): navigate_to('settings')
        return

    st.warning(_("This will restore the application to the previously backed-up version. The application will then restart. You may need to manually refresh your browser if it does not reconnect automatically."))
    
    if st.button(_("Restore and Restart"), type="primary", use_container_width=True):
        with st.spinner(_("Restoring and restarting...")):
            restore_previous_version()
        # This code is unreachable due to os.execv in the called function
        st.success(_("Restore complete. Please restart the application."))
            
def view_settings_language():
    """
    Renders the form to change the application language.
    """
    render_header(_("Change Language"))

    supported_languages = {'en': 'English', 'de': 'Deutsch', 'fr': 'Français', 'es': 'Español', 'cs': 'Čeština'}
    
    # 'en' is always available as the default
    available_languages = {'en': 'English'}
    # Add other languages if their locale directory exists
    for lang, name in supported_languages.items():
        if lang != 'en' and os.path.isdir(os.path.join('locale', lang)):
            available_languages[lang] = name

    lang_codes = list(available_languages.keys())
    lang_names = [f"{name} ({code})" for code, name in available_languages.items()]

    config = get_config()
    current_lang_code = config.get('language', 'en')
    
    try:
        current_lang_index = lang_codes.index(current_lang_code)
    except ValueError:
        current_lang_index = 0

    with st.form("language_form"):
        selected_lang_name = st.selectbox(_("Select Language"), options=lang_names, index=current_lang_index)
        submitted = st.form_submit_button(_("Save"), use_container_width=True)

        if submitted:
            selected_index = lang_names.index(selected_lang_name)
            new_lang_code = lang_codes[selected_index]
            config['language'] = new_lang_code
            save_config(config)
            set_feedback(_("Language changed. Please restart the application for the changes to take effect."))
            navigate_to('settings')
            st.rerun()

    if st.button(_("Cancel"), use_container_width=True):
        navigate_to('settings')

def view_report_specific_day():
    """
    Renders the form to generate a daily report for a specific date.
    """
    render_header(_("Daily Report (Specific Day)"))
    
    with st.form("specific_day_form"):
        selected_date = st.date_input(_("Select Date"), value=datetime.now(), format="YYYY-MM-DD")
        submitted = st.form_submit_button(_("Generate Report"), use_container_width=True)
        
        if submitted:
            report = st.session_state.tracker.generate_daily_report(selected_date)
            st.session_state.context['report'] = report
            navigate_to('view_report')
            st.rerun()
            
    if st.button(_("Back"), use_container_width=True):
        navigate_to('reporting')

def view_report_date_range():
    """
    Renders the form to generate a report for a date range.
    """
    render_header(_("Date Range Report"))
    
    with st.form("date_range_form"):
        col1, col2 = st.columns(2)
        with col1:
            start_date = st.date_input(_("Start Date"), value=datetime.now(), format="YYYY-MM-DD")
        with col2:
            end_date = st.date_input(_("End Date"), value=datetime.now(), format="YYYY-MM-DD")
            
        submitted = st.form_submit_button(_("Generate Report"), use_container_width=True)
        
        if submitted:
            if start_date > end_date:
                st.error(_("Error: The start date cannot be after the end date."))
            else:
                report = st.session_state.tracker.generate_date_range_report(start_date, end_date)
                st.session_state.context['report'] = report
                navigate_to('view_report')
                st.rerun()
            
    if st.button(_("Back"), use_container_width=True):
        navigate_to('reporting')

def view_report_detailed_task():
    """
    Renders the first step of the detailed task report: selecting the project.
    """
    render_header(_("Detailed Task Report"), _("Step 1: Select Project"))
    
    main_projects = st.session_state.tracker.list_main_projects(status_filter='all')
    if not main_projects:
        st.info(_("No projects found."))
        if st.button(_("Back"), use_container_width=True):
            navigate_to('reporting')
        return

    main_options = [p['main_project_name'] for p in main_projects]
    selected_main = st.selectbox(_("Select Project"), main_options)
    
    if st.button(_("Next"), use_container_width=True):
        st.session_state.context['selected_main'] = selected_main
        navigate_to('report_detailed_task_select_task')
        st.rerun()
        
    if st.button(_("Back"), use_container_width=True):
        navigate_to('reporting')

def view_report_detailed_task_select_task():
    """
    Renders the second step of the detailed task report: selecting the task.
    """
    main_project = st.session_state.context.get('selected_main')
    if not main_project:
        set_feedback(_("No project selected. Please start again."), "error")
        navigate_to('report_detailed_task')
        st.rerun()
        return

    render_header(_("Detailed Task Report"), f"{_('Step 2: Select Task from')} {main_project}")
    
    tasks = st.session_state.tracker.list_tasks(main_project_name=main_project, status_filter='all')
    if not tasks:
        st.info(_("No tasks found for '{name}'.").format(name=main_project))
        if st.button(_("Back"), use_container_width=True):
            navigate_to('report_detailed_task')
        return

    task_options = [t['task_name'] for t in tasks]
    task_status = {t['task_name']: t['status'] for t in tasks}
    
    selected_task = st.selectbox(_("Select Task"), task_options, format_func=lambda x: f"{x} (done)" if task_status.get(x) == 'done' else x)
    if st.button(_("Generate Report"), use_container_width=True):
        report = st.session_state.tracker.generate_task_report(main_project, selected_task)
        st.session_state.context = {'report': report} # Clear context and set report
        navigate_to('view_report')
        st.rerun()
            
    if st.button(_("Back"), use_container_width=True):
        navigate_to('report_detailed_sub')

def view_report_detailed_main():
    """
    Renders the form to generate a detailed report for a project.
    """
    render_header(_("Detailed Project Report"))
    
    main_projects = st.session_state.tracker.list_main_projects(status_filter='all')
    if not main_projects:
        st.info(_("No projects found."))
        if st.button(_("Back"), use_container_width=True):
            navigate_to('reporting')
        return

    main_options = [p['main_project_name'] for p in main_projects]
    
    with st.form("detailed_main_report_form"):
        selected_main = st.selectbox(_("Select Project"), main_options)
        submitted = st.form_submit_button(_("Generate Report"), use_container_width=True)
        if submitted:
            report = st.session_state.tracker.generate_main_project_report(selected_main)
            st.session_state.context = {'report': report}
            navigate_to('view_report')
            st.rerun()
            
    if st.button(_("Back"), use_container_width=True):
        navigate_to('reporting')

def view_report_detailed_daily():
    """
    Renders the form to generate a detailed daily report.
    """
    render_header(_("Detailed Daily Report"))
    
    with st.form("detailed_daily_report_form"):
        selected_date = st.date_input(_("Select Date"), value=datetime.now(), format="YYYY-MM-DD")
        submitted = st.form_submit_button(_("Generate Report"), use_container_width=True)
        
        if submitted:
            report = st.session_state.tracker.generate_detailed_daily_report(selected_date)
            st.session_state.context = {'report': report}
            navigate_to('view_report')
            st.rerun()
            
    if st.button(_("Back"), use_container_width=True):
        navigate_to('reporting')

def view_report_display():
    """
    Renders the generated report content.
    """
    render_header(_("Report Result"))
    report = st.session_state.context.get('report', '')
    # The report is a Markdown string. Wrapping it inside an HTML div with
    # unsafe_allow_html=True prevents Streamlit from rendering the Markdown.
    # By passing the report string directly to st.markdown, it will be correctly parsed and displayed.
    st.markdown(report)
    if st.button(_("Back"), use_container_width=True): navigate_to('reporting')

# --- Generic List/Action View Helper ---
# To avoid creating 50 separate functions, we can use a generic pattern for simple lists/actions
# But for clarity in this example, I'll implement a few key ones and placeholders for others.

def view_generic_placeholder(title):
    """
    Renders a placeholder view for features not yet implemented in the GUI.

    :param title: The title of the placeholder view.
    """
    render_header(title)
    st.info("This feature is available in the CLI. GUI implementation coming soon.")
    if st.button(_("Back"), use_container_width=True): 
        # Logic to go back
        if 'task' in st.session_state.menu: navigate_to('task_mgmt')
        elif 'main_project' in st.session_state.menu: navigate_to('main_project_mgmt')
        elif 'report' in st.session_state.menu: navigate_to('reporting')
        elif 'settings' in st.session_state.menu: navigate_to('settings')
        else: navigate_to('main')

# --- Main Router ---

menu_map = {
    'main': view_main,
    'task_planning': view_task_planning,
    'today_view': view_today_tasks, # New view for today's tasks
    'project_management': view_project_management,
    'main_project_mgmt': view_main_project_mgmt,
    'task_mgmt': view_task_mgmt,
    'reporting': view_reporting,
    'settings': view_settings,
    
    # Actions
    'add_main_project': view_add_main_project,
    'list_main_projects': view_list_main_projects,
    'start_work': view_start_work,
    'show_current_work': view_show_current_work,
    'settings_port': view_settings_port,
    'view_report': view_report_display,
    
    'rename_main_project': view_rename_main_project,
    'close_main_project': view_close_main_project,
    'reopen_main_project': view_reopen_main_project,
    'delete_main_project': view_delete_main_project,
    'list_inactive_main': view_list_inactive_main_projects,
    'demote_main_project': view_demote_main_project,
    'list_completed_main': view_list_completed_main,
    
    'add_task': view_add_task,
    'add_task_form': view_add_task_form,
    'list_tasks': view_list_tasks,
    'edit_task': view_edit_task,
    'edit_task_select_task': view_edit_task_select_task,
    'edit_task_form': view_edit_task_form,
    'rename_task': view_rename_task,
    'close_task': view_close_task,
    'reopen_task': view_reopen_task,
    'delete_task': view_delete_task,
    'move_task': view_move_task,
    'list_inactive_tasks': view_list_inactive_tasks,
    'list_closed_tasks': view_list_closed_tasks,
    'delete_all_closed_tasks': view_delete_all_closed_tasks,
    'promote_task_to_project': view_promote_task,
    
    'report_specific_day': view_report_specific_day,
    'report_date_range': view_report_date_range,
    'report_detailed_task': view_report_detailed_task,
    'report_detailed_task_select_task': view_report_detailed_task_select_task,
    'report_detailed_main': view_report_detailed_main,
    'report_detailed_daily': view_report_detailed_daily,
    
    'settings_language': view_settings_language,
    'settings_restore': view_settings_restore,
    'settings_storage': view_settings_storage,
    'settings_css': view_settings_css,
    'settings_view_mode': view_settings_view_mode,
}

# --- Execution ---

if st.session_state.menu in menu_map:
    menu_map[st.session_state.menu]()
else:
    st.error(f"Menu '{st.session_state.menu}' not found.")
    if st.button("Reset"):
        navigate_to('main')
        st.rerun()

# --- Sidebar Footer ---
with st.sidebar:
    st.markdown("---")
    st.caption("Time Control © 2026")
    st.caption(f"Port: {get_config().get('streamlit_port', 8501)}")