import TimeTracker 

tt = TimeTracker.TimeTracker()

print('Menü')
print('1 Neues Hauptprojekt anlegen')
print('2 Hauptprojekte auflisten')
print('3 Hauptprojekt löschen')


i = input('Auswahl: ')

if i == '1':
    print('Neues Hauptprojekt anlegen')
    main_project_name = input('Name des Hauptprojekts: ')
    tt.add_main_project(main_project_name)
elif i == '2':
    print('Hauptprojekte auflisten')
    print(tt.list_main_projects())
elif i == '3':
    print('Hauptprojekt löschen')
    main_project_name = input('Name des Hauptprojekts: ')
    tt.delete_main_project(main_project_name)
else:
    print('Ungültige Auswahl')

