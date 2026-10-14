"""
What becomes of an email's body on its way to being a task's note.

Issue #662. The import used to take the first text/plain part it met. That is
right for an ordinary Outlook forward and wrong for everything else: a mail
with HTML only gave an empty note, a single-part HTML mail gave its source
code, a mail forwarded as an attachment gave its own empty body, and a text
file attached to an HTML mail was taken for the mail. Each of these is built
below in the shape the mail programs actually send.

All addresses are invented: the repository is public.
"""

import os
import sys
import time
import unittest
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from unittest import mock

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import tt.mail_body
from tt.mail_body import (MAX_HTML_CHARS, MAX_NOTE_CHARS, SEPARATOR, body_of,
                          html_to_text)

NOTIFICATION = """<html><head>
<meta http-equiv="Content-Type" content="text/html; charset=utf-8">
<style>body, td { font-family: Arial }</style>
<title>Notification</title>
</head><body>
<p><b>Bugs | Development</b></p>
<table>
<tr><td>ID:</td><td><a href="https://tracker.example.com/bug?id=4711">4711</a></td></tr>
<tr><td>Name</td><td>Mesh fails with hinges</td></tr>
<tr><td>Note</td><td>Gr&ouml;&szlig;e der Netzelemente wird ignoriert.</td></tr>
</table>
<p><a href="https://tracker.example.com/bug?id=4711">Open record</a></p>
</body></html>"""

ELLIPSIS = '\N{HORIZONTAL ELLIPSIS}'


def _parsed(message):
    """A built message as the import sees it: sent, received and parsed."""
    return message_from_bytes(message.as_bytes(policy=policy.SMTP))


def _raw(body, content_type=b'text/plain'):
    """A single-part message exactly as these bytes, unencoded."""
    return message_from_bytes(b'Content-Type: ' + content_type
                              + b'\r\nContent-Transfer-Encoding: 8bit\r\n\r\n' + body)


def _html(text=NOTIFICATION):
    return MIMEText(text, 'html', 'utf-8')


def _plain(text):
    return MIMEText(text, 'plain', 'utf-8')


def _image():
    image = MIMEBase('image', 'png')
    image.set_payload(b'\x89PNG\r\n\x1a\n')
    image.add_header('Content-ID', '<logo>')
    return image


def _alternative(*parts):
    message = MIMEMultipart('alternative')
    for part in parts:
        message.attach(part)
    return message


def _mixed(*parts):
    message = MIMEMultipart('mixed')
    for part in parts:
        message.attach(part)
    return message


def _attached(inner, disposition='attachment'):
    """A message carried inside another, as a forward-as-attachment does."""
    wrapper = MIMEBase('message', 'rfc822')
    wrapper.set_payload([inner])
    if disposition:
        wrapper.add_header('Content-Disposition', disposition, filename='forwarded.eml')
    return wrapper


def _named(part, filename, disposition='attachment'):
    part.add_header('Content-Disposition', disposition, filename=filename)
    return part


class TestTheBodyIsFound(unittest.TestCase):

    def assertNotification(self, note):
        self.assertIn('4711', note)
        self.assertIn('Größe der Netzelemente wird ignoriert.', note)
        self.assertNotIn('<td', note)
        self.assertNotIn('font-family', note, 'the style sheet came through')

    def test_plain_text_comes_through_as_it_is(self):
        """The ordinary Outlook forward - what notes have always looked like."""
        message = _alternative(_plain('Von: tracker@example.com\nBetreff: Bug 4711'), _html())
        self.assertEqual(body_of(_parsed(message)),
                         'Von: tracker@example.com\nBetreff: Bug 4711')

    def test_the_plain_alternative_wins_wherever_it_stands(self):
        self.assertEqual(body_of(_parsed(_alternative(_html('<p>HTML</p>'), _plain('Plain')))),
                         'Plain')

    def test_html_only_is_turned_into_text(self):
        """The shape that gave an empty note: no text/plain part anywhere."""
        related = MIMEMultipart('related')
        related.attach(_html())
        related.attach(_image())
        self.assertNotification(body_of(_parsed(_alternative(related))))

    def test_a_single_html_part_is_not_its_source(self):
        self.assertNotification(body_of(_parsed(_html())))

    def test_an_empty_plain_alternative_gives_way_to_the_html(self):
        for empty in ('', '   \n\n ', '\xa0\n', '\N{ZERO WIDTH NO-BREAK SPACE}',
                      '\N{ZERO WIDTH SPACE}\r\n', '\x00'):
            with self.subTest(plain=repr(empty)):
                message = _alternative(_plain(empty), _html())
                self.assertNotification(body_of(_parsed(message)))

    def test_an_attached_text_file_is_not_the_body(self):
        attachment = _named(_plain('log line 1\nlog line 2'), 'log.txt')
        note = body_of(_parsed(_mixed(_html(), attachment)))
        self.assertNotification(note)
        self.assertNotIn('log line', note)

    def test_nor_is_one_sent_inline_with_a_name(self):
        """How Apple Mail attaches a text file, and scripted mailers too."""
        for disposition in ('inline', None):
            with self.subTest(disposition=disposition):
                log = _plain('log line 1')
                if disposition:
                    log.add_header('Content-Disposition', disposition, filename='server.log')
                else:
                    log.set_param('name', 'server.log')
                note = body_of(_parsed(_mixed(_plain('Please look at the log.'), log)))
                self.assertEqual(note, 'Please look at the log.')

    def test_nor_an_attached_alternative(self):
        attached = _named(_alternative(_plain('Attached alternative')), 'a.txt')
        self.assertEqual(body_of(_parsed(_mixed(_plain('Body'), attached))), 'Body')

    def test_but_an_inline_part_without_a_name_is_the_body(self):
        hello = _plain('Hello')
        hello.add_header('Content-Disposition', 'inline')
        self.assertEqual(body_of(_parsed(_mixed(hello))), 'Hello')

    def test_a_mail_that_is_nothing_but_an_attachment_is_read(self):
        """
        A report from a backup job: one text part, sent as a file. It is all
        the mail says, and the import has always kept it.
        """
        report = _named(_plain('Backup OK: 42 files'), 'report.txt')
        self.assertEqual(body_of(_parsed(report)), 'Backup OK: 42 files')
        self.assertEqual(body_of(_raw(b'a;b\n1;2', b'text/csv')), 'a;b\n1;2')

    def test_a_message_forwarded_as_an_attachment(self):
        """
        Its own body is empty, or a signature; the message is attached. Both
        are kept, the attached one set off as Outlook sets off a forward.
        """
        for outer in ('', 'Frank Faulstich\nExample Ltd.'):
            with self.subTest(outer=outer):
                original = EmailMessage()
                original['Subject'] = 'Bug 4711'
                original.set_content(NOTIFICATION, subtype='html')
                note = body_of(_parsed(_mixed(_plain(outer), _attached(original))))
                self.assertNotification(note)
                self.assertIn(SEPARATOR, note)
                self.assertTrue(note.startswith(outer or SEPARATOR))

    def test_the_two_are_laid_out_like_an_outlook_forward(self):
        note = body_of(_parsed(_mixed(_plain('Outer'), _attached(_plain('Inner')))))
        self.assertEqual(note, 'Outer\n\n' + SEPARATOR + '\nInner')

    def test_an_attached_message_with_no_text_adds_nothing(self):
        self.assertEqual(body_of(_parsed(_mixed(_plain('Outer'), _attached(_image())))), 'Outer')

    def test_an_alternative_with_nothing_in_it_does_not_cost_the_attachment(self):
        """Outlook's own forward-as-attachment, with an empty plain part."""
        note = body_of(_parsed(_mixed(_alternative(_plain('')), _attached(_plain('Inner text')))))
        self.assertIn('Inner text', note)

    def test_an_attached_message_inside_an_attached_message(self):
        middle = _mixed(_plain('Middle'), _attached(_plain('Innermost')))
        note = body_of(_parsed(_mixed(_plain('Outer'), _attached(middle))))
        self.assertIn('Innermost', note)
        self.assertEqual(note.count(SEPARATOR), 2)

    def test_an_attached_message_appears_once(self):
        """Inline or wrapped, it is still one message, not two copies."""
        inline = body_of(_parsed(_mixed(_plain('Outer'), _attached(_plain('Inner text'), None))))
        self.assertEqual(inline.count('Inner text'), 1, inline)
        wrapper = _attached(_plain('Inner text'), None)
        self.assertEqual(body_of(_parsed(wrapper)).count('Inner text'), 1)

    def test_a_signed_message_gives_its_content_and_not_its_signature(self):
        for signature in (MIMEBase('application', 'pkcs7-signature'), _plain('SIGNATURE')):
            with self.subTest(signature=signature.get_content_type()):
                signed = MIMEMultipart('signed', protocol='application/pkcs7-signature')
                signed.attach(_alternative(_plain('Bug 4711 was modified.'), _html()))
                if signature.get_content_type() != 'text/plain':
                    signature.set_payload(b'0\x82\x01\x00')
                signed.attach(signature)
                self.assertEqual(body_of(_parsed(signed)), 'Bug 4711 was modified.')

    def test_parts_of_a_mixed_body_are_all_kept(self):
        """Apple Mail splits the text around an inline picture."""
        mixed = _mixed(_plain('Before the picture.'), _image(), _plain('After it.'))
        self.assertEqual(body_of(_parsed(mixed)), 'Before the picture.\n\nAfter it.')

    def test_an_invisible_piece_adds_no_blank_lines(self):
        mixed = _mixed(_plain('a'), _html('<p>&#8203;</p>'), _plain('b'))
        self.assertEqual(body_of(_parsed(mixed)), 'a\n\nb')

    def test_something_that_is_not_text_gives_no_note(self):
        """An opaque S/MIME mail used to give binary rubbish."""
        opaque = MIMEBase('application', 'pkcs7-mime', smime_type='signed-data')
        opaque.set_payload(b'0\x82\x05\x00\x06\t*\x86H\x86\xf7\r\x01\x07\x02')
        self.assertEqual(body_of(_parsed(opaque)), '')

    def test_a_multipart_without_a_boundary_is_read_as_text(self):
        """Python cannot divide it, so it is one body - and the import kept it."""
        for header in (b'multipart/mixed', b'multipart/mixed; boundary="XYZ"'):
            with self.subTest(header=header):
                self.assertEqual(body_of(_raw(b'--ABC\r\nJust text\r\n', header)),
                                 '--ABC\nJust text')

    def test_one_bad_part_costs_only_its_own_text(self):
        """
        Whatever goes wrong with one part, the rest of the mail is still the
        note - it used to be all or nothing.
        """
        message = _parsed(_mixed(_plain('First'), _plain('Broken'), _plain('Last')))
        real = tt.mail_body._decoded

        def failing_on_the_second(part):
            if 'Broken' in real(part):
                raise RuntimeError('broken part')
            return real(part)
        with mock.patch.object(tt.mail_body, '_decoded', side_effect=failing_on_the_second):
            self.assertEqual(body_of(message), 'First\n\nLast')

    def test_one_bad_attached_message_costs_only_its_own_text(self):
        inner = _plain('Inner')
        inner['X-Broken'] = 'yes'
        message = _parsed(_mixed(_plain('Outer'), _attached(inner)))
        real = tt.mail_body._text_of

        def failing_inside(part, whole=False):
            if part.get('X-Broken'):
                raise RuntimeError('broken message')
            return real(part, whole)
        with mock.patch.object(tt.mail_body, '_text_of', side_effect=failing_inside):
            self.assertEqual(body_of(message), 'Outer')


class TestTheCharset(unittest.TestCase):

    def test_the_declared_one_is_used(self):
        self.assertEqual(body_of(_raw(b'\xb3\xf3d\xbc', b'text/plain; charset=iso-8859-2')),
                         '\N{LATIN SMALL LETTER L WITH STROKE}\xf3d'
                         '\N{LATIN SMALL LETTER Z WITH ACUTE}')

    def test_a_wrong_declaration_does_not_cost_the_note(self):
        self.assertTrue(body_of(_raw(b'Gr\xf6\xdfe', b'text/plain; charset=utf-8')).startswith('Gr'))

    def test_one_python_does_not_know(self):
        self.assertEqual(body_of(_raw(b'Gr\xf6\xdfe', b'text/plain; charset="x-unknown"')), 'Größe')

    def test_none_declared_and_utf_8(self):
        self.assertEqual(body_of(_raw('Größe'.encode('utf-8'))), 'Größe')

    def test_none_declared_and_windows_1252(self):
        """\\x80 and \\x84 are where Windows-1252 and Latin-1 part company."""
        self.assertEqual(body_of(_raw(b'\x84Preis\x93 5 \x80')),
                         '\N{DOUBLE LOW-9 QUOTATION MARK}Preis'
                         '\N{LEFT DOUBLE QUOTATION MARK} 5 \N{EURO SIGN}')
        self.assertTrue(body_of(_raw(b'a\x81b')).startswith('a'))

    def test_latin_1_is_read_as_windows_1252(self):
        """As browsers and Outlook read it - otherwise the euro sign vanishes."""
        for label in (b'iso-8859-1', b'latin1', b'us-ascii'):
            with self.subTest(label=label):
                self.assertEqual(body_of(_raw(b'Preis 5 \x80', b'text/plain; charset=' + label)),
                                 'Preis 5 \N{EURO SIGN}')

    def test_a_label_that_is_not_a_text_codec(self):
        """
        "quoted-printable" written where the charset belongs: Python knows the
        codec, but not for text, and decoding with it raised - which cost the
        whole note, the text beside it included.
        """
        self.assertEqual(body_of(_raw(b'Gr\xc3\xb6\xc3\x9fe',
                                      b'text/plain; charset=quoted-printable')), 'Größe')
        raw = (b'Content-Type: multipart/alternative; boundary="B"\r\n\r\n'
               b'--B\r\nContent-Type: text/plain; charset="quoted-printable"\r\n\r\n'
               b'Neue Bestellung\r\n'
               b'--B\r\nContent-Type: text/html; charset=utf-8\r\n\r\n<p>HTML</p>\r\n--B--\r\n')
        self.assertEqual(body_of(message_from_bytes(raw)), 'Neue Bestellung')


class TestWhatTheNoteLooksLike(unittest.TestCase):

    def test_line_feeds_only(self):
        """
        A browser hands a text box back with line feeds, so a note stored with
        carriage returns changed - and was saved and synchronised - the first
        time anybody typed into it.
        """
        raw = (b'Content-Type: text/plain; charset=utf-8\r\n\r\n'
               b'first\r\nsecond\rthird\r\n')
        self.assertEqual(body_of(message_from_bytes(raw)), 'first\nsecond\nthird')

    def test_no_blank_lines_or_trailing_spaces_at_the_ends(self):
        """The box shows six lines; Outlook's forward began with a blank one."""
        self.assertEqual(body_of(_parsed(_plain('\n\n  \nText   \nmore  \n\n'))),
                         'Text\nmore')

    def test_but_the_first_line_keeps_its_indent(self):
        self.assertEqual(body_of(_parsed(_plain('  indented\nnext'))), '  indented\nnext')

    def test_a_no_break_space_is_a_space(self):
        self.assertEqual(body_of(_parsed(_plain('a\xa0b'))), 'a b')

    def test_no_characters_xml_refuses(self):
        """One of them makes the SOAP server refuse to list any task at all."""
        note = body_of(_parsed(_plain('a\x00b\x0cc\x1bd\N{ZERO WIDTH SPACE}e'
                                      '\N{ZERO WIDTH NO-BREAK SPACE}f\x7fg\ufffeh\uffffi')))
        self.assertEqual(note, 'abcdefghi')

    def test_the_joiners_stay(self):
        """Emoji and Persian or Indic words need them; nothing refuses them."""
        text = 'a\N{ZERO WIDTH NON-JOINER}b\N{ZERO WIDTH JOINER}c'
        self.assertEqual(body_of(_parsed(_plain(text))), text)

    def test_no_lone_surrogates(self):
        note = body_of(_raw(b'a+2AA-b', b'text/plain; charset=utf-7'))
        note.encode('utf-8')

    def test_a_note_is_never_too_large_to_synchronise(self):
        note = body_of(_parsed(_plain(('x' * 78 + '\n') * 3000)))
        self.assertTrue(note.endswith('\n' + ELLIPSIS))
        self.assertLessEqual(len(note), MAX_NOTE_CHARS + 2)
        self.assertGreater(len(note), MAX_NOTE_CHARS - 100)
        self.assertEqual(set(note.split('\n')[:-1]), {'x' * 78}, 'not cut at a line end')

    def test_one_long_line_is_cut_too(self):
        note = body_of(_parsed(_plain('x' * (3 * MAX_NOTE_CHARS))))
        self.assertLessEqual(len(note), MAX_NOTE_CHARS + 2)
        self.assertTrue(note.endswith('\n' + ELLIPSIS))

    def test_a_note_of_exactly_the_limit_is_not_cut(self):
        text = (('x' * 99 + '\n') * (MAX_NOTE_CHARS // 100))[:-1] + 'x'
        self.assertEqual(len(text), MAX_NOTE_CHARS)
        self.assertEqual(body_of(_parsed(_plain(text))), text)

    def test_a_broken_message_gives_an_empty_note_rather_than_an_exception(self):
        class Broken:
            def get_content_type(self):
                raise ValueError('broken')

            def walk(self):
                raise ValueError('broken')
        self.assertEqual(body_of(Broken()), '')


class TestHtmlToText(unittest.TestCase):

    def test_table_rows_are_lines_and_cells_are_joined(self):
        text = html_to_text('<table><tr><td>Bug</td><td></td><td>4711</td></tr>'
                            '<tr><td>Status:</td><td>Open</td></tr></table>')
        self.assertEqual(text, 'Bug | 4711\nStatus: Open')

    def test_cells_as_real_html_has_them(self):
        """White space between the cells, header cells, text in several pieces."""
        self.assertEqual(html_to_text('<table><tr><td>Status:</td>\n<td>Open</td></tr>'
                                      '<tr><td>A</td>\n<td> B</td></tr></table>'),
                         'Status: Open\nA | B')
        self.assertEqual(html_to_text('<table><tr><th>A</th><th>B</th></tr></table>'), 'A | B')
        self.assertEqual(html_to_text('<table><tr><td>a <b>b</b></td><td>c <i>d</i></td></tr></table>'),
                         'a b | c d')

    def test_a_link_shows_its_address_once(self):
        self.assertEqual(html_to_text('<a href="https://example.com/a">Open</a>'),
                         'Open <https://example.com/a>')
        self.assertEqual(html_to_text('<a href="https://Example.com/A">https://example.com/a</a>'),
                         'https://example.com/a')
        self.assertEqual(html_to_text('x <a href="https://example.com/"><img src="a"></a>'),
                         'x <https://example.com/>')

    def test_only_its_own_text_counts_as_showing_the_address(self):
        self.assertEqual(html_to_text('https://example.com/a: <a href="https://example.com/a">here</a>'),
                         'https://example.com/a: here <https://example.com/a>')

    def test_only_web_addresses_are_shown(self):
        for href in ('mailto:a@example.com', 'javascript:void(0)', '#top', 'data:image/png;base64,AA'):
            with self.subTest(href=href):
                self.assertEqual(html_to_text('<a href="%s">here</a>' % href), 'here')
        for href, shown in (('http://example.com/x', 'http://example.com/x'),
                            ('HTTPS://EXAMPLE.COM/', 'HTTPS://EXAMPLE.COM/'),
                            (' https://example.com/x\n', 'https://example.com/x')):
            with self.subTest(href=href):
                self.assertEqual(html_to_text('<a href="%s">Open</a>' % href), 'Open <%s>' % shown)

    def test_an_address_the_preview_could_not_take_is_encoded(self):
        self.assertEqual(html_to_text('<a href="https://example.com/a b">x</a>'),
                         'x <https://example.com/a%20b>')
        self.assertEqual(html_to_text('<a href="https://example.com/?q=&lt;b&gt;">x</a>'),
                         'x <https://example.com/?q=%3Cb%3E>')

    def test_lists(self):
        self.assertEqual(html_to_text('<ul><li>one</li><li><p>two</p></li></ul>'
                                      '<ol><li>first</li><li>second</li></ol>'),
                         '- one\n- two\n\n1. first\n2. second')

    def test_nested_lists_are_indented(self):
        self.assertEqual(html_to_text('<ul><li>a<ul><li>b</li></ul></li><li>c</li></ul>'),
                         '- a\n  - b\n- c')

    def test_but_not_without_end(self):
        """Eight levels; past that the indent would only push the text off the box."""
        lines = html_to_text('<ul><li>x' * 12).split('\n')
        self.assertEqual(lines[8], '  ' * 8 + '- x')
        self.assertEqual(lines[11], '  ' * 8 + '- x')

    def test_list_items_as_they_come(self):
        self.assertEqual(html_to_text('<ul><li>a<li>b</ul>'), '- a\n- b')
        self.assertEqual(html_to_text('<ul><li>a</li>b</ul>'), '- a\nb')
        self.assertEqual(html_to_text('<ul><li>a<br>b</li></ul>'), '- a\nb')
        self.assertEqual(html_to_text('<ul><li>\n<span>one</span></li></ul>'), '- one')
        self.assertEqual(html_to_text('<ul><li></li></ul><p>x</p>'), 'x')
        self.assertEqual(html_to_text('<ul><li>a</li></ul><p>b</p>'), '- a\n\nb')

    def test_paragraphs_and_breaks(self):
        """
        One line per paragraph, as Outlook's own plain text has it; its empty
        paragraphs are the only blank lines its mail has.
        """
        html = ('<p class=MsoNormal>Hallo,<o:p></o:p></p>'
                '<p class=MsoNormal><o:p>&nbsp;</o:p></p>'
                '<p class=MsoNormal>erste<br>zweite Zeile</p>'
                '<p>dritte</p>')
        self.assertEqual(html_to_text(html), 'Hallo,\n\nerste\nzweite Zeile\ndritte')
        self.assertEqual(html_to_text('a<br><br>b'), 'a\n\nb')
        self.assertEqual(html_to_text('<p>a<br>\n b</p>'), 'a\nb')

    def test_blocks(self):
        self.assertEqual(html_to_text('<div>a</div><div>b</div>'), 'a\nb')
        self.assertEqual(html_to_text('a<div>b</div>c'), 'a\nb\nc')
        self.assertEqual(html_to_text('<h1>T</h1><p>x</p>'), 'T\n\nx')
        self.assertEqual(html_to_text('<p>a</p><table><tr><td>b</td></tr></table><p>c</p>'),
                         'a\n\nb\n\nc')

    def test_white_space_is_collapsed_except_in_pre(self):
        self.assertEqual(html_to_text('<p>a \n\t b</p><pre>  x\n    y</pre>'),
                         'a b\n\n  x\n    y')
        self.assertEqual(html_to_text('<b>a</b> <i>b</i>'), 'a b')
        self.assertEqual(html_to_text('a <b> </b>b'), 'a b')
        self.assertEqual(html_to_text('<pre>a\n\nb</pre>'), 'a\n\nb')
        self.assertEqual(html_to_text('<pre>x</pre><p>y</p>'), 'x\n\ny')
        self.assertEqual(html_to_text('<pre>a\n   </pre><p>b</p>'), 'a\n\nb')

    def test_a_rule_is_outlooks_separator(self):
        self.assertEqual(html_to_text('<p>above</p><hr><p>below</p>'),
                         'above\n\n%s\n\nbelow' % SEPARATOR)

    def test_what_is_not_text_is_left_out(self):
        html = ('<html><head><title>T</title><style>p{color:red}</style>'
                '<script>alert(1)</script></head><body>'
                '<!--[if gte mso 9]><xml><o:shapedefaults/></xml><![endif]-->'
                '<p><img src="cid:logo" alt="Logo">Text</p></body></html>')
        self.assertEqual(html_to_text(html), 'Text')
        self.assertEqual(html_to_text('<html><title>T</title><p>x</p></html>'), 'x')
        self.assertEqual(html_to_text('<p>a</p><svg><text>logo</text></svg>'), 'a')
        self.assertEqual(html_to_text('<xml>x</xml><p>a</p>'), 'a')

    def test_html_that_leaves_out_its_head_and_body_tags(self):
        """Valid HTML, and what minifiers produce."""
        self.assertEqual(html_to_text('<!DOCTYPE html><html><head><meta charset=utf-8>'
                                      '<title>Order</title><p>Order 4711 received.</p>'),
                         'Order 4711 received.')
        self.assertEqual(html_to_text('<head><title>T</title><body><p>Text</p>'), 'Text')

    def test_the_body_ends_whatever_the_head_left_open(self):
        self.assertEqual(html_to_text('<head><xml><o:settings>junk<body><p>Text</p>'), 'Text')

    def test_end_tags_that_were_never_opened(self):
        self.assertEqual(html_to_text('<p>a</p></style><p>b</p>'), 'a\nb')
        self.assertEqual(html_to_text('<style>p{x}</style><p>a</a>b</p>'), 'ab')
        self.assertEqual(html_to_text('<style>p{x}</style><p>a</p></ul><p>b</p>'), 'a\n\nb')
        self.assertEqual(html_to_text('<p>a</p></pre><p>b  c</p>'), 'a\n\nb c')

    def test_text_after_the_last_tag(self):
        self.assertEqual(html_to_text('<p>Grüße</p>R&D'), 'Grüße\nR&D')

    def test_outlooks_list_markup(self):
        """Python versions disagree about who gets <![if ...]>; neither shows it."""
        for section in ('<![if !supportLists]>', '<![ if !supportLists ]>', '<![foo]>'):
            with self.subTest(section=section):
                text = html_to_text('<style>p{color:red}</style><p class=MsoListParagraph>%s'
                                    '<span>-<span>&nbsp;&nbsp;</span></span><![endif]>'
                                    'Punkt &amp; Komma</p>' % section)
                self.assertEqual(text, '- Punkt & Komma')

    def test_when_the_parser_gives_up_the_text_is_still_readable(self):
        with mock.patch.object(tt.mail_body._TextWriter, 'feed', side_effect=AssertionError):
            text = html_to_text('<html><head><style>p{color:red}</style></head>'
                                '<body><p>a</p><p>R&amp;D</p></body></html>')
            unclosed = html_to_text('<p>a</p><style>p{color:red}')
        self.assertEqual(text.split(), ['a', 'R&D'])
        self.assertEqual(unclosed.split(), ['a'])


class TestNothingTakesLong(unittest.TestCase):
    """
    The import runs while the window waits. Every one of these used to take
    seconds to minutes, or gigabytes.
    """

    def assertQuick(self, html, seconds=2.0):
        started = time.perf_counter()
        note = body_of(_parsed(_html(html)))
        self.assertLess(time.perf_counter() - started, seconds)
        self.assertLessEqual(len(note), MAX_NOTE_CHARS + 2)
        return note

    def test_lists_nested_thousands_deep(self):
        self.assertQuick('<ul><li>x' * 20000)

    def test_one_endless_line(self):
        self.assertQuick('<table><tr>' + '<td>x</td>' * 100000 + '</tr></table>')

    def test_more_html_than_is_ever_read(self):
        self.assertEqual(len(html_to_text('<p>' + 'x' * (2 * MAX_HTML_CHARS))), MAX_HTML_CHARS - 3)


if __name__ == '__main__':
    unittest.main()
