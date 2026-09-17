"""
The passphrase, the key it makes, and where that key is kept.

Between the cryptography in tt/sync_crypto.py, which knows nothing about this
application, and the sync path, which should not have to know how a key is
made. This module answers one question for the sync path - "am I sealing, and
with what?" - and offers the handful of actions the settings screen needs to
change the answer.

OFF BY DEFAULT, AND OFF MEANS OFF
---------------------------------
An account without an `e2ee` block, or with one that says otherwise, is not
encrypted and behaves exactly as this application always has. That is not a
formality: it is the state almost every installation is in, it is what every
existing test exercises, and nothing here may change it.

WHY THE KEY IS ON DISK AND THE PASSPHRASE IS NOT
------------------------------------------------
The passphrase is never stored. The key derived from it is, beside the sync
token in the per-user configuration directory.

That is forced by how this application is actually run. The synchronisation is
not driven by the interface alone: TimeTrackerMCP_Server, TimeTrackerREST_Server
and TimeTrackerSOAP_Server all call sync_engine.bring_up_to_date() themselves,
and none of them has anybody sitting in front of it to ask. A key that only
existed in the interface's memory would mean those three either stop
synchronising or write operations nobody can read.

What that costs is worth saying plainly rather than hiding: the passphrase
protects the data from the server, not from someone with this machine's files.
It never did protect them from that - data.json is plain JSON on this disk by
design, and the queue of unsent operations sits in this very directory - so
nothing is given up that was there before. Only one boundary moves, the one to
the server, and that is the boundary the whole feature is about.

WHY THE SALT LIVES IN config.json
---------------------------------
Because config.json is the file people copy to a second machine, and the salt
has to be the same on both or the same passphrase yields a different key. It
is not secret - its job is to stop one precomputed table from serving against
every account at once - so travelling in an ordinary settings file costs
nothing, and it saves inventing a way to carry it.

The parameters travel with it. Raising the work factor later would otherwise
strand every key derived before the change, with no way to tell why.
"""

import os

from tt import sync_client, sync_crypto

# What status() can report.
OFF = 'off'                     # not switched on; send and accept plain text
READY = 'ready'                 # switched on and this machine holds the key
LOCKED = 'locked'               # switched on, but the passphrase has not been
                                # entered on this machine yet
STALE = 'stale'                 # the stored key belongs to another salt or
                                # another account than the settings now name
NO_SALT = 'no_salt'             # switched on with nothing to derive from
NOT_SIGNED_IN = 'not_signed_in'  # the account names the ciphertext; without it
                                # nothing can be sealed

# All of them, named as a group so that the settings screen can be held to
# covering every one. A state nothing draws is a machine that has quietly
# stopped synchronising with nothing on screen to say why - and each of these
# is cleared by the user doing something, so there is always something to say.
STATES = (OFF, READY, LOCKED, STALE, NO_SALT, NOT_SIGNED_IN)

KEY_FILE_VERSION = 1


def _key_path():
    return os.path.join(sync_client.config_dir(), 'sync_key.json')


def settings(config):
    """
    The e2ee block of a configuration, or an empty one.

    Tolerant on purpose: a hand-edited config.json with rubbish in this place
    must read as "off" rather than stop the application.
    """
    if not isinstance(config, dict):
        return {}
    sync_cfg = config.get('sync')
    if not isinstance(sync_cfg, dict):
        return {}
    block = sync_cfg.get('e2ee')
    return block if isinstance(block, dict) else {}


def is_enabled(config):
    """Whether this account is meant to be encrypted. False unless it says so."""
    return settings(config).get('enabled') is True


def salt_of(config):
    """The account's salt as bytes, or None when it is missing or unreadable."""
    raw = settings(config).get('salt')
    if not isinstance(raw, str):
        return None
    try:
        salt = bytes.fromhex(raw)
    except ValueError:
        return None
    return salt if len(salt) == sync_crypto.SALT_BYTES else None


def params_of(config):
    """
    The derivation parameters recorded for this account.

    Falls back to the module's current defaults, which is right for an account
    set up before the parameters were written down - those were the defaults
    at the time.
    """
    block = settings(config)
    out = {}
    for name, default in (('n', sync_crypto.SCRYPT_N),
                          ('r', sync_crypto.SCRYPT_R),
                          ('p', sync_crypto.SCRYPT_P)):
        value = block.get(name)
        out[name] = value if isinstance(value, int) and value > 0 else default
    return out


def account_context():
    """
    What names this account inside the ciphertext, or None when unknown.

    The username the credential was issued for. Both machines sign in to the
    same account, so both compute the same value; it is bound into every
    sealed operation so that one cannot be moved to another account.

    None when this machine is not signed in - which is why encryption cannot
    be switched on before signing in, and why status() says so rather than
    sealing under some placeholder that the other machine would not share.
    """
    creds = sync_client.load_credentials()
    if not isinstance(creds, dict):
        return None
    name = creds.get('username')
    return name if isinstance(name, str) and name else None


def stored():
    """The key record this machine holds, or None."""
    record = sync_client._read_json(_key_path())
    return record if isinstance(record, dict) else None


def _remember(key, salt, params, account):
    """
    Writes the derived key as the current one, keeping every earlier one.

    Adding, never replacing. A passphrase can be changed, and the moment it
    is, the log still holds operations sealed with the key before it - this
    machine's own among them. A writer that overwrote would take those away
    exactly when they are needed, and the only way back would be to type the
    old passphrase again, which takes the new one away instead. There is no
    resting place in that, so nothing here removes a key; forget_key() is the
    one door out, and it is deliberate.

    The current key is also written at the top level under 'key' and 'key_id'.
    That is where a single-key file had it, so a build from before the ring
    existed still finds what it needs rather than declaring the file damaged.
    """
    held = stored() or {}
    earlier = [k for k in (held.get('previous') or []) if isinstance(k, dict)]

    was = held.get('key')
    if isinstance(was, str) and was and was != key.hex():
        earlier.insert(0, {'key': was, 'key_id': held.get('key_id')})

    fresh, seen = [], {key.hex()}
    for entry in earlier:
        raw = entry.get('key')
        if isinstance(raw, str) and raw and raw not in seen:
            seen.add(raw)
            fresh.append({'key': raw, 'key_id': entry.get('key_id')})

    sync_client._write_private(_key_path(), {
        'version': KEY_FILE_VERSION,
        'key': key.hex(),
        'key_id': sync_crypto.key_id(key),
        'previous': fresh,
        'salt': salt.hex(),
        'account': account,
        'n': params['n'], 'r': params['r'], 'p': params['p'],
    })


def forget_key():
    """
    Removes this machine's key.

    Separate from disable() and never done on its way, because it cannot be
    undone from here: operations already sealed with it stay on the server,
    and without the key or the passphrase there is no way back to them.
    """
    try:
        os.remove(_key_path())
    except OSError:
        pass


def status(config):
    """
    What the sync path needs to know before it sends or applies anything.

    :return: (state, key) - the key is bytes only when the state is READY,
             and None in every other case. A caller that seals on anything
             but READY is a bug.
    :rtype: tuple
    """
    if not is_enabled(config):
        return OFF, None

    salt = salt_of(config)
    if salt is None:
        return NO_SALT, None

    account = account_context()
    if account is None:
        return NOT_SIGNED_IN, None

    record = stored()
    if not record:
        return LOCKED, None

    # A key derived from a different salt, or for a different account, would
    # produce ciphertext the other machines cannot read - and would fail to
    # read theirs. Copying a config.json from elsewhere is the ordinary way
    # this happens, so it is reported rather than treated as corruption.
    if record.get('salt') != salt.hex() or record.get('account') != account:
        return STALE, None
    if record.get('version') != KEY_FILE_VERSION:
        return STALE, None

    try:
        key = bytes.fromhex(record.get('key') or '')
    except ValueError:
        return STALE, None
    if len(key) != sync_crypto.KEY_BYTES:
        return STALE, None
    return READY, key


def _with_e2ee(config, block):
    """A copy of the configuration carrying this e2ee block. Never the original."""
    updated = dict(config) if isinstance(config, dict) else {}
    sync_cfg = dict(updated.get('sync') or {})
    sync_cfg['e2ee'] = block
    updated['sync'] = sync_cfg
    return updated


def keyring(config):
    """
    Every key this machine can open something with, current one first.

    Sealing uses exactly one key - the current one, which status() returns.
    Opening is the other way about: the log holds whatever was sealed with
    whichever passphrase was in force at the time, and a machine that can only
    read the newest of them stops at its own older history.

    :return: A list of keys as bytes. Empty when the state is not READY -
             there is nothing to open with and nothing to say about it here.
    :rtype: list
    """
    state, current = status(config)
    if state != READY:
        return []

    ring = [current]
    seen = {current}
    for entry in (stored() or {}).get('previous') or []:
        try:
            key = bytes.fromhex((entry or {}).get('key') or '')
        except ValueError:
            continue
        if len(key) == sync_crypto.KEY_BYTES and key not in seen:
            seen.add(key)
            ring.append(key)
    return ring


def holds_key_named(config, named):
    """
    Whether this machine holds the key an envelope names.

    Only ever a hint, and treated as one. The identifier travels outside the
    ciphertext and nothing authenticates it, so a server could put any value
    there; what it can produce that way is a wrong answer to this question,
    never a decryption. It is used to tell the user which of two very
    different things went wrong - "sealed with a key you do not have" or
    "this is damaged" - after every key has actually been tried.
    """
    if not isinstance(named, str) or not named:
        return False
    return any(sync_crypto.key_id(key) == named for key in keyring(config))


def enable(config, passphrase=None):
    """
    Switches encryption on.

    Two different things, told apart by whether the settings already carry a
    salt - and it matters which, because getting it wrong destroys data.

    SETTING IT UP. No salt yet. One is made here and written into the
    configuration the caller then saves; every later machine derives from that
    same salt, which is why it travels with the settings rather than being
    made again. The passphrase is required.

    SWITCHING IT BACK ON. A salt is already there, from before it was switched
    off. It is kept exactly as it was, and the passphrase is neither needed
    nor accepted. Making a second salt here is the bug this shape exists to
    prevent: every operation already sealed under the first one would be
    orphaned on the server, unreadable by anybody, with nothing to say so -
    and disable() keeps the key precisely so that they stay readable. Asking
    for the passphrase again would be no better, because a mistyped one
    derives a perfectly valid second key and breaks it just as quietly.

    Starting over deliberately is still possible: switch off, remove the
    `e2ee` block from config.json, switch on again. That is out of reach by
    accident, which is the point.

    :return: (config, state). The configuration is a new dict - the caller
             saves it - and the state is READY, or the reason it is not. After
             switching back on it may be LOCKED, when this machine no longer
             holds the key; the caller then asks for the passphrase through
             unlock().
    :rtype: tuple
    """
    account = account_context()
    if account is None:
        return config, NOT_SIGNED_IN

    settled = salt_of(config)
    if settled is not None:
        block = dict(settings(config), enabled=True)
        updated = _with_e2ee(config, block)
        return updated, status(updated)[0]

    if not passphrase:
        raise sync_crypto.SealError("the passphrase is empty")

    salt = sync_crypto.new_salt()
    params = {'n': sync_crypto.SCRYPT_N, 'r': sync_crypto.SCRYPT_R,
              'p': sync_crypto.SCRYPT_P}
    key = sync_crypto.derive_key(passphrase, salt, **params)
    _remember(key, salt, params, account)
    return _with_e2ee(config, {'enabled': True, 'salt': salt.hex(), **params}), READY


def is_set_up(config):
    """
    Whether this account already has a salt, and so a passphrase in the world.

    What the settings screen asks to tell "switch it on for the first time"
    from "switch it back on": the first needs a passphrase and the second
    must not take one.
    """
    return salt_of(config) is not None


def unlock(config, passphrase):
    """
    Derives this machine's key from a passphrase for an account already set up.

    Used on the second machine, and after a STALE record. The salt comes from
    the configuration, so the same passphrase gives the same key as elsewhere
    - and a mistyped one gives a different key, which is what the fingerprint
    shown beside this is for.

    :return: The resulting state: READY, or the reason it is not.
    :rtype: str
    """
    if not is_enabled(config):
        return OFF
    salt = salt_of(config)
    if salt is None:
        return NO_SALT
    account = account_context()
    if account is None:
        return NOT_SIGNED_IN
    if not passphrase:
        raise sync_crypto.SealError("the passphrase is empty")

    params = params_of(config)
    key = sync_crypto.derive_key(passphrase, salt, **params)
    _remember(key, salt, params, account)
    return READY


def derive(config, passphrase):
    """
    The key a passphrase gives for this account, written down nowhere.

    Split from unlock() for the one job that must not leave a trace until it
    has succeeded: changing the passphrase. That has to seal a document with
    the new key and get the server to accept it before the key becomes this
    machine's current one - otherwise there is a window in which the log is
    sealed with a key nobody has been told about, and a machine lost in that
    window takes the account's history with it.

    :raises SealError: no passphrase, or this account has no usable salt.
    :rtype: bytes
    """
    salt = salt_of(config)
    if salt is None:
        raise sync_crypto.SealError("this account has no salt to derive from")
    if not passphrase:
        raise sync_crypto.SealError("the passphrase is empty")
    return sync_crypto.derive_key(passphrase, salt, **params_of(config))


def adopt(config, key):
    """
    Makes an already derived key the current one, keeping the earlier ones.

    The second half of a passphrase change, run only once the new key has
    proved itself by sealing something the server accepted.

    :return: The resulting state, which is READY unless something else is
             wrong with the account.
    :rtype: str
    """
    salt = salt_of(config)
    account = account_context()
    if salt is None:
        return NO_SALT
    if account is None:
        return NOT_SIGNED_IN
    _remember(key, salt, params_of(config), account)
    return status(config)[0]


def disable(config):
    """
    Switches encryption off for this account, keeping the key.

    The key stays because operations already sealed are still on the server,
    and reading them back needs it. Someone who wants it gone says so
    separately, through forget_key().

    :return: The configuration to save.
    :rtype: dict
    """
    block = settings(config)
    if not block:
        return dict(config) if isinstance(config, dict) else {}
    return _with_e2ee(config, dict(block, enabled=False))


def fingerprint_of(config):
    """
    The fingerprint of this machine's key, or None when it holds none.

    Shown in the settings so that two machines can be compared by eye: the
    same passphrase and salt give the same fingerprint, and a typing mistake
    gives a different one before anything has been synchronised.
    """
    state, key = status(config)
    return sync_crypto.fingerprint(key) if state == READY else None
