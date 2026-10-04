<?php
/**
 * Proof of work in front of the password hash (issue #591).
 *
 * Hashing a password is deliberately expensive (TC_BCRYPT_COST), which makes
 * it a lever: whoever can make the server hash can make it work hard, for the
 * price of one HTTP request. Once registration is open to anybody (#552) that
 * price has to go up, and it has to go up for the caller rather than for the
 * operator's users - so no CAPTCHA, which sends them to a third party and
 * assumes a browser, and no e-mail check, which is a subsystem with personal
 * data and a spam problem attached (development-plan/open-registration.md,
 * section 2). The client here is a program, and a program can be asked to
 * compute something.
 *
 * The server hands out a challenge; the client finds a nonce such that
 * SHA-256(challenge ":" nonce) begins with TC_POW_BITS zero bits, about a
 * million tries at 20; the server checks that with one hash. Done properly or
 * not at all:
 *
 *  - Issued by the server, so nobody can bring their own. Each challenge
 *    carries an HMAC under a key only the server knows.
 *  - Bound to the time it was issued and valid for TC_POW_TTL seconds, so a
 *    stock of solutions cannot be built up in advance.
 *  - Single-use: the first solution that arrives is recorded, every later
 *    one refused. Without that a solved challenge is a token that can be
 *    minted once and replayed for ever.
 *  - Its difficulty is written into the challenge and checked against
 *    TC_POW_BITS. Raising the constant makes every later challenge harder,
 *    and a client needs no change for it: it solves whatever it is given, up
 *    to the 28 bits of POW_MAX_BITS in tt/sync_client.py. Each bit doubles
 *    the work - on an ordinary machine about 0.7 s at 20, 10 s at 24, three
 *    minutes at 28 - so anything near that ceiling is for a server under
 *    siege, not for every day.
 *
 * Handing out a challenge writes nothing. A server that stored each one would
 * create a file for every request anybody cared to send - the reason the
 * sign-in allowance is one global counter rather than one per caller. Only a
 * solution leaves something behind, and a solution costs its sender the work.
 *
 * Where it is asked for: in front of ?a=register, the one path that hashes a
 * password for somebody who has no account yet. There an invitation code
 * already does the job - nothing is hashed without a code only the operator
 * can make - so a solution is checked when one is sent but not demanded. It
 * becomes the requirement for registering without a code, if that is ever
 * opened (#552); invitations then stay the way to skip it.
 */

const TC_POW_BITS = 20;     // leading zero bits; about 0.7 s for the client
const TC_POW_TTL  = 300;    // seconds a challenge may be solved and used in
const TC_POW_SKEW = 60;     // how far ahead of this clock an issue time may be

// What a challenge may be for. Each is part of what the HMAC covers, so a
// solution made for one purpose is worth nothing for another.
const TC_POW_PURPOSES = ['register'];

// A spent challenge is remembered until it has expired anyway; tidying that
// up is done in passing, a few at a time, like everything here without cron.
const TC_POW_PRUNE_MAX = 50;

// Spent challenges are filed by the minute they were issued in, one directory
// per minute. Tidying then only has to look at the few directories whose
// minute is long enough past, rather than at every challenge spent recently -
// which somebody solving challenges as fast as they can would otherwise make
// the cost of every single spend.
const TC_POW_BUCKET = 60;

function tc_pow_key_dir($store)   { return $store . '/pow-key'; }
function tc_pow_spent_dir($store) { return $store . '/pow-spent'; }

/**
 * Why the key cannot be used, for Show status - or null when it can, or
 * when there is none yet and the next challenge will make one.
 *
 * Only ever said there. Writing it to the error log from the request that
 * found it would let anybody who keeps asking for challenges fill that log.
 */
function tc_pow_key_problem($store)
{
    $dir = tc_pow_key_dir($store);
    if (!file_exists($dir)) {
        return null;
    }
    $data = tc_read_json($dir . '/key.dat.php');
    $hex  = is_array($data) ? ($data['key'] ?? null) : null;
    if (is_string($hex) && preg_match('/^[a-f0-9]{64}$/D', $hex)) {
        return null;
    }
    return 'The proof-of-work key in ' . $dir . ' cannot be read, so no challenges are '
         . 'being issued. Delete that directory; the next request makes a new key, and only '
         . 'challenges handed out in the last few minutes stop counting.';
}

/**
 * The server's own key for signing challenges, made on first use.
 *
 * Kept in a directory of its own because of how it is made. Two requests can
 * both find it missing; each builds a complete directory under a temporary
 * name and renames it into place, and renaming a directory onto one that
 * already holds a file fails - so exactly one of them wins, everybody reads
 * the winner's, and no challenge is ever signed with a key that is then
 * replaced. A plain file renamed into place would be overwritten by the loser
 * instead, and every challenge the winner had issued meanwhile would fail.
 * store.php explains why the lock cannot be what decides this.
 *
 * @return string|null The 32-byte key; null when it cannot be read or made.
 */
function tc_pow_key($store)
{
    $dir  = tc_pow_key_dir($store);
    $read = function () use ($dir) {
        $data = tc_read_json($dir . '/key.dat.php');
        $hex  = is_array($data) ? ($data['key'] ?? null) : null;
        return is_string($hex) && preg_match('/^[a-f0-9]{64}$/D', $hex) ? hex2bin($hex) : null;
    };
    $key = $read();
    if ($key !== null || file_exists($dir)) {
        // There but unreadable is not mended by making another: a new key
        // would quietly void every challenge in flight, and the reason it
        // cannot be read would still be there.
        return $key;
    }
    $tmp = $store . '/.tmp-pow-key-' . bin2hex(random_bytes(6));
    if (tc_secure_mkdir($tmp, false)
            && tc_write_json($tmp . '/key.dat.php', ['key' => bin2hex(random_bytes(32))])) {
        @rename($tmp, $dir);
    }
    // Whether that rename won or another request's did, theirs or ours is
    // the one in place now, and what is left here is only a loser's copy.
    @unlink($tmp . '/key.dat.php');
    @rmdir($tmp);
    clearstatcache();
    return $read();
}

/**
 * The part of a challenge its HMAC covers.
 */
function tc_pow_signed_part($purpose, $issued, $bits, $salt)
{
    return '1.' . $purpose . '.' . $issued . '.' . $bits . '.' . $salt;
}

/**
 * Issues a challenge. Writes nothing.
 *
 * @param int|null $now  For the tests; the current time otherwise.
 * @param int|null $bits For the tests; TC_POW_BITS otherwise.
 * @return array|null ['challenge' => string, 'bits' => int, 'expires_in' => int],
 *                    or null when the key is not available.
 */
function tc_pow_challenge($store, $purpose, $now = null, $bits = null)
{
    if (!in_array($purpose, TC_POW_PURPOSES, true)) {
        return null;
    }
    $key = tc_pow_key($store);
    if ($key === null) {
        return null;
    }
    $now    = $now === null ? time() : (int)$now;
    $bits   = $bits === null ? TC_POW_BITS : (int)$bits;
    $signed = tc_pow_signed_part($purpose, $now, $bits, bin2hex(random_bytes(8)));
    return [
        'challenge'  => $signed . '.' . hash_hmac('sha256', $signed, $key),
        'bits'       => $bits,
        // A duration rather than a time, so a client whose clock is wrong
        // still knows how long it has.
        'expires_in' => TC_POW_TTL,
    ];
}

/**
 * How many zero bits a binary digest begins with.
 */
function tc_pow_leading_zero_bits($digest)
{
    $bits = 0;
    $len  = strlen($digest);
    for ($i = 0; $i < $len; $i++) {
        $byte = ord($digest[$i]);
        if ($byte === 0) {
            $bits += 8;
            continue;
        }
        for ($mask = 0x80; ($byte & $mask) === 0; $mask >>= 1) {
            $bits++;
        }
        break;
    }
    return $bits;
}

/**
 * Checks a solution and uses the challenge up.
 *
 * Everything that costs nothing is checked before the one thing that writes:
 * the form, the signature, the time, the difficulty, the work itself. Only a
 * solution that passes all of them is recorded - in a directory named after
 * the challenge's signature, because creating a directory either happens or
 * finds one already there, in one step, even where flock() does nothing. That
 * is what makes it single-use; the second request to arrive with the same
 * challenge finds the directory and is refused.
 *
 * Numbers are taken only as they were written, without leading zeros. Read
 * leniently, "0179..." and "179..." would carry the same signature but be two
 * different strings - one challenge spendable once per way of padding it.
 *
 * @param int|null $now     For the tests; the current time otherwise.
 * @param int|null $minBits For the tests; TC_POW_BITS otherwise.
 * @return string|null null when accepted; otherwise 'pow_invalid',
 *                     'pow_expired', 'pow_used', or 'busy' when the use could
 *                     not be recorded.
 */
function tc_pow_spend($store, $challenge, $nonce, $purpose, $now = null, $minBits = null)
{
    $now     = $now === null ? time() : (int)$now;
    $minBits = $minBits === null ? TC_POW_BITS : (int)$minBits;
    if (!is_string($challenge) || !is_string($nonce)
            || !preg_match('/^1\.([a-z]+)\.(0|[1-9]\d{0,11})\.(0|[1-9]\d{0,2})\.([a-f0-9]{16})\.([a-f0-9]{64})$/D',
                           $challenge, $m)
            || !preg_match('/^[0-9a-z]{1,64}$/D', $nonce)) {
        return 'pow_invalid';
    }
    list(, $for, $issued, $bits, $salt, $mac) = $m;
    $issued = (int)$issued;
    $bits   = (int)$bits;

    $key = tc_pow_key($store);
    if ($key === null) {
        return 'busy';
    }
    if ($for !== $purpose
            || !hash_equals(hash_hmac('sha256', tc_pow_signed_part($for, $issued, $bits, $salt), $key), $mac)) {
        return 'pow_invalid';
    }
    if ($issued > $now + TC_POW_SKEW) {
        return 'pow_invalid';
    }
    if ($now - $issued > TC_POW_TTL) {
        return 'pow_expired';
    }
    // Issued before the constant was raised: solved, but to the old standard.
    if ($bits < $minBits || $bits > 256) {
        return 'pow_invalid';
    }
    if (tc_pow_leading_zero_bits(hash('sha256', $challenge . ':' . $nonce, true)) < $bits) {
        return 'pow_invalid';
    }

    $spent  = tc_pow_spent_dir($store);
    $minute = $spent . '/' . intdiv($issued, TC_POW_BUCKET);
    if ((!is_dir($spent) && !tc_secure_mkdir($spent, false))
            // Another request may make it at the same moment, which is fine.
            || (!is_dir($minute) && !@mkdir($minute, 0700) && !is_dir($minute))) {
        return 'busy';
    }
    $marker = $minute . '/' . $mac;
    if (!@mkdir($marker, 0700)) {
        clearstatcache();
        // Accepted without the record, it would be accepted again.
        return is_dir($marker) ? 'pow_used' : 'busy';
    }
    tc_pow_prune($store, $now);
    return null;
}

/**
 * Forgets spent challenges that have expired since.
 *
 * Their record is only needed while the challenge could still be presented,
 * and that is judged by when it was issued - the signed time, which is what
 * the minute directories are named after, not when a file was written, which
 * is another server's clock or the file server's. A minute goes once every
 * challenge in it is past TC_POW_TTL by more than the TC_POW_SKEW any server
 * judging it may be behind this one.
 *
 * Bounded twice over: at most TC_POW_PRUNE_MAX records per call, and only the
 * minutes that are due are opened at all. There are a handful of minutes, not
 * one entry per challenge, so finding them costs next to nothing.
 *
 * @return int How many records were removed.
 */
function tc_pow_prune($store, $now)
{
    $spent = tc_pow_spent_dir($store);
    $top   = @opendir($spent);
    if (!$top) {
        return 0;
    }
    $removed = 0;
    while ($removed < TC_POW_PRUNE_MAX && ($name = readdir($top)) !== false) {
        if (!preg_match('/^\d{1,12}$/D', $name)
                || $now - ((int)$name + 1) * TC_POW_BUCKET <= TC_POW_TTL + TC_POW_SKEW) {
            continue;
        }
        $dir   = $spent . '/' . $name;
        $inner = @opendir($dir);
        if ($inner) {
            while ($removed < TC_POW_PRUNE_MAX && ($entry = readdir($inner)) !== false) {
                if ($entry !== '.' && $entry !== '..' && @rmdir($dir . '/' . $entry)) {
                    $removed++;
                }
            }
            closedir($inner);
        }
        // Goes only once it is empty; otherwise the next call goes on with it.
        @rmdir($dir);
    }
    closedir($top);
    return $removed;
}
