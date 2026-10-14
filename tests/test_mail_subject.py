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
import time
import unittest
from email import message_from_bytes, policy

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from tt.mail_subject import (FORWARD_PREFIXES, REPLY_PREFIXES,
                             read_subject, strip_prefixes)


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

    def test_polish_outlook_forwards_too(self):
        """Issue #662: "PD: Dev II | Bugs ..." arrived as a task name."""
        self.assertEqual(strip_prefixes('PD: Bugs | 4711'), 'Bugs | 4711')


def _message(header_lines):
    """A message whose header is exactly these bytes, as it would arrive."""
    return message_from_bytes(header_lines + b'\r\n\r\nBody.\r\n')


class TestReadingTheHeader(unittest.TestCase):
    """
    Issue #662. A long subject arrives folded, and the folds went into the
    task name - "Plastic RC\\r\\n Hinges" - in every report and every list the
    task appeared in. And a header in a charset Python did not know stopped
    the whole import.
    """

    def test_a_folded_subject_is_one_line(self):
        for fold in (b'\r\n ', b'\r\n\t', b'\n '):
            with self.subTest(fold=fold):
                message = _message(b'Subject: WG: Bugs | 4711 - Plastic RC' + fold
                                   + b'Hinges: Mesh Input Fail 2')
                self.assertEqual(read_subject(message),
                                 'WG: Bugs | 4711 - Plastic RC Hinges: Mesh Input Fail 2')

    def test_a_space_before_an_encoded_word_on_the_next_line_stays(self):
        """It went: "4711 -Schnittgrößen"."""
        message = _message(b'Subject: Bugs | 4711 -\r\n'
                           b' =?utf-8?q?Schnittgr=C3=B6=C3=9Fen?= falsch')
        self.assertEqual(read_subject(message), 'Bugs | 4711 - Schnittgrößen falsch')

    def test_encoded_words_next_to_each_other_join_up(self):
        message = _message(b'Subject: =?utf-8?B?R3LDtsOf?=\r\n =?utf-8?B?ZQ==?= ok')
        self.assertEqual(read_subject(message), 'Größe ok')

    def test_raw_utf_8_in_the_header(self):
        self.assertEqual(read_subject(_message('Subject: WG: Größe'.encode('utf-8'))),
                         'WG: Größe')

    def test_a_charset_python_does_not_know(self):
        """What such a header holds is usually Windows-1252, not UTF-8."""
        message = _message(b'Subject: =?x-unknown?Q?Gr=F6=DFe?= ok')
        self.assertEqual(read_subject(message), 'Größe ok')

    def test_raw_bytes_that_are_not_utf_8(self):
        self.assertEqual(read_subject(_message(b'Subject: Gr\xf6\xdfe')), 'Größe')

    def test_no_subject_at_all(self):
        self.assertEqual(read_subject(_message(b'From: a@example.com')), '')

    def test_either_policy(self):
        """The import parses with the old one; nothing here may depend on it."""
        raw = (b'Subject: =?utf-8?q?F=C3=BCr?= Halle 3\r\n\r\nBody.')
        for chosen in (policy.compat32, policy.default):
            with self.subTest(policy=chosen):
                self.assertEqual(read_subject(message_from_bytes(raw, policy=chosen)),
                                 'Für Halle 3')

    def test_it_never_raises(self):
        class Broken:
            def raw_items(self):
                raise ValueError('broken')
        self.assertEqual(read_subject(Broken()), '')

    def test_the_header_name_in_any_case(self):
        self.assertEqual(read_subject(_message(b'subject: Hi')), 'Hi')

    def test_a_fold_before_an_encoded_word_of_any_kind(self):
        """The fold that lost its space was not always CRLF and a space."""
        for fold in (b'\n ', b'\r\n\t'):
            with self.subTest(fold=fold):
                message = _message(b'Subject: Bugs | 4711 -' + fold
                                   + b'=?utf-8?q?Schnittgr=C3=B6=C3=9Fen?= falsch')
                self.assertEqual(read_subject(message), 'Bugs | 4711 - Schnittgrößen falsch')

    def test_every_run_of_white_space_is_one_space(self):
        self.assertEqual(read_subject(_message(b'Subject: A  B\tC')), 'A B C')

    def test_the_declared_charset_of_each_word(self):
        self.assertEqual(read_subject(_message(b'Subject: =?iso-8859-2?Q?=B3=F3d=BC?=')),
                         '\N{LATIN SMALL LETTER L WITH STROKE}\xf3d'
                         '\N{LATIN SMALL LETTER Z WITH ACUTE}')
        self.assertEqual(read_subject(_message(b'Subject: =?utf-8?Q?Gr?= =?iso-8859-1?Q?=F6=DFe?=')),
                         'Größe')

    def test_a_word_whose_charset_is_wrong(self):
        self.assertTrue(read_subject(_message(b'Subject: =?utf-8?Q?Gr=F6=DFe?=')).startswith('Gr'))

    def test_raw_windows_1252(self):
        """\\x80 is where Windows-1252 and Latin-1 part company."""
        self.assertEqual(read_subject(_message(b'Subject: Preis 5 \x80')),
                         'Preis 5 \N{EURO SIGN}')
        self.assertTrue(read_subject(_message(b'Subject: a\x81b')).startswith('a'))
        self.assertEqual(read_subject(_message(b'Subject: Gr\xf6\xdfe\r\n und mehr')),
                         'Größe und mehr')

    def test_raw_bytes_beside_an_encoded_word(self):
        """
        Each raw byte came out as six characters of escape sequence:
        "Gr\\udcc3\\udcb6..." - in the task name, for good.
        """
        for raw, expected in ((b'Gr\xc3\xb6\xc3\x9fe =?utf-8?q?F=C3=BCr?= Halle', 'Größe Für Halle'),
                              (b'Gr\xf6\xdfe =?utf-8?q?F=C3=BCr?= Halle', 'Größe Für Halle'),
                              (b'=?utf-8?q?F=C3=BCr?= Gr\xc3\xb6\xc3\x9fe', 'Für Größe'),
                              (b'C:\\users =?utf-8?q?F=C3=BCr?=', 'C:\\users Für'),
                              ('Preis 5 \N{EURO SIGN} '.encode('utf-8') + b'=?utf-8?q?F=C3=BCr?=',
                               'Preis 5 \N{EURO SIGN} Für')):
            with self.subTest(raw=raw):
                self.assertEqual(read_subject(_message(b'Subject: ' + raw)), expected)

    def test_one_broken_word_does_not_cost_the_rest(self):
        """It cost the whole subject, and the task was called "No Subject"."""
        self.assertEqual(read_subject(_message(b'Subject: Bugs 4711 =?utf-8?B?Q?= tail')),
                         'Bugs 4711 =?utf-8?B?Q?= tail')
        for charset in (b'idna', b'quoted-printable'):
            with self.subTest(charset=charset):
                self.assertEqual(read_subject(_message(b'Subject: =?' + charset + b'?Q?abc?= tail')),
                                 'abc tail')

    def test_no_characters_a_task_name_cannot_hold(self):
        """
        Controls and XML's noncharacters break the SOAP list; a lone surrogate
        cannot be saved at all, and the task was lost with it.
        """
        self.assertEqual(read_subject(_message(b'Subject: =?utf-8?Q?a=00b=7Fc?=')), 'a b c')
        self.assertEqual(read_subject(_message(b'Subject: =?utf-8?q?a=EF=BF=BEb=EF=BF=BFc?=')),
                         'a b c')
        read_subject(_message(b'Subject: =?utf-7?q?Gr+2AA-e?=')).encode('utf-8')

        class Parsed:
            """A message parsed from a string that held a lone surrogate."""
            def raw_items(self):
                return [('Subject', 'a\ud800b')]
        self.assertEqual(read_subject(Parsed()), 'a?b')

    def test_a_huge_header_takes_no_time(self):
        """Unfolded first, a malformed one took seconds per hundred kilobytes."""
        header = b'Subject: ' + b'\r\n '.join([b'=?a?q?x'] * 15000)
        started = time.perf_counter()
        read_subject(_message(header))
        self.assertLess(time.perf_counter() - started, 1.0)


if __name__ == '__main__':
    unittest.main()
