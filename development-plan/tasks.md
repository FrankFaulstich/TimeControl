# Integration eine Aufgabenverwaltung

## Zweck

Durch die Erweiterung soll die Verwendung eine Todo-Liste überflüssig gemacht werden.

## Umsetzung

### Tasks

Aus den Sub-Projekten werden Tasks. [#307](https://github.com/FrankFaulstich/TimeControl/issues/307)

Bei der Umsetzung wird sich an den Möglichkeiten von Microsoft To Do orientiert.

Tasks bekommen folgende neue Eigenschaften [#308](https://github.com/FrankFaulstich/TimeControl/issues/308)

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
- Notizen (`note`)
  - Textfeld für Notizen im Markdown-Format.

### Neue Ansichten

#### Task-Ansicht [#309](https://github.com/FrankFaulstich/TimeControl/issues/309)


Es wird eine Ansicht hinzugefügt, in der ein einzelner Task angezeigt werden kann. Darin lässt sich das Fälligkeitsdatum ändern und Notizen bearbeiten.

#### Task-Liste [#310](https://github.com/FrankFaulstich/TimeControl/issues/310)

Listet alle Tasks auf, deren Status nicht `closed` ist.

In der Task-Liste kann eingestellt werden, welche Tasks angezeigt werden sollen und wie sie sortiert werden sollen.

- Tagesübersicht
- Morgen
- Wochenübersicht
- überfällige Tasks
- ungeplante Tasks

#### Today [#311](https://github.com/FrankFaulstich/TimeControl/issues/311)

Enthält alle Tasks mit der Eigenschaft `today`.

### Zu ändernde Ansichten

#### Hinzufügen von Tasks [#312](https://github.com/FrankFaulstich/TimeControl/issues/312)

Hier wird die Möglichkeit geschaffen, ein Fälligkeitsdatum hinzuzufügen.

### Wiederkehrende Tasks [#313](https://github.com/FrankFaulstich/TimeControl/issues/313)

Von dieser speziellen Art von Tasks wird eine Kopie erzeugt, wenn der Task auf `done` gesetzt wurde.

Perioden:

- täglich
- an allen Arbeitstagen
- wöchentlich
- monatlich

### Tasks per E-Mail hinzufügen [#314](https://github.com/FrankFaulstich/TimeControl/issues/314)

Es wird regelmäßig eine E-Mailadresse abgerufen. Die vorhanden Mails werden in Tasks umgewandelt. Der Task wir als ungeplanter Task eingefügt.

Die E-Mailadresse wird in der config.json gespeichert.
