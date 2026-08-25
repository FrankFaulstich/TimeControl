<?php
/**
 * TimeControl sync server - operator installer.
 *
 * Strato's standard packages give FTP and a control panel and nothing else -
 * no shell. So the one thing that cannot be done from a command line has to
 * be done from a browser, and that means proving who is asking.
 *
 * The proof is write access to this directory: create a file called
 * setup.enable next to this one, containing a passphrase of your choosing,
 * and enter that passphrase here. Without the file this page is a bare 404
 * and does nothing. On success the file is deleted, so the window in which
 * anything here can be reached is one the operator opens deliberately and
 * lasts minutes.
 *
 * There is no admin account and no admin session - nothing to steal between
 * uses, and nothing to remember.
 */

if (PHP_VERSION_ID < 70400) {
    http_response_code(500);
    exit('PHP 7.4 or newer required');
}
umask(0077);

require_once __DIR__ . '/lib/store.php';
require_once __DIR__ . '/lib/auth.php';
require_once __DIR__ . '/lib/probe.php';

ini_set('display_errors', '0');

const TC_ENABLE_FILE = __DIR__ . '/setup.enable';

$enable = @file_get_contents(TC_ENABLE_FILE);
if ($enable === false || trim($enable) === '') {
    // A bare 404, on purpose: with no passphrase file there is nothing to
    // enable, and this page should not even admit to existing.
    http_response_code(404);
    exit;
}
// Not the raw contents. See tc_read_passphrase(): an editor can leave bytes
// in this file that nobody can see, and comparing against them refuses the
// right passphrase for ever while calling it a wrong one.
$enableFile = tc_read_passphrase($enable);
if ($enableFile['error'] !== null) {
    header('Content-Type: text/plain; charset=utf-8');
    exit($enableFile['error'] . "\n");
}
$expected = $enableFile['passphrase'];
if (strlen($expected) < 12) {
    header('Content-Type: text/plain; charset=utf-8');
    exit("setup.enable must contain a passphrase of at least 12 characters.\n");
}

// Everything below sends a passphrase and then a password over the wire.
if (empty($_SERVER['HTTPS']) || strtolower($_SERVER['HTTPS']) === 'off') {
    if ((int)($_SERVER['SERVER_PORT'] ?? 0) !== 443) {
        header('Content-Type: text/plain; charset=utf-8');
        exit("Refusing to run over plain HTTP - the passphrase would travel in the clear.\n");
    }
}

// setup.enable holds the operator passphrase in plain text, and it has to sit
// in a web directory because write access to that directory is precisely what
// proves who the operator is. An .htaccess is supposed to keep it from being
// served - but .htaccess files are easy to leave behind when uploading, since
// many FTP clients hide names starting with a dot, and a protection that is
// merely assumed is no protection. So it is demonstrated instead: fetch the
// file the way an outsider would and refuse to go on if it comes back.
$selfUrl = 'https://' . ($_SERVER['HTTP_HOST'] ?? '')
         . rtrim(dirname($_SERVER['SCRIPT_NAME'] ?? '/'), '/') . '/setup.enable';
$exposure = tc_fetch_verdict($selfUrl, $expected);
if ($exposure !== 'protected') {
    header('Content-Type: text/plain; charset=utf-8');
    if ($exposure === 'leaked') {
        echo "STOP - your passphrase is readable over the web.\n\n";
        echo "  " . $selfUrl . "\n\n";
        echo "serves the contents of setup.enable to anyone who asks. Almost always\n";
        echo "this means the .htaccess file that should sit next to setup.php was not\n";
        echo "uploaded - FTP clients routinely hide names beginning with a dot.\n\n";
        echo "Delete setup.enable now, upload the .htaccess files, and choose a new\n";
        echo "passphrase before trying again - treat the old one as compromised.\n";
    } else {
        echo "Cannot verify that setup.enable is protected from the web.\n\n";
        echo "This server could not fetch " . $selfUrl . " itself, so whether an\n";
        echo "outsider could read your passphrase is unknown. Open that URL in a\n";
        echo "browser: you should see \"Forbidden\" or \"Not Found\", never the\n";
        echo "passphrase itself.\n\n";
        echo "Nothing has been done.\n";
    }
    exit;
}

$notices = [];
if ($enableFile['note'] !== null) {
    // Said out loud rather than silently worked around. The file goes on
    // carrying the mark, and the operator would otherwise never learn that
    // their editor is putting invisible bytes into a credential file.
    $notices[] = $enableFile['note'];
}
$errors  = [];
$done    = false;

function h($s) { return htmlspecialchars((string)$s, ENT_QUOTES, 'UTF-8'); }

/**
 * Picks the store location and proves it is not reachable over the web.
 *
 * Above the document root is preferred, but the host probe found that path
 * unavailable here, so the fallback is a randomly named directory inside the
 * web space. Placement is not the point - the canary is. A directory is only
 * accepted once a fetch of a file inside it has actually been refused.
 *
 * WHY EVERY LOCATION IS FETCHED FOR, INCLUDING THE ONE "OUTSIDE"
 * --------------------------------------------------------------
 * This used to check only the candidate inside the web directory, and take
 * the other on trust: two levels above this script had to be outside the web
 * space, so there was nothing to prove. That holds when tc/ sits directly
 * under the document root, and fails as soon as it does not. With tc/ at
 * /public_html/apps/tc, two levels up IS /public_html - so the store landed
 * in the web space with no .htaccess, no index.html and no canary, and the
 * installer said it was fine.
 *
 * The trust was in the wrong place: it was extended to exactly the layout
 * where it is least obvious whether the store is exposed. So nothing is
 * assumed now. Every candidate is fetched for, at every address that could
 * plausibly reach it, and one that cannot be checked is passed over rather
 * than used.
 */
function tc_install($baseUrl)
{
    // Before anything is created. If this installer cannot fetch its own
    // directory, then no "the canary did not come back" below would mean
    // anything, and a store must not be placed on the strength of a question
    // that was never actually asked.
    $cannotCheck = tc_prove_fetching_works(__DIR__, $baseUrl);
    if ($cannotCheck !== null) {
        return [null, $cannotCheck];
    }

    $candidates = [];

    // Above the web directory. Uses __DIR__ rather than DOCUMENT_ROOT: on
    // this host the two resolve to different paths, and only __DIR__ is the
    // one this script is actually running from.
    $candidates[] = dirname(dirname(__DIR__)) . '/tcstore';

    // Inside it, behind a name nobody can guess.
    $inside = __DIR__ . '/store-' . bin2hex(random_bytes(6));
    $candidates[] = $inside;

    $refused = [];
    foreach ($candidates as $path) {
        if (!tc_secure_mkdir($path)) {
            continue;
        }
        $isInside = (strpos($path, __DIR__) === 0);

        if ($isInside) {
            @file_put_contents($path . '/.htaccess', TC_DENY_HTACCESS);
            @chmod($path . '/.htaccess', 0644);
            @file_put_contents($path . '/index.html', '');
        }

        // The canary. Deliberately a .txt and deliberately WITHOUT the PHP
        // guard every real stored file carries: a .php file would be executed
        // rather than sent, output nothing, and so report "protected" no
        // matter whether .htaccess works. That would be the check confirming
        // itself. Only a file the web server would hand over verbatim can
        // actually demonstrate that something is stopping it.
        $marker    = bin2hex(random_bytes(16));
        $canaryPhp = $path . '/canary.txt';
        @file_put_contents($canaryPhp, 'tc-canary ' . $marker);
        @chmod($canaryPhp, 0600);

        $problem = tc_prove_unreadable($path, __DIR__, $baseUrl, $marker);
        if ($problem !== null) {
            tc_discard_dir($path);
            $refused[] = $path . ': ' . $problem;
            continue;
        }
        @unlink($canaryPhp);

        foreach (['tokens', 'users'] as $sub) {
            tc_secure_mkdir($path . '/' . $sub);
        }
        tc_write_json(tc_users_file($path), ['users' => (object)[]]);

        $config = "<?php\n// Written by setup.php. Do not edit by hand.\nreturn "
            . var_export(['store' => $path, 'installed' => date('c')], true) . ";\n";
        if (!tc_write_secure(__DIR__ . '/config.php', $config)) {
            return [null, 'Could not write config.php.'];
        }
        // Left at the 0600 tc_write_secure gives it. PHP runs as the owner
        // here, so nothing needs wider access - and this file names the
        // store directory, which is the one thing worth knowing for anyone
        // who gets as far as reading files on this account.

        return [$path, null];
    }

    if ($refused) {
        // Named, because the two locations fail for different reasons and the
        // fix differs with them: a store readable inside the web directory
        // means .htaccess is not being honoured and this host cannot be used,
        // while one readable above it means tc/ is nested deeper than the
        // installer's guess and the operator can move it.
        return [null, 'No store location could be shown to be safe, so nothing has been '
            . 'installed. ' . implode('; ', $refused) . '. Open one of those addresses in '
            . 'a browser: if you see the word tc-canary, that directory is being served '
            . 'to the world and must not hold the store.'];
    }
    return [null, 'Could not create a store directory anywhere.'];
}

/**
 * Removes a store directory an aborted install had just created.
 *
 * Without this, every refused attempt leaves another empty, randomly named
 * directory behind - so the one thing a worried operator does, try again,
 * quietly litters the web space. Only the shallow contents this function's
 * caller can have created are removed; it never recurses, so a directory
 * that somehow already held data is left alone rather than deleted.
 */
function tc_discard_dir($path)
{
    foreach (['canary.txt', '.htaccess', 'index.html'] as $name) {
        @unlink($path . '/' . $name);
    }
    @rmdir($path);
}

/**
 * Removes a directory and everything under it.
 *
 * Refuses to touch anything outside the store. A recursive delete driven by
 * a path is worth being paranoid about even when the caller looks
 * trustworthy, because the cost of being wrong is unbounded.
 */
function tc_rmtree($path, $store)
{
    $real  = realpath($path);
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
            tc_rmtree($child, $store);
        } else {
            @unlink($child);
        }
    }
    return @rmdir($real);
}

// ---------------------------------------------------------------------------

if (($_SERVER['REQUEST_METHOD'] ?? '') === 'POST') {
    // Trimmed for the same reason the stored one is: a passphrase pasted out
    // of a file brings the newline with it, and the mismatch that causes is
    // just as invisible as a byte order mark. Nothing is given up by it -
    // the stored passphrase is trimmed too, so one ending in a space could
    // never have been set in the first place.
    $given = trim((string)($_POST['passphrase'] ?? ''));
    if (!hash_equals($expected, $given)) {
        $errors[] = 'Wrong passphrase.';
    } else {
        $action  = (string)($_POST['action'] ?? '');
        $scheme  = 'https';
        $baseUrl = $scheme . '://' . ($_SERVER['HTTP_HOST'] ?? '')
                 . rtrim(dirname($_SERVER['SCRIPT_NAME'] ?? '/'), '/');

        if ($action === 'install') {
            if (tc_config() !== null) {
                $errors[] = 'Already installed. Delete config.php first if you really mean to reinstall.';
            } else {
                list($path, $err) = tc_install($baseUrl);
                if ($err) {
                    $errors[] = $err;
                } else {
                    $notices[] = 'Installed. Store: ' . $path;
                    $done = true;
                }
            }
        } elseif ($action === 'adduser') {
            $config = tc_config();
            if ($config === null) {
                $errors[] = 'Not installed yet.';
            } else {
                $name = trim((string)($_POST['username'] ?? ''));
                $pass = (string)($_POST['password'] ?? '');
                if (!preg_match('/^[A-Za-z0-9._-]{3,32}$/', $name)) {
                    $errors[] = 'Username must be 3-32 characters, letters/digits/dot/underscore/hyphen.';
                } elseif (strlen($pass) < 12) {
                    $errors[] = 'Password must be at least 12 characters.';
                } else {
                    $store = $config['store'];
                    $lock  = tc_lock(tc_users_lock($store));
                    if (!$lock) {
                        $errors[] = 'Could not lock the user store.';
                    } else {
                        $data = tc_read_json(tc_users_file($store));
                        if (!is_array($data) || !isset($data['users'])) {
                            $data = ['users' => []];
                        }
                        if (isset($data['users'][$name])) {
                            $errors[] = 'That account already exists.';
                        } else {
                            $uid = bin2hex(random_bytes(16));
                            $data['users'][$name] = [
                                'uid'     => $uid,
                                'pass'    => password_hash($pass, PASSWORD_BCRYPT, ['cost' => TC_BCRYPT_COST]),
                                'created' => date('c'),
                            ];
                            tc_write_json(tc_users_file($store), $data);
                            tc_secure_mkdir(tc_user_dir($store, $uid));
                            tc_secure_mkdir(tc_user_dir($store, $uid) . '/seen');
                            tc_write_json(tc_user_dir($store, $uid) . '/user.dat.php',
                                ['disabled' => false, 'devices' => []]);
                            $notices[] = 'Account "' . $name . '" created.';
                            $done = true;
                        }
                        tc_unlock($lock);
                    }
                }
            }
        } elseif ($action === 'deluser') {
            $config = tc_config();
            if ($config === null) {
                $errors[] = 'Not installed yet.';
            } else {
                $name  = trim((string)($_POST['username'] ?? ''));
                $store = $config['store'];
                $lock  = tc_lock(tc_users_lock($store));
                if (!$lock) {
                    $errors[] = 'Could not lock the user store.';
                } else {
                    $data = tc_read_json(tc_users_file($store));
                    if (!is_array($data) || !isset($data['users'][$name])) {
                        $errors[] = 'No such account.';
                    } else {
                        $uid = $data['users'][$name]['uid'];
                        // Tokens live in a shared directory keyed by token id,
                        // so they have to go individually - dropping the user
                        // directory alone would leave working credentials
                        // pointing at an account that no longer exists.
                        $rec = tc_read_json(tc_user_dir($store, $uid) . '/user.dat.php');
                        foreach (($rec['devices'] ?? []) as $d) {
                            if (!empty($d['token_id'])) {
                                tc_token_revoke($store, $d['token_id']);
                            }
                        }
                        tc_rmtree(tc_user_dir($store, $uid), $store);
                        unset($data['users'][$name]);
                        tc_write_json(tc_users_file($store), $data);
                        $notices[] = 'Account "' . $name . '" and all of its data were deleted.';
                        $done = true;
                    }
                    tc_unlock($lock);
                }
            }
        } elseif ($action === 'status') {
            $config = tc_config();
            if ($config === null) {
                $notices[] = 'Not installed.';
            } else {
                $data  = tc_read_json(tc_users_file($config['store']));
                $names = ($data && !empty($data['users'])) ? array_keys((array)$data['users']) : [];
                $notices[] = 'Store: ' . $config['store'];
                $notices[] = 'Accounts: ' . ($names ? implode(', ', $names) : '(none)');
                $tokens = glob(tc_tokens_dir($config['store']) . '/*.dat.php');
                $notices[] = 'Live tokens: ' . ($tokens ? count($tokens) : 0);
            }
        } else {
            $errors[] = 'Unknown action.';
        }

        // Close the window as soon as anything was actually changed.
        if ($done) {
            if (@unlink(TC_ENABLE_FILE)) {
                $notices[] = 'setup.enable has been deleted - this page is closed again.';
            } else {
                $errors[] = 'IMPORTANT: setup.enable could NOT be deleted. Remove it by FTP now, '
                          . 'otherwise anyone with the passphrase can keep using this page.';
            }
        }
    }
}

header('Content-Type: text/html; charset=utf-8');
header('X-Robots-Tag: noindex, nofollow');
?><!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TimeControl sync - setup</title>
<style>
 body{font:15px/1.5 system-ui,sans-serif;max-width:40rem;margin:2rem auto;padding:0 1rem;color:#222}
 fieldset{border:1px solid #ccc;border-radius:6px;margin:1rem 0;padding:1rem}
 legend{padding:0 .4rem;font-weight:600}
 label{display:block;margin:.6rem 0 .2rem}
 input{width:100%;padding:.45rem;border:1px solid #bbb;border-radius:4px;box-sizing:border-box}
 button{margin-top:.8rem;padding:.5rem 1rem;border:1px solid #888;border-radius:4px;background:#f4f4f4;cursor:pointer}
 .ok{background:#eaf6ea;border-left:4px solid #4a4;padding:.6rem;margin:.4rem 0}
 .err{background:#fdeaea;border-left:4px solid #c44;padding:.6rem;margin:.4rem 0}
 code{background:#f2f2f2;padding:.1rem .3rem;border-radius:3px}
</style></head><body>
<h1>TimeControl sync &ndash; setup</h1>
<?php foreach ($notices as $n): ?><div class="ok"><?= h($n) ?></div><?php endforeach; ?>
<?php foreach ($errors as $e): ?><div class="err"><?= h($e) ?></div><?php endforeach; ?>

<p>Every action needs the passphrase from <code>setup.enable</code>. That file is
deleted as soon as something is changed &ndash; upload it again for the next action.</p>

<form method="post">
<fieldset><legend>1. Install</legend>
  <p>Creates the store and proves it cannot be read over the web.</p>
  <label>Passphrase</label><input type="password" name="passphrase" autocomplete="off">
  <button type="submit" name="action" value="install">Install</button>
</fieldset>
</form>

<form method="post">
<fieldset><legend>2. Create an account</legend>
  <label>Username</label><input name="username" autocomplete="off">
  <label>Password (12+ characters)</label><input type="password" name="password" autocomplete="new-password">
  <label>Passphrase</label><input type="password" name="passphrase" autocomplete="off">
  <button type="submit" name="action" value="adduser">Create</button>
</fieldset>
</form>

<form method="post">
<fieldset><legend>Delete an account</legend>
  <p><strong>Irreversible.</strong> Removes the account, its whole operation
  log and every token it holds.</p>
  <label>Username</label><input name="username" autocomplete="off">
  <label>Passphrase</label><input type="password" name="passphrase" autocomplete="off">
  <button type="submit" name="action" value="deluser">Delete</button>
</fieldset>
</form>

<form method="post">
<fieldset><legend>Status</legend>
  <p>Read-only &ndash; does not consume <code>setup.enable</code>.</p>
  <label>Passphrase</label><input type="password" name="passphrase" autocomplete="off">
  <button type="submit" name="action" value="status">Show status</button>
</fieldset>
</form>
</body></html>
