# Integration eine Aufgabenverwaltung

## Zweck

Durch die Erweiterung soll die Verwendung eine Todo-Liste überflüssig gemacht werden.

## Umsetzung

### Tasks

Tasks sind eine spezielle Art von Sub-Projekten. Es muss noch entschieden werden, ob andere Sub-Projekte überhaupt noch gebraucht werden. Eventuell ist es sinnvoll, die Sub-Projekte einfach zu erweitern.

Bei der Umsetzung wird sich an den Möglichkeiten von Microsoft To Do orientiert.

Tasks bekommen folgende neue Eigenschaften:

- Fälligkeitsdatum
- `today`
  - Erscheint in der Tagesliste "Mein Tag".

Folgende Eigenschaften werden erweitert:

- Status:
  - `open`
    - Ist bereits umgesetzt.
  - `planed`
    - Der Task hat ein Fälligkeitsdatum.
  - `done`
    - ist erledigt
  - `closed`
    - Erscheint nicht mehr in den Auswahllisten.
    - Ist bereits umgesetzt.
- Notizen
  - Textfeld für Notizen im Markdown-Format.

### Neue Ansichten

#### Task-Ansicht

Es wird eine Ansicht hinzugefügt, in der ein einzelner Task angezeigt werden kann. Darin lässt sich das Fälligkeitsdatum ändern und Notizen bearbeiten.

#### Task-Liste

Listet alle Tasks auf, deren Status nicht `closed` ist.

In der Task-Liste kann eingestellt werden, welche Tasks angezeigt werden sollen und wie sie sortiert werden sollen.

- Tagesübersicht
- Morgen
- Wochenübersicht
- überfällige Tasks
- ungeplante Tasks

#### Wochenübersicht

Zeigt alle Tasks, die vom aktuellen Datum an innerhalb einer Woche fällig sind. Die Tasks werden in Wochentagen einsortiert.

Einzelne Tasks können angeklickt und in der Task-Ansicht bearbeitet werden.

### Zu ändernde Ansichten

#### Hinzufügen von Sub-Projekten

Hier wird die Möglichkeit geschaffen, ein Fälligkeitsdatum hinzuzufügen.

### Wiederkehrende Tasks

Von dieser speziellen Art von Tasks wird eine Kopie erzeugt, wenn der Task auf `done` gesetzt wurde.

### Tasks per E-Mail hinzufügen

Es wird regelmäßig eine E-Mailadresse abgerufen. Die vorhanden Mails werden in Tasks umgewandelt. Die E-Mailadresse wird in der config.json gespeichert.
