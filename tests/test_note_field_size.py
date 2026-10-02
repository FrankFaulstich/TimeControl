"""
How the notes field of the task forms is sized (issues #327 and #636).

#327 asked for the field to follow the window, and got it by forcing the size:
the field was set to 100% of a box whose height was fixed with !important. A
browser obeys !important over the size a drag gives the field itself, so the
resize handle could be grabbed and nothing moved - #636. Seen on the Mac, and
shown the same in Chromium and in WKWebView: the drag wrote its height onto
the field, and the stylesheet overruled it.

And how the Preview tab beside the field frames the note, which it did not:
the frame was an empty strip above the note rather than a box around it.

SL_Menu cannot be imported without a Streamlit session, so the functions that
write this CSS and the preview are taken out of the source and run against a
stand-in that collects what they would have put on the page; the CSS is then
read rule by rule and property by property. The rest is read from the source,
the way the other view tests here do.
"""

import ast
import contextlib
import glob
import os
import re
import unittest

from tt.markdown_editor import editor_script

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
SETTINGS_VIEW = os.path.join(REPO_ROOT, 'sl', 'SL_Menu.py')
START = r'max\(\s*%dpx\s*,\s*calc\(\s*100vh\s*-\s*560px\s*\)\s*\)'


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


class _Page:
    """
    Stands in for streamlit: keeps what would have been written, and in
    which containers - innermost last.
    """

    def __init__(self):
        self.written = []
        self.drawn = []
        self.containers = []

    def markdown(self, body, unsafe_allow_html=False):
        self.written.append(body)
        self.drawn.append((body, unsafe_allow_html, tuple(self.containers)))

    @contextlib.contextmanager
    def container(self, border=None, key=None, **_options):
        self.containers.append({'border': border, 'key': key})
        try:
            yield
        finally:
            self.containers.pop()


def _run(name, *args):
    """Runs name(*args) out of SL_Menu.py against a _Page, and gives that back."""
    module = ast.Module(body=[_function(_tree(), name)], type_ignores=[])
    page = _Page()
    namespace = {'st': page, '_': lambda text: text}
    exec(compile(module, SETTINGS_VIEW, 'exec'), namespace)
    namespace[name](*args)
    return page


def _css(min_height):
    """What render_note_area_css(min_height) puts on the page."""
    return ''.join(_run('render_note_area_css', min_height).written)


def _rules(css):
    """
    Every rule as (one selector, {property: value}), a selector list split
    into its selectors - so a list that names the box around the field as well
    as the field is judged one selector at a time.
    """
    out = []
    for selectors, body in re.findall(r'([^{}]+)\{([^{}]*)\}', css):
        declarations = {}
        for part in body.split(';'):
            if ':' in part:
                name, value = part.split(':', 1)
                declarations[name.strip().lower()] = value.strip()
        for selector in selectors.split(','):
            selector = ' '.join(selector.replace('<style>', '').split())
            if selector:
                out.append((selector, declarations))
    return out


def _names_wrapper(selector):
    return "data-baseweb='textarea'" in selector or 'data-baseweb="textarea"' in selector


def _is_field(selector):
    return _names_wrapper(selector) and selector.endswith(' textarea')


def _is_panel(selector):
    return 'stTabPanel' in selector or 'tab-panel' in selector


class TestTheNotesFieldCanBeResized(unittest.TestCase):

    def test_its_height_is_given_not_forced(self):
        """
        The whole of #636. An inline height - which is what a drag sets - wins
        over an ordinary rule, and loses to one marked !important. Nor may a
        minimum or a maximum on the field hold a drag back the other way.
        """
        fields = [d for s, d in _rules(_css(250)) if _is_field(s)]
        self.assertTrue(fields, "no rule for the field")
        for declarations in fields:
            self.assertIn('height', declarations)
            self.assertNotIn('!important', declarations['height'])
            self.assertNotIn('min-height', declarations)
            self.assertNotIn('max-height', declarations)
            self.assertNotEqual(declarations.get('resize'), 'none')

    def test_nothing_around_it_has_a_fixed_height(self):
        """
        The other half of the old trap: a box of fixed height around the
        field, with the field at 100% of it. Grown by a drag, the field would
        only be clipped.
        """
        for selector, declarations in _rules(_css(250)):
            if (_names_wrapper(selector) and not _is_field(selector)) or _is_panel(selector):
                with self.subTest(selector=selector):
                    self.assertNotIn('height', declarations)
                    self.assertNotIn('max-height', declarations)

    def test_it_still_starts_as_tall_as_the_window_allows(self):
        """
        What #327 asked for stays: the starting height follows the window,
        and never falls below the minimum each form asks for - max(), not
        min(), which would make that minimum a ceiling.
        """
        for minimum in (250, 200):
            with self.subTest(minimum=minimum):
                for selector, declarations in _rules(_css(minimum)):
                    if _is_field(selector):
                        self.assertRegex(declarations['height'], '^%s$' % (START % minimum))

    def test_the_preview_tab_gets_the_starting_height_as_a_minimum(self):
        """
        So a short note does not make the page collapse when switching to
        Preview. The same value as the field starts with, and named both
        ways: Streamlit 1.55 no longer puts its own test id on a tab panel,
        and the old selector matched nothing there.
        """
        rules = _rules(_css(250))
        start = [d['height'] for s, d in rules if _is_field(s)][0]
        panels = [(s, d) for s, d in rules if _is_panel(s)]
        self.assertTrue(any('stTabPanel' in s for s, _d in panels))
        self.assertTrue(any('data-baseweb="tab-panel"' in s for s, _d in panels))
        for selector, declarations in panels:
            with self.subTest(selector=selector):
                self.assertEqual(declarations.get('min-height'), start)

    def test_but_not_the_tab_that_holds_the_field(self):
        """
        There a minimum would keep the page from shrinking with a field
        dragged shorter, and leave blank space above the buttons.
        """
        for selector, _declarations in _rules(_css(250)):
            if _is_panel(selector):
                with self.subTest(selector=selector):
                    self.assertIn(':not(:has(textarea))', selector)


class TestBothFormsUseIt(unittest.TestCase):

    def test_adding_and_editing_a_task_are_sized_the_same_way(self):
        tree = _tree()
        for view, minimum in (('view_add_task_form', 250), ('view_edit_task_form', 200)):
            with self.subTest(view=view):
                calls = [node for node in ast.walk(_function(tree, view))
                         if isinstance(node, ast.Call) and getattr(node.func, 'id', None) == 'render_note_area_css']
                self.assertEqual(len(calls), 1, "%s does not size its notes field" % view)
                given = ([a.value for a in calls[0].args if isinstance(a, ast.Constant)]
                         + [k.value.value for k in calls[0].keywords if k.arg == 'min_height'])
                self.assertEqual(given, [minimum])

    def test_the_old_forced_size_is_gone_everywhere(self):
        """
        Anywhere on the page it would win again: a stylesheet does not know
        which view wrote it. Every piece of CSS in SL_Menu.py and in the
        themes, %-escapes undone, with no height forced on the field, the box
        around it or a tab panel.
        """
        sheets = [node.value.replace('%%', '%') for node in ast.walk(_tree())
                  if isinstance(node, ast.Constant) and isinstance(node.value, str) and '{' in node.value]
        for path in glob.glob(os.path.join(REPO_ROOT, 'sl', '*.css')):
            with open(path, encoding='utf-8') as handle:
                sheets.append(handle.read())
        for css in sheets:
            for selector, declarations in _rules(css):
                if _names_wrapper(selector) or _is_panel(selector) or selector.endswith('textarea'):
                    with self.subTest(selector=selector):
                        self.assertNotIn('!important', declarations.get('height', ''))
                        if not _is_field(selector) and not selector.endswith('textarea'):
                            self.assertNotIn('height', declarations)


class TestTheDraggedSizeSurvivesARedraw(unittest.TestCase):
    """
    Streamlit 1.55 tells blocks apart by their position, so an element that
    comes and goes above the notes field makes the field a new one - which
    starts at the page's height again, a drag undone without anybody touching
    it.
    """

    def test_the_warning_style_is_written_on_every_run(self):
        """
        The red border for a missing or out-of-order date: written empty when
        there is nothing wrong, not left out, so nothing below it moves.
        """
        tree = _tree()
        for view in ('view_add_task_form', 'view_edit_task_form'):
            with self.subTest(view=view):
                function = _function(tree, view)
                for node in ast.walk(function):
                    if isinstance(node, ast.If) and 'validation_error' in ast.unparse(node.test):
                        written = [n for n in ast.walk(ast.Module(body=node.body, type_ignores=[]))
                                   if isinstance(n, ast.Call) and ast.unparse(n.func) == 'st.markdown']
                        self.assertEqual(written, [], "the style still comes and goes with the error")
                guarded = [n for n in ast.walk(function)
                           if isinstance(n, ast.Call) and ast.unparse(n.func) == 'st.markdown'
                           and n.args and isinstance(n.args[0], ast.IfExp)
                           and 'validation_error' in ast.unparse(n.args[0].test)]
                self.assertEqual(len(guarded), 1, "the style is not written in place on every run")

    def test_the_editor_gives_a_redrawn_field_its_size_back(self):
        """
        For everything else that can appear above the field - a message, a
        sync notice. Only a drag writes a height onto the element itself, so
        that is what is kept, per field, and given back when it is drawn again.
        """
        script = editor_script(['new_task_note'])
        self.assertIn('function keepSize(area, key)', script)
        attach = re.search(r'function attach\(area, key\) \{(.*?)\n  function ', script, re.S)
        self.assertTrue(attach, "attach() no longer knows which field it attaches to")
        self.assertIn('keepSize(area, key);', attach.group(1))
        self.assertIn('attach(area, key);', script)
        # Given back only to a field that has no height of its own yet...
        self.assertIn('if (sizes[key] && !area.style.height)', script)
        # ...and remembered only from one a drag has set.
        self.assertTrue(re.search(r'if \(area\.style\.height\) \{\s*sizes\[key\] = area\.style\.height;', script),
                        "the size is not remembered from the field's own height")


class TestThePreviewIsFramed(unittest.TestCase):
    """
    The frame on the Preview tab was a <div> opened by one st.markdown and
    closed by another. Every st.markdown is an element of its own, so the
    browser closed the <div> where the first one ended: an empty bordered
    strip, and the note below it, unframed.
    """

    def _drawn(self, note):
        page = _run('render_note_preview', note)
        self.assertEqual(page.containers, [], "a container was left open")
        return page.drawn

    def test_the_note_is_drawn_inside_the_frame(self):
        note = '# Heading\n\n- one\n- two'
        drawn = self._drawn(note)
        self.assertEqual(len(drawn), 1)
        body, _unsafe, containers = drawn[0]
        self.assertEqual(body, note)
        self.assertEqual(len(containers), 1, "the note is not inside the frame")
        self.assertTrue(containers[0]['border'])
        self.assertEqual(containers[0]['key'], 'note_preview')

    def test_an_empty_note_shows_the_placeholder_there(self):
        drawn = self._drawn('')
        self.assertEqual([(body, len(containers)) for body, _unsafe, containers in drawn],
                         [('*No notes provided.*', 1)])

    def test_html_in_a_note_stays_text(self):
        """
        The note is written as the Markdown it is, not wrapped into a string
        of HTML that is let through - that would make the HTML typed into a
        note part of the page.
        """
        note = '<b>bold</b> <script>alert(1)</script>'
        for body, unsafe, _containers in self._drawn(note):
            self.assertFalse(unsafe)
            self.assertEqual(body, note)

    def test_the_frame_starts_at_the_height_of_the_field(self):
        """
        Found by the key the frame is drawn with, so the two cannot drift
        apart. A minimum, not a height: a longer note makes it grow.
        """
        key = self._drawn('note')[0][2][0]['key']
        rules = _rules(_css(250))
        start = [d['height'] for s, d in rules if _is_field(s)][0]
        frame = [d for s, d in rules if s == '.st-key-%s' % key]
        self.assertEqual(len(frame), 1, "no rule for the frame")
        self.assertEqual(frame[0].get('min-height'), start)
        self.assertNotIn('height', frame[0])
        self.assertNotIn('max-height', frame[0])

    def test_both_forms_show_their_note_that_way(self):
        tree = _tree()
        for view, key in (('view_add_task_form', 'new_task_note'), ('view_edit_task_form', 'edit_task_note')):
            with self.subTest(view=view):
                tabs = [node for node in ast.walk(_function(tree, view))
                        if isinstance(node, ast.With) and ast.unparse(node.items[0].context_expr) == 'tab_preview']
                self.assertEqual(len(tabs), 1)
                self.assertEqual([ast.unparse(statement) for statement in tabs[0].body],
                                 ['render_note_preview(st.session_state.%s)' % key])

    def test_no_markdown_leaves_a_div_for_the_next_one_to_close(self):
        """
        Anywhere in SL_Menu.py: whatever one st.markdown opens, it has to
        close itself. The next one cannot.
        """
        for node in ast.walk(_tree()):
            if isinstance(node, ast.Call) and ast.unparse(node.func) == 'st.markdown' and node.args:
                text = ''.join(part.value for part in ast.walk(node.args[0])
                               if isinstance(part, ast.Constant) and isinstance(part.value, str))
                with self.subTest(line=node.lineno):
                    self.assertEqual(text.count('<div'), text.count('</div>'))
