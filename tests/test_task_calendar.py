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
    def _task(name, due=None, status="open"):
        task = {"task_name": name, "status": status}
        if due:
            task["due_date"] = due
        return task

    @staticmethod
    def _named(day):
        return [t["task_name"] for t in day.tasks]

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


# Run the tests if the file is called directly
if __name__ == '__main__':
    unittest.main()
