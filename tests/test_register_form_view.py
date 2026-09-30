"""
The form that redeems an invitation code (issue #588), read out of the source.

Read rather than run, for the reason the other view tests here give: a
Streamlit script cannot be imported without starting a Streamlit session.

What is checked is the handful of things that would quietly cost somebody
their new account: that a password nobody can recover is asked for twice and
compared before anything is sent, that the widget keys cannot collide with
another form on the same screen, and that a device whose own sign-in merely
lapsed is not offered a second account instead of signing in again.
"""

import ast
import os
import re
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
SETTINGS_VIEW = os.path.join(REPO_ROOT, 'sl', 'SL_Menu.py')


def _source():
    with open(SETTINGS_VIEW, encoding='utf-8') as handle:
        return handle.read()


def _tree():
    return ast.parse(_source(), SETTINGS_VIEW)


def _function(tree, name):
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError('%s() is not in %s any more' % (name, SETTINGS_VIEW))


def _dotted(call):
    target = call.func
    if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
        return '%s.%s' % (target.value.id, target.attr)
    if isinstance(target, ast.Name):
        return target.id
    return getattr(target, 'attr', '')


def _calls(node, name):
    return [n for n in ast.walk(node) if isinstance(n, ast.Call) and _dotted(n) == name]


class TestTheRegisterForm(unittest.TestCase):

    def setUp(self):
        self.tree = _tree()
        self.form = _function(self.tree, 'render_register_form')

    def test_it_redeems_through_the_client(self):
        calls = _calls(self.form, 'sync_client.register')
        self.assertEqual(len(calls), 1, "the form no longer sends the code")
        self.assertEqual([a.id for a in calls[0].args if isinstance(a, ast.Name)],
                         ['base_url', 'code', 'username', 'password'])

    def test_the_two_passwords_are_compared_before_anything_is_sent(self):
        """
        The server keeps only a hash, and the operator never knew it: a typo
        here is a password nobody can get back.
        """
        for node in ast.walk(self.form):
            if (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
                    and isinstance(node.test.ops[0], ast.NotEq)
                    and {getattr(node.test.left, 'id', None),
                         getattr(node.test.comparators[0], 'id', None)} == {'password', 'repeated'}):
                sent_when_different = any(_calls(n, 'sync_client.register') for n in node.body)
                sent_when_same = any(_calls(n, 'sync_client.register') for n in node.orelse)
                self.assertFalse(sent_when_different, "sent although the passwords differ")
                self.assertTrue(sent_when_same, "not sent when they match")
                return
        self.fail("the two passwords are no longer compared")

    def test_failures_are_worded_for_this_form(self):
        self.assertTrue(_calls(self.form, 'register_error_message'))
        self.assertFalse(_calls(self.form, 'sign_in_error_message'),
                         "a failed registration would be called a failed sign-in")

    def test_success_wakes_the_worker(self):
        # Having no credential is what it had stopped for - so forced, and
        # only once there is one.
        nudges = _calls(self.form, 'sync_engine.nudge')
        self.assertEqual(len(nudges), 1)
        force = [k.value for k in nudges[0].keywords if k.arg == 'force']
        self.assertTrue(force and isinstance(force[0], ast.Constant) and force[0].value is True)
        on_success = [n for n in ast.walk(self.form)
                      if isinstance(n, ast.If) and "result.get('ok')" in ast.unparse(n.test)]
        self.assertTrue(on_success and any(nudges[0] in list(ast.walk(b)) for b in on_success[0].body))

    def test_its_keys_are_its_own(self):
        """
        The sign-in form sits right above it with fields of the same names.
        Streamlit refuses two widgets with one key, and takes the whole screen
        down saying so.
        """
        source = _source()
        keys = [k.value.value for n in ast.walk(self.form) if isinstance(n, ast.Call)
                for k in n.keywords if k.arg == 'key' and isinstance(k.value, ast.Constant)]
        # The form's own key too: st.form takes it as its first argument.
        keys += [n.args[0].value for n in _calls(self.form, 'st.form')
                 if n.args and isinstance(n.args[0], ast.Constant)]
        self.assertEqual(len(keys), 5, keys)
        for key in keys:
            with self.subTest(key=key):
                self.assertEqual(len(re.findall(r'["\']%s["\']' % re.escape(key), source)), 1)


class TestWhereItIsOffered(unittest.TestCase):

    def setUp(self):
        self.tree = _tree()
        self.parents = {}
        for node in ast.walk(self.tree):
            for child in ast.iter_child_nodes(node):
                self.parents[child] = node
        calls = _calls(self.tree, 'render_register_form')
        self.assertEqual(len(calls), 1)
        self.call = calls[0]

    def _branch(self, test_text):
        """The If whose test reads test_text, and which side of it the form is on."""
        child, node = self.call, self.parents.get(self.call)
        while node is not None:
            if isinstance(node, ast.If) and ast.unparse(node.test) == test_text:
                return node, ('body' if child in node.body else 'orelse')
            child, node = node, self.parents.get(node)
        self.fail("the form no longer sits under `if %s`" % test_text)

    def test_not_to_a_device_that_is_signed_in(self):
        # Registering from there would quietly move the device to a new, empty
        # account in place of its own.
        _, side = self._branch("state['state'] == 'ok'")
        self.assertEqual(side, 'orelse')

    def test_not_to_a_device_whose_own_sign_in_lapsed(self):
        """
        That account exists; what it needs is to sign in again. Offering a new
        account in its place is how somebody ends up with their data in two.
        """
        _, side = self._branch("state['state'] != 'rejected'")
        self.assertEqual(side, 'body')
        # And with the saved address, the one the code belongs to.
        self.assertIn("base_url", ast.unparse(self.call))

    def test_but_that_device_is_offered_a_way_to_it(self):
        """
        A lapsed sign-in can also be an account that is gone - deleted, or
        replaced by one made from an invitation. Signing out lives with the
        signed-in state, so without a way to forget the sign-in here the
        device would never reach the form at all.
        """
        node, _ = self._branch("state['state'] != 'rejected'")
        self.assertTrue(any(_calls(n, 'sync_client.logout') for n in node.orelse),
                        "a device whose account is gone cannot get past this state")


if __name__ == '__main__':
    unittest.main()
