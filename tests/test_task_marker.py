"""
What sits in front of a task in the lists you work from.

Issue #624: every task carried a dash except the one being worked on (a
hammer) and the finished ones (a tick), and every name sat in from the
margin by the width of that column. The dash said only "this is an item in a
list", which the list already says; the column held an empty space in front
of nearly every name. Both went; the hammer and the tick stayed.

The rule itself is three lines. What this file is really for is the second
half of the change: those three lines used to exist three times over, once
per list, and the lists are the sort of thing that gets copied when a fourth
one is written. So it checks both that the rule is right and that there is
only one of it.

Read from the source rather than by running it - a Streamlit script cannot be
imported without starting a Streamlit session. The rule is small enough to be
read out of the syntax tree and evaluated here, which is what lets the first
half be tested at all.
"""

import ast
import os
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
WORK_VIEW = os.path.join(REPO_ROOT, 'sl', 'SL_Menu.py')

# The whole prefix, separator included - see task_marker().
HAMMER = "🔨 "
TICK = "✔ "


def _tree():
    with open(WORK_VIEW, encoding='utf-8') as handle:
        return ast.parse(handle.read(), WORK_VIEW)


def _function(name, tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError('%s() is not in %s any more' % (name, WORK_VIEW))


def _marker(is_active, is_done, tree=None):
    """
    Runs the real task_marker() out of the source.

    Compiled from the syntax tree rather than copied here: a copy would go
    on passing after the original changed, which is the one thing a test of
    a function it cannot import must not do.
    """
    source = _function('task_marker', tree or _tree())
    namespace = {}
    exec(compile(ast.Module(body=[source], type_ignores=[]),
                 WORK_VIEW, 'exec'), namespace)
    return namespace['task_marker'](is_active, is_done)


class TestWhatEachTaskCarries(unittest.TestCase):

    def test_an_ordinary_task_carries_nothing(self):
        """The change itself: no marker, and nothing standing in for one."""
        self.assertEqual(_marker(is_active=False, is_done=False), "")

    def test_the_one_being_worked_on_keeps_its_hammer(self):
        self.assertEqual(_marker(is_active=True, is_done=False), HAMMER)

    def test_a_finished_one_keeps_its_tick(self):
        self.assertEqual(_marker(is_active=False, is_done=True), TICK)

    def test_work_running_on_a_finished_task_still_shows_the_hammer(self):
        """
        Reachable: a task can be marked done from another machine, or in a
        second browser tab, while the clock is running on it here. What is
        happening now is the more useful of the two things to see.
        """
        self.assertEqual(_marker(is_active=True, is_done=True), HAMMER)

    def test_the_marker_is_never_a_dash_again(self):
        for active in (False, True):
            for done in (False, True):
                with self.subTest(is_active=active, is_done=done):
                    self.assertNotIn("-", _marker(active, done))

    def test_a_marker_brings_the_space_after_it(self):
        """
        The callers write it straight in front of the name, so the gap has
        to come from here - and an ordinary task must not get one, or every
        name would still be pushed in by a space nobody can see.
        """
        self.assertTrue(_marker(True, False).endswith(" "))
        self.assertTrue(_marker(False, True).endswith(" "))
        self.assertEqual(_marker(False, False), "")


class TestThereIsOnlyOneOfTheRule(unittest.TestCase):
    """
    The lists are drawn in two functions and three places. Before this the
    same three lines sat in each of them, which is how one list ends up
    keeping a marker the others have lost.
    """

    def setUp(self):
        self.tree = _tree()
        with open(WORK_VIEW, encoding='utf-8') as handle:
            self.source = handle.read()

    def _marker_slots(self):
        """How many task lists there are, counted by their marker."""
        return self.source.count("{marker}")

    def test_every_list_asks_the_same_function(self):
        """
        Counted against the lists themselves rather than against a number
        written here: a fourth list that worked its marker out on its own
        would leave a fixed number untouched and pass.
        """
        calls = [node for node in ast.walk(self.tree)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                 and node.func.id == 'task_marker']
        self.assertEqual(len(calls), self._marker_slots(),
                         'a task list draws a marker without asking task_marker()')
        self.assertGreaterEqual(len(calls), 3, 'the task lists have gone missing')

    def test_nothing_decides_the_marker_for_itself(self):
        """
        Guards the count above: a fourth list could call the function and
        still spell its own marker out beside it.
        """
        self.assertEqual(self.source.count('🔨'), 1,
                         'the hammer is written in more than one place')
        self.assertEqual(self.source.count('✔'), 1,
                         'the tick is written in more than one place')

    def test_no_list_indents_its_names_any_more(self):
        """
        The second half of issue #624. The marker used to sit in a 2rem
        column that stayed there when the marker did not, so every name in
        every list began a fixed distance in from the margin.
        """
        self.assertEqual(self._marker_slots(), 3, 'a task list lost its marker')
        self.assertNotIn("width: 2rem", self.source,
                         'a task list still holds a column open for its marker')

    def test_no_listing_is_a_bulleted_list_any_more(self):
        """
        The plain listings - closed tasks, inactive projects, and the rest -
        were Markdown lists, which a browser both bullets and indents.
        """
        self.assertNotIn('st.markdown(f"- ', self.source,
                         'a listing is still rendered as a Markdown bullet list')
        self.assertNotIn('st.write(f"- ', self.source)


if __name__ == '__main__':
    unittest.main()
