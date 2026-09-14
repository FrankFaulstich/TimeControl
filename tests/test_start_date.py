"""
The start date: the day a task begins to belong in today's work.

Issue #623. Until now a task appeared under Today's Tasks on the one day it
was due. Work that has to be picked up somewhere inside a stretch of days had
no way to say so - it either sat in the list from the moment it was created,
because somebody ticked Today by hand, or it turned up on the last possible
day.

The rule is one line - is today between the two dates - and almost all of the
care here is about the edges of it: the day before, the day after, a start
date with no due date to close the window, and what becomes of the pair when
a recurring task rolls over.
"""

import os
import shutil
import sys
import tempfile
import unittest
from datetime import date, timedelta

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from tt.TimeTracker import TimeTracker

TODAY = date.today()


def day(offset):
    """A date `offset` days from today, as the document stores it."""
    return (TODAY + timedelta(days=offset)).isoformat()


class StartDateTestCase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.tracker = TimeTracker(file_path=os.path.join(self.tmp, 'data.json'))
        self.tracker.add_main_project("P")

    def task(self, name):
        for candidate in self.tracker.data["projects"][0]["tasks"]:
            if candidate["task_name"] == name:
                return candidate
        raise AssertionError("no task called %r" % name)

    def flagged(self, name):
        return bool(self.task(name).get("today"))


class TestWhatTheSweepMarksForToday(StartDateTestCase):

    def test_a_task_inside_its_window_is_marked(self):
        self.tracker.add_task("P", "laufend", start_date=day(-2), due_date=day(3))
        self.tracker.set_today_flag_for_due_tasks()
        self.assertTrue(self.flagged("laufend"))

    def test_on_the_first_day_of_the_window(self):
        """Inclusive: the day work may begin is a day it belongs in the list."""
        self.tracker.add_task("P", "faengt heute an", start_date=day(0), due_date=day(5))
        self.tracker.set_today_flag_for_due_tasks()
        self.assertTrue(self.flagged("faengt heute an"))

    def test_and_on_the_last(self):
        self.tracker.add_task("P", "heute faellig", start_date=day(-5), due_date=day(0))
        self.tracker.set_today_flag_for_due_tasks()
        self.assertTrue(self.flagged("heute faellig"))

    def test_a_task_whose_window_has_not_opened_is_left_alone(self):
        """
        The point of the whole feature. Without this the start date would be
        decoration: everything would be in today's list from the day it was
        written down.
        """
        self.tracker.add_task("P", "spaeter", start_date=day(1), due_date=day(6))
        self.tracker.set_today_flag_for_due_tasks()
        self.assertFalse(self.flagged("spaeter"))

    def test_a_task_whose_window_has_closed_is_not_marked_again(self):
        self.tracker.add_task("P", "vorbei", start_date=day(-9), due_date=day(-1))
        self.tracker.set_today_flag_for_due_tasks()
        self.assertFalse(self.flagged("vorbei"))

    def test_and_the_overdue_sweep_takes_it_back_off_the_list(self):
        """
        The two sweeps have to agree, or a task marked on the last day of its
        window would stay in today's list for ever.
        """
        self.tracker.add_task("P", "vorbei", start_date=day(-9), due_date=day(-1),
                              today=True)
        self.tracker.cleanup_overdue_today_tasks()
        self.assertFalse(self.flagged("vorbei"))

    def test_a_task_with_only_a_due_date_behaves_as_it_always_did(self):
        self.tracker.add_task("P", "heute", due_date=day(0))
        self.tracker.add_task("P", "morgen", due_date=day(1))
        self.tracker.set_today_flag_for_due_tasks()
        self.assertTrue(self.flagged("heute"))
        self.assertFalse(self.flagged("morgen"))

    def test_a_task_with_no_dates_at_all_is_never_marked(self):
        self.tracker.add_task("P", "irgendwann")
        self.tracker.set_today_flag_for_due_tasks()
        self.assertFalse(self.flagged("irgendwann"))

    def test_a_finished_task_is_not_dragged_back_in(self):
        self.tracker.add_task("P", "erledigt", start_date=day(-2), due_date=day(3))
        self.tracker.update_task("P", "erledigt", status=TimeTracker.STATUS_DONE)
        self.tracker.set_today_flag_for_due_tasks()
        self.assertFalse(self.flagged("erledigt"))

    def test_the_sweep_reports_whether_it_changed_anything(self):
        """
        The caller saves and sends on the strength of this, and the interface
        runs the sweep on every redraw - one that always claimed a change
        would write the document every time the screen was drawn.
        """
        self.tracker.add_task("P", "laufend", start_date=day(-2), due_date=day(3))
        self.assertTrue(self.tracker.set_today_flag_for_due_tasks())
        self.assertFalse(self.tracker.set_today_flag_for_due_tasks())

    def test_within_the_window_the_flag_comes_back_after_being_cleared(self):
        """
        Not a defect, and worth writing down because it is the one thing
        about this that surprises: the sweep runs on every redraw, so a task
        cannot be dismissed from today's list for a day in the middle of its
        own window. It is the same as a task due today, which has behaved
        this way all along.
        """
        self.tracker.add_task("P", "laufend", start_date=day(-2), due_date=day(3))
        self.tracker.set_today_flag_for_due_tasks()
        self.tracker.update_task("P", "laufend", today=False)
        self.assertFalse(self.flagged("laufend"))

        self.tracker.set_today_flag_for_due_tasks()
        self.assertTrue(self.flagged("laufend"))


class TestAStartDateAlwaysHasADueDate(StartDateTestCase):
    """
    "Between the start date and the due date" needs both ends. Given only a
    start date, the application fills the other one in rather than leaving a
    window that never closes.
    """

    def test_adding_one_with_no_due_date_fills_the_due_date_in(self):
        self.tracker.add_task("P", "nur Start", start_date=day(2))
        self.assertEqual(self.task("nur Start")["due_date"], day(2))
        self.assertEqual(self.task("nur Start")["start_date"], day(2))

    def test_a_due_date_that_was_given_is_left_alone(self):
        self.tracker.add_task("P", "beides", start_date=day(1), due_date=day(4))
        self.assertEqual(self.task("beides")["due_date"], day(4))

    def test_a_task_with_no_start_date_gets_no_due_date_invented(self):
        self.tracker.add_task("P", "offen")
        self.assertIsNone(self.task("offen")["due_date"])

    def test_setting_a_start_date_later_fills_it_in_too(self):
        self.tracker.add_task("P", "offen")
        self.tracker.update_task("P", "offen", start_date=day(3))
        self.assertEqual(self.task("offen")["due_date"], day(3))

    def test_clearing_only_the_due_date_leaves_the_start_date_in_its_place(self):
        """
        The consequence of the rule, stated so nobody has to discover it: a
        task that keeps its start date keeps a due date to match.
        """
        self.tracker.add_task("P", "beides", start_date=day(1), due_date=day(4))
        self.tracker.update_task("P", "beides", clear_due_date=True)
        self.assertEqual(self.task("beides")["due_date"], day(1))

    def test_clearing_both_leaves_both_empty(self):
        self.tracker.add_task("P", "beides", start_date=day(1), due_date=day(4))
        self.tracker.update_task("P", "beides", clear_due_date=True,
                                 clear_start_date=True)
        self.assertIsNone(self.task("beides")["due_date"])
        self.assertIsNone(self.task("beides")["start_date"])

    def test_an_omitted_start_date_means_unchanged(self):
        """The same promise update_task makes about every other field."""
        self.tracker.add_task("P", "beides", start_date=day(1), due_date=day(4))
        self.tracker.update_task("P", "beides", priority=5)
        self.assertEqual(self.task("beides")["start_date"], day(1))


class TestARecurringTaskKeepsItsWindow(StartDateTestCase):

    def _next_instance(self, name):
        tasks = [t for t in self.tracker.data["projects"][0]["tasks"]
                 if t["task_name"] == name and t["status"] == TimeTracker.STATUS_OPEN]
        self.assertEqual(len(tasks), 1, "expected exactly one open instance")
        return tasks[0]

    def test_the_start_date_travels_with_the_due_date(self):
        """
        Left where it was, the start date would be in the past for ever and
        every future instance would count as current the moment it appeared -
        the opposite of what a start date is for.
        """
        self.tracker.add_task("P", "woechentlich", start_date=day(-3), due_date=day(0),
                              recurring=True, frequency="weekly")
        self.tracker.update_task("P", "woechentlich", status=TimeTracker.STATUS_DONE)

        nxt = self._next_instance("woechentlich")
        self.assertEqual(nxt["due_date"], day(7))
        self.assertEqual(nxt["start_date"], day(4), "the window moved or changed length")

    def test_the_gap_between_the_two_is_what_is_kept(self):
        self.tracker.add_task("P", "monatlich", start_date=day(-10), due_date=day(0),
                              recurring=True, frequency="monthly")
        self.tracker.update_task("P", "monatlich", status=TimeTracker.STATUS_DONE)

        nxt = self._next_instance("monatlich")
        gap = (date.fromisoformat(nxt["due_date"])
               - date.fromisoformat(nxt["start_date"])).days
        self.assertEqual(gap, 10)

    def test_a_task_without_one_does_not_acquire_one(self):
        self.tracker.add_task("P", "taeglich", due_date=day(0),
                              recurring=True, frequency="daily")
        self.tracker.update_task("P", "taeglich", status=TimeTracker.STATUS_DONE)
        self.assertIsNone(self._next_instance("taeglich")["start_date"])

    def test_a_date_that_cannot_be_read_is_not_guessed_at(self):
        self.tracker.add_task("P", "kaputt", due_date=day(0),
                              recurring=True, frequency="daily")
        self.task("kaputt")["start_date"] = "irgendwann"
        self.tracker.update_task("P", "kaputt", status=TimeTracker.STATUS_DONE)
        self.assertIsNone(self._next_instance("kaputt")["start_date"])


class TestTheFieldTravelsWithEverythingElse(StartDateTestCase):

    def test_a_document_written_before_this_existed_gains_the_field(self):
        """
        Every task carries the key, so the sweep and the sync never have to
        tell "no start date" apart from "a version that did not have them".
        """
        self.tracker.add_task("P", "alt", due_date=day(0))
        del self.task("alt")["start_date"]

        reopened = TimeTracker(file_path=self.tracker.file_path)
        reopened.data["projects"][0]["tasks"][0].pop("start_date", None)
        reopened._migrate_data_structure()
        self.assertIsNone(reopened.data["projects"][0]["tasks"][0]["start_date"])

    def test_it_is_one_of_the_fields_the_machines_exchange(self):
        self.assertIn("start_date", TimeTracker.TASK_SYNC_FIELDS)
        from tt.sync_apply import TASK_FIELDS
        self.assertIn("start_date", TASK_FIELDS,
                      "the receiving machine would drop it on arrival")

    def test_it_is_listed_with_the_task(self):
        self.tracker.add_task("P", "sichtbar", start_date=day(1), due_date=day(4))
        listed = self.tracker.list_tasks(main_project_name="P")[0]
        self.assertEqual(listed["start_date"], day(1))


if __name__ == '__main__':
    unittest.main()
