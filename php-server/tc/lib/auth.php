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

function tc_users_file($store)   { return $store . '/users.dat.php'; }
function tc_users_lock($store)   { return $store . '/users.lock'; }
function tc_tokens_dir($store)   { return $store . '/tokens'; }
function tc_user_dir($store, $uid) { return $store . '/users/' . $uid; }

/**
 * Looks up an account by name.
 *
 * @return array|null The record with its username attached, or null.
 */
function tc_user_find($store, $username)
{
    $data = tc_read_json(tc_users_file($store));
    if (!$data || empty($data['users']) || !isset($data['users'][$username])) {
        return null;
    }
    $user = $data['users'][$username];
    $user['username'] = $username;
    return $user;
}

/**
 * Consumes one unit of the global password-checking allowance.
 *
 * @return bool False when the allowance for this minute is used up.
 */
function tc_hash_budget_take($store)
{
    $path = $store . '/rate.dat.php';
    $lock = tc_lock($store . '/rate.lock');
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
        if ($state['n'] >= TC_HASH_BUDGET_PER_MINUTE) {
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
