"""
Talking to the sync server: where the credential lives, signing in and
registering, and the requests the sync engine makes with the session that
leaves. What to send and what to do with the answers is not decided here;
that is tt/sync_engine.py and tt/sync_apply.py.

WHY THE CREDENTIAL IS NOT IN config.json
----------------------------------------
Two reasons, both specific to this project rather than general principle.

config.json is tracked in a public git repository, so a token placed there is
one routine `git add -A` away from being published permanently, in every
clone and fork.

More importantly, config.json is a file people copy. Setting up a second
machine by copying it across is the obvious thing to do, and it is even the
behaviour we want for the server address. But the token carries a device
identity, and the server keeps its duplicate-suppression counter per device
and replaces "this device's" token on every sign-in. Two machines sharing one
identity would revoke each other's tokens and swallow each other's retries.

So the split is: the server address, the on/off switch and the interval are
ordinary settings and stay in config.json - copying those to a second machine
is helpful. The token and the device identity live here instead, per machine,
outside the project directory.
"""

import hashlib
import json
import os
import platform
import secrets
import time
import unicodedata
from urllib.parse import urlsplit, urlunsplit

import requests

try:
    # The same helper update.py uses, for the same reason: requests' timeout
    # begins after the address has been resolved, so it does not bound the
    # DNS lookup - the hang that issue #539 was about. Guarded because tt/
    # modules are also imported by the servers, which may be started from a
    # directory where the launcher script is not importable.
    from update import _call_with_deadline
except ImportError:
    _call_with_deadline = None

# The answers that mean "go and ask somewhere else". Read from the status
# code rather than from requests' own is_redirect, which also wants a Location
# header and is a property of its response object: this check has to hold for
# anything that answers like a response, which is what the tests hand it.
# 304 is deliberately not here - it is a 3xx that redirects nothing, and
# nothing in this client sends a conditional request that could earn one.
REDIRECT_CODES = frozenset((301, 302, 303, 307, 308))

# Every call gets one. update.py established the idiom, and a sync that can
# hang indefinitely would freeze the interface it runs behind.
TIMEOUT = 20

# The ceiling on a whole call, DNS included. Set above the request's own
# worst case - `timeout` applies separately to connect and read - so it only
# ever fires for a lookup that is genuinely stuck.
DEADLINE = 2 * TIMEOUT + 5

# The server's own minimum (TC_PASSWORD_MIN in php-server/tc/lib/auth.php),
# checked here as well only to spare a round trip. The server's is the one that
# counts; this one merely has to be no stricter.
PASSWORD_MIN = 12

# A register whose answer never arrived may still have made the account - the
# server finishes its work whether or not anybody is left to hear about it. For
# these, signing in with the same name and password is how to find out.
UNANSWERED = frozenset(('timeout', 'unreachable', 'bad_response'))

# The proof of work a registration brings along (issue #591; the server side is
# php-server/tc/lib/pow.php). Whatever difficulty the server asks for is
# solved, so it can be raised without a new client - up to this, beyond which
# a search would take this machine hours rather than seconds.
POW_MAX_BITS = 28

# Left over of the time a challenge is valid, for the request that delivers
# the solution. Solving is given the rest.
POW_MARGIN = 30

# The server's answers after which a fresh challenge is the remedy: a solution
# that came too late or twice - a slow connection, or a request whose answer
# got lost - and one the server no longer takes, because its difficulty was
# raised or its key replaced while the challenge was on its way.
POW_RETRY = frozenset(('pow_expired', 'pow_used', 'pow_invalid'))

# Failures of the challenge request that the registration would only meet
# again. Nothing has been sent to register at that point, so there is nothing
# to find out by signing in, and waiting for two more timeouts helps nobody.
POW_UNREACHABLE = frozenset(('timeout', 'unreachable', 'tls_failed', 'address_redirects'))


def config_dir():
    """
    Returns the per-user directory holding this machine's sync credential.

    Resolved from the operating system, never relative to the working
    directory: a frozen build chdir's to the directory holding the .exe, and
    that is exactly where this must NOT end up. Under Programme/Program Files
    it would not be writable at all; anywhere else it would be shared by every
    Windows account on the machine, and a portable install on a USB stick
    would carry the token around with it.

    :return: Absolute path to the directory (not created by this call).
    :rtype: str
    """
    # An explicit override, honoured before anything else. Two uses: running
    # a second instance against a separate identity, and keeping a test run
    # away from the directory the installed application is using. That second
    # one is not hypothetical - this directory is per user, not per checkout,
    # so a test that reaches it is writing into somebody's live cursor and
    # credential.
    override = os.environ.get('TC_CONFIG_DIR')
    if override:
        return override

    if os.name == 'nt':
        base = os.environ.get('APPDATA') or os.path.expanduser('~')
    else:
        base = os.environ.get('XDG_CONFIG_HOME') or os.path.join(os.path.expanduser('~'), '.config')
    return os.path.join(base, 'TimeControl')


def _credentials_path():
    return os.path.join(config_dir(), 'sync_credentials.json')


def _device_path():
    return os.path.join(config_dir(), 'device.json')


def _write_private(path, data):
    """
    Writes JSON so that, as far as the platform allows, only its owner can
    read it.

    On POSIX the mode does the work. On Windows os.chmod only toggles the
    read-only attribute - it cannot restrict *who* may read - so there the
    protection comes from the location instead: %APPDATA% sits inside the
    user profile, which Windows already keeps other standard accounts out of.
    That is an assurance from the operating system rather than from us, which
    is part of why the token expires on its own after ninety days.
    """
    os.makedirs(config_dir(), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _read_json(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def device_identity():
    """
    Returns this machine's identity, creating it on first use.

    Kept apart from the credential on purpose. Signing out, or a token being
    rejected, deletes the credential - but the identity has to survive that,
    or every sign-in would look like a brand new machine to the server,
    accumulate a fresh device entry each time, and defeat the very
    idempotency that makes a repeated sign-in harmless.

    :return: {'device_uid': 16 hex chars, 'device_name': str}
    :rtype: dict
    """
    existing = _read_json(_device_path())
    if existing and existing.get('device_uid'):
        return existing
    identity = {
        'device_uid': secrets.token_hex(8),
        'device_name': (platform.node() or 'unnamed')[:60],
    }
    _write_private(_device_path(), identity)
    return identity


def load_credentials():
    """Returns the stored credential, or None when not signed in."""
    data = _read_json(_credentials_path())
    if data and data.get('token') and data.get('base_url'):
        return data
    return None


def clear_credentials():
    """Forgets the token. The device identity is deliberately kept."""
    try:
        os.remove(_credentials_path())
    except OSError:
        pass


def _endpoint(base_url):
    """
    Normalises whatever the user typed into the API entry point.

    People paste the address of the directory, with or without a trailing
    slash, and sometimes the entry point itself. All three should work rather
    than producing an unexplained 404.
    """
    url = (base_url or '').strip().rstrip('/')
    if url.endswith('index.php'):
        return url
    return url + '/index.php'


# Names that can only mean this machine. Matched exactly, so a host that
# merely begins with one of them - 127.0.0.1.example.com - is not loopback.
LOOPBACK_HOSTS = ('localhost', '127.0.0.1', '::1')


def is_loopback(base_url):
    """Whether the address reaches this machine and nothing beyond it."""
    return (urlsplit(_endpoint(base_url)).hostname or '').lower() in LOOPBACK_HOSTS


def _transport_is_safe(base_url):
    """
    Whether a password may be sent to this address.

    https, or a loopback address. The rule exists so a password is never put
    on a network in the clear; loopback is not a network, and it is the only
    way to run the client against the real php-server/tc code - PHP's own
    built-in server does no TLS - rather than only against a stand-in written
    from the same reading of the contract as the client itself.

    This does not open a way in from outside. The server keeps its own,
    separate refusal (index.php: tc_is_https), so a real deployment reached
    over plain http still turns the request away; getting past both takes the
    deliberate test router in php-server/test-endpoints.php.
    """
    endpoint = _endpoint(base_url).lower()
    if endpoint.startswith('https://'):
        return True
    return endpoint.startswith('http://') and is_loopback(base_url)


def _canonical(base_url):
    """
    An address reduced to a form two spellings of the same server share.

    Only used for comparing, never for calling. On top of what _endpoint()
    already settles, the scheme and the host are lowercased: those are
    case-insensitive by definition, so treating a retyped `Example.com` as a
    different server would be wrong. The path is left alone, because on most
    servers it is not.
    """
    parts = urlsplit(_endpoint(base_url))
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(),
                       parts.path, parts.query, parts.fragment))


def active_base_url():
    """
    The address requests actually go to, or None when not signed in.

    Not necessarily the one in config.json. The token was issued by a
    particular server and is stored beside the address it belongs to; the
    setting can be edited afterwards, and until the next sign-in the two say
    different things. The interface shows this one so that which is which is
    visible rather than guessed at.
    """
    creds = load_credentials()
    return creds.get('base_url') if creds else None


def address_changed(configured):
    """
    Whether a configured address is not the one the stored token belongs to.

    WHY THE TOKEN IS NOT SIMPLY SENT TO THE NEW ADDRESS
    ---------------------------------------------------
    It would make the setting appear to work, and it is the wrong thing to do.
    A token is a bearer credential: whoever holds it can read and write this
    account's data. It was issued by one particular server, and the address is
    something a person types - so following it blindly means handing that
    credential to whatever host a typo happens to name. The server would not
    accept it, but by then it has been sent.

    So the address stays fixed for the life of the token, and a change becomes
    something the user is told about and resolves by signing in again - which
    is the only step that can hand the new server a credential it issued
    itself.

    :param configured: The address from config.json.
    :return: False when not signed in, or when nothing is configured; there is
             nothing to disagree about in either case.
    """
    creds = load_credentials()
    if not creds or not (configured or '').strip():
        return False
    return _canonical(configured) != _canonical(creds['base_url'])


def _post(base_url, action, payload=None, token=None, params=None, deadline=None):
    """
    Performs one request and converts every failure into a stable code.

    The caller has to be able to tell "wrong password" from "no network" -
    they call for entirely different responses from the user - so transport
    failures get their own codes rather than being folded into a generic
    error.

    :param payload: Sent as a JSON body via POST. None makes it a GET.
    :param params: Extra query parameters beside the action, for the
                   endpoints that read them from the query string.
    :param deadline: Seconds for the whole call, DEADLINE when not given -
                     longer only for what is long to send or fetch.
    """
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['X-TC-Token'] = token
    url = _endpoint(base_url)
    query = {'a': action}
    query.update(params or {})

    # Not followed, and not by accident.
    #
    # requests follows a redirect on its own and strips the Authorization
    # header when the host changes - but it knows nothing about X-TC-Token,
    # which is this application's bearer credential and would be sent on to
    # wherever the redirect pointed, including plain http.
    #
    # Following only https targets would close that, but the better answer is
    # simpler: the address is a setting, entered once. If it redirects, the
    # setting is wrong, and correcting it beats paying for a redirect on every
    # request for ever. So the redirect is reported and the token stays here.
    def _send():
        if payload is None:
            return requests.get(url, params=query, headers=headers, timeout=TIMEOUT,
                                allow_redirects=False)
        # Encoded here rather than handed over as a str, and without ASCII
        # escaping. Two reasons, both about the byte count: requests encodes a
        # str body as latin-1, which German task names are not, and fit_batch
        # measures what it is about to send this same way. Escaping here and
        # measuring there would make every umlaut count for two bytes more
        # than the budget was told about - and the budget exists because the
        # server turns an over-long body into an empty one.
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        return requests.post(url, params=query, headers=headers,
                             data=body, timeout=TIMEOUT, allow_redirects=False)

    try:
        # requests' own timeout does not cover the DNS lookup that runs
        # first, and with no route to the network that lookup can hang far
        # longer than any of these numbers. update.py hit exactly this and
        # solved it with a deadline around the whole call; a sync that hangs
        # would wedge the worker permanently, so it needs the same guard.
        if _call_with_deadline is not None:
            response = _call_with_deadline(_send, deadline or DEADLINE)
        else:
            response = _send()
    except TimeoutError:
        return {'ok': False, 'error': 'timeout'}
    except requests.exceptions.SSLError:
        return {'ok': False, 'error': 'tls_failed'}
    except requests.exceptions.Timeout:
        return {'ok': False, 'error': 'timeout'}
    except requests.exceptions.RequestException:
        return {'ok': False, 'error': 'unreachable'}

    if getattr(response, 'status_code', 0) in REDIRECT_CODES:
        # Reported in its own right rather than as a malformed answer, because
        # the two need entirely different things from the user: one is a
        # server having a bad day, this is an address that wants correcting.
        return {'ok': False, 'error': 'address_redirects',
                'status': response.status_code}

    try:
        return response.json()
    except ValueError:
        # An HTML error page, or another application answering on this path.
        return {'ok': False, 'error': 'bad_response', 'status': response.status_code}


def login(base_url, username, password):
    """
    Signs in and stores the token for this machine.

    :return: The server's reply, with 'ok' telling the caller what happened.
    :rtype: dict
    """
    if not (base_url or '').strip():
        return {'ok': False, 'error': 'no_server'}
    if not username or not password:
        return {'ok': False, 'error': 'missing_credentials'}
    if not _transport_is_safe(base_url):
        # The server refuses plain HTTP anyway; failing here saves sending
        # the password in the clear to find that out.
        return {'ok': False, 'error': 'https_required'}

    identity = device_identity()
    result = _post(base_url, 'login', {
        'username': username,
        'password': password,
        'device_uid': identity['device_uid'],
        'device_name': identity['device_name'],
    })
    return _keep_token(base_url, username, result)


def _keep_token(base_url, username, result):
    """
    Stores the token a sign-in or a registration answered with.

    One function for both, because the rest of the application must not be
    able to tell them apart: an account made a moment ago is signed in to in
    exactly the way an old one is.
    """
    if result.get('ok'):
        if not result.get('token'):
            # A success without a token is not something this server does,
            # so the address is answering for something else. Saying so
            # beats a KeyError from deep inside the sign-in button.
            return {'ok': False, 'error': 'bad_response'}
        _write_private(_credentials_path(), {
            'version': 1,
            'base_url': _endpoint(base_url),
            'username': username,
            'token': result['token'],
            'expires_at': result.get('expires_at'),
        })
    return result


def normalise_invite_code(code):
    """
    An invitation code as it was typed or pasted, reduced to what was issued.

    The same rule as the server's tc_invite_normalise(): lowercased, with
    every space, punctuation mark and invisible formatting character removed -
    the hyphens the setup page groups it with, the quotes or full stop of the
    sentence it was copied from, and whatever a mail client or chat window
    slips in. Letters and digits are never removed, so "Code: 3f2a..." keeps
    "code" and is refused rather than read as some other code.

    :return: The code, or '' when nothing is left of it.
    """
    return ''.join(ch for ch in (code or '').lower()
                   if not (ch.isspace() or unicodedata.category(ch)[0] == 'P'
                           or unicodedata.category(ch) in ('Zs', 'Cf')))


def leading_zero_bits(digest):
    """How many zero bits a digest begins with - tc_pow_leading_zero_bits()."""
    bits = 0
    for byte in digest:
        if byte:
            return bits + 8 - byte.bit_length()
        bits += 8
    return bits


def solve_challenge(challenge, bits, seconds):
    """
    Finds a nonce for a proof-of-work challenge from the server.

    The server's rule: SHA-256 of the challenge, a colon and the nonce begins
    with `bits` zero bits. The nonce is a counter written in decimal, the
    plainest thing that fits the server's pattern for it.

    :param seconds: How long to search. A challenge expires; a solution found
                    after that is worth nothing.
    :return: The nonce as a string, or None when none was found in time.
    """
    prefix = hashlib.sha256((challenge + ':').encode('ascii'))
    whole, rest = divmod(bits, 8)
    zeros = bytes(whole)
    # The bits of the next byte that have to be zero as well.
    mask = (0xFF << (8 - rest)) & 0xFF
    deadline = time.monotonic() + seconds
    nonce = 0
    while True:
        # The clock is read every few thousand tries rather than every one:
        # at a million and more a second, reading it each time would cost
        # more than the hashing.
        for nonce in range(nonce, nonce + 4096):
            attempt = prefix.copy()
            attempt.update(str(nonce).encode('ascii'))
            digest = attempt.digest()
            if digest[:whole] == zeros and not (rest and digest[whole] & mask):
                return str(nonce)
        nonce += 1
        if time.monotonic() >= deadline:
            return None


def _proof_of_work(base_url, purpose):
    """
    Asks the server for a challenge and solves it.

    :return: (work, failure). work is the 'pow' a request carries, or None
             when there is nothing to send: a server from before issue #591
             does not know the request, and a challenge too hard to solve in
             time is left for the server to decide about - today it does not
             insist, and it would answer that it does if it ever did. failure
             is set only when the server could not be reached at all, from
             POW_UNREACHABLE.
    """
    answer = _post(base_url, 'challenge', {'purpose': purpose})
    if answer.get('error') in POW_UNREACHABLE:
        return None, answer['error']
    challenge = answer.get('challenge') if answer.get('ok') else None
    bits = answer.get('bits')
    expires_in = answer.get('expires_in')
    if (not isinstance(challenge, str) or not challenge.isascii()
            or type(bits) is not int or not 0 <= bits <= POW_MAX_BITS
            or type(expires_in) is not int):
        return None, None
    nonce = solve_challenge(challenge, bits, max(1, expires_in - POW_MARGIN))
    if nonce is None:
        return None, None
    return {'challenge': challenge, 'nonce': nonce}, None


def register(base_url, code, username, password):
    """
    Creates an account with an invitation code, and signs this machine in to it.

    The server answers the way a sign-in does, so the token is kept the same
    way (issue #588).

    A proof of work goes along with it (issue #591). With a code the server
    does not demand one - the code already stands in front of the password
    hash - but it checks one that is sent, so this is the path that keeps the
    mechanism in use until registering without a code needs it.

    :return: The server's reply, with 'ok' telling the caller what happened.
    :rtype: dict
    """
    if not (base_url or '').strip():
        return {'ok': False, 'error': 'no_server'}
    code = normalise_invite_code(code)
    if not code:
        return {'ok': False, 'error': 'missing_invite'}
    if not username or not password:
        return {'ok': False, 'error': 'missing_credentials'}
    if len(password) < PASSWORD_MIN:
        return {'ok': False, 'error': 'weak_password'}
    if not _transport_is_safe(base_url):
        return {'ok': False, 'error': 'https_required'}

    identity = device_identity()
    request = {
        'code': code,
        'username': username,
        'password': password,
        'device_uid': identity['device_uid'],
        'device_name': identity['device_name'],
    }
    for _ in range(2):
        work, unreachable = _proof_of_work(base_url, 'register')
        if unreachable:
            return {'ok': False, 'error': unreachable}
        if work is None:
            # Never the one refused a moment ago.
            request.pop('pow', None)
        else:
            request['pow'] = work
        result = _post(base_url, 'register', request)
        # Refused before the code was looked at, so the code is still good
        # for one more try with a fresh challenge. Only one: a client and
        # server that disagree about the rules would otherwise go on for ever.
        if result.get('error') not in POW_RETRY:
            break
    if result.get('error') in UNANSWERED:
        # Trying again would be told the code is used, if it was. Signing in
        # finds out which it was, and is the right thing to have done either
        # way: it succeeds exactly when the account is there.
        recovered = login(base_url, username, password)
        if recovered.get('ok'):
            return dict(recovered, recovered=True)
        return result
    return _keep_token(base_url, username, result)


def logout():
    """
    Revokes this machine's token, server-side where possible.

    The local credential is dropped either way: a user who asked to sign out
    should end up signed out even when the server cannot be reached, and the
    token expires on its own regardless.
    """
    creds = load_credentials()
    if not creds:
        return {'ok': True, 'revoked': False}
    result = _post(creds['base_url'], 'logout', token=creds['token'])
    clear_credentials()
    return result


def status():
    """
    Checks the stored credential against the server.

    :return: dict with 'state' as one of:
             'not_configured' - no credential stored
             'ok'             - the token works
             'rejected'       - the server does not accept it any more
             'unreachable'    - could not ask (network, TLS, wrong address)
    """
    creds = load_credentials()
    if not creds:
        return {'state': 'not_configured'}

    result = _post(creds['base_url'], 'ping', token=creds['token'])
    if result.get('ok'):
        return {
            'state': 'ok',
            'username': creds.get('username'),
            'base_url': creds.get('base_url'),
            'expires_at': result.get('expires_at') or creds.get('expires_at'),
            'device_uid': result.get('device_uid'),
        }
    if result.get('error') == 'invalid_token':
        # Expired, revoked, the account switched off - or deleted by the
        # operator, or removed as never used, and then signing in fails too
        # and only the operator can say why. The server tells none of these
        # apart, on purpose; to the user it means: sign in again.
        return {'state': 'rejected', 'username': creds.get('username')}
    return {'state': 'unreachable', 'error': result.get('error', 'unreachable')}


# ---------------------------------------------------------------------------
# The log itself. These three speak for the stored credential, so the caller
# never handles the token - and cannot accidentally send it somewhere else.
# ---------------------------------------------------------------------------

# The server's own ceiling (TC_PUSH_MAX_OPS / TC_PULL_MAX_OPS). Sending more
# has the whole batch rejected, so the caller must send it in pieces.
MAX_OPS_PER_CALL = 500

# And a ceiling in bytes, which is the one that actually bites. The server
# reads at most a megabyte of request body; anything longer arrives as a
# truncated fragment that will not parse, and it then reads as an empty
# request - accepted, acknowledged, and containing nothing. Five hundred
# operations carrying notes and task names go well past that, so counting
# operations alone is no protection at all.
#
# Half of the server's limit, because this counts the operations and the
# server counts everything: the envelope, and whatever the transfer adds.
MAX_BYTES_PER_CALL = 512 * 1024


def fit_batch(operations, max_ops=None, max_bytes=None):
    """
    Takes as many operations from the front as will actually arrive.

    :param operations: Wire-ready operations, in the order they must be sent.
    :return: The prefix that fits. Never empty when given anything: one
             operation too large to send on its own would otherwise sit at
             the head of the queue and block everything behind it for ever.
             Better to send it and be told than to stop silently.
    """
    max_ops = MAX_OPS_PER_CALL if max_ops is None else max_ops
    max_bytes = MAX_BYTES_PER_CALL if max_bytes is None else max_bytes

    batch, total = [], 0
    for op in operations[:max_ops]:
        size = len(json.dumps(op, ensure_ascii=False).encode('utf-8')) + 1
        if batch and total + size > max_bytes:
            break
        batch.append(op)
        total += size
    return batch


def _authenticated(action, payload=None, params=None, deadline=None):
    creds = load_credentials()
    if not creds:
        return {'ok': False, 'error': 'not_signed_in'}
    return _post(creds['base_url'], action, payload, token=creds['token'], params=params,
                 deadline=deadline)


def head():
    """
    The cheap poll: how far the log has got, without transferring it.

    Asked at the start of every cycle that has nothing of its own to send,
    which is nearly all of them - see sync_engine._nothing_to_do(). The point
    is not the size of the answer but what the server has to do to produce
    it: this reads one small file, where a push takes the log's exclusive
    lock and refuses rather than waits when another machine holds it.
    """
    return _authenticated('head')


def push(base_seq, ops):
    """
    Sends this machine's operations and reads back what it has not seen.

    One round trip, because submitting work and learning what happened
    elsewhere are the same conversation.

    :param base_seq: The last sequence number already applied here.
    :param ops: Queued operations. May be empty - that makes this a
                plain catch-up, which is how a machine with nothing to
                contribute stays up to date.
    :return: On success 'head', 'assigned' ([lc, seq] pairs), 'dups' (lc
             values the server had already recorded), 'ops' and 'more'.
             The reply never contains this machine's own operations.
    """
    return _authenticated('push', {'base_seq': int(base_seq), 'ops': list(ops)})


def pull(since, limit=MAX_OPS_PER_CALL):
    """
    Reads the log from a point, including this machine's own operations.

    That last part is the difference from push, and the reason this exists:
    after a lost response, or on a machine restored from a backup, the only
    way to learn where one's own operations sit in the order is to be told.

    A reply carrying 'needs_snapshot' means the log no longer reaches back
    this far: the caller has to take the snapshot first and resume from
    'snapshot_seq'. It arrives with no operations at all rather than with the
    part that survives, because that part starts in the middle - every object
    created before the snapshot point would be missing, and almost everything
    after it would then be dropped as referring to something unknown.
    """
    return _authenticated('pull', params={'since': int(since), 'limit': int(limit)})


# The server's own ceiling on a snapshot upload (TC_SNAPSHOT_MAX_BYTES). A
# document past this is refused, and nothing the client does will make it
# smaller - so it is caught here rather than rediscovered as a 413 on every
# cycle for the rest of the installation's life. It was 4 MiB until a real
# document of 5.3 MB was refused for a week and its account filled up behind
# it; the server's comment has the arithmetic for 16.
MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024

# A snapshot is the one thing this client moves that can take longer than
# DEADLINE allows a call: 16 MiB over a slow uplink is minutes, not seconds.
# Its call gets DEADLINE plus the time it would take at this rate - half a
# megabit a second, an upload slower than most - so that only a connection
# that has really stopped runs out of time.
SNAPSHOT_MIN_RATE = 64 * 1024   # bytes a second


def _snapshot_deadline(size):
    """The time a snapshot of `size` bytes is allowed, start to finish."""
    return DEADLINE + size / SNAPSHOT_MIN_RATE


def get_snapshot():
    """
    Fetches the document the server holds and the sequence number it covers.

    :return: On success 'seq', 'head' and 'document'. 'no_snapshot' when the
             account has none, which is the ordinary state of a young server.
    """
    # How big it is is not known before it arrives, so the largest one the
    # server would have accepted.
    return _authenticated('snapshot', deadline=_snapshot_deadline(MAX_SNAPSHOT_BYTES))


def put_snapshot(seq, document):
    """
    Offers a document as the snapshot for sequence number `seq`.

    The server takes it only from a machine that was at head, so this is
    worth attempting only straight after a cycle that reached it - and it
    answers 'not_at_head' rather than failing when the log has moved on in
    between, which is a reason to try again later, not a reason to stop.

    The document travels as the whole request body, with the sequence number
    in the query string, so the server can store the bytes exactly as they
    arrived instead of decoding and re-encoding a document it has no business
    understanding.
    """
    size = len(json.dumps(document, ensure_ascii=False).encode('utf-8'))
    if size > MAX_SNAPSHOT_BYTES:
        return {'ok': False, 'error': 'snapshot_too_large', 'bytes': size}
    return _authenticated('snapshot', document, params={'seq': int(seq)},
                          deadline=_snapshot_deadline(size))
