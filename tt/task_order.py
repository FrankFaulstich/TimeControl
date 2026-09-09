"""
The orders a list of tasks can be shown in, and how to apply them.

Plain functions rather than a class: none of them carries state between
calls, and in Python a module is already the namespace a class of static
methods would only imitate. They used to live in TimeTracker.py, which is
about the document on disk and never called any of them.
"""

import unicodedata


# The orders a list of tasks can be presented in. 'none' is what the data
# already is - the order tasks were created in - and is the default, so a
# view that offers no choice behaves as it always did.
TASK_ORDER_NONE = "none"
TASK_ORDER_PRIORITY = "priority"
TASK_ORDER_ALPHABETICAL = "alphabetical"
TASK_ORDERS = (TASK_ORDER_NONE, TASK_ORDER_PRIORITY, TASK_ORDER_ALPHABETICAL)


def alphabetical_key(name):
    """
    Sort key that puts 'Ärger' under A rather than behind Z.

    Comparing strings by code point is only alphabetical for unaccented
    ASCII: 'Ä' is U+00C4, so it lands after 'Z' and the German, Czech and
    Spanish translations all get a list that reads as unsorted. Decomposing
    to NFKD and dropping the combining marks folds each accented letter onto
    its base one, which is where a reader looks for it.

    This is a folding, not full collation: Czech traditionally files 'ch'
    after 'h', and that ordering needs a locale-aware collator this does not
    pretend to be. Folding still puts 'č' next to 'c' instead of past 'z',
    which is the error that actually looks broken.

    The original string is the tie-break so that names differing only in
    case or accent keep a stable, predictable order.
    """
    name = name or ""
    folded = unicodedata.normalize("NFKD", name.casefold())
    return ("".join(c for c in folded if not unicodedata.combining(c)), name)


def sort_tasks(tasks, order):
    """
    Returns `tasks` in the requested order, without touching the original.

    An unknown order leaves the list alone rather than raising: this drives a
    display, and a stale value in a saved UI state should not take out the
    whole view.
    """
    tasks = list(tasks)
    if order == TASK_ORDER_PRIORITY:
        # Descending, and stable, so tasks sharing a priority stay in the
        # order they were created in.
        tasks.sort(key=lambda t: t.get('priority') or 0, reverse=True)
    elif order == TASK_ORDER_ALPHABETICAL:
        tasks.sort(key=lambda t: alphabetical_key(t.get('task_name')))
    return tasks
