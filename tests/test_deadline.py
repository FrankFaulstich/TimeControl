"""
The deadline every network call runs under (tt/deadline.py, issue #667).

It used to live in update.py; the e-mail import needs it too, and importing
the update module from the core of the application for it would have tied
the two together. What it promises is small and has to hold exactly: the
answer when there is one, the error when there is one, and nothing at all
past the deadline - not even at shutdown.
"""

import os
import sys
import threading
import time
import unittest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import update
from tt.deadline import call_with_deadline


class TestTheDeadline(unittest.TestCase):

    def test_the_answer_comes_back(self):
        self.assertEqual(call_with_deadline(lambda a, b=0: a + b, 1, 2, b=3), 5)

    def test_so_does_the_error(self):
        def refuse():
            raise ConnectionRefusedError('no')
        with self.assertRaises(ConnectionRefusedError):
            call_with_deadline(refuse, 1)

    def test_a_call_that_never_returns_is_given_up_on_in_time(self):
        released = threading.Event()
        self.addCleanup(released.set)
        started = time.monotonic()
        with self.assertRaises(TimeoutError):
            # Bounded, so that a broken deadline fails this rather than hangs it.
            call_with_deadline(released.wait, 0.2, 10)
        self.assertLess(time.monotonic() - started, 2)

    def test_what_is_left_running_cannot_keep_the_application_open(self):
        """A daemon thread: the interpreter does not wait for it on exit."""
        released = threading.Event()
        self.addCleanup(released.set)
        seen = []

        def record():
            seen.append(threading.current_thread().daemon)
            released.wait(10)
        with self.assertRaises(TimeoutError):
            call_with_deadline(record, 0.2)
        self.assertEqual(seen, [True])

    def test_none_is_an_answer_like_any_other(self):
        self.assertIsNone(call_with_deadline(lambda: None, 1))

    def test_update_and_sync_use_this_one(self):
        """One helper, not a copy per caller."""
        import tt.sync_client
        self.assertIs(update._call_with_deadline, call_with_deadline)
        self.assertIs(tt.sync_client._call_with_deadline, call_with_deadline)


if __name__ == '__main__':
    unittest.main()
