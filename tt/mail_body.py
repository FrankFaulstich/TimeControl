"""
Turning the body of an email into the note of a task.

A task made from a mail carries the mail's text as its note, and that text has
to come out of whatever shape the sending program chose. The import used to
take the first text/plain part it met and nothing else. That is right for the
ordinary forward from Outlook, which always sends a plain-text copy beside the
HTML, and wrong for every other shape (issue #662):

- a mail that is HTML only - a system notification sent or redirected
  directly, or one with inline images - has no text/plain part at all, so the
  note came out empty;
- a single-part HTML mail is not multipart, so its HTML source became the
  note, tags and style sheet included;
- a mail forwarded as an attachment carries an empty or signature-only body
  of its own, and the message that matters sits in the attachment;
- a text file attached to an HTML mail was taken for its body.

WHAT IS TAKEN, AND IN WHAT ORDER
--------------------------------
The mail's own text first. Where it offers alternatives, the plain one if it
says anything at all, otherwise the HTML one turned into text - so the
ordinary Outlook forward comes through exactly as it always did, apart from
its line endings. A part sent as an attachment, or carrying a file name, is
never the body - unless it is all the mail consists of. Then the text of every
message attached to it, each set off by the separator Outlook itself puts
above a forwarded message.

WHAT THE TEXT LOOKS LIKE
------------------------
The note is Markdown, but it is first seen in a plain text box a few lines
high, so what comes out of HTML is written to read well there: one line per
paragraph, row or list item, table cells joined by a bar, a link as its text
followed by the address in angle brackets - the way Outlook writes it, and an
address the Markdown preview makes clickable. Nothing is added for emphasis,
headings or images.

Every note leaves here with plain line feeds and without the characters XML
forbids - one of them in a note breaks the SOAP server's whole task list - and
no longer than MAX_NOTE_CHARS, because a single change too large for the sync
server is refused and blocks every change queued behind it.

NOTHING HERE RAISES, OR TAKES LONG
----------------------------------
A mail in a charset nobody has heard of, or with a broken structure, gives a
shorter note or an empty one, never an exception. The import works through
the whole mailbox in one go and deletes what it has imported only at the end,
so one bad message used to stop it half way, leave the earlier ones to be
imported again next time, and do so on every run after that. For the same
reason no HTML is read beyond MAX_HTML_CHARS, and writing stops once a note
is longer than it can be kept: the import runs while the window waits.
"""

import codecs
import html as html_entities
import re
from html.parser import HTMLParser

# Comfortably below what one synchronised change may weigh: 100,000
# characters are at most 400 KB of UTF-8, and the server takes 1 MiB.
MAX_NOTE_CHARS = 100000

# How much HTML is read at all. Ten times what any ordinary notification
# weighs; past it a note would be cut off anyway, and the parsers of some
# Python versions slow down badly on crafted input that size.
MAX_HTML_CHARS = 1000000

# What Outlook writes above a forwarded message, and what an attached one is
# set off by here.
SEPARATOR = '_' * 32

# Characters that are not text: the C0 controls bar tab and line feed, DEL,
# the invisible ones mail programs sprinkle about, the byte order mark, and
# the two noncharacters XML refuses as firmly as the controls. The zero-width
# joiner and non-joiner stay: emoji and Persian or Indic words need them.
_NOT_TEXT = re.compile('[\x00-\x08\x0b-\x1f\x7f'
                       '\N{ZERO WIDTH SPACE}\N{WORD JOINER}'
                       '\N{ZERO WIDTH NO-BREAK SPACE}\ufffe\uffff]')

# Labels a mail program writes without meaning them. What arrives under them
# is UTF-8 or Windows-1252 at least as often, and browsers and Outlook read
# them as Windows-1252 for that reason - so they are guessed at like an
# undeclared charset.
_UNRELIABLE_CHARSETS = frozenset(('ascii', 'iso8859-1'))

# Content of these elements is never text a person wrote. <head> is not among
# them: HTML may leave out both </head> and <body>, and the only parts of a
# head that hold text are listed here anyway.
_SKIPPED = frozenset(('style', 'script', 'title', 'template', 'xml',
                      'noscript', 'object', 'iframe', 'svg'))

# Elements that begin and end a line of their own.
_BLOCKS = frozenset(('p', 'div', 'tr', 'table', 'ul', 'ol', 'li', 'dl', 'dt',
                     'dd', 'blockquote', 'pre', 'center', 'section', 'article',
                     'header', 'footer', 'address', 'form', 'fieldset',
                     'caption', 'figure', 'figcaption', 'main', 'nav'))

# Elements that stand apart by a blank line, as a paragraph would in print.
_SPACED = frozenset(('h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'table', 'ul', 'ol',
                     'blockquote', 'pre'))

# Word's conditional markup, <![if !supportLists]> and <![endif]>. Removed
# before parsing, because older Python versions give up on the whole document
# at the first variant of it they do not expect.
_MARKED_SECTION = re.compile(r'<!\[(?!CDATA\[)[^\]<>]*\]>')

# Elements the plain fallback drops with their content.
_HIDDEN_START = re.compile(r'<(head|style|script|title)\b')


def body_of(message):
    """
    The text of a mail, ready to be a task's note.

    :param message: A parsed message, as email.message_from_bytes() returns
                    it under either policy.
    :return: The text, tidied as the module docstring says. An empty string
             when the mail has no text this can read.
    :rtype: str
    """
    try:
        pieces = [_safe_text(message, whole=True)]
        for attached in _attached_messages(message):
            text = _safe_text(attached, whole=True)
            if text.strip():
                pieces.append(SEPARATOR + '\n' + text)
        return _tidy('\n\n'.join(piece for piece in pieces if piece.strip()))
    except Exception:
        # Deliberately broad: whatever is wrong with one mail, the others in
        # the mailbox still have to be imported - see the module docstring.
        return ''


def html_to_text(html):
    """
    The text of an HTML document, written out as the module docstring says.

    :param html: The document, already decoded.
    :return: Its text. Not yet tidied - body_of() does that for every note.
    :rtype: str
    """
    html = _MARKED_SECTION.sub('', html[:MAX_HTML_CHARS])
    parser = _TextWriter()
    try:
        parser.feed(html)
        parser.close()
        return parser.text()
    except Exception:
        # HTMLParser hardly ever gives up, but it is not promised. Bare text
        # is a worse note than the above and a better one than none.
        return _bare_text(html)


def _bare_text(html):
    """
    The text of an HTML document the parser gave up on: tags removed, and the
    style sheets and scripts with them, which would otherwise come first.
    Linear in the length of the document, whatever is wrong with it.
    """
    lower = html.lower()
    kept, position = [], 0
    while True:
        start = _HIDDEN_START.search(lower, position)
        if not start:
            kept.append(html[position:])
            break
        kept.append(html[position:start.start()])
        end = lower.find('</' + start.group(1), start.end())
        if end < 0:
            break
        close = lower.find('>', end)
        position = len(html) if close < 0 else close + 1
    text = re.sub(r'<[^<>]*>', ' ', ''.join(kept))
    return html_entities.unescape(text)


def _is_attachment(part):
    """
    Whether a part was sent as a file rather than as the text of the mail.

    The file name counts as much as the disposition: Apple Mail sends an
    attached text file as "inline" with a name, and scripted mailers often
    give a name and nothing else. The text Apple Mail continues after an
    inline picture has neither.
    """
    return (part.get_content_disposition() == 'attachment'
            or part.get_filename() is not None)


def _safe_text(part, whole=False):
    """_text_of(), costing only this part's text if it goes wrong."""
    try:
        return _text_of(part, whole)
    except Exception:
        return ''


def _text_of(part, whole=False):
    """
    The text a part contributes as the body of its message.

    Message parts inside it are left to _attached_messages(): this is only
    the message's own text.

    :param whole: The part is a whole message, not a piece of one. Its
                  disposition is then no reason to leave it out, and any text
                  type but HTML is read as plain text - it is all there is.
    """
    kind = part.get_content_type()
    if kind == 'message/rfc822':
        return ''
    if part.is_multipart():
        children = [child for child in part.get_payload()
                    if not _is_attachment(child)
                    and child.get_content_type() != 'message/rfc822']
        if kind == 'multipart/alternative':
            # The plain alternative if it says anything; Outlook's own
            # plain-text rendering is what notes have always looked like.
            # Otherwise whichever of the others does.
            ordered = ([c for c in children if c.get_content_type() == 'text/plain']
                       + [c for c in children if c.get_content_type() != 'text/plain'])
            for child in ordered:
                text = _safe_text(child)
                if text.strip():
                    return text
            return ''
        if kind == 'multipart/signed':
            # The content, then its signature. Only the first is text.
            children = children[:1]
        texts = [_safe_text(child) for child in children]
        return '\n\n'.join(text for text in texts if text.strip())
    if kind.startswith('multipart/'):
        # Declared multipart, but with no boundary Python could find, so it
        # is one undivided body. What is in it is text more often than not,
        # and the import used to keep it.
        return _visible(_decoded(part))
    if kind == 'text/html':
        return '' if _is_attachment(part) and not whole else _visible(html_to_text(_decoded(part)))
    if kind == 'text/plain' or (whole and kind.startswith('text/')):
        return '' if _is_attachment(part) and not whole else _visible(_decoded(part))
    return ''


def _attached_messages(message):
    """Every message attached to this one, in order, nested ones included."""
    found = []
    for part in message.walk():
        if part.get_content_type() != 'message/rfc822':
            continue
        payload = part.get_payload()
        if isinstance(payload, list):
            found.extend(payload[:1])
    return found


def _decoded(part):
    """
    A text part's payload as a string, whatever its charset claims.

    The declared charset first, if Python knows it as a text encoding and it
    is not one of the labels mail programs write without meaning them. Then
    UTF-8, which is what an undeclared charset nowadays mostly is; then
    Windows-1252, which is what it otherwise is in this user's mail - and
    which can decode practically any byte, so a wrong guess costs a few odd
    characters rather than the note.
    """
    payload = part.get_payload(decode=True)
    if not payload:
        return ''
    charset = part.get_content_charset()
    if charset:
        try:
            name = codecs.lookup(charset).name
        except LookupError:
            name = None
        if name and name not in _UNRELIABLE_CHARSETS:
            try:
                return payload.decode(name, errors='replace')
            except (LookupError, UnicodeError, TypeError, ValueError):
                # A codec Python has, but not for text: "quoted-printable"
                # written where the charset belongs.
                pass
    try:
        return payload.decode('utf-8')
    except UnicodeDecodeError:
        return payload.decode('cp1252', errors='replace')


def _visible(text):
    """The text, or nothing if all it holds is white space of some kind."""
    return text if _NOT_TEXT.sub('', text).replace('\xa0', ' ').strip() else ''


def _tidy(text):
    """
    What every note goes through on its way out.

    Line feeds only: a browser hands a text box back with them, and a note
    stored with carriage returns would therefore change - and be saved and
    sent to the other machines - the first time somebody typed into it.
    """
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    text = _NOT_TEXT.sub('', text).replace('\xa0', ' ')
    # Lone surrogates come from bytes Python could not decode, and cannot be
    # written to a UTF-8 file at all.
    text = text.encode('utf-8', 'replace').decode('utf-8')
    lines = [line.rstrip() for line in text.split('\n')]
    text = '\n'.join(lines).strip('\n')
    if len(text) > MAX_NOTE_CHARS:
        cut = text.rfind('\n', 0, MAX_NOTE_CHARS)
        text = text[:cut if cut > 0 else MAX_NOTE_CHARS].rstrip() + '\n\N{HORIZONTAL ELLIPSIS}'
    return text


class _TextWriter(HTMLParser):
    """
    Collects the text of an HTML document, a line at a time.

    Inline text is gathered into the current line; a block boundary ends it.
    How many blank lines separate two blocks is only settled when the next
    text arrives, so that empty elements - which Outlook produces in numbers -
    cannot pile blank lines up.
    """

    # Past this, nothing more is written: the note is cut to MAX_NOTE_CHARS
    # anyway, and some documents - lists nested thousands deep - would
    # otherwise grow far faster than they are long.
    LIMIT = 2 * MAX_NOTE_CHARS

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._lines = []        # finished lines
        # The line being written, in pieces, joined once when it ends. A
        # string grown piece by piece is copied whole each time, which made a
        # table row of a hundred thousand cells take seconds.
        self._line = []
        self._prefix = ''       # what the current line starts with, e.g. "- "
        self._gap = 0           # blank lines owed before the next text
        self._skip = 0          # depth inside elements whose content is dropped
        self._pre = 0           # depth inside <pre>
        self._lists = []        # one entry per open list: None, or the next number
        self._cell = False      # a table cell has begun on the current line
        self._links = []        # per open <a>: (address or None, where its text began)
        self._paragraphs = []   # per open <p>: how much text had been written before it
        self._written = 0       # pieces of text written so far
        self._size = 0          # characters written so far

    # --- the parser's callbacks ---

    def handle_starttag(self, tag, attrs):
        if tag == 'body':
            # Whatever an unclosed element in the head was hiding, the body
            # is not part of it.
            self._skip = 0
            return
        if tag in _SKIPPED:
            self._skip += 1
            return
        if self._skip:
            return
        if tag == 'br':
            self._end_line(force=True)
        elif tag == 'hr':
            self._block(spaced=True)
            self._emit_line(SEPARATOR)
            self._gap = max(self._gap, 1)
        elif tag in ('td', 'th'):
            self._cell = True
        elif tag == 'a':
            self._links.append((_address(dict(attrs).get('href')),
                                len(self._lines), len(self._line)))
        elif tag in ('ul', 'ol'):
            self._block(spaced=not self._lists)
            self._lists.append(1 if tag == 'ol' else None)
        elif tag == 'li':
            self._block()
            depth = max(len(self._lists), 1)
            number = self._lists[-1] if self._lists else None
            if number is None:
                marker = '- '
            else:
                marker = '%d. ' % number
                self._lists[-1] = number + 1
            # Capped, or a list nested thousands deep would spend more on its
            # indentation than on its text.
            self._prefix = '  ' * min(depth - 1, 8) + marker
        elif tag in _BLOCKS or tag in _SPACED:
            # A paragraph inside a list item carries the item's marker.
            prefix = self._prefix
            self._block(spaced=tag in _SPACED)
            self._prefix = prefix
            if tag == 'pre':
                self._pre += 1
            elif tag == 'p':
                self._paragraphs.append(self._written)

    def handle_endtag(self, tag):
        if tag in _SKIPPED:
            self._skip = max(self._skip - 1, 0)
            return
        if self._skip:
            return
        if tag == 'a' and self._links:
            href, line, start = self._links.pop()
            # The link's own text, if it is still on the line being written.
            shown = ''.join(self._line[start:] if line == len(self._lines) else self._line)
            if href and href.lower() not in shown.lower():
                self._write(' <%s>' % href if shown.strip() else '<%s>' % href)
        elif tag in ('ul', 'ol'):
            if self._lists:
                self._lists.pop()
            self._block(spaced=not self._lists)
        elif tag == 'li':
            self._block()
        elif tag == 'pre':
            self._pre = max(self._pre - 1, 0)
            self._block(spaced=True)
        elif tag == 'p':
            empty = self._paragraphs and self._paragraphs.pop() == self._written
            self._block()
            if empty and self._lines:
                # Outlook spaces paragraphs with empty ones, <p>&nbsp;</p>,
                # and that is the only blank line its mail has.
                self._gap = max(self._gap, 1)
        elif tag in _BLOCKS or tag in _SPACED:
            self._block(spaced=tag in _SPACED)

    def handle_data(self, data):
        if self._skip or self._size > self.LIMIT:
            return
        if self._pre:
            parts = data.replace('\r\n', '\n').split('\n')
            for index, part in enumerate(parts):
                if index:
                    self._end_line(force=True)
                if part:
                    self._write(part, raw=True)
            return
        text = re.sub(r'[\s\xa0]+', ' ', data)
        if not text.strip():
            # White space between two words of running text is a space, not
            # nothing - but at the start of a line it is nothing.
            if self._line and not self._line[-1].endswith(' '):
                self._line.append(' ')
                self._size += 1
            return
        self._write(text)

    # --- writing ---

    def _write(self, text, raw=False):
        if self._size > self.LIMIT:
            return
        if not self._line:
            if not raw:
                text = text.lstrip()
                if not text:
                    return
            if self._lines and self._gap:
                self._lines.extend([''] * self._gap)
                self._size += self._gap
            self._gap = 0
            self._line = [self._prefix] if self._prefix else []
            self._size += len(self._prefix)
            self._prefix = ''
            self._cell = False
        elif self._cell:
            # The first text of a further cell in the same row. A label that
            # ends in a colon reads as one with its value.
            while self._line and not self._line[-1].strip():
                self._line.pop()
            if self._line:
                self._line[-1] = self._line[-1].rstrip()
            separator = ' ' if self._line and self._line[-1].endswith(':') else ' | '
            self._line.append(separator)
            self._size += len(separator)
            text = text.lstrip()
            self._cell = False
        self._line.append(text)
        self._written += 1
        self._size += len(text)

    def _emit_line(self, line):
        if self._size > self.LIMIT:
            return
        if self._lines and self._gap:
            self._lines.extend([''] * self._gap)
            self._size += self._gap
        self._gap = 0
        self._lines.append(line)
        self._size += len(line) + 1

    def _end_line(self, force=False):
        line = ''.join(self._line).rstrip()
        if line.strip():
            self._lines.append(line)
            self._size += 1         # the line feed it will be joined with
        elif force and self._lines:
            # A <br> on an empty line is a blank line someone meant.
            self._gap = max(self._gap, 1)
        self._line = []
        self._cell = False

    def _block(self, spaced=False):
        self._end_line()
        self._prefix = ''
        if spaced:
            self._gap = max(self._gap, 1)

    def text(self):
        self._end_line()
        return '\n'.join(self._lines)


def _address(href):
    """
    The address a link is worth showing, or None.

    Web addresses only: a mailto link shows its address as its text anyway,
    and the rest - anchors, scripts, embedded data - mean nothing in a note.
    Spaces are encoded, because the Markdown preview does not take an address
    in angle brackets that contains one.
    """
    if not href:
        return None
    href = href.strip()
    if not re.match(r'(?i)^(https?|ftp)://', href):
        return None
    return href.replace(' ', '%20').replace('<', '%3C').replace('>', '%3E')
