"""
What survives a subject line on its way to becoming a task name.

Issue #619. The half that matters most here is the second one: a rule that
strips markers off the front of a string can just as easily strip off the
front of somebody's actual subject, and it would do it silently - the task
would simply be called something slightly wrong, for ever, and nobody would
connect it to this file.
"""

import os
import sys
import unittest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from tt.mail_subject import (FORWARD_PREFIXES, REPLY_PREFIXES,
                             strip_prefixes)


class TestTheMarkersComeOff(unittest.TestCase):

    def test_the_two_the_issue_named(self):
        self.assertEqual(strip_prefixes('FW: Angebot'), 'Angebot')
        self.assertEqual(strip_prefixes('WG: Angebot'), 'Angebot')

    def test_a_forwarded_reply_loses_both(self):
        """
        The case that decides how far this goes. Outlook stacks the markers,
        so taking off only the outer one leaves "AW: Angebot Halle 3" - no
        better to read than what it started as.
        """
        self.assertEqual(strip_prefixes('WG: AW: Angebot Halle 3'),
                         'Angebot Halle 3')

    def test_the_same_marker_twice(self):
        self.assertEqual(strip_prefixes('WG: WG: Protokoll'), 'Protokoll')

    def test_every_marker_this_knows(self):
        for marker in FORWARD_PREFIXES + REPLY_PREFIXES:
            with self.subTest(marker=marker):
                self.assertEqual(strip_prefixes('%s: Statik' % marker.upper()),
                                 'Statik')

    def test_case_does_not_matter(self):
        """Outlook writes WG:, Thunderbird writes Fwd:, people type wg:."""
        for spelling in ('FW:', 'Fw:', 'fw:', 'fW:'):
            with self.subTest(spelling=spelling):
                self.assertEqual(strip_prefixes(spelling + ' Termin'), 'Termin')

    def test_the_space_after_the_colon_is_optional(self):
        self.assertEqual(strip_prefixes('WG:Angebot'), 'Angebot')

    def test_and_french_typography_puts_one_before_it(self):
        self.assertEqual(strip_prefixes('Re : Demande de devis'),
                         'Demande de devis')

    def test_the_count_some_clients_keep(self):
        for spelling in ('RE[2]:', 'Re(3):', 'AW[12]:', 'RE[ 2 ]:'):
            with self.subTest(spelling=spelling):
                self.assertEqual(strip_prefixes(spelling + ' Ticket 44'),
                                 'Ticket 44')

    def test_surrounding_whitespace_goes_too(self):
        self.assertEqual(strip_prefixes('  WG:   Angebot  '), 'Angebot')


class TestNothingElseComesOff(unittest.TestCase):
    """
    The dangerous half. Everything below is a subject that must arrive whole.
    """

    def test_a_subject_with_no_marker_is_untouched(self):
        for subject in ('Rechnung 2026', 'Halle 3 neu vermessen',
                        'Angebot: Halle 3', 'Projekt: Statik prüfen'):
            with self.subTest(subject=subject):
                self.assertEqual(strip_prefixes(subject), subject)

    def test_a_tag_the_mail_server_added_is_part_of_the_subject(self):
        """
        "[EXTERN]" and its kind are not this module's business. Guessing at
        them would eventually eat a bracketed word somebody meant to keep.
        """
        self.assertEqual(strip_prefixes('FW: [EXTERN] Rechnung'),
                         '[EXTERN] Rechnung')

    def test_a_marker_in_the_middle_stays_where_it_is(self):
        self.assertEqual(strip_prefixes('Angebot WG: Halle 3'),
                         'Angebot WG: Halle 3')

    def test_stripping_stops_at_the_first_thing_it_does_not_know(self):
        self.assertEqual(strip_prefixes('WG: Fehler: RE: doch nicht'),
                         'Fehler: RE: doch nicht')

    def test_a_word_that_merely_begins_like_a_marker(self):
        """
        The failure this is guarding: "Rechnung" begins with "Re". Requiring
        the colon is what keeps it whole.
        """
        for subject in ('Rechnung offen', 'Review Halle 3', 'Awards 2026',
                        'Trennwand bestellen', 'Fwdays Konferenz'):
            with self.subTest(subject=subject):
                self.assertEqual(strip_prefixes(subject), subject)

    def test_single_letter_markers_are_deliberately_not_known(self):
        """
        Italian clients use "R:" and "I:". Knowing them would mean a subject
        that genuinely starts "R: " loses its first word, and a task quietly
        named the wrong thing is worse than one with a marker still on it.
        """
        self.assertEqual(strip_prefixes('R: Preventivo'), 'R: Preventivo')
        self.assertEqual(strip_prefixes('I: Preventivo'), 'I: Preventivo')


class TestTheAwkwardOnes(unittest.TestCase):

    def test_a_subject_that_is_nothing_but_markers_keeps_them(self):
        """
        An empty task name would be the one outcome worse than a strange one:
        the row in the list would have nothing to click on at all.
        """
        self.assertEqual(strip_prefixes('WG:'), 'WG:')
        self.assertEqual(strip_prefixes('WG: AW:'), 'WG: AW:')

    def test_an_empty_subject_stays_empty(self):
        self.assertEqual(strip_prefixes(''), '')

    def test_a_missing_subject_does_not_raise(self):
        """The caller decodes a header that may not be there at all."""
        self.assertEqual(strip_prefixes(None), '')

    def test_a_very_long_chain_still_terminates(self):
        """Each round has to shorten the string, or this would not return."""
        self.assertEqual(strip_prefixes('WG: ' * 200 + 'Protokoll'),
                         'Protokoll')


if __name__ == '__main__':
    unittest.main()
