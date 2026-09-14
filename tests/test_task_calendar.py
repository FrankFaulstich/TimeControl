import unittest
import os
import sys
from datetime import date, timedelta

# Add parent directory to path to import modules from root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from tt.task_calendar import due_on, month_grid, shift_month


class TestShiftMonth(unittest.TestCase):
    """Stepping the calendar from one month to the next."""

    def test_a_step_forward(self):
        self.assertEqual(shift_month(2026, 5, 1), (2026, 6))

    def test_a_step_back(self):
        self.assertEqual(shift_month(2026, 5, -1), (2026, 4))

    def test_december_carries_the_year_with_it(self):
        self.assertEqual(shift_month(2026, 12, 1), (2027, 1))

    def test_january_carries_it_back(self):
        self.assertEqual(shift_month(2026, 1, -1), (2025, 12))

    def test_a_whole_year_lands_on_the_same_month(self):
        self.assertEqual(shift_month(2026, 7, 12), (2027, 7))
        self.assertEqual(shift_month(2026, 7, -12), (2025, 7))

    def test_no_step_changes_nothing(self):
        self.assertEqual(shift_month(2026, 7, 0), (2026, 7))


class TestDueOn(unittest.TestCase):
    """Reading the day a task belongs on."""

    def test_a_plain_date(self):
        self.assertEqual(due_on({"due_date": "2026-09-08"}), date(2026, 9, 8))

    def test_a_date_carrying_a_time_still_lands_on_its_day(self):
        self.assertEqual(due_on({"due_date": "2026-09-08 14:30:00"}),
                         date(2026, 9, 8))

    def test_an_undated_task_belongs_nowhere(self):
        self.assertIsNone(due_on({"task_name": "irgendwann"}))
        self.assertIsNone(due_on({"due_date": None}))
        self.assertIsNone(due_on({"due_date": ""}))

    def test_an_unreadable_date_costs_that_task_its_square_not_the_month(self):
        self.assertIsNone(due_on({"due_date": "demnächst"}))
        self.assertIsNone(due_on({"due_date": "2026-13-40"}))


class TestMonthGrid(unittest.TestCase):
    """The month laid out as whole weeks."""

    @staticmethod
    def _task(name, due=None, status="open", start=None):
        task = {"task_name": name, "status": status}
        if due:
            task["due_date"] = due
        if start:
            task["start_date"] = start
        return task

    @staticmethod
    def _named(day):
        return [task["task_name"] for task, _is_due in day.tasks]

    @staticmethod
    def _due_here(day):
        """The tasks on this day that are actually wanted by it."""
        return [task["task_name"] for task, is_due in day.tasks if is_due]

    def _flat(self, grid):
        return [day for week in grid for day in week]

    def test_every_week_is_a_full_seven_days(self):
        for grid in (month_grid(2026, 9, []), month_grid(2026, 2, []),
                     month_grid(2027, 2, [])):
            self.assertTrue(all(len(week) == 7 for week in grid))

    def test_the_weeks_begin_on_monday(self):
        self.assertTrue(all(week[0].weekday() == 0
                            for week in [[d.date for d in w]
                                         for w in month_grid(2026, 9, [])]))

    def test_the_days_run_without_a_gap(self):
        days = [day.date for day in self._flat(month_grid(2026, 9, []))]
        self.assertEqual(days, [days[0] + timedelta(days=i)
                                for i in range(len(days))])

    def test_the_whole_month_is_there(self):
        in_month = [day.date for day in self._flat(month_grid(2026, 9, []))
                    if day.in_month]
        self.assertEqual(in_month[0], date(2026, 9, 1))
        self.assertEqual(in_month[-1], date(2026, 9, 30))
        self.assertEqual(len(in_month), 30)

    def test_days_from_the_months_either_side_are_marked_as_visitors(self):
        grid = month_grid(2026, 9, [])
        self.assertFalse(grid[0][0].in_month)
        self.assertEqual(grid[0][0].date, date(2026, 8, 31))

    def test_a_task_sits_on_the_day_it_is_due(self):
        grid = month_grid(2026, 9, [self._task("Angebot", "2026-09-08")])
        found = [day for day in self._flat(grid) if day.tasks]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].date, date(2026, 9, 8))
        self.assertEqual(self._named(found[0]), ["Angebot"])

    def test_several_tasks_on_one_day_all_appear(self):
        grid = month_grid(2026, 9, [self._task("A", "2026-09-08"),
                                    self._task("B", "2026-09-08")])
        found = [day for day in self._flat(grid) if day.tasks][0]
        self.assertEqual(self._named(found), ["A", "B"])

    def test_an_undated_task_appears_nowhere(self):
        grid = month_grid(2026, 9, [self._task("irgendwann")])
        self.assertEqual([day for day in self._flat(grid) if day.tasks], [])

    def test_a_task_due_in_a_neighbouring_month_still_shows_on_its_day(self):
        """
        August 31st is a real square on September's page. Leaving it empty
        would say nothing is due on a day something is.
        """
        grid = month_grid(2026, 9, [self._task("Rand", "2026-08-31")])
        found = [day for day in self._flat(grid) if day.tasks][0]
        self.assertEqual(found.date, date(2026, 8, 31))
        self.assertFalse(found.in_month)

    def test_a_task_outside_the_page_is_left_off(self):
        grid = month_grid(2026, 9, [self._task("weit weg", "2026-12-01")])
        self.assertEqual([day for day in self._flat(grid) if day.tasks], [])

    def test_a_finished_task_keeps_its_square(self):
        """
        The grid keeps whatever it is handed and judges nothing by status.

        The calendar view does not show finished tasks (issue #610), but it
        decides that by filtering before it calls here - which is where that
        decision belongs, and what lets this stay a plain bucketing function
        that any other caller can use differently.
        """
        grid = month_grid(2026, 9, [self._task("erledigt", "2026-09-08", "done")])
        found = [day for day in self._flat(grid) if day.tasks][0]
        self.assertEqual(self._named(found), ["erledigt"])

    def test_one_unreadable_date_does_not_cost_the_month(self):
        grid = month_grid(2026, 9, [self._task("kaputt", "demnächst"),
                                    self._task("gut", "2026-09-08")])
        found = [day for day in self._flat(grid) if day.tasks]
        self.assertEqual([self._named(d) for d in found], [["gut"]])

    def test_february_in_a_leap_year(self):
        in_month = [d.date for d in self._flat(month_grid(2028, 2, []))
                    if d.in_month]
        self.assertEqual(in_month[-1], date(2028, 2, 29))

    def test_a_month_beginning_on_a_monday_grows_no_empty_first_week(self):
        """June 2026 starts on a Monday; its first square must be the 1st."""
        grid = month_grid(2026, 6, [])
        self.assertEqual(grid[0][0].date, date(2026, 6, 1))
        self.assertTrue(grid[0][0].in_month)


class TestTheWindow(unittest.TestCase):
    """
    Issue #623 in the calendar: a task with a start date runs across days
    rather than sitting on one, and the month should show how long there is
    for it. The stretch drawn here is the same one that puts the task into
    Today's Tasks, or the page and the list would disagree about what is
    current.
    """

    def _task(self, name, start=None, due=None):
        task = {"task_name": name, "status": "open"}
        if start:
            task["start_date"] = start
        if due:
            task["due_date"] = due
        return task

    def _days_with(self, grid, name):
        return [day.date for week in grid for day in week
                if any(task["task_name"] == name for task, _ in day.tasks)]

    def _due_day(self, grid, name):
        found = [day.date for week in grid for day in week
                 for task, is_due in day.tasks
                 if task["task_name"] == name and is_due]
        self.assertEqual(len(found), 1, "a task has exactly one deadline")
        return found[0]

    def test_a_task_fills_every_day_of_its_window(self):
        grid = month_grid(2026, 9, [self._task("Fenster", "2026-09-07",
                                               "2026-09-10")])
        self.assertEqual(self._days_with(grid, "Fenster"),
                         [date(2026, 9, d) for d in (7, 8, 9, 10)])

    def test_the_last_day_is_the_one_marked_as_due(self):
        grid = month_grid(2026, 9, [self._task("Fenster", "2026-09-07",
                                               "2026-09-10")])
        self.assertEqual(self._due_day(grid, "Fenster"), date(2026, 9, 10))

    def test_a_task_without_a_start_date_still_sits_on_one_day(self):
        grid = month_grid(2026, 9, [self._task("Punkt", due="2026-09-08")])
        self.assertEqual(self._days_with(grid, "Punkt"), [date(2026, 9, 8)])
        self.assertEqual(self._due_day(grid, "Punkt"), date(2026, 9, 8))

    def test_a_window_of_one_day_is_that_day(self):
        grid = month_grid(2026, 9, [self._task("kurz", "2026-09-08",
                                               "2026-09-08")])
        self.assertEqual(self._days_with(grid, "kurz"), [date(2026, 9, 8)])
        self.assertEqual(self._due_day(grid, "kurz"), date(2026, 9, 8))

    def test_a_window_that_begins_before_the_page_is_shown_from_its_edge(self):
        """
        A fortnight that started in August is still running in September, and
        the page has to say so - it is read to see what is current.
        """
        grid = month_grid(2026, 9, [self._task("laeuft", "2026-08-20",
                                               "2026-09-03")])
        days = self._days_with(grid, "laeuft")
        self.assertEqual(days[0], date(2026, 8, 31),
                         "the page begins on the 31st of August")
        self.assertEqual(days[-1], date(2026, 9, 3))

    def test_a_window_that_ends_after_the_page_runs_off_it(self):
        grid = month_grid(2026, 9, [self._task("lang", "2026-09-28",
                                               "2026-10-20")])
        days = self._days_with(grid, "lang")
        self.assertEqual(days[0], date(2026, 9, 28))
        self.assertEqual(days[-1], date(2026, 10, 4),
                         "the page ends on the 4th of October")
        self.assertEqual([d for week in grid for day in week
                          for task, is_due in day.tasks if is_due], [],
                         "a deadline off the page must not be drawn on it")

    def test_a_window_that_covers_the_month_entirely(self):
        grid = month_grid(2026, 9, [self._task("Dauerlaeufer", "2026-01-01",
                                               "2026-12-31")])
        self.assertEqual(len(self._days_with(grid, "Dauerlaeufer")),
                         sum(len(week) for week in grid))

    def test_a_start_date_on_its_own_is_enough_to_be_drawn(self):
        """
        The tracker gives such a task a due date to match, but a document
        written by an older version or by hand need not have one.
        """
        grid = month_grid(2026, 9, [self._task("nur Start", "2026-09-08")])
        self.assertEqual(self._days_with(grid, "nur Start"), [date(2026, 9, 8)])

    def test_a_start_date_after_the_due_date_falls_back_to_the_deadline(self):
        """
        The interface refuses to save that pair. Arriving anyway - from an
        API, or another machine - it must not empty the square: a date that is
        wrong is still one somebody meant something by.
        """
        grid = month_grid(2026, 9, [self._task("verdreht", "2026-09-20",
                                               "2026-09-08")])
        self.assertEqual(self._days_with(grid, "verdreht"), [date(2026, 9, 8)])

    def test_an_unreadable_start_date_costs_the_window_not_the_task(self):
        grid = month_grid(2026, 9, [self._task("halb kaputt", "demnächst",
                                               "2026-09-08")])
        self.assertEqual(self._days_with(grid, "halb kaputt"),
                         [date(2026, 9, 8)])

    def test_the_order_tasks_were_given_in_is_kept_on_every_day(self):
        grid = month_grid(2026, 9, [self._task("A", "2026-09-07", "2026-09-09"),
                                    self._task("B", due="2026-09-08")])
        eighth = [day for week in grid for day in week
                  if day.date == date(2026, 9, 8)][0]
        self.assertEqual([task["task_name"] for task, _ in eighth.tasks],
                         ["A", "B"])


# Run the tests if the file is called directly
if __name__ == '__main__':
    unittest.main()
