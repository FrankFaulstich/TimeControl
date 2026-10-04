import os
import re
import sys
import unittest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from tt import sync_client, sync_messages
from tt.sync_messages import (REJECTED_BY_SERVER, register_error_message,
                              sign_in_error_message, sync_error_message)

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


class TestTheHeadlineFitsWhereItIsShown(unittest.TestCase):
    """
    There is one table of explanations and two places that read from it. Only
    one of them is a sign-in form, and calling every unrecognised failure a
    sign-in failure told the wrong story everywhere else.
    """

    def test_a_background_failure_does_not_blame_signing_in(self):
        """
        The two cases from the report. Neither has anything to do with the
        sign-in form, and the user reading the settings status line may not
        have opened that form for months.

        Both halves are asserted. A right headline over a bare code would
        still leave them none the wiser, so these have to be explained rather
        than merely renamed.
        """
        for code in ('busy', 'too_many_ops'):
            with self.subTest(code=code):
                message = sync_error_message(code)
                self.assertNotIn('Sign-in', message)
                self.assertNotIn(code, message,
                                 "still falling through to the code-only wording")

    def test_an_unrecognised_code_is_named_rather_than_swallowed(self):
        """
        The code is the only thing an unknown failure can offer, so it has to
        survive into the message for the user to pass on.
        """
        self.assertEqual(sync_error_message('wat'), "Unexpected error (wat).")
        self.assertEqual(sign_in_error_message('wat'), "Sign-in failed (wat).")

    def test_no_code_at_all_still_produces_a_sentence(self):
        self.assertIn('?', sync_error_message(None))
        self.assertIn('?', sign_in_error_message(''))

    def test_a_known_code_reads_the_same_from_either_side(self):
        """
        The headline differs; the explanation must not. Two tables would
        drift, and the user would be told different things about one failure
        depending on which screen happened to show it.
        """
        for code in ('timeout', 'invalid_token', 'local_io', 'busy'):
            with self.subTest(code=code):
                self.assertEqual(sync_error_message(code), sign_in_error_message(code))

    def test_it_composes_into_the_sentence_the_header_wraps_it_in(self):
        """
        render_sync_notice puts this after "Synchronisation is paused: ", so a
        fallback that began with the word "Synchronisation" would stutter.
        """
        wrapped = "Synchronisation is paused: " + sync_error_message('wat')
        self.assertEqual(wrapped, "Synchronisation is paused: Unexpected error (wat).")

    def test_a_batch_the_server_refused_says_so_once(self):
        """
        Nine codes that differ only in which check tripped. One explanation,
        with the code kept for reporting - nine near-identical sentences would
        be nine chances to word it differently.
        """
        seen = {sync_error_message(code).replace(code, '<code>')
                for code in REJECTED_BY_SERVER}
        self.assertEqual(len(seen), 1, seen)
        self.assertIn('unknown_op', sync_error_message('unknown_op'))


class TestEveryReachableCodeIsExplained(unittest.TestCase):
    """
    The table drifts behind the code that produces the codes - that is exactly
    how "Sign-in failed (busy)." came about. So the sources are read rather
    than trusted: a new error code with no explanation turns this red.
    """

    # Returned by the snapshot upload, which is written to the diagnostic log
    # and never becomes the sync's recorded failure - so no screen can show
    # it, and an entry for it would be a claim nobody could check.
    NOT_SHOWN = {'snapshot_too_large'}

    def codes_in(self, relative_path):
        with open(os.path.join(ROOT, relative_path), encoding='utf-8') as handle:
            source = handle.read()
        found = set(re.findall(r"'error':\s*'([a-z_]+)'", source))
        found |= set(re.findall(r"_record_failure\('([a-z_]+)'\)", source))
        return found - self.NOT_SHOWN

    def assert_all_explained(self, codes, where):
        unexplained = sorted(c for c in codes
                             if sync_error_message(c) == "Unexpected error (%s)." % c)
        self.assertEqual(unexplained, [],
                         "%s produces codes with no explanation: %s" % (where, unexplained))

    def test_every_code_the_client_returns(self):
        codes = self.codes_in('tt/sync_client.py')
        self.assertGreater(len(codes), 5, "the source stopped being readable this way")
        self.assert_all_explained(codes, 'tt/sync_client.py')

    def test_every_failure_the_engine_records(self):
        codes = self.codes_in('tt/sync_engine.py')
        self.assertIn('address_changed', codes, "the source stopped being readable this way")
        self.assert_all_explained(codes, 'tt/sync_engine.py')

    def test_the_server_errors_a_push_or_a_pull_can_meet(self):
        """
        Listed rather than read out of the PHP, because only some of what that
        server can answer ever reaches a cycle: the snapshot endpoint's
        refusals are handled where they happen and never recorded as the
        sync's failure. These are the ones push and pull can return.
        """
        self.assert_all_explained([
            'invalid_token', 'https_required', 'not_installed', 'busy',
            'body_too_large', 'bad_json', 'method_not_allowed', 'unknown_action',
            'ops_not_a_list', 'too_many_ops', 'op_not_an_object', 'unknown_op',
            'bad_lc', 'bad_uid', 'bad_fields',
            # Issue #586: a push to an account that has used up its storage.
            'quota_exceeded',
        ], 'the sync server')

    def test_everything_redeeming_an_invitation_can_meet(self):
        """
        Issue #588. Listed for the reason above: most of these only the
        server says (tc_invite_redeem and the register case in index.php), so
        reading the client's source would not find them.
        """
        codes = [
            # the server's refusals
            'invalid_invite', 'bad_username', 'weak_password', 'username_taken',
            'busy', 'io', 'account_created_sign_in', 'bad_device_uid',
            'unconverted', 'invite_lost',
            # issue #591: the proof of work that goes along with it
            'pow_invalid', 'pow_expired', 'pow_used',
            # the client's own, before anything is sent
            'no_server', 'missing_invite', 'missing_credentials', 'https_required',
            # and the form's
            'passwords_differ',
            # reaching the server at all
            'timeout', 'unreachable', 'tls_failed', 'bad_response', 'address_redirects',
            'not_installed', 'body_too_large', 'bad_json', 'method_not_allowed',
            'unknown_action',
        ]
        fallback = "Could not create the account ({code})."
        unexplained = [c for c in codes if register_error_message(c) == fallback.format(code=c)]
        self.assertEqual(unexplained, [], "redeeming can meet codes with no explanation")


class TestRedeemingAnInvitationIsExplainedForWhatItIs(unittest.TestCase):
    """
    The form that redeems a code is not the sign-in form, and at two moments
    the difference is the whole of what the person needs to know.
    """

    def test_an_unrecognised_failure_is_not_called_a_failed_sign_in(self):
        self.assertEqual(register_error_message('wat'), "Could not create the account (wat).")

    def test_a_lost_answer_says_both_things_it_could_mean(self):
        """
        After a sign-in, no answer means try again. Here the server may have
        made the account and spent the code regardless - so the message has
        to cover both, rather than send somebody to use a code that is gone.
        """
        for code in ('timeout', 'unreachable', 'bad_response'):
            with self.subTest(code=code):
                message = register_error_message(code)
                self.assertNotEqual(message, sign_in_error_message(code))
                self.assertIn('sign', message.lower())
                self.assertIn('still valid', message)

    def test_it_means_the_failures_the_client_tries_signing_in_after(self):
        """
        Two copies of one list, kept apart so this module imports nothing but
        the translator. They have to name the same failures, or one of them
        would be explained as something the client did not do.
        """
        self.assertEqual(sync_messages.UNANSWERED, sync_client.UNANSWERED)

    def test_a_used_code_points_at_signing_in(self):
        """
        The likeliest reason somebody meets a used code is that they used it
        themselves - on an earlier try, or on their first machine.
        """
        self.assertIn('sign in', register_error_message('invalid_invite'))

    def test_and_where_to_turn_if_that_account_was_removed(self):
        """
        Issue #589: an account nothing was ever stored in is removed again
        after a month without use. Signing in then only says the password is
        wrong - it must not say which names exist - so this is the one place
        that can point at the way back.
        """
        self.assertIn('new code', register_error_message('invalid_invite'))

    def test_a_refusal_that_is_not_theirs_says_the_code_survived(self):
        for code in ('username_taken', 'io', 'unconverted'):
            with self.subTest(code=code):
                self.assertIn('not been used up', register_error_message(code))

    def test_busy_is_true_under_a_form_as_well(self):
        """
        It used to say the request "retries on its own", which is only true of
        the background sync - under a form nothing does.
        """
        self.assertNotIn('retries on its own', register_error_message('busy'))
        self.assertIn('Try again', register_error_message('busy'))


if __name__ == '__main__':
    unittest.main()
