import ast
import os
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
STREAMLIT_MODULE = os.path.join(REPO_ROOT, 'sl', 'SL_Menu.py')

# Both tab bodies keep an expander's open/closed state the same way, and
# both got the same defect with it. Checked as a pair, so a fix to one
# and not the other cannot pass.
EXPANDER_BODIES = ('_today_tasks_body', '_task_planning_body')


def _function(name):
    """
    The parsed body of one top-level function in the Streamlit module.

    Read by source rather than by running it: a Streamlit script cannot be
    imported without starting a Streamlit session, which is the same reason
    the defect below could sit in it unnoticed.
    """
    with open(STREAMLIT_MODULE, encoding='utf-8') as handle:
        tree = ast.parse(handle.read(), STREAMLIT_MODULE)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError('%s() is not in %s any more' % (name, STREAMLIT_MODULE))


def _session_state_reads(nodes):
    """
    Every `st.session_state[...]` read among the given statements.

    Only the subscript form. Attribute access (st.session_state.foo) raises
    AttributeError on a missing name, but the places that use it here set the
    name a few lines earlier; it is indexing a key a widget was supposed to
    have registered that can fail.
    """
    reads = []
    for statement in nodes:
        for node in ast.walk(statement):
            if (isinstance(node, ast.Subscript)
                    and isinstance(node.ctx, ast.Load)
                    and isinstance(node.value, ast.Attribute)
                    and node.value.attr == 'session_state'):
                reads.append(node.lineno)
    return reads


class TestTheExpanderBookkeepingCannotHideWhatWentWrong(unittest.TestCase):
    """
    The traceback in issue #572 ends in the today view's copy of this; the
    planning view had the identical line, unreported, and was fixed with it.

    Each project's group of tasks is drawn inside an expander whose open or
    closed state is mirrored into a plain dict, and the mirror-update sits in
    a `finally` on purpose: the buttons inside the group call st.rerun(),
    which unwinds the script by raising, and the update has to happen anyway
    or the group snaps shut on the way to editing a task.

    That makes the `finally` a place where an exception is normally already
    on its way out - and an exception raised in a `finally` replaces it. The
    update read st.session_state[expander_key] directly, so on any run where
    the widget had not registered that key, the KeyError from the bookkeeping
    was reported in place of whatever had actually happened. In #572 the run
    was the application restarted as a bare script, where session state does
    not work at all and no widget registers anything.

    The restart that caused it is fixed elsewhere (update.py, and the launcher
    that carries the request out). This is about the line only being able to
    tell the truth.
    """

    def _final_bodies(self, name):
        bodies = []
        for node in ast.walk(_function(name)):
            if isinstance(node, ast.Try) and node.finalbody:
                bodies.append(node.finalbody)
        return bodies

    def test_both_views_still_have_bookkeeping_in_a_finally(self):
        """
        Guards the two tests below: if a `finally` is ever restructured away
        they would pass by finding nothing, and stop meaning anything.
        """
        for name in EXPANDER_BODIES:
            with self.subTest(view=name):
                self.assertTrue(self._final_bodies(name),
                                'no finally left in %s - the tests below no '
                                'longer check what they were written for' % name)

    def test_nothing_in_a_finally_indexes_session_state(self):
        for name in EXPANDER_BODIES:
            for body in self._final_bodies(name):
                with self.subTest(view=name):
                    self.assertEqual(
                        _session_state_reads(body), [],
                        'a missing key here raises in a finally, which throws '
                        'away the exception that was already unwinding - '
                        'use .get()')

    def test_the_mirror_falls_back_to_the_state_it_drew_with(self):
        """
        .get() with no default answers None, and None is not "unknown", it is
        "collapsed" - the group would shut itself the first time the widget
        stayed quiet. The value the expander was drawn with is the honest
        answer: nothing told us it changed.
        """
        for name in EXPANDER_BODIES:
            defaults_seen = 0
            for body in self._final_bodies(name):
                for statement in body:
                    for node in ast.walk(statement):
                        if (isinstance(node, ast.Call)
                                and isinstance(node.func, ast.Attribute)
                                and node.func.attr == 'get'
                                and isinstance(node.func.value, ast.Attribute)
                                and node.func.value.attr == 'session_state'):
                            with self.subTest(view=name):
                                self.assertEqual(
                                    len(node.args), 2,
                                    'st.session_state.get() here needs the '
                                    'fallback value, not None')
                            defaults_seen += 1
            with self.subTest(view=name):
                self.assertTrue(defaults_seen,
                                '%s is not reading session state in its '
                                'finally at all any more - this test is '
                                'checking nothing' % name)


class TestTheProgressBarOverTodaysTasks(unittest.TestCase):
    """
    The bar under the active-work box. Everything it computes lives in
    completion_ratio() and is tested there; what can only be checked here is
    how the view uses it.
    """

    def _progress_calls(self):
        calls = []
        for node in ast.walk(_function('_today_tasks_body')):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == 'progress'):
                calls.append(node)
        return calls

    def test_the_view_draws_exactly_one_bar(self):
        self.assertEqual(len(self._progress_calls()), 1)

    def test_the_bar_carries_no_figures(self):
        """
        Asked for explicitly: the bar and nothing else. st.progress' second
        argument is a caption printed beside it, and the list below already
        names every task and marks the finished ones.
        """
        bar = self._progress_calls()[0]
        self.assertEqual(len(bar.args), 1, 'the value, and nothing else')
        self.assertEqual(bar.keywords, [], 'no text= beside the bar')

    def test_no_bar_is_drawn_on_a_day_with_no_tasks(self):
        """
        completion_ratio() answers None then, and an unguarded st.progress
        would raise on it - but the point is what a reader would see: a bar
        sitting at zero on a day that asks nothing of them.
        """
        guarded = False
        for node in ast.walk(_function('_today_tasks_body')):
            if not isinstance(node, ast.If):
                continue
            test = node.test
            looks_like_a_none_check = (
                isinstance(test, ast.Compare)
                and len(test.ops) == 1
                and isinstance(test.ops[0], ast.IsNot)
                and isinstance(test.comparators[0], ast.Constant)
                and test.comparators[0].value is None)
            if looks_like_a_none_check and any(
                    isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Attribute)
                    and inner.func.attr == 'progress'
                    for statement in node.body
                    for inner in ast.walk(statement)):
                guarded = True
        self.assertTrue(guarded,
                        'st.progress has to sit behind an "is not None" check '
                        'on the ratio')

    def test_the_bar_is_read_from_the_same_list_as_the_tasks_below(self):
        """
        Both come from today_tasks_all, which is built once. Two reads of the
        tracker could disagree, and then the bar would contradict the list it
        sits directly above.
        """
        built = [node for node in ast.walk(_function('_today_tasks_body'))
                 if isinstance(node, ast.Name)
                 and node.id == 'today_tasks_all'
                 and isinstance(node.ctx, ast.Store)]
        self.assertEqual(len(built), 1,
                         'today_tasks_all is assembled in one place only')


class TestTheActiveWorkButtonsMatchTheTaskRows(unittest.TestCase):
    """
    The three buttons behind the active-work box are meant to sit at the same
    spacing as the three behind every task in the list below them.

    That is not a matter of using the same weights. Streamlit gives a column
    `flex: 1 1 calc(share% - 16px)` and these buttons are a fixed 40px, so the
    gap between two of them comes out as the column width minus 24. The task
    rows are drawn inside an expander, whose padding makes their row about
    34px narrower than the active-work row - so an equal share of each gives
    an unequal column. Dividing the wider row by one more takes that back
    out; measured in the browser, the two gaps come to 14.3px and 14.7px.

    Nothing about that survives a change to either row's weights, and nothing
    about it fails loudly - the buttons just drift apart again. Hence this.
    """

    def _button_rows(self, function_name):
        """Every st.columns([...]) in the function that ends in three 1s."""
        for node in ast.walk(_function(function_name)):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == 'columns'
                    and node.args
                    and isinstance(node.args[0], ast.List)):
                continue
            weights = ast.literal_eval(node.args[0])
            # The row is identified by how many trailing single-weight button
            # columns it has, which both rows share.
            if len(weights) >= 4 and weights[-3:] == [1, 1, 1]:
                yield weights

    def test_the_active_work_row_has_three_button_columns(self):
        rows = list(self._button_rows('_today_tasks_body'))
        self.assertTrue(rows, 'no row with three button columns left')

    def test_the_two_rows_stay_one_apart(self):
        rows = list(self._button_rows('_today_tasks_body'))
        totals = sorted(sum(row) for row in rows)
        self.assertEqual(len(set(totals)), 2,
                         'expected exactly two kinds of row here: the '
                         'active-work row and the task rows')
        narrower, wider = totals[0], totals[-1]
        self.assertEqual(wider, narrower + 1,
                         'the active-work row is divided by one more than the '
                         'task rows, to cancel out the expander padding that '
                         'makes the task rows narrower - change one and the '
                         'buttons stop lining up')


if __name__ == '__main__':
    unittest.main()
