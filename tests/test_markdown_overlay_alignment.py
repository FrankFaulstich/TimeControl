"""
Whether the coloured overlay of the notes field stays on top of the text the
caret is moving through.

The overlay draws the visible text; the textarea above it, its glyphs made
transparent, draws the caret. The two must set every character in the same
place, and three times they did not:

- WebKit, the engine of the Mac app, kerns and joins ligatures within a run of
  text but never across the edge of an element. The textarea is one run, the
  overlay one element per Markdown marker, so after every marker the visible
  text moved and the caret was left beside it. Measured in WKWebView: red
  textarea glyphs and blue overlay glyphs drew apart by 1.5 to 2 points after
  the first marker of a line, and lay exactly on top of each other on a line
  without one.
- A scrollbar that takes room - Windows, or a Mac with a mouse - narrows the
  textarea's text but not the overlay's, so a long note wrapped at different
  places in the two and every line after that was off. Under zoom or display
  scaling the scrollbar is a fraction of a pixel wide, so whole pixels are not
  enough to put that right.
- The keycap asterisk emoji was cut in two by the italic marker, and the rest
  of its line moved by four pixels.

The wiring that fixes the first two needs a browser. Rather than read it as
text, this runs the real script under node against a few stand-in elements -
just enough page for it to attach to one field, with a ResizeObserver that
can be made to report a new size - and looks at what it did: the stylesheet it
wrote and the padding it gave the overlay at each step.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from tt.markdown_editor import editor_script

NODE = shutil.which('node')

# A page with one notes field on it, and nothing else the script does not ask
# for. The textarea's computed style is what the scenario says; each step then
# changes its size and reports that to every ResizeObserver, the way a browser
# does when a scrollbar comes or goes.
_PAGE = r"""
const fs = require('fs');
const script = fs.readFileSync(process.argv[2], 'utf8');
const scenario = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const written = [];
const observers = [];
function element(tag) {
  return {
    tagName: tag, className: '', dataset: {}, children: [],
    style: {setProperty: function (name, value) { this[name] = value; }},
    classList: {add: function () {}},
    addEventListener: function () {},
    insertBefore: function (node) { this.children.push(node); }
  };
}
const area = element('textarea');
area.value = '**Anna** am _Montag_';
area.offsetWidth = scenario.offsetWidth;
area.clientWidth = scenario.clientWidth;
area.rectWidth = scenario.offsetWidth;
area.getBoundingClientRect = function () { return {width: this.rectWidth}; };
area.scrollTop = 0;
area.scrollLeft = 0;
const host = element('div');
area.parentElement = host;
const areaStyle = {
  color: 'rgb(49, 51, 63)', fontFamily: 'Source Sans', fontSize: '14px',
  paddingLeft: '12px', paddingRight: scenario.paddingRight,
  borderLeftWidth: scenario.border, borderRightWidth: scenario.border,
  wordBreak: 'normal'
};
const doc = {
  head: {appendChild: function (node) { written.push(node.textContent); }},
  body: {},
  getElementById: function () { return null; },
  createElement: element,
  querySelectorAll: function (selector) { return /textarea$/.test(selector) ? [area] : []; },
  execCommand: function () {}
};
const win = {
  document: doc,
  getComputedStyle: function (node) {
    if (node === area) return areaStyle;
    if (node === doc.body) return {backgroundColor: 'rgb(255, 255, 255)'};
    return {position: 'relative'};
  },
  CSS: {escape: function (s) { return s; }},
  MutationObserver: function () { this.observe = function () {}; },
  ResizeObserver: function (callback) {
    observers.push(callback);
    this.observe = function () {};
  },
  setTimeout: setTimeout, clearTimeout: clearTimeout
};
global.window = {parent: win};
eval(script);
const overlay = host.children[0];
const paddings = [overlay ? overlay.style.paddingRight : null];
(scenario.steps || []).forEach(function (step) {
  ['offsetWidth', 'clientWidth', 'rectWidth'].forEach(function (name) {
    if (name in step) area[name] = step[name];
  });
  const entries = 'textWidth' in step ? [{contentRect: {width: step.textWidth}}] : [];
  observers.forEach(function (callback) { callback(entries); });
  paddings.push(overlay.style.paddingRight);
});
process.stdout.write(JSON.stringify({
  css: written.join('\n'),
  attached: area.dataset.tcMdEditor === '1',
  paddings: paddings,
  highlighted: scenario.highlight ? tcHighlight(scenario.highlight) : null
}));
"""


def _attach(offset_width=702, client_width=700, border='1px', padding_right='12px',
            steps=(), highlight=None):
    """Runs the editor against one field; returns what it wrote and set."""
    workspace = tempfile.mkdtemp(prefix='tc-mdpage-')
    try:
        paths = {name: os.path.join(workspace, name)
                 for name in ('editor.js', 'page.js', 'scenario.json')}
        with open(paths['editor.js'], 'w', encoding='utf-8') as handle:
            handle.write(editor_script(['new_task_note']))
        with open(paths['page.js'], 'w', encoding='utf-8') as handle:
            handle.write(_PAGE)
        with open(paths['scenario.json'], 'w', encoding='utf-8') as handle:
            json.dump({'offsetWidth': offset_width, 'clientWidth': client_width,
                       'border': border, 'paddingRight': padding_right,
                       'steps': list(steps), 'highlight': highlight}, handle)
        finished = subprocess.run([NODE, paths['page.js'], paths['editor.js'],
                                   paths['scenario.json']],
                                  capture_output=True, text=True, timeout=60)
        if finished.returncode != 0:
            raise AssertionError('node could not run the editor:\n%s' % finished.stderr.strip())
        result = json.loads(finished.stdout)
        if not result['attached']:
            raise AssertionError('the editor did not attach to the stand-in field')
        return result
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def _declarations(css, selector):
    """
    The declarations of the rules whose selector list names exactly this
    selector - not a descendant of it, not a pseudo-element of it - merged.
    """
    merged = {}
    for selectors, body in re.findall(r'([^{}]+)\{([^{}]*)\}', css):
        if selector in [s.strip() for s in selectors.split(',')]:
            for part in body.split(';'):
                if ':' in part:
                    name, value = part.split(':', 1)
                    merged[name.strip()] = value.strip()
    return merged


@unittest.skipUnless(NODE, "node is not installed")
class TestEveryCharacterTakesTheSameRoomInBoth(unittest.TestCase):

    def test_neither_kerns_nor_joins_letters(self):
        for selector in ('.tc-md-overlay', '.tc-md-live'):
            with self.subTest(selector=selector):
                rules = _declarations(_attach()['css'], selector)
                self.assertEqual(rules.get('font-kerning'), 'none !important')
                self.assertEqual(rules.get('font-variant-ligatures'), 'none !important')

    def test_nor_by_the_font_s_own_features(self):
        """
        font-kerning and font-variant-ligatures are the ordinary switches;
        the feature settings are what a font or a stylesheet could turn back
        on behind them. Read as the browser would: one malformed entry makes
        the whole declaration invalid, and then all of them are back on.
        """
        for selector in ('.tc-md-overlay', '.tc-md-live'):
            with self.subTest(selector=selector):
                value = _declarations(_attach()['css'], selector).get('font-feature-settings', '')
                self.assertTrue(value.endswith('!important'), value)
                entries = [e.strip() for e in value[:-len('!important')].split(',')]
                tags = []
                for entry in entries:
                    match = re.fullmatch(r"""(['"])(\w{4})\1\s+0""", entry)
                    self.assertTrue(match, 'not a valid feature setting: %r' % entry)
                    tags.append(match.group(2))
                self.assertEqual(sorted(tags), ['calt', 'clig', 'kern', 'liga'])

    def test_on_the_textarea_too_and_not_to_be_overridden(self):
        """
        Turned off on one of the two only, the other would still kern and
        they would disagree the other way round. !important, because the
        textarea's font comes from Streamlit's own classes.
        """
        rules = _declarations(_attach()['css'], '.tc-md-live')
        for name in ('font-kerning', 'font-variant-ligatures', 'font-feature-settings'):
            self.assertIn('!important', rules.get(name, ''), name)


@unittest.skipUnless(NODE, "node is not installed")
class TestTheOverlayWrapsWhereTheTextareaDoes(unittest.TestCase):

    def test_a_scrollbar_s_width_is_given_back_to_the_overlay(self):
        # 702 wide, 1px borders, a 14px scrollbar: 686 left inside.
        self.assertEqual(_attach(offset_width=702, client_width=686)['paddings'][0], '26px')

    def test_floating_scrollbars_change_nothing(self):
        """The usual Mac case: the scrollbar draws over the text, taking no room."""
        self.assertEqual(_attach(offset_width=702, client_width=700)['paddings'][0], '12px')

    def test_a_scrollbar_that_appears_as_the_note_grows_and_goes_again(self):
        """
        The new-task note starts empty, without a scrollbar, so what it gets
        at attach is the wrong answer for any note longer than the field. The
        size reported afterwards is what has to count, both ways.
        """
        paddings = _attach(offset_width=702, client_width=700, steps=[
            {'clientWidth': 686, 'textWidth': 662},     # 702 - 2 borders - 24 padding - 14
            {'clientWidth': 700, 'textWidth': 676},     # and without the scrollbar
        ])['paddings']
        self.assertEqual(paddings, ['12px', '26px', '12px'])

    def test_under_zoom_the_exact_width_counts_not_the_whole_pixels(self):
        """
        At 110 % a 14px scrollbar is 12.7 CSS pixels. Rounded, the overlay's
        text would be a fraction wider or narrower than the textarea's, and a
        line ending in that sliver wraps in one and not in the other.
        """
        paddings = _attach(offset_width=638, client_width=625, steps=[
            {'rectWidth': 638.1875, 'textWidth': 599.4602},
        ])['paddings']
        self.assertAlmostEqual(float(paddings[1][:-2]), 12 + (638.1875 - 2 - 24 - 599.4602), places=3)

    def test_never_less_than_the_textarea_s_own_padding(self):
        # Fractional borders can make the arithmetic come out below zero.
        self.assertEqual(_attach(offset_width=700, client_width=700, border='0.5px')['paddings'][0], '12px')


@unittest.skipUnless(NODE, "node is not installed")
class TestAnEmojiIsNotTakenForAMarker(unittest.TestCase):

    KEYCAP = '*\ufe0f\u20e3'

    def test_the_keycap_asterisk_stays_whole(self):
        """
        Cut at the asterisk, the overlay drew a bare asterisk and a box where
        the textarea draws one keycap, four pixels apart.
        """
        line = self.KEYCAP + ' Stern *kursiv* Ende'
        highlighted = _attach(highlight=line)['highlighted']
        self.assertIn(self.KEYCAP + ' Stern ', highlighted)
        self.assertIn('<span class="tci">kursiv</span>', highlighted)

    def test_bold_does_not_start_inside_one_either(self):
        # Whatever pairs up instead, no bold run may begin with the
        # selector that belongs to the character before it.
        highlighted = _attach(highlight='**\ufe0fx** und so')['highlighted']
        self.assertNotIn('<span class="tcb">\ufe0f', highlighted)
        self.assertIn('**\ufe0fx', highlighted)

    def test_ordinary_emphasis_is_still_found(self):
        highlighted = _attach(highlight='**fett** und *kursiv* und __auch__')['highlighted']
        self.assertIn('<span class="tcb">fett</span>', highlighted)
        self.assertIn('<span class="tci">kursiv</span>', highlighted)
        self.assertIn('<span class="tcb">auch</span>', highlighted)
