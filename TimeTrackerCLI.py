from TimeTracker import TimeTracker
from datetime import datetime, timedelta

def _handle_project_management(tt):
    """Handles the project management submenu."""
    while True:
        print("\n--- Project Management ---")
        print("1.  Add Main Project")
        print("2.  List Main Projects")
        print("3.  Rename Main Project")
        print("4.  Delete Main Project")
        print("5.  List Inactive Main Projects")
        print("--------------------------------")
        print("6.  Add Sub-Project")
        print("7.  List Sub-Projects")
        print("8.  Rename Sub-Project")
        print("9.  Delete Sub-Project")
        print("10. Move Sub-Project")
        print("11. List Inactive Sub-Projects")
        print("--------------------------------")
        print("0.  Back to Main Menu")
        print("--------------------------------")
        
        choice = input("Choice: ")

        if choice == '1':
            # Funktion 1: Add new main project
            print("\n--- Add New Main Project ---")
            main_project_name = input('Name of the main project: ')
            tt.add_main_project(main_project_name)
            print(f"Main project '{main_project_name}' added.")
        elif choice == '2':
            # Funktion 2: List main projects
            print("\n--- List Main Projects ---")
            projects = tt.list_main_projects()
            if projects:
                for i, project in enumerate(projects, 1):
                    print(f"{i}. {project}")
            else:
                print("No main projects found.")
        elif choice == '3':
            # Funktion 4: Rename Main Project
            print("\n--- Rename Main Project ---")
            main_projects = tt.list_main_projects()
            if not main_projects:
                print("No main projects to rename.")
                continue

            print("Select a main project to rename:")
            for i, project_name in enumerate(main_projects, 1):
                print(f"{i}. {project_name}")
            
            try:
                choice_num = int(input("Enter the number of the main project: "))
                old_name = main_projects[choice_num - 1]
                
                new_name = input(f"Enter the new name for '{old_name}': ")

                if tt.rename_main_project(old_name, new_name):
                    print(f"Main project '{old_name}' successfully renamed to '{new_name}'.")
                else:
                    print(f"Error: Could not rename. The new name '{new_name}' might already exist.")
            except (ValueError, IndexError):
                print("Invalid input. Please enter a valid number.")
        elif choice == '4':
            # Funktion 5: Delete Main Project
            print("\n--- Delete Main Project ---")
            main_projects = tt.list_main_projects()
            if not main_projects:
                print("No main projects to delete.")
                continue

            print("Select a main project to delete:")

            for i, project_name in enumerate(main_projects, 1):
                print(f"{i}. {project_name}")
            
            try:
                main_project_choice = int(input("Enter the number of the main project: "))
                main_project_name = main_projects[main_project_choice - 1]
                if tt.delete_main_project(main_project_name):
                    print(f"Main project '{main_project_name}' deleted.")
                else:
                    print(f"Error: Main project '{main_project_name}' not found.")
            except (ValueError, IndexError):
                print("Invalid input. Please enter a valid number.")
        elif choice == '5':
            # Funktion 3: List inactive main-projects
            print("\n--- List Inactive Main-Projects ---")
            try:
                inactive_weeks = int(input("Enter the number of weeks without activity (e.g., 8): "))
                if inactive_weeks < 1:
                    print("Please enter a positive number.")
                    continue
                
                inactive_list = tt.list_inactive_main_projects(inactive_weeks)
                
                if inactive_list:
                    print(f"\nInactive Main-Projects (>{inactive_weeks} weeks):")
                    for item in inactive_list:
                        print(f"  - Main Project: {item['main_project']}")
                        print(f"    Last Activity: {item['last_activity']}")
                        print("-" * 20)
                else:
                    print(f"No main-projects found inactive for more than {inactive_weeks} weeks.")

            except ValueError:
                print("Invalid input. Please enter a valid number for weeks.")
        elif choice == '6':
            # Funktion 6: Add new sub-project
            print("\n--- Add New Sub-Project ---")
            main_projects = tt.list_main_projects()
            if not main_projects:
                print("No main projects found. Please add one first.")
                continue
            
            print("Select a main project to add a sub-project to:")
            for i, project_name in enumerate(main_projects, 1):
                print(f"{i}. {project_name}")

            try:
                main_project_choice = int(input("Enter the number of the main project: "))
                main_project_name = main_projects[main_project_choice - 1]
                
                sub_project_name = input('Name of the new sub-project: ')
                if tt.add_sub_project(main_project_name, sub_project_name):
                    print(f"Sub-project '{sub_project_name}' added to '{main_project_name}'.")
                else:
                    print(f"Error: Main project '{main_project_name}' not found.")
            except (ValueError, IndexError):
                print("Invalid input. Please enter a valid number.")
        elif choice == '7':
            # Funktion 7: List Sub-Projects
            print("\n--- List Sub-Projects ---")
            main_projects = tt.list_main_projects()
            if not main_projects:
                print("No main projects found. Cannot list sub-projects.")
                continue

            print("Select the main project whose sub-projects you want to list:")
            for i, project_name in enumerate(main_projects, 1):
                print(f"{i}. {project_name}")
            
            try:
                main_project_choice = int(input("Enter the number of the main project: "))
                main_project_name = main_projects[main_project_choice - 1]
                sub_projects = tt.list_sub_projects(main_project_name)
                if sub_projects:
                    print(f"Sub-projects for '{main_project_name}':")
                    for i, sub_project in enumerate(sub_projects, 1):
                        print(f"{i}. {sub_project}")
                else:
                    print(f"No sub-projects found for '{main_project_name}'.")
            except (ValueError, IndexError):
                print("Invalid input. Please enter a valid number.")
        elif choice == '8':
            # Funktion 9: Rename Sub-Project
            print("\n--- Rename Sub-Project ---")
            main_projects = tt.list_main_projects()
            if not main_projects:
                print("No main projects found.")
                continue

            print("Select the main project:")
            for i, project_name in enumerate(main_projects, 1):
                print(f"{i}. {project_name}")
            
            try:
                main_project_choice = int(input("Enter the number of the main project: "))
                main_project_name = main_projects[main_project_choice - 1]

                sub_projects = tt.list_sub_projects(main_project_name)
                if not sub_projects:
                    print(f"No sub-projects to rename for '{main_project_name}'.")
                    continue
                
                print(f"Select a sub-project from '{main_project_name}' to rename:")
                for i, sub_project_name in enumerate(sub_projects, 1):
                    print(f"{i}. {sub_project_name}")

                sub_project_choice = int(input("Enter the number of the sub-project: "))
                old_sub_project_name = sub_projects[sub_project_choice - 1]
                
                new_sub_project_name = input(f"Enter the new name for '{old_sub_project_name}': ")

                if tt.rename_sub_project(main_project_name, old_sub_project_name, new_sub_project_name):
                    print(f"Sub-project '{old_sub_project_name}' renamed to '{new_sub_project_name}'.")
                else:
                    print(f"Error: Could not rename. The new name might already exist or the project was not found.")
            except (ValueError, IndexError):
                print("Invalid input. Please enter a valid number.")
        elif choice == '9':
            # Funktion 10: Delete Sub-Project
            print("\n--- Delete Sub-Project ---")
            main_projects = tt.list_main_projects()
            if not main_projects:
                print("No main projects found.")
                continue

            print("Select the main project:")

            for i, project_name in enumerate(main_projects, 1):
                print(f"{i}. {project_name}")
            
            try:
                main_project_choice = int(input("Enter the number of the main project: "))
                main_project_name = main_projects[main_project_choice - 1]

                sub_projects = tt.list_sub_projects(main_project_name)
                if not sub_projects:
                    print(f"No sub-projects to delete for '{main_project_name}'.")
                    continue
                
                print(f"Select a sub-project from '{main_project_name}' to delete:")
                for i, sub_project_name in enumerate(sub_projects, 1):
                    print(f"{i}. {sub_project_name}")

                sub_project_choice = int(input("Enter the number of the sub-project: "))
                sub_project_name = sub_projects[sub_project_choice - 1]

                if tt.delete_sub_project(main_project_name, sub_project_name):
                    print(f"Sub-project '{sub_project_name}' deleted from '{main_project_name}'.")
                else:
                    print(f"Error: Main project or sub-project not found.")

            except (ValueError, IndexError):
                print("Invalid input. Please enter a valid number.")
        elif choice == '11':
            # Funktion 8: List Inactive Sub-Projects
            print("\n--- List Inactive Sub-Projects ---")
            try:
                inactive_weeks = int(input("Enter the number of weeks without activity (e.g., 4): "))
                if inactive_weeks < 1:
                    print("Please enter a positive number.")
                    continue
                
                inactive_list = tt.list_inactive_sub_projects(inactive_weeks)
                
                if inactive_list:
                    print(f"\nInactive Sub-Projects (>{inactive_weeks} weeks):")
                    for item in inactive_list:
                        print(f"  - Main Project: {item['main_project']}")
                        print(f"    Sub-Project: {item['sub_project']}")
                        print(f"    Last Activity: {item['last_activity']}")
                        print("-" * 20)
                else:
                    print(f"No sub-projects found inactive for more than {inactive_weeks} weeks.")

            except ValueError:
                print("Invalid input. Please enter a valid number for weeks.")
        elif choice == '10':
            # New Funktion: Move Sub-Project
            print("\n--- Move Sub-Project ---")
            main_projects = tt.list_main_projects()
            if len(main_projects) < 2:
                print("You need at least two main projects to move a sub-project.")
                continue

            try:
                # 1. Select source main project
                print("Select the source main project:")
                for i, project_name in enumerate(main_projects, 1):
                    print(f"{i}. {project_name}")
                source_choice = int(input("Enter the number of the source main project: "))
                source_main_project = main_projects[source_choice - 1]

                # 2. Select sub-project to move
                sub_projects = tt.list_sub_projects(source_main_project)
                if not sub_projects:
                    print(f"No sub-projects to move from '{source_main_project}'.")
                    continue
                
                print(f"\nSelect a sub-project from '{source_main_project}' to move:")
                for i, sub_project_name in enumerate(sub_projects, 1):
                    print(f"{i}. {sub_project_name}")
                sub_project_choice = int(input("Enter the number of the sub-project: "))
                sub_project_to_move = sub_projects[sub_project_choice - 1]

                # 3. Select destination main project
                print("\nSelect the destination main project:")
                # Filter out the source project from the list of possible destinations
                dest_options = [p for p in main_projects if p != source_main_project]
                for i, project_name in enumerate(dest_options, 1):
                    print(f"{i}. {project_name}")
                dest_choice = int(input("Enter the number of the destination main project: "))
                dest_main_project = dest_options[dest_choice - 1]

                # 4. Perform the move
                success, message = tt.move_sub_project(source_main_project, sub_project_to_move, dest_main_project)
                if success:
                    print(f"Successfully moved '{sub_project_to_move}' from '{source_main_project}' to '{dest_main_project}'.")
                else:
                    print(f"Error: {message}")

            except (ValueError, IndexError):
                print("Invalid input. Please enter a valid number.")

        elif choice == '0':
            break
        else:
            print("Invalid choice. Please enter a number from 0 to 11.")

def _handle_reporting(tt):
    """Handles the reporting submenu."""
    while True:
        print("\n--- Reporting ---")
        print("1. Daily Report (Today)")
        print("2. Daily Report (Specific Day)")
        print("3. Date Range Report")
        print("--------------------------")
        print("0. Back to Main Menu")
        print("--------------------------")

        choice = input("Choice: ")

        if choice == '1':
            # Funktion 14: Generate daily report (Today)
            print("\n--- Generate Daily Report (Today) ---")
            report_text = tt.generate_daily_report()
            print(report_text)
        elif choice == '2':
            # Funktion 15: Generate a daily report for a specific day
            print("\n--- Generate Daily Report for a specific Day ---")
            specific_date_str = input("Enter the date (YYYY-MM-DD): ")
            try:
                specific_date = datetime.strptime(specific_date_str, "%Y-%m-%d").date()
                report_text = tt.generate_daily_report(specific_date)
                print(report_text)
            except ValueError:
                print("Invalid date format. Please use YYYY-MM-DD.")
        elif choice == '3':
            # Funktion 16: Generate report for a date range
            print("\n--- Generate Report for a Date Range ---")
            start_date_str = input("Enter the start date (YYYY-MM-DD): ")
            end_date_str = input("Enter the end date (YYYY-MM-DD): ")
            try:
                start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
                end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
                if start_date > end_date:
                    print("Error: The start date cannot be after the end date.")
                    continue
                report_text = tt.generate_date_range_report(start_date, end_date)
                print(report_text)
            except ValueError:
                print("Invalid date format. Please use YYYY-MM-DD.")
        elif choice == '0':
            break
        else:
            print("Invalid choice. Please enter a number from 0 to 3.")

def run_menu():
    """
    Starts the interactive menu for the time tracking application.
    """
    tt = TimeTracker()

    while True:
        print("\n=== Time Control " + tt.get_version() + " ===")
        print("--- Main Menu ---")
        print("1. Start work on sub-project")
        print("2. Show current work")
        print("3. Stop current work")
        print("------------------------------------------------")
        print("4. Handle projects and sub-projects")
        print("5. Reporting")
        print("------------------------------------------------")
        print("0. Exit")
        print("------------------------------------------------")
        
        choice = input("Choice: ")

        if choice == '1':
            # Funktion 11: Start work on sub-project
            print("\n--- Start Work on a Sub-Project ---")
            main_projects = tt.list_main_projects()
            if not main_projects:
                print("No main projects found. Please add one first.")
                continue

            print("Select a main project:")

            for i, project_name in enumerate(main_projects, 1):
                print(f"{i}. {project_name}")
            
            try:
                main_project_choice = int(input("Enter the number of the main project: "))
                main_project_name = main_projects[main_project_choice - 1]

                sub_projects = tt.list_sub_projects(main_project_name)
                if not sub_projects:
                    print(f"No sub-projects found for '{main_project_name}'.")
                    continue
                
                print(f"Select a sub-project from '{main_project_name}':")

                for i, sub_project_name in enumerate(sub_projects, 1):
                    print(f"{i}. {sub_project_name}")

                sub_project_choice = int(input("Enter the number of the sub-project: "))
                sub_project_name = sub_projects[sub_project_choice - 1]

                if tt.start_work(main_project_name, sub_project_name):
                    print(f"Work started on '{sub_project_name}' in project '{main_project_name}'.")
                else:
                    print("Error starting work.")

            except (ValueError, IndexError):
                print("Invalid input. Please enter a valid number.")

        elif choice == '2':
            # Funktion 12: Show current work
            print("\n--- Current Active Work ---")
            current_work = tt.get_current_work()
            if current_work:
                start_time_str = current_work['start_time']
                start_time_dt = datetime.fromisoformat(start_time_str)
                duration = datetime.now() - start_time_dt
                
                hours, remainder = divmod(duration.total_seconds(), 3600)
                minutes, seconds = divmod(remainder, 60)

                print(f"You are currently working on:")
                print(f"  Main Project: {current_work['main_project_name']}")
                print(f"  Sub-Project:  {current_work['sub_project_name']}")
                print(f"  Started at:   {start_time_dt.strftime('%Y-%m-%d %H:%M:%S')}")
                print(f"  Duration:     {int(hours):02}:{int(minutes):02}:{int(seconds):02}")
            else:
                print("No active work session.")

        elif choice == '3':
            # Funktion 13: Stop current work
            print("\n--- Stop Work ---")
            if tt.stop_work():
                print("Work session stopped successfully.")
            else:
                print("No active work session to stop.")

        elif choice == '4':
            _handle_project_management(tt)

        elif choice == '5':
            _handle_reporting(tt)
        
        elif choice == '0':
            print("Exiting application. Goodbye!")
            break
            
        else:
            print("Invalid choice. Please enter a number from 0 to 5.")

if __name__ == '__main__':
    run_menu()