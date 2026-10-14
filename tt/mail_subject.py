"""
Turning an email subject line into the name of a task.

A task created by forwarding a mail to yourself arrives called
"WG: AW: Angebot Halle 3". None of that front matter is the thing the task is
about - it is a record of how the message travelled - and in a list of
today's work it is what you read first. So it comes off.

WHICH MARKERS, AND WHY THESE
----------------------------
Both the forwarding and the replying ones, because they stack: a forwarded
reply carries one of each, and taking off only the outer one leaves the
subject looking no better than before.

The list covers the five languages the application itself ships in, because
the marker is written by the mail program of the person forwarding the
message - which is this user's own - rather than by the sender. Outlook says
WG: in German, TR: in French, RV: in Spanish and FW: in English and Czech;
Gmail and Thunderbird say Fwd:. Replies are RE: nearly everywhere, with AW:
in German and Odp: in Czech mail services. And Polish Outlook's PD:, because
forwards marked that way have turned up among this user's imported mail
(issue #662) - Outlook does not always forward in the language it is set to.

READING THE HEADER
------------------
Before any of that, the header has to become text, and two things stood in
the way (issue #662). A long subject arrives folded over several lines, and
the line breaks went into the task name with it - visible in every report the
task appears in, and impossible to type back when asking for the task by
name. And a header in a charset Python does not know, or with raw 8-bit bytes
in it, raised an exception that stopped the whole import. read_subject() does
both properly.

Deliberately no single-letter markers - Italian clients use "R:" and "I:" -
because a subject that genuinely begins "R: " would lose its first word and
nothing would say so. The same caution rules out "REF:", which in French is
as often a reference number as a reply.

WHAT IS LEFT ALONE
------------------
Everything from the first word that is not one of these markers, exactly as
it was: "FW: [EXTERN] Rechnung" becomes "[EXTERN] Rechnung", not "Rechnung".
Tags a mail server adds are part of the subject as far as this is concerned,
and a person who wants them gone can say so in a way this file cannot guess.
"""

import re
from email.header import decode_header

# Weitergeleitet, Transféré, Reenviar, Forward, Przekaż dalej.
FORWARD_PREFIXES = ('fwd', 'fw', 'wg', 'tr', 'rv', 'pd')

# Antwort, Odpověď, Reply.
REPLY_PREFIXES = ('antwort', 'antw', 'aw', 'odp', 're')

# Longest first, so "antwort:" is not read as "antw" followed by something
# that is not a colon. The regular expression would find its way there by
# backtracking anyway; ordering it says so out loud.
PREFIXES = tuple(sorted(FORWARD_PREFIXES + REPLY_PREFIXES,
                        key=len, reverse=True))

# One marker at the very front, and what may sit around it:
#
#   \s*            leading space, on the second and later rounds
#   (?:re|aw|...)  the marker itself, in any case
#   [\[(]\d+[\])]  the count some clients keep - "RE[2]:", "Re(3):"
#   \s*:\s*        the colon, which French typography puts a space before
_MARKER = re.compile(
    r'^\s*(?:%s)\s*(?:[\[(]\s*\d+\s*[\])])?\s*:\s*' % '|'.join(PREFIXES),
    re.IGNORECASE)


# What a task name may not contain: control characters, and the two
# noncharacters XML refuses as firmly - one of them in a name breaks the SOAP
# server's task list.
_CONTROLS = re.compile('[\x00-\x1f\x7f\ufffe\uffff]')

# More than a task name ever needs, and the decoder takes time that grows
# with the square of a header's length when the header is malformed.
_MAX_HEADER_CHARS = 4096


def read_subject(message):
    """
    The subject of a mail as one line of text.

    Decoded - encoded words in any charset Python knows, raw UTF-8 too - and
    unfolded, with every run of white space, the folds included, made a
    single space. The words of a subject are what a task name needs; its
    exact spacing is not.

    Decoded here rather than by the email package's own header parsing,
    because that one reads bytes in a charset it does not know as UTF-8, and
    what such a header holds is far more often Windows-1252 - "Gr��e" for
    "Größe". This tries the declared charset, then UTF-8, then Windows-1252,
    the same order tt/mail_body.py reads a body in.

    :param message: A parsed message, under either policy. Its header is read
                    as it arrived, before either policy has had a go at it.
    :return: The subject, or an empty string if there is none or it cannot
             be read at all. Never raises: one unreadable header must not
             stop the rest of the mailbox from being imported.
    :rtype: str
    """
    try:
        raw = next((value for name, value in message.raw_items()
                    if name.lower() == 'subject'), None)
        if raw is None:
            return ''
        # Unfolded first: decode_header() works line by line and drops the
        # space at the start of each, which ran "556016 -" and an encoded
        # word on the next line together.
        raw = re.sub(r'\r?\n(?=[ \t])', '', str(raw)[:_MAX_HEADER_CHARS])
        # Bytes outside ASCII arrive held as surrogates. Made text before
        # anything else, because beside an encoded word decode_header() would
        # turn each of them into six characters of escape sequence.
        try:
            raw = _bytes_as_text(raw.encode('utf-8', 'surrogateescape'))
        except UnicodeError:
            pass
        try:
            text = ''.join(_decode_word(word, charset)
                           for word, charset in decode_header(raw))
        except Exception:
            # One broken encoded word. The rest of the subject is still a
            # better name than none, with that word left as it arrived.
            text = raw
    except Exception:
        return ''
    # Lone surrogates from a codec that let them through cannot be written to
    # a UTF-8 file, and the task would be lost with them.
    text = text.encode('utf-8', 'replace').decode('utf-8')
    return ' '.join(_CONTROLS.sub(' ', text).split())


def _decode_word(word, charset):
    """One piece of a header as decode_header() returns it, as text."""
    if isinstance(word, str):
        return word
    if charset is None:
        # Text that was not encoded, sitting beside words that were:
        # decode_header() hands it back in this form.
        try:
            return word.decode('raw-unicode-escape')
        except UnicodeDecodeError:
            return word.decode('latin-1')
    if charset != 'unknown-8bit':
        try:
            return word.decode(charset, errors='replace')
        except (LookupError, UnicodeError, TypeError, ValueError):
            # Unknown, or a codec Python has but not for text.
            pass
    return _bytes_as_text(word)


def _bytes_as_text(data):
    """Bytes of no known charset: UTF-8 if they are that, else Windows-1252."""
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError:
        return data.decode('cp1252', errors='replace')


def strip_prefixes(subject):
    """
    Removes the mail program's markers from the front of a subject line.

    Repeatedly, because they stack, and in any order, because a message that
    has been round a few times carries them in the order it travelled.

    :param subject: The decoded subject line.
    :return: The subject without its leading markers. The original, trimmed,
             when removing them would leave nothing at all - a message
             actually titled "WG:" is a strange thing, but an empty task name
             is a worse answer to it than a strange one.
    :rtype: str
    """
    text = subject or ''
    while True:
        shorter = _MARKER.sub('', text, count=1)
        if shorter == text:
            break
        text = shorter
    return text.strip() or (subject or '').strip()
