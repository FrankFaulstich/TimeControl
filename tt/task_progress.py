"""
How much of a list of tasks is behind you.

Plain functions rather than a class: none of them carries state between
calls, and in Python a module is already the namespace a class of static
methods would only imitate. They used to live in TimeTracker.py, which is
about the document on disk and never called any of them.
"""

def completion_counts(tasks):
    """
    How many of `tasks` are finished, and how many there are.

    The one place either number is worked out, so the bar drawn from the
    ratio below and the figures named beside it cannot disagree.

    :return: (done, total).
    """
    tasks = list(tasks)
    return sum(1 for task in tasks if task.get('status') == 'done'), len(tasks)


def completion_ratio(tasks):
    """
    How much of `tasks` is done, as a fraction between 0 and 1.

    Counts what is finished, not what is left: a bar drawn from this fills up
    as the day is worked through, which is what a reader expects of one.

    :return: None when there is nothing to measure. An empty list has no
        ratio - 0.0 would be a bar reading "none of it done", which is not
        the same thing as having nothing to do, and is the more discouraging
        of the two to be told wrongly.
    """
    done, total = completion_counts(tasks)
    if not total:
        return None
    return done / total
