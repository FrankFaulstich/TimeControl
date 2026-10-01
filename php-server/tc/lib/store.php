<?php
/**
 * Storage primitives for the TimeControl sync server.
 *
 * Everything here exists because of what the host probe measured on the
 * target account:
 *
 *  - New files arrive as 0640, and the group id looked like one shared with
 *    other customers. So permissions are never left to the umask; every write
 *    chmods explicitly.
 *  - The store has to live inside the document root, because writing above it
 *    was not possible. So every stored file also carries a PHP guard line: if
 *    the directory's .htaccess ever stops being honoured, the file is still
 *    executed rather than served, and yields nothing.
 *  - flock() reported success, but that does not prove it locks - silent
 *    no-ops are a known NFS behaviour. Nothing here relies on the lock for
 *    integrity; the lock only serialises. Integrity comes from writing to a
 *    temporary file and renaming it into place, which is atomic on the host.
 */

// Prefixed to every stored file. Fetched over HTTP with PHP active, this
// executes and returns a bare 404; the payload after it is never emitted.
const TC_GUARD = "<?php http_response_code(404); exit; ?>\n";

// Content of the .htaccess dropped into every directory that must never be
// served. Both the 2.4 and the 2.2 form are present because which one a
// shared host honours is not something we get to choose.
const TC_DENY_HTACCESS = "Options -Indexes\n"
    . "<IfModule mod_authz_core.c>\n  Require all denied\n</IfModule>\n"
    . "<IfModule !mod_authz_core.c>\n  Order allow,deny\n  Deny from all\n</IfModule>\n";

/**
 * Creates a directory nobody but the owner can enter.
 *
 * @param bool $parents Whether missing directories above it are made too.
 *                      Not for anything inside an account's own directory:
 *                      a request still in flight when the account is removed
 *                      would build the directory up again around it (#589).
 */
function tc_secure_mkdir($path, $parents = true)
{
    if (is_dir($path)) {
        @chmod($path, 0700);
        return true;
    }
    if (!@mkdir($path, 0700, $parents)) {
        return false;
    }
    @chmod($path, 0700);
    return true;
}

/**
 * Removes a directory and everything under it.
 *
 * Refuses to touch anything outside the store. A recursive delete driven by
 * a path is worth being paranoid about even when the caller looks
 * trustworthy, because the cost of being wrong is unbounded.
 *
 * @return bool True when nothing is left of it - including when there was
 *              nothing there to begin with, so a second try after an
 *              interrupted one is not reported as a failure.
 */
function tc_remove_tree($path, $store)
{
    if (!file_exists($path) && !is_link($path)) {
        return true;
    }
    $real   = realpath($path);
    $inside = realpath($store);
    if ($real === false || $inside === false || strpos($real, $inside . DIRECTORY_SEPARATOR) !== 0) {
        return false;
    }
    foreach (scandir($real) ?: [] as $entry) {
        if ($entry === '.' || $entry === '..') {
            continue;
        }
        $child = $real . '/' . $entry;
        if (is_dir($child) && !is_link($child)) {
            tc_remove_tree($child, $store);
        } else {
            @unlink($child);
        }
    }
    return @rmdir($real);
}

/**
 * Writes a file atomically and leaves it readable only by its owner.
 *
 * The rename is what makes this safe against a request being killed by
 * max_execution_time: either the previous generation of the file is intact,
 * or the new one is, never a half-written mixture.
 */
function tc_write_secure($path, $contents)
{
    $tmp = dirname($path) . '/.tmp' . bin2hex(random_bytes(6));
    if (@file_put_contents($tmp, $contents) === false) {
        return false;
    }
    // Before the rename, so the file is never briefly visible at its final
    // name with the umask's permissions still on it.
    @chmod($tmp, 0600);
    if (!@rename($tmp, $path)) {
        @unlink($tmp);
        return false;
    }
    @chmod($path, 0600);
    return true;
}

function tc_write_json($path, array $data)
{
    $json = json_encode($data, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT);
    if ($json === false) {
        return false;
    }
    return tc_write_secure($path, TC_GUARD . $json . "\n");
}

/**
 * Reads a stored JSON file, stripping the guard line.
 *
 * The prefix length is taken from the constant rather than hardcoded - an
 * off-by-one here would make every stored file unreadable, and would do so
 * silently.
 */
function tc_read_json($path)
{
    $raw = @file_get_contents($path);
    if ($raw === false) {
        return null;
    }
    $guardLen = strlen(TC_GUARD);
    if (strncmp($raw, TC_GUARD, $guardLen) === 0) {
        $raw = substr($raw, $guardLen);
    }
    $data = json_decode($raw, true);
    return is_array($data) ? $data : null;
}

/**
 * Takes an advisory lock, giving up rather than queueing.
 *
 * Blocking would be worse than failing here: with a short execution limit a
 * queue of stalled requests ties up worker processes for the whole vhost,
 * and the caller can simply retry.
 *
 * @param float $wait How long to keep trying. 0 tries once: for work that is
 *                    better skipped than waited for, such as tidying up.
 * @return resource|null The open handle to pass to tc_unlock, or null.
 */
function tc_lock($lockPath, $wait = 5.0)
{
    $fh = @fopen($lockPath, 'c');
    if (!$fh) {
        return null;
    }
    @chmod($lockPath, 0600);
    $deadline = microtime(true) + $wait;
    while (!@flock($fh, LOCK_EX | LOCK_NB)) {
        if (microtime(true) >= $deadline) {
            @fclose($fh);
            return null;
        }
        usleep(20000);
    }
    return $fh;
}

function tc_unlock($fh)
{
    if ($fh) {
        @flock($fh, LOCK_UN);
        @fclose($fh);
    }
}

/**
 * Loads the installed configuration, or null when setup has not run.
 */
function tc_config()
{
    static $config = null;
    if ($config !== null) {
        return $config;
    }
    $path = dirname(__DIR__) . '/config.php';
    if (!is_file($path)) {
        return null;
    }
    $loaded = include $path;
    if (!is_array($loaded) || empty($loaded['store']) || !is_dir($loaded['store'])) {
        return null;
    }
    $config = $loaded;
    return $config;
}
