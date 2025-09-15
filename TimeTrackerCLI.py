from TimeTracker import TimeTracker

def run_menu():
    """
    Starts the interactive menu for the time tracking application.
    """
    tt = TimeTracker()

    while True:
        print("\n--- Time Tracking Menu ---")
        print("1 Add new main project")
        print("2 List main projects")
        print("3 Delete main project")
        print("4 Add new sub-project")
        print("5 List sub-projects")
        print("6 Delete sub-project")
        print("7 Start work on sub-project")
        print("8 Stop current work")
        print("0 Exit")
        print("--------------------------")
        
        choice = input("Choice: ")

        if choice == '1':
            print("\n--- Add New Main Project ---")
            main_project_name = input('Name of the main project: ')
            tt.add_main_project(main_project_name)
            print(f"Main project '{main_project_name}' added.")
            
        elif choice == '2':
            print("\n--- List Main Projects ---")
            projects = tt.list_main_projects()
            if projects:
                for i, project in enumerate(projects, 1):
                    print(f"{i}. {project}")
            else:
                print("No main projects found.")

        elif choice == '3':
            print("\n--- Delete Main Project ---")
            main_projects = tt.list_main_projects()
            if not main_projects:
                print("No main projects found. Cannot delete.")
                continue

            print("Select the main project to delete:")
            for i, project_name in enumerate(main_projects, 1):
                print(f"{i}. {project_name}")
            
            try:
                main_project_choice = int(input("Enter the number of the main project to delete: "))
                main_project_name = main_projects[main_project_choice - 1]
                if tt.delete_main_project(main_project_name):
                    print(f"Main project '{main_project_name}' deleted.")
                else:
                    print(f"Error: Main project '{main_project_name}' not found.")
            except (ValueError, IndexError):
                print("Invalid input. Please enter a valid number.")

        elif choice == '4':
            print("\n--- Add New Sub-Project ---")
            main_projects = tt.list_main_projects()
            if not main_projects:
                print("No main projects found. Please add one first.")
                continue

            print("Select the main project to add a sub-project to:")
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

        elif choice == '5':
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
        
        elif choice == '6':
            print("\n--- Delete Sub-Project ---")
            main_projects = tt.list_main_projects()
            if not main_projects:
                print("No main projects found. Cannot delete sub-projects.")
                continue

            print("Select the main project containing the sub-project to delete:")
            for i, project_name in enumerate(main_projects, 1):
                print(f"{i}. {project_name}")
            
            try:
                main_project_choice = int(input("Enter the number of the main project: "))
                main_project_name = main_projects[main_project_choice - 1]
                
                sub_projects = tt.list_sub_projects(main_project_name)
                if not sub_projects:
                    print(f"No sub-projects found for '{main_project_name}'. Cannot delete.")
                    continue
                
                print(f"Select the sub-project to delete from '{main_project_name}':")
                for i, sub_project_name in enumerate(sub_projects, 1):
                    print(f"{i}. {sub_project_name}")
                
                sub_project_choice = int(input("Enter the number of the sub-project to delete: "))
                sub_project_name = sub_projects[sub_project_choice - 1]
                
                if tt.delete_sub_project(main_project_name, sub_project_name):
                    print(f"Sub-project '{sub_project_name}' deleted from '{main_project_name}'.")
                else:
                    print(f"Error: Sub-project '{sub_project_name}' not found in '{main_project_name}'.")
            except (ValueError, IndexError):
                print("Invalid input. Please enter a valid number.")

        elif choice == '7':
            print("\n--- Start Work on a Sub-Project ---")
            main_projects = tt.list_main_projects()
            if not main_projects:
                print("No main projects found. Please add one first.")
                continue

            print("Select the main project:")
            for i, project_name in enumerate(main_projects, 1):
                print(f"{i}. {project_name}")
            
            try:
                main_project_choice = int(input("Enter the number of the main project: "))
                main_project_name = main_projects[main_project_choice - 1]

                sub_projects = tt.list_sub_projects(main_project_name)
                if not sub_projects:
                    print(f"No sub-projects found for '{main_project_name}'.")
                    continue
                
                print(f"Select the sub-project from '{main_project_name}':")
                for i, sub_project_name in enumerate(sub_projects, 1):
                    print(f"{i}. {sub_project_name}")

                sub_project_choice = int(input("Enter the number of the sub-project: "))
                sub_project_name = sub_projects[sub_project_choice - 1]

                if tt.start_work(main_project_name, sub_project_name):
                    print(f"Work started on '{sub_project_name}' in project '{main_project_name}'.")
                else:
                    print(f"Error starting work on '{sub_project_name}'. A previous session might still be active or project not found.")

            except (ValueError, IndexError):
                print("Invalid input. Please enter a valid number.")

        elif choice == '8':
            print("\n--- Stop Work ---")
            if tt.stop_work():
                print("Work session stopped successfully.")
            else:
                print("No active work session to stop.")

        elif choice == '0':
            print("Exiting application. Goodbye!")
            break
            
        else:
            print("Invalid choice. Please enter a number from 0 to 8.")

if __name__ == '__main__':
    run_menu()