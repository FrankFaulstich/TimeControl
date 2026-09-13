"""
The Markdown editor: what a key does to the text, and what the colours cover.

The behaviour is JavaScript, because it has to happen inside the browser's
own textarea. That does not make it untestable - the half that decides
things is deliberately free of the DOM (see tt/markdown_editor.py), so it can
be run under node and asked the same questions the browser would ask it.

Node is on every GitHub runner, so this runs in CI on all three platforms. It
skips where node is absent rather than failing: a developer without it should
still be able to run the suite, and the Python half below is checked either
way.

WHAT THIS CANNOT SEE
--------------------
The wiring: finding Streamlit's textareas, keeping the overlay lined up,
whether execCommand still reaches React. That needs a browser and a running
Streamlit, and it is checked by hand against the real app.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from tt.markdown_editor import (INDENT, _PURE_JS, editor_html, editor_script)

NODE = shutil.which('node')

# A harness rather than a test runner: it evaluates the module's own source,
# calls what it is told to call, and hands the answers back as JSON. Keeping
# the cases in Python means a failure reads as a Python assertion, naming the
# input, instead of as a stack trace from somewhere inside node.
_HARNESS = """
const fs = require('fs');
eval(fs.readFileSync(process.argv[2], 'utf8'));
const calls = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const out = calls.map(function (call) {
  return {enter: tcEnterAction, indent: tcIndentAction,
          highlight: tcHighlight}[call.fn].apply(null, call.args);
});
process.stdout.write(JSON.stringify(out));
"""


def _run_js(calls):
    """Calls the pure half under node and returns what it answered."""
    workspace = tempfile.mkdtemp(prefix='tc-mdjs-')
    try:
        source = os.path.join(workspace, 'editor.js')
        harness = os.path.join(workspace, 'harness.js')
        payload = os.path.join(workspace, 'calls.json')
        with open(source, 'w', encoding='utf-8') as handle:
            handle.write(_PURE_JS.replace('__INDENT__', INDENT)
                         .replace('__WIDTH__', str(len(INDENT))))
        with open(harness, 'w', encoding='utf-8') as handle:
            handle.write(_HARNESS)
        with open(payload, 'w', encoding='utf-8') as handle:
            json.dump(calls, handle)
        finished = subprocess.run([NODE, harness, source, payload],
                                  capture_output=True, text=True, timeout=60)
        if finished.returncode != 0:
            raise AssertionError('node could not run the editor:\n%s'
                                 % finished.stderr.strip())
        return json.loads(finished.stdout)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def _applied(text, action):
    """The text as it would be after the browser carried the action out."""
    if action is None:
        return None
    return text[:action['select'][0]] + action['insert'] + text[action['select'][1]:]


@unittest.skipUnless(NODE, 'node is not installed')
class TestEnterCarriesTheListOn(unittest.TestCase):

    def _enter(self, cases):
        answers = _run_js([{'fn': 'enter', 'args': [text, at, at]}
                           for text, at in cases])
        return [_applied(text, answer)
                for (text, _at), answer in zip(cases, answers)]

    def test_a_bullet_brings_the_next_one_with_it(self):
        cases = [('- erster', 8), ('* erster', 8), ('+ erster', 8)]
        self.assertEqual(self._enter(cases),
                         ['- erster\n- ', '* erster\n* ', '+ erster\n+ '])

    def test_a_number_counts_on(self):
        self.assertEqual(self._enter([('3. drei', 7), ('9) neun', 7)]),
                         ['3. drei\n4. ', '9) neun\n10) '])

    def test_a_quote_stays_quoted(self):
        self.assertEqual(self._enter([('> zitat', 7), ('>> tief', 7)]),
                         ['> zitat\n> ', '>> tief\n>> '])

    def test_a_ticked_box_carries_on_as_an_empty_one(self):
        """Repeating [x] would call the next thing done before it is written."""
        self.assertEqual(self._enter([('- [x] fertig', 12)]),
                         ['- [x] fertig\n- [ ] '])

    def test_the_indentation_comes_with_it(self):
        self.assertEqual(self._enter([('  - tief', 8)]), ['  - tief\n  - '])

    def test_an_empty_item_ends_the_list(self):
        text = '- erster\n- '
        self.assertEqual(self._enter([(text, len(text))]), ['- erster\n'])

    def test_an_empty_item_one_level_in_comes_out_a_level_first(self):
        text = '- erster\n  - '
        self.assertEqual(self._enter([(text, len(text))]), ['- erster\n- '])

    def test_ordinary_text_is_left_to_the_browser(self):
        """
        The important half. Enter has to go on inserting a plain newline
        everywhere else, and the way this says so is by declining to act.
        """
        answers = _run_js([{'fn': 'enter', 'args': ['nur Text', 8, 8]},
                           {'fn': 'enter', 'args': ['', 0, 0]},
                           {'fn': 'enter', 'args': ['  eingerueckt', 13, 13]}])
        self.assertEqual(answers, [None, None, None])

    def test_enter_in_front_of_the_marker_does_not_write_a_second_one(self):
        answers = _run_js([{'fn': 'enter', 'args': ['- erster', 0, 0]},
                           {'fn': 'enter', 'args': ['- erster', 1, 1]}])
        self.assertEqual(answers, [None, None])

    def test_a_selection_is_the_browser_s_own_business(self):
        answers = _run_js([{'fn': 'enter', 'args': ['- erster', 2, 5]}])
        self.assertEqual(answers, [None])


@unittest.skipUnless(NODE, 'node is not installed')
class TestTabIndents(unittest.TestCase):

    def _tab(self, text, start, end, outdent=False):
        answer = _run_js([{'fn': 'indent',
                           'args': [text, start, end, outdent]}])[0]
        return _applied(text, answer)

    def test_in_the_middle_of_a_word_it_is_just_a_wide_space(self):
        self.assertEqual(self._tab('abcdef', 3, 3), 'abc' + INDENT + 'def')

    def test_at_the_front_of_a_list_item_the_whole_item_moves(self):
        self.assertEqual(self._tab('- eins', 0, 0), INDENT + '- eins')

    def test_and_from_inside_the_marker_too(self):
        self.assertEqual(self._tab('- eins', 2, 2), INDENT + '- eins')

    def test_every_line_of_a_selection_moves(self):
        self.assertEqual(self._tab('- eins\n- zwei', 0, 13),
                         INDENT + '- eins\n' + INDENT + '- zwei')

    def test_shift_tab_brings_them_back(self):
        text = INDENT + '- eins\n' + INDENT + '- zwei'
        self.assertEqual(self._tab(text, 0, len(text), outdent=True),
                         '- eins\n- zwei')

    def test_a_blank_line_is_not_given_trailing_spaces(self):
        """Some readers turn two trailing spaces into a line break."""
        self.assertEqual(self._tab('eins\n\nzwei', 0, 10),
                         INDENT + 'eins\n\n' + INDENT + 'zwei')

    def test_there_is_nothing_to_outdent(self):
        self.assertIsNone(_run_js([{'fn': 'indent',
                                    'args': ['- eins', 0, 0, True]}])[0])


@unittest.skipUnless(NODE, 'node is not installed')
class TestTheColoursDoNotMoveTheText(unittest.TestCase):
    """
    The one thing the highlighting must never get wrong.

    The colour comes from a second element behind the textarea holding the
    same characters. If it ever holds different ones - a swallowed backslash,
    an unescaped bracket, a lost space - the two drift apart, and the caret,
    which the textarea draws from its own copy, ends up somewhere the text is
    not. So: whatever goes in must come out again, exactly.
    """

    SAMPLES = [
        '# Halle 3',
        'Statik **pruefen** und _messen_, siehe `norm.md`.',
        '- [ ] offene Aufgabe',
        '> Anmerkung vom Pruefer',
        '```\nx = 1\n```',
        '[Norm](https://example.com) und ~~verworfen~~',
        '---',
        'a < b & c > d',
        'Pruefstaende Groesse: 1 < 2',
        '  eingerueckt\tmit Tab',
        '*nicht geschlossen und `auch nicht',
        '',
        '\n\n',
        'Zeile\nmit\nvielen\nZeilen',
    ]

    @staticmethod
    def _text_of(html):
        """The characters the overlay would actually show."""
        out, inside = [], False
        for char in html:
            if char == '<':
                inside = True
            elif char == '>':
                inside = False
            elif not inside:
                out.append(char)
        return (''.join(out).replace('&lt;', '<').replace('&gt;', '>')
                .replace('&amp;', '&'))

    def test_every_character_survives_being_coloured(self):
        answers = _run_js([{'fn': 'highlight', 'args': [sample]}
                           for sample in self.SAMPLES])
        for sample, html in zip(self.SAMPLES, answers):
            with self.subTest(sample=sample):
                # The trailing newline is deliberate - see tcHighlight.
                self.assertEqual(self._text_of(html), sample + '\n')

    def test_markup_in_the_note_is_escaped_rather_than_rendered(self):
        html = _run_js([{'fn': 'highlight',
                         'args': ['<script>alert(1)</script>']}])[0]
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)

    def test_the_parts_are_told_apart(self):
        html = _run_js([{'fn': 'highlight',
                         'args': ['# Titel\n- **fett** `code`\n> zitat']}])[0]
        for cls in ('tcm', 'tch', 'tcb', 'tcc', 'tcq'):
            with self.subTest(cls=cls):
                self.assertIn('class="%s"' % cls, html)


class TestWhatIsHandedToTheBrowser(unittest.TestCase):
    """The Python half, which needs neither node nor a browser."""

    def test_the_keys_travel_as_json(self):
        script = editor_script(['edit_task_note', 'new_task_note'])
        self.assertIn('["edit_task_note", "new_task_note"]', script)

    def test_nothing_is_left_unsubstituted(self):
        script = editor_script(['a'])
        for placeholder in ('__KEYS__', '__INDENT__', '__WIDTH__'):
            with self.subTest(placeholder=placeholder):
                self.assertNotIn(placeholder, script)

    def _embedded_keys(self, script):
        """The list the script will actually see, read back out of it."""
        start = script.index('var KEYS = ') + len('var KEYS = ')
        # To the end of the line, not to the first ";" - a key may contain
        # one. A newline inside a key travels as \n, so the literal itself
        # never spans two lines.
        line = script[start:script.index('\n', start)].rstrip().rstrip(';')
        return json.loads(line.replace('<\\/', '</'))

    def test_a_key_stays_one_string_however_it_is_spelled(self):
        """
        The keys are built from task ids rather than typed, so a key that
        tries to end its own quoting is not a way in today. It is one line of
        insurance against the day one of them is built from something a
        person wrote.
        """
        awkward = ['x"]; window.stolen = 1; //', "back\\slash", 'zeile\nnochwas']
        self.assertEqual(self._embedded_keys(editor_script(awkward)), awkward)

    def test_the_script_cannot_close_its_own_tag(self):
        """
        editor_html puts this between <script> and </script>. A closing tag
        anywhere inside would end it early, and the rest of the editor would
        be printed onto the page as text.
        """
        hostile = ['a</script><img src=x onerror=alert(1)>']
        html = editor_html(hostile)
        self.assertEqual(html.lower().count('</script>'), 1)
        self.assertTrue(html.rstrip().endswith('</script>'))
        # And the key still arrives intact, rather than being mangled.
        self.assertEqual(self._embedded_keys(editor_script(hostile)), hostile)

    def test_the_html_is_a_script_and_nothing_else(self):
        html = editor_html(['a']).strip()
        self.assertTrue(html.startswith('<script>'))
        self.assertTrue(html.endswith('</script>'))

    def test_keys_that_are_not_strings_are_made_into_them(self):
        self.assertIn('["7"]', editor_script([7]))


if __name__ == '__main__':
    unittest.main()
