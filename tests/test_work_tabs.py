import ast
import os
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
WORK_VIEW = os.path.join(REPO_ROOT, 'sl', 'SL_Menu.py')


def _tree():
    """
    SL_Menu.py parsed.

    Read by source rather than by running it: a Streamlit script cannot be
    imported without starting a Streamlit session, so nothing in it can be
    reached from a test any other way.
    """
    with open(WORK_VIEW, encoding='utf-8') as handle:
        return ast.parse(handle.read(), WORK_VIEW)


def _function(name, tree=None):
    for node in ast.walk(tree or _tree()):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError('%s() is not in %s any more' % (name, WORK_VIEW))


def _assigned(name, tree):
    for node in tree.body:
        if (isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == name
                        for t in node.targets)):
            return ast.literal_eval(node.value)
    raise AssertionError('%s is not defined at module level any more' % name)


class TestEveryWorkTabIsWiredAllTheWayThrough(unittest.TestCase):
    """
    The home screen's tabs are held together by four separate places agreeing
    with each other: the list of routes, the label for each, the dispatch in
    view_work(), and menu_map, which is what lets a form return to the tab it
    was opened from. Three of the four fail loudly but late - a missing label
    is a KeyError the moment the screen is drawn, and a missing menu_map entry
    silently drops the user somewhere else on the way back from a form.

    None of it is checkable at import time, because the module cannot be
    imported. So it is checked here, against the source.
    """

    def setUp(self):
        self.tree = _tree()
        self.routes = _assigned('_WORK_ROUTES', self.tree)

    def test_there_are_three_tabs(self):
        self.assertEqual(list(self.routes),
                         ['today_view', 'task_planning', 'calendar'])

    def test_the_home_tab_is_first(self):
        """
        view_work() falls back to _WORK_ROUTES[0] whenever the menu names
        something that is not a tab, and the app's home is the today view.
        """
        self.assertEqual(self.routes[0], 'today_view')

    def test_every_route_has_a_label(self):
        labels = _function('_work_tab_labels', self.tree)
        named = set()
        for node in ast.walk(labels):
            if isinstance(node, ast.Dict):
                named.update(k.value for k in node.keys
                             if isinstance(k, ast.Constant))
        self.assertEqual(set(self.routes) - named, set(),
                         'a route with no label is a KeyError as the screen '
                         'is drawn')

    def test_every_route_reaches_the_work_screen_from_menu_map(self):
        """
        Without an entry here a form's "return to where you came from" drops
        the user on the default screen instead of the tab they left.
        """
        for node in ast.walk(self.tree):
            if (isinstance(node, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == 'menu_map'
                            for t in node.targets)):
                mapped = {k.value: v.id for k, v in zip(node.value.keys,
                                                        node.value.values)
                          if isinstance(k, ast.Constant)
                          and isinstance(v, ast.Name)}
                break
        else:
            raise AssertionError('menu_map is not a plain dict any more')
        for route in self.routes:
            self.assertEqual(mapped.get(route), 'view_work',
                             '%s does not reach the work screen' % route)

    def test_every_route_has_a_body_that_gets_called(self):
        called = set()
        for node in ast.walk(_function('view_work', self.tree)):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                called.add(node.func.id)
        bodies = {'today_view': '_today_tasks_body',
                  'task_planning': '_task_planning_body',
                  'calendar': '_calendar_body'}
        for route in self.routes:
            self.assertIn(bodies[route], called,
                          '%s is never drawn' % route)


class TestTheCalendarSendsYouBackToTheCalendar(unittest.TestCase):
    """
    Clicking a task in the calendar opens the edit form, and the form returns
    to whatever 'return_to' says. Naming another tab there is not an error
    anything would catch: the edit works, and the user simply finds
    themselves somewhere they did not come from.
    """

    def test_the_return_route_is_the_calendar(self):
        targets = []
        for node in ast.walk(_function('_calendar_body')):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if (isinstance(target, ast.Subscript)
                        and isinstance(target.slice, ast.Constant)
                        and target.slice.value == 'return_to'):
                    targets.append(node.value)
        self.assertEqual(len(targets), 1,
                         'the calendar sets return_to exactly once')
        self.assertEqual(ast.literal_eval(targets[0]), 'calendar')


class TestTheCalendarShowsOnlyWhatIsStillOutstanding(unittest.TestCase):
    """
    Issue #610: the calendar is read to see what is still coming, so a
    finished task no longer takes up a square.

    It used to show them with a tick. Two things had to happen for that: the
    finished ones are filtered out of the list the grid is built from, and
    the tick itself is gone - with nothing finished left to draw, a branch
    for it could never run again.

    month_grid() itself still keeps whatever it is handed, tick or no tick;
    which tasks belong on the page is the caller's decision, not the grid's.
    """

    def _done_comparisons(self):
        for node in ast.walk(_function('_calendar_body')):
            if (isinstance(node, ast.Compare)
                    and len(node.ops) == 1
                    and any(isinstance(c, ast.Constant) and c.value == 'done'
                            for c in node.comparators)):
                yield type(node.ops[0]).__name__

    def test_finished_tasks_are_kept_off_the_page(self):
        self.assertIn('NotEq', list(self._done_comparisons()),
                      'nothing in the calendar filters finished tasks out')

    def test_nothing_is_left_that_only_a_finished_task_could_reach(self):
        """
        A `== 'done'` here would be dead: the filter above means none get
        this far. Dead branches read as coverage of a case that is in fact
        handled elsewhere, which is how the settings screen grew one.
        """
        self.assertNotIn('Eq', list(self._done_comparisons()),
                         'a branch for finished tasks can no longer run')

    def test_it_is_the_filtered_list_that_reaches_the_grid(self):
        """
        Filtering into a variable and then handing the grid the unfiltered
        one would put every finished task straight back on the page, and
        nothing else would notice.
        """
        function = _function('_calendar_body')
        filtered = [node.targets[0].id for node in ast.walk(function)
                    if isinstance(node, ast.Assign)
                    and isinstance(node.value, ast.ListComp)
                    and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)]
        self.assertEqual(len(filtered), 1,
                         'expected exactly one filtered task list here')
        passed = []
        for node in ast.walk(function):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == 'month_grid'):
                passed += [a.id for a in node.args if isinstance(a, ast.Name)]
        self.assertIn(filtered[0], passed,
                      'month_grid() is not being given the filtered list')


if __name__ == '__main__':
    unittest.main()
