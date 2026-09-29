<?php
/**
 * Accounts, passwords and device tokens.
 *
 * The credential handed to a client looks like
 *
 *     tc1.<token_id>.<secret>
 *
 * The token_id is public and is literally the filename the token lives under,
 * so validating a token is one computed path rather than a scan of every
 * token on the system. Only sha256(secret) is stored, so the store holds
 * nothing that can be replayed as a credential.
 */

require_once __DIR__ . '/store.php';

const TC_BCRYPT_COST = 12;

// A token dies 90 days after it was issued no matter what, and 30 days after
// it was last used. The absolute limit is the only thing that ever terminates
// a compromise nobody noticed - a copied credential shows up as the device
// that is legitimately there already, so there is no new entry to spot.
const TC_TOKEN_TTL = 7776000;  // 90 days
const TC_IDLE_TTL  = 2592000;  // 30 days

// Password checks are deliberately expensive, which makes them a lever for
// anyone wanting to tie up the host. This is a single global allowance rather
// than a per-user or per-IP one: the probe showed REMOTE_ADDR is the real
// client address here, but an attacker picks that, and a counter per attacker
// -supplied key is a way to fill the filesystem with small files. One counter
// cannot be inflated and cannot lock out a specific account by name.
const TC_HASH_BUDGET_PER_MINUTE = 30;

// What that one counter costs: anybody who can reach ?a=login can spend it, and
// once it is spent the owner cannot sign in either - not to add a machine, and
// not to recover one whose token has expired or been revoked. (Synchronising is
// untouched throughout; push, pull, head and snapshot never hash a password.)
//
// So there is a reserve beside it, drawn on only when the allowance above is
// gone, and only by a device this account has signed in from before. That is
// the one thing an attacker flooding the endpoint does not have: a device id is
// 64 random bits, known to the device and the server and sent nowhere else, and
// the server only ever records one after a correct password. Recording it is
// also what the owner's machines already have - the entry outlives the token,
// through expiry and through signing out - so the reserve reaches exactly the
// two cases the lockout hurt.
//
// Still one global file, for the reason above: nothing about it is keyed on
// what the caller sends, so it cannot be made to create files. And small,
// because it is a lever too, only one that fewer hands can reach.
const TC_HASH_RESERVE_PER_MINUTE = 10;

// One file per account (issue #587). They used to be one JSON file,
// users.dat.php, decoded whole on every sign-in and rewritten whole on every
// account change. Measured, that costs little time at the numbers in question -
// a few milliseconds at five thousand accounts - but everything about it grows
// with the count: the memory each sign-in needs, the time the lock is held, and
// how much one unreadable file takes down with it. Registration (#552) makes the
// count somebody else's decision. So now a sign-in reads one small file, and an
// account change writes one.
//
// users.dat.php is still recognised, as what an installation from before this
// has, and converted the first time anything asks - see tc_accounts_migrate().
function tc_users_file($store)    { return $store . '/users.dat.php'; }
function tc_users_lock($store)    { return $store . '/users.lock'; }
function tc_accounts_dir($store)  { return $store . '/accounts'; }
function tc_tokens_dir($store)    { return $store . '/tokens'; }

/**
 * Where an account's record lives: a name computed from the username, the way
 * a token's file is computed from its id - one path, never a search.
 *
 * Hashed rather than used as it stands. The name at sign-in is whatever the
 * caller sent, of any length and made of any bytes, and a hash makes it a
 * filename without deciding which of those would be safe as one - the same
 * reason an account's directory is named after its uid. It also keeps "Frank"
 * and "frank" apart on a filesystem that would fold them into one.
 */
function tc_account_file($store, $username)
{
    return tc_accounts_dir($store) . '/' . hash('sha256', (string)$username) . '.dat.php';
}

/**
 * The operator passphrase as it was meant, out of a file an editor may have
 * decorated without showing anything.
 *
 * setup.enable is written by hand, usually in whatever editor is nearest, and
 * two of them leave bytes in it that nobody can see. A comparison against the
 * raw contents then refuses the right passphrase for ever and calls it
 * "Wrong passphrase.", which sends the operator looking for a typo that is
 * not there.
 *
 * Surrounding whitespace was already dealt with. A byte order mark was not:
 * it is not whitespace, so trim() leaves it, and it became the first
 * character of the passphrase. It also added three to the length, so a
 * passphrase too short to be allowed could slip past the minimum.
 *
 * A UTF-16 file is not repaired, only named. Every character in it would be
 * padded with a NUL byte, so nothing anyone types could ever match, and
 * quietly transcoding a credential file is a worse habit than saying what is
 * wrong with it.
 *
 * @param string $raw The file contents.
 * @return array{passphrase: string, note: string|null, error: string|null}
 *         'note' is worth telling the operator but does not stop anything;
 *         'error' means the file cannot be used as it stands.
 */
function tc_read_passphrase($raw)
{
    foreach (["\xFF\xFE" => 'UTF-16 (little-endian)',
              "\xFE\xFF" => 'UTF-16 (big-endian)'] as $bom => $encoding) {
        if (strncmp($raw, $bom, 2) === 0) {
            return ['passphrase' => '', 'note' => null,
                    'error' => 'setup.enable is saved as ' . $encoding . '. Save it as '
                             . 'plain text - UTF-8 without a byte order mark - and try '
                             . 'again.'];
        }
    }

    if (strncmp($raw, "\xEF\xBB\xBF", 3) === 0) {
        return ['passphrase' => trim(substr($raw, 3)), 'error' => null,
                'note' => 'setup.enable begins with a byte order mark, which your editor '
                        . 'added and does not show. It has been ignored here, but it '
                        . 'will come back the next time the file is saved that way.'];
    }
    return ['passphrase' => trim($raw), 'note' => null, 'error' => null];
}
function tc_user_dir($store, $uid) { return $store . '/users/' . $uid; }

/**
 * Looks up an account by name.
 *
 * @return array|null The record with its username attached, or null.
 */
function tc_user_find($store, $username)
{
    $username = (string)$username;
    if (!tc_accounts_migrate($store)) {
        // The old list is still there, so it is still the whole truth: nothing
        // adds or removes an account until it is gone. Signing in goes on
        // working while the conversion cannot finish - on a full disk, say.
        $data = tc_read_json(tc_users_file($store));
        if (!$data || empty($data['users']) || !isset($data['users'][$username])) {
            return null;
        }
        $user = $data['users'][$username];
        $user['username'] = $username;
        return $user;
    }

    $user = tc_read_json(tc_account_file($store, $username));
    // The name is kept inside and compared, so a file that is not what its
    // name says - copied, restored or edited by hand - finds nobody.
    if (!is_array($user) || ($user['username'] ?? null) !== $username) {
        return null;
    }
    return $user;
}

/**
 * Every account, by name, in order.
 *
 * For Show status, which is the one place that wants them all - an operator
 * asking, never a client.
 *
 * @return array<string, array>
 */
function tc_accounts_list($store)
{
    if (!tc_accounts_migrate($store)) {
        $data = tc_read_json(tc_users_file($store));
        return is_array($data['users'] ?? null) ? $data['users'] : [];
    }
    $accounts = [];
    foreach (glob(tc_accounts_dir($store) . '/*.dat.php') ?: [] as $path) {
        $record = tc_read_json($path);
        // Only what a sign-in would find under that name, for the reason
        // tc_user_find() compares it.
        if (is_array($record) && is_string($record['username'] ?? null)
                && tc_account_file($store, $record['username']) === $path) {
            $accounts[$record['username']] = $record;
        }
    }
    ksort($accounts, SORT_STRING);
    return $accounts;
}

/**
 * Creates an account, touching nothing that belongs to any other.
 *
 * That is the point of it. Adding an account used to read the whole list and
 * write it back - and when the read failed, it began a new list holding only
 * the new account, so every other one was gone.
 *
 * @param string $passHash As password_hash() made it; the cost is the caller's.
 * @return array ['uid' => string], or ['error' => 'exists'|'busy'|'unconverted'|'io']
 */
function tc_account_create($store, $username, $passHash)
{
    $username = (string)$username;
    if (!tc_accounts_migrate($store)) {
        // While the old list is there it is the truth, and an account written
        // beside it could take a name it already holds.
        return ['error' => 'unconverted'];
    }
    $lock = tc_lock(tc_users_lock($store));
    if (!$lock) {
        return ['error' => 'busy'];
    }
    try {
        $path = tc_account_file($store, $username);
        if (is_file($path)) {
            return ['error' => 'exists'];
        }
        $uid = bin2hex(random_bytes(16));
        $dir = tc_user_dir($store, $uid);
        // The account's own directory first and the record last. The record is
        // what makes the account exist, and one pointing at a directory that is
        // not there yet would answer its every sign-in with "busy".
        if (!tc_secure_mkdir($dir) || !tc_secure_mkdir($dir . '/seen')
                || !tc_write_json($dir . '/user.dat.php', ['disabled' => false, 'devices' => []])
                || !tc_secure_mkdir(tc_accounts_dir($store))
                || !tc_write_json($path, ['username' => $username, 'uid' => $uid,
                                          'pass' => $passHash, 'created' => date('c')])) {
            return ['error' => 'io'];
        }
        return ['uid' => $uid];
    } finally {
        tc_unlock($lock);
    }
}

/**
 * Converts users.dat.php, if there still is one, into one file per account.
 *
 * Runs on the first request that looks an account up after an update, because
 * there is nothing else to run it: no shell, no cron. From then on it costs a
 * sign-in one stat().
 *
 * The old file goes only once every account in it has its own, and it is never
 * removed when it could not be read - that would lose every account at once.
 * Until it is gone it stays the authority: each run writes every account again
 * rather than trusting what an interrupted run left, and nothing may add or
 * remove an account in the meantime.
 *
 * @return bool True when there is nothing left to convert.
 */
function tc_accounts_migrate($store)
{
    $legacy = tc_users_file($store);
    if (!is_file($legacy)) {
        return true;
    }
    $lock = tc_lock(tc_users_lock($store));
    if (!$lock) {
        return false;
    }
    try {
        if (!is_file($legacy)) {
            return true;   // finished by somebody else while this waited
        }
        $data = tc_read_json($legacy);
        if (!is_array($data) || !is_array($data['users'] ?? null)
                || !tc_secure_mkdir(tc_accounts_dir($store))) {
            return false;
        }
        foreach ($data['users'] as $name => $record) {
            // Anything else never was an account: nothing could sign in with it.
            if (is_array($record)
                    && !tc_write_json(tc_account_file($store, (string)$name),
                                      ['username' => (string)$name] + $record)) {
                return false;
            }
        }
        return @unlink($legacy);
    } finally {
        tc_unlock($lock);
    }
}

/**
 * Consumes one unit of the global password-checking allowance.
 *
 * @return bool False when the allowance for this minute is used up.
 */
function tc_hash_budget_take($store)
{
    return tc_budget_take($store, 'rate', TC_HASH_BUDGET_PER_MINUTE);
}

/**
 * Consumes one unit of the reserve, for a sign-in the allowance turned away.
 *
 * Only ever called for a device tc_login_from_known_device() recognised, and
 * only once tc_hash_budget_take() has said no - see the constant for why.
 *
 * @return bool False when the reserve for this minute is used up as well.
 */
function tc_hash_reserve_take($store)
{
    return tc_budget_take($store, 'rate-reserve', TC_HASH_RESERVE_PER_MINUTE);
}

/**
 * Whether a sign-in comes from a device this account has signed in from before.
 *
 * Decided before any password is hashed, and cheaply: two small reads, no
 * bcrypt. That ordering is the whole point - a check that cost as much as the
 * hash it guards would be no help against somebody spending hashes.
 *
 * It says nothing about whether the password is right. It only decides who
 * may queue for the reserve; the password is still checked in full after.
 *
 * A disabled account is not recognised, so switching one off still closes it
 * completely rather than leaving it a door the flood cannot reach.
 *
 * @param string $username  As the caller typed it.
 * @param string $deviceUid As the caller sent it. Anything not shaped like a
 *                          device id is simply not one of ours.
 * @return bool
 */
function tc_login_from_known_device($store, $username, $deviceUid)
{
    if (!is_string($deviceUid) || !preg_match('/^[a-f0-9]{16}$/', $deviceUid)) {
        return false;
    }
    $user = tc_user_find($store, (string)$username);
    if (!$user || empty($user['uid']) || !empty($user['disabled'])) {
        return false;
    }
    $record = tc_read_json(tc_user_dir($store, $user['uid']) . '/user.dat.php');
    if (!is_array($record) || !is_array($record['devices'] ?? null)) {
        return false;
    }
    foreach ($record['devices'] as $device) {
        if (is_array($device) && ($device['device_uid'] ?? null) === $deviceUid) {
            return true;
        }
    }
    return false;
}

/**
 * One per-minute allowance, kept in its own file.
 *
 * @param string $name  The file's stem. 'rate' is the name the allowance has
 *                      always had, and every existing installation has one.
 * @param int    $limit How many a minute.
 * @return bool False when the allowance for this minute is used up.
 */
function tc_budget_take($store, $name, $limit)
{
    $path = $store . '/' . $name . '.dat.php';
    $lock = tc_lock($store . '/' . $name . '.lock');
    if (!$lock) {
        // Refusing rather than waving it through: the budget exists to stop
        // this endpoint being used to burn the host's CPU, and an unenforced
        // budget is no budget.
        return false;
    }
    try {
        $now    = time();
        $window = intdiv($now, 60);
        $state  = tc_read_json($path);
        if (!is_array($state) || ($state['win'] ?? null) !== $window) {
            $state = ['win' => $window, 'n' => 0];
        }
        if ($state['n'] >= $limit) {
            return false;
        }
        $state['n']++;
        tc_write_json($path, $state);
        return true;
    } finally {
        tc_unlock($lock);
    }
}

/**
 * Issues a token for a device, replacing any token that device already holds.
 *
 * Replacing rather than appending is what makes a repeated login harmless: a
 * client whose response was lost retries, and gets one row rather than a
 * second live credential nothing will ever clean up.
 *
 * @return array{token: string, expires_at: int}|null
 */
function tc_token_issue($store, $uid, $deviceUid, $deviceName)
{
    $userDir = tc_user_dir($store, $uid);
    $lock    = tc_lock($userDir . '/user.lock');
    if (!$lock) {
        return null;
    }
    try {
        $user = tc_read_json($userDir . '/user.dat.php');
        if (!is_array($user)) {
            $user = ['devices' => []];
        }
        if (!isset($user['devices']) || !is_array($user['devices'])) {
            $user['devices'] = [];
        }

        // Drop the device's previous token file, if any.
        foreach ($user['devices'] as $existing) {
            if (($existing['device_uid'] ?? null) === $deviceUid
                && !empty($existing['token_id'])) {
                @unlink(tc_tokens_dir($store) . '/' . $existing['token_id'] . '.dat.php');
            }
        }
        $user['devices'] = array_values(array_filter(
            $user['devices'],
            function ($d) use ($deviceUid) { return ($d['device_uid'] ?? null) !== $deviceUid; }
        ));

        $tokenId = bin2hex(random_bytes(8));
        $secret  = rtrim(strtr(base64_encode(random_bytes(32)), '+/', '-_'), '=');
        $now     = time();
        $expires = $now + TC_TOKEN_TTL;

        $written = tc_write_json(
            tc_tokens_dir($store) . '/' . $tokenId . '.dat.php',
            [
                'uid'         => $uid,
                'device_uid'  => $deviceUid,
                'hash'        => hash('sha256', $secret),
                'iat'         => $now,
                'exp'         => $expires,
            ]
        );
        if (!$written) {
            return null;
        }

        $user['devices'][] = [
            'device_uid'  => $deviceUid,
            'device_name' => $deviceName,
            'token_id'    => $tokenId,
            'iat'         => $now,
            'exp'         => $expires,
        ];
        tc_write_json($userDir . '/user.dat.php', $user);
        tc_touch_seen($store, $uid, $deviceUid);

        return ['token' => 'tc1.' . $tokenId . '.' . $secret, 'expires_at' => $expires];
    } finally {
        tc_unlock($lock);
    }
}

/**
 * Records that a device was just seen.
 *
 * A zero-byte file whose mtime carries the whole meaning. Kept apart from the
 * token file on purpose: updating the token file on every request would race
 * with a revocation and could re-create a credential that had just been
 * withdrawn. A stray file here grants nothing.
 */
function tc_touch_seen($store, $uid, $deviceUid)
{
    $dir = tc_user_dir($store, $uid) . '/seen';
    if (!is_dir($dir) && !tc_secure_mkdir($dir)) {
        return;
    }
    $path = $dir . '/' . $deviceUid;
    if (!is_file($path)) {
        @file_put_contents($path, '');
        @chmod($path, 0600);
    } else {
        @touch($path);
    }
}

/**
 * Validates a presented credential.
 *
 * @return array|null ['uid'=>…, 'device_uid'=>…, 'exp'=>…] or null.
 */
function tc_token_check($store, $presented)
{
    if (!is_string($presented)) {
        return null;
    }
    $parts = explode('.', $presented);
    if (count($parts) !== 3 || $parts[0] !== 'tc1') {
        return null;
    }
    list(, $tokenId, $secret) = $parts;

    // The id becomes a filename, so nothing but hex may pass.
    if (!preg_match('/^[a-f0-9]{16}$/', $tokenId)) {
        return null;
    }

    $record = tc_read_json(tc_tokens_dir($store) . '/' . $tokenId . '.dat.php');
    if (!$record) {
        return null;
    }
    if (!hash_equals((string)($record['hash'] ?? ''), hash('sha256', $secret))) {
        return null;
    }

    $now = time();
    if ($now >= (int)($record['exp'] ?? 0)) {
        @unlink(tc_tokens_dir($store) . '/' . $tokenId . '.dat.php');
        return null;
    }

    // Idle expiry, from the sidecar's mtime.
    $seen = tc_user_dir($store, $record['uid']) . '/seen/' . $record['device_uid'];
    $last = @filemtime($seen);
    if ($last !== false && ($now - $last) > TC_IDLE_TTL) {
        @unlink(tc_tokens_dir($store) . '/' . $tokenId . '.dat.php');
        return null;
    }

    // An account can be switched off without hunting down its tokens.
    $user = tc_read_json(tc_user_dir($store, $record['uid']) . '/user.dat.php');
    if (is_array($user) && !empty($user['disabled'])) {
        return null;
    }

    tc_touch_seen($store, $record['uid'], $record['device_uid']);
    return [
        'uid'        => $record['uid'],
        'device_uid' => $record['device_uid'],
        'exp'        => (int)$record['exp'],
        'token_id'   => $tokenId,
    ];
}

function tc_token_revoke($store, $tokenId)
{
    if (!preg_match('/^[a-f0-9]{16}$/', $tokenId)) {
        return false;
    }
    return @unlink(tc_tokens_dir($store) . '/' . $tokenId . '.dat.php');
}
