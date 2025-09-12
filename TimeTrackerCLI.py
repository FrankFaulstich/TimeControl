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
        print("0 Exit")
        print("--------------------------")
        
        choice = input("Choice: ") # 'auswahl' wurde durch 'choice' ersetzt

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
            main_project_name = input('Name of the main project to delete: ')
            if tt.delete_main_project(main_project_name):
                print(f"Main project '{main_project_name}' deleted.")
            else:
                print(f"Error: Main project '{main_project_name}' not found.")
                
        elif choice == '4':
            print("\n--- Add New Sub-Project ---")
            main_project_name = input('Name of the main project to add a sub-project to: ')
            sub_project_name = input('Name of the new sub-project: ')
            if tt.add_sub_project(main_project_name, sub_project_name):
                print(f"Sub-project '{sub_project_name}' added to '{main_project_name}'.")
            else:
                print(f"Error: Main project '{main_project_name}' not found.")

        elif choice == '5':
            print("\n--- List Sub-Projects ---")
            main_project_name = input('Name of the main project whose sub-projects you want to list: ')
            sub_projects = tt.list_sub_projects(main_project_name)
            if sub_projects:
                print(f"Sub-projects for '{main_project_name}':")
                for i, sub_project in enumerate(sub_projects, 1):
                    print(f"{i}. {sub_project}")
            else:
                print(f"Main project '{main_project_name}' not found or has no sub-projects.")
        
        elif choice == '6':
            print("\n--- Delete Sub-Project ---")
            main_project_name = input('Name of the main project: ')
            sub_project_name = input('Name of the sub-project to delete: ')
            if tt.delete_sub_project(main_project_name, sub_project_name):
                print(f"Sub-project '{sub_project_name}' deleted from '{main_project_name}'.")
            else:
                print(f"Error: Main project or sub-project not found.")

        elif choice == '0':
            print("Exiting application. Goodbye!")
            break
            
        else:
            print("Invalid choice. Please enter a number from 0 to 6.")

if __name__ == '__main__':
    run_menu()
