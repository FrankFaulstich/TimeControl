"""
Talking to the sync server: where the credential lives, and signing in.

This module deliberately holds no synchronisation logic yet - only the
connection and the credential. Emitting operations and applying incoming ones
come later and will use the session established here.

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

import json
import os
import platform
import secrets

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

# Every call gets one. update.py established the idiom, and a sync that can
# hang indefinitely would freeze the interface it runs behind.
TIMEOUT = 20

# The ceiling on a whole call, DNS included. Set above the request's own
# worst case - `timeout` applies separately to connect and read - so it only
# ever fires for a lookup that is genuinely stuck.
DEADLINE = 2 * TIMEOUT + 5


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


def _post(base_url, action, payload=None, token=None, params=None):
    """
    Performs one request and converts every failure into a stable code.

    The caller has to be able to tell "wrong password" from "no network" -
    they call for entirely different responses from the user - so transport
    failures get their own codes rather than being folded into a generic
    error.

    :param payload: Sent as a JSON body via POST. None makes it a GET.
    :param params: Extra query parameters beside the action, for the
                   endpoints that read them from the query string.
    """
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['X-TC-Token'] = token
    url = _endpoint(base_url)
    query = {'a': action}
    query.update(params or {})

    def _send():
        if payload is None:
            return requests.get(url, params=query, headers=headers, timeout=TIMEOUT)
        # Encoded here rather than handed over as a str, and without ASCII
        # escaping. Two reasons, both about the byte count: requests encodes a
        # str body as latin-1, which German task names are not, and fit_batch
        # measures what it is about to send this same way. Escaping here and
        # measuring there would make every umlaut count for two bytes more
        # than the budget was told about - and the budget exists because the
        # server turns an over-long body into an empty one.
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        return requests.post(url, params=query, headers=headers,
                             data=body, timeout=TIMEOUT)

    try:
        # requests' own timeout does not cover the DNS lookup that runs
        # first, and with no route to the network that lookup can hang far
        # longer than any of these numbers. update.py hit exactly this and
        # solved it with a deadline around the whole call; a sync that hangs
        # would wedge the worker permanently, so it needs the same guard.
        if _call_with_deadline is not None:
            response = _call_with_deadline(_send, DEADLINE)
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
    if not _endpoint(base_url).lower().startswith('https://'):
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
        # Expired, revoked from another machine, or the account was switched
        # off. All three mean the same thing to the user: sign in again.
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


def _authenticated(action, payload=None, params=None):
    creds = load_credentials()
    if not creds:
        return {'ok': False, 'error': 'not_signed_in'}
    return _post(creds['base_url'], action, payload, token=creds['token'], params=params)


def head():
    """The cheap poll: how far the log has got, without transferring it."""
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
# cycle for the rest of the installation's life.
MAX_SNAPSHOT_BYTES = 4 * 1024 * 1024


def get_snapshot():
    """
    Fetches the document the server holds and the sequence number it covers.

    :return: On success 'seq', 'head' and 'document'. 'no_snapshot' when the
             account has none, which is the ordinary state of a young server.
    """
    return _authenticated('snapshot')


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
    return _authenticated('snapshot', document, params={'seq': int(seq)})
