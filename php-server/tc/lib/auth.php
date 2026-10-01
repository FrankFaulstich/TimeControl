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

// What an account may be called and what its password must be, in one place:
// setup.php and ?a=register both create accounts, and two copies of a rule are
// how one of them ends up weaker.
//
// Anchored with D, and not only as a nicety: without it '$' also matches
// before a final newline, so "frank\n" would pass - a second account that
// looks exactly like frank in Show status, and that setup.php, which trims
// what it is given, could never name to delete.
const TC_USERNAME_PATTERN   = '/^[A-Za-z0-9._-]{3,32}$/D';
const TC_DEVICE_UID_PATTERN = '/^[a-f0-9]{16}$/D';
const TC_PASSWORD_MIN       = 12;

// How long an invitation stays redeemable. Long enough to reach somebody who
// is away for a week; short enough that one forgotten in an old message has
// stopped working by the time anybody finds it.
const TC_INVITE_TTL = 604800;  // 7 days

// When an account made from an invitation, that nothing was ever stored in,
// is removed again (issue #589). Derived from the idle limit rather than
// chosen, and never to be set below it: by then every token the account ever
// had has failed that check, so no device of it could come back without its
// password. Removing it earlier would end a sign-in that was still working -
// somebody away for a fortnight, back to a laptop that thinks it is synced.
const TC_UNUSED_SECONDS = TC_IDLE_TTL + 86400;  // 31 days

// How much one sweep may do. It runs inside somebody's registration, which
// should not wait on a clear-out of everything that has piled up.
const TC_SWEEP_EXAMINE = 20;
const TC_SWEEP_REMOVE  = 5;
const TC_REMOVED_KEEP  = 50;   // removals Show status can still name

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
function tc_invites_dir($store)   { return $store . '/invites'; }
function tc_unused_dir($store)    { return $store . '/unused'; }
function tc_removed_file($store)  { return $store . '/removed.dat.php'; }

/**
 * Where it is noted that an account came from an invitation and has not been
 * used yet (issue #589). Keyed by uid, not by name: a name can be taken again
 * by somebody else, a uid never is.
 */
function tc_unused_marker($store, $uid) { return tc_unused_dir($store) . '/' . $uid . '.dat.php'; }

/**
 * Whether an account still exists underneath its tokens. A token file can
 * outlive its account - a request in flight while it is removed, a token its
 * device list never recorded - and nothing may be let in, or written, on the
 * strength of one.
 */
function tc_account_present($store, $uid)
{
    return is_string($uid) && preg_match('/^[A-Za-z0-9]{1,64}$/D', $uid) === 1
        && is_file(tc_user_dir($store, $uid) . '/user.dat.php');
}

function tc_username_acceptable($username)
{
    return is_string($username) && preg_match(TC_USERNAME_PATTERN, $username) === 1;
}

/**
 * Long enough, and something bcrypt can take: password_hash() throws on a NUL
 * byte rather than returning false, so one would end the request halfway.
 *
 * Counted in characters, the unit every message and the client use, not in
 * bytes: six umlauts are six characters, not twelve.
 */
function tc_password_acceptable($password)
{
    return is_string($password) && mb_strlen($password, 'UTF-8') >= TC_PASSWORD_MIN
        && strpos($password, "\0") === false;
}

/**
 * A short label somebody else typed - a device name, an invitation's note -
 * made fit to keep: control characters out, at most 60 characters. Each is
 * only ever shown back to whoever it describes, or to the operator, and always
 * escaped where it is shown, so nothing further is done to it.
 */
function tc_label_clean($name)
{
    $clean = preg_replace('/[^\P{C}]+/u', '', (string)$name);
    return mb_substr($clean === null ? '' : $clean, 0, 60);
}

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
        if (is_file(tc_account_file($store, $username))) {
            return ['error' => 'exists'];
        }
        return tc_account_write($store, $username, $passHash);
    } finally {
        tc_unlock($lock);
    }
}

/**
 * The part of creating an account that writes it: for callers already holding
 * the users lock, which tc_account_create() and tc_invite_redeem() both are.
 * A second tc_lock() of the same file from the same request would not see its
 * own lock as its own - it would wait out the five seconds and give up.
 *
 * That the name is still free is the caller's to have checked, under that same
 * lock. Nothing here makes it exclusive on its own: a rename replaces whatever
 * it lands on, and a primitive that does not is one the host probe never
 * measured.
 *
 * @param array $extra     Further fields for the record, such as where it came from.
 * @param bool  $unwatched Whether it may be removed again if it is never used
 *                         (issue #589) - true for an account somebody made by
 *                         redeeming a code, never for one the operator made.
 * @return array ['uid' => string], or ['error' => 'io']
 */
function tc_account_write($store, $username, $passHash, array $extra = [], $unwatched = false)
{
    $uid = bin2hex(random_bytes(16));
    $dir = tc_user_dir($store, $uid);
    // The account's own directory first and the record last. The record is
    // what makes the account exist, and one pointing at a directory that is
    // not there yet would answer its every sign-in with "busy". The marker
    // goes before the record as well, so a record never lacks one it should
    // have had: an account that silently lost it would never be looked at.
    if (!tc_secure_mkdir($dir) || !tc_secure_mkdir($dir . '/seen')
            || !tc_write_json($dir . '/user.dat.php', ['disabled' => false, 'devices' => []])
            || ($unwatched && !tc_unused_mark($store, $uid, (string)$username))
            || !tc_secure_mkdir(tc_accounts_dir($store))
            || !tc_write_json(tc_account_file($store, $username),
                              ['username' => (string)$username, 'uid' => $uid,
                               'pass' => $passHash, 'created' => date('c')] + $extra)) {
        return ['error' => 'io'];
    }
    return ['uid' => $uid];
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

// ---------------------------------------------------------------------------
// Invitations (issue #588).
//
// How somebody other than the operator gets an account: setup.php makes a
// code, the operator hands it over, and ?a=register exchanges it, once, for an
// account whose password the operator never sees. Issuing the code is the
// approval. It happens while the operator is there, which is the only time
// this server has one.
//
// Only sha256(code) is stored, as with tokens, so the store holds nothing that
// could be redeemed. A code is 64 random bits: there is nothing to guess, so a
// wrong one needs no counter - it costs a stat() and gets nowhere near bcrypt.
// ---------------------------------------------------------------------------

function tc_invite_file($store, $code)
{
    return tc_invites_dir($store) . '/' . hash('sha256', $code) . '.dat.php';
}

/**
 * Where a code is while it is being redeemed. Still a .php file, so the guard
 * line inside it keeps working; not a .dat.php one, so nothing that looks for
 * open invitations can mistake it for one.
 */
function tc_invite_claim_file($store, $code)
{
    return tc_invites_dir($store) . '/' . hash('sha256', $code) . '.claimed.php';
}

/**
 * A code as somebody typed or pasted it, reduced to what was issued.
 *
 * Lowercased, and every space, punctuation mark and invisible formatting
 * character taken out: the hyphens setup.php groups it with, the quotes or the
 * full stop of the sentence it was copied from, and whatever a mail client or
 * a chat window adds on the way - a no-break space, an en dash, a zero-width
 * joiner. Letters and digits are never removed, so what is left is either the
 * code or not one: "Code: 3f2a..." keeps "code" and is refused, rather than
 * being read as some other code.
 *
 * The client does the same, but this is the one that counts.
 *
 * @return string|null Sixteen hex characters, or null when it is not a code.
 */
function tc_invite_normalise($code)
{
    if (!is_string($code)) {
        return null;
    }
    $clean = preg_replace('/[\s\p{Z}\p{P}\p{Cf}]+/u', '', strtolower($code));
    return (is_string($clean) && preg_match('/^[a-f0-9]{16}$/D', $clean)) ? $clean : null;
}

/**
 * Makes an invitation.
 *
 * @param string $note Who it is for, as the operator put it. Only ever shown
 *                     to the operator, in setup.php.
 * @return array|null ['code', 'created', 'expires', 'note'], or null when it
 *                    could not be written - and then there is no code to show.
 */
function tc_invite_create($store, $note = '')
{
    // Here rather than only at install: every store from before this version
    // has no such directory.
    if (!tc_secure_mkdir(tc_invites_dir($store))) {
        return null;
    }
    $code   = bin2hex(random_bytes(8));
    $now    = time();
    $record = ['created' => $now, 'expires' => $now + TC_INVITE_TTL,
               'note' => tc_label_clean($note)];
    if (!tc_write_json(tc_invite_file($store, $code), $record)) {
        return null;
    }
    return ['code' => $code] + $record;
}

/**
 * The invitation a code stands for, if it may still be redeemed.
 *
 * No lock and no hash, on purpose: this is the gate, and it has to be cheaper
 * than anything behind it. An expired one is removed on the way past.
 *
 * @return array|null
 */
function tc_invite_find($store, $code)
{
    $code = tc_invite_normalise($code);
    if ($code === null) {
        return null;
    }
    $path   = tc_invite_file($store, $code);
    $invite = tc_read_json($path);
    if (!is_array($invite)) {
        return null;
    }
    if (time() >= (int)($invite['expires'] ?? 0)) {
        @unlink($path);
        return null;
    }
    return $invite;
}

/**
 * Exchanges an invitation for an account.
 *
 * In this order, and the order is the design:
 *
 *  1. The code, before anything else - before the lock, and above all before
 *     the password is hashed. bcrypt at cost 12 is the one expensive thing
 *     here, and a check that came after it would stop nobody making the
 *     server do it. So nobody without a code learns anything, either - not
 *     even which names are taken.
 *  2. The name and the password. Neither uses the code up: a taken name is
 *     worth trying again with another.
 *  3. Under the users lock: the code once more, since somebody may have
 *     redeemed it meanwhile; the name; the hash; the account. Hashing under
 *     the lock is what holds a code to one hash - requests racing with it
 *     queue behind the first and find the code gone. Where the lock does
 *     nothing, each request already racing can hash once before step 4
 *     decides: one burst per code, never a way to keep the server busy.
 *  4. The code is claimed by renaming it, after the hash and before the
 *     account is written. A rename succeeds once whether or not the lock does
 *     anything on this host, so no code makes two accounts. If the account
 *     cannot be written the claim is renamed back and the code still works:
 *     a full disk should not cost somebody their invitation. The hash comes
 *     first because it is the slow part, and a request stopped halfway
 *     through it should leave the code where it was.
 *
 * @return array ['uid' => string], or ['error' => 'invalid_invite'|'bad_username'
 *               |'weak_password'|'username_taken'|'unconverted'|'busy'|'io'
 *               |'invite_lost']
 */
function tc_invite_redeem($store, $code, $username, $password)
{
    if (tc_invite_find($store, $code) === null) {
        return ['error' => 'invalid_invite'];
    }
    if (!tc_username_acceptable($username)) {
        return ['error' => 'bad_username'];
    }
    if (!tc_password_acceptable($password)) {
        return ['error' => 'weak_password'];
    }
    // While an old account list is still in place it is the truth, and a name
    // checked beside it could be one it holds (see tc_account_create). Finding
    // that out spends neither the code nor a hash. It is not 'busy': the one
    // time the list is converted, nothing else is waiting for the lock, so a
    // list still there is one that cannot be read or cannot be written out -
    // and that waits for the operator, not for a second try.
    if (!tc_accounts_migrate($store)) {
        return ['error' => 'unconverted'];
    }
    $lock = tc_lock(tc_users_lock($store));
    if (!$lock) {
        return ['error' => 'busy'];
    }
    try {
        $code   = tc_invite_normalise($code);
        $invite = tc_invite_find($store, $code);
        if ($invite === null) {
            return ['error' => 'invalid_invite'];
        }
        if (is_file(tc_account_file($store, $username))) {
            return ['error' => 'username_taken'];
        }
        $hash = password_hash($password, PASSWORD_BCRYPT, ['cost' => TC_BCRYPT_COST]);
        if (!is_string($hash)) {
            return ['error' => 'io'];
        }

        $open  = tc_invite_file($store, $code);
        $claim = tc_invite_claim_file($store, $code);
        // Touched before the rename, which keeps the mtime it finds: a claim
        // is then young from the moment it exists, and Show status, which
        // tidies away old ones, cannot take it for one left by a request that
        // died - not even in between.
        @touch($open);
        if (!@rename($open, $claim)) {
            clearstatcache();
            // Somebody else's rename won, and the code is theirs. Unless it is
            // still there: then the rename failed for some other reason, the
            // code is as good as it was, and saying it is used would be false.
            return ['error' => is_file($open) ? 'io' : 'invalid_invite'];
        }

        // The name once more, now that the hash is done. Where the lock works
        // this cannot have changed; where it does nothing, this is what keeps
        // the gap between asking and writing as small as setup.php's - the
        // hash no longer sits in it. That is narrower, not closed: that a name
        // stays free until it is written rests on the lock, as it does there.
        if (is_file(tc_account_file($store, $username))) {
            $made = ['error' => 'username_taken'];
        } else {
            try {
                // What it was for goes with the account: the one thing that
                // lets the operator tell, later, whose a name they did not
                // choose is.
                $made = tc_account_write($store, $username, $hash, ['invited' => [
                    'note'   => (string)($invite['note'] ?? ''),
                    'issued' => (int)($invite['created'] ?? 0),
                ]], true);
            } catch (Throwable $e) {
                error_log('tc_invite_redeem: ' . $e->getMessage());
                $made = ['error' => 'io'];
            }
        }
        if (isset($made['uid'])) {
            @unlink($claim);
        } elseif (!@rename($claim, $open)) {
            // Next to never - a rename back within one directory - but then
            // the code is gone, and the answer must not say otherwise.
            error_log('tc_invite_redeem: a claimed code could not be put back');
            $made = ['error' => 'invite_lost'];
        }
        return $made;
    } finally {
        tc_unlock($lock);
    }
}

/**
 * The invitations that are still open, soonest to expire first.
 *
 * Tidies on the way, for Show status is the one place that looks at them all:
 * expired ones go, and so does a claim old enough that the request holding it
 * cannot still be running - its code is spent either way.
 *
 * @return array[] Each ['created', 'expires', 'note'].
 */
function tc_invites_list($store)
{
    $now  = time();
    $open = [];
    foreach (glob(tc_invites_dir($store) . '/*.dat.php') ?: [] as $path) {
        $invite = tc_read_json($path);
        if (!is_array($invite)) {
            continue;   // unreadable is not the same as expired
        }
        if ($now >= (int)($invite['expires'] ?? 0)) {
            @unlink($path);
            continue;
        }
        $open[] = $invite;
    }
    foreach (glob(tc_invites_dir($store) . '/*.claimed.php') ?: [] as $path) {
        if ($now - (int)@filemtime($path) > 600) {
            @unlink($path);
        }
    }
    usort($open, function ($a, $b) {
        return (int)($a['expires'] ?? 0) <=> (int)($b['expires'] ?? 0);
    });
    return $open;
}

/**
 * Withdraws every invitation nobody has redeemed yet.
 *
 * Claims included: a redemption in progress whose account then fails to be
 * written puts its claim back by renaming it, and a claim that is no longer
 * there cannot be put back - so a withdrawn code stays withdrawn.
 *
 * @return int How many open invitations there were.
 */
function tc_invites_withdraw($store)
{
    // Under the lock, so it cannot fall between a redemption's failed write
    // and the rename that puts its code back - between the two globs below,
    // that code would be in neither. Without the lock, withdrawing still
    // happens; it only loses that guarantee, as everything else here does.
    $lock = tc_lock(tc_users_lock($store));
    try {
        return tc_invites_withdraw_now($store);
    } finally {
        tc_unlock($lock);
    }
}

function tc_invites_withdraw_now($store)
{
    $withdrawn = 0;
    foreach (glob(tc_invites_dir($store) . '/*.dat.php') ?: [] as $path) {
        if (@unlink($path)) {
            $withdrawn++;
        }
    }
    foreach (glob(tc_invites_dir($store) . '/*.claimed.php') ?: [] as $path) {
        @unlink($path);
    }
    return $withdrawn;
}

// ---------------------------------------------------------------------------
// Accounts nobody ever used (issue #589).
//
// An account made from an invitation that nothing was ever stored in, and
// that no device can still reach without its password, is removed again. Two
// cases must never be confused, and this only ever touches the first:
//
//  - never used: its log holds nothing at all. Removing it loses nothing;
//    whatever its devices hold locally stays there, and is offered again to
//    whichever account they sign in to next.
//  - used and then abandoned: there is a log, perhaps years of it. That is
//    reported to the operator, never deleted by anything here.
//
// Accounts the operator made in setup.php are never candidates: the operator
// made those on purpose, perhaps for somebody who starts next month, and sees
// and removes them in Show status. Only a redemption leaves the marker this
// looks for, so an account made from an invitation before this version is
// not a candidate either.
//
// There is no cron, so this runs where token expiry does: lazily, bounded, on
// a request that was going to touch accounts anyway - a registration, the
// path whose frequency grows with the problem.
// ---------------------------------------------------------------------------

/**
 * Notes that a new account came from an invitation and has not been used.
 *
 * Its mtime is the moment it could first be due, so a sweep can take the
 * oldest first and stop at the first one still in the future.
 */
function tc_unused_mark($store, $uid, $username)
{
    $now  = time();
    $path = tc_unused_marker($store, $uid);
    $json = json_encode(['username' => $username, 'uid' => $uid, 'since' => $now],
                        JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
    if ($json === false || !tc_secure_mkdir(tc_unused_dir($store))) {
        return false;
    }
    // Dated before it can be seen, not after: a rename keeps the mtime, so
    // from the moment it exists it says "not before then". Dated afterwards,
    // there would be an instant in which a sweep - one the lock failed to keep
    // out - found a due marker with no record yet, and took a registration
    // still being written for a removal that had been interrupted.
    $tmp = tc_unused_dir($store) . '/.tmp' . bin2hex(random_bytes(6));
    if (@file_put_contents($tmp, TC_GUARD . $json . "\n") === false) {
        return false;
    }
    @chmod($tmp, 0600);
    @touch($tmp, $now + TC_UNUSED_SECONDS + 1);
    if (!@rename($tmp, $path)) {
        @unlink($tmp);
        return false;
    }
    return true;
}

/**
 * When a device last reached an account, if nothing was ever stored in it.
 *
 * Every check fails closed. A directory that cannot be listed, a date that
 * cannot be read, a device list that cannot be decoded: each means "cannot
 * tell", and an account nobody can tell about is kept.
 *
 * A device listed without a seen/ entry counts as reachable until its token
 * expires outright, because that is how long tc_token_check lets it in.
 *
 * @param array $marker As tc_unused_mark() wrote it.
 * @return int|false|null The last contact; false when something is stored,
 *                        so the account is in use; null when it cannot be told.
 */
function tc_account_unused_since($store, $uid, array $marker)
{
    $dir = tc_user_dir($store, $uid);
    $log = $dir . '/log';
    if (file_exists($log)) {
        $entries = @scandir($log);
        if ($entries === false) {
            return null;
        }
        // Anything at all - a segment, a state file, the leftovers of a write
        // that was interrupted. An empty directory is what a refused snapshot
        // leaves, and holds nothing.
        if (array_diff($entries, ['.', '..']) !== []) {
            return false;
        }
    }

    $last = (int)($marker['since'] ?? 0);
    if ($last <= 0) {
        return null;
    }
    $seen = $dir . '/seen';
    if (file_exists($seen)) {
        $entries = @scandir($seen);
        if ($entries === false) {
            return null;
        }
        foreach (array_diff($entries, ['.', '..']) as $entry) {
            $at = @filemtime($seen . '/' . $entry);
            if ($at === false) {
                return null;
            }
            $last = max($last, $at);
        }
    }
    $user = tc_read_json($dir . '/user.dat.php');
    if (!is_array($user) || !is_array($user['devices'] ?? null)) {
        return null;
    }
    foreach ($user['devices'] as $device) {
        if (!is_array($device) || !is_string($device['device_uid'] ?? null)) {
            return null;
        }
        if (!is_file($seen . '/' . $device['device_uid'])) {
            // Idle expiry cannot apply without a seen/ entry, so this token
            // lasts until it expires; count it as reached until then.
            $last = max($last, (int)($device['exp'] ?? PHP_INT_MAX) - TC_IDLE_TTL);
        }
    }
    return $last;
}

function tc_account_trash($store, $uid) { return $store . '/users/.gone-' . $uid; }

/**
 * Removes an account and everything it holds. For callers holding the users
 * lock: setup.php, on the operator's word, and the sweep below.
 *
 * Every step may be the one an interrupted earlier try left undone, so each
 * is safe to repeat and a missing piece counts as done. The account's
 * directory is not deleted in place but renamed out of the way first: one
 * atomic step, which works even while a file inside it is open - the sweep
 * holds its log lock - and even on NFS, where deleting an open file leaves a
 * stand-in behind that no rmdir gets past. Emptying it follows, as tidying.
 *
 * @return bool False when the directory could not be moved; nothing that
 *              matters has been lost then, and it can simply be tried again.
 */
function tc_account_delete($store, $username, $uid)
{
    $dir = tc_user_dir($store, $uid);
    if (is_dir($dir)) {
        // Tokens live in a shared directory keyed by token id, so they have
        // to go individually. A token missed here still gets nowhere: once
        // the directory is gone, tc_token_check refuses it.
        $user = tc_read_json($dir . '/user.dat.php');
        foreach ((array)($user['devices'] ?? []) as $device) {
            if (!empty($device['token_id'])) {
                tc_token_revoke($store, (string)$device['token_id']);
            }
        }
        $trash = tc_account_trash($store, $uid);
        if (is_dir($trash)) {
            tc_remove_tree($trash, $store);
        }
        if (!@rename($dir, $trash)) {
            return false;
        }
    }
    // Only if the name is still this account's: it can have been given to
    // somebody else since, and then it is theirs.
    $recordPath = tc_account_file($store, $username);
    $record     = tc_read_json($recordPath);
    if (is_array($record) && ($record['uid'] ?? null) === $uid && !@unlink($recordPath)) {
        // The marker stays, so the next sweep can still finish this.
        return false;
    }
    @unlink(tc_unused_marker($store, $uid));
    return true;
}

/**
 * Empties a few directories that removals left out of the way. Bounded, like
 * the sweep, and best effort: one that will not go now goes next time.
 */
function tc_trash_tidy($store, $max = 3)
{
    foreach (array_slice(glob($store . '/users/.gone-*', GLOB_ONLYDIR) ?: [], 0, $max) as $trash) {
        tc_remove_tree($trash, $store);
    }
}

/**
 * Removes accounts made from an invitation that nobody ever used.
 *
 * Takes the users lock without waiting: a registration that finds it busy
 * leaves the sweep to the next one, which loses nothing - an account that
 * holds nothing costs nothing to keep a while longer.
 *
 * Bounded twice: at most TC_SWEEP_EXAMINE markers are looked at and at most
 * TC_SWEEP_REMOVE accounts removed. What it does not bound is listing the
 * markers' names and dates, which grows with the number of accounts that are
 * unused right now - a handful on a server whose accounts come by invitation.
 *
 * @return int How many accounts were removed.
 */
function tc_accounts_sweep_unused($store)
{
    // Before one-file-per-account the names live in the old list, and a
    // marker whose record cannot be found would read as an account that is
    // gone. Nothing is swept until that list is converted.
    if (is_file(tc_users_file($store))) {
        return 0;
    }
    $lock = tc_lock(tc_users_lock($store), 0);
    if (!$lock) {
        return 0;
    }
    $removed = 0;
    try {
        $now = time();
        $due = [];
        foreach (glob(tc_unused_dir($store) . '/*.dat.php') ?: [] as $path) {
            $at = @filemtime($path);
            if ($at !== false && $at <= $now) {
                $due[$path] = $at;
            }
        }
        asort($due);
        $examined = 0;
        foreach (array_keys($due) as $path) {
            if ($examined++ >= TC_SWEEP_EXAMINE || $removed >= TC_SWEEP_REMOVE) {
                break;
            }
            // One marker's trouble is its own; the rest are still looked at,
            // and none of it reaches the registration this runs inside.
            try {
                $removed += tc_account_sweep_one($store, $path, $now) ? 1 : 0;
            } catch (Throwable $e) {
                error_log('tc_accounts_sweep_unused: ' . $e->getMessage());
            }
        }
    } finally {
        tc_unlock($lock);
    }
    // As many as this sweep could have added, and one more, so leftovers
    // cannot pile up from one registration to the next.
    tc_trash_tidy($store, TC_SWEEP_REMOVE + 1);
    return $removed;
}

/**
 * Looks at one marker, and removes its account if the time has come.
 *
 * @return bool Whether an account was removed.
 */
function tc_account_sweep_one($store, $markerPath, $now)
{
    $marker = tc_read_json($markerPath);
    $uid    = $marker['uid'] ?? null;
    $name   = $marker['username'] ?? null;
    // The uid becomes a path. Only a marker this code wrote is acted on, and
    // any other is thrown away: it would otherwise sit at the front of every
    // sweep for good. Losing a marker only ever keeps an account.
    if (!is_string($uid) || !preg_match('/^[a-f0-9]{32}$/D', $uid) || !is_string($name)
            || $markerPath !== tc_unused_marker($store, $uid)) {
        @unlink($markerPath);
        return false;
    }

    // What follows finishes removals that were interrupted, and an
    // interrupted removal was due long ago. A marker younger than that has a
    // registration behind it that may still be under way.
    $old = $now - (int)($marker['since'] ?? $now) > TC_UNUSED_SECONDS;

    $recordPath = tc_account_file($store, $name);
    $record     = tc_read_json($recordPath);
    if (!is_array($record) || ($record['uid'] ?? null) !== $uid) {
        if ((is_file($recordPath) && !is_array($record)) || !$old) {
            return false;   // there but unreadable, or too new to tell: kept
        }
        // The account is gone, or its name is somebody else's now: what is
        // left is a removal that was interrupted - this sweep's, or the
        // operator's. Finished, and said only in the log: whose it was is no
        // longer known for certain, so it is not listed as never used.
        if (tc_account_delete($store, $name, $uid)) {
            error_log(sprintf('tc: finished an interrupted removal of "%s" (uid %s)', $name, $uid));
        }
        return false;
    }

    // Its directory already out of the way - a removal that stopped halfway.
    // There is no lock to take in a directory that is not there, and nothing
    // in it to lose.
    if (!is_dir(tc_user_dir($store, $uid))) {
        if ($old && tc_account_delete($store, $name, $uid)) {
            error_log(sprintf('tc: finished an interrupted removal of "%s" (uid %s)', $name, $uid));
        }
        return false;
    }

    $last = tc_account_unused_since($store, $uid, $marker);
    if ($last === false) {
        @unlink($markerPath);   // used: never a candidate again
        return false;
    }
    if ($last === null) {
        return false;
    }
    if ($now - $last <= TC_UNUSED_SECONDS) {
        // Not yet. Dated to the moment it next could be, so it waits at the
        // back rather than taking a place at the front every time. Only if it
        // is still there: touch() would bring back, empty, a marker that the
        // first thing this account stored has just taken away.
        if (is_file($markerPath)) {
            @touch($markerPath, $last + TC_UNUSED_SECONDS + 1);
        }
        return false;
    }

    // Held while it is checked once more and removed, so a device arriving
    // this very moment either got in first and stored something - and the
    // account is kept - or finds it gone and is refused.
    $logLock = tc_lock(tc_log_lock_path($store, $uid), 0);
    if (!$logLock) {
        return false;   // somebody is using it right now
    }
    try {
        $last = tc_account_unused_since($store, $uid, $marker);
        if (!is_int($last) || $now - $last <= TC_UNUSED_SECONDS) {
            if ($last === false) {
                @unlink($markerPath);
            }
            return false;
        }
        if (!tc_account_delete($store, $name, $uid)) {
            return false;
        }
    } finally {
        tc_unlock($logLock);
    }
    // Emptied now that nothing in it is held open, rather than left for
    // tc_trash_tidy - which takes whichever leftovers come first, so a
    // removal could otherwise leave its own behind. Where it will not go yet,
    // a later tidy has it.
    tc_remove_tree(tc_account_trash($store, $uid), $store);
    return tc_removed_note($store, $marker, $record, $last);
}

/**
 * Says that an account was removed, where the operator can find it.
 *
 * Once in the server's error log, and in a short list Show status reads: the
 * person whose account it was will not be told why they cannot sign in -
 * signing in does not say which names exist - so the operator must be able to.
 *
 * @return bool Always true: the account is gone whether or not this was kept.
 */
function tc_removed_note($store, array $marker, array $record, $lastContact)
{
    $entry = [
        'username' => (string)($marker['username'] ?? ''),
        'uid'      => (string)($marker['uid'] ?? ''),
        'since'    => (int)($marker['since'] ?? 0),
        'contact'  => (int)$lastContact,
        'removed'  => time(),
        'note'     => (string)($record['invited']['note'] ?? ''),
    ];
    error_log(sprintf('tc: removed the never-used account "%s" (uid %s, registered %s, no contact since %s)',
        $entry['username'], $entry['uid'], date('c', $entry['since']),
        $entry['contact'] ? date('c', $entry['contact']) : 'unknown'));
    $list = tc_read_json(tc_removed_file($store));
    if (!is_array($list) && file_exists(tc_removed_file($store))) {
        // There, but not readable: rewriting it would start a new list and
        // lose every name in the old one. The log line above has this one.
        return true;
    }
    $kept = is_array($list['removed'] ?? null) ? $list['removed'] : [];
    $kept[] = $entry;
    tc_write_json(tc_removed_file($store), ['removed' => array_slice($kept, -TC_REMOVED_KEEP)]);
    return true;
}

/**
 * The accounts removed as never used, most recent first.
 *
 * @return array[]
 */
function tc_removed_list($store)
{
    $list = tc_read_json(tc_removed_file($store));
    return array_reverse(is_array($list['removed'] ?? null) ? $list['removed'] : []);
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
    if (!is_string($deviceUid) || !preg_match(TC_DEVICE_UID_PATTERN, $deviceUid)) {
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
        if (!tc_write_json($userDir . '/user.dat.php', $user)) {
            // A token the device list does not name is one nothing can find
            // again to revoke: not handed out.
            @unlink(tc_tokens_dir($store) . '/' . $tokenId . '.dat.php');
            return null;
        }
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
    // Only this one level: if the account's directory is gone, so is the
    // account, and a request that was already on its way must not bring it
    // back as a directory nothing points to.
    if (!is_dir($dir) && !tc_secure_mkdir($dir, false)) {
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

    // A token its account has gone from under - removed while a request was
    // on its way, or one the device list never recorded - is worth nothing.
    // Asked by existence, not by whether it can be read: a read that fails
    // for a moment must not sign every device out.
    // Refused, not deleted: one stat() that fails for a moment would
    // otherwise sign a working device out for good. A token whose account
    // really is gone stays refused on every use, and the device list the
    // removal revoked from has the rest.
    if (!tc_account_present($store, $record['uid'] ?? null)) {
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
