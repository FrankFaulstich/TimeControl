# Integration of a Task Management System

## Purpose

This extension is intended to eliminate the need for a to-do list.

## Implementation

### Tasks

Subprojects will be converted into tasks. [#307](https://github.com/FrankFaulstich/TimeControl/issues/307)

The implementation will be based on the features of Microsoft To Do.

Tasks will have the following new properties [#308](https://github.com/FrankFaulstich/TimeControl/issues/308)

- Due date
- `today`
  - Appears in the “My Day” daily list.

The following properties will be expanded:

- Status:
  - `open`
    - Already implemented.
  - `planned`
    - The task has a due date.
  - `done`
    - Completed
  - `closed`
    - No longer appears in the dropdown lists.
    - Has already been implemented.
- Notes (`note`)
  - Text field for notes in Markdown format.

### New Views

#### Task View [#309](https://github.com/FrankFaulstich/TimeControl/issues/309)

A view is added in which a single task can be displayed. In this view, you can change the due date and edit notes.

#### Task List [#310](https://github.com/FrankFaulstich/TimeControl/issues/310)

Lists all tasks whose status is not `closed`.

In the task list, you can configure which tasks should be displayed and how they should be sorted.

- Daily overview
- Tomorrow
- Weekly overview
- Overdue tasks
- Unplanned tasks

#### Today [#311](https://github.com/FrankFaulstich/TimeControl/issues/311)

Contains all tasks with the `today` property.

### Views to Modify

#### Adding Tasks [#312](https://github.com/FrankFaulstich/TimeControl/issues/312)

This feature allows you to add a due date.

### Recurring tasks [#313](https://github.com/FrankFaulstich/TimeControl/issues/313)

A copy of this specific type of task is created when the task is marked as `done`.

Periods:

- daily
- on all workdays
- weekly
- monthly

### Add Tasks via Email [#314](https://github.com/FrankFaulstich/TimeControl/issues/314)

An email address is checked regularly. Existing emails are converted into tasks. The task is added as an unscheduled task.

The email address is stored in config.json.
