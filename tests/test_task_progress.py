import unittest
import os
import sys

# Add parent directory to path to import modules from root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from tt.task_progress import completion_counts, completion_ratio


class TestCompletionRatio(unittest.TestCase):
    """What the progress bar over today's tasks is drawn from."""

    @staticmethod
    def _tasks(*statuses):
        return [{"task_name": "t%d" % i, "status": s}
                for i, s in enumerate(statuses)]

    def test_a_day_not_started_is_empty(self):
        self.assertEqual(completion_ratio(self._tasks("open", "open")), 0.0)

    def test_a_day_finished_is_full(self):
        self.assertEqual(completion_ratio(self._tasks("done", "done")), 1.0)

    def test_it_counts_what_is_done_not_what_is_left(self):
        """
        The direction is the whole point: three of four finished has to read
        as nearly full, not nearly empty.
        """
        self.assertEqual(
            completion_ratio(self._tasks("done", "done", "done", "open")), 0.75)

    def test_nothing_to_do_is_not_the_same_as_nothing_done(self):
        """
        An empty day has no ratio. Answering 0.0 would draw an empty bar,
        which says the day is entirely unstarted.
        """
        self.assertIsNone(completion_ratio([]))

    def test_a_task_without_a_status_counts_as_unfinished(self):
        self.assertEqual(completion_ratio([{"task_name": "t"}]), 0.0)

    def test_only_done_counts_as_finished(self):
        """
        'closed' is a different thing from 'done' and never reaches this view
        anyway; if one ever did it must not be mistaken for work completed.
        """
        self.assertEqual(completion_ratio(self._tasks("closed", "done")), 0.5)

    def test_the_caller_s_list_is_left_alone(self):
        tasks = self._tasks("done", "open")
        completion_ratio(tasks)
        self.assertEqual(len(tasks), 2)

    def test_it_takes_anything_it_can_iterate(self):
        """The view hands it a list; a generator must not come out as zero."""
        self.assertEqual(
            completion_ratio(t for t in self._tasks("done", "open")), 0.5)


class TestCompletionCounts(unittest.TestCase):
    """
    The figures the progress bar's tooltip names.

    They come from here rather than being counted again at the point of use,
    so the bar and the sentence beside it cannot end up telling the reader
    two different things.
    """

    @staticmethod
    def _tasks(*statuses):
        return [{"task_name": "t%d" % i, "status": s}
                for i, s in enumerate(statuses)]

    def test_it_reports_how_many_are_done_and_how_many_there_are(self):
        self.assertEqual(completion_counts(self._tasks("done", "open", "open")),
                         (1, 3))

    def test_a_day_not_started(self):
        self.assertEqual(completion_counts(self._tasks("open", "open")), (0, 2))

    def test_a_day_finished(self):
        self.assertEqual(completion_counts(self._tasks("done", "done")), (2, 2))

    def test_an_empty_day_counts_nothing_rather_than_refusing(self):
        """Unlike the ratio, which has no answer, both figures are simply 0."""
        self.assertEqual(completion_counts([]), (0, 0))

    def test_a_task_without_a_status_is_not_finished(self):
        self.assertEqual(completion_counts([{"task_name": "t"}]), (0, 1))

    def test_closed_is_not_done(self):
        self.assertEqual(completion_counts(self._tasks("closed", "done")), (1, 2))

    def test_it_takes_anything_it_can_iterate(self):
        """The view hands it a list; a generator must not come out as (0, 0)."""
        self.assertEqual(
            completion_counts(t for t in self._tasks("done", "open")), (1, 2))

    def test_the_caller_s_list_is_left_alone(self):
        tasks = self._tasks("done", "open")
        completion_counts(tasks)
        self.assertEqual(len(tasks), 2)

    def test_the_bar_and_the_figures_always_agree(self):
        """
        The one property that matters: whatever the tooltip says, the bar has
        to be drawn at that fraction. Checked over every mix of four tasks.
        """
        import itertools
        for mix in itertools.product(("done", "open", "closed", None), repeat=4):
            tasks = [{"task_name": "t", "status": s} if s else {"task_name": "t"}
                     for s in mix]
            done, total = completion_counts(tasks)
            with self.subTest(mix=mix):
                self.assertEqual(completion_ratio(tasks), done / total)


# Run the tests if the file is called directly
if __name__ == '__main__':
    unittest.main()
