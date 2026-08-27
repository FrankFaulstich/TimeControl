import ast
import os
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
TODAY_VIEW = os.path.join(REPO_ROOT, 'sl', 'SL_Menu.py')


def _function(name):
    """
    The parsed body of one top-level function in the Streamlit module.

    Read by source rather than by running it: a Streamlit script cannot be
    imported without starting a Streamlit session, which is the same reason
    the defect below could sit in it unnoticed.
    """
    with open(TODAY_VIEW, encoding='utf-8') as handle:
        tree = ast.parse(handle.read(), TODAY_VIEW)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError('%s() is not in %s any more' % (name, TODAY_VIEW))


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
    The traceback in issue #572 ends here.

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

    def _final_bodies(self, function):
        bodies = []
        for node in ast.walk(function):
            if isinstance(node, ast.Try) and node.finalbody:
                bodies.append(node.finalbody)
        return bodies

    def test_the_view_still_has_bookkeeping_in_a_finally(self):
        """
        Guards the two tests below: if the `finally` is ever restructured
        away they would pass by finding nothing, and stop meaning anything.
        """
        self.assertTrue(self._final_bodies(_function('_today_tasks_body')),
                        'no finally left in the today view - the tests below '
                        'no longer check what they were written for')

    def test_nothing_in_a_finally_indexes_session_state(self):
        for body in self._final_bodies(_function('_today_tasks_body')):
            self.assertEqual(
                _session_state_reads(body), [],
                'a missing key here raises in a finally, which throws away '
                'the exception that was already unwinding - use .get()')

    def test_the_mirror_falls_back_to_the_state_it_drew_with(self):
        """
        .get() with no default answers None, and None is not "unknown", it is
        "collapsed" - the group would shut itself the first time the widget
        stayed quiet. The value the expander was drawn with is the honest
        answer: nothing told us it changed.
        """
        defaults_seen = 0
        for body in self._final_bodies(_function('_today_tasks_body')):
            for statement in body:
                for node in ast.walk(statement):
                    if (isinstance(node, ast.Call)
                            and isinstance(node.func, ast.Attribute)
                            and node.func.attr == 'get'
                            and isinstance(node.func.value, ast.Attribute)
                            and node.func.value.attr == 'session_state'):
                        self.assertEqual(
                            len(node.args), 2,
                            'st.session_state.get() here needs the fallback '
                            'value, not None')
                        defaults_seen += 1
        self.assertTrue(defaults_seen,
                        'the mirror-update is not reading session state at '
                        'all any more - this test is checking nothing')


if __name__ == '__main__':
    unittest.main()
