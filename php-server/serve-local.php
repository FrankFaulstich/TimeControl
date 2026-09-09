<?php
/**
 * Puts the real server up on this machine, with one throwaway account, so the
 * check-*.py scripts can be run against php-server/tc itself.
 *
 * Why this exists
 * ---------------
 * Everything on the client side had been verified against a stand-in that
 * reimplements this server in Python. A stand-in written from the same
 * reading of the contract as the client carries the same misreadings, and
 * they cancel out invisibly. Running the checks against the code that
 * actually ships is the only way to see those - and it should not require a
 * live account on somebody's server to do it.
 *
 * What this is not
 * ----------------
 * Not a way to run TimeControl. There is no TLS here: PHP's built-in server
 * cannot do it, so a one-line router tells index.php the request arrived over
 * HTTPS. index.php's own refusal of plain HTTP is left exactly as it is - the
 * router is what stands in front of it, the same arrangement test-endpoints.php
 * has used all along. Everything lives in a temporary directory and is thrown
 * away when this stops; the checkout is not written to.
 *
 * Usage
 * -----
 *   php php-server/serve-local.php              # a free port is chosen
 *   php php-server/serve-local.php 8613         # or name one
 *
 * It prints the address and the account, then stays in the foreground.
 * In another terminal:
 *
 *   TC_SYNC_URL=http://127.0.0.1:<port>/ python3 php-server/check-oplog.py
 *
 * Ctrl-C here removes the installation again.
 */

require __DIR__ . '/tc/lib/store.php';

const TC_LOCAL_BCRYPT_COST = 4;   // a throwaway account, not a password store

function tc_local_copytree($from, $to)
{
    tc_secure_mkdir($to);
    foreach (scandir($from) as $entry) {
        if ($entry === '.' || $entry === '..') {
            continue;
        }
        $a = $from . '/' . $entry;
        $b = $to . '/' . $entry;
        is_dir($a) ? tc_local_copytree($a, $b) : copy($a, $b);
    }
}

function tc_local_rmtree($path)
{
    if (!is_dir($path)) {
        @unlink($path);
        return;
    }
    foreach (scandir($path) as $entry) {
        if ($entry !== '.' && $entry !== '..') {
            tc_local_rmtree($path . '/' . $entry);
        }
    }
    @rmdir($path);
}

$port = isset($argv[1]) ? (int)$argv[1] : 0;
if ($port === 0) {
    // Ask the operating system for one nobody is using, rather than guessing
    // and failing with a bind error that reads like something else.
    $probe = stream_socket_server('tcp://127.0.0.1:0', $errno, $errstr);
    if (!$probe) {
        fwrite(STDERR, "Could not find a free port: $errstr\n");
        exit(1);
    }
    $port = (int)explode(':', stream_socket_get_name($probe, false))[1];
    fclose($probe);
}

$user = 'localcheck';
$pass = bin2hex(random_bytes(9));

$root  = sys_get_temp_dir() . '/tc-serve-local-' . bin2hex(random_bytes(6));
$web   = $root . '/tc';
$store = $root . '/store';

tc_secure_mkdir($root);
tc_local_copytree(__DIR__ . '/tc', $web);
tc_secure_mkdir($store);
// The subdirectories setup.php lays down. Without tokens/ every sign-in
// answers "busy", which is a confusing way to be told the store is missing.
foreach (['tokens', 'users'] as $sub) {
    tc_secure_mkdir($store . '/' . $sub);
}
file_put_contents($web . '/config.php',
    "<?php return " . var_export(['store' => $store], true) . ";\n");

// One account, laid out the way setup.php lays one down.
$uid = bin2hex(random_bytes(16));
tc_write_json($store . '/users.dat.php', ['users' => [$user => [
    'uid'     => $uid,
    'pass'    => password_hash($pass, PASSWORD_BCRYPT,
                               ['cost' => TC_LOCAL_BCRYPT_COST]),
    'created' => date('c'),
]]]);
tc_secure_mkdir($store . '/users/' . $uid);
tc_secure_mkdir($store . '/users/' . $uid . '/seen');
tc_write_json($store . '/users/' . $uid . '/user.dat.php',
              ['disabled' => false, 'devices' => []]);

file_put_contents($root . '/router.php',
    "<?php\n\$_SERVER['HTTPS'] = 'on';\nrequire "
  . var_export($web . '/index.php', true) . ";\n");

printf("Server   http://127.0.0.1:%d/\nAccount  %s\nPassword %s\nStore    %s\n\n",
       $port, $user, $pass, $store);
printf("  TC_SYNC_URL=http://127.0.0.1:%d/ python3 php-server/check-oplog.py\n\n",
       $port);
echo "Ctrl-C removes the installation again.\n\n";

$cleanup = function () use ($root) {
    tc_local_rmtree($root);
};
register_shutdown_function($cleanup);
if (function_exists('pcntl_signal')) {
    pcntl_async_signals(true);
    foreach ([SIGINT, SIGTERM] as $signal) {
        pcntl_signal($signal, function () use ($cleanup) {
            $cleanup();
            exit(0);
        });
    }
}

// Foreground on purpose: this is a thing you start, use, and stop.
passthru(sprintf('%s -S 127.0.0.1:%d %s',
                 escapeshellarg(PHP_BINARY), $port,
                 escapeshellarg($root . '/router.php')));
