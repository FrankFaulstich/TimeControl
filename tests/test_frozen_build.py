"""
What a PyInstaller build would and would not contain.

Issue #556: TimeControl.spec lists the sync modules under hiddenimports
because sl/SL_Menu.py is shipped as data and never scanned for imports - and
that fix had never been checked. Checking it against a real build means
Windows, a build machine and several minutes; this reads the spec and the
sources instead and answers the same question in milliseconds, on every
platform, on every run.

It is worth having as a test rather than only as a build step because of how
the failure looks. A module that SL_Menu imports inside a try/except goes
missing quietly - SYNC_AVAILABLE turns False and the feature is simply gone.
One imported at the top does not: the script dies on import, and Streamlit
still answers on its port with the page shell, so even the smoke test that
fetches http://localhost:8501 sees a healthy 200.
"""

import ast
import os
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
SPEC = os.path.join(REPO_ROOT, 'TimeControl.spec')

# The top-level names that are this project's own, as opposed to installed
# packages, which PyInstaller finds by itself.
FIRST_PARTY = ('tt', 'sl', 'i18n', 'update')


def _spec_tree():
    with open(SPEC, encoding='utf-8') as handle:
        return ast.parse(handle.read(), SPEC)


def _assigned_list(name, tree):
    """The first `name = [...]` at module level, as a Python list."""
    for node in tree.body:
        if (isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == name
                        for t in node.targets)):
            return ast.literal_eval(node.value)
    raise AssertionError('%s is not a plain list in %s any more' % (name, SPEC))


def _entry_scripts(tree):
    """The scripts Analysis() is pointed at - the only ones it scans."""
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == 'Analysis' and node.args):
            return [os.path.splitext(s)[0] for s in ast.literal_eval(node.args[0])]
    raise AssertionError('Analysis() is not called the expected way in %s' % SPEC)


def _shipped_as_data(tree):
    """The .py files copied in verbatim, which nothing scans for imports."""
    return [source for source, _target in _assigned_list('datas', tree)
            if source.endswith('.py')]


def _module_file(module):
    path = os.path.join(REPO_ROOT, module.replace('.', os.sep) + '.py')
    return path if os.path.exists(path) else None


def _first_party_imports(path):
    """Every module of this project that `path` imports, by dotted name."""
    with open(path, encoding='utf-8') as handle:
        tree = ast.parse(handle.read(), path)
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split('.')[0] in FIRST_PARTY:
                found.add(node.module)
                if node.module == 'tt':
                    # `from tt import sync_client` names a submodule, not an
                    # attribute of the package.
                    found.update('tt.' + a.name for a in node.names)
        elif isinstance(node, ast.Import):
            found.update(a.name for a in node.names
                         if a.name.split('.')[0] in FIRST_PARTY)
    return found


def _reachable(tree):
    """
    Everything of ours a build would end up containing.

    PyInstaller follows imports from the entry scripts, and takes the
    hiddenimports on trust. Anything not reachable that way is not in the
    build, however plainly the source asks for it.
    """
    tree = tree or _spec_tree()
    seen, pending = set(), list(_entry_scripts(tree)) + list(
        _assigned_list('hiddenimports', tree))
    while pending:
        module = pending.pop()
        if module in seen:
            continue
        seen.add(module)
        path = _module_file(module)
        if path:
            pending.extend(_first_party_imports(path) - seen)
    return seen


class TestNothingTheAppNeedsIsLeftOutOfTheBuild(unittest.TestCase):

    def setUp(self):
        self.tree = _spec_tree()

    def test_the_spec_still_looks_the_way_this_reads_it(self):
        """
        Guards everything below. If the spec is restructured so these cannot
        be read, the checks would quietly find nothing to complain about.
        """
        self.assertTrue(_entry_scripts(self.tree))
        self.assertTrue(_shipped_as_data(self.tree),
                        'no .py is shipped as data any more - if that is '
                        'deliberate, this whole file can go')

    def test_every_module_a_data_shipped_script_imports_is_in_the_build(self):
        reachable = _reachable(self.tree)
        for script in _shipped_as_data(self.tree):
            needed = _first_party_imports(os.path.join(REPO_ROOT, script))
            missing = sorted(m for m in needed
                             if _module_file(m) and m not in reachable)
            with self.subTest(script=script):
                self.assertEqual(
                    missing, [],
                    '%s imports these, and a build would not contain them. '
                    'Add them to hiddenimports in TimeControl.spec, or let '
                    'the spec derive them.' % script)

    def test_a_module_only_the_data_script_wants_is_noticed(self):
        """
        The test above is only worth its place if it would actually fail.
        Nothing in the entry graph imports this name, so a build could not
        contain it, and the check has to say so.
        """
        reachable = _reachable(self.tree)
        self.assertNotIn('tt.nicht_vorhanden', reachable)

    def test_hidden_imports_name_modules_that_exist(self):
        """
        A misspelling here is not an error at build time; PyInstaller warns
        and carries on, and the module is simply absent.
        """
        for module in _assigned_list('hiddenimports', self.tree):
            if module.split('.')[0] not in FIRST_PARTY:
                continue          # installed packages are not ours to find
            with self.subTest(module=module):
                self.assertIsNotNone(
                    _module_file(module),
                    'hiddenimports names %s, which is not a file here' % module)


if __name__ == '__main__':
    unittest.main()
