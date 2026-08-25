import ast
import os
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
SETTINGS_VIEW = os.path.join(REPO_ROOT, 'sl', 'SL_Menu.py')


def _function(name):
    """The parsed body of one top-level function in the settings module."""
    with open(SETTINGS_VIEW, encoding='utf-8') as handle:
        tree = ast.parse(handle.read(), SETTINGS_VIEW)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError('%s() is not in %s any more' % (name, SETTINGS_VIEW))


class TestTheSyncStateHasNoBranchThatCannotRun(unittest.TestCase):
    """
    The settings screen picks what to draw from a small `state` dict it builds
    itself. Read by source rather than by running it, because a Streamlit
    script cannot be imported without starting a Streamlit session - which is
    also why a branch of this kind can rot unnoticed.

    It did. The screen used to call sync_client.status() on every redraw and
    branch on all four states that returns; the call was removed because it
    put a blocking network round trip behind every keystroke, and the dict
    that replaced it cannot produce 'unreachable'. The branch for it stayed
    behind and read like coverage of a case that is in fact handled elsewhere.
    """

    def produced_and_compared(self, function):
        """
        Which values the state dict can hold, and which ones are branched on.
        """
        produced, compared = set(), set()
        for node in ast.walk(function):
            # {'state': 'ok', ...} - what the screen can decide it is in.
            if isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values):
                    if (isinstance(key, ast.Constant) and key.value == 'state'
                            and isinstance(value, ast.Constant)
                            and isinstance(value.value, str)):
                        produced.add(value.value)
            # state['state'] == 'ok' - what it does something about.
            if (isinstance(node, ast.Compare)
                    and isinstance(node.left, ast.Subscript)
                    and isinstance(node.left.value, ast.Name)
                    and node.left.value.id == 'state'
                    and isinstance(node.left.slice, ast.Constant)
                    and node.left.slice.value == 'state'
                    and len(node.ops) == 1 and isinstance(node.ops[0], ast.Eq)
                    and isinstance(node.comparators[0], ast.Constant)):
                compared.add(node.comparators[0].value)
        return produced, compared

    def test_every_state_branched_on_is_one_the_screen_can_be_in(self):
        produced, compared = self.produced_and_compared(_function('view_settings'))

        # If either comes back empty the shape has changed enough that this
        # test is no longer looking at anything, which must not read as a pass.
        self.assertTrue(produced, "no sync state is built here any more")
        self.assertTrue(compared, "no sync state is branched on here any more")

        self.assertEqual(sorted(compared - produced), [],
                         "branches on a state the screen can never be in")

    def test_every_state_the_screen_can_be_in_is_dealt_with(self):
        """
        The other direction, which is the worse one: a state nothing branches
        on falls through to whatever comes last, silently.
        """
        produced, compared = self.produced_and_compared(_function('view_settings'))

        # 'ok' and 'not_configured' are the two halves of one if/else rather
        # than named branches, so only 'ok' is compared against by name.
        implicit = {'not_configured'}
        self.assertEqual(sorted(produced - compared - implicit), [],
                         "a state the screen can be in has nothing to draw for it")


if __name__ == '__main__':
    unittest.main()
