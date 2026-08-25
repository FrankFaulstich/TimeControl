<?php
/**
 * Tests the checks the installer refuses to install without.
 *
 *     php php-server/test-setup.php
 *
 * The store being unreadable over the web is the property the whole storage
 * design rests on, and it used to be taken on trust for the location outside
 * the web directory: two levels above tc/ had to be outside the web space, so
 * there was nothing to prove. That holds when tc/ sits directly under the
 * document root and fails the moment it does not - with tc/ at /apps/tc, two
 * levels up IS the document root, and the store went there with no .htaccess
 * and no check while the installer reported success.
 *
 * Which address gets probed for which layout is therefore the thing worth
 * testing, and it is pure - no server needed. What does need one is whether
 * a fetch can tell a served directory from an unserved one, and PHP brings
 * a web server along for that. Nothing here is written inside the repository.
 *
 * setup.php itself is deliberately not driven: it insists on an operator
 * passphrase and on HTTPS, both rightly, and the built-in server has no TLS.
 * That is why these functions live in lib/probe.php rather than inside it.
 */

require_once __DIR__ . '/tc/lib/probe.php';

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
    printf("  FAIL  %s%s\n", $label, $detail === '' ? '' : "\n        " . $detail);
}

function tc_rmtree($path)
{
    if (!is_dir($path) || is_link($path)) {
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

print("Which address the store would be served at\n");

// The layout that used to be trusted, and the one that broke it. tc/ is a
// directory deep inside the web space, so two levels above it is the document
// root - a place the world can read.
$urls = tc_public_urls_for('/var/www/html/tcstore', '/var/www/html/apps/tc',
                           'https://host.example/apps/tc', 'canary.txt');
tc_check('a nested tc/ yields the address that really reaches the store',
         in_array('https://host.example/tcstore/canary.txt', $urls, true),
         implode(' ', $urls));

// The common layout. Two levels above tc/ is above the document root, so no
// URL leads there - but the guess is still made and still fetched for,
// because being sure requires asking rather than reasoning.
$urls = tc_public_urls_for('/home/u/tcstore', '/home/u/public_html/tc',
                           'https://host.example/tc', 'canary.txt');
tc_check('a flat tc/ still produces something to fetch for', $urls !== [],
         'nothing would be checked at all');
tc_check('and it is the reading where the document root is the site root',
         in_array('https://host.example/tcstore/canary.txt', $urls, true),
         implode(' ', $urls));

// An alias or a per-directory root makes the URL path say nothing about how
// deep the directory is, so that reading is always among the ones tried.
$urls = tc_public_urls_for('/srv/app/tcstore', '/srv/app/web/tc',
                           'https://host.example/deep/down/here/tc', 'canary.txt');
tc_check('the site-root reading is always tried',
         in_array('https://host.example/tcstore/canary.txt', $urls, true),
         implode(' ', $urls));

// Inside the web directory there is no guessing: one address, and it is exact.
$urls = tc_public_urls_for('/var/www/html/tc/store-abc', '/var/www/html/tc',
                           'https://host.example/tc', 'canary.txt');
tc_check('a store inside tc/ has exactly one, exact address',
         $urls === ['https://host.example/tc/store-abc/canary.txt'],
         implode(' ', $urls));

$urls = tc_public_urls_for('/var/www/tcstore', '/var/www/html/tc',
                           'https://host.example:8443/html/tc', 'canary.txt');
tc_check('a port on the address is carried through',
         $urls && strpos($urls[0], 'https://host.example:8443/') === 0,
         implode(' ', $urls));

print("\nWhat a fetch can actually tell apart\n");

$root = sys_get_temp_dir() . '/tc-setup-test-' . bin2hex(random_bytes(6));
@mkdir($root . '/tcstore', 0755, true);
$marker = bin2hex(random_bytes(16));
file_put_contents($root . '/tcstore/canary.txt', 'tc-canary ' . $marker);
// Deliberately no index.html anywhere: the built-in server answers an
// unmatched path with the document root's index page and a 200 if there is
// one, and every "not found" below would then be a 200 carrying a page.
// That is how the missing-curl test first passed without testing anything.

$port = 8100 + random_int(0, 800);
$log = $root . '/server.log';
$server = proc_open(
    sprintf('%s -S 127.0.0.1:%d -t %s', escapeshellarg(PHP_BINARY), $port, escapeshellarg($root)),
    [1 => ['file', $log, 'a'], 2 => ['file', $log, 'a']], $pipes, $root);

register_shutdown_function(function () use ($server, $root) {
    if (is_resource($server)) {
        proc_terminate($server);
        proc_close($server);
    }
    tc_rmtree($root);
});

$origin = sprintf('http://127.0.0.1:%d', $port);
$up = false;
for ($i = 0; $i < 60; $i++) {
    usleep(100000);
    if (tc_is_served($origin . '/tcstore/canary.txt', $marker) === true) {
        $up = true;
        break;
    }
}
if (!$up) {
    fwrite(STDERR, "the built-in server did not come up\n" . (string)@file_get_contents($log));
    exit(1);
}

// A store that is being served. The installer must refuse this location, and
// the message has to name the address so the operator can look for themselves.
$problem = tc_prove_unreadable($root . '/tcstore', $root . '/tc', $origin . '/tc', $marker);
tc_check('a readable store is refused', $problem !== null);
tc_check('and the address is named', $problem && strpos($problem, '/tcstore/canary.txt') !== false,
         (string)$problem);

// One that is not. Nothing is served from there, which is what an .htaccess
// that works, or a directory above the document root, both look like.
$problem = tc_prove_unreadable($root . '/nowhere', $root . '/tc', $origin . '/tc', $marker);
tc_check('a store nothing serves is accepted', $problem === null, (string)$problem);

// And one where the question could not be put at all. "No answer" is not the
// same as "no", and treating it as one is how a store ends up somewhere
// nobody ever checked - the whole shape of the bug this replaced.
$problem = tc_prove_unreadable($root . '/tcstore', $root . '/tc',
                               'http://127.0.0.1:1/tc', $marker);
tc_check('an address that cannot be reached is not read as a no',
         $problem !== null, 'silence was taken for proof');

// The positive control. Without it, "nothing came back" and "we asked the
// wrong address" are the same answer - and only one of them means the store
// is safe.
tc_check('fetching this directory is confirmed when it works',
         tc_prove_fetching_works($root, $origin) === null);

$refused = tc_prove_fetching_works($root, $origin . '/there-is-nothing-here');
tc_check('and reported when the address leads nowhere', $refused !== null);
tc_check('with nothing installed on the strength of a question never asked',
         $refused && strpos($refused, 'Nothing has been installed') !== false,
         (string)$refused);

print("\nThe fallback used where curl is absent\n");

// In a second interpreter with curl switched off, because that is the only
// way to reach this path - tc_fetch_verdict prefers curl wherever it exists,
// so running it here would test the branch that was never broken.
//
// Without ignore_errors, file_get_contents turns a 403 or a 404 into false,
// which arrives as "could not ask". A properly protected store would then
// report as unverifiable, and the installer's refusal would fire on exactly
// the hosts that are in good order.
function tc_verdict_without_curl($url, $marker)
{
    $script = sprintf(
        'require %s; echo tc_fetch_verdict(%s, %s);',
        var_export(__DIR__ . '/tc/lib/probe.php', true),
        var_export($url, true), var_export($marker, true));
    $command = sprintf('%s -d disable_functions=curl_init -r %s 2>/dev/null',
                       escapeshellarg(PHP_BINARY), escapeshellarg($script));
    return trim((string)shell_exec($command));
}

$without = trim((string)shell_exec(sprintf(
    '%s -d disable_functions=curl_init -r %s 2>/dev/null', escapeshellarg(PHP_BINARY),
    escapeshellarg('var_dump(function_exists("curl_init"));'))));
tc_check('curl really is out of the way for this', $without === 'bool(false)',
         'the second interpreter still has curl, so the fallback is not what ran: ' . $without);
tc_check('a served file still reads as leaked',
         tc_verdict_without_curl($origin . '/tcstore/canary.txt', $marker) === 'leaked');
tc_check('and a 404 reads as protected, not as unanswerable',
         tc_verdict_without_curl($origin . '/definitely-not-here.txt', $marker) === 'protected',
         tc_verdict_without_curl($origin . '/definitely-not-here.txt', $marker));

printf("\n%d tests, %d failed\n", $GLOBALS['tc_tests'], $GLOBALS['tc_failed']);
exit($GLOBALS['tc_failed'] === 0 ? 0 : 1);
