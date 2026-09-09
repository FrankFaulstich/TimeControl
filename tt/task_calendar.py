"""
A month laid out as whole weeks, with each task on the day it is due.

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
# and last rows carry days from the months either side), and what is due then.
CalendarDay = namedtuple('CalendarDay', ('date', 'in_month', 'tasks'))


def due_on(task):
    """
    The day a task is due, or None if it is not dated or the date is unusable.

    Dropped rather than raised on: this feeds a calendar, and one task with a
    date nothing can read should cost that task its square, not the month.
    """
    raw = task.get('due_date')
    if not raw:
        return None
    try:
        # Sliced, so a value that carries a time of day as well still lands
        # on its day rather than being thrown away.
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def month_grid(year, month, tasks):
    """
    The given month as whole weeks, each day carrying the tasks due on it.

    Weeks run Monday to Sunday, which is what the weekly overview in the
    planning tab already assumes.

    Days from the neighbouring months fill out the first and last week and
    carry their own tasks: they are real days on this page, and leaving them
    empty would say nothing is due when something is. `in_month` marks them so
    the view can show them as the visitors they are.

    :return: a list of weeks, each a list of seven CalendarDay.
    """
    due = {}
    for task in tasks:
        day = due_on(task)
        if day is not None:
            due.setdefault(day, []).append(task)
    weeks = calendar.Calendar(firstweekday=0).monthdatescalendar(year, month)
    return [[CalendarDay(day, (day.year, day.month) == (year, month),
                         due.get(day, []))
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
