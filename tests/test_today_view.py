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
        argument is a caption printed above it, and the list below already
        names every task and marks the finished ones.

        The figures do get named now (issue #607), but on hover - see
        TestTheProgressBarSaysItsFiguresOnHover below. Nothing is printed
        beside the bar, which is what this still guards.
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


class TestTheProgressBarSaysItsFiguresOnHover(unittest.TestCase):
    """
    Issue #607: resting the pointer on the bar names how many of the day's
    tasks are done.

    st.progress has no help= of its own, so the tooltip is borrowed from an
    empty st.markdown beside it and laid over the bar by a stylesheet. That
    makes three separate things that have to stay together - the bar, the
    markdown carrying the tooltip, and the stylesheet that moves it - and
    losing any one of them fails quietly: the bar keeps working, it just
    stops answering, or grows a stray question mark on a line of its own.
    """

    def _body(self):
        return _function('_today_tasks_body')

    def _calls(self, name, attribute=True):
        for node in ast.walk(self._body()):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if attribute and isinstance(func, ast.Attribute) and func.attr == name:
                yield node
            elif not attribute and isinstance(func, ast.Name) and func.id == name:
                yield node

    def test_the_bar_has_a_tooltip(self):
        with_help = [c for c in self._calls('markdown')
                     if any(k.arg == 'help' for k in c.keywords)]
        self.assertEqual(len(with_help), 1,
                         'exactly one markdown here carries the tooltip')

    def test_the_stylesheet_that_moves_it_is_actually_applied(self):
        """
        Without it Streamlit draws the tooltip as a question mark on its own
        line under the bar, and hovering the bar does nothing.
        """
        self.assertTrue(list(self._calls('render_progress_css', attribute=False)),
                        'render_progress_css() is never called')

    def test_the_figures_come_from_the_same_count_as_the_bar(self):
        """
        Counting again at the point of use is how a tooltip ends up
        disagreeing with the bar it sits on.
        """
        self.assertTrue(list(self._calls('completion_counts', attribute=False)),
                        'the tooltip is not reading completion_counts()')

    def test_the_bar_and_its_tooltip_stand_or_fall_together(self):
        """
        On a day with no tasks there is no bar - and there must be no lone
        tooltip either, hovering over nothing.
        """
        paired = False
        for node in ast.walk(self._body()):
            if not isinstance(node, ast.If):
                continue
            inner = [n for statement in node.body for n in ast.walk(statement)]
            hat_balken = any(isinstance(n, ast.Call)
                             and isinstance(n.func, ast.Attribute)
                             and n.func.attr == 'progress' for n in inner)
            hat_tooltip = any(isinstance(n, ast.Call)
                              and isinstance(n.func, ast.Attribute)
                              and n.func.attr == 'markdown'
                              and any(k.arg == 'help' for k in n.keywords)
                              for n in inner)
            if hat_balken and hat_tooltip:
                paired = True
        self.assertTrue(paired,
                        'the bar and its tooltip must sit behind the same '
                        'guard, or an empty day keeps one of them')


if __name__ == '__main__':
    unittest.main()
