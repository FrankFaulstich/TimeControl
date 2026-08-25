<?php
/**
 * Finding out what this host will actually hand to an outsider.
 *
 * The whole storage design rests on one property: the store is not readable
 * over the web. That property is not something a program can arrange - it
 * depends on .htaccess being honoured, on where the document root sits, on
 * whether a rewrite or an alias is in the way. So it is not arranged, it is
 * measured, by asking the web server for a file and seeing what comes back.
 *
 * These live apart from setup.php so they can be exercised on their own. The
 * installer cannot be: it refuses to start without an operator passphrase and
 * insists on HTTPS, both rightly, and both of which make the one part worth
 * testing - which address gets probed for which layout - unreachable behind
 * them.
 */

/**
 * @return string 'protected' | 'leaked' | 'unknown'
 */
function tc_fetch_verdict($url, $marker)
{
    $body = false;
    if (function_exists('curl_init')) {
        $ch = curl_init($url);
        curl_setopt_array($ch, [
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_TIMEOUT        => 8,
            CURLOPT_FOLLOWLOCATION => false,
        ]);
        $body = curl_exec($ch);
    } elseif (filter_var(ini_get('allow_url_fopen'), FILTER_VALIDATE_BOOLEAN)) {
        // ignore_errors, or the one answer this check is hoping for is the
        // one it cannot read. Without it file_get_contents turns a 403 or a
        // 404 into false, which arrives here as "could not ask" - so on a
        // host without curl, a store that is properly protected reports as
        // unverifiable, and the installer's refusal would fire on exactly
        // the installations that are in good order.
        $context = stream_context_create(['http' => [
            'ignore_errors'   => true,
            'timeout'         => 8,
            'follow_location' => 0,
        ]]);
        $body = @file_get_contents($url, false, $context);
    }
    if ($body === false) {
        return 'unknown';
    }
    return (strpos((string)$body, $marker) !== false) ? 'leaked' : 'protected';
}

/**
 * Whether a URL hands the file's contents back.
 *
 * The same question as tc_fetch_verdict, asked in the direction its callers
 * think in - one of them wants the answer to be yes.
 *
 * @return bool|null Null when the question could not be put to the server.
 */
function tc_is_served($url, $marker)
{
    $verdict = tc_fetch_verdict($url, $marker);
    if ($verdict === 'unknown') {
        return null;
    }
    return $verdict === 'leaked';
}

/**
 * Every public address that could serve a file inside a store directory.
 *
 * For a store under the web directory there is exactly one, and it is exact.
 * For one outside, there is guesswork: which URL reaches it depends on where
 * the document root sits, and this code does not get to know that - the host
 * probe found DOCUMENT_ROOT and the directory the installer runs from
 * pointing at different places. So both readings are tried, and the location
 * is only accepted when neither of them hands the file over.
 *
 * @param string $path    The store directory.
 * @param string $webDir  The directory the installer is served from.
 * @param string $baseUrl The public address of $webDir, no trailing slash.
 * @param string $file    Name of the file inside $path to address.
 * @return array URLs, most likely first.
 */
function tc_public_urls_for($path, $webDir, $baseUrl, $file)
{
    $webDir = rtrim($webDir, '/');
    if (strpos($path, $webDir . '/') === 0) {
        return [rtrim($baseUrl, '/') . '/' . substr($path, strlen($webDir) + 1) . '/' . $file];
    }

    $parts  = parse_url($baseUrl);
    $origin = $parts['scheme'] . '://' . $parts['host']
            . (isset($parts['port']) ? ':' . $parts['port'] : '');
    $segments = array_values(array_filter(
        explode('/', trim($parts['path'] ?? '', '/')), 'strlen'));

    // How far above the web directory the store sits. Two, for the location
    // the installer prefers; worked out rather than assumed, so a different
    // candidate does not silently get the wrong address.
    $up = 0;
    for ($above = dirname($webDir); $above !== dirname($above); $above = dirname($above)) {
        $up++;
        if (strpos($path, $above . '/') === 0) {
            break;
        }
    }

    $urls = [];
    // If the URL path has that many segments to give up, the store is inside
    // the web space and this is where it would be served from.
    if ($up > 0 && count($segments) >= $up) {
        $prefix = implode('/', array_slice($segments, 0, count($segments) - $up));
        $urls[] = rtrim($origin . '/' . $prefix, '/') . '/' . basename($path) . '/' . $file;
    }
    // And the reading where the document root is the site root, which is what
    // an alias or a per-directory root produces.
    $urls[] = $origin . '/' . basename($path) . '/' . $file;

    return array_values(array_unique($urls));
}

/**
 * Establishes that a store directory cannot be read over the web.
 *
 * @param string $marker The random string written into the canary.
 * @return string|null Why the location was refused, or null when every
 *                     address that could reach it declined to.
 */
function tc_prove_unreadable($path, $webDir, $baseUrl, $marker)
{
    foreach (tc_public_urls_for($path, $webDir, $baseUrl, 'canary.txt') as $url) {
        $served = tc_is_served($url, $marker);
        if ($served === true) {
            return 'the store would be readable over the web at ' . $url;
        }
        if ($served === null) {
            return 'the installer could not check ' . $url;
        }
    }
    return null;
}

/**
 * Confirms the installer can fetch its own directory at the address it
 * believes serves it.
 *
 * The positive control, and the thing that makes every refusal above mean
 * something. "The canary did not come back" has two readings - the store is
 * protected, or the wrong address was asked - and only one of them is a
 * reason to install. A rewrite rule, an alias, or a proxy in front of the
 * site all produce the second, silently.
 *
 * @return string|null An error to show the operator, or null when fetching
 *                     this directory demonstrably works.
 */
function tc_prove_fetching_works($webDir, $baseUrl)
{
    // A fixed name, so a run killed between writing and deleting leaves one
    // file behind rather than one per attempt. What is random is the marker
    // inside it, which is all that has to be unguessable.
    $name   = 'canary-selftest.txt';
    $file   = rtrim($webDir, '/') . '/' . $name;
    $marker = bin2hex(random_bytes(16));
    if (@file_put_contents($file, 'tc-canary ' . $marker) === false) {
        return 'Could not write a test file into this directory, so nothing could be '
             . 'checked. Nothing has been installed.';
    }
    @chmod($file, 0644);

    $served = tc_is_served(rtrim($baseUrl, '/') . '/' . $name, $marker);
    @unlink($file);

    if ($served === true) {
        return null;
    }
    return 'The installer could not fetch ' . rtrim($baseUrl, '/') . '/' . $name . ' - so '
         . 'it has no way to find out whether the store would be readable either, and it '
         . 'will not install a store it has not checked. Either this server cannot make '
         . 'requests to itself, or that address does not lead here. Nothing has been '
         . 'installed.';
}
