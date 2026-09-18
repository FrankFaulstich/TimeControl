"""
That every notes field is actually given the Markdown editor.

tests/test_markdown_editor.py checks what the editor does. This checks the
one thing that cannot be seen from there: whether it is switched on. There
are three notes fields in the interface, in three different views, and a
fourth would be easy to add without noticing the other three do something
this one does not.

Read from the source rather than by running it. A Streamlit script cannot be
imported without starting a Streamlit session, which is exactly why a view
can quietly lose a line and nothing says so until somebody opens it.
"""

import ast
import os
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
STREAMLIT_MODULE = os.path.join(REPO_ROOT, 'sl', 'SL_Menu.py')

ENHANCER = 'render_markdown_editor'


def _tree():
    with open(STREAMLIT_MODULE, encoding='utf-8') as handle:
        return ast.parse(handle.read(), STREAMLIT_MODULE)


def _calls(node, name):
    """Every call to `name(...)` anywhere under `node`."""
    found = []
    for child in ast.walk(node):
        if (isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
                and child.func.id == name):
            found.append(child)
        elif (isinstance(child, ast.Call)
              and isinstance(child.func, ast.Attribute)
              and child.func.attr == name):
            found.append(child)
    return found


def _note_areas(tree):
    """
    Every st.text_area in the interface that holds a note, by its key.

    Found by the key rather than by the label, because the label is
    translated and a German installation would read "Notizen (Markdown)".
    """
    def spelled_out(value):
        if isinstance(value, ast.Constant):
            return value.value if isinstance(value.value, str) else None
        if isinstance(value, ast.JoinedStr):
            # An f-string, one field per imported email. The literal parts are
            # enough to recognise it.
            return ''.join(part.value for part in value.values
                           if isinstance(part, ast.Constant))
        return None

    # A key can be put in a local first and handed over by name. Following
    # that is not tidiness: a field whose key this cannot read is a field this
    # stops watching, and it would go on passing while the editor quietly came
    # off it.
    by_name = {}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)):
            written = spelled_out(node.value)
            if written:
                by_name[node.targets[0].id] = written

    keys = []
    for call in _calls(tree, 'text_area'):
        for keyword in call.keywords:
            if keyword.arg != 'key':
                continue
            if isinstance(keyword.value, ast.Name):
                found = by_name.get(keyword.value.id)
            else:
                found = spelled_out(keyword.value)
            if found:
                keys.append(found)
    return [key for key in keys if 'note' in key]


def _enclosing_function(tree, node):
    for candidate in ast.walk(tree):
        if not isinstance(candidate, ast.FunctionDef):
            continue
        for child in ast.walk(candidate):
            if child is node:
                return candidate.name
    return None


class TestEveryNotesFieldIsAMarkdownEditor(unittest.TestCase):

    def setUp(self):
        self.tree = _tree()

    def test_the_source_still_looks_the_way_this_reads_it(self):
        """
        Guards the rest. If the notes fields are built some other way, the
        checks below would find nothing missing because they find nothing.
        """
        self.assertTrue(_note_areas(self.tree),
                        'no notes text_area found in %s any more'
                        % STREAMLIT_MODULE)
        self.assertTrue(_calls(self.tree, ENHANCER),
                        '%s() is not called anywhere any more' % ENHANCER)

    def test_there_are_still_three_of_them(self):
        """
        Not a rule, a tripwire. A fourth notes field is welcome; it just has
        to be given the editor too, and this is what says so.
        """
        self.assertEqual(sorted(_note_areas(self.tree)),
                         ['edit_task_note', 'email_task_note_',
                          'new_task_note'])

    def test_each_view_that_has_one_switches_the_editor_on(self):
        with_a_field = set()
        for call in _calls(self.tree, 'text_area'):
            for keyword in call.keywords:
                if keyword.arg == 'key':
                    name = _enclosing_function(self.tree, call)
                    text = ast.dump(keyword.value)
                    if 'note' in text:
                        with_a_field.add(name)
        with_the_editor = {_enclosing_function(self.tree, call)
                           for call in _calls(self.tree, ENHANCER)}
        missing = sorted(with_a_field - with_the_editor - {None})
        self.assertEqual(
            missing, [],
            'these views draw a notes field and leave it a plain text box: '
            '%s. Call %s() with that field\'s key.'
            % (', '.join(missing), ENHANCER))

    def test_the_keys_it_is_given_are_the_keys_the_fields_have(self):
        """
        The script finds a field by its key. A call naming a key no field
        uses enhances nothing, and says nothing about it either.
        """
        fields = set(_note_areas(self.tree))
        for call in _calls(self.tree, ENHANCER):
            for argument in call.args:
                if isinstance(argument, ast.Constant):
                    with self.subTest(key=argument.value):
                        self.assertIn(argument.value, fields)

    def test_the_enhancer_is_defined_where_it_is_used(self):
        names = [node.name for node in self.tree.body
                 if isinstance(node, ast.FunctionDef)]
        self.assertIn(ENHANCER, names)


if __name__ == '__main__':
    unittest.main()
