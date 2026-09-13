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
in German and Odp: in Czech mail services.

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

# Weitergeleitet, Transféré, Reenviar, Forward.
FORWARD_PREFIXES = ('fwd', 'fw', 'wg', 'tr', 'rv')

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
