"""
The form that saves itself, and the trap that comes with saving itself.

The edit panel on the email-task screen has no Save button: it writes as soon
as a field differs from what is stored. That is only the same thing as "the
person edited it" while the widgets are kept in step with the document -
because Streamlit's `value=` is the value a widget STARTS with, and is ignored
on every redraw after the first.

Without that, the form held whatever it was drawn with and put it back over
anything that had changed since: a Today flag set by the daily sweep, by the
star in another view, or by the other machine. Sent onwards as an edit, it
made flags come and go on both machines.

Read out of the source rather than run, for the reason the other view tests
here give: a Streamlit script cannot be imported without starting a session.
"""

import ast
import os
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
VIEW = os.path.join(REPO_ROOT, 'sl', 'SL_Menu.py')

# The four fields of the panel, by the key each widget is created with.
FIELDS = ('email_task_name_', 'email_task_due_date_',
          'email_task_today_', 'email_task_note_')


def _source():
    with open(VIEW, encoding='utf-8') as handle:
        return handle.read()


def _tree():
    return ast.parse(_source(), VIEW)


def _keys_by_name(tree):
    """
    Which local name holds which of the panel's keys.

    The keys are put in locals rather than repeated at each use, so a scan for
    the literal finds only the assignments. This maps `today_key` back to
    `email_task_today_` so the widgets and the follow calls can be matched by
    the field they are actually about.
    """
    held = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)):
            continue
        spelled = ast.unparse(node.value)
        for field in FIELDS:
            if field in spelled:
                held[node.targets[0].id] = field
    return held


def _field_of(value, held):
    """The field a `key=` or first argument refers to, directly or by name."""
    spelled = ast.unparse(value)
    for field in FIELDS:
        if field in spelled:
            return field
    return held.get(spelled)


def _widget_calls():
    """Every widget in the file that is created with one of the panel's keys."""
    tree = _tree()
    held = _keys_by_name(tree)
    made = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        for keyword in node.keywords:
            if keyword.arg != 'key':
                continue
            field = _field_of(keyword.value, held)
            if field:
                made.append((field, node))
    return made


class TestTheWidgetsFollowTheDocument(unittest.TestCase):

    def test_all_four_fields_are_still_drawn(self):
        """If a key is renamed, the checks below would pass by being empty."""
        found = {field for field, _node in _widget_calls()}
        self.assertEqual(found, set(FIELDS),
                         'the panel no longer has the fields this guards')

    def test_none_of_them_is_given_a_starting_value(self):
        """
        `value=` beside a key is the trap itself: ignored after the first
        redraw, so the widget quietly keeps its own copy while the code reads
        as though it were being refreshed from the document.
        """
        for field, node in _widget_calls():
            passed = {keyword.arg for keyword in node.keywords}
            self.assertNotIn(
                'value', passed,
                '%s is given value= beside its key - it will be ignored on '
                'every redraw but the first' % field)

    def test_each_of_them_is_kept_in_step_instead(self):
        tree = _tree()
        held = _keys_by_name(tree)
        followed = set()
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == 'follow_stored_value' and node.args):
                field = _field_of(node.args[0], held)
                if field:
                    followed.add(field)
        self.assertEqual(followed, set(FIELDS),
                         'not every field follows the stored value: %s'
                         % ', '.join(sorted(set(FIELDS) - followed)))


class TestKeepingInStepDoesTheRightThing(unittest.TestCase):
    """
    follow_stored_value() cannot be imported - it lives in a Streamlit script -
    so its logic is read out of the source and exercised against a plain dict
    standing in for the session state. Worth the trouble: getting this the
    wrong way round is what the whole bug was.
    """

    def _follow(self):
        """The real function, compiled out of the module with st stubbed."""
        for node in _tree().body:
            if (isinstance(node, ast.FunctionDef)
                    and node.name == 'follow_stored_value'):
                module = ast.Module(body=[node], type_ignores=[])
                namespace = {'st': self.streamlit, '_NOT_SEEN_YET': object()}
                exec(compile(ast.fix_missing_locations(module), VIEW, 'exec'),
                     namespace)
                return namespace['follow_stored_value']
        raise AssertionError('follow_stored_value() is gone from %s' % VIEW)

    def setUp(self):
        class FakeStreamlit:
            session_state = {}
        self.streamlit = FakeStreamlit()
        self.streamlit.session_state = {}
        self.follow = self._follow()

    def test_the_first_time_it_seeds_the_widget(self):
        self.follow('w', True)
        self.assertIs(self.streamlit.session_state['w'], True)

    def test_a_value_changed_on_disk_moves_the_widget_to_it(self):
        """The case the bug was: the sweep, the star, or the other machine."""
        self.follow('w', True)
        self.follow('w', False)
        self.assertIs(self.streamlit.session_state['w'], False)

    def test_an_edit_stands_while_the_document_has_not_moved(self):
        self.follow('w', True)
        self.streamlit.session_state['w'] = False      # the person unticked it
        self.follow('w', True)
        self.assertIs(self.streamlit.session_state['w'], False,
                      'the edit was thrown away')

    def test_it_does_not_rewrite_the_widget_on_every_pass(self):
        """
        Or an edit would be undone by the very next redraw, which is the same
        bug wearing the other hat.
        """
        self.follow('w', 'stored')
        self.streamlit.session_state['w'] = 'typed'
        for _ in range(5):
            self.follow('w', 'stored')
        self.assertEqual(self.streamlit.session_state['w'], 'typed')

    def test_it_hands_back_the_stored_value_to_compare_against(self):
        self.assertEqual(self.follow('w', 'stored'), 'stored')

    def test_a_stored_value_of_false_is_not_mistaken_for_absent(self):
        """
        The sentinel matters: False, None and '' are all real values here, and
        `.get(key)` alone would reseed the widget on every pass over them.
        """
        self.follow('w', False)
        self.streamlit.session_state['w'] = True       # the person ticked it
        self.follow('w', False)
        self.assertIs(self.streamlit.session_state['w'], True)

    def test_and_neither_is_none(self):
        self.follow('w', None)
        self.streamlit.session_state['w'] = 'typed'
        self.follow('w', None)
        self.assertEqual(self.streamlit.session_state['w'], 'typed')

    def test_the_first_pass_seeds_the_widget_whatever_the_value_is(self):
        """
        Where the sentinel actually earns its keep. Without it, "nothing seen
        yet" is told apart from a stored value by comparing against None - so
        a stored None looks like a value already seen, the widget is never
        seeded, and it starts on whatever the widget type defaults to instead
        of on what the document says.
        """
        for stored in (None, False, '', 0):
            self.streamlit.session_state = {}
            self.follow('w', stored)
            self.assertIn('w', self.streamlit.session_state,
                          'a stored %r left the widget unseeded' % (stored,))
            self.assertEqual(self.streamlit.session_state['w'], stored)


if __name__ == '__main__':
    unittest.main()
