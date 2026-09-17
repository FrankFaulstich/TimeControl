"""
Sealing an operation so that only machines holding the passphrase can read it.

This module is the cryptographic core and nothing else: it knows how to turn a
passphrase into a key, how to seal one operation and how to open it again. It
does no file access, holds no state between calls, and knows nothing about the
server, the queue or the document. That is deliberate - everything here can be
tested without a network, a server or a running application, and the wiring
that does touch those things is kept separate so that a mistake there cannot
quietly change what the cryptography does.

WHY THE WHOLE OPERATION IS SEALED, NOT JUST ITS FIELDS
------------------------------------------------------
The obvious design seals the `f` object, since that is where the names and the
notes live and since the server only ever checks that it is an object at all
(php-server/tc/lib/oplog.php:600-602). It is not enough. Seven of the twelve
verbs carry no `f` whatsoever - a deletion is `{op: 'task.delete', uid, ts}`
(tt/TimeTracker.py:818), and so are the moves and the opening and closing of
time entries. For those there would be nothing to encrypt and, worse, nothing
to authenticate: anybody able to append to the log could write a plausible
`project.delete`, and every machine would carry it out, taking the project and
all its tasks with it (tt/sync_apply.py:274-281).

So the unit of sealing is the operation itself. What travels is an envelope
carrying only what the server structurally needs, and the real operation -
verb, identifiers, timestamps and fields alike - is inside the ciphertext.
This also hides the stream of verbs, which is worth more than it looks: an
`entry.add` with a start time and an `entry.close` with an end time are a
timesheet, accurate to the second, even when every name in it is unreadable.

WHAT REMAINS IN THE CLEAR, AND WHY IT HAS TO
--------------------------------------------
`op`   A verb from the server's fixed list, or the batch is refused
       (oplog.php:589). Every sealed operation carries the same placeholder,
       so the verb no longer says anything about what happened.
`lc`   The per-device counter the server uses to recognise a repeated push
       (oplog.php:154). It is the mechanism that makes a lost response
       harmless, and it cannot be hidden without giving that up.

Everything else the server tolerates - uid, project, task, ts, start, end - is
simply left out. The server only validates those when they are present
(oplog.php:595-599), so omitting them costs nothing and keeps the shape of the
document off the wire.

WHAT THIS DOES NOT DO
---------------------
Confidentiality, not integrity of the log as a whole. Each operation is
authenticated on its own, so it cannot be altered or moved to another account
- but the order of operations is the server's to assign (oplog.php:158), and
nothing here stops a hostile server from withholding an operation, replaying
an old one, or reordering two. Guarding against that needs a chain across
operations, which is a larger change; `ENVELOPE_VERSION` is what leaves room
for it.

THE KEY IDENTIFIER IS NOT A WEAKNESS
------------------------------------
Each envelope names the key that sealed it. That is a verification oracle: a
guessed passphrase can be checked against it. It gives away nothing, because
the ciphertext already is such an oracle - AES-GCM authenticates, so a guess
can simply be tried. The cost of a guess is scrypt either way, and that is
where the defence actually lives. Without the identifier a changed passphrase
would be silent data loss, since the server is blind and accepts operations
sealed with a key nobody holds any more.
"""

import base64
import hashlib
import hmac
import json
import zlib

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# The name of this construction. It travels in the authenticated data, so a
# later suite cannot be passed off as this one.
SUITE = "tc-e2ee-1"

# Bumped when the envelope's shape or the construction changes. A reader that
# meets a version it does not know must refuse rather than guess.
ENVELOPE_VERSION = 1

# The verb every sealed operation shows the server. It has to be a member of
# the server's list (oplog.php:23-27) or the push is refused - which is a
# useful property rather than an obstacle: a server that has not been taught
# this verb rejects encrypted traffic loudly instead of storing something its
# own tools cannot read.
SEALED_OP = "op.sealed"

# scrypt, from the standard library, so the key derivation adds no dependency
# of its own. Measured on the development machine: 0.26 s and 128 MiB. That is
# paid once when the passphrase is entered, not on every operation - the
# derived key is what gets kept.
SCRYPT_N = 1 << 17
SCRYPT_R = 8
SCRYPT_P = 1

# OpenSSL refuses scrypt outright when it would exceed its own memory ceiling,
# and the default ceiling is below what these parameters need. Passing the
# limit explicitly is not optional: without it the call raises
# "memory limit exceeded" rather than producing a key.
SCRYPT_MAXMEM = 128 * SCRYPT_R * SCRYPT_N * 2

SALT_BYTES = 16
KEY_BYTES = 32
NONCE_BYTES = 12


class SealError(Exception):
    """Something could not be sealed. Always a programming error here."""


class OpenError(Exception):
    """
    A sealed operation could not be read.

    Deliberately its own exception rather than a returned None. An unreadable
    operation must never be skipped quietly: the sync path swallows errors in
    several places by design, and an operation silently dropped is two
    machines that stop agreeing with nothing to show for it. Whoever catches
    this has to decide what to do, and cannot do so by accident.
    """


def new_salt():
    """
    A fresh salt for a new account.

    Generated on the machine that sets up the account, never taken from the
    server: the server is the party this is meant to keep data from, and one
    that handed the same salt to every account would let a single precomputed
    table serve against all of them.

    :rtype: bytes
    """
    import os
    return os.urandom(SALT_BYTES)


def derive_key(passphrase, salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P):
    """
    Turns a passphrase into the master key.

    The parameters are arguments rather than constants read from the module so
    that a key derived years ago with weaker ones can still be reproduced: the
    values in force when an account was set up are stored with its salt, and
    passed back in here. Raising the module's defaults then affects new
    accounts without stranding old ones.

    :param passphrase: What the user typed. Normalised to UTF-8 bytes.
    :param salt: The account's salt, as returned by new_salt().
    :return: The master key. Never used directly - see _subkey().
    :rtype: bytes
    """
    if not isinstance(salt, (bytes, bytearray)) or len(salt) != SALT_BYTES:
        raise SealError("the salt must be %d bytes" % SALT_BYTES)
    if not passphrase:
        raise SealError("the passphrase is empty")
    return hashlib.scrypt(
        passphrase.encode("utf-8"), salt=bytes(salt),
        n=n, r=r, p=p, dklen=KEY_BYTES, maxmem=_maxmem_for(n, r),
    )


def _maxmem_for(n, r):
    """
    The ceiling to hand OpenSSL for these parameters.

    Derived rather than fixed: the constant above is right for the defaults,
    but derive_key() accepts others, and a ceiling that does not grow with
    them turns a legitimate parameter choice into "memory limit exceeded".
    """
    return 128 * r * n * 2


def _subkey(master, label, length=32):
    """
    One purpose-specific key from the master.

    HMAC-based, in the shape of HKDF's expand step, so that the key used to
    encrypt, the identifier that travels on the wire and the fingerprint shown
    to the user are three unrelated values. Nothing today would break if they
    were the same, but keys that serve two purposes are how constructions come
    apart later, and separating them costs nothing.
    """
    out = hmac.new(master, b"\x01" + SUITE.encode("ascii") + b"/" + label,
                   hashlib.sha256).digest()
    return out[:length]


def key_id(master):
    """
    The short public name of a key, carried in every envelope.

    Eight hexadecimal characters. Its only job is to let a reader tell "sealed
    with a key I do not have" from "damaged", which is what makes changing the
    passphrase something other than silent loss.

    :rtype: str
    """
    return _subkey(master, b"key-id", 4).hex()


def fingerprint(master):
    """
    The key in a form a person can read aloud and compare.

    Shown on both machines when a second one is set up: same passphrase and
    same salt give the same fingerprint, and a mistyped passphrase shows a
    different one immediately rather than after a sync has gone wrong.

    Grouped in fours because that is what makes two of them comparable by eye.
    Derived under its own label, so what is read off a screen is not the value
    that travels on the wire.

    :rtype: str
    """
    raw = _subkey(master, b"fingerprint", 6).hex().upper()
    return "-".join(raw[i:i + 4] for i in range(0, len(raw), 4))


def _canonical(op):
    """
    The bytes that actually get encrypted.

    Sorted keys and no incidental whitespace, so that sealing the same
    operation twice differs only in the nonce. Not required by anything today;
    it is what makes the tests able to say "these two are the same operation"
    without decrypting, and it costs nothing.
    """
    return json.dumps(op, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _associated(context, lc):
    """
    The cleartext this ciphertext is bound to.

    Length-prefixed rather than joined with a separator. Joining invites the
    one bug this is meant to prevent: two different (context, lc) pairs whose
    concatenation is identical, and therefore a ciphertext that can be moved
    between them while still verifying.

    `context` names the account. Binding it stops a ciphertext from being
    transplanted into another account on the same server, which is otherwise
    free for whoever holds the store.
    """
    parts = (SUITE.encode("ascii"),
             str(ENVELOPE_VERSION).encode("ascii"),
             SEALED_OP.encode("ascii"),
             str(context).encode("utf-8"),
             str(lc).encode("ascii"))
    return b"".join(len(p).to_bytes(4, "big") + p for p in parts)


def seal(op, master, context):
    """
    Wraps one operation into the envelope that travels to the server.

    :param op: The operation as the queue holds it. Must carry 'lc'.
    :param master: The key from derive_key().
    :param context: The account this belongs to, bound into the ciphertext.
    :return: The envelope. Its 'f' is an object, which is what lets the
             server's existing check accept it unchanged.
    :rtype: dict
    """
    if not isinstance(op, dict):
        raise SealError("an operation must be an object")
    lc = op.get("lc")
    if not isinstance(lc, int) or isinstance(lc, bool) or lc < 1:
        raise SealError("an operation needs an lc of 1 or more")

    import os
    nonce = os.urandom(NONCE_BYTES)
    cipher = AESGCM(_subkey(master, b"enc"))
    sealed = cipher.encrypt(nonce, _canonical(op), _associated(context, lc))
    return {
        "op": SEALED_OP,
        "lc": lc,
        "f": {
            "v": ENVELOPE_VERSION,
            "k": key_id(master),
            "c": base64.b64encode(nonce + sealed).decode("ascii"),
        },
    }


def is_sealed(wire):
    """Whether an operation arriving from the server is one of ours."""
    return isinstance(wire, dict) and wire.get("op") == SEALED_OP


def sealed_key_id(wire):
    """
    Which key an envelope names, or None if it does not name one.

    Separate from open_sealed() so that a caller can tell "not my key" from
    "damaged" without having to attempt the decryption first.
    """
    fields = wire.get("f") if isinstance(wire, dict) else None
    if not isinstance(fields, dict):
        return None
    named = fields.get("k")
    return named if isinstance(named, str) else None


def open_sealed(wire, master, context):
    """
    Recovers the operation from an envelope.

    Everything that can go wrong raises OpenError, including a version this
    build does not know: an envelope from a later version may mean something
    different, and reading it under this version's rules would be a guess.

    :raises OpenError: unreadable, altered, or sealed with another key.
    :rtype: dict
    """
    if not is_sealed(wire):
        raise OpenError("not a sealed operation")
    fields = wire.get("f")
    if not isinstance(fields, dict):
        raise OpenError("the envelope carries no fields")

    version = fields.get("v")
    if version != ENVELOPE_VERSION:
        raise OpenError("envelope version %r is not one this build knows"
                        % (version,))

    lc = wire.get("lc")
    if not isinstance(lc, int) or isinstance(lc, bool):
        raise OpenError("the envelope carries no usable lc")

    raw = fields.get("c")
    if not isinstance(raw, str):
        raise OpenError("the envelope carries no ciphertext")
    try:
        blob = base64.b64decode(raw, validate=True)
    except Exception:
        raise OpenError("the ciphertext is not valid base64")
    if len(blob) <= NONCE_BYTES:
        raise OpenError("the ciphertext is too short to be one")

    cipher = AESGCM(_subkey(master, b"enc"))
    try:
        plain = cipher.decrypt(blob[:NONCE_BYTES], blob[NONCE_BYTES:],
                               _associated(context, lc))
    except InvalidTag:
        raise OpenError("the operation could not be authenticated - wrong "
                        "passphrase, or it was altered on the way")

    try:
        op = json.loads(plain.decode("utf-8"))
    except Exception:
        raise OpenError("what came out is not an operation")
    if not isinstance(op, dict):
        raise OpenError("what came out is not an object")

    # The lc inside has to be the one the envelope showed the server, or the
    # two disagree about which operation this is - and the server's copy is
    # the one that drove its duplicate suppression.
    if op.get("lc") != lc:
        raise OpenError("the sealed operation disagrees with its envelope")
    return op


# ---------------------------------------------------------------------------
# The snapshot.
#
# The log only grows, so a machine joining late would replay everything ever
# done. A snapshot is the whole document at one sequence number, and it is the
# one thing that makes catching up bounded work.
#
# It is also, in one piece, everything every operation is sealed to keep back.
# So it is sealed too - and differently in two ways.
#
# It is compressed first. A document is repetitive JSON and shrinks a great
# deal; the ciphertext then has to travel as base64, which grows it by a
# third, and the server refuses anything over four megabytes. Compressing
# before encrypting is what keeps a real document inside that.
#
# And the sequence number is bound into it. The server hands back a snapshot
# along with the number it claims the snapshot covers, and the client has no
# other way to check that claim - it discards its queue and adopts what it is
# given. Baked into the ciphertext, a server that offers last month's document
# under this week's number is caught rather than believed.
# ---------------------------------------------------------------------------

# The key under which the sealed document sits. The server recognises this and
# nothing else about it: see tc_snapshot_validate in php-server/tc/lib/oplog.php.
DOCUMENT_ENVELOPE = "e2ee"

# Distinct from the operations' label, so that a sealed operation cannot be
# handed back as a document, or the other way about.
DOCUMENT_LABEL = "snapshot"


def _document_associated(context, seq):
    """What a sealed document is bound to. Length-prefixed, as above."""
    parts = (SUITE.encode("ascii"),
             str(ENVELOPE_VERSION).encode("ascii"),
             DOCUMENT_LABEL.encode("ascii"),
             str(context).encode("utf-8"),
             str(int(seq)).encode("ascii"))
    return b"".join(len(p).to_bytes(4, "big") + p for p in parts)


def seal_document(document, master, context, seq):
    """
    Wraps a whole document for the server to hold and not read.

    :param document: The document as the tracker holds it.
    :param seq: The sequence number it describes. Bound into the ciphertext,
                so the server cannot later claim it describes another.
    :return: The envelope to upload. An object, with valid JSON inside and
             out, because the server splices the stored bytes straight into
             its reply (php-server/tc/index.php:250).
    :rtype: dict
    """
    if not isinstance(document, dict):
        raise SealError("a document must be an object")

    import os
    packed = zlib.compress(_canonical(document), 9)
    nonce = os.urandom(NONCE_BYTES)
    cipher = AESGCM(_subkey(master, b"enc"))
    sealed = cipher.encrypt(nonce, packed, _document_associated(context, seq))
    return {
        DOCUMENT_ENVELOPE: {
            "v": ENVELOPE_VERSION,
            "k": key_id(master),
            "c": base64.b64encode(nonce + sealed).decode("ascii"),
        },
    }


def is_sealed_document(candidate):
    """Whether what came back from the server is a sealed document."""
    return (isinstance(candidate, dict)
            and isinstance(candidate.get(DOCUMENT_ENVELOPE), dict))


def sealed_document_key_id(candidate):
    """
    Which key a sealed document names, or None.

    Its own reader rather than sealed_key_id(): an operation carries the
    identifier under 'f' and a document under its own envelope key, and
    handing one to the other's reader returns None - which would read as "no
    key named" and is a different thing entirely from "not that shape".
    """
    if not is_sealed_document(candidate):
        return None
    named = candidate[DOCUMENT_ENVELOPE].get("k")
    return named if isinstance(named, str) else None


def open_document(envelope, master, context, seq):
    """
    Recovers a document from its envelope.

    :param seq: The sequence number the server says this covers. The seal was
                made over that number, so a mismatch fails here rather than
                being adopted - which is the only check there is on a claim
                the client otherwise has to take on trust.
    :raises OpenError: unreadable, altered, sealed with another key, or
            offered under a sequence number it was not made for.
    :rtype: dict
    """
    if not is_sealed_document(envelope):
        raise OpenError("not a sealed document")
    fields = envelope[DOCUMENT_ENVELOPE]

    if fields.get("v") != ENVELOPE_VERSION:
        raise OpenError("document envelope version %r is not one this build knows"
                        % (fields.get("v"),))

    raw = fields.get("c")
    if not isinstance(raw, str):
        raise OpenError("the envelope carries no ciphertext")
    try:
        blob = base64.b64decode(raw, validate=True)
    except Exception:
        raise OpenError("the ciphertext is not valid base64")
    if len(blob) <= NONCE_BYTES:
        raise OpenError("the ciphertext is too short to be one")

    cipher = AESGCM(_subkey(master, b"enc"))
    try:
        packed = cipher.decrypt(blob[:NONCE_BYTES], blob[NONCE_BYTES:],
                                _document_associated(context, seq))
    except InvalidTag:
        raise OpenError("the document could not be authenticated - wrong "
                        "passphrase, altered on the way, or offered under a "
                        "sequence number it was not sealed for")

    try:
        document = json.loads(zlib.decompress(packed).decode("utf-8"))
    except Exception:
        raise OpenError("what came out is not a document")
    if not isinstance(document, dict):
        raise OpenError("what came out is not an object")
    return document
