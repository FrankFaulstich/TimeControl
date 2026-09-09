import unittest
import os
import sys

# Add parent directory to path to import modules from root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from tt.task_order import (TASK_ORDER_ALPHABETICAL, TASK_ORDER_NONE,
                           TASK_ORDER_PRIORITY, sort_tasks)


class TestSortTasks(unittest.TestCase):
    """The orders the task lists can be presented in."""

    @staticmethod
    def _task(name, priority=0):
        return {"task_name": name, "priority": priority}

    @staticmethod
    def _names(tasks):
        return [t.get("task_name") for t in tasks]

    def test_no_sorting_hands_back_what_it_was_given(self):
        tasks = [self._task("Zebra", 1), self._task("Apfel", 9)]
        self.assertEqual(self._names(sort_tasks(tasks, TASK_ORDER_NONE)),
                         ["Zebra", "Apfel"])

    def test_an_unknown_order_leaves_the_list_alone(self):
        """A stale value in a saved UI state must not take out the view."""
        tasks = [self._task("Zebra"), self._task("Apfel")]
        self.assertEqual(self._names(sort_tasks(tasks, "by-phase-of-moon")),
                         ["Zebra", "Apfel"])

    def test_sorting_does_not_disturb_the_caller_s_list(self):
        tasks = [self._task("Zebra"), self._task("Apfel")]
        sort_tasks(tasks, TASK_ORDER_ALPHABETICAL)
        self.assertEqual(self._names(tasks), ["Zebra", "Apfel"])

    def test_the_most_important_task_comes_first(self):
        tasks = [self._task("mittel", 5), self._task("hoch", 9),
                 self._task("niedrig", 1)]
        self.assertEqual(self._names(sort_tasks(tasks, TASK_ORDER_PRIORITY)),
                         ["hoch", "mittel", "niedrig"])

    def test_tasks_sharing_a_priority_keep_their_order(self):
        tasks = [self._task("zuerst angelegt", 5),
                 self._task("danach angelegt", 5)]
        self.assertEqual(self._names(sort_tasks(tasks, TASK_ORDER_PRIORITY)),
                         ["zuerst angelegt", "danach angelegt"])

    def test_a_task_without_a_priority_sorts_as_the_lowest(self):
        """Nothing writes a null priority, but a peer's data might carry one."""
        tasks = [{"task_name": "ohne"}, {"task_name": "null", "priority": None},
                 self._task("mit", 3)]
        self.assertEqual(self._names(sort_tasks(tasks, TASK_ORDER_PRIORITY))[0],
                         "mit")

    def test_alphabetical_ignores_upper_and_lower_case(self):
        tasks = [self._task("banane"), self._task("Apfel"), self._task("Citrone")]
        self.assertEqual(self._names(sort_tasks(tasks, TASK_ORDER_ALPHABETICAL)),
                         ["Apfel", "banane", "Citrone"])

    def test_an_umlaut_sorts_where_a_reader_looks_for_it(self):
        """
        Comparing by code point would file 'Ärger' behind 'Zeit', because
        'Ä' is U+00C4. Every translation except English has letters that
        would land there.
        """
        tasks = [self._task("Zeiterfassung"), self._task("Ärger"),
                 self._task("Angebot"), self._task("Übersicht"),
                 self._task("Čas"), self._task("Ñandú")]
        self.assertEqual(self._names(sort_tasks(tasks, TASK_ORDER_ALPHABETICAL)),
                         ["Angebot", "Ärger", "Čas", "Ñandú",
                          "Übersicht", "Zeiterfassung"])

    def test_a_task_without_a_name_does_not_break_the_list(self):
        tasks = [self._task("Angebot"), {"task_name": None}, {}]
        self.assertEqual(self._names(sort_tasks(tasks, TASK_ORDER_ALPHABETICAL))[-1],
                         "Angebot")

    def test_names_differing_only_in_accent_keep_a_settled_order(self):
        """
        Folding makes these two compare equal, so the name itself breaks the
        tie - otherwise the list could reshuffle between runs.
        """
        first = sort_tasks([self._task("Muller"), self._task("Müller")],
                           TASK_ORDER_ALPHABETICAL)
        second = sort_tasks([self._task("Müller"), self._task("Muller")],
                            TASK_ORDER_ALPHABETICAL)
        self.assertEqual(self._names(first), self._names(second))


# Run the tests if the file is called directly
if __name__ == '__main__':
    unittest.main()
