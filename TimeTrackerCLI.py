from TimeTracker import TimeTracker
from datetime import datetime, timedelta

def run_menu():
    """
    Starts the interactive menu for the time tracking application.
    """
    tt = TimeTracker()

    while True:
        print("\n--- Time Tracking Menu ---")
        print("1  Add new main project")
        print("2  List main projects")
        print("3  List inactive main-projects") # NEU an Position 3
        print("4  Rename main project")        # NEU
        print("5  Delete main project")        # Verschiebung von 4
        print("------------------------------------------------")
        print("6  Add new sub-project")        # Verschiebung von 5
        print("7  List sub-projects")          # Verschiebung von 6
        print("8  List inactive sub-projects") # Verschiebung von 7
        print("9  Rename sub-project")         # Verschiebung von 8
        print("10 Delete sub-project")         # Verschiebung von 9
        print("------------------------------------------------")
        print("11 Start work on sub-project")  
        print("12 Stop current work")          
        print("------------------------------------------------")
        print("13 Generate daily report (Today)") 
        print("14 Generate a daily report for a specific day") 
        print("15 Generate report for a date range")
        print("------------------------------------------------")
        print("0 Exit")
        print("------------------------------------------------")
        
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
            # NEUE FUNKTION an Position 3: List inactive main-projects (War 8)
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

        elif choice == '4':
            # NEUE FUNKTION an Position 4: Rename Main Project
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

        elif choice == '5':
            # Funktion 5: Delete Main Project (War 4)
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

        elif choice == '6':
            # Funktion 6: Add new sub-project (War 5)
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
            # Funktion 7: List Sub-Projects (War 6)
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
            # Funktion 8: List Inactive Sub-Projects (War 7)
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

        elif choice == '9':
            # Funktion 9: Rename Sub-Project (War 8)
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

        elif choice == '10':
            # Funktion 10: Delete Sub-Project (War 9)
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
            # Funktion 11: Start work on sub-project (War 10)
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

        elif choice == '12':
            # Funktion 12: Stop current work (War 11)
            print("\n--- Stop Work ---")
            if tt.stop_work():
                print("Work session stopped successfully.")
            else:
                print("No active work session to stop.")

        elif choice == '13':
            # Funktion 13: Generate daily report (Today) (War 12)
            print("\n--- Generate Daily Report (Today) ---")
            report_text = tt.generate_daily_report()
            print(report_text)

        elif choice == '14':
            # Funktion 14: Generate a daily report for a specific day (War 13)
            print("\n--- Generate Daily Report for a specific Day ---")
            specific_date_str = input("Enter the date (YYYY-MM-DD): ")
            try:
                specific_date = datetime.strptime(specific_date_str, "%Y-%m-%d").date()
                report_text = tt.generate_daily_report(specific_date)
                print(report_text)
            except ValueError:
                print("Invalid date format. Please use YYYY-MM-DD.")

        elif choice == '15':
            # Funktion 15: Generate report for a date range (War 14)
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
            print("Exiting application. Goodbye!")
            break
            
        else:
            print("Invalid choice. Please enter a number from 0 to 15.")

if __name__ == '__main__':
    run_menu()