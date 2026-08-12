<?php
/**
 * TimeControl - host probe
 * =========================
 *
 * A throwaway diagnostic. It answers the questions that decide whether the
 * sync server can safely live on a given shared webspace, by measuring them
 * on the actual account rather than trusting documentation.
 *
 * The one that matters most: does PHP run as YOUR user, or as an account
 * shared with every other customer on the machine? The whole storage design
 * rests on chmod 0600 meaning something. Where all tenants' PHP runs under
 * one uid and open_basedir is unset, a neighbour's script reads your files
 * as easily as your own does, and file permissions protect nothing.
 *
 * USAGE
 *   1. Edit PROBE_KEY below - any value, just not the default.
 *   2. Upload this single file by FTP into your web directory.
 *   3. Open https://<your-host>/tcprobe.php?key=<your value>
 *   4. Send the output back.
 *   5. DELETE THE FILE. It reports paths and configuration that are useful
 *      to an attacker; it is meant to exist for minutes, not to stay.
 *
 * It creates a few files while running and removes them again. The only one
 * that may survive is the reachability canary, and only when PHP cannot make
 * outbound requests to fetch it itself - the page says so explicitly and
 * tells you what to do.
 *
 * Nothing here writes anything a later install depends on.
 */

// Ships empty on purpose. The gate below is a length check, and an empty
// value fails it - so an unedited copy refuses every call. A shipped key long
// enough to pass its own gate would arm the probe for anyone who uploaded it
// without reading, with a key published in this repository for all to see.
const PROBE_KEY   = '';
const PROBE_BUILD = 3;

// ---------------------------------------------------------------------------

// The gate is the LENGTH of the key, not a comparison against the shipped
// default. That is deliberate. Replacing every occurrence of the default
// string is the obvious way to configure a file like this, and a sentinel
// comparison written as PROBE_KEY === 'CHANGE-ME' gets rewritten along with
// it - leaving a probe that refuses every call, for a reason nothing on the
// page explains and that looks exactly like the file not being there.
// A length check has nothing for a search-and-replace to break.
if (strlen(PROBE_KEY) < 12) {
    header('Content-Type: text/plain; charset=utf-8');
    echo "TimeControl host probe (build " . PROBE_BUILD . ")\n\n";
    echo "The file is uploaded and PHP is running it. It refuses to go further\n";
    echo "because PROBE_KEY is only " . strlen(PROBE_KEY) . " characters long.\n\n";
    echo "Edit the line near the top of this file:\n\n";
    echo "    const PROBE_KEY = '" . PROBE_KEY . "';\n\n";
    echo "Put at least 12 characters between the quotes - change ONLY this one\n";
    echo "line - upload it again, and call this URL with ?key=<that value>.\n\n";
    echo "The report reveals paths and configuration, so the key is what keeps\n";
    echo "it from being readable by anyone who finds the URL.\n";
    exit;
}

// Once a real key is set, a wrong or missing one gets nothing: an armed probe
// should not confirm its own existence to someone guessing at URLs.
if (!isset($_GET['key']) || !hash_equals(PROBE_KEY, (string)$_GET['key'])) {
    http_response_code(404);
    exit;
}

header('Content-Type: text/plain; charset=utf-8');
header('X-Robots-Tag: noindex, nofollow');

$results = [];
$cleanup  = [];

function say($section) {
    echo "\n" . str_repeat('=', 66) . "\n" . $section . "\n" . str_repeat('=', 66) . "\n";
}

function item($label, $value, $verdict = null) {
    $line = sprintf('  %-34s %s', $label . ':', $value);
    if ($verdict !== null) {
        $line .= '   [' . $verdict . ']';
    }
    echo $line . "\n";
}

echo "TimeControl host probe\n";
echo 'run at ' . date('c') . "\n";

// ---------------------------------------------------------------------------
say('1. PHP');

$phpOk = PHP_VERSION_ID >= 70400;
item('version', PHP_VERSION, $phpOk ? 'OK' : 'TOO OLD - need 7.4+');
item('SAPI', PHP_SAPI);
item('max_execution_time', ini_get('max_execution_time') . ' s');
item('memory_limit', ini_get('memory_limit'));
item('open_basedir', ini_get('open_basedir') ?: '(not set)');

$needed = ['random_bytes', 'password_hash', 'password_verify', 'hash_equals',
           'json_encode', 'json_decode', 'flock', 'rename', 'file_put_contents'];
$missing = array_values(array_filter($needed, function ($f) { return !function_exists($f); }));
item('required functions', $missing ? 'MISSING: ' . implode(', ', $missing) : 'all present',
     $missing ? 'PROBLEM' : 'OK');

// bcrypt cost 12 is the intended setting; measure what it actually costs here,
// because a slow shared CPU turns the login endpoint into its own bottleneck.
if (function_exists('password_hash')) {
    $t0 = microtime(true);
    password_hash('probe-timing-only', PASSWORD_BCRYPT, ['cost' => 12]);
    $ms = (microtime(true) - $t0) * 1000;
    item('bcrypt cost 12', sprintf('%.0f ms', $ms),
         $ms < 1500 ? 'OK' : 'SLOW - consider cost 11');
}

// ---------------------------------------------------------------------------
say('2. Identity - who does PHP run as?');

$uid = function_exists('posix_geteuid') ? posix_geteuid() : null;
if ($uid !== null && function_exists('posix_getpwuid')) {
    $pw = posix_getpwuid($uid);
    item('effective user', ($pw['name'] ?? '?') . ' (uid ' . $uid . ')');
    item('home directory', $pw['dir'] ?? '(unknown)');
} else {
    // ext-posix is commonly disabled on shared hosting. Fall back to asking
    // the filesystem: create a file, see who ends up owning it.
    item('ext-posix', 'not available - deriving from a created file');
}
item('get_current_user()', function_exists('get_current_user') ? get_current_user() : '?');

$probeDir = __DIR__ . '/tcprobe_tmp_' . bin2hex(random_bytes(4));
$dirMade  = @mkdir($probeDir, 0700);
if ($dirMade) {
    $cleanup[] = $probeDir;
    $f = $probeDir . '/owner_test';
    @file_put_contents($f, 'x');
    if (is_file($f)) {
        $cleanup[] = $f;
        $st = @stat($f);
        item('files are owned by uid', $st ? (string)$st['uid'] : '?');
        item('files are owned by gid', $st ? (string)$st['gid'] : '?');
        item('umask / resulting mode', sprintf('%04o', @fileperms($f) & 0777),
             (@fileperms($f) & 0077) ? 'GROUP/WORLD READABLE' : 'OK - owner only');
        item('directory mode', sprintf('%04o', @fileperms($probeDir) & 0777));
    }
} else {
    item('mkdir in web directory', 'FAILED', 'PROBLEM');
}

echo "\n  NOTE: a uid shared with other customers is the go/no-go. If PHP here\n";
echo "  runs as a generic account (www-data, apache, wwwrun) AND open_basedir\n";
echo "  is not set, then 0600 protects nothing from a co-tenant's script.\n";

// ---------------------------------------------------------------------------
say('3. Can the store live above the document root?');

$docRoot = $_SERVER['DOCUMENT_ROOT'] ?? '';
item('DOCUMENT_ROOT', $docRoot ?: '(unknown)');
item('script directory', __DIR__);

$above     = dirname($docRoot ?: __DIR__);
$aboveTest = $above . '/tcprobe_above_' . bin2hex(random_bytes(4));
if ($docRoot && @mkdir($aboveTest, 0700)) {
    $cleanup[] = $aboveTest;
    item('write above docroot', 'YES - ' . $above, 'PREFERRED LAYOUT AVAILABLE');
} else {
    item('write above docroot', 'no (' . $above . ')',
         'FALLBACK LAYOUT - store goes inside the web directory');
}

// ---------------------------------------------------------------------------
say('4. Locking and atomic writes');

if ($dirMade) {
    $lockFile = $probeDir . '/lock_test';
    $cleanup[] = $lockFile;
    $fh = @fopen($lockFile, 'c');
    if ($fh) {
        $got = @flock($fh, LOCK_EX | LOCK_NB);
        item('flock LOCK_EX|LOCK_NB', $got ? 'acquired' : 'FAILED', $got ? 'OK' : 'PROBLEM');
        if ($got) { @flock($fh, LOCK_UN); }
        @fclose($fh);
        echo "\n  CAVEAT: flock() succeeding here does NOT prove it works. On some NFS-\n";
        echo "  backed hosting it reports success while locking nothing. It cannot be\n";
        echo "  tested from a single request - the design must not depend on locking\n";
        echo "  alone for anything that would corrupt data if it silently failed.\n\n";
    }

    $src = $probeDir . '/rename_src';
    $dst = $probeDir . '/rename_dst';
    @file_put_contents($src, 'payload');
    @file_put_contents($dst, 'old');
    $renamed = @rename($src, $dst);
    $cleanup[] = $dst;
    item('rename() over existing file',
         $renamed && @file_get_contents($dst) === 'payload' ? 'works' : 'FAILED',
         $renamed ? 'OK' : 'PROBLEM');
}

// ---------------------------------------------------------------------------
say('5. Outbound HTTP - can the server verify itself?');

$hasCurl   = function_exists('curl_init');
$hasFopen  = filter_var(ini_get('allow_url_fopen'), FILTER_VALIDATE_BOOLEAN);
item('ext-curl', $hasCurl ? 'available' : 'not available');
item('allow_url_fopen', $hasFopen ? 'on' : 'off');
item('outbound HTTP possible', ($hasCurl || $hasFopen) ? 'YES' : 'NO',
     ($hasCurl || $hasFopen) ? 'OK' : 'MANUAL CHECK NEEDED');

// ---------------------------------------------------------------------------
say('6. Is a store directory reachable over the web?');

$scheme  = (!empty($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off') ? 'https' : 'http';
$host    = $_SERVER['HTTP_HOST'] ?? 'localhost';
$baseUrl = $scheme . '://' . $host . rtrim(dirname($_SERVER['SCRIPT_NAME'] ?? '/'), '/');
item('request scheme', $scheme, $scheme === 'https' ? 'OK' : 'NOT HTTPS - see below');
item('base URL', $baseUrl);

$canaryToken = bin2hex(random_bytes(16));
$canaryUrl   = null;
if ($dirMade) {
    // .htaccess first, then the file it is supposed to be hiding.
    @file_put_contents($probeDir . '/.htaccess',
        "Options -Indexes\n" .
        "<IfModule mod_authz_core.c>\n  Require all denied\n</IfModule>\n" .
        "<IfModule !mod_authz_core.c>\n  Order allow,deny\n  Deny from all\n</IfModule>\n");
    $cleanup[] = $probeDir . '/.htaccess';

    @file_put_contents($probeDir . '/canary.json', json_encode(['marker' => $canaryToken]));
    $cleanup[] = $probeDir . '/canary.json';

    $canaryUrl = $baseUrl . '/' . basename($probeDir) . '/canary.json';

    // Three outcomes, never two. A request that did not complete says nothing
    // about whether the file is reachable - reporting that as "protected"
    // would be asserting the very thing this check exists to demonstrate,
    // and would wave through a store that is in fact served to the world.
    $verdict = 'unknown';
    $detail  = null;

    if ($hasCurl) {
        $ch = curl_init($canaryUrl);
        curl_setopt_array($ch, [
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_TIMEOUT        => 5,
            CURLOPT_SSL_VERIFYPEER => true,
            CURLOPT_FOLLOWLOCATION => false,
        ]);
        $body = curl_exec($ch);
        $code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
        $err  = curl_error($ch);
        // No curl_close(): it has had no effect since PHP 8.0 and is
        // deprecated as of 8.5, where calling it prints a warning into the
        // middle of this report.
        if ($body === false) {
            $detail = 'request failed - ' . $err;
        } else {
            $detail  = 'HTTP ' . $code;
            $verdict = (strpos((string)$body, $canaryToken) !== false) ? 'leaked' : 'protected';
        }
    } elseif ($hasFopen) {
        $body = @file_get_contents($canaryUrl);
        if ($body === false) {
            $detail = 'request failed';
        } else {
            $detail  = 'fetched';
            $verdict = (strpos((string)$body, $canaryToken) !== false) ? 'leaked' : 'protected';
        }
    }

    if ($verdict === 'leaked') {
        item('canary fetch result', $detail);
        item('.htaccess protection', 'INEFFECTIVE', 'STORE MUST NOT LIVE IN THE WEB DIRECTORY');
    } elseif ($verdict === 'protected') {
        item('canary fetch result', $detail);
        item('.htaccess protection', 'effective', 'OK');
    } else {
        if ($detail !== null) {
            item('canary fetch result', $detail);
            item('.htaccess protection', 'UNKNOWN', 'MUST BE CHECKED BY HAND');
        }
        echo "\n  The self-check could not complete, so this must be checked by hand.\n";
        echo "  Open this URL in a browser:\n\n    " . $canaryUrl . "\n\n";
        echo "  Expected: 403 Forbidden (or 404).\n";
        echo "  If you instead see a JSON document, .htaccess is being ignored on\n";
        echo "  this host and the store must NOT be placed inside the web directory.\n";
        echo "  This directory is left in place so you can test it - delete\n";
        echo "  '" . basename($probeDir) . "' by FTP afterwards.\n\n";
        // Keep the directory so the manual check is possible.
        $cleanup = array_values(array_filter($cleanup, function ($p) use ($probeDir) {
            return strpos($p, $probeDir) !== 0;
        }));
    }
}

if ($scheme !== 'https') {
    echo "\n  This request arrived over plain HTTP. A bearer token sent this way is\n";
    echo "  readable by anyone on the path. TLS is not optional for the sync server -\n";
    echo "  check whether the hosting package includes a certificate.\n";
}

// ---------------------------------------------------------------------------
say('7. Proxy headers (do NOT trust these blindly)');

foreach (['REMOTE_ADDR', 'HTTP_X_FORWARDED_FOR', 'HTTP_X_REAL_IP', 'HTTP_CF_CONNECTING_IP'] as $h) {
    item($h, isset($_SERVER[$h]) ? (string)$_SERVER[$h] : '(absent)');
}
echo "\n  If REMOTE_ADDR is a fixed internal address, the TLS terminator sits in\n";
echo "  front and per-IP rate limiting would lump every client together.\n";

// ---------------------------------------------------------------------------
// Clean up everything created, deepest path first.
usort($cleanup, function ($a, $b) { return strlen($b) - strlen($a); });
$left = [];
foreach ($cleanup as $path) {
    if (is_dir($path)) {
        if (!@rmdir($path)) { $left[] = $path; }
    } elseif (is_file($path)) {
        if (!@unlink($path)) { $left[] = $path; }
    }
}

say('Done');
if ($left) {
    echo "  Could not remove:\n";
    foreach ($left as $p) { echo '    ' . $p . "\n"; }
    echo "  Delete these by FTP.\n";
} else {
    echo "  All temporary files removed.\n";
}
echo "\n  NOW DELETE tcprobe.php.\n";
