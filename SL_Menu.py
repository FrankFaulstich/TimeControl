import streamlit as st
import json
import os
from datetime import datetime
from TimeTracker import TimeTracker
from i18n import _

# --- Configuration & Setup ---
CONFIG_FILE = 'config.json'

st.set_page_config(
    page_title="Time Control",
    page_icon="⏱️",
    layout="centered",
    initial_sidebar_state="collapsed"
)

# Minimalistic CSS for macOS-like feel
st.markdown("""
<style>
    .stApp {
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    }
    h1, h2, h3 {
        font-weight: 600;
        color: #1d1d1f;
    }
    .stButton button {
        border-radius: 8px;
        font-weight: 500;
    }
    .report-box {
        background-color: #f5f5f7;
        padding: 15px;
        border-radius: 10px;
        font-family: 'Menlo', monospace;
        font-size: 0.9em;
        white-space: pre-wrap;
        border: 1px solid #e1e1e1;
    }
    .menu-option {
        padding: 8px 0;
        border-bottom: 1px solid #f0f0f0;
    }
</style>
""", unsafe_allow_html=True)

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
    st.session_state.menu = menu_name
    st.session_state.feedback = None
    st.rerun()
    
def set_feedback(message, type='success'):
    st.session_state.feedback = {'message': message, 'type': type}

def get_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return {}

def save_config(config):
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=4)

# --- UI Components ---

def render_header(title, subtitle=None):
    st.title(title)
    if subtitle:
        st.caption(subtitle)
    if st.session_state.feedback:
        f = st.session_state.feedback
        if f['type'] == 'success': st.success(f['message'])
        elif f['type'] == 'info': st.info(f['message'])
        elif f['type'] == 'error': st.error(f['message'])
        st.session_state.feedback = None # Clear after showing

# --- Views ---

def view_main():
    render_header("Time Control", f"Version {st.session_state.tracker.get_version()}")
    
    if st.button(f"1. {_('Start work on sub-project')}", use_container_width=True):
        navigate_to('start_work_select_main')
    if st.button(f"2. {_('Show current work')}", use_container_width=True):
        navigate_to('show_current_work')
    if st.button(f"3. {_('Stop current work')}", use_container_width=True):
        if st.session_state.tracker.stop_work():
            set_feedback(_("Work session stopped successfully."))
        else:
            set_feedback(_("No active work session to stop."), 'info')
        st.rerun()

    st.divider()

    if st.button(f"4. {_('Handle projects and sub-projects')}", use_container_width=True):
        navigate_to('project_management')
    if st.button(f"5. {_('Reporting')}", use_container_width=True):
        navigate_to('reporting')
    if st.button(f"6. {_('Settings')}", use_container_width=True):
        navigate_to('settings')

    st.divider()
    
    if st.button(f"0. {_('Exit')}", use_container_width=True):
        st.stop()

def view_project_management():
    render_header(_("Project Management"))
    
    if st.button(f"1. {_('Main Project Management')}", use_container_width=True):
        navigate_to('main_project_mgmt')
    if st.button(f"2. {_('Sub-Project Management')}", use_container_width=True):
        navigate_to('sub_project_mgmt')
    
    st.divider()
    
    if st.button(f"0. {_('Back to Main Menu')}", use_container_width=True):
        navigate_to('main')

def view_main_project_mgmt():
    render_header(_("Main Project Management"))
    
    if st.button(f"1. {_('Add Main Project')}", use_container_width=True): navigate_to('add_main_project')
    if st.button(f"2. {_('List Main Projects')}", use_container_width=True): navigate_to('list_main_projects')
    if st.button(f"3. {_('Rename Main Project')}", use_container_width=True): navigate_to('rename_main_project')
    if st.button(f"4. {_('Close Main Project')}", use_container_width=True): navigate_to('close_main_project')
    if st.button(f"5. {_('Re-open Main Project')}", use_container_width=True): navigate_to('reopen_main_project')
    if st.button(f"6. {_('Delete Main Project')}", use_container_width=True): navigate_to('delete_main_project')
    if st.button(f"7. {_('List Inactive Main Projects')}", use_container_width=True): navigate_to('list_inactive_main')
    if st.button(f"8. {_('Demote Main-Project to Sub-Project')}", use_container_width=True): navigate_to('demote_main_project')
    if st.button(f"9. {_('List Completed Main Projects')}", use_container_width=True): navigate_to('list_completed_main')
    
    st.divider()
    
    if st.button(f"0. {_('Back')}", use_container_width=True):
        navigate_to('project_management')

def view_sub_project_mgmt():
    render_header(_("Sub-Project Management"))
    
    if st.button(f"1. {_('Add Sub-Project')}", use_container_width=True): navigate_to('add_sub_project')
    if st.button(f"2. {_('List Sub-Projects')}", use_container_width=True): navigate_to('list_sub_projects')
    if st.button(f"3. {_('Rename Sub-Project')}", use_container_width=True): navigate_to('rename_sub_project')
    if st.button(f"4. {_('Close Sub-Project')}", use_container_width=True): navigate_to('close_sub_project')
    if st.button(f"5. {_('Re-open Sub-Project')}", use_container_width=True): navigate_to('reopen_sub_project')
    if st.button(f"6. {_('Delete Sub-Project')}", use_container_width=True): navigate_to('delete_sub_project')
    if st.button(f"7. {_('Move Sub-Project')}", use_container_width=True): navigate_to('move_sub_project')
    if st.button(f"8. {_('List Inactive Sub-Projects')}", use_container_width=True): navigate_to('list_inactive_sub')
    if st.button(f"9. {_('List All Closed Sub-Projects')}", use_container_width=True): navigate_to('list_closed_sub')
    if st.button(f"10. {_('Delete All Closed Sub-Projects')}", use_container_width=True): navigate_to('delete_all_closed_sub')
    if st.button(f"11. {_('Promote Sub-Project to Main-Project')}", use_container_width=True): navigate_to('promote_sub_project')
    
    st.divider()
    
    if st.button(f"0. {_('Back')}", use_container_width=True):
        navigate_to('project_management')

def view_reporting():
    render_header(_("Reporting"))
    tt = st.session_state.tracker
    
    if st.button(f"1. {_('Daily Report (Today)')}", use_container_width=True):
        report = tt.generate_daily_report()
        st.session_state.context['report'] = report
        navigate_to('view_report')
        st.rerun()
    if st.button(f"2. {_('Daily Report (Specific Day)')}", use_container_width=True): navigate_to('report_specific_day')
    if st.button(f"3. {_('Date Range Report')}", use_container_width=True): navigate_to('report_date_range')
    if st.button(f"4. {_('Detailed Sub-Project Report')}", use_container_width=True): navigate_to('report_detailed_sub')
    if st.button(f"5. {_('Detailed Main-Project Report')}", use_container_width=True): navigate_to('report_detailed_main')
    if st.button(f"6. {_('Detailed Daily Report')}", use_container_width=True): navigate_to('report_detailed_daily')
    
    st.divider()
    
    if st.button(f"0. {_('Back to Main Menu')}", use_container_width=True):
        navigate_to('main')

def view_settings():
    render_header(_("Settings"))
    
    if st.button(f"1. {_('Change Language')}", use_container_width=True): navigate_to('settings_language')
    if st.button(f"2. {_('Restore Previous Version')}", use_container_width=True): navigate_to('settings_restore')
    if st.button(f"3. {_('Change Data Storage Location')}", use_container_width=True): navigate_to('settings_storage')
    if st.button(f"4. {_('Change Streamlit Port')}", use_container_width=True): navigate_to('settings_port')
    
    st.divider()
    
    if st.button(f"0. {_('Back to Main Menu')}", use_container_width=True):
        navigate_to('main')

# --- Action Views (Forms) ---

def view_add_main_project():
    render_header(_("Add New Main Project"))
    with st.form("add_main_form"):
        name = st.text_input(_("Name of the main project"))
        submitted = st.form_submit_button(_("Add Project"), use_container_width=True)
        if submitted and name:
            st.session_state.tracker.add_main_project(name)
            set_feedback(_("Main project '{name}' added.").format(name=name))
            st.session_state.menu = 'main_project_mgmt'
            st.rerun()
    if st.button(_("Cancel"), use_container_width=True):
        navigate_to('main_project_mgmt')

def view_close_sub_project():
    render_header(_("Close Sub-Project"))
    
    main_projects = st.session_state.tracker.list_main_projects(status_filter='open')
    if not main_projects:
        st.info(_("No open main projects found."))
        if st.button(_("Back"), use_container_width=True):
            navigate_to('sub_project_mgmt')
        return

    main_options = [p['main_project_name'] for p in main_projects]
    selected_main = st.selectbox(_("Select Main Project"), main_options)
    
    sub_projects = st.session_state.tracker.list_sub_projects(main_project_name=selected_main, status_filter='open')
    
    if not sub_projects:
        st.info(_("No open sub-projects to close in '{name}'.").format(name=selected_main))
        if st.button(_("Back"), use_container_width=True):
            navigate_to('sub_project_mgmt')
        return

    sub_options = [sp['sub_project_name'] for sp in sub_projects]

    with st.form("close_sub_form"):
        selected_sub = st.selectbox(_("Select Sub-Project"), sub_options)
        submitted = st.form_submit_button(_("Close Sub-Project"), use_container_width=True)
        
        if submitted:
            if st.session_state.tracker.close_sub_project(selected_main, selected_sub):
                set_feedback(_("Sub-project '{sub_name}' in '{main_name}' has been closed.").format(sub_name=selected_sub, main_name=selected_main))
                navigate_to('sub_project_mgmt')
                st.rerun()
            else:
                st.error(_("Error: Main project or sub-project not found."))

    if st.button(_("Cancel"), use_container_width=True):
        navigate_to('sub_project_mgmt')

def view_reopen_sub_project():
    render_header(_("Re-open Sub-Project"))
    
    main_projects = st.session_state.tracker.list_main_projects(status_filter='open')
    if not main_projects:
        st.info(_("No open main projects found."))
        if st.button(_("Back"), use_container_width=True):
            navigate_to('sub_project_mgmt')
        return

    main_options = [p['main_project_name'] for p in main_projects]
    selected_main = st.selectbox(_("Select Main Project"), main_options)
    
    sub_projects = st.session_state.tracker.list_sub_projects(main_project_name=selected_main, status_filter='closed')
    
    if not sub_projects:
        st.info(_("No closed sub-projects to reopen in '{name}'.").format(name=selected_main))
        if st.button(_("Back"), use_container_width=True):
            navigate_to('sub_project_mgmt')
        return

    sub_options = [sp['sub_project_name'] for sp in sub_projects]

    with st.form("reopen_sub_form"):
        selected_sub = st.selectbox(_("Select Sub-Project"), sub_options)
        submitted = st.form_submit_button(_("Re-open Sub-Project"), use_container_width=True)
        
        if submitted:
            if st.session_state.tracker.reopen_sub_project(selected_main, selected_sub):
                set_feedback(_("Sub-project '{sub_name}' in '{main_name}' has been reopened.").format(sub_name=selected_sub, main_name=selected_main))
                navigate_to('sub_project_mgmt')
                st.rerun()
            else:
                st.error(_("Error: Main project or sub-project not found."))

    if st.button(_("Cancel"), use_container_width=True):
        navigate_to('sub_project_mgmt')

def view_delete_sub_project():
    render_header(_("Delete Sub-Project"))
    
    main_projects = st.session_state.tracker.list_main_projects(status_filter='open')
    if not main_projects:
        st.info(_("No open main projects found."))
        if st.button(_("Back"), use_container_width=True):
            navigate_to('sub_project_mgmt')
        return

    main_options = [p['main_project_name'] for p in main_projects]
    selected_main = st.selectbox(_("Select Main Project"), main_options)
    
    sub_projects = st.session_state.tracker.list_sub_projects(main_project_name=selected_main, status_filter='open')
    
    if not sub_projects:
        st.info(_("No open sub-projects to delete in '{name}'.").format(name=selected_main))
        if st.button(_("Back"), use_container_width=True):
            navigate_to('sub_project_mgmt')
        return

    sub_options = [sp['sub_project_name'] for sp in sub_projects]

    with st.form("delete_sub_form"):
        selected_sub = st.selectbox(_("Select Sub-Project"), sub_options)
        st.warning(_("This action cannot be undone."))
        submitted = st.form_submit_button(_("Delete Sub-Project"), use_container_width=True)
        
        if submitted:
            if st.session_state.tracker.delete_sub_project(selected_main, selected_sub):
                set_feedback(_("Sub-project '{sub_name}' deleted from '{main_name}'.").format(sub_name=selected_sub, main_name=selected_main))
                navigate_to('sub_project_mgmt')
                st.rerun()
            else:
                st.error(_("Error: Main project or sub-project not found."))

    if st.button(_("Cancel"), use_container_width=True):
        navigate_to('sub_project_mgmt')

def view_move_sub_project():
    render_header(_("Move Sub-Project"))
    
    main_projects = st.session_state.tracker.list_main_projects(status_filter='open')
    if not main_projects:
        st.info(_("No open main projects found."))
        if st.button(_("Back"), use_container_width=True):
            navigate_to('sub_project_mgmt')
        return

    main_options = [p['main_project_name'] for p in main_projects]
    source_main = st.selectbox(_("Select Source Main Project"), main_options)
    
    sub_projects = st.session_state.tracker.list_sub_projects(main_project_name=source_main, status_filter='all')
    
    if not sub_projects:
        st.info(_("No sub-projects found in '{name}'.").format(name=source_main))
        if st.button(_("Back"), use_container_width=True):
            navigate_to('sub_project_mgmt')
        return

    sub_options = [sp['sub_project_name'] for sp in sub_projects]
    target_options = [p for p in main_options if p != source_main]
    
    if not target_options:
        st.warning(_("No other main projects available to move to."))
        if st.button(_("Back"), use_container_width=True):
            navigate_to('sub_project_mgmt')
        return

    with st.form("move_sub_form"):
        selected_sub = st.selectbox(_("Select Sub-Project"), sub_options)
        target_main = st.selectbox(_("Select Target Main Project"), target_options)
        
        submitted = st.form_submit_button(_("Move Sub-Project"), use_container_width=True)
        
        if submitted:
            if st.session_state.tracker.move_sub_project(source_main, selected_sub, target_main):
                set_feedback(_("Sub-project '{sub}' moved from '{src}' to '{dst}'.").format(sub=selected_sub, src=source_main, dst=target_main))
                navigate_to('sub_project_mgmt')
                st.rerun()
            else:
                st.error(_("Error: Could not move sub-project."))

    if st.button(_("Cancel"), use_container_width=True):
        navigate_to('sub_project_mgmt')

def view_list_inactive_sub_projects():
    render_header(_("List Inactive Sub-Projects"))
    
    weeks = st.number_input(_("Weeks of inactivity"), min_value=1, value=4, step=1)
    
    inactive_list = st.session_state.tracker.list_inactive_sub_projects(weeks)
    
    if inactive_list:
        st.markdown(_("Inactive Sub-Projects (> {weeks} weeks):").format(weeks=weeks))
        for item in inactive_list:
            st.markdown(f"- **{item['main_project']}** / {item['sub_project']}")
            st.caption(f"{_('Last Activity')}: {item['last_activity']}")
    else:
        st.info(_("No sub-projects found inactive for more than {weeks} weeks.").format(weeks=weeks))
        
    if st.button(_("Back"), use_container_width=True):
        navigate_to('sub_project_mgmt')

def view_list_closed_sub_projects():
    render_header(_("List All Closed Sub-Projects"))
    
    main_projects = st.session_state.tracker.list_main_projects(status_filter='all')
    found_any = False
    
    if main_projects:
        for mp in main_projects:
            mp_name = mp['main_project_name']
            closed_subs = st.session_state.tracker.list_sub_projects(main_project_name=mp_name, status_filter='closed')
            if closed_subs:
                found_any = True
                st.markdown(f"**{mp_name}**")
                for sp in closed_subs:
                    st.markdown(f"- {sp['sub_project_name']}")
    
    if not found_any:
        st.info(_("No closed sub-projects found."))
        
    if st.button(_("Back"), use_container_width=True):
        navigate_to('sub_project_mgmt')

def view_promote_sub_project():
    render_header(_("Promote Sub-Project to Main-Project"))
    
    main_projects = st.session_state.tracker.list_main_projects(status_filter='open')
    if not main_projects:
        st.info(_("No open main projects found."))
        if st.button(_("Back"), use_container_width=True):
            navigate_to('sub_project_mgmt')
        return

    main_options = [p['main_project_name'] for p in main_projects]
    selected_main = st.selectbox(_("Select Main Project"), main_options)
    
    sub_projects = st.session_state.tracker.list_sub_projects(main_project_name=selected_main, status_filter='open')
    
    if not sub_projects:
        st.info(_("No open sub-projects to promote in '{name}'.").format(name=selected_main))
        if st.button(_("Back"), use_container_width=True):
            navigate_to('sub_project_mgmt')
        return

    sub_options = [sp['sub_project_name'] for sp in sub_projects]

    with st.form("promote_sub_form"):
        selected_sub = st.selectbox(_("Select Sub-Project"), sub_options)
        st.info(_("This will create a new Main Project with the sub-project's name and move all time entries to a 'General' sub-project within it."))
        
        submitted = st.form_submit_button(_("Promote to Main Project"), use_container_width=True)
        
        if submitted:
            success, message = st.session_state.tracker.promote_sub_project(selected_main, selected_sub)
            if success:
                set_feedback(message)
                navigate_to('sub_project_mgmt')
                st.rerun()
            else:
                st.error(f"Error: {message}")

    if st.button(_("Cancel"), use_container_width=True):
        navigate_to('sub_project_mgmt')

def view_list_main_projects():
    render_header(_("List Main Projects"))
    projects = st.session_state.tracker.list_main_projects(status_filter='all')
    if projects:
        for p in projects:
            status = f"({_('closed')})" if p['status'] == 'closed' else ""
            st.markdown(f"- **{p['main_project_name']}** {status}")
    else:
        st.info(_("No main projects found."))
    if st.button(_("Back"), use_container_width=True):
        navigate_to('main_project_mgmt')

def view_rename_main_project():
    render_header(_("Rename Main Project"))
    projects = st.session_state.tracker.list_main_projects(status_filter='open')
    
    if not projects:
        st.info(_("No open main projects to rename."))
        if st.button(_("Back"), use_container_width=True):
            navigate_to('main_project_mgmt')
        return

    options = [p['main_project_name'] for p in projects]
    
    with st.form("rename_main_form"):
        selected_project = st.selectbox(_("Select Main Project"), options)
        new_name = st.text_input(_("New Name"))
        submitted = st.form_submit_button(_("Rename"), use_container_width=True)
        
        if submitted:
            if not new_name:
                st.error(_("Please enter a new name."))
            elif new_name == selected_project:
                st.warning(_("New name is the same as the old name."))
            elif st.session_state.tracker.rename_main_project(selected_project, new_name):
                set_feedback(_("Main project '{old_name}' successfully renamed to '{new_name}'.").format(old_name=selected_project, new_name=new_name))
                navigate_to('main_project_mgmt')
                st.rerun()
            else:
                st.error(_("Error: Could not rename. The new name '{new_name}' might already exist.").format(new_name=new_name))

    if st.button(_("Cancel"), use_container_width=True):
        navigate_to('main_project_mgmt')

def view_list_sub_projects():
    render_header(_("List Sub-Projects"))
    
    main_projects = st.session_state.tracker.list_main_projects(status_filter='all')
    if not main_projects:
        st.info(_("No main projects found."))
        if st.button(_("Back"), use_container_width=True):
            navigate_to('sub_project_mgmt')
        return

    options = [p['main_project_name'] for p in main_projects]
    selected_main = st.selectbox(_("Select Main Project"), options)
    
    sub_projects = st.session_state.tracker.list_sub_projects(main_project_name=selected_main, status_filter='all')
    
    if sub_projects:
        st.markdown(_("Sub-projects for '{name}':").format(name=selected_main))
        for sp in sub_projects:
            name = sp['sub_project_name']
            status = f"({_('closed')})" if sp['status'] == 'closed' else ""
            st.markdown(f"- {name} {status}")
    else:
        st.info(_("No sub-projects found for '{name}'.").format(name=selected_main))
        
    if st.button(_("Back"), use_container_width=True):
        navigate_to('sub_project_mgmt')

def view_rename_sub_project():
    render_header(_("Rename Sub-Project"))
    
    main_projects = st.session_state.tracker.list_main_projects(status_filter='open')
    if not main_projects:
        st.info(_("No open main projects found."))
        if st.button(_("Back"), use_container_width=True):
            navigate_to('sub_project_mgmt')
        return

    main_options = [p['main_project_name'] for p in main_projects]
    selected_main = st.selectbox(_("Select Main Project"), main_options)
    
    sub_projects = st.session_state.tracker.list_sub_projects(main_project_name=selected_main, status_filter='open')
    
    if not sub_projects:
        st.info(_("No open sub-projects to rename in '{name}'.").format(name=selected_main))
        if st.button(_("Back"), use_container_width=True):
            navigate_to('sub_project_mgmt')
        return

    sub_options = [sp['sub_project_name'] for sp in sub_projects]

    with st.form("rename_sub_form"):
        selected_sub = st.selectbox(_("Select Sub-Project"), sub_options)
        new_name = st.text_input(_("New Name"))
        submitted = st.form_submit_button(_("Rename"), use_container_width=True)
        
        if submitted:
            if not new_name:
                st.error(_("Please enter a new name."))
            elif new_name == selected_sub:
                st.warning(_("New name is the same as the old name."))
            elif st.session_state.tracker.rename_sub_project(selected_main, selected_sub, new_name):
                set_feedback(_("Sub-project '{old_name}' renamed to '{new_name}'.").format(old_name=selected_sub, new_name=new_name))
                navigate_to('sub_project_mgmt')
                st.rerun()
            else:
                st.error(_("Error: Could not rename. The new name might already exist."))

    if st.button(_("Cancel"), use_container_width=True):
        navigate_to('sub_project_mgmt')
def view_close_main_project():
    render_header(_("Close Main Project"))
    projects = st.session_state.tracker.list_main_projects(status_filter='open')
    
    if not projects:
        st.info(_("No open main projects to close."))
        if st.button(_("Back"), use_container_width=True):
            navigate_to('main_project_mgmt')
        return

    options = [p['main_project_name'] for p in projects]
    
    with st.form("close_main_form"):
        selected_project = st.selectbox(_("Select Main Project"), options)
        submitted = st.form_submit_button(_("Close Project"), use_container_width=True)
        
        if submitted:
            if st.session_state.tracker.close_main_project(selected_project):
                set_feedback(_("Main project '{name}' has been closed.").format(name=selected_project))
                navigate_to('main_project_mgmt')
                st.rerun()
            else:
                st.error(_("Error: Main project not found."))

    if st.button(_("Cancel"), use_container_width=True):
        navigate_to('main_project_mgmt')

def view_reopen_main_project():
    render_header(_("Re-open Main Project"))
    projects = st.session_state.tracker.list_main_projects(status_filter='closed')
    
    if not projects:
        st.info(_("No closed main projects to reopen."))
        if st.button(_("Back"), use_container_width=True):
            navigate_to('main_project_mgmt')
        return

    options = [p['main_project_name'] for p in projects]
    
    with st.form("reopen_main_form"):
        selected_project = st.selectbox(_("Select Main Project"), options)
        submitted = st.form_submit_button(_("Re-open Project"), use_container_width=True)
        
        if submitted:
            if st.session_state.tracker.reopen_main_project(selected_project):
                set_feedback(_("Main project '{name}' has been reopened.").format(name=selected_project))
                navigate_to('main_project_mgmt')
                st.rerun()
            else:
                st.error(_("Error: Main project not found."))

    if st.button(_("Cancel"), use_container_width=True):
        navigate_to('main_project_mgmt')

def view_add_sub_project_select_main():
    render_header(_("Add Sub-Project"), _("Step 1: Select Main Project"))
    projects = st.session_state.tracker.list_main_projects(status_filter='open')
    if not projects:
        st.warning(_("No open main projects found. Please add one first."))
        if st.button(_("Back"), use_container_width=True): navigate_to('sub_project_mgmt')
        return

    options = [p['main_project_name'] for p in projects]
    selected = st.selectbox(_("Main Project"), options)
    
    if st.button(_("Next"), use_container_width=True):
        st.session_state.context['selected_main'] = selected
        navigate_to('add_sub_project_form')
        st.rerun()
    if st.button(_("Cancel"), use_container_width=True): navigate_to('sub_project_mgmt')

def view_add_sub_project_form():
    main_project = st.session_state.context.get('selected_main')
    if not main_project:
        set_feedback(_("No main project selected. Please start again."), "error")
        navigate_to('add_sub_project')
        st.rerun()
        return

    render_header(_("Add Sub-Project"), f"{_('To Main Project:')} {main_project}")
    
    existing_subs = [s['sub_project_name'].lower() for s in st.session_state.tracker.list_sub_projects(main_project_name=main_project)]

    with st.form("add_sub_form"):
        name = st.text_input(_("Name of the new sub-project"))
        submitted = st.form_submit_button(_("Add Sub-Project"), use_container_width=True)
        if submitted and name:
            if name.lower() in existing_subs:
                set_feedback(_("A sub-project with this name already exists in this main project."), 'error')
                st.rerun()
            elif st.session_state.tracker.add_sub_project(main_project, name):
                set_feedback(_("Sub-project '{sub_name}' added to '{main_name}'.").format(sub_name=name, main_name=main_project))
                st.session_state.context = {}
                navigate_to('sub_project_mgmt')
                st.rerun()
            else:
                set_feedback(_("Error: Could not find main project '{name}'.").format(name=main_project), 'error')
                st.rerun()

    if st.button(_("Cancel"), use_container_width=True): 
        st.session_state.context = {}
        navigate_to('sub_project_mgmt')

def view_start_work_select_main():
    render_header(_("Start Work"), _("Select Main Project"))
    projects = st.session_state.tracker.list_main_projects(status_filter='open')
    if not projects:
        st.warning(_("No open main projects found."))
        if st.button(_("Back"), use_container_width=True): navigate_to('main')
        return

    # Use selectbox for GUI friendliness, but we could use input
    options = [p['main_project_name'] for p in projects]
    selected = st.selectbox(_("Main Project"), options)
    
    if st.button(_("Next"), use_container_width=True):
        st.session_state.context['selected_main'] = selected
        navigate_to('start_work_select_sub')
        st.rerun()
    if st.button(_("Cancel"), use_container_width=True): navigate_to('main')

def view_start_work_select_sub():
    main_project = st.session_state.context.get('selected_main')
    render_header(_("Start Work"), f"{_('Select Sub-Project from')} {main_project}")
    
    subs = st.session_state.tracker.list_sub_projects(main_project_name=main_project, status_filter='open')
    if not subs:
        st.warning(_("No open sub-projects found."))
        if st.button(_("Back"), use_container_width=True): navigate_to('start_work_select_main')
        return

    options = [s['sub_project_name'] for s in subs]
    selected = st.selectbox(_("Sub-Project"), options)

    if st.button(_("Start Work"), use_container_width=True):
        if st.session_state.tracker.start_work(main_project, selected):
            set_feedback(_("Work started on '{sub_name}' in project '{main_name}'.").format(sub_name=selected, main_name=main_project))
            navigate_to('main')
            st.rerun()
    if st.button(_("Cancel"), use_container_width=True): navigate_to('main')

def view_show_current_work():
    render_header(_("Current Active Work"))
    current = st.session_state.tracker.get_current_work()
    if current:
        start_time = datetime.fromisoformat(current['start_time'])
        duration = datetime.now() - start_time
        hours, remainder = divmod(duration.total_seconds(), 3600)
        minutes, seconds = divmod(remainder, 60)
        
        st.markdown(f"**{_('Main Project')}:** {current['main_project_name']}")
        st.markdown(f"**{_('Sub-Project')}:** {current['sub_project_name']}")
        st.markdown(f"**{_('Started at')}:** {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        st.markdown(f"**{_('Duration')}:** {int(hours):02}:{int(minutes):02}:{int(seconds):02}")
    else:
        st.info(_("No active work session."))
    
    if st.button(_("Back"), use_container_width=True): navigate_to('main')

def view_settings_port():
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

def view_report_display():
    render_header(_("Report Result"))
    report = st.session_state.context.get('report', '')
    st.markdown(f'<div class="report-box">{report}</div>', unsafe_allow_html=True)
    if st.button(_("Back"), use_container_width=True): navigate_to('reporting')

# --- Generic List/Action View Helper ---
# To avoid creating 50 separate functions, we can use a generic pattern for simple lists/actions
# But for clarity in this example, I'll implement a few key ones and placeholders for others.

def view_generic_placeholder(title):
    render_header(title)
    st.info("This feature is available in the CLI. GUI implementation coming soon.")
    if st.button(_("Back"), use_container_width=True): 
        # Simple logic to go back up one level
        if 'sub_project' in st.session_state.menu: navigate_to('sub_project_mgmt')
        elif 'main_project' in st.session_state.menu: navigate_to('main_project_mgmt')
        elif 'report' in st.session_state.menu: navigate_to('reporting')
        elif 'settings' in st.session_state.menu: navigate_to('settings')
        else: navigate_to('main')

# --- Main Router ---

menu_map = {
    'main': view_main,
    'project_management': view_project_management,
    'main_project_mgmt': view_main_project_mgmt,
    'sub_project_mgmt': view_sub_project_mgmt,
    'reporting': view_reporting,
    'settings': view_settings,
    
    # Actions
    'add_main_project': view_add_main_project,
    'list_main_projects': view_list_main_projects,
    'start_work_select_main': view_start_work_select_main,
    'start_work_select_sub': view_start_work_select_sub,
    'show_current_work': view_show_current_work,
    'settings_port': view_settings_port,
    'view_report': view_report_display,
    
    'rename_main_project': view_rename_main_project,
    'close_main_project': view_close_main_project,
    'reopen_main_project': view_reopen_main_project,
    'delete_main_project': lambda: view_generic_placeholder(_("Delete Main Project")),
    'list_inactive_main': lambda: view_generic_placeholder(_("List Inactive Main Projects")),
    'demote_main_project': lambda: view_generic_placeholder(_("Demote Main-Project")),
    'list_completed_main': lambda: view_generic_placeholder(_("List Completed Main Projects")),
    
    'add_sub_project': view_add_sub_project_select_main,
    'add_sub_project_form': view_add_sub_project_form,
    'list_sub_projects': view_list_sub_projects,
    'rename_sub_project': view_rename_sub_project,
    'close_sub_project': view_close_sub_project,
    'reopen_sub_project': view_reopen_sub_project,
    'delete_sub_project': view_delete_sub_project,
    'move_sub_project': view_move_sub_project,
    'list_inactive_sub': view_list_inactive_sub_projects,
    'list_closed_sub': view_list_closed_sub_projects,
    'delete_all_closed_sub': lambda: view_generic_placeholder(_("Delete All Closed Sub-Projects")),
    'promote_sub_project': view_promote_sub_project,
    
    'report_specific_day': lambda: view_generic_placeholder(_("Daily Report (Specific Day)")),
    'report_date_range': lambda: view_generic_placeholder(_("Date Range Report")),
    'report_detailed_sub': lambda: view_generic_placeholder(_("Detailed Sub-Project Report")),
    'report_detailed_main': lambda: view_generic_placeholder(_("Detailed Main-Project Report")),
    'report_detailed_daily': lambda: view_generic_placeholder(_("Detailed Daily Report")),
    
    'settings_language': lambda: view_generic_placeholder(_("Change Language")),
    'settings_restore': lambda: view_generic_placeholder(_("Restore Previous Version")),
    'settings_storage': lambda: view_generic_placeholder(_("Change Data Storage Location")),
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