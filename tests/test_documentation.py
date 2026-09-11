"""
That docs/modules.rst still names the modules this project actually has.

Issue #560: the page had `automodule:: TimeTracker` long after TimeTracker
moved into the tt package, and `automodule:: TimeTrackerMCP` for a file
called TimeTrackerMCP_Server.py. Sphinx does not treat either as an error -
it writes "failed to import", carries on, and produces a page with the
section heading still there and nothing underneath it. Meanwhile eleven
modules written since had never been added at all. The built documentation
covered one module out of seventeen, and looked complete.

Neither half of that shows up in a build that nobody reads the log of, so it
is checked here instead: on every run, on every platform, without Sphinx
installed. The docs job in CI builds the page for real with -W, which catches
what this cannot - reStructuredText that autodoc chokes on inside a docstring.
Together they cover the two ways this page goes wrong.
"""

import os
import re
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
MODULES_RST = os.path.join(REPO_ROOT, 'docs', 'modules.rst')

# Where this project's own code lives. '' is the repository root, and it is
# read without recursing - the directories not named here (tests, examples,
# php-server, docs) are not part of the application.
CODE_DIRS = ('', 'tt', 'sl')

# The ones that are deliberately absent from the page, and why. Kept as a
# mapping rather than a list so the reason travels with the name; a later
# reader deciding whether an entry is still right needs it.
NOT_DOCUMENTED = {
    'tt.__init__':
        'the package marker, and empty',
    'i18n':
        'no public surface to document: one private initialiser and the '
        'translation function it returns. Every module that uses it is '
        'documented, and importing it is what those pages describe.',
    'sl.SL_Menu':
        'a Streamlit page script rather than a module. autodoc documents '
        'what it can import, and importing this one draws the interface.',
}

DIRECTIVE = re.compile(r'^\.\.\s+automodule::\s+(\S+)\s*$', re.MULTILINE)


def _rst():
    with open(MODULES_RST, encoding='utf-8') as handle:
        return handle.read()


def _documented(text=None):
    """The dotted module names the page points autodoc at, in order."""
    return DIRECTIVE.findall(text if text is not None else _rst())


def _module_file(dotted):
    """The file a dotted name resolves to, or None if there is none."""
    path = os.path.join(REPO_ROOT, dotted.replace('.', os.sep) + '.py')
    return path if os.path.isfile(path) else None


def _modules_here():
    """Every module of this project, by the dotted name autodoc would use."""
    found = set()
    for directory in CODE_DIRS:
        full = os.path.join(REPO_ROOT, directory) if directory else REPO_ROOT
        for name in sorted(os.listdir(full)):
            if not name.endswith('.py'):
                continue
            stem = name[:-3]
            found.add('%s.%s' % (directory, stem) if directory else stem)
    return found


class TestThePageStillDescribesThisProject(unittest.TestCase):

    def test_the_page_can_still_be_read_the_way_this_reads_it(self):
        """
        Guards everything below. Were the directives written some other way -
        an autosummary table, say, or a generated stub tree - the checks
        would find nothing to document and nothing to complain about.
        """
        self.assertTrue(
            _documented(),
            '%s has no automodule directives any more. If the documentation '
            'is generated differently now, this file needs rewriting rather '
            'than deleting.' % MODULES_RST)

    def test_every_module_of_this_project_is_documented(self):
        missing = sorted(_modules_here()
                         - set(_documented())
                         - set(NOT_DOCUMENTED))
        self.assertEqual(
            missing, [],
            'these modules exist and appear nowhere in the documentation: '
            '%s. Add an automodule directive for each in docs/modules.rst, '
            'or an entry in NOT_DOCUMENTED here saying why not.'
            % ', '.join(missing))

    def test_every_documented_name_resolves_to_a_module(self):
        for dotted in _documented():
            with self.subTest(module=dotted):
                self.assertIsNotNone(
                    _module_file(dotted),
                    'docs/modules.rst points autodoc at %s, which is not a '
                    'file here. Sphinx warns about this and builds the page '
                    'without it.' % dotted)

    def test_nothing_is_named_twice(self):
        """
        Two directives for one module make Sphinx describe every function in
        it twice, and warn about the duplicate object descriptions.
        """
        seen = _documented()
        doubled = sorted({m for m in seen if seen.count(m) > 1})
        self.assertEqual(doubled, [], 'documented more than once: %s'
                         % ', '.join(doubled))

    def test_each_directive_asks_for_the_members(self):
        """
        Without :members: a module contributes its own docstring and not one
        word about anything in it - which looks like documentation and is
        the failure this whole file is about, one module at a time.
        """
        text = _rst()
        for block in text.split('.. automodule:: ')[1:]:
            dotted = block.split('\n', 1)[0].strip()
            head = block.split('\n\n', 1)[0]
            with self.subTest(module=dotted):
                self.assertIn(':members:', head,
                              '%s is documented without :members:' % dotted)

    def test_the_exemptions_are_still_about_real_files(self):
        """
        An exemption for a module that has since been deleted or renamed is
        a hole: the name it excuses is gone, and whatever replaced it is
        excused by nothing and would have been caught.
        """
        for dotted, reason in NOT_DOCUMENTED.items():
            with self.subTest(module=dotted):
                self.assertIsNotNone(
                    _module_file(dotted),
                    'NOT_DOCUMENTED excuses %s (%s), which no longer exists'
                    % (dotted, reason))

    def test_an_exemption_is_not_also_documented(self):
        """The two lists disagreeing means one of them is out of date."""
        both = sorted(set(_documented()) & set(NOT_DOCUMENTED))
        self.assertEqual(
            both, [],
            '%s is documented and listed as not documented at the same time'
            % ', '.join(both))


class TestTheCheckWouldActuallyFail(unittest.TestCase):
    """
    The tests above are worth their place only if they can go red. Both of
    the mistakes issue #560 was about are staged here against the same
    readers the real checks use.
    """

    def test_a_stale_name_is_noticed(self):
        self.assertIsNone(_module_file('TimeTracker'),
                          'TimeTracker.py is back at the repository root; '
                          'the rest of this file assumes it moved into tt/')
        self.assertIsNone(_module_file('TimeTrackerMCP'))

    def test_a_module_left_out_is_noticed(self):
        """
        The comparison the check above makes, run against a page that has
        lost one of its directives. As a difference, so that this says
        nothing about the page as it stands and only about the reader.
        """
        documented = set(_documented())
        before = _modules_here() - documented - set(NOT_DOCUMENTED)
        after = _modules_here() - (documented - {'update'}) - set(NOT_DOCUMENTED)
        self.assertEqual(sorted(after - before), ['update'])

    def test_the_reader_finds_what_is_there(self):
        """
        A directive regex that matched nothing would make every check above
        pass on an empty page.
        """
        self.assertIn('tt.TimeTracker', _documented())
        self.assertIn('update', _documented())
        self.assertIn('tt.TimeTracker', _modules_here())


if __name__ == '__main__':
    unittest.main()
