import os
import sys
import json

# Import the translation function `_` which is initialized in i18n.py on first import.
# This must be done before other local modules are imported.
from i18n import _

from TimeTracker import TimeTracker
from datetime import datetime, timedelta

try:
    from update import check_for_updates, download_update, install_update

    UPDATE_AVAILABLE = True
except ImportError:
    UPDATE_AVAILABLE = False


def _handle_project_management(tt):
    """Handles the project management submenu."""
    while True:
        print(_("\n--- Project Management ---"))
        print(_("1.  Add Main Project"))
        print(_("2.  List Main Projects"))
        print(_("3.  Rename Main Project"))
        print(_("4.  Delete Main Project"))
        print(_("5.  List Inactive Main Projects"))
        print("--------------------------------")
        print(_("6.  Add Sub-Project"))
        print(_("7.  List Sub-Projects"))
        print(_("8.  Rename Sub-Project"))
        print(_("9.  Delete Sub-Project"))
        print(_("10. Move Sub-Project"))
        print(_("11. List Inactive Sub-Projects"))
        print(_("12. Promote Sub-Project to Main-Project"))
        print(_("13. Demote Main-Project to Sub-Project"))
        print("--------------------------------")
        print(_("0.  Back to Main Menu"))
        print("--------------------------------")

        choice = input(_("Choice: "))

        if choice == '1':
            # Funktion 1: Add new main project
            print(_("\n--- Add New Main Project ---"))
            main_project_name = input(_('Name of the main project: '))
            tt.add_main_project(main_project_name)
            print(_("Main project '{name}' added.").format(name=main_project_name))
        elif choice == '2':
            # Funktion 2: List main projects
            print(_("\n--- List Main Projects ---"))
            projects = tt.list_main_projects()
            if projects:
                for i, project in enumerate(projects, 1):
                    print(f"{i}. {project}")
            else:
                print(_("No main projects found."))
        elif choice == '3':
            # Funktion 4: Rename Main Project
            print(_("\n--- Rename Main Project ---"))
            main_projects = tt.list_main_projects()
            if not main_projects:
                print(_("No main projects to rename."))
                continue

            print(_("Select a main project to rename:"))
            for i, project_name in enumerate(main_projects, 1):
                print(f"{i}. {project_name}")

            try:
                choice_num = int(input(_("Enter the number of the main project: ")))
                old_name = main_projects[choice_num - 1]

                new_name = input(_("Enter the new name for '{name}': ").format(name=old_name))

                if tt.rename_main_project(old_name, new_name):
                    print(_("Main project '{old_name}' successfully renamed to '{new_name}'.").format(old_name=old_name, new_name=new_name))
                else:
                    print(_("Error: Could not rename. The new name '{new_name}' might already exist.").format(new_name=new_name))
            except (ValueError, IndexError):
                print(_("Invalid input. Please enter a valid number."))
        elif choice == '4':
            # Funktion 5: Delete Main Project
            print(_("\n--- Delete Main Project ---"))
            main_projects = tt.list_main_projects()
            if not main_projects:
                print(_("No main projects to delete."))
                continue

            print(_("Select a main project to delete:"))

            for i, project_name in enumerate(main_projects, 1):
                print(f"{i}. {project_name}")

            try:
                main_project_choice = int(input(_("Enter the number of the main project: ")))
                main_project_name = main_projects[main_project_choice - 1]
                if tt.delete_main_project(main_project_name):
                    print(_("Main project '{name}' deleted.").format(name=main_project_name))
                else:
                    print(_("Error: Main project '{name}' not found.").format(name=main_project_name))
            except (ValueError, IndexError):
                print(_("Invalid input. Please enter a valid number."))
        elif choice == '5':
            # Funktion 3: List inactive main-projects
            print(_("\n--- List Inactive Main-Projects ---"))
            try:
                inactive_weeks = int(input(_("Enter the number of weeks without activity (e.g., 8): ")))
                if inactive_weeks < 1:
                    print(_("Please enter a positive number."))
                    continue

                inactive_list = tt.list_inactive_main_projects(inactive_weeks)

                if inactive_list:
                    print(_("\nInactive Main-Projects (>{weeks} weeks):").format(weeks=inactive_weeks))
                    for item in inactive_list:
                        print(_("  - Main Project: {name}").format(name=item['main_project']))
                        print(_("    Last Activity: {date}").format(date=item['last_activity']))
                        print("-" * 20)
                else:
                    print(_("No main-projects found inactive for more than {weeks} weeks.").format(weeks=inactive_weeks))

            except ValueError:
                print(_("Invalid input. Please enter a valid number for weeks."))
        elif choice == '6':
            # Funktion 6: Add new sub-project
            print(_("\n--- Add New Sub-Project ---"))
            main_projects = tt.list_main_projects()
            if not main_projects:
                print(_("No main projects found. Please add one first."))
                continue

            print(_("Select a main project to add a sub-project to:"))
            for i, project_name in enumerate(main_projects, 1):
                print(f"{i}. {project_name}")

            try:
                main_project_choice = int(input(_("Enter the number of the main project: ")))
                main_project_name = main_projects[main_project_choice - 1]

                sub_project_name = input(_('Name of the new sub-project: '))
                if tt.add_sub_project(main_project_name, sub_project_name):
                    print(_("Sub-project '{sub_name}' added to '{main_name}'.").format(sub_name=sub_project_name, main_name=main_project_name))
                else:
                    print(_("Error: Main project '{name}' not found.").format(name=main_project_name))
            except (ValueError, IndexError):
                print(_("Invalid input. Please enter a valid number."))
        elif choice == '7':
            # Funktion 7: List Sub-Projects
            print(_("\n--- List Sub-Projects ---"))
            main_projects = tt.list_main_projects()
            if not main_projects:
                print(_("No main projects found. Cannot list sub-projects."))
                continue

            print(_("Select the main project whose sub-projects you want to list:"))
            for i, project_name in enumerate(main_projects, 1):
                print(f"{i}. {project_name}")

            try:
                main_project_choice = int(input(_("Enter the number of the main project: ")))
                main_project_name = main_projects[main_project_choice - 1]
                sub_projects = tt.list_sub_projects(main_project_name)
                if sub_projects:
                    print(_("Sub-projects for '{name}':").format(name=main_project_name))
                    for i, sub_project in enumerate(sub_projects, 1):
                        print(f"{i}. {sub_project}")
                else:
                    print(_("No sub-projects found for '{name}'.").format(name=main_project_name))
            except (ValueError, IndexError):
                print(_("Invalid input. Please enter a valid number."))
        elif choice == '8':
            # Funktion 9: Rename Sub-Project
            print(_("\n--- Rename Sub-Project ---"))
            main_projects = tt.list_main_projects()
            if not main_projects:
                print(_("No main projects found."))
                continue

            print(_("Select the main project:"))
            for i, project_name in enumerate(main_projects, 1):
                print(f"{i}. {project_name}")

            try:
                main_project_choice = int(input(_("Enter the number of the main project: ")))
                main_project_name = main_projects[main_project_choice - 1]

                sub_projects = tt.list_sub_projects(main_project_name)
                if not sub_projects:
                    print(_("No sub-projects to rename for '{name}'.").format(name=main_project_name))
                    continue

                print(_("Select a sub-project from '{name}' to rename:").format(name=main_project_name))
                for i, sub_project_name in enumerate(sub_projects, 1):
                    print(f"{i}. {sub_project_name}")

                sub_project_choice = int(input(_("Enter the number of the sub-project: ")))
                old_sub_project_name = sub_projects[sub_project_choice - 1]

                new_sub_project_name = input(_("Enter the new name for '{name}': ").format(name=old_sub_project_name))

                if tt.rename_sub_project(main_project_name, old_sub_project_name, new_sub_project_name):
                    print(_("Sub-project '{old_name}' renamed to '{new_name}'.").format(old_name=old_sub_project_name, new_name=new_sub_project_name))
                else:
                    print(_("Error: Could not rename. The new name might already exist or the project was not found."))
            except (ValueError, IndexError):
                print(_("Invalid input. Please enter a valid number."))
        elif choice == '9':
            # Funktion 10: Delete Sub-Project
            print(_("\n--- Delete Sub-Project ---"))
            main_projects = tt.list_main_projects()
            if not main_projects:
                print(_("No main projects found."))
                continue

            print(_("Select the main project:"))

            for i, project_name in enumerate(main_projects, 1):
                print(f"{i}. {project_name}")

            try:
                main_project_choice = int(input(_("Enter the number of the main project: ")))
                main_project_name = main_projects[main_project_choice - 1]

                sub_projects = tt.list_sub_projects(main_project_name)
                if not sub_projects:
                    print(_("No sub-projects to delete for '{name}'.").format(name=main_project_name))
                    continue

                print(_("Select a sub-project from '{name}' to delete:").format(name=main_project_name))
                for i, sub_project_name in enumerate(sub_projects, 1):
                    print(f"{i}. {sub_project_name}")

                sub_project_choice = int(input(_("Enter the number of the sub-project: ")))
                sub_project_name = sub_projects[sub_project_choice - 1]

                if tt.delete_sub_project(main_project_name, sub_project_name):
                    print(_("Sub-project '{sub_name}' deleted from '{main_name}'.").format(sub_name=sub_project_name, main_name=main_project_name))
                else:
                    print(_("Error: Main project or sub-project not found."))

            except (ValueError, IndexError):
                print(_("Invalid input. Please enter a valid number."))
        elif choice == '11':
            # Funktion 8: List Inactive Sub-Projects
            print(_("\n--- List Inactive Sub-Projects ---"))
            try:
                inactive_weeks = int(input(_("Enter the number of weeks without activity (e.g., 4): ")))
                if inactive_weeks < 1:
                    print(_("Please enter a positive number."))
                    continue

                inactive_list = tt.list_inactive_sub_projects(inactive_weeks)

                if inactive_list:
                    print(_("\nInactive Sub-Projects (>{weeks} weeks):").format(weeks=inactive_weeks))
                    for item in inactive_list:
                        print(_("  - Main Project: {name}").format(name=item['main_project']))
                        print(_("    Sub-Project: {name}").format(name=item['sub_project']))
                        print(_("    Last Activity: {date}").format(date=item['last_activity']))
                        print("-" * 20)
                else:
                    print(_("No sub-projects found inactive for more than {weeks} weeks.").format(weeks=inactive_weeks))

            except ValueError:
                print(_("Invalid input. Please enter a valid number for weeks."))
        elif choice == '10':
            # New Funktion: Move Sub-Project
            print(_("\n--- Move Sub-Project ---"))
            main_projects = tt.list_main_projects()
            if len(main_projects) < 2:
                print(_("You need at least two main projects to move a sub-project."))
                continue

            try:
                # 1. Select source main project
                print(_("Select the source main project:"))
                for i, project_name in enumerate(main_projects, 1):
                    print(f"{i}. {project_name}")
                source_choice = int(input(_("Enter the number of the source main project: ")))
                source_main_project = main_projects[source_choice - 1]

                # 2. Select sub-project to move
                sub_projects = tt.list_sub_projects(source_main_project)
                if not sub_projects:
                    print(_("No sub-projects to move from '{name}'.").format(name=source_main_project))
                    continue

                print(_("\nSelect a sub-project from '{name}' to move:").format(name=source_main_project))
                for i, sub_project_name in enumerate(sub_projects, 1):
                    print(f"{i}. {sub_project_name}")
                sub_project_choice = int(input(_("Enter the number of the sub-project: ")))
                sub_project_to_move = sub_projects[sub_project_choice - 1]

                # 3. Select destination main project
                print(_("\nSelect the destination main project:"))
                # Filter out the source project from the list of possible destinations
                dest_options = [p for p in main_projects if p != source_main_project]
                for i, project_name in enumerate(dest_options, 1):
                    print(f"{i}. {project_name}")
                dest_choice = int(input(_("Enter the number of the destination main project: ")))
                dest_main_project = dest_options[dest_choice - 1]

                # 4. Perform the move
                success, message = tt.move_sub_project(source_main_project, sub_project_to_move, dest_main_project)
                if success:
                    print(_("Successfully moved '{sub_name}' from '{source_name}' to '{dest_name}'.").format(sub_name=sub_project_to_move, source_name=source_main_project, dest_name=dest_main_project))
                else:
                    print(_("Error: {message}").format(message=message))

            except (ValueError, IndexError):
                print(_("Invalid input. Please enter a valid number."))

        elif choice == '12':
            # New Funktion: Promote Sub-Project
            print(_("\n--- Promote Sub-Project to Main-Project ---"))
            main_projects = tt.list_main_projects()
            if not main_projects:
                print(_("No main projects found."))
                continue

            try:
                # 1. Select source main project
                print(_("Select the main project containing the sub-project to promote:"))
                for i, project_name in enumerate(main_projects, 1):
                    print(f"{i}. {project_name}")
                source_choice = int(input(_("Enter the number of the main project: ")))
                source_main_project = main_projects[source_choice - 1]

                # 2. Select sub-project to promote
                sub_projects = tt.list_sub_projects(source_main_project)
                if not sub_projects:
                    print(_("No sub-projects to promote in '{name}'.").format(name=source_main_project))
                    continue

                print(_("\nSelect a sub-project from '{name}' to promote:").format(name=source_main_project))
                for i, sub_project_name in enumerate(sub_projects, 1):
                    print(f"{i}. {sub_project_name}")
                sub_project_choice = int(input(_("Enter the number of the sub-project: ")))
                sub_project_to_promote = sub_projects[sub_project_choice - 1]

                # 3. Perform the promotion
                success, message = tt.promote_sub_project(source_main_project, sub_project_to_promote)
                print(message)

            except (ValueError, IndexError):
                print(_("Invalid input. Please enter a valid number."))

        elif choice == '13':
            # New Funktion: Demote Main-Project
            print(_("\n--- Demote Main-Project to Sub-Project ---"))
            main_projects = tt.list_main_projects()
            if len(main_projects) < 2:
                print(_("You need at least two main projects for this operation."))
                continue

            try:
                # 1. Select main project to demote
                print(_("Select the main project to demote:"))
                for i, project_name in enumerate(main_projects, 1):
                    print(f"{i}. {project_name}")
                demote_choice = int(input(_("Enter the number of the project to demote: ")))
                project_to_demote = main_projects[demote_choice - 1]

                # 2. Select new parent main project
                print(_("\nSelect the new parent main project:"))
                parent_options = [p for p in main_projects if p != project_to_demote]
                for i, project_name in enumerate(parent_options, 1):
                    print(f"{i}. {project_name}")
                parent_choice = int(input(_("Enter the number of the new parent project: ")))
                new_parent_project = parent_options[parent_choice - 1]

                # 3. Perform the demotion
                success, message = tt.demote_main_project(project_to_demote, new_parent_project)
                print(message)

            except (ValueError, IndexError):
                print(_("Invalid input. Please enter a valid number."))

        elif choice == '0':
            break
        else:
            print(_("Invalid choice. Please enter a number from 0 to 13."))

def _handle_reporting(tt):
    """Handles the reporting submenu."""
    while True:
        print(_("\n--- Reporting ---"))
        print(_("1. Daily Report (Today)"))
        print(_("2. Daily Report (Specific Day)"))
        print(_("3. Date Range Report"))
        print("--------------------------")
        print(_("0. Back to Main Menu"))
        print("--------------------------")

        choice = input(_("Choice: "))

        if choice == '1':
            # Funktion 14: Generate daily report (Today)
            print(_("\n--- Generate Daily Report (Today) ---"))
            report_text = tt.generate_daily_report()
            print(report_text)
        elif choice == '2':
            # Funktion 15: Generate a daily report for a specific day
            print(_("\n--- Generate Daily Report for a specific Day ---"))
            specific_date_str = input(_("Enter the date (YYYY-MM-DD): "))
            try:
                specific_date = datetime.strptime(specific_date_str, "%Y-%m-%d").date()
                report_text = tt.generate_daily_report(specific_date)
                print(report_text)
            except ValueError:
                print(_("Invalid date format. Please use YYYY-MM-DD."))
        elif choice == '3':
            # Funktion 16: Generate report for a date range
            print(_("\n--- Generate Report for a Date Range ---"))
            start_date_str = input(_("Enter the start date (YYYY-MM-DD): "))
            end_date_str = input(_("Enter the end date (YYYY-MM-DD): "))
            try:
                start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
                end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
                if start_date > end_date:
                    print(_("Error: The start date cannot be after the end date."))
                    continue
                report_text = tt.generate_date_range_report(start_date, end_date)
                print(report_text)
            except ValueError:
                print(_("Invalid date format. Please use YYYY-MM-DD."))
        elif choice == '0':
            break
        else:
            print(_("Invalid choice. Please enter a number from 0 to 3."))

def _handle_language_settings():
    """Handles the language selection submenu."""
    while True:
        print(_("\n--- Language Settings ---"))
        # List available languages by scanning the locale directory
        try:
            # Explicitly define supported languages to control the order and display names
            supported_languages = {'en': 'English', 'de': 'Deutsch', 'cs': 'Čeština'}
            available_languages = {lang: name for lang, name in supported_languages.items() if os.path.isdir(os.path.join('locale', lang))}
        except FileNotFoundError:
            available_languages = {}

        if not available_languages:
            print(_("No additional languages found."))
            return

        for i, lang in enumerate(available_languages, 1):
            print(f"{i}. {lang}")
        print("--------------------------")
        print(_("0. Back to Main Menu"))
        print("--------------------------")

        choice = input(_("Choice: "))
        if choice == '0':
            break
        
        try:
            choice_num = int(choice)
            if 1 <= choice_num <= len(available_languages):
                selected_lang = list(available_languages.keys())[choice_num - 1]
                # Update config.json
                CONFIG_FILE = 'config.json'
                with open(CONFIG_FILE, 'r+') as f:
                    config = json.load(f)
                    config['language'] = selected_lang
                    f.seek(0)
                    json.dump(config, f, indent=4)
                print(_("Language changed to '{lang}'. Please restart the application.").format(lang=selected_lang))
                return # Go back to main menu to encourage restart
            else:
                print(_("Invalid choice."))
        except ValueError:
            print(_("Invalid input. Please enter a number."))

CONFIG_FILE = 'config.json'

def run_menu():
    """
    Starts the interactive menu for the time tracking application.
    """
    # --- Update-Check beim Start ---
    if UPDATE_AVAILABLE and os.path.exists("update.zip"):
        install_update()
        print(_("Application is restarting to complete the update..."))
        os.execv(sys.executable, ['python'] + sys.argv)
        return # Exit after restart

    tt = TimeTracker()    
    tt.initialize_dependencies() # Now this can be called safely.

    while True:
        print(_("\n=== Time Control {version} ===").format(version=tt.get_version()))
        print(_("--- Main Menu ---"))
        print(_("1. Start work on sub-project"))
        print(_("2. Show current work"))
        print(_("3. Stop current work"))
        print("------------------------------------------------")
        print(_("4. Handle projects and sub-projects"))
        print(_("5. Reporting"))
        print(_("6. Settings"))
        print("------------------------------------------------")
        print(_("0. Exit"))
        print("------------------------------------------------")

        choice = input(_("Choice: "))

        if choice == '1':
            # Funktion 11: Start work on sub-project
            print(_("\n--- Start Work on a Sub-Project ---"))
            main_projects = tt.list_main_projects()
            if not main_projects:
                print(_("No main projects found. Please add one first."))
                continue

            print(_("Select a main project:"))

            for i, project_name in enumerate(main_projects, 1):
                print(f"{i}. {project_name}")

            try:
                main_project_choice = int(input(_("Enter the number of the main project: ")))
                main_project_name = main_projects[main_project_choice - 1]

                sub_projects = tt.list_sub_projects(main_project_name)
                if not sub_projects:
                    print(_("No sub-projects found for '{name}'.").format(name=main_project_name))
                    continue

                print(_("Select a sub-project from '{name}':").format(name=main_project_name))

                for i, sub_project_name in enumerate(sub_projects, 1):
                    print(f"{i}. {sub_project_name}")

                sub_project_choice = int(input(_("Enter the number of the sub-project: ")))
                sub_project_name = sub_projects[sub_project_choice - 1]

                if tt.start_work(main_project_name, sub_project_name):
                    print(_("Work started on '{sub_name}' in project '{main_name}'.").format(sub_name=sub_project_name, main_project_name=main_project_name))
                else:
                    print(_("Error starting work."))

            except (ValueError, IndexError):
                print(_("Invalid input. Please enter a valid number."))

        elif choice == '2':
            # Funktion 12: Show current work
            print(_("\n--- Current Active Work ---"))
            current_work = tt.get_current_work()
            if current_work:
                start_time_str = current_work['start_time']
                start_time_dt = datetime.fromisoformat(start_time_str)
                duration = datetime.now() - start_time_dt
                
                hours, remainder = divmod(duration.total_seconds(), 3600)
                minutes, seconds = divmod(remainder, 60)

                print(_("You are currently working on:"))
                print(_("  Main Project: {name}").format(name=current_work['main_project_name']))
                print(_("  Sub-Project:  {name}").format(name=current_work['sub_project_name']))
                print(_("  Started at:   {date}").format(date=start_time_dt.strftime('%Y-%m-%d %H:%M:%S')))
                print(_("  Duration:     {duration}").format(duration=f"{int(hours):02}:{int(minutes):02}:{int(seconds):02}"))
            else:
                print(_("No active work session."))

        elif choice == '3':
            # Funktion 13: Stop current work
            print(_("\n--- Stop Work ---"))
            if tt.stop_work():
                print(_("Work session stopped successfully."))
            else:
                print(_("No active work session to stop."))

        elif choice == '4':
            _handle_project_management(tt)

        elif choice == '5':
            _handle_reporting(tt)

        elif choice == '6':
            _handle_language_settings()
        
        elif choice == '0':
            # --- Update-Check beim Beenden ---
            if UPDATE_AVAILABLE:
                print(_("\nChecking for updates..."))
                is_update, _unused_version, url = check_for_updates(tt.get_version())
                if is_update and url:
                    download_update(url)
            print(_("Exiting application. Goodbye!"))
            break

        else:
            print(_("Invalid choice. Please enter a number from 0 to 6."))

if __name__ == '__main__':
    run_menu()