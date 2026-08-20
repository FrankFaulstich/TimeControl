<?php
/**
 * Tests index.php over real HTTP, without needing a web server installed.
 *
 *     php php-server/test-endpoints.php
 *
 * test-oplog.php calls the log functions directly, which is the right way to
 * set up the awkward cases but leaves index.php itself untouched: the
 * routing, the sequence number arriving in the query string rather than the
 * body, the size limits, and the snapshot response, which is assembled by
 * hand instead of through json_encode. A typo in any of those would only ever
 * show up against a live installation.
 *
 * So this starts PHP's own built-in server against a copy of tc/ in a
 * temporary directory - nothing is written inside the repository - and talks
 * to it. The router below claims HTTPS on the way in, because index.php
 * refuses anything else and the built-in server does not do TLS.
 */

require_once __DIR__ . '/tc/lib/store.php';

const TC_BCRYPT_COST_TEST = 4;   // this is a test, not a password store

$GLOBALS['tc_tests'] = 0;
$GLOBALS['tc_failed'] = 0;

function tc_check($label, $condition, $detail = '')
{
    $GLOBALS['tc_tests']++;
    if ($condition) {
        printf("  ok    %s\n", $label);
        return;
    }
    $GLOBALS['tc_failed']++;
    printf("  FAIL  %s%s\n", $label, $detail === '' ? '' : '   (' . $detail . ')');
}

function tc_rmtree($path)
{
    if (!is_dir($path)) {
        @unlink($path);
        return;
    }
    foreach (scandir($path) as $entry) {
        if ($entry !== '.' && $entry !== '..') {
            tc_rmtree($path . '/' . $entry);
        }
    }
    @rmdir($path);
}

function tc_copytree($from, $to)
{
    tc_secure_mkdir($to);
    foreach (scandir($from) as $entry) {
        if ($entry === '.' || $entry === '..') {
            continue;
        }
        $src = $from . '/' . $entry;
        $dst = $to . '/' . $entry;
        if (is_dir($src)) {
            tc_copytree($src, $dst);
        } else {
            copy($src, $dst);
        }
    }
}

/**
 * One request. Returns [status, decoded body, raw body].
 */
function tc_request($base, $method, $query, $body = null, $token = null)
{
    $context = ['http' => [
        'method' => $method,
        'header' => "Content-Type: application/json\r\n"
                    . ($token ? "X-TC-Token: $token\r\n" : ''),
        'ignore_errors' => true,
        'timeout' => 15,
    ]];
    if ($body !== null) {
        $context['http']['content'] = $body;
    }
    $raw = @file_get_contents($base . '?' . http_build_query($query), false,
                              stream_context_create($context));
    $status = 0;
    foreach ($http_response_header ?? [] as $line) {
        if (preg_match('#^HTTP/\S+\s+(\d+)#', $line, $m)) {
            $status = (int)$m[1];
        }
    }
    return [$status, json_decode((string)$raw, true), (string)$raw];
}

// --- set up a throwaway installation ---------------------------------------

$root  = sys_get_temp_dir() . '/tc-endpoint-test-' . bin2hex(random_bytes(6));
$web   = $root . '/tc';
$store = $root . '/store';
tc_secure_mkdir($root);
tc_copytree(__DIR__ . '/tc', $web);
tc_secure_mkdir($store);
// The same subdirectories setup.php lays down. Without tokens/ every sign-in
// answers "busy", which is a confusing way to be told the store is not there.
foreach (['tokens', 'users'] as $sub) {
    tc_secure_mkdir($store . '/' . $sub);
}

file_put_contents($web . '/config.php',
    "<?php return " . var_export(['store' => $store], true) . ";\n");

// An account, made the way setup.php makes one.
$uid = bin2hex(random_bytes(16));
tc_write_json($store . '/users.dat.php', ['users' => ['tester' => [
    'uid' => $uid,
    'pass' => password_hash('secret', PASSWORD_BCRYPT, ['cost' => TC_BCRYPT_COST_TEST]),
    'created' => date('c'),
]]]);
tc_secure_mkdir($store . '/users/' . $uid);
tc_secure_mkdir($store . '/users/' . $uid . '/seen');
tc_write_json($store . '/users/' . $uid . '/user.dat.php',
              ['disabled' => false, 'devices' => []]);

// index.php refuses plain HTTP, and the built-in server does not do TLS. The
// router says so on the way in; nothing else about the request is touched.
file_put_contents($root . '/router.php',
    "<?php\n\$_SERVER['HTTPS'] = 'on';\nrequire __DIR__ . '/tc/index.php';\n");

$port = 8000 + random_int(0, 900);
$descriptors = [1 => ['file', $root . '/server.log', 'a'],
                2 => ['file', $root . '/server.log', 'a']];
$server = proc_open(
    sprintf('%s -S 127.0.0.1:%d %s', escapeshellarg(PHP_BINARY), $port,
            escapeshellarg($root . '/router.php')),
    $descriptors, $pipes, $root);

register_shutdown_function(function () use ($server, $root) {
    if (is_resource($server)) {
        proc_terminate($server);
        proc_close($server);
    }
    tc_rmtree($root);
});

$base = "http://127.0.0.1:$port/";
for ($i = 0; $i < 50; $i++) {
    usleep(100000);
    [$status] = tc_request($base, 'GET', ['a' => 'ping']);
    if ($status) {
        break;
    }
}
if (!$status) {
    fwrite(STDERR, "the built-in server did not come up\n");
    fwrite(STDERR, (string)@file_get_contents($root . '/server.log'));
    exit(1);
}

// --- the tests -------------------------------------------------------------

print("Signing in\n");
$device = bin2hex(random_bytes(8));
[$status, $body] = tc_request($base, 'POST', ['a' => 'login'], json_encode([
    'username' => 'tester', 'password' => 'secret',
    'device_uid' => $device, 'device_name' => 'test',
]));
tc_check('a token is issued', $status === 200 && !empty($body['token']),
         $status . ' ' . json_encode($body));
$token = $body['token'] ?? '';

print("\nFilling the log\n");
$ops = [];
for ($i = 1; $i <= 5; $i++) {
    $ops[] = ['op' => 'project.create', 'lc' => $i, 'uid' => sprintf('%016x', $i),
              'f' => ['name' => 'Projekt ' . $i]];
}
[$status, $body] = tc_request($base, 'POST', ['a' => 'push'],
                              json_encode(['base_seq' => 0, 'ops' => $ops]), $token);
tc_check('five operations accepted', $status === 200 && $body['head'] === 5,
         json_encode($body));
tc_check('a log with no snapshot reports zero', ($body['snapshot_seq'] ?? null) === 0,
         var_export($body['snapshot_seq'] ?? null, true));
tc_check('and does not ask for one', ($body['needs_snapshot'] ?? null) === false);

print("\nOffering a snapshot\n");
$document = ['schema_version' => 2, 'next_id' => 1, '_deleted' => [], 'projects' => [
    ['uid' => sprintf('%016x', 1), 'main_project_name' => 'Prüfstände Größe',
     'status' => 'open', 'last_started' => null, 'tasks' => []],
]];
$json = json_encode($document, JSON_UNESCAPED_UNICODE);

[$status, $body] = tc_request($base, 'POST', ['a' => 'snapshot', 'seq' => 4], $json, $token);
tc_check('below head it is refused with 409',
         $status === 409 && ($body['error'] ?? '') === 'not_at_head',
         $status . ' ' . json_encode($body));

[$status, $body] = tc_request($base, 'GET', ['a' => 'snapshot'], null, $token);
tc_check('there is nothing to fetch yet',
         $status === 404 && ($body['error'] ?? '') === 'no_snapshot', $status);

[$status, $body] = tc_request($base, 'POST', ['a' => 'snapshot', 'seq' => 5], $json, $token);
tc_check('at head it is accepted',
         $status === 200 && ($body['snapshot_seq'] ?? 0) === 5,
         $status . ' ' . json_encode($body));

print("\nFetching it back\n");
[$status, $body, $raw] = tc_request($base, 'GET', ['a' => 'snapshot'], null, $token);
tc_check('the response parses at all', $status === 200 && is_array($body),
         substr($raw, 0, 120));
tc_check('it is the document that went in', ($body['document'] ?? null) === $document,
         json_encode($body['document'] ?? null));
tc_check('non-ASCII survived the hand-built response',
         ($body['document']['projects'][0]['main_project_name'] ?? '') === 'Prüfstände Größe');
tc_check('it names the point it covers', ($body['seq'] ?? 0) === 5);
tc_check('and the current head', ($body['head'] ?? 0) === 5);

print("\nWhat a machine below the point is told\n");
[$status, $body] = tc_request($base, 'GET', ['a' => 'pull', 'since' => 0], null, $token);
tc_check('pull sends it to the snapshot', ($body['needs_snapshot'] ?? null) === true);
tc_check('and hands it nothing to misread', ($body['ops'] ?? null) === []);
tc_check('naming where the snapshot sits', ($body['snapshot_seq'] ?? 0) === 5);

[$status, $body] = tc_request($base, 'POST', ['a' => 'push'],
                              json_encode(['base_seq' => 0, 'ops' => [
                                  ['op' => 'task.set', 'lc' => 900,
                                   'uid' => sprintf('%016x', 9), 'f' => ['priority' => 1]]]]),
                              $token);
tc_check('its own work is still accepted', $status === 200 && $body['head'] === 6,
         json_encode($body));
tc_check('while it is still sent to the snapshot',
         ($body['needs_snapshot'] ?? null) === true);

print("\nAnd a machine at the point\n");
[$status, $body] = tc_request($base, 'GET', ['a' => 'pull', 'since' => 5], null, $token);
tc_check('reads on as before', ($body['needs_snapshot'] ?? null) === false);
tc_check('getting the tail', count($body['ops'] ?? []) === 1, json_encode($body['ops'] ?? []));
tc_check('which starts just past the snapshot',
         ($body['ops'][0]['s'] ?? 0) === 6, json_encode($body['ops'] ?? []));

print("\nA snapshot may weigh more than an ordinary request\n");
$padded = $document;
$padded['projects'][0]['tasks'] = [[
    'uid' => sprintf('%016x', 2), 'id' => 1, 'task_name' => 'gross',
    'note' => str_repeat('x', 2 * 1024 * 1024), 'status' => 'open',
    'time_entries' => [],
]];
[$status, $body] = tc_request($base, 'POST', ['a' => 'snapshot', 'seq' => 6],
                              json_encode($padded, JSON_UNESCAPED_UNICODE), $token);
tc_check('two megabytes is taken, where a push of that size would not be',
         $status === 200 && ($body['snapshot_seq'] ?? 0) === 6,
         $status . ' ' . json_encode($body));

print("\nWhat is refused\n");
[$status, $body] = tc_request($base, 'POST', ['a' => 'snapshot', 'seq' => 0], $json, $token);
tc_check('a missing sequence number', $status === 400 && ($body['error'] ?? '') === 'bad_seq',
         $status . ' ' . json_encode($body));

[$status, $body] = tc_request($base, 'PUT', ['a' => 'snapshot'], $json, $token);
tc_check('a method that is neither', $status === 405, $status);

[$status, $body] = tc_request($base, 'GET', ['a' => 'snapshot'], null, 'not-a-token');
tc_check('an unusable token', $status === 401 && ($body['error'] ?? '') === 'invalid_token',
         $status);

$big = json_encode(['projects' => [['uid' => sprintf('%016x', 1),
                                    'note' => str_repeat('x', 5 * 1024 * 1024)]]]);
[$status, $body] = tc_request($base, 'POST', ['a' => 'snapshot', 'seq' => 6], $big, $token);
tc_check('a document past the size limit',
         $status === 413 && ($body['error'] ?? '') === 'body_too_large',
         $status . ' ' . json_encode($body));

// The ordinary limit has to stay where it was: a snapshot is the one request
// allowed to be larger, and raising it for everything would undo the reason
// the cap exists.
$bigPush = json_encode(['base_seq' => 0, 'ops' => [
    ['op' => 'task.set', 'lc' => 1, 'uid' => sprintf('%016x', 1),
     'f' => ['note' => str_repeat('x', 2 * 1024 * 1024)]]]]);
[$status, $body] = tc_request($base, 'POST', ['a' => 'push'], $bigPush, $token);
tc_check('a push past the ordinary limit still is too',
         $status === 413 && ($body['error'] ?? '') === 'body_too_large',
         $status . ' ' . json_encode($body));

printf("\n%d tests, %d failed\n", $GLOBALS['tc_tests'], $GLOBALS['tc_failed']);
exit($GLOBALS['tc_failed'] === 0 ? 0 : 1);
