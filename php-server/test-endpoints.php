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
// For TC_HASH_BUDGET_PER_MINUTE, which the sign-in tests below spend: read from
// the server's own definition so they go on meaning something if it changes.
require_once __DIR__ . '/tc/lib/auth.php';
// For the storage limit below: its value, and the log's own bookkeeping.
require_once __DIR__ . '/tc/lib/oplog.php';
// For solving the challenges ?a=challenge hands out, by the server's own count.
require_once __DIR__ . '/tc/lib/pow.php';

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
foreach (['tokens', 'users', 'accounts'] as $sub) {
    tc_secure_mkdir($store . '/' . $sub);
}

file_put_contents($web . '/config.php',
    "<?php return " . var_export(['store' => $store], true) . ";\n");

// An account, made by what setup.php makes one with.
$uid = tc_account_create($store, 'tester',
    password_hash('secret', PASSWORD_BCRYPT, ['cost' => TC_BCRYPT_COST_TEST]))['uid'];

// index.php refuses plain HTTP, and the built-in server does not do TLS. The
// router says so on the way in; nothing else about the request is touched.
file_put_contents($root . '/router.php',
    "<?php\n\$_SERVER['HTTPS'] = 'on';\nrequire __DIR__ . '/tc/index.php';\n");

// Clear of 8500-8530, where TimeControl's own interface may be running.
$port = 9000 + random_int(0, 800);
$descriptors = [1 => ['file', $root . '/server.log', 'a'],
                2 => ['file', $root . '/server.log', 'a']];
// display_errors off, as on any host in production. It matters for the large
// snapshots below: a body past post_max_size (8M by default) makes PHP warn
// before index.php has run, and shown, that warning would come before the
// answer and break it. Hidden, PHP still hands the whole body over - which is
// what lets a snapshot be larger than post_max_size at all.
$server = proc_open(
    sprintf('%s -d display_errors=0 -S 127.0.0.1:%d %s', escapeshellarg(PHP_BINARY), $port,
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

// The route every quiet cycle now takes, and the one this file never asked
// for until issue #561 - the client half was pinned in the Python tests and
// nothing had ever requested ?a=head over HTTP at all.
print("\nThe cheap poll\n");
[$status, $body] = tc_request($base, 'GET', ['a' => 'head'], null, $token);
tc_check('an empty log reports zero',
         $status === 200 && ($body['head'] ?? null) === 0,
         $status . ' ' . json_encode($body));
tc_check('and says when it answered', is_int($body['server_time'] ?? null));

[$status, $body] = tc_request($base, 'GET', ['a' => 'head'], null, 'not-a-token');
tc_check('without a usable token it refuses, like everything else',
         $status === 401 && ($body['error'] ?? '') === 'invalid_token',
         $status . ' ' . json_encode($body));

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

[$status, $body] = tc_request($base, 'GET', ['a' => 'head'], null, $token);
tc_check('the poll now says the same number the push did',
         $status === 200 && ($body['head'] ?? null) === 5,
         $status . ' ' . json_encode($body));

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

// The document that outgrew the old limit of 4 MiB: a few thousand tasks, many
// with long notes, 5.3 MB in all. Refused, it could never be compacted, and its
// account filled up behind it until every push was refused as well.
[$status, $body] = tc_request($base, 'POST', ['a' => 'push'], json_encode(['base_seq' => 6, 'ops' => [
    ['op' => 'task.set', 'lc' => 1000, 'uid' => sprintf('%016x', 2), 'f' => ['note' => 'one more']]]]),
    $token);
tc_check('(a change, so there is something new to compact)', $status === 200 && ($body['head'] ?? 0) === 7,
         $status . ' ' . json_encode($body));
$heavy = $document;
$heavy['projects'][0]['tasks'] = [];
for ($t = 0; $t < 2600; $t++) {
    $heavy['projects'][0]['tasks'][] = [
        'uid' => sprintf('%016x', 100 + $t), 'id' => $t + 1, 'task_name' => 'Aufgabe ' . $t,
        'note' => str_repeat('Lange Notiz aus einer E-Mail, mit Umlauten: äöü ß. ', 38),
        'status' => 'open', 'time_entries' => [],
    ];
}
$heavyJson = json_encode($heavy, JSON_UNESCAPED_UNICODE);
[$status, $body] = tc_request($base, 'POST', ['a' => 'snapshot', 'seq' => 7], $heavyJson, $token);
tc_check(sprintf('a document of %.1f MB, the size that was refused, is taken', strlen($heavyJson) / 1e6),
         strlen($heavyJson) > 5 * 1024 * 1024 && $status === 200 && ($body['snapshot_seq'] ?? 0) === 7,
         $status . ' ' . json_encode($body));
[$status, $body, $raw] = tc_request($base, 'GET', ['a' => 'snapshot'], null, $token);
tc_check('and handed back as it was sent',
         $status === 200 && ($body['document'] ?? null) == $heavy && strlen($raw) > strlen($heavyJson),
         $status . ' ' . strlen($raw));

// Past PHP's post_max_size, which this server leaves at the 8M default: a body
// that is not a form is handed over whole all the same.
[$status, $body] = tc_request($base, 'POST', ['a' => 'push'], json_encode(['base_seq' => 7, 'ops' => [
    ['op' => 'task.set', 'lc' => 1001, 'uid' => sprintf('%016x', 2), 'f' => ['note' => 'and one more']]]]),
    $token);
$heavier = $heavy;
$heavier['projects'][0]['tasks'] = array_merge($heavy['projects'][0]['tasks'], array_map(function ($task) {
    $task['uid'] = sprintf('%016x', hexdec($task['uid']) + 10000);
    return $task;
}, $heavy['projects'][0]['tasks']));
$heavierJson = json_encode($heavier, JSON_UNESCAPED_UNICODE);
[$status, $body] = tc_request($base, 'POST', ['a' => 'snapshot', 'seq' => 8], $heavierJson, $token);
tc_check(sprintf('and one of %.1f MB, past PHP\'s post_max_size', strlen($heavierJson) / 1e6),
         strlen($heavierJson) > 8 * 1024 * 1024 && $status === 200 && ($body['snapshot_seq'] ?? 0) === 8,
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
                                    'note' => str_repeat('x', TC_SNAPSHOT_MAX_BYTES)]]]);
[$status, $body] = tc_request($base, 'POST', ['a' => 'snapshot', 'seq' => 7], $big, $token);
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

// Issue #585. The password-checking allowance is one counter for the whole
// installation, and anybody who can reach ?a=login can spend it. What that used
// to cost was nobody being able to sign in at all until it refilled.
print("\nSigning in while somebody else has spent the allowance\n");

// Spent by writing the counter rather than by making thirty requests: each of
// those would be a real bcrypt, and a run that crossed a minute boundary would
// find the counter reset and test nothing. Written again just before each
// request for the same reason, and nudged clear of the boundary first.
if (time() % 60 > 55) {
    sleep(61 - time() % 60);
}
$spend = function () use ($store) {
    tc_write_json($store . '/rate.dat.php',
                  ['win' => intdiv(time(), 60), 'n' => TC_HASH_BUDGET_PER_MINUTE]);
};

$spend();
[$status, $body] = tc_request($base, 'POST', ['a' => 'login'], json_encode([
    'username' => 'tester', 'password' => 'secret',
    'device_uid' => bin2hex(random_bytes(8)), 'device_name' => 'new',
]));
tc_check('a device this account has never seen is still turned away',
         $status === 429 && ($body['error'] ?? '') === 'too_many_attempts',
         $status . ' ' . json_encode($body));

$spend();
[$status, $body] = tc_request($base, 'POST', ['a' => 'login'], json_encode([
    'username' => 'tester', 'password' => 'secret',
    'device_uid' => $device, 'device_name' => 'test',
]));
tc_check('the device that signed in before gets through anyway',
         $status === 200 && !empty($body['token']),
         $status . ' ' . json_encode($body));

$spend();
[$status, $body] = tc_request($base, 'POST', ['a' => 'login'], json_encode([
    'username' => 'tester', 'password' => 'not the password',
    'device_uid' => $device, 'device_name' => 'test',
]));
tc_check('and it still has to know the password',
         $status === 401 && ($body['error'] ?? '') === 'invalid_credentials',
         $status . ' ' . json_encode($body));

$spend();
[$status, $body] = tc_request($base, 'POST', ['a' => 'login'], json_encode([
    'username' => 'somebody-else', 'password' => 'secret',
    'device_uid' => $device, 'device_name' => 'test',
]));
tc_check('a known device under a name it never used is turned away like a stranger',
         $status === 429 && ($body['error'] ?? '') === 'too_many_attempts',
         $status . ' ' . json_encode($body));

// The reserve has to end as well. It is still a password check, and a known
// device is not necessarily its owner's any more - a stolen laptop is one. Were
// the reserve drawn on without being counted, that laptop could guess at the
// password without limit during a flood it caused itself. (Counted, it can
// still spend the reserve for every other account as well; that is a gap of
// its own, see TC_HASH_RESERVE_PER_MINUTE.)
$spend();
tc_write_json($store . '/rate-reserve.dat.php',
              ['win' => intdiv(time(), 60), 'n' => TC_HASH_RESERVE_PER_MINUTE]);
[$status, $body] = tc_request($base, 'POST', ['a' => 'login'], json_encode([
    'username' => 'tester', 'password' => 'secret',
    'device_uid' => $device, 'device_name' => 'test',
]));
tc_check('and once the reserve is spent too, so is the known device',
         $status === 429 && ($body['error'] ?? '') === 'too_many_attempts',
         $status . ' ' . json_encode($body));

// Issue #586. The account is made to look full through its bookkeeping - a
// retired segment with a large size - rather than by writing fifty megabytes.
print("\nAn account that has used up its storage\n");

// A fresh token. The sign-in tests above signed this same device in again,
// which replaces its token, so the one from the start of the file is gone -
// and they spent both allowances on purpose, so those are cleared first.
@unlink($store . '/rate.dat.php');
@unlink($store . '/rate-reserve.dat.php');
[$status, $body] = tc_request($base, 'POST', ['a' => 'login'], json_encode([
    'username' => 'tester', 'password' => 'secret',
    'device_uid' => $device, 'device_name' => 'test',
]));
$token = $body['token'] ?? '';
tc_check('signed in again for this part', $status === 200 && $token !== '',
         $status . ' ' . json_encode($body));

$state = tc_log_state($store, $uid);
$state['retired'][] = ['f' => 'seg-planted.log.php', 'first' => 0, 'last' => 0,
                       'bytes' => TC_ACCOUNT_QUOTA_BYTES, 'n' => 0, 'at' => time()];
tc_write_json(tc_log_state_path($store, $uid), $state);

// A counter higher than anything this device has sent above. An old one would
// be a repeat, and a repeat writes nothing - so it is rightly not refused,
// and would pass for a full account that had stopped refusing anything.
[$status, $body] = tc_request($base, 'POST', ['a' => 'push'], json_encode([
    'base_seq' => 0, 'ops' => [
        ['op' => 'task.set', 'lc' => 999999, 'uid' => sprintf('%016x', 9),
         'f' => ['note' => 'one more']]]]), $token);
tc_check('a push is refused with Insufficient Storage',
         $status === 507 && ($body['error'] ?? '') === 'quota_exceeded',
         $status . ' ' . json_encode($body));
tc_check('and says how full, not only that it is',
         ($body['quota'] ?? null) === TC_ACCOUNT_QUOTA_BYTES
         && (int)($body['usage'] ?? 0) >= TC_ACCOUNT_QUOTA_BYTES,
         json_encode($body));

// Signing in and asking where the log stands still work: a full account can
// still find out what is going on, and still be put right.
[$status, $body] = tc_request($base, 'GET', ['a' => 'head'], null, $token);
tc_check('the cheap poll still answers', $status === 200, $status . ' ' . json_encode($body));

// Issue #587. What the owner's server looks like the moment the new files are
// uploaded: accounts still in the one old list, and nobody there to convert it.
print("\nAn installation from before one file per account\n");

@unlink($store . '/rate.dat.php');
@unlink($store . '/rate-reserve.dat.php');
$oldUid = bin2hex(random_bytes(16));
tc_secure_mkdir(tc_user_dir($store, $oldUid) . '/seen');
tc_write_json(tc_user_dir($store, $oldUid) . '/user.dat.php', ['disabled' => false, 'devices' => []]);
tc_write_json(tc_users_file($store), ['users' => ['oldtimer' => [
    'uid' => $oldUid,
    'pass' => password_hash('from-before', PASSWORD_BCRYPT, ['cost' => TC_BCRYPT_COST_TEST]),
    'created' => date('c'),
]]]);

[$status, $body] = tc_request($base, 'POST', ['a' => 'login'], json_encode([
    'username' => 'oldtimer', 'password' => 'from-before',
    'device_uid' => 'abcdefabcdef0587', 'device_name' => 'test',
]));
tc_check('an account from the old list signs in', $status === 200 && !empty($body['token']),
         $status . ' ' . json_encode($body));
tc_check('and the list was converted on the way',
         !is_file(tc_users_file($store))
         && (tc_read_json(tc_account_file($store, 'oldtimer'))['uid'] ?? null) === $oldUid);

[$status, $body] = tc_request($base, 'POST', ['a' => 'login'], json_encode([
    'username' => 'tester', 'password' => 'secret',
    'device_uid' => $device, 'device_name' => 'test',
]));
tc_check('without losing an account that was already converted', $status === 200,
         $status . ' ' . json_encode($body));

// Issue #588. An invitation made the way setup.php makes one, redeemed over
// ?a=register the way the client redeems it.
print("\nBeing invited\n");

/** A registration from a new device, with whatever the test wants changed. */
function tc_register($base, array $fields)
{
    return tc_request($base, 'POST', ['a' => 'register'], json_encode($fields + [
        'username' => 'invitee', 'password' => 'long enough, surely',
        'device_uid' => 'fedcba9876543210', 'device_name' => 'new laptop',
    ]));
}

// Both allowances spent, as by somebody flooding ?a=login. Registering must
// neither need them nor take from them.
$minute = intdiv(time(), 60);
tc_write_json($store . '/rate.dat.php', ['win' => $minute, 'n' => TC_HASH_BUDGET_PER_MINUTE]);
tc_write_json($store . '/rate-reserve.dat.php', ['win' => $minute, 'n' => TC_HASH_RESERVE_PER_MINUTE]);

$code = tc_invite_create($store, 'for the test')['code'];
// With the name of an account that exists: without a code, nothing about
// names may be learnt here, or this would list them for free.
[$status, $body] = tc_register($base, ['code' => 'ffffffffffffffff', 'username' => 'tester']);
tc_check('a code that was never issued is refused, whatever the name',
         $status === 403 && ($body['error'] ?? '') === 'invalid_invite', $status . ' ' . json_encode($body));
[$status, $body] = tc_register($base, ['code' => $code, 'username' => 'tester']);
tc_check('a name that is taken is refused, without using the code up',
         $status === 409 && ($body['error'] ?? '') === 'username_taken', $status . ' ' . json_encode($body));
[$status, $body] = tc_register($base, ['code' => $code, 'username' => "tester\n"]);
tc_check('and so is the same name with a newline after it',
         $status === 400 && ($body['error'] ?? '') === 'bad_username', $status . ' ' . json_encode($body));
[$status, $body] = tc_register($base, ['code' => $code, 'device_uid' => "fedcba9876543210\n"]);
tc_check('and a device id with one', $status === 400 && ($body['error'] ?? '') === 'bad_device_uid',
         $status . ' ' . json_encode($body));

// As somebody reads it off the setup page and types it in.
[$status, $body] = tc_register($base, ['code' => strtoupper(implode('-', str_split($code, 4)))]);
$invitedToken = $body['token'] ?? '';
tc_check('the code as setup.php shows it makes an account, and signs this device in',
         $status === 200 && $invitedToken !== '' && ($body['username'] ?? '') === 'invitee',
         $status . ' ' . json_encode($body));
tc_check('while every sign-in allowance was spent',
         (tc_read_json($store . '/rate.dat.php')['n'] ?? null) === TC_HASH_BUDGET_PER_MINUTE
         && (tc_read_json($store . '/rate-reserve.dat.php')['n'] ?? null) === TC_HASH_RESERVE_PER_MINUTE);

// And from an allowance with room in it, which is the one that could show a
// unit being taken - a spent one is never written again, whatever asks.
tc_write_json($store . '/rate.dat.php', ['win' => intdiv(time(), 60), 'n' => 0]);
[$status, $body] = tc_register($base, ['code' => tc_invite_create($store)['code'],
                                       'username' => 'invitee2', 'device_uid' => 'fedcba9876543211']);
tc_check('without taking anything from it', $status === 200
         && (tc_read_json($store . '/rate.dat.php')['n'] ?? null) === 0, $status . ' ' . json_encode($body));

[$status, $body] = tc_request($base, 'GET', ['a' => 'ping'], null, $invitedToken);
tc_check('the token it gave works', $status === 200, $status . ' ' . json_encode($body));
[$status, $body] = tc_register($base, ['code' => $code, 'username' => 'second']);
tc_check('the code does not work twice',
         $status === 403 && ($body['error'] ?? '') === 'invalid_invite', $status . ' ' . json_encode($body));

@unlink($store . '/rate.dat.php');
@unlink($store . '/rate-reserve.dat.php');
[$status, $body] = tc_request($base, 'POST', ['a' => 'login'], json_encode([
    'username' => 'invitee', 'password' => 'long enough, surely',
    'device_uid' => '0123456789abcdef', 'device_name' => 'second machine',
]));
tc_check('and the account signs in from another machine with the password chosen',
         $status === 200 && !empty($body['token']), $status . ' ' . json_encode($body));

// Issue #589. An account made from an invitation, that nothing was stored in
// and no device has reached for longer than any of its tokens could last.
print("\nAccounts nobody ever used\n");

$idle = tc_account_write($store, 'nobody-came', 'x', ['invited' => ['note' => 'test', 'issued' => 0]], true)['uid'];
$longAgo = time() - TC_UNUSED_SECONDS - 86400;
$marker = tc_read_json(tc_unused_marker($store, $idle));
$marker['since'] = $longAgo;
tc_write_json(tc_unused_marker($store, $idle), $marker);
touch(tc_unused_marker($store, $idle), $longAgo + TC_UNUSED_SECONDS + 1);
// And one the sweep has to look at and keep: due by its marker, but its
// device was here a moment ago.
$recent = tc_user_find($store, 'invitee2')['uid'];
touch(tc_unused_marker($store, $recent), time() - 60);

[$status, $body] = tc_register($base, ['code' => 'ffffffffffffffff', 'username' => 'sweeper']);
tc_check('a registration without a valid code tidies nothing',
         $status === 403 && tc_user_find($store, 'nobody-came') !== null, $status . ' ' . json_encode($body));

[$status, $body] = tc_register($base, ['code' => tc_invite_create($store)['code'],
                                       'username' => 'sweeper', 'device_uid' => 'fedcba9876543212']);
tc_check('a registration removes an account nobody ever used',
         $status === 200 && tc_user_find($store, 'nobody-came') === null && !is_dir(tc_user_dir($store, $idle)),
         $status . ' ' . json_encode($body));
tc_check('and keeps the one it made, and the ones in use or made by the operator',
         !empty($body['token']) && tc_user_find($store, 'sweeper') !== null
         && tc_user_find($store, 'invitee') !== null && tc_user_find($store, 'tester') !== null);
clearstatcache();
tc_check('including one it looked at, whose device was here a moment ago',
         tc_user_find($store, 'invitee2') !== null && filemtime(tc_unused_marker($store, $recent)) > time());

// A device whose account has gone, as it next reaches the server: the same
// answer as for any token that stopped working, so the client asks for a
// sign-in - and does not believe its work was stored somewhere.
//
// Also one token the device list never recorded, which nothing revokes by
// name: its file outlives the account, and only the account being gone can
// stop it.
$inviteeUid = tc_user_find($store, 'invitee')['uid'];
$stray = tc_token_issue($store, $inviteeUid, '00000000000000aa', 'elsewhere')['token'];
$list = tc_read_json(tc_user_dir($store, $inviteeUid) . '/user.dat.php');
$list['devices'] = array_values(array_filter($list['devices'], function ($d) {
    return $d['device_uid'] !== '00000000000000aa';
}));
tc_write_json(tc_user_dir($store, $inviteeUid) . '/user.dat.php', $list);
tc_account_delete($store, 'invitee', $inviteeUid);
[$status, $body] = tc_request($base, 'GET', ['a' => 'head'], null, $invitedToken);
tc_check('a device of a removed account is told its token no longer works',
         $status === 401 && ($body['error'] ?? '') === 'invalid_token', $status . ' ' . json_encode($body));
[$status, $body] = tc_request($base, 'GET', ['a' => 'head'], null, $stray);
tc_check('and so is one holding a token its device list never recorded',
         $status === 401 && ($body['error'] ?? '') === 'invalid_token'
         && !is_dir(tc_user_dir($store, $inviteeUid)), $status . ' ' . json_encode($body));

// Issue #591. A challenge from ?a=challenge, solved the way the client solves
// it, sent along with a registration. With a code the server does not insist
// on one, but holds one that is sent to every rule - before the code is even
// looked at, so a refused one costs the invitation nothing.
print("\nA proof of work along with it\n");

function tc_challenge($base)
{
    return tc_request($base, 'POST', ['a' => 'challenge'], json_encode(['purpose' => 'register']));
}

function tc_solve($challenge, $bits)
{
    for ($n = 0; ; $n++) {
        if (tc_pow_leading_zero_bits(hash('sha256', $challenge . ':' . $n, true)) >= $bits) {
            return (string)$n;
        }
    }
}

[$status, $body] = tc_request($base, 'GET', ['a' => 'challenge']);
tc_check('a challenge is asked for with POST', $status === 405, (string)$status);
[$status, $body] = tc_request($base, 'POST', ['a' => 'challenge'], json_encode(['purpose' => 'login']));
tc_check('and only for what one is used for',
         $status === 400 && ($body['error'] ?? '') === 'bad_purpose', $status . ' ' . json_encode($body));
[$status, $issued] = tc_challenge($base);
tc_check('one is handed out at the difficulty the server is set to',
         $status === 200 && is_string($issued['challenge'] ?? null)
         && ($issued['bits'] ?? null) === TC_POW_BITS && ($issued['expires_in'] ?? null) === TC_POW_TTL,
         $status . ' ' . json_encode($issued));

$solved = ['challenge' => $issued['challenge'], 'nonce' => tc_solve($issued['challenge'], $issued['bits'])];
[$status, $body] = tc_register($base, ['code' => tc_invite_create($store)['code'], 'username' => 'worker',
                                       'device_uid' => 'fedcba98765432b1', 'pow' => $solved]);
tc_check('a registration that brings one solved is made',
         $status === 200 && !empty($body['token']), $status . ' ' . json_encode($body));

$code = tc_invite_create($store)['code'];
[$status, $body] = tc_register($base, ['code' => $code, 'username' => 'worker2',
                                       'device_uid' => 'fedcba98765432b2', 'pow' => $solved]);
tc_check('the same solution does not count twice',
         $status === 403 && ($body['error'] ?? '') === 'pow_used', $status . ' ' . json_encode($body));

[, $fresh] = tc_challenge($base);
$miss = 0;
while (tc_pow_leading_zero_bits(hash('sha256', $fresh['challenge'] . ':' . $miss, true)) >= $fresh['bits']) {
    $miss++;
}
$miss = (string)$miss;
[$status, $body] = tc_register($base, ['code' => $code, 'username' => 'worker2', 'device_uid' => 'fedcba98765432b2',
                                       'pow' => ['challenge' => $fresh['challenge'], 'nonce' => $miss]]);
tc_check('one that was not worked for is refused',
         $status === 403 && ($body['error'] ?? '') === 'pow_invalid', $status . ' ' . json_encode($body));
$shapes = ['a sentence' => 'solved, honestly', 'null' => null, 'an empty list' => [],
           'a challenge without a nonce' => ['challenge' => $fresh['challenge']]];
foreach ($shapes as $what => $shape) {
    // Sent is sent: an empty one is not the same as none, or leaving it
    // empty would be the way round it once it is demanded.
    [$status, $body] = tc_register($base, ['code' => $code, 'username' => 'worker2',
                                           'device_uid' => 'fedcba98765432b2', 'pow' => $shape]);
    tc_check('and so is ' . $what . ' in its place',
             $status === 403 && ($body['error'] ?? '') === 'pow_invalid', $status . ' ' . json_encode($body));
}

// One the server issued, but longer ago than it lasts. The test shares the
// store's key, so it can be made here as the server would have made it then.
$old = tc_pow_challenge($store, 'register', time() - TC_POW_TTL - 5);
[$status, $body] = tc_register($base, ['code' => $code, 'username' => 'worker2', 'device_uid' => 'fedcba98765432b2',
                                       'pow' => ['challenge' => $old['challenge'],
                                                 'nonce' => tc_solve($old['challenge'], $old['bits'])]]);
tc_check('one solved too late is told so, for the client to fetch a fresh one',
         $status === 403 && ($body['error'] ?? '') === 'pow_expired', $status . ' ' . json_encode($body));
[$status, $body] = tc_request($base, 'POST', ['a' => 'challenge'], json_encode([]));
tc_check('a challenge asked for without saying what for is refused',
         $status === 400 && ($body['error'] ?? '') === 'bad_purpose', $status . ' ' . json_encode($body));

// The key unreadable: no challenge, and a solution that comes anyway cannot
// be judged - both a moment's 'busy' to the client, which then registers
// without one.
rename(tc_pow_key_dir($store), $store . '/pow-key-aside');
tc_secure_mkdir(tc_pow_key_dir($store));
[$status, $body] = tc_challenge($base);
tc_check('without a key that can be read there are no challenges',
         $status === 503 && ($body['error'] ?? '') === 'busy', $status . ' ' . json_encode($body));
[$status, $body] = tc_register($base, ['code' => $code, 'username' => 'worker2', 'device_uid' => 'fedcba98765432b2',
                                       'pow' => ['challenge' => $fresh['challenge'], 'nonce' => '1']]);
tc_check('and a solution cannot be judged',
         $status === 503 && ($body['error'] ?? '') === 'busy', $status . ' ' . json_encode($body));
rmdir(tc_pow_key_dir($store));
rename($store . '/pow-key-aside', tc_pow_key_dir($store));

// The code went through all of that unharmed: the work is checked first.
[$status, $body] = tc_register($base, ['code' => $code, 'username' => 'worker2', 'device_uid' => 'fedcba98765432b2',
                                       'pow' => ['challenge' => $fresh['challenge'],
                                                 'nonce' => tc_solve($fresh['challenge'], $fresh['bits'])]]);
tc_check('none of those used up the invitation', $status === 200, $status . ' ' . json_encode($body));

// A client from before this sends none, and an invitation is enough.
[$status, $body] = tc_register($base, ['code' => tc_invite_create($store)['code'], 'username' => 'worker3',
                                       'device_uid' => 'fedcba98765432b3']);
tc_check('a registration without one is still made with a code',
         $status === 200 && !empty($body['token']), $status . ' ' . json_encode($body));

printf("\n%d tests, %d failed\n", $GLOBALS['tc_tests'], $GLOBALS['tc_failed']);
exit($GLOBALS['tc_failed'] === 0 ? 0 : 1);
