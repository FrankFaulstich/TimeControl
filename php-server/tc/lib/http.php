<?php
/**
 * Request and response helpers.
 *
 * The error contract matters more than it looks. A client that cannot tell
 * "your token is gone, log in again" from "the network is having a bad day"
 * has only one move - retry for ever - so every failure carries a stable
 * machine-readable code alongside the human text.
 */

function tc_json($code, array $payload)
{
    http_response_code($code);
    header('Content-Type: application/json; charset=utf-8');
    header('Cache-Control: no-store');
    header('X-Content-Type-Options: nosniff');
    header('X-Robots-Tag: noindex, nofollow');
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
 * Reads and decodes the request body.
 *
 * Capped, and with a bounded nesting depth: this endpoint is reachable by
 * anyone, and neither an enormous body nor a deeply nested structure should
 * be able to exhaust memory before the credential has even been looked at.
 */
function tc_body()
{
    $raw = file_get_contents('php://input', false, null, 0, 1048576);
    if ($raw === false || $raw === '') {
        return [];
    }
    $data = json_decode($raw, true, 32);
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
 * Checked directly rather than trusting a forwarding header: the probe showed
 * REMOTE_ADDR is the real client address on this host, so there is no proxy
 * whose X-Forwarded-Proto would be authoritative - which means anything
 * claiming to be one is the client talking about itself.
 */
function tc_is_https()
{
    if (!empty($_SERVER['HTTPS']) && strtolower($_SERVER['HTTPS']) !== 'off') {
        return true;
    }
    return (int)($_SERVER['SERVER_PORT'] ?? 0) === 443;
}
