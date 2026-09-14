"""
A month laid out as whole weeks, with each task on every day it runs.

A task with only a due date occupies that one day, as it always has. One that
also carries a start date (issue #623) occupies every day from the start to
the due date - the same stretch that puts it into Today's Tasks - so the
month shows how long there is for a piece of work, not only the day it is
wanted by.

Plain functions rather than a class: none of them carries state between
calls, and in Python a module is already the namespace a class of static
methods would only imitate. They used to live in TimeTracker.py, which is
about the document on disk and never called any of them.
"""

import calendar
from collections import namedtuple
from datetime import date


# One cell of a month grid: the day it stands for, whether that day belongs to
# the month being shown (a grid always starts and ends mid-week, so the first
# and last rows carry days from the months either side), and what runs then.
#
# `tasks` holds (task, is_due) pairs: a task that runs across several days is
# in every one of their squares, and the flag says which of them is the day it
# is actually wanted by. The view needs that to keep the deadline legible
# among the days leading up to it.
CalendarDay = namedtuple('CalendarDay', ('date', 'in_month', 'tasks'))


def _day(raw):
    """
    One stored date as a day, or None when it is missing or unreadable.

    Dropped rather than raised on: this feeds a calendar, and one task with a
    date nothing can read should cost that task its square, not the month.
    """
    if not raw:
        return None
    try:
        # Sliced, so a value that carries a time of day as well still lands
        # on its day rather than being thrown away.
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def due_on(task):
    """The day a task is due, or None if it is not dated or unreadable."""
    return _day(task.get('due_date'))


def starts_on(task):
    """The day a task becomes current, or None if it carries no start date."""
    return _day(task.get('start_date'))


def window_of(task):
    """
    The first and last day a task occupies, or None if it occupies none.

    A start date alone is enough - TimeTracker gives such a task a due date to
    match, but a document written by hand or by an older version need not have
    one, and a calendar should still be able to draw it.

    A start date after the due date describes a stretch that never opens. The
    interface refuses to save that pair, and if one arrives anyway the task is
    drawn on its deadline alone rather than nowhere: a date that is wrong is
    still a date somebody meant something by, and a silently empty month is
    the one outcome nobody can act on.
    """
    start, due = starts_on(task), due_on(task)
    if start is None and due is None:
        return None
    if due is None:
        return start, start
    if start is None or start > due:
        return due, due
    return start, due


def month_grid(year, month, tasks):
    """
    The given month as whole weeks, each day carrying the tasks that run on it.

    Weeks run Monday to Sunday, which is what the weekly overview in the
    planning tab already assumes.

    Days from the neighbouring months fill out the first and last week and
    carry their own tasks: they are real days on this page, and leaving them
    empty would say nothing is running when something is. `in_month` marks
    them so the view can show them as the visitors they are.

    Each day is asked about each task rather than every task being expanded
    into the days it covers: a window may be months long while the page is
    six weeks, and the days outside it are not this month's business.

    :return: a list of weeks, each a list of seven CalendarDay, whose `tasks`
             are (task, is_due) pairs in the order the tasks were given.
    """
    windows = [(task, window_of(task)) for task in tasks]
    windows = [(task, span) for task, span in windows if span is not None]
    weeks = calendar.Calendar(firstweekday=0).monthdatescalendar(year, month)
    return [[CalendarDay(day, (day.year, day.month) == (year, month),
                         [(task, day == last)
                          for task, (first, last) in windows
                          if first <= day <= last])
             for day in week]
            for week in weeks]


def shift_month(year, month, delta):
    """
    The (year, month) `delta` months away from the given one.

    Counted in months since year 0 rather than by adjusting the two numbers
    separately, so December to January carries the year with it and stepping
    backwards past January needs no special case.
    """
    total = year * 12 + (month - 1) + delta
    return total // 12, total % 12 + 1
