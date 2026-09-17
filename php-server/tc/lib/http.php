<?php
/**
 * Request and response helpers.
 *
 * The error contract matters more than it looks. A client that cannot tell
 * "your token is gone, log in again" from "the network is having a bad day"
 * has only one move - retry for ever - so every failure carries a stable
 * machine-readable code alongside the human text.
 */

// What an ordinary request body may weigh. A snapshot is the one thing that
// legitimately exceeds it and says so explicitly; everything else stays here.
const TC_BODY_MAX = 1048576;

function tc_send_headers($code)
{
    http_response_code($code);
    header('Content-Type: application/json; charset=utf-8');
    header('Cache-Control: no-store');
    header('X-Content-Type-Options: nosniff');
    header('X-Robots-Tag: noindex, nofollow');
}

function tc_json($code, array $payload)
{
    tc_send_headers($code);
    echo json_encode($payload, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
    exit;
}

/**
 * @param string $code  Stable identifier the client branches on.
 * @param string $text  Human explanation; never parsed.
 */
function tc_fail($status, $code, $text)
{
    tc_json($status, ['ok' => false, 'error' => $code, 'message' => $text]);
}

function tc_ok(array $payload = [])
{
    tc_json(200, ['ok' => true] + $payload);
}

/**
 * Reads the request body as it arrived, refusing anything over the limit.
 *
 * Say so rather than reading a prefix and shrugging. Truncating the body and
 * handing back whatever parses turns a too-large push into an empty one: the
 * server appends nothing, answers "ok", and the client strikes the operations
 * off as delivered. Nothing is reported at either end and the changes are
 * simply gone. Refusing is the only safe answer, and the client can then send
 * the batch in smaller pieces.
 *
 * @param int $limit Bytes. Raised only for the snapshot upload, which is a
 *                   whole document by nature and is read only after the
 *                   token has been checked.
 */
function tc_raw_body($limit = TC_BODY_MAX)
{
    $raw = file_get_contents('php://input', false, null, 0, $limit + 1);
    if ($raw === false) {
        return '';
    }
    if (strlen($raw) > $limit) {
        tc_fail(413, 'body_too_large',
                'The request body exceeds ' . $limit . ' bytes.');
    }
    return $raw;
}

/**
 * Reads and decodes the request body.
 *
 * Capped, and with a bounded nesting depth: this endpoint is reachable by
 * anyone, and neither an enormous body nor a deeply nested structure should
 * be able to exhaust memory before the credential has even been looked at.
 */
function tc_body($limit = TC_BODY_MAX)
{
    $raw = tc_raw_body($limit);
    if ($raw === '') {
        return [];
    }

    $data = json_decode($raw, true, 32);
    if ($data === null && strtolower(trim($raw)) !== 'null') {
        tc_fail(400, 'bad_json', 'The request body is not valid JSON.');
    }
    return is_array($data) ? $data : [];
}

/**
 * Returns the presented credential, or null.
 *
 * X-TC-Token is the primary carrier. Authorization is accepted too, but is
 * not relied upon: some shared hosts strip it before PHP ever sees it, and
 * recovering it needs an .htaccess rule - authentication should not depend on
 * a file whose effect we cannot guarantee.
 */
function tc_presented_token()
{
    if (!empty($_SERVER['HTTP_X_TC_TOKEN'])) {
        return trim($_SERVER['HTTP_X_TC_TOKEN']);
    }
    $auth = $_SERVER['HTTP_AUTHORIZATION'] ?? $_SERVER['REDIRECT_HTTP_AUTHORIZATION'] ?? '';
    if (stripos($auth, 'Bearer ') === 0) {
        return trim(substr($auth, 7));
    }
    return null;
}

/**
 * True when the request arrived over TLS.
 *
 * There is no way to work this out that is right everywhere, because the
 * answer depends on what sits in front of PHP - and only whoever installed
 * this knows that. So it is a setting: `https` in tc/config.php, one of
 *
 *   'auto'    the default, and what every installation had before the setting
 *             existed. $_SERVER['HTTPS'] when the server sets it, and failing
 *             that, port 443. The port is an indication rather than a proof -
 *             a plaintext request arriving on 443 passes - which is why the
 *             other two exist.
 *
 *   'strict'  $_SERVER['HTTPS'] alone. Correct wherever the server sets it,
 *             and the right choice for anyone who wants no guessing at all.
 *             It refuses a genuinely encrypted request on a server that does
 *             not set the variable, which is why it is not the default.
 *
 *   'proxy'   X-Forwarded-Proto, for a front end that terminates TLS and
 *             speaks to PHP in the clear. Without this such an installation
 *             answers 403 to everything for ever, which looks like a broken
 *             server rather than a setting. Only choose it when something
 *             really does sit in front: the header is the client's to invent
 *             otherwise, and a proxy that does not strip an incoming copy
 *             hands that invention straight through.
 *
 * @return bool
 */
function tc_is_https()
{
    $mode = 'auto';
    // Guarded because setup.php reaches this before a store exists, and a
    // fresh installation has no config.php at all.
    if (function_exists('tc_config')) {
        $config = tc_config();
        if (is_array($config) && !empty($config['https'])) {
            $mode = (string)$config['https'];
        }
    }
    return tc_transport_is_tls($mode);
}

/**
 * The decision itself, with the mode handed in.
 *
 * Split out so it can be tested against made-up $_SERVER values without a
 * config.php, and so setup.php can ask the same question the API asks - it
 * used to carry its own copy of this, which is exactly the sort of pair that
 * drifts apart and leaves one door open after the other has been shut.
 *
 * @param string $mode One of 'auto', 'strict', 'proxy'. Anything else is
 *                     treated as 'auto': a typo in a setting must not be the
 *                     thing that makes a server refuse every request.
 * @return bool
 */
function tc_transport_is_tls($mode)
{
    if ($mode === 'proxy') {
        // A list when the request crossed more than one hop; the first entry
        // is what the client itself spoke.
        $parts = explode(',', $_SERVER['HTTP_X_FORWARDED_PROTO'] ?? '');
        $spoke = strtolower(trim($parts[0]));
        if ($spoke === 'https') {
            return true;
        }
        if ($spoke === 'http') {
            // The front end saying plainly that the client hop was not
            // encrypted. Believed, and refused.
            return false;
        }
        // No header at all: fall through. A proxy that speaks TLS onwards as
        // well sets HTTPS here instead, and refusing that would be wrong.
    }
    if (!empty($_SERVER['HTTPS']) && strtolower($_SERVER['HTTPS']) !== 'off') {
        return true;
    }
    if ($mode === 'strict') {
        return false;
    }
    return (int)($_SERVER['SERVER_PORT'] ?? 0) === 443;
}
