# Integration eine Aufgabenverwaltung

## Zweck

Durch die Erweiterung soll die Verwendung eine Todo-Liste überflüssig gemacht werden.

## Ideen

### Tasks

Tasks sind eine spezielle Art von Sub-Projekten.

Tasks bekommen folgende neue Eigenschaften:

- Fälligkeitsdatum
- `today`
  - Erscheint in der Tagesliste.

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

### Wiederkehrende Tasks

Von dieser speziellen Art von Tasks wird eine Kopie erzeugt, wenn der Task auf `done` gesetzt wurde.

### Tasks per E-Mail hinzufügen

Es wird regelmäßig eine E-Mailadresse abgerufen. Die vorhanden Mails werden in Tasks umgewandelt. Die E-Mailadresse wird in der config.json gespeichert.
