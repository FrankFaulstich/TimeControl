<?php
/**
 * TimeControl sync server - API entry point.
 *
 * This first slice carries authentication only: log in, prove a token,
 * log out. The operation log that actually synchronises data comes next and
 * will sit behind exactly this check.
 */

if (PHP_VERSION_ID < 70400) {
    http_response_code(500);
    exit('PHP 7.4 or newer required');
}
umask(0077);

require_once __DIR__ . '/lib/store.php';
require_once __DIR__ . '/lib/auth.php';
require_once __DIR__ . '/lib/http.php';
require_once __DIR__ . '/lib/oplog.php';

// Errors go to a file inside the store, never to the response: a stack trace
// naming absolute paths is a gift to anyone probing this endpoint.
ini_set('display_errors', '0');
ini_set('log_errors', '1');

// The transport is checked before anything else - before the credential is
// looked at, and before the server says anything at all about its own state.
// A bearer token is worth exactly as much as the channel carrying it, so
// accepting one "just this once" over plain HTTP would mean it has already
// been exposed. Answering questions about whether the service is installed
// belongs behind the same line.
if (!tc_is_https()) {
    tc_fail(403, 'https_required', 'This endpoint is only available over HTTPS.');
}

$config = tc_config();
if ($config === null) {
    tc_fail(503, 'not_installed', 'The server has not been set up yet. Run setup.php.');
}
ini_set('error_log', $config['store'] . '/error.log');
$store = $config['store'];

$action = isset($_GET['a']) ? (string)$_GET['a'] : '';

switch ($action) {

    // -----------------------------------------------------------------
    case 'login':
        if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') {
            tc_fail(405, 'method_not_allowed', 'Use POST.');
        }
        $body       = tc_body();
        $username   = isset($body['username']) ? (string)$body['username'] : '';
        $password   = isset($body['password']) ? (string)$body['password'] : '';
        $deviceUid  = isset($body['device_uid']) ? (string)$body['device_uid'] : '';
        $deviceName = isset($body['device_name']) ? (string)$body['device_name'] : 'unnamed';

        // The device id becomes a filename; and a name is only ever shown
        // back to its owner, so it is length-capped rather than sanitised.
        if (!preg_match('/^[a-f0-9]{16}$/', $deviceUid)) {
            tc_fail(400, 'bad_device_uid', 'device_uid must be 16 hexadecimal characters.');
        }
        $deviceName = mb_substr(preg_replace('/[^\P{C}]+/u', '', $deviceName), 0, 60);

        if ($username === '' || $password === '') {
            tc_fail(400, 'missing_credentials', 'username and password are required.');
        }

        if (!tc_hash_budget_take($store)) {
            tc_fail(429, 'too_many_attempts', 'Too many sign-in attempts right now. Try again shortly.');
        }

        $user = tc_user_find($store, $username);

        // Verify against a dummy hash when the account does not exist, so a
        // wrong username and a wrong password cost the same time. Otherwise
        // the response time alone tells an attacker which names are real.
        $hash = ($user && isset($user['pass']))
            ? $user['pass']
            : '$2y$12$C6UzMDM.H6dfI/f/IKcEeO.PxLnaBoQAhLtOUsFAKEHASHvalue.';
        $passwordOk = password_verify($password, $hash);

        if (!$user || !$passwordOk || !empty($user['disabled'])) {
            tc_fail(401, 'invalid_credentials', 'Wrong username or password.');
        }

        $issued = tc_token_issue($store, $user['uid'], $deviceUid, $deviceName);
        if ($issued === null) {
            tc_fail(503, 'busy', 'Could not issue a token right now. Try again.');
        }
        tc_ok([
            'token'      => $issued['token'],
            'expires_at' => $issued['expires_at'],
            'username'   => $user['username'],
        ]);
        break;

    // -----------------------------------------------------------------
    case 'ping':
        $session = tc_token_check($store, tc_presented_token());
        if (!$session) {
            // One code for every reason the token is not usable - expired,
            // revoked, account switched off. The client's response is the
            // same in all three cases: log in again. Distinguishing them
            // here would only tell an attacker which tokens once existed.
            tc_fail(401, 'invalid_token', 'Token is missing, expired or revoked.');
        }
        tc_ok([
            'device_uid' => $session['device_uid'],
            'expires_at' => $session['exp'],
            'server_time' => time(),
        ]);
        break;

    // -----------------------------------------------------------------
    case 'logout':
        $session = tc_token_check($store, tc_presented_token());
        if (!$session) {
            // Already not usable - which is the state the caller wanted.
            tc_ok(['revoked' => false]);
        }
        tc_token_revoke($store, $session['token_id']);
        tc_ok(['revoked' => true]);
        break;

    // -----------------------------------------------------------------
    // The cheap poll. A client that syncs every few minutes asks this first
    // and only pushes or pulls when the answer has moved.
    case 'head':
        $session = tc_token_check($store, tc_presented_token());
        if (!$session) {
            tc_fail(401, 'invalid_token', 'Token is missing, expired or revoked.');
        }
        $state = tc_log_state($store, $session['uid']);
        tc_ok(['head' => (int)$state['head'], 'server_time' => time()]);
        break;

    // -----------------------------------------------------------------
    // Push and pull are one round trip: submitting work and learning what
    // happened elsewhere are the same conversation, and splitting them would
    // double the requests for no gain.
    case 'push':
        if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') {
            tc_fail(405, 'method_not_allowed', 'Use POST.');
        }
        $session = tc_token_check($store, tc_presented_token());
        if (!$session) {
            tc_fail(401, 'invalid_token', 'Token is missing, expired or revoked.');
        }
        $body    = tc_body();
        $baseSeq = isset($body['base_seq']) ? (int)$body['base_seq'] : 0;
        $ops     = $body['ops'] ?? [];

        $bad = tc_ops_validate($ops);
        if ($bad !== null) {
            tc_fail(400, $bad, 'The batch was rejected: ' . $bad . '.');
        }

        // The device comes from the token, never from the body. Letting a
        // caller name its own device would let it move another device's
        // duplicate counter and make that device's retries vanish.
        $result = tc_log_append($store, $session['uid'], $session['device_uid'], $ops);
        if ($result === null) {
            tc_fail(503, 'busy', 'The log is locked right now. Retry.');
        }

        $read = tc_log_read($store, $session['uid'], $baseSeq, TC_PULL_MAX_OPS, $session['device_uid']);
        tc_ok([
            'head'     => $result['head'],
            'assigned' => $result['assigned'],
            'dups'     => $result['dups'],
            'ops'      => $read['ops'],
            'more'     => $read['more'],
        ]);
        break;

    // -----------------------------------------------------------------
    // Catching up without anything to contribute.
    case 'pull':
        $session = tc_token_check($store, tc_presented_token());
        if (!$session) {
            tc_fail(401, 'invalid_token', 'Token is missing, expired or revoked.');
        }
        $since = isset($_GET['since']) ? (int)$_GET['since'] : 0;
        $limit = isset($_GET['limit']) ? max(1, min(TC_PULL_MAX_OPS, (int)$_GET['limit'])) : TC_PULL_MAX_OPS;

        // No device is excluded here: a client asking to catch up from a
        // given point wants everything after it, including its own earlier
        // work - that is what a fresh machine, or one restoring a backup,
        // needs in order to rebuild.
        $read = tc_log_read($store, $session['uid'], $since, $limit);
        tc_ok(['head' => $read['head'], 'ops' => $read['ops'], 'more' => $read['more']]);
        break;

    // -----------------------------------------------------------------
    default:
        tc_fail(404, 'unknown_action', 'Unknown action.');
}
