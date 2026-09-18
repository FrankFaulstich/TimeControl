"""
The encryption block of the settings screen, read out of the source.

Read rather than run, for the reason the other view tests here give: a
Streamlit script cannot be imported without starting a Streamlit session. That
is also what makes this worth writing down - a branch in a file nothing can
import is exactly the sort of thing that rots unnoticed.

What is checked is not how the screen looks. It is the handful of properties
that would cost the user their data if they were quietly lost: that every
state the key store can report is drawn, that a passphrase which cannot be
recovered is asked for twice, that the only honest check of it is offered, and
that the irreversible action asks first.
"""

import ast
import os
import sys
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, REPO_ROOT)

from tt import sync_secret

SETTINGS_VIEW = os.path.join(REPO_ROOT, 'sl', 'SL_Menu.py')


def _source():
    with open(SETTINGS_VIEW, encoding='utf-8') as handle:
        return handle.read()


def _tree():
    return ast.parse(_source(), SETTINGS_VIEW)


def _function(name, tree=None):
    for node in (tree or _tree()).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError('%s() is not in %s any more' % (name, SETTINGS_VIEW))


def _calls(function):
    """Every call in a function, as dotted names where that is what they are."""
    out = []
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
            out.append('%s.%s' % (target.value.id, target.attr))
        elif isinstance(target, ast.Attribute):
            out.append(target.attr)
        elif isinstance(target, ast.Name):
            out.append(target.id)
    return out


def _secret_states_mentioned(function):
    """Which sync_secret.<STATE> constants the function refers to."""
    known = {name for name in dir(sync_secret)
             if name.isupper() and isinstance(getattr(sync_secret, name), str)}
    found = set()
    for node in ast.walk(function):
        if (isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == 'sync_secret'
                and node.attr in known):
            found.add(getattr(sync_secret, node.attr))
    return found


class TestEveryStateIsDrawn(unittest.TestCase):

    def test_the_screen_says_something_about_each_one(self):
        """
        Each of these is cleared by the user doing something - typing the
        passphrase, signing in, copying a settings file. A state that reached
        no branch would be a device that had silently stopped synchronising
        with an empty box where the reason should be.
        """
        mentioned = _secret_states_mentioned(_function('render_encryption_settings'))
        # OFF is the fall-through at the end rather than a comparison: it is
        # where nearly every installation is, and the screen's ordinary
        # content is the form that leaves it.
        missing = set(sync_secret.STATES) - mentioned - {sync_secret.OFF}
        self.assertEqual(missing, set(),
                         'the settings screen draws nothing for: %s'
                         % ', '.join(sorted(missing)))

    def test_the_states_group_covers_what_status_can_return(self):
        """STATES is what the test above measures against, so it has to be complete."""
        declared = {getattr(sync_secret, name) for name in dir(sync_secret)
                    if name.isupper() and isinstance(getattr(sync_secret, name), str)}
        self.assertEqual(set(sync_secret.STATES), declared)


class TestThePassphraseIsTreatedAsUnrecoverable(unittest.TestCase):

    def test_it_is_asked_for_twice_before_encryption_is_switched_on(self):
        """
        There is no way back from a typing mistake: whatever was typed becomes
        the key, and what it seals can only be opened by typing the same
        mistake again.
        """
        function = _function('render_encryption_settings')
        inputs = [node for node in ast.walk(function)
                  if isinstance(node, ast.Call)
                  and isinstance(node.func, ast.Attribute)
                  and node.func.attr == 'text_input']
        password_inputs = [node for node in inputs
                           if any(kw.arg == 'type'
                                  and getattr(kw.value, 'value', None) == 'password'
                                  for kw in node.keywords)]
        self.assertGreaterEqual(
            len(password_inputs), 3,
            'expected two fields to switch on and one to unlock; a lost '
            'confirmation field would let a typo become the key')

    def test_the_two_fields_are_actually_compared(self):
        """
        Two boxes on the screen prove nothing on their own. Without the
        comparison the second one is decoration, and a typo in the first
        becomes the key with no warning at all.
        """
        function = _function('render_encryption_settings')
        named = {}
        for node in ast.walk(function):
            if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                    and isinstance(node.value.func, ast.Attribute)
                    and node.value.func.attr == 'text_input'
                    and isinstance(node.targets[0], ast.Name)):
                for keyword in node.value.keywords:
                    wanted = getattr(keyword.value, 'value', None)
                    if keyword.arg == 'key' and wanted in ('e2ee_new_pass',
                                                           'e2ee_new_pass_repeat'):
                        named[wanted] = node.targets[0].id
        self.assertEqual(len(named), 2,
                         'the two passphrase fields are not both there')

        both = set(named.values())
        compared = any(
            both <= {inner.id for inner in ast.walk(node)
                     if isinstance(inner, ast.Name)}
            for node in ast.walk(function) if isinstance(node, ast.Compare))
        self.assertTrue(compared,
                        'the confirmation field is never compared with the first')

    def test_nothing_claims_to_know_whether_the_passphrase_was_right(self):
        """
        Nothing on this machine can tell. A mistyped passphrase yields a
        perfectly valid key that simply is not the right one - so the screen
        must offer the fingerprint to compare, not a verdict it cannot reach.
        """
        source = ast.get_source_segment(
            _source(), _function('render_encryption_settings')) or ''
        for claim in ('Wrong passphrase', 'Passphrase is correct',
                      'Incorrect passphrase'):
            self.assertNotIn(claim, source)

    def test_an_account_already_set_up_is_not_offered_the_setup_form(self):
        """
        Switching encryption back on must not go through the form that asks
        for a passphrase. enable() ignores one in that case, but a screen that
        asks for it invites the user to type something and believe it mattered
        - and the whole reason the key is kept through an off is so that what
        is already sealed on the server stays readable.
        """
        function = _function('render_encryption_settings')
        self.assertIn('sync_secret.is_set_up', _calls(function),
                      'the screen cannot tell "set up before" from "never set up"')

        guards = [node for node in ast.walk(function)
                  if isinstance(node, ast.If) and any(
                      isinstance(inner, ast.Call)
                      and isinstance(inner.func, ast.Attribute)
                      and inner.func.attr == 'is_set_up'
                      for inner in ast.walk(node.test))]
        self.assertTrue(guards, 'is_set_up() is called but nothing branches on it')
        self.assertTrue(
            any(isinstance(node, ast.Return) for guard in guards
                for node in ast.walk(guard)),
            'the branch for an account already set up falls through to the '
            'form below it')

    def test_the_fingerprint_is_shown_once_the_key_is_held(self):
        self.assertIn('sync_secret.fingerprint_of',
                      _calls(_function('render_encryption_settings')))


class TestAPassphraseChangedElsewhereHasAWayIn(unittest.TestCase):
    """
    The trap this exists to keep shut.

    When somebody changes the passphrase on another device, this one still
    holds a perfectly good key - the old one - so the key store calls it ready
    and the screen draws the ready view. The form for typing a passphrase
    lives in the branch for a machine that has no key, which this is not. Left
    like that, the only control within reach is the one that forgets the key,
    and that cannot be undone.
    """

    def test_the_ready_view_leads_to_a_passphrase_field(self):
        called = _calls(_function('render_encryption_settings'))
        self.assertIn('_render_key_from_elsewhere', called,
                      'a device holding an outdated key has nowhere to type the new one')

    def test_that_field_adds_a_key_rather_than_replacing_one(self):
        """
        unlock() writes through the keyring and keeps what was there. The
        screen must go through it rather than inventing its own path.
        """
        function = _function('_render_key_from_elsewhere')
        self.assertIn('sync_secret.unlock', _calls(function))
        self.assertNotIn('sync_secret.forget_key', _calls(function))
        self.assertNotIn('sync_secret.enable', _calls(function))

    def test_it_is_offered_for_the_case_it_is_meant_for(self):
        source = ast.get_source_segment(
            _source(), _function('_render_key_from_elsewhere')) or ''
        self.assertIn('e2ee_unknown_key', source,
                      'the form is shown without knowing that a key is missing')


class TestChangingThePassphrase(unittest.TestCase):

    def test_the_screen_offers_it(self):
        self.assertIn('_render_change_passphrase',
                      _calls(_function('render_encryption_settings')))

    def test_it_goes_through_the_engine_not_the_key_store(self):
        """
        sync_secret alone would adopt the new key at once, leaving the server
        sealed with the old one until some later snapshot caught up. The
        engine is where the sealing and the upload happen first.
        """
        function = _function('_render_change_passphrase')
        self.assertIn('sync_engine.change_passphrase', _calls(function))
        for direct in ('sync_secret.adopt', 'sync_secret.derive',
                       'sync_secret.enable'):
            self.assertNotIn(direct, _calls(function),
                             'the screen adopts a key without sealing anything')

    def test_the_new_passphrase_is_asked_for_twice_and_compared(self):
        function = _function('_render_change_passphrase')
        named = {}
        for node in ast.walk(function):
            if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                    and isinstance(node.value.func, ast.Attribute)
                    and node.value.func.attr == 'text_input'
                    and isinstance(node.targets[0], ast.Name)):
                for keyword in node.value.keywords:
                    wanted = getattr(keyword.value, 'value', None)
                    if keyword.arg == 'key' and wanted in ('e2ee_change_pass',
                                                           'e2ee_change_repeat'):
                        named[wanted] = node.targets[0].id
        self.assertEqual(len(named), 2, 'the repeat field is missing')
        both = set(named.values())
        self.assertTrue(
            any(both <= {inner.id for inner in ast.walk(node)
                         if isinstance(inner, ast.Name)}
                for node in ast.walk(function) if isinstance(node, ast.Compare)),
            'the two fields are never compared')


class TestTheIrreversibleActionAsksFirst(unittest.TestCase):

    def test_forgetting_the_key_happens_only_behind_the_confirmation(self):
        """
        Not "the confirmation flag is mentioned somewhere" - that stays true
        of a screen whose button calls forget_key() directly and leaves the
        confirmation drawing itself for nobody. Every call has to sit inside
        the branch that flag guards.
        """
        function = _function('_render_encryption_off_buttons')

        def forget_calls(node):
            return [inner for inner in ast.walk(node)
                    if isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Attribute)
                    and inner.func.attr == 'forget_key']

        every = forget_calls(function)
        self.assertTrue(every, 'nothing forgets the key at all any more')

        guarded = set()
        for node in ast.walk(function):
            if isinstance(node, ast.If) and any(
                    isinstance(inner, ast.Attribute)
                    and inner.attr == 'confirm_forget_e2ee_key'
                    for inner in ast.walk(node.test)):
                guarded.update(id(call) for call in forget_calls(node))

        unguarded = [call for call in every if id(call) not in guarded]
        self.assertEqual(
            unguarded, [],
            'forget_key() is reachable without passing the confirmation '
            '(line %s)' % (unguarded[0].lineno if unguarded else '-'))

    def test_switching_off_does_not_forget_the_key(self):
        """
        Two actions of different sizes. Switching off only stops new work
        being sealed; what is already on the server still needs the key, and
        turning it back on should not cost the passphrase again.
        """
        tree = _tree()
        for name in ('render_encryption_settings', '_render_encryption_off_buttons'):
            for node in ast.walk(_function(name, tree)):
                if not isinstance(node, ast.If):
                    continue
                body_calls = []
                for statement in node.body:
                    body_calls.extend(_calls(statement))
                if 'sync_secret.disable' in body_calls:
                    self.assertNotIn('sync_secret.forget_key', body_calls,
                                     'switching off also forgets the key')


class TestAMissingLibraryDoesNotTakeSyncDownWithIt(unittest.TestCase):
    """
    sync_secret pulls in `cryptography`, which synchronisation itself does not
    need. Importing it inside the existing sync try/except would mean a
    missing optional dependency switching off the whole sync screen - and
    explaining itself with a message about `requests`.
    """

    def _import_blocks(self):
        return [node for node in _tree().body if isinstance(node, ast.Try)]

    def test_it_has_an_import_guard_of_its_own(self):
        for block in self._import_blocks():
            names = [alias.name for statement in block.body
                     if isinstance(statement, ast.ImportFrom)
                     for alias in statement.names]
            if 'sync_secret' in names:
                self.assertNotIn('sync_client', names,
                                 'sync_secret shares a guard with the sync client')
                return
        self.fail('sync_secret is not imported behind a guard at all')

    def test_the_screen_checks_that_guard_before_using_it(self):
        function = _function('render_encryption_settings')
        self.assertTrue(
            any(isinstance(node, ast.Name) and node.id == 'E2EE_AVAILABLE'
                for node in ast.walk(function)),
            'the encryption block uses sync_secret without checking it imported')

    def test_the_flag_is_set_both_ways(self):
        source = _source()
        self.assertIn('E2EE_AVAILABLE = True', source)
        self.assertIn('E2EE_AVAILABLE = False', source)


class TestItIsWiredIntoTheSettingsScreen(unittest.TestCase):

    def test_the_block_is_actually_drawn(self):
        """A view function nothing calls is a feature nobody can reach."""
        callers = [node.name for node in _tree().body
                   if isinstance(node, ast.FunctionDef)
                   and 'render_encryption_settings' in _calls(node)
                   and node.name != 'render_encryption_settings']
        self.assertTrue(callers, 'render_encryption_settings() is never called')

    def test_saving_stores_what_the_key_store_returned(self):
        """
        enable() hands back a new configuration rather than changing the one
        it was given. Saving the old one instead would leave the key on this
        machine and the salt nowhere - the STALE state, for ever.
        """
        function = _function('render_encryption_settings')
        saves = [node for node in ast.walk(function)
                 if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name)
                 and node.func.id == 'save_config']
        self.assertTrue(saves, 'nothing is saved when encryption is switched on')
        saved_names = {node.args[0].id for node in saves
                       if node.args and isinstance(node.args[0], ast.Name)}
        self.assertIn('updated', saved_names,
                      'the configuration returned by enable() is not the one saved')


if __name__ == '__main__':
    unittest.main()
