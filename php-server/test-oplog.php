<?php
/**
 * Tests for the operation log and its compaction, run without a web server.
 *
 *     php php-server/test-oplog.php
 *
 * The check-*.py scripts exercise a real installation over HTTPS, which is
 * the right way to find out whether a host behaves. This does the opposite:
 * it calls the log functions directly against a throwaway store, so the parts
 * that are awkward to provoke over a network - a segment retired while
 * another straddles the snapshot point, a request killed between two writes -
 * can be set up exactly and checked.
 *
 * Nothing here touches a real store: everything happens under a temporary
 * directory that is removed at the end.
 */

require_once __DIR__ . '/tc/lib/store.php';
require_once __DIR__ . '/tc/lib/auth.php';
require_once __DIR__ . '/tc/lib/oplog.php';

$GLOBALS['tc_tests'] = 0;
$GLOBALS['tc_failed'] = 0;
$GLOBALS['tc_current'] = '';

// What the code under test writes to the error log goes to a file of its own,
// where a test can look for it, rather than into the results.
$GLOBALS['tc_error_log'] = sys_get_temp_dir() . '/tc-oplog-test-' . bin2hex(random_bytes(6)) . '.log';
ini_set('error_log', $GLOBALS['tc_error_log']);
register_shutdown_function(function () { @unlink($GLOBALS['tc_error_log']); });

function tc_test($name, callable $body)
{
    $GLOBALS['tc_current'] = $name;
    $store = tc_temp_store();
    try {
        $body($store, 'u0000000000000001');
        printf("  ok    %s\n", $name);
    } catch (Throwable $exc) {
        $GLOBALS['tc_failed']++;
        printf("  FAIL  %s\n        %s\n", $name, $exc->getMessage());
    } finally {
        $GLOBALS['tc_tests']++;
        tc_rmtree($store);
    }
}

function tc_assert($condition, $what)
{
    if (!$condition) {
        throw new RuntimeException($what);
    }
}

function tc_assert_same($expected, $actual, $what)
{
    if ($expected !== $actual) {
        throw new RuntimeException(sprintf('%s: expected %s, got %s', $what,
            var_export($expected, true), var_export($actual, true)));
    }
}

function tc_temp_store()
{
    $dir = sys_get_temp_dir() . '/tc-oplog-test-' . bin2hex(random_bytes(6));
    tc_secure_mkdir($dir . '/users/u0000000000000001');
    // As an account has it. The log refuses to write for an account whose
    // directory has no user.dat.php, since that is one that has been removed.
    tc_write_json($dir . '/users/u0000000000000001/user.dat.php', ['disabled' => false, 'devices' => []]);
    return $dir;
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

/** A batch of distinguishable operations, numbered from $fromLc. */
function tc_ops($count, $fromLc = 1)
{
    $ops = [];
    for ($i = 0; $i < $count; $i++) {
        $ops[] = ['op' => 'task.set', 'lc' => $fromLc + $i,
                  'uid' => sprintf('%016x', $fromLc + $i),
                  'f' => ['task_name' => 'task ' . ($fromLc + $i)]];
    }
    return $ops;
}

/** Pushes $count operations in batches the server would actually accept. */
function tc_fill($store, $uid, $device, $count, $fromLc = 1)
{
    $done = 0;
    while ($done < $count) {
        $batch = min(TC_PUSH_MAX_OPS, $count - $done);
        $result = tc_log_append($store, $uid, $device, tc_ops($batch, $fromLc + $done));
        tc_assert($result !== null, 'append returned null while filling');
        $done += $batch;
    }
    return tc_log_state($store, $uid)['head'];
}

function tc_document($projects = 1)
{
    $doc = ['schema_version' => 2, 'next_id' => 10, 'projects' => [], '_deleted' => []];
    for ($i = 1; $i <= $projects; $i++) {
        $doc['projects'][] = [
            'uid' => sprintf('%016x', $i), 'main_project_name' => 'Project ' . $i,
            'status' => 'open', 'tasks' => [],
        ];
    }
    return json_encode($doc);
}

function tc_read_all($store, $uid, $since)
{
    $ops = [];
    $guard = 0;
    while ($guard++ < 200) {
        $page = tc_log_read($store, $uid, $since, TC_PULL_MAX_OPS);
        tc_assert(!$page['needs_snapshot'], 'read_all hit needs_snapshot at ' . $since);
        foreach ($page['ops'] as $op) {
            $ops[] = $op;
            $since = max($since, (int)$op['s']);
        }
        if (!$page['more']) {
            break;
        }
    }
    return $ops;
}

print("Operation log\n");

tc_test('appending and reading back the whole log', function ($store, $uid) {
    tc_fill($store, $uid, 'dev0000000000001', 30);
    $ops = tc_read_all($store, $uid, 0);
    tc_assert_same(30, count($ops), 'operation count');
    tc_assert_same(1, (int)$ops[0]['s'], 'first sequence number');
    tc_assert_same(30, (int)$ops[29]['s'], 'last sequence number');
});

tc_test('a segment file left behind by a failed append is not adopted', function ($store, $uid) {
    // An append that died between creating its segment file and recording it
    // in the state leaves a file with a fragment in it. The next append lands
    // on the same name; adopting those bytes would put half a line into the
    // log and leave the byte count describing a file that no longer matches.
    $dir = tc_log_dir($store, $uid);
    tc_secure_mkdir($dir);
    file_put_contents($dir . '/seg-0000001.log.php', TC_GUARD . '{"s":1,"op":"task.se');

    tc_fill($store, $uid, 'dev0000000000001', 3);

    $ops = tc_read_all($store, $uid, 0);
    tc_assert_same(3, count($ops), 'operations readable after the leftover');
    $state = tc_log_state($store, $uid);
    $seg = $state['segments'][0];
    tc_assert_same((int)filesize($dir . '/' . $seg['f']), (int)$seg['bytes'],
                   'recorded byte count matches the file');
});

print("\nSnapshots - what is refused\n");

tc_test('nothing to snapshot on an empty log', function ($store, $uid) {
    $result = tc_snapshot_put($store, $uid, 'dev0000000000001', 1, tc_document());
    tc_assert_same('log_empty', $result['error'], 'error code');
});

tc_test('a device that is not at head', function ($store, $uid) {
    $head = tc_fill($store, $uid, 'dev0000000000001', 10);
    $result = tc_snapshot_put($store, $uid, 'dev0000000000001', $head - 1, tc_document());
    tc_assert_same('not_at_head', $result['error'], 'error code');
    tc_assert_same(null, tc_snapshot_meta($store, $uid), 'nothing was stored');
});

tc_test('a snapshot no newer than the one already held', function ($store, $uid) {
    $head = tc_fill($store, $uid, 'dev0000000000001', 10);
    tc_snapshot_put($store, $uid, 'dev0000000000001', $head, tc_document());
    $result = tc_snapshot_put($store, $uid, 'dev0000000000001', $head, tc_document());
    tc_assert_same('not_newer', $result['error'], 'error code');
});

tc_test('bodies that are not a document', function ($store, $uid) {
    tc_assert_same('snapshot_empty', tc_snapshot_validate(''), 'empty body');
    tc_assert_same('snapshot_not_json', tc_snapshot_validate('<html>404</html>'), 'an error page');
    tc_assert_same('snapshot_shape', tc_snapshot_validate('{"tasks":[]}'), 'no projects key');
    tc_assert_same('snapshot_shape', tc_snapshot_validate('{"projects":"no"}'), 'projects not a list');
    tc_assert_same('snapshot_has_no_projects', tc_snapshot_validate('{"projects":[]}'),
                   'an empty document');
    tc_assert_same('snapshot_too_large',
                   tc_snapshot_validate('{"projects":[' . str_repeat('0', TC_SNAPSHOT_MAX_BYTES) . ']}'),
                   'past the size limit');
    tc_assert_same(null, tc_snapshot_validate(tc_document()), 'a real document');
});

print("\nSnapshots - what happens when one is accepted\n");

tc_test('the snapshot is stored and pointed at', function ($store, $uid) {
    $head = tc_fill($store, $uid, 'dev0000000000001', 10);
    $result = tc_snapshot_put($store, $uid, 'dev0000000000001', $head, tc_document(3));

    tc_assert_same($head, $result['snapshot_seq'], 'reported sequence number');
    $snap = tc_snapshot_meta($store, $uid);
    tc_assert($snap !== null, 'the state points at a snapshot');
    tc_assert_same($head, (int)$snap['seq'], 'recorded sequence number');
    $stored = tc_read_json(tc_snapshot_file($store, $uid, $snap));
    tc_assert_same(3, count($stored['projects']), 'the document came back intact');
});

tc_test('a machine below the snapshot point is told to fetch it', function ($store, $uid) {
    $head = tc_fill($store, $uid, 'dev0000000000001', 10);
    tc_snapshot_put($store, $uid, 'dev0000000000001', $head, tc_document());

    $page = tc_log_read($store, $uid, 0, TC_PULL_MAX_OPS);
    tc_assert_same(true, $page['needs_snapshot'], 'flagged');
    tc_assert_same(0, count($page['ops']), 'and given nothing to misread');
    tc_assert_same($head, $page['snapshot_seq'], 'told where the snapshot sits');
});

tc_test('a machine at or above the snapshot point reads on as before', function ($store, $uid) {
    $head = tc_fill($store, $uid, 'dev0000000000001', 10);
    tc_snapshot_put($store, $uid, 'dev0000000000001', $head, tc_document());
    tc_fill($store, $uid, 'dev0000000000002', 5, 100);

    $page = tc_log_read($store, $uid, $head, TC_PULL_MAX_OPS);
    tc_assert_same(false, $page['needs_snapshot'], 'not asked for the snapshot');
    tc_assert_same(5, count($page['ops']), 'the tail after the snapshot');
    tc_assert_same($head + 1, (int)$page['ops'][0]['s'], 'starting just past it');
});

print("\nDiscarding segments\n");

tc_test('a segment holding operations above the point is not retired', function ($store, $uid) {
    // The rule that keeps criterion two: never discard a file that still
    // carries an operation no snapshot covers. A snapshot is only accepted
    // at head today, where every segment qualifies, so the straddling case
    // is put to tc_snapshot_retire directly rather than through a push that
    // cannot produce it.
    tc_fill($store, $uid, 'dev0000000000001', 2500);
    $state = tc_log_state($store, $uid);
    tc_assert(count($state['segments']) >= 3, 'the fill produced several segments');

    $straddling = $state['segments'][1];
    $seq = (int)$straddling['first'];      // inside the second segment
    tc_assert($seq > (int)$state['segments'][0]['last'], 'the point really is inside it');

    $retired = tc_snapshot_retire($state, $seq);

    tc_assert_same(1, $retired, 'exactly the segment that ends below the point');
    tc_assert_same($straddling['f'], $state['segments'][0]['f'], 'the straddling one stayed');
    foreach ($state['retired'] as $segment) {
        tc_assert((int)$segment['last'] <= $seq,
                  'nothing retired that reaches above the point: ' . $segment['f']);
    }

    // And what stayed still answers in full.
    $state['snapshot'] = ['seq' => $seq, 'f' => 'snap.json.php', 'bytes' => 1, 'at' => time()];
    tc_write_json(tc_log_state_path($store, $uid), $state);
    $ops = tc_read_all($store, $uid, $seq);
    tc_assert_same((int)$state['head'] - $seq, count($ops),
                   'every operation above the point is still there');
    $expected = $seq + 1;
    foreach ($ops as $op) {
        tc_assert_same($expected, (int)$op['s'], 'contiguous sequence numbers');
        $expected++;
    }
});

tc_test('every operation is either in the snapshot or still readable', function ($store, $uid) {
    // Criterion two, stated as the property rather than as a mechanism: after
    // compaction, an operation may only be missing from the log if the
    // snapshot is claimed to cover it.
    $head = tc_fill($store, $uid, 'dev0000000000001', 1500);
    tc_snapshot_put($store, $uid, 'dev0000000000001', $head, tc_document());
    tc_fill($store, $uid, 'dev0000000000002', 700, 9000);

    $state = tc_log_state($store, $uid);
    $covered = (int)$state['snapshot']['seq'];
    $ops = tc_read_all($store, $uid, $covered);

    tc_assert_same((int)$state['head'] - $covered, count($ops), 'the whole tail is readable');
    $expected = $covered + 1;
    foreach ($ops as $op) {
        tc_assert_same($expected, (int)$op['s'], 'with no gap in it');
        $expected++;
    }
    foreach ($state['segments'] as $segment) {
        tc_assert(is_file(tc_log_dir($store, $uid) . '/' . $segment['f']),
                  'a live segment still has its file: ' . $segment['f']);
    }
});

tc_test('retired segments wait out the grace period before deletion', function ($store, $uid) {
    $head = tc_fill($store, $uid, 'dev0000000000001', 1200);
    tc_snapshot_put($store, $uid, 'dev0000000000001', $head, tc_document());

    $state = tc_log_state($store, $uid);
    tc_assert(count($state['retired']) > 0, 'something was retired');
    $dir = tc_log_dir($store, $uid);
    foreach ($state['retired'] as $segment) {
        tc_assert(is_file($dir . '/' . $segment['f']),
                  'the file is still on disk during the grace period: ' . $segment['f']);
    }

    // Age them past the grace period and sweep.
    foreach ($state['retired'] as $i => $segment) {
        $state['retired'][$i]['at'] = time() - TC_SEG_GRACE_SECONDS - 1;
    }
    $names = array_column($state['retired'], 'f');
    $deleted = tc_snapshot_sweep($state, $dir);

    tc_assert_same(count($names), $deleted, 'all of them swept');
    tc_assert_same(0, count($state['retired']), 'and taken off the list');
    foreach ($names as $name) {
        tc_assert(!is_file($dir . '/' . $name), 'the file is gone: ' . $name);
    }
});

tc_test('a new segment never reuses the name of a retired one', function ($store, $uid) {
    $head = tc_fill($store, $uid, 'dev0000000000001', 1200);
    $state = tc_log_state($store, $uid);
    $before = array_column($state['segments'], 'f');

    tc_snapshot_put($store, $uid, 'dev0000000000001', $head, tc_document());
    // Retirement shortens the segment list. A name derived from that length
    // would now be handed out a second time and append live operations to a
    // file that is waiting to be swept.
    tc_fill($store, $uid, 'dev0000000000002', 1200, 5000);

    $state = tc_log_state($store, $uid);
    $names = array_merge(array_column($state['segments'], 'f'),
                         array_column($state['retired'], 'f'));
    tc_assert_same(count($names), count(array_unique($names)), 'every segment file has its own name');
    foreach ($state['segments'] as $segment) {
        tc_assert(!in_array($segment['f'], array_column($state['retired'], 'f'), true),
                  'a live segment is not also retired: ' . $segment['f']);
    }
    tc_assert(count($before) > 0, 'the first fill produced segments');
});

tc_test('the snapshot before last is kept, the one before that removed', function ($store, $uid) {
    $dir = tc_log_dir($store, $uid);
    $files = [];
    for ($round = 1; $round <= 3; $round++) {
        $head = tc_fill($store, $uid, 'dev0000000000001', 10, $round * 1000);
        tc_snapshot_put($store, $uid, 'dev0000000000001', $head, tc_document($round));
        $files[$round] = tc_snapshot_meta($store, $uid)['f'];
    }
    tc_assert(is_file($dir . '/' . $files[3]), 'the current snapshot is there');
    tc_assert(is_file($dir . '/' . $files[2]), 'the one before it is kept for recovery');
    tc_assert(!is_file($dir . '/' . $files[1]), 'the one before that is gone');
});

print("\nBeing interrupted\n");

// A request can be killed at any point by max_execution_time. Each of these
// builds the store as it would be left at one such point, and asks whether
// what remains can still be read correctly.

tc_test('killed after writing the snapshot file, before the pointer', function ($store, $uid) {
    $head = tc_fill($store, $uid, 'dev0000000000001', 20);
    $dir = tc_log_dir($store, $uid);
    file_put_contents($dir . '/snap-0000000020.json.php', TC_GUARD . tc_document());

    tc_assert_same(null, tc_snapshot_meta($store, $uid), 'no snapshot is in force');
    $ops = tc_read_all($store, $uid, 0);
    tc_assert_same(20, count($ops), 'the log still answers in full');
    tc_assert_same($head, (int)tc_log_state($store, $uid)['head'], 'head is untouched');
});

tc_test('killed after the pointer, before the segments were retired', function ($store, $uid) {
    $head = tc_fill($store, $uid, 'dev0000000000001', 1200);
    $dir = tc_log_dir($store, $uid);
    file_put_contents($dir . '/snap.json.php', TC_GUARD . tc_document());
    $state = tc_log_state($store, $uid);
    $segments = count($state['segments']);
    $state['snapshot'] = ['seq' => $head, 'f' => 'snap.json.php', 'bytes' => 1, 'at' => time()];
    tc_write_json(tc_log_state_path($store, $uid), $state);

    $snap = tc_snapshot_meta($store, $uid);
    tc_assert($snap !== null, 'the snapshot is in force');
    tc_assert_same($segments, count(tc_log_state($store, $uid)['segments']),
                   'the segments are simply still in use');
    tc_assert_same(true, tc_log_read($store, $uid, 0, 10)['needs_snapshot'],
                   'a newcomer is sent to the snapshot');
    tc_fill($store, $uid, 'dev0000000000002', 3, 9000);
    tc_assert_same(3, count(tc_read_all($store, $uid, $head)), 'and the tail reads on');
});

tc_test('killed after retiring the segments, before the files were deleted', function ($store, $uid) {
    $head = tc_fill($store, $uid, 'dev0000000000001', 1200);
    tc_snapshot_put($store, $uid, 'dev0000000000001', $head, tc_document());
    // tc_snapshot_put leaves exactly this state during the grace period.
    $state = tc_log_state($store, $uid);
    tc_assert(count($state['retired']) > 0, 'files retired but present');

    tc_fill($store, $uid, 'dev0000000000002', 4, 9000);
    tc_assert_same(4, count(tc_read_all($store, $uid, $head)), 'the tail reads correctly');
    tc_assert_same(true, tc_log_read($store, $uid, 0, 10)['needs_snapshot'], 'newcomers redirected');
});

tc_test('killed after deleting files, before the list was written', function ($store, $uid) {
    $head = tc_fill($store, $uid, 'dev0000000000001', 1200);
    tc_snapshot_put($store, $uid, 'dev0000000000001', $head, tc_document());
    $state = tc_log_state($store, $uid);
    $dir = tc_log_dir($store, $uid);
    foreach ($state['retired'] as $segment) {
        @unlink($dir . '/' . $segment['f']);
    }

    // The list still names files that are gone. Nothing reads them, and the
    // sweep must not trip over their absence.
    tc_fill($store, $uid, 'dev0000000000002', 4, 9000);
    tc_assert_same(4, count(tc_read_all($store, $uid, $head)), 'the tail is unaffected');
    foreach ($state['retired'] as $i => $segment) {
        $state['retired'][$i]['at'] = 0;
    }
    $deleted = tc_snapshot_sweep($state, $dir);
    tc_assert($deleted >= 0, 'the sweep survives missing files');
    tc_assert_same(0, count($state['retired']), 'and clears the list');
});

// ---------------------------------------------------------------------------
// What a batch has to look like to be accepted at all.
//
// tc_ops_validate had no coverage here before end-to-end encryption needed a
// verb of its own. It is the gate every operation passes through, and what it
// lets past is handed to every other machine as the truth - so a rule quietly
// lost here would not be noticed until two machines had stopped agreeing.
// ---------------------------------------------------------------------------

/** One sealed operation, shaped as tt/sync_crypto.py seals them. */
function tc_sealed($lc = 1)
{
    return ['op' => TC_OP_SEALED, 'lc' => $lc,
            'f' => ['v' => 1, 'k' => 'a1b2c3d4', 'c' => 'Zm9vYmFy']];
}

tc_test('a sealed operation is accepted', function ($store, $uid) {
    tc_assert_same(null, tc_ops_validate([tc_sealed()]),
                   'the placeholder verb was refused');
});

tc_test('an unknown verb is still refused', function ($store, $uid) {
    tc_assert_same('unknown_op',
                   tc_ops_validate([['op' => 'task.explode', 'lc' => 1]]),
                   'the list stopped being a list');
});

tc_test('a sealed operation without its payload is refused', function ($store, $uid) {
    // It carries no operation at all: the verb, the identifiers and the
    // fields are all inside the payload. The machine receiving it could only
    // stop, so it is turned away here instead.
    tc_assert_same('bad_fields',
                   tc_ops_validate([['op' => TC_OP_SEALED, 'lc' => 1]]),
                   'an empty envelope was accepted');
});

tc_test('a sealed operation still needs a counter', function ($store, $uid) {
    $op = tc_sealed();
    unset($op['lc']);
    tc_assert_same('bad_lc', tc_ops_validate([$op]), 'the counter became optional');
});

tc_test('a sealed operation names no objects, and is not asked to', function ($store, $uid) {
    // uid, project and task are inside the ciphertext, so they are absent
    // from the envelope. The check on their shape only applies when they are
    // there - if that ever became mandatory, every sealed push would fail.
    $op = tc_sealed();
    tc_assert(!isset($op['uid']), 'the envelope should carry no uid');
    tc_assert_same(null, tc_ops_validate([$op]), 'an absent uid was treated as a bad one');
});

tc_test('a sealed operation is stored and handed back whole', function ($store, $uid) {
    $result = tc_log_append($store, $uid, 'dev0000000000001', [tc_sealed(7)]);
    tc_assert($result !== null, 'the append failed');
    tc_assert_same(1, $result['head'], 'it was not recorded');

    $read = tc_log_read($store, $uid, 0, 10);
    tc_assert_same(1, count($read['ops']), 'it did not come back');
    $back = $read['ops'][0];
    tc_assert_same(TC_OP_SEALED, $back['op'], 'the verb changed on the way');
    tc_assert_same('Zm9vYmFy', $back['f']['c'], 'the payload changed on the way');
    tc_assert_same(1, $back['s'], 'it got no place in the order');
    tc_assert_same('dev0000000000001', $back['dev'], 'the device was not stamped on');
});

tc_test('the server learns nothing from a sealed operation', function ($store, $uid) {
    // The whole point, stated as a test: what reaches the disk holds no verb
    // but the placeholder and no field of the operation it stands for.
    tc_log_append($store, $uid, 'dev0000000000001', [tc_sealed(3)]);
    $state = tc_log_state($store, $uid);
    $segment = tc_log_dir($store, $uid) . '/' . $state['segments'][0]['f'];
    $written = file_get_contents($segment);
    foreach (['task.set', 'task_name', 'project.delete', 'entry.add'] as $absent) {
        tc_assert(strpos($written, $absent) === false,
                  sprintf('%s reached the segment file', $absent));
    }
    tc_assert(strpos($written, TC_OP_SEALED) !== false,
              'the placeholder should be there');
});

tc_test('sealed and plain operations can share a log', function ($store, $uid) {
    // The state an account is in while it is being changed over: one machine
    // already sealing, another not yet.
    $mixed = [tc_sealed(1), ['op' => 'task.set', 'lc' => 2,
                             'uid' => sprintf('%016x', 2), 'f' => ['priority' => 3]]];
    tc_assert_same(null, tc_ops_validate($mixed), 'the mixture was refused');
    $result = tc_log_append($store, $uid, 'dev0000000000001', $mixed);
    tc_assert_same(2, $result['head'], 'both should have been recorded');
    $ops = tc_log_read($store, $uid, 0, 10)['ops'];
    tc_assert_same(TC_OP_SEALED, $ops[0]['op'], 'the sealed one changed');
    tc_assert_same('task.set', $ops[1]['op'], 'the plain one changed');
});

// ---------------------------------------------------------------------------
// A snapshot from an encrypted account.
// ---------------------------------------------------------------------------

/** A sealed document, shaped as tt/sync_crypto.py seal_document() makes them. */
function tc_sealed_document()
{
    return json_encode([TC_SNAPSHOT_SEALED => [
        'v' => 1, 'k' => 'a1b2c3d4', 'c' => 'Zm9vYmFyYmF6']]);
}

tc_test('a sealed document is accepted as a snapshot', function ($store, $uid) {
    tc_assert_same(null, tc_snapshot_validate(tc_sealed_document()),
                   'a sealed document was refused');
});

tc_test('a readable document is judged exactly as before', function ($store, $uid) {
    // The sealed case must not have loosened anything for the plain one: an
    // emptied data.json offered as the truth is still the mistake this
    // refuses, and the codes are what the client's messages are written for.
    tc_assert_same('snapshot_empty', tc_snapshot_validate(''), 'empty');
    tc_assert_same('snapshot_not_json', tc_snapshot_validate('not json'), 'not json');
    tc_assert_same('snapshot_shape', tc_snapshot_validate('{"a":1}'), 'no projects');
    tc_assert_same('snapshot_shape', tc_snapshot_validate('{"projects":"x"}'),
                   'projects not a list');
    tc_assert_same('snapshot_has_no_projects', tc_snapshot_validate('{"projects":[]}'),
                   'empty projects');
    tc_assert_same(null, tc_snapshot_validate(tc_document()), 'a real document');
});

tc_test('a sealed document still has to be JSON and within the limit', function ($store, $uid) {
    // Both survive the new branch, and both must: the size because the store
    // is not a dumping ground, and the JSON because index.php splices the
    // stored bytes straight into its reply.
    tc_assert_same('snapshot_not_json',
                   tc_snapshot_validate('e2ee: yes'), 'not json');
    tc_assert_same('snapshot_too_large',
                   tc_snapshot_validate(str_repeat('x', TC_SNAPSHOT_MAX_BYTES + 1)),
                   'too large');
});

tc_test('the envelope is recognised by presence, not by its contents', function ($store, $uid) {
    // Presence only - the shape inside is the clients' business, and a server
    // that learned it would need updating whenever it changed.
    tc_assert_same(null, tc_snapshot_validate('{"e2ee":{"anything":1}}'),
                   'the server started reading the envelope');
    // But it does have to be an object: a scalar there is not an envelope,
    // and the plain checks must then still apply.
    tc_assert_same('snapshot_shape', tc_snapshot_validate('{"e2ee":"nope"}'),
                   'a scalar passed as an envelope');
});

tc_test('a sealed snapshot is stored and handed back byte for byte', function ($store, $uid) {
    $head = tc_fill($store, $uid, 'dev0000000000001', 3);
    $raw = tc_sealed_document();
    $result = tc_snapshot_put($store, $uid, 'dev0000000000001', $head, $raw);
    tc_assert(empty($result['error']), 'the snapshot was not accepted');

    $snap = tc_snapshot_meta($store, $uid);
    $stored = file_get_contents(tc_snapshot_file($store, $uid, $snap));
    $body = substr($stored, strlen(TC_GUARD));
    tc_assert_same($raw, $body, 'the stored bytes are not the ones sent');

    $back = json_decode($body, true);
    tc_assert(isset($back[TC_SNAPSHOT_SEALED]['c']), 'the envelope did not survive');
    foreach (['projects', 'task_name', 'tasks'] as $absent) {
        tc_assert(strpos($body, $absent) === false,
                  sprintf('%s reached the stored snapshot', $absent));
    }
});

// ---------------------------------------------------------------------------
// How the server decides a request arrived over TLS.
//
// The whole API sits behind this one answer - index.php asks it before it
// will say anything at all - so a mistake here is either a server that
// refuses every request or one that accepts a credential over plain HTTP.
// ---------------------------------------------------------------------------

require_once __DIR__ . '/tc/lib/http.php';

/** Runs one decision against a made-up request environment. */
function tc_tls_with($mode, array $server)
{
    $saved = $_SERVER;
    foreach (['HTTPS', 'SERVER_PORT', 'HTTP_X_FORWARDED_PROTO'] as $key) {
        unset($_SERVER[$key]);
    }
    $_SERVER = array_merge($_SERVER, $server);
    try {
        return tc_transport_is_tls($mode);
    } finally {
        $_SERVER = $saved;
    }
}

tc_test('auto is what every installation had before the setting', function () {
    tc_assert_same(true, tc_tls_with('auto', ['HTTPS' => 'on']), 'HTTPS on');
    tc_assert_same(true, tc_tls_with('auto', ['HTTPS' => '1']), 'HTTPS 1');
    tc_assert_same(false, tc_tls_with('auto', ['HTTPS' => 'off', 'SERVER_PORT' => '80']),
                   'HTTPS off on port 80');
    tc_assert_same(true, tc_tls_with('auto', ['SERVER_PORT' => '443']), 'port 443');
    tc_assert_same(false, tc_tls_with('auto', ['SERVER_PORT' => '8080']), 'port 8080');
    tc_assert_same(false, tc_tls_with('auto', []), 'nothing to go on');
});

tc_test('strict does not guess from the port', function () {
    // The whole point of it: a plaintext request arriving on 443 passes under
    // 'auto' and must not here.
    tc_assert_same(false, tc_tls_with('strict', ['SERVER_PORT' => '443']),
                   'the port was still believed');
    tc_assert_same(true, tc_tls_with('strict', ['HTTPS' => 'on']), 'HTTPS on');
    tc_assert_same(false, tc_tls_with('strict', ['HTTPS' => 'off']), 'HTTPS off');
});

tc_test('proxy believes the front end, in both directions', function () {
    tc_assert_same(true, tc_tls_with('proxy', ['HTTP_X_FORWARDED_PROTO' => 'https']),
                   'the proxy said https');
    tc_assert_same(true, tc_tls_with('proxy', ['HTTP_X_FORWARDED_PROTO' => 'HTTPS']),
                   'case should not matter');
    // More than one hop: the first entry is what the client itself spoke.
    tc_assert_same(true, tc_tls_with('proxy', ['HTTP_X_FORWARDED_PROTO' => 'https, http']),
                   'a list of hops');
    tc_assert_same(false, tc_tls_with('proxy', ['HTTP_X_FORWARDED_PROTO' => 'http',
                                                'SERVER_PORT' => '443']),
                   'the proxy said http and was not believed');
});

tc_test('proxy without the header falls back rather than refusing', function () {
    // A front end that speaks TLS onwards as well sets HTTPS instead, and
    // turning that away would be wrong.
    tc_assert_same(true, tc_tls_with('proxy', ['HTTPS' => 'on']), 'HTTPS on');
    tc_assert_same(false, tc_tls_with('proxy', ['SERVER_PORT' => '80']), 'nothing at all');
});

tc_test('the header is ignored unless it was asked for', function () {
    // Under 'auto' and 'strict' it is the client talking about itself.
    foreach (['auto', 'strict'] as $mode) {
        tc_assert_same(false,
            tc_tls_with($mode, ['HTTP_X_FORWARDED_PROTO' => 'https', 'SERVER_PORT' => '80']),
            $mode . ' believed a header it should not');
    }
});

tc_test('a typo in the setting does not lock everybody out', function () {
    // A server that refuses every request because of a misspelt mode would
    // look broken, and the way out of it is not obvious from the outside.
    tc_assert_same(true, tc_tls_with('Proxy', ['HTTPS' => 'on']), 'wrong case');
    tc_assert_same(true, tc_tls_with('', ['HTTPS' => 'on']), 'empty');
    tc_assert_same(true, tc_tls_with('nonsense', ['SERVER_PORT' => '443']),
                   'unknown mode should behave as auto');
});

// ---------------------------------------------------------------------------
// Signing in during a flood (issue #585).
//
// The password-checking allowance is one global counter, and whoever reaches
// ?a=login can spend it. These are the pieces that keep the owner able to sign
// in anyway: a reserve, and the one fact that decides who may draw on it.
// ---------------------------------------------------------------------------

/**
 * An account with a real password hash, written where setup.php puts one - but
 * directly, because these tests need to choose its uid and switch it off.
 */
function tc_account($store, $uid, $username = 'frank', $disabled = false)
{
    $record = ['username' => $username, 'uid' => $uid,
               'pass' => password_hash('richtig', PASSWORD_BCRYPT, ['cost' => 4])];
    if ($disabled) {
        $record['disabled'] = true;
    }
    tc_secure_mkdir(tc_accounts_dir($store));
    tc_write_json(tc_account_file($store, $username), $record);
    tc_secure_mkdir(tc_tokens_dir($store));
    tc_secure_mkdir(tc_user_dir($store, $uid));
}

tc_test('the allowance counts, and refuses at its limit', function ($store, $uid) {
    for ($i = 0; $i < 3; $i++) {
        tc_assert_same(true, tc_budget_take($store, 'probe', 3), 'take ' . ($i + 1));
    }
    tc_assert_same(false, tc_budget_take($store, 'probe', 3), 'the fourth was allowed');
});

tc_test('a new minute starts the count again', function ($store, $uid) {
    tc_write_json($store . '/probe.dat.php', ['win' => intdiv(time(), 60) - 1, 'n' => 99]);
    tc_assert_same(true, tc_budget_take($store, 'probe', 3),
                   'last minute\'s count was still held against this one');
});

tc_test('the reserve and the allowance do not share a count', function ($store, $uid) {
    // The reserve is only any use if spending the allowance leaves it alone.
    tc_write_json($store . '/rate.dat.php',
                  ['win' => intdiv(time(), 60), 'n' => TC_HASH_BUDGET_PER_MINUTE]);
    tc_assert_same(false, tc_hash_budget_take($store), 'the allowance was not spent');
    tc_assert_same(true, tc_hash_reserve_take($store), 'the reserve went with it');
});

tc_test('the reserve runs out too', function ($store, $uid) {
    // It is a lever as well, only one fewer hands can reach - so it has an end.
    for ($i = 0; $i < TC_HASH_RESERVE_PER_MINUTE; $i++) {
        tc_hash_reserve_take($store);
    }
    tc_assert_same(false, tc_hash_reserve_take($store), 'the reserve had no limit');
});

tc_test('a device this account signed in from is recognised', function ($store, $uid) {
    tc_account($store, $uid);
    tc_token_issue($store, $uid, 'a1b2c3d4e5f60718', 'laptop');
    tc_assert_same(true, tc_login_from_known_device($store, 'frank', 'a1b2c3d4e5f60718'),
                   'the device that signed in was not recognised');
});

tc_test('and it stays recognised once its token has expired', function ($store, $uid) {
    // One of the two cases the lockout hurt: recovering after expiry. The
    // token file goes; the device entry has to outlive it.
    tc_account($store, $uid);
    $issued = tc_token_issue($store, $uid, 'a1b2c3d4e5f60718', 'laptop');
    $tokenId = explode('.', $issued['token'])[1];
    $file = tc_tokens_dir($store) . '/' . $tokenId . '.dat.php';
    $record = tc_read_json($file);
    $record['exp'] = time() - 1;
    tc_write_json($file, $record);
    tc_assert_same(null, tc_token_check($store, $issued['token']), 'the token did not expire');

    tc_assert_same(true, tc_login_from_known_device($store, 'frank', 'a1b2c3d4e5f60718'),
                   'an expired token took the device with it');
});

tc_test('and once it has signed out', function ($store, $uid) {
    // The other case: somebody signed out on purpose and wants back in.
    tc_account($store, $uid);
    $issued = tc_token_issue($store, $uid, 'a1b2c3d4e5f60718', 'laptop');
    tc_token_revoke($store, explode('.', $issued['token'])[1]);
    tc_assert_same(true, tc_login_from_known_device($store, 'frank', 'a1b2c3d4e5f60718'),
                   'signing out made the device a stranger');
});

tc_test('a device this account never signed in from is not', function ($store, $uid) {
    tc_account($store, $uid);
    tc_token_issue($store, $uid, 'a1b2c3d4e5f60718', 'laptop');
    tc_assert_same(false, tc_login_from_known_device($store, 'frank', 'ffffffffffffffff'),
                   'a guessed device id was let into the reserve');
});

tc_test('a known device under the wrong account is not', function ($store, $uid) {
    // Recognition is per account. Otherwise a device of one user would be a
    // way past the flood into somebody else's.
    tc_account($store, $uid);
    tc_token_issue($store, $uid, 'a1b2c3d4e5f60718', 'laptop');
    tc_assert_same(false, tc_login_from_known_device($store, 'nobody', 'a1b2c3d4e5f60718'),
                   'the device was recognised for a name it never used');
});

tc_test('a disabled account recognises nothing', function ($store, $uid) {
    // Switching an account off has to close it completely, not leave it one
    // door the flood cannot reach.
    tc_account($store, $uid);
    tc_token_issue($store, $uid, 'a1b2c3d4e5f60718', 'laptop');
    tc_account($store, $uid, 'frank', true);
    tc_assert_same(false, tc_login_from_known_device($store, 'frank', 'a1b2c3d4e5f60718'),
                   'a disabled account still let a device into the reserve');
});

tc_test('anything not shaped like a device id is nobody', function ($store, $uid) {
    tc_account($store, $uid);
    tc_token_issue($store, $uid, 'a1b2c3d4e5f60718', 'laptop');
    foreach (['', 'short', '../../etc/passwd', 'A1B2C3D4E5F60718', null, 17] as $bad) {
        tc_assert_same(false, tc_login_from_known_device($store, 'frank', $bad),
                       'a malformed device id was recognised: ' . var_export($bad, true));
    }
});

tc_test('a malformed id is refused even when the stored list holds one', function ($store, $uid) {
    // The shape check looks redundant - a device id only ever matches by exact
    // comparison against ids the server wrote itself. It stops being so the
    // moment the list is not what the server wrote: user.dat.php edited by
    // hand, restored from somewhere, or damaged. Then an entry like the one
    // below is there to be matched, and only the check keeps it out.
    tc_account($store, $uid);
    tc_write_json(tc_user_dir($store, $uid) . '/user.dat.php',
                  ['devices' => [['device_uid' => '../../etc']]]);
    tc_assert_same(false, tc_login_from_known_device($store, 'frank', '../../etc'),
                   'a malformed id matched a malformed entry');
});

tc_test('recognising a device hashes nothing', function ($store, $uid) {
    // The whole reason it can stand in front of the hash: a check as costly
    // as the thing it guards would be no help against somebody spending those.
    tc_account($store, $uid);
    tc_token_issue($store, $uid, 'a1b2c3d4e5f60718', 'laptop');
    $start = microtime(true);
    for ($i = 0; $i < 50; $i++) {
        tc_login_from_known_device($store, 'frank', 'a1b2c3d4e5f60718');
    }
    $each = (microtime(true) - $start) / 50;
    tc_assert($each < 0.01, sprintf('each check took %.1f ms - that is a hash, not a read',
                                     $each * 1000));
});

// ---------------------------------------------------------------------------
// How much one account may keep (issue #586).
//
// Filling fifty megabytes for real would make every run slow. Usage is read
// from the account's own bookkeeping, so it is steered here by planting a
// retired segment with a large size in the state. Retired segments are out of
// the reading path, so nothing else notices it is not really there - which is
// also why the sweep can delete it harmlessly.
// ---------------------------------------------------------------------------

/** Makes the account look this many bytes full, as a retired segment. */
function tc_plant_usage($store, $uid, $bytes, $at = null)
{
    $state = tc_log_state($store, $uid);
    $state['retired'][] = ['f' => 'seg-planted.log.php', 'first' => 0, 'last' => 0,
                           'bytes' => $bytes, 'n' => 0, 'at' => $at ?? time()];
    tc_secure_mkdir(tc_log_dir($store, $uid));
    tc_write_json(tc_log_state_path($store, $uid), $state);
}

tc_test('usage counts every part of the log that is on disk', function ($store, $uid) {
    tc_assert_same(0, tc_account_usage(['segments' => []]), 'an empty log');
    tc_assert_same(18, tc_account_usage([
        'segments' => [['bytes' => 5], ['bytes' => 3]],
        'retired' => [['bytes' => 4]],
        'snapshot' => ['bytes' => 2],
        'snapshot_previous' => ['bytes' => 4],
    ]), 'live, retired and both snapshots together');
});

tc_test('an append that fits is taken', function ($store, $uid) {
    $result = tc_log_append($store, $uid, 'dev0000000000001', tc_ops(3));
    tc_assert(empty($result['error']), 'a small append was refused');
    tc_assert_same(3, $result['head'], 'it was not recorded');
});

tc_test('an append that would cross the limit is refused whole', function ($store, $uid) {
    tc_fill($store, $uid, 'dev0000000000001', 2);
    tc_plant_usage($store, $uid, TC_ACCOUNT_QUOTA_BYTES - 10);

    $result = tc_log_append($store, $uid, 'dev0000000000001', tc_ops(3, 100));
    tc_assert_same('quota_exceeded', $result['error'] ?? null, 'the append went through');
    tc_assert_same(TC_ACCOUNT_QUOTA_BYTES, $result['quota'], 'the limit was not reported');
    tc_assert($result['usage'] >= TC_ACCOUNT_QUOTA_BYTES - 10, 'the usage was not reported');
});

tc_test('what is about to be written counts, not only what is there', function ($store, $uid) {
    // Just under the limit, with room for less than one operation. A check of
    // the usage alone would find the account not yet full and let the write
    // through that takes it over - so the new bytes have to be in the sum.
    tc_plant_usage($store, $uid, TC_ACCOUNT_QUOTA_BYTES - 5);
    tc_assert(tc_account_usage(tc_log_state($store, $uid)) < TC_ACCOUNT_QUOTA_BYTES,
              'the account should start just under its limit');

    $result = tc_log_append($store, $uid, 'dev0000000000001', tc_ops(1));
    tc_assert_same('quota_exceeded', $result['error'] ?? null,
                   'the write that crosses the limit was let through');
});

tc_test('a refused append leaves nothing behind', function ($store, $uid) {
    // No sequence numbers handed out, no counter moved: the client has to be
    // able to offer exactly these operations again once there is room, and
    // have them land as new rather than be waved away as duplicates.
    tc_fill($store, $uid, 'dev0000000000001', 2);
    $before = tc_log_state($store, $uid);
    tc_plant_usage($store, $uid, TC_ACCOUNT_QUOTA_BYTES);

    tc_log_append($store, $uid, 'dev0000000000001', tc_ops(3, 100));
    $after = tc_log_state($store, $uid);
    tc_assert_same((int)$before['head'], (int)$after['head'], 'head moved');
    tc_assert_same((int)$before['devices']['dev0000000000001']['max_lc'],
                   (int)$after['devices']['dev0000000000001']['max_lc'],
                   'the duplicate counter moved for work that was not stored');
    tc_assert_same(2, count(tc_read_all($store, $uid, 0)), 'something was written');
});

tc_test('a batch of nothing but repeats is not refused', function ($store, $uid) {
    // Nothing would be written, so there is nothing to refuse - and refusing
    // would stop a client from learning that its earlier push had landed.
    tc_fill($store, $uid, 'dev0000000000001', 3);
    tc_plant_usage($store, $uid, TC_ACCOUNT_QUOTA_BYTES * 2);
    $result = tc_log_append($store, $uid, 'dev0000000000001', tc_ops(3));
    tc_assert(empty($result['error']), 'repeats were refused as if they took space');
    tc_assert_same([1, 2, 3], $result['dups'], 'the repeats were not reported as such');
});

tc_test('a snapshot is accepted even when the account is full', function ($store, $uid) {
    // It is the only thing that makes an account smaller. Refusing it for
    // being over the limit would leave a full account full for good.
    $head = tc_fill($store, $uid, 'dev0000000000001', 4);
    tc_plant_usage($store, $uid, TC_ACCOUNT_QUOTA_BYTES * 2);
    $result = tc_snapshot_put($store, $uid, 'dev0000000000001', $head, tc_document());
    tc_assert(is_array($result) && empty($result['error']), 'a full account could not compact');
});

tc_test('and a full account gets its space back at once, not in a week', function ($store, $uid) {
    // With the ordinary grace the segments the snapshot replaced would stay
    // for seven days, the account would stay over its limit, and every push
    // in that week would still be refused.
    $head = tc_fill($store, $uid, 'dev0000000000001', 4);
    tc_plant_usage($store, $uid, TC_ACCOUNT_QUOTA_BYTES * 2);
    tc_snapshot_put($store, $uid, 'dev0000000000001', $head, tc_document());

    $state = tc_log_state($store, $uid);
    tc_assert_same(0, count($state['retired']), 'retired segments were kept for their grace');
    tc_assert(tc_account_usage($state) < TC_ACCOUNT_QUOTA_BYTES,
              'the account is still over its limit after compacting');

    $again = tc_log_append($store, $uid, 'dev0000000000001', tc_ops(2, 500));
    tc_assert(empty($again['error']), 'pushes are still refused after compacting');
});

tc_test('an account with room keeps the week of grace', function ($store, $uid) {
    // The grace is a safety margin for a snapshot that turns out to be wrong.
    // Giving it up is only worth it when there is no space for it.
    $head = tc_fill($store, $uid, 'dev0000000000001', 4);
    tc_snapshot_put($store, $uid, 'dev0000000000001', $head, tc_document());
    tc_assert(count(tc_log_state($store, $uid)['retired']) > 0,
              'an account with plenty of room lost its grace period');
});

// ---------------------------------------------------------------------------
// One file per account (issue #587).
//
// The accounts used to be one list, read whole on every sign-in and written
// whole on every change. What matters now is that each account is on its own:
// found by one computed path, changed without touching any other - and that an
// installation from before still has every account after the update.
// ---------------------------------------------------------------------------

print("\nAccounts\n");

/** The old list, as every installation from before this has it. */
function tc_legacy_accounts($store, array $users)
{
    tc_write_json(tc_users_file($store), ['users' => $users]);
}

tc_test('an account is found by its name', function ($store, $uid) {
    $made = tc_account_create($store, 'frank', password_hash('richtig', PASSWORD_BCRYPT, ['cost' => 4]));
    $user = tc_user_find($store, 'frank');
    tc_assert(is_array($user), 'the account just created was not found');
    tc_assert_same($made['uid'], $user['uid'], 'uid');
    tc_assert_same('frank', $user['username'], 'username');
    tc_assert(password_verify('richtig', $user['pass']), 'the password does not check');
    tc_assert(is_file(tc_user_dir($store, $made['uid']) . '/user.dat.php'),
              'the account has nowhere to keep its devices');
});

tc_test('a name that differs only in case is another account', function ($store, $uid) {
    // As it always was: the old list was keyed by the exact name. A filesystem
    // that folds case must not quietly make two accounts one.
    $lower = tc_account_create($store, 'frank', 'x');
    tc_assert_same(null, tc_user_find($store, 'Frank'), 'Frank found frank');
    $upper = tc_account_create($store, 'Frank', 'y');
    tc_assert(isset($upper['uid']), 'Frank was refused as taken: ' . json_encode($upper));
    tc_assert_same($lower['uid'], tc_user_find($store, 'frank')['uid'], 'frank was replaced');
});

tc_test('a name that is taken is refused, and its account left alone', function ($store, $uid) {
    $first = tc_account_create($store, 'frank', 'x');
    tc_assert_same(['error' => 'exists'], tc_account_create($store, 'frank', 'y'), 'second');
    tc_assert_same($first['uid'], tc_user_find($store, 'frank')['uid'], 'the account was replaced');
});

tc_test('adding an account rewrites no other', function ($store, $uid) {
    // The whole of the issue. Rewriting was what grew with the count: the
    // time the lock is held, and what a failed write could take with it.
    for ($i = 0; $i < 30; $i++) {
        tc_account_create($store, 'user' . $i, 'x');
    }
    $before = [];
    foreach (glob(tc_accounts_dir($store) . '/*') as $path) {
        $before[$path] = fileinode($path) . ':' . md5_file($path);
    }
    tc_account_create($store, 'one-more', 'x');
    clearstatcache();
    foreach ($before as $path => $was) {
        tc_assert_same($was, fileinode($path) . ':' . md5_file($path),
                       'adding an account rewrote ' . basename($path));
    }
    tc_assert(!is_file(tc_users_file($store)), 'a list of all accounts is still being written');
});

tc_test('a damaged account costs nobody else theirs', function ($store, $uid) {
    tc_account_create($store, 'anna', 'x');
    $bert = tc_account_create($store, 'bert', 'x');
    file_put_contents(tc_account_file($store, 'anna'), TC_GUARD . '{"username": "an');
    tc_assert_same(null, tc_user_find($store, 'anna'), 'a damaged record was read');
    tc_assert_same($bert['uid'], tc_user_find($store, 'bert')['uid'], 'bert went with anna');
    tc_assert(isset(tc_account_create($store, 'carl', 'x')['uid']),
              'nobody can be added while one record is damaged');
});

tc_test('a name is looked up, never followed as a path', function ($store, $uid) {
    // At sign-in the name is whatever the caller sent. Were it the filename,
    // this one would reach a file outside the accounts directory.
    tc_secure_mkdir(tc_accounts_dir($store));
    tc_write_json($store . '/evil.dat.php', ['username' => '../evil', 'uid' => 'x', 'pass' => 'x']);
    foreach (['../evil', str_repeat('x', 5000), "a\0b", '', '.', '..'] as $name) {
        tc_assert_same(null, tc_user_find($store, $name), 'found ' . var_export($name, true));
    }
});

tc_test('a record under somebody else\'s name finds nobody', function ($store, $uid) {
    // A file copied or restored to the wrong place must not become a way into
    // the account it describes, under a name that account does not have.
    tc_account_create($store, 'anna', 'x');
    tc_secure_mkdir(tc_accounts_dir($store));
    copy(tc_account_file($store, 'anna'), tc_account_file($store, 'bert'));
    tc_assert_same(null, tc_user_find($store, 'bert'), 'bert signed in as anna');
});

tc_test('Show status lists every account, in order, and only those', function ($store, $uid) {
    // The stray one has a name of its own. A copy of alpha would merge into
    // alpha in the listing and prove nothing.
    tc_account_create($store, 'zeta', 'x');
    tc_account_create($store, 'alpha', 'x');
    tc_write_json(tc_accounts_dir($store) . '/' . str_repeat('0', 64) . '.dat.php',
                  ['username' => 'ghost', 'uid' => 'uid-ghost', 'pass' => 'x']);
    tc_assert_same(['alpha', 'zeta'], array_keys(tc_accounts_list($store)), 'listed');
});

tc_test('an installation from before keeps every account', function ($store, $uid) {
    tc_legacy_accounts($store, [
        'frank' => ['uid' => 'uid-frank', 'pass' => 'hash-frank', 'created' => '2025-01-01'],
        'anna'  => ['uid' => 'uid-anna', 'pass' => 'hash-anna', 'disabled' => true],
        '1234'  => ['uid' => 'uid-1234', 'pass' => 'hash-1234'],
    ]);
    $frank = tc_user_find($store, 'frank');
    tc_assert(!is_file(tc_users_file($store)), 'the old list was not converted');
    tc_assert_same(['username' => 'frank', 'uid' => 'uid-frank', 'pass' => 'hash-frank',
                    'created' => '2025-01-01'], $frank, 'frank');
    tc_assert_same(true, tc_user_find($store, 'anna')['disabled'] ?? null,
                   'a switched-off account came back switched on');
    // A name of digits is an integer key to PHP, and must still be found.
    tc_assert_same('uid-1234', tc_user_find($store, '1234')['uid'] ?? null, '1234');
});

tc_test('an old list that cannot be read is left exactly as it was', function ($store, $uid) {
    // Adding an account to a list that could not be read used to begin a new
    // one, holding only the new account. Here nothing may be lost that way.
    $damaged = TC_GUARD . '{"users": {"frank": {"uid": "uid-fr';
    file_put_contents(tc_users_file($store), $damaged);
    tc_assert_same(null, tc_user_find($store, 'frank'), 'found in a damaged list');
    tc_assert_same(['error' => 'unconverted'], tc_account_create($store, 'new', 'x'),
                   'an account was added beside a list that could not be read');
    tc_assert_same($damaged, file_get_contents(tc_users_file($store)), 'the old list was touched');
    tc_assert_same([], glob(tc_accounts_dir($store) . '/*') ?: [], 'accounts were written');
});

tc_test('while it cannot be converted, signing in works from the old list', function ($store, $uid) {
    // A full disk, say. The owner must still be able to sign in, and nothing
    // may add or remove an account behind the list's back meanwhile.
    tc_legacy_accounts($store, ['frank' => ['uid' => 'uid-frank', 'pass' => 'x']]);
    file_put_contents(tc_accounts_dir($store), 'in the way');
    tc_assert_same('uid-frank', tc_user_find($store, 'frank')['uid'] ?? null, 'frank');
    tc_assert_same(['frank'], array_keys(tc_accounts_list($store)), 'Show status');
    tc_assert_same(['error' => 'unconverted'], tc_account_create($store, 'new', 'x'), 'create');
    tc_assert(is_file(tc_users_file($store)), 'the old list went without being converted');

    // And the next request that can, finishes it.
    unlink(tc_accounts_dir($store));
    tc_assert_same('uid-frank', tc_user_find($store, 'frank')['uid'] ?? null, 'frank, after');
    tc_assert(!is_file(tc_users_file($store)), 'the conversion was never finished');
});

tc_test('until the old list is gone, it is what counts', function ($store, $uid) {
    // An interrupted conversion leaves files behind, and the old list may have
    // been edited since - to switch an account off, say. The list wins.
    tc_legacy_accounts($store, ['frank' => ['uid' => 'uid-frank', 'pass' => 'x', 'disabled' => true]]);
    tc_secure_mkdir(tc_accounts_dir($store));
    tc_write_json(tc_account_file($store, 'frank'),
                  ['username' => 'frank', 'uid' => 'uid-frank', 'pass' => 'x']);
    tc_assert_same(true, tc_user_find($store, 'frank')['disabled'] ?? null,
                   'what an interrupted run left overrode the list');
});

// ---------------------------------------------------------------------------
// Invitations (issue #588).
//
// A code the operator makes, exchanged once for an account. What has to hold:
// nothing is hashed without a valid code, a code makes one account, and a
// refusal that is not the invitee's fault - a taken name, a full disk, a busy
// server - leaves the code as good as it was.
// ---------------------------------------------------------------------------

print("\nInvitations\n");

const TC_TEST_PASSWORD = 'long enough, surely';

/** How long one real password hash takes here - what "nothing was hashed" is measured against. */
function tc_hash_seconds()
{
    static $seconds = null;
    if ($seconds === null) {
        $t = microtime(true);
        password_hash('x', PASSWORD_BCRYPT, ['cost' => TC_BCRYPT_COST]);
        $seconds = microtime(true) - $t;
    }
    return $seconds;
}

/** Runs $body and says whether it took anything like as long as one hash. */
function tc_assert_no_hash(callable $body, $what)
{
    $t = microtime(true);
    $result = $body();
    $took = microtime(true) - $t;
    tc_assert($took < tc_hash_seconds() / 4, sprintf('%s took %.0f ms, a hash takes %.0f ms',
              $what, $took * 1000, tc_hash_seconds() * 1000));
    return $result;
}

tc_test('an invitation is made where there was no place for one, and only its hash is kept', function ($store, $uid) {
    // Every store from before this version has no invites/ directory.
    tc_assert(!is_dir(tc_invites_dir($store)), 'the test store already had one');
    $made = tc_invite_create($store, 'for Anna');
    tc_assert(preg_match('/^[a-f0-9]{16}$/D', $made['code'] ?? '') === 1, 'code: ' . json_encode($made));
    tc_assert_same(TC_INVITE_TTL, $made['expires'] - $made['created'], 'lifetime');
    tc_assert_same('for Anna', $made['note'], 'note');
    foreach (glob(tc_invites_dir($store) . '/*') as $path) {
        tc_assert(strpos(basename($path) . file_get_contents($path), $made['code']) === false,
                  'the code itself is in the store: ' . basename($path));
    }
});

tc_test('a code is found however it was pasted', function ($store, $uid) {
    $code = tc_invite_create($store)['code'];
    $grouped = implode('-', str_split($code, 4));
    foreach ([$code, strtoupper($grouped), ' ' . $grouped . "\n",
              str_replace('-', "\u{00A0}", $grouped),      // no-break space
              str_replace('-', "\u{2013}", $grouped),      // en dash
              str_replace('-', "\u{2011}", $grouped),      // non-breaking hyphen
              implode("\u{200B}", str_split($code, 4)),
              // out of a sentence, with its quotes and full stop
              '"' . $grouped . '".', "\u{201E}" . $code . "\u{201C}", '(' . $code . ')'] as $pasted) {
        tc_assert(tc_invite_find($store, $pasted) !== null, 'not found: ' . json_encode($pasted));
    }
    // A letter is never taken out, whatever it is: what is left is the code
    // or it is not one. Dropping everything but hex would read the first two
    // as codes as well - one of them somebody else's.
    foreach (['Code: ' . $code, substr($code, 0, 8) . 'x' . substr($code, 8),
              'o' . $code, substr($code, 1), $code . 'a', '', null, 17, [$code]] as $wrong) {
        tc_assert_same(null, tc_invite_find($store, $wrong), 'found: ' . json_encode($wrong));
    }
});

tc_test('an expired invitation is refused, and removed on the way', function ($store, $uid) {
    $code = tc_invite_create($store)['code'];
    $path = tc_invite_file($store, $code);
    $record = tc_read_json($path);
    $record['expires'] = time() - 1;
    tc_write_json($path, $record);
    tc_assert_same(['error' => 'invalid_invite'],
                   tc_invite_redeem($store, $code, 'anna', TC_TEST_PASSWORD), 'redeemed');
    tc_assert(!is_file($path), 'an expired invitation was kept');
});

tc_test('redeeming makes the account the invitee chose', function ($store, $uid) {
    $code = tc_invite_create($store, 'for Anna')['code'];
    $made = tc_invite_redeem($store, $code, 'anna', TC_TEST_PASSWORD);
    tc_assert(isset($made['uid']), json_encode($made));
    $user = tc_user_find($store, 'anna');
    tc_assert_same($made['uid'], $user['uid'] ?? null, 'uid');
    tc_assert(password_verify(TC_TEST_PASSWORD, $user['pass']), 'the password does not check');
    tc_assert_same('for Anna', $user['invited']['note'] ?? null, 'where the account came from');
    tc_assert(is_file(tc_user_dir($store, $made['uid']) . '/user.dat.php'), 'no place for devices');
    tc_assert_same([], glob(tc_invites_dir($store) . '/*') ?: [], 'the code, or its claim, is left');
});

tc_test('a code makes one account', function ($store, $uid) {
    $code = tc_invite_create($store)['code'];
    tc_invite_redeem($store, $code, 'anna', TC_TEST_PASSWORD);
    tc_assert_same(['error' => 'invalid_invite'],
                   tc_invite_redeem($store, $code, 'bert', TC_TEST_PASSWORD), 'second');
    tc_assert_same(null, tc_user_find($store, 'bert'), 'a second account was made');
});

tc_test('five requests racing with one code make one account', function ($store, $uid) {
    // Separate processes, started together: the case the lock and the claim
    // are both there for.
    $code = tc_invite_create($store)['code'];
    $running = [];
    for ($i = 0; $i < 5; $i++) {
        // Each reports the CPU time it spent redeeming, which is how a hash
        // shows: waiting for the lock costs next to none.
        $script = sprintf('require %s; $u = getrusage(); $r = tc_invite_redeem(%s, %s, %s, %s); '
                          . '$v = getrusage(); $r["cpu"] = ($v["ru_utime.tv_sec"] - $u["ru_utime.tv_sec"]) '
                          . '+ ($v["ru_utime.tv_usec"] - $u["ru_utime.tv_usec"]) / 1e6; echo json_encode($r);',
                          var_export(__DIR__ . '/tc/lib/auth.php', true), var_export($store, true),
                          var_export($code, true), var_export('racer' . $i, true),
                          var_export(TC_TEST_PASSWORD, true));
        $process = proc_open([PHP_BINARY, '-r', $script], [1 => ['pipe', 'w']], $pipes);
        $running[] = [$process, $pipes[1]];
    }
    $won = 0;
    $hashed = 0;
    foreach ($running as [$process, $out]) {
        $answer = json_decode(stream_get_contents($out), true);
        proc_close($process);
        $won += isset($answer['uid']) ? 1 : 0;
        $hashed += ($answer['cpu'] ?? 0) > tc_hash_seconds() / 2 ? 1 : 0;
    }
    tc_assert_same(1, $won, 'accounts made');
    tc_assert_same(1, count(tc_accounts_list($store)), 'accounts there');
    // The point of hashing under the lock: the ones that lost queued for it
    // and found the code gone, rather than each paying for a hash first.
    tc_assert_same(1, $hashed, 'requests that paid for a hash');
});

tc_test('a wrong code is refused before anything is hashed', function ($store, $uid) {
    // The rule the issue puts above the rest: a gate after bcrypt is not one.
    // With a name that exists, too: answering "taken" to somebody without a
    // code would list the accounts for free.
    tc_invite_create($store);
    tc_account_create($store, 'anna', 'x');
    $answer = tc_assert_no_hash(function () use ($store) {
        return tc_invite_redeem($store, 'ffffffffffffffff', 'anna', TC_TEST_PASSWORD);
    }, 'a wrong code');
    tc_assert_same(['error' => 'invalid_invite'], $answer, 'answer');
});

tc_test('and before the lock, which somebody else may be holding', function ($store, $uid) {
    // Otherwise every guess would queue behind a real redemption, and take it
    // five seconds to be told no.
    tc_account_create($store, 'anna', 'x');
    $held = tc_lock(tc_users_lock($store));
    try {
        $answer = tc_assert_no_hash(function () use ($store) {
            return tc_invite_redeem($store, 'ffffffffffffffff', 'anna', TC_TEST_PASSWORD);
        }, 'a wrong code while the lock is held');
    } finally {
        tc_unlock($held);
    }
    tc_assert_same(['error' => 'invalid_invite'], $answer, 'answer');
});

tc_test('a taken name is refused without hashing, and leaves the code usable', function ($store, $uid) {
    tc_account_create($store, 'anna', 'x');
    $code = tc_invite_create($store)['code'];
    $answer = tc_assert_no_hash(function () use ($store, $code) {
        return tc_invite_redeem($store, $code, 'anna', TC_TEST_PASSWORD);
    }, 'a taken name');
    tc_assert_same(['error' => 'username_taken'], $answer, 'answer');
    tc_assert(isset(tc_invite_redeem($store, $code, 'anna2', TC_TEST_PASSWORD)['uid']),
              'the code was used up by a name that was taken');
});

tc_test('names and passwords that are refused leave the code usable', function ($store, $uid) {
    $code = tc_invite_create($store)['code'];
    $refused = [
        ['an', 'bad_username'], ['anna maria', 'bad_username'], [str_repeat('a', 33), 'bad_username'],
        // '$' alone would let this one through, as a second "anna" that looks
        // exactly like the first and that setup.php could never delete.
        ["anna\n", 'bad_username'],
    ];
    foreach ($refused as [$name, $error]) {
        tc_assert_same(['error' => $error], tc_invite_redeem($store, $code, $name, TC_TEST_PASSWORD),
                       json_encode($name));
    }
    // Six umlauts are twelve bytes but six characters, and characters are
    // what every message counts.
    foreach (['short', "long enough\0but not", str_repeat('ä', 6)] as $password) {
        // A NUL byte is refused rather than handed to password_hash(), which
        // throws on one and would end the request halfway through.
        tc_assert_same(['error' => 'weak_password'], tc_invite_redeem($store, $code, 'anna', $password),
                       json_encode($password));
    }
    tc_assert(isset(tc_invite_redeem($store, $code, 'anna', TC_TEST_PASSWORD)['uid']),
              'a refusal used the code up');
});

tc_test('setup and register refuse the same names', function ($store, $uid) {
    foreach (['frank', 'a.b-c_d', 'F12', str_repeat('x', 32)] as $name) {
        tc_assert(tc_username_acceptable($name), 'refused: ' . $name);
    }
    foreach (['fr', "frank\n", 'frank ', 'fränk', '../x', str_repeat('x', 33), null] as $name) {
        tc_assert(!tc_username_acceptable($name), 'accepted: ' . json_encode($name));
    }
    tc_assert(!preg_match(TC_DEVICE_UID_PATTERN, "a1b2c3d4e5f60718\n"), 'a device id with a newline');
    tc_assert(tc_password_acceptable(str_repeat('ä', 12)), 'twelve umlauts are twelve characters');
    tc_assert(!tc_password_acceptable(str_repeat('ä', 11)), 'eleven umlauts are not');
});

tc_test('while the old account list cannot be converted, nothing is spent', function ($store, $uid) {
    file_put_contents(tc_users_file($store), TC_GUARD . '{"users": {"anna"');
    $code = tc_invite_create($store)['code'];
    // Not "busy": trying again would meet the same list, until the operator
    // has looked at it.
    tc_assert_same(['error' => 'unconverted'], tc_invite_redeem($store, $code, 'anna', TC_TEST_PASSWORD), 'answer');
    tc_assert(tc_invite_find($store, $code) !== null, 'the code was used up');
    tc_assert_same(['u0000000000000001'], array_map('basename', glob($store . '/users/*')),
                   'an account was begun beside the old list');
});

tc_test('an account that cannot be written leaves the code usable', function ($store, $uid) {
    // A full disk, say: the invitee should not need a new code for it.
    $code = tc_invite_create($store)['code'];
    file_put_contents(tc_accounts_dir($store), 'in the way');
    tc_assert_same(['error' => 'io'], tc_invite_redeem($store, $code, 'anna', TC_TEST_PASSWORD), 'answer');
    tc_assert(tc_invite_find($store, $code) !== null, 'the code was used up');
    tc_assert_same([], glob(tc_invites_dir($store) . '/*.claimed.php') ?: [], 'a claim was left');

    unlink(tc_accounts_dir($store));
    tc_assert(isset(tc_invite_redeem($store, $code, 'anna', TC_TEST_PASSWORD)['uid']), 'and afterwards');
});

tc_test('the code is claimed before an account is made, and a claim that fails makes none', function ($store, $uid) {
    // The claim is what keeps a code to one account where the lock does
    // nothing - and wherever it does, as here, the lock would hide a claim
    // that had stopped working. So it is made to fail on purpose: a
    // directory with something in it cannot be renamed onto.
    $code = tc_invite_create($store)['code'];
    tc_secure_mkdir(tc_invite_claim_file($store, $code) . '/in-the-way');
    $answer = tc_invite_redeem($store, $code, 'anna', TC_TEST_PASSWORD);
    tc_assert_same(null, tc_user_find($store, 'anna'), 'an account was made without the code being claimed');
    // Nobody else took the code, so it is not called used: it is still there.
    tc_assert_same(['error' => 'io'], $answer, 'answer');
    tc_assert(tc_invite_find($store, $code) !== null, 'the code was lost');
});

tc_test('withdrawing waits for a redemption in progress', function ($store, $uid) {
    // Otherwise it could fall between a failed write and the rename that
    // puts that code back, and the code would survive being withdrawn.
    tc_invite_create($store);
    $held = tc_lock(tc_users_lock($store));
    $script = sprintf('require %s; $t = microtime(true); $n = tc_invites_withdraw(%s); '
                      . 'echo json_encode([$n, microtime(true) - $t]);',
                      var_export(__DIR__ . '/tc/lib/auth.php', true), var_export($store, true));
    $process = proc_open([PHP_BINARY, '-r', $script], [1 => ['pipe', 'w']], $pipes);
    usleep(400000);
    tc_unlock($held);
    [$withdrawn, $waited] = json_decode(stream_get_contents($pipes[1]), true);
    proc_close($process);
    tc_assert_same(1, $withdrawn, 'withdrawn');
    tc_assert($waited > 0.2, sprintf('it did not wait for the lock (%.0f ms)', $waited * 1000));
});

tc_test('open invitations are listed, and what is past is tidied away', function ($store, $uid) {
    $late = tc_invite_create($store, 'late');
    $soon = tc_invite_create($store, 'soon');
    $record = tc_read_json(tc_invite_file($store, $soon['code']));
    $record['expires'] = time() + 60;
    tc_write_json(tc_invite_file($store, $soon['code']), $record);
    $gone = tc_invite_create($store, 'gone')['code'];
    $record['expires'] = time() - 1;
    tc_write_json(tc_invite_file($store, $gone), $record);

    // A claim left by a request that died long ago, and one still in progress.
    $stale = tc_invite_claim_file($store, 'aaaaaaaaaaaaaaaa');
    $fresh = tc_invite_claim_file($store, 'bbbbbbbbbbbbbbbb');
    tc_write_json($stale, ['created' => 0]);
    touch($stale, time() - 3600);
    tc_write_json($fresh, ['created' => 0]);

    tc_assert_same(['soon', 'late'], array_column(tc_invites_list($store), 'note'), 'listed');
    tc_assert(!is_file(tc_invite_file($store, $gone)), 'an expired invitation was kept');
    tc_assert(!is_file($stale), 'a claim nobody can still be holding was kept');
    tc_assert(is_file($fresh), 'a claim in progress was taken away');
    tc_assert(tc_invite_find($store, $late['code']) !== null, 'listing spent a code');
});

tc_test('withdrawing stops every open code, and nothing else', function ($store, $uid) {
    $used = tc_invite_create($store)['code'];
    tc_invite_redeem($store, $used, 'anna', TC_TEST_PASSWORD);
    $open = [tc_invite_create($store)['code'], tc_invite_create($store)['code']];
    tc_write_json(tc_invite_claim_file($store, 'cccccccccccccccc'), ['created' => 0]);

    tc_assert_same(2, tc_invites_withdraw($store), 'withdrawn');
    foreach ($open as $code) {
        tc_assert_same(null, tc_invite_find($store, $code), 'a withdrawn code still works');
    }
    tc_assert_same([], glob(tc_invites_dir($store) . '/*') ?: [], 'a claim could still be put back');
    tc_assert(tc_user_find($store, 'anna') !== null, 'withdrawing took an account with it');
});

// ---------------------------------------------------------------------------
// Accounts nobody ever used (issue #589).
//
// Removing accounts automatically is the one thing here that could lose
// somebody's work, so most of these are about what must be kept: anything
// stored at all, anything a device could still reach, anything the operator
// made, anything that cannot be read. And one is about the account that is
// removed while its device is arriving - which must neither be let in nor
// come back as a directory nothing points to.
// ---------------------------------------------------------------------------

print("\nAccounts nobody ever used\n");

/** A day more than removal waits for. */
const TC_TEST_LONG_AGO = TC_UNUSED_SECONDS / 86400 + 1;

/**
 * An account made by redeeming a code, without paying for a real hash, with
 * everything about it - the registration, every device's last contact -
 * having happened $days ago.
 */
function tc_registered($store, $name, $days, $device = null)
{
    $made = tc_account_write($store, $name, 'x', ['invited' => ['note' => 'for ' . $name, 'issued' => 0]], true);
    $uid  = $made['uid'];
    tc_secure_mkdir(tc_tokens_dir($store));
    if ($device !== null) {
        tc_token_issue($store, $uid, $device, 'laptop');
    }
    tc_age($store, $uid, $days);
    return $uid;
}

function tc_age($store, $uid, $days)
{
    $at     = time() - (int)round($days * 86400);
    $path   = tc_unused_marker($store, $uid);
    $marker = tc_read_json($path);
    $marker['since'] = $at;
    tc_write_json($path, $marker);
    touch($path, min(time(), $at + TC_UNUSED_SECONDS + 1));
    foreach (glob(tc_user_dir($store, $uid) . '/seen/*') ?: [] as $seen) {
        touch($seen, $at);
    }
}

function tc_gone($store, $name, $uid)
{
    clearstatcache();
    return tc_user_find($store, $name) === null && !is_dir(tc_user_dir($store, $uid))
        && !is_file(tc_unused_marker($store, $uid));
}

tc_test('only an account made by redeeming a code is looked after this way', function ($store, $uid) {
    $code = tc_invite_create($store)['code'];
    $invited = tc_invite_redeem($store, $code, 'anna', TC_TEST_PASSWORD)['uid'];
    tc_assert(is_file(tc_unused_marker($store, $invited)), 'a registered account is not watched');
    // The operator made this one on purpose - for somebody who starts next
    // month, perhaps - and removes it in Show status if it is not wanted.
    $made = tc_account_create($store, 'bert', 'x')['uid'];
    tc_assert(!is_file(tc_unused_marker($store, $made)), 'an account the operator made is watched');
});

tc_test('one nothing was stored in, that no device could still reach, is removed', function ($store, $uid) {
    $gone = tc_registered($store, 'anna', TC_TEST_LONG_AGO, 'a1b2c3d4e5f60718');
    $token = tc_read_json(tc_user_dir($store, $gone) . '/user.dat.php')['devices'][0]['token_id'];
    tc_assert_same(1, tc_accounts_sweep_unused($store), 'removed');
    tc_assert(tc_gone($store, 'anna', $gone), 'something of it is left');
    tc_assert(!is_file(tc_tokens_dir($store) . '/' . $token . '.dat.php'), 'its token was left');
    tc_assert_same([], glob($store . '/users/.gone-*') ?: [], 'what was moved away was not emptied');
    // Said where the operator can find it: the person is only told their
    // password is wrong.
    tc_assert_same('anna', tc_removed_list($store)[0]['username'] ?? null, 'Show status cannot name it');
    tc_assert(strpos((string)@file_get_contents(ini_get('error_log')), '"anna"') !== false,
              'the removal left no line in the log');
});

tc_test('but not while any of its devices could still sign in without a password', function ($store, $uid) {
    // Until then a token of it still works: somebody back from a fortnight
    // away must find the laptop still synchronising, not a lost account.
    foreach ([8, TC_IDLE_TTL / 86400] as $i => $days) {
        $kept = tc_registered($store, 'anna' . $i, $days, sprintf('a1b2c3d4e5f6071%d', $i));
        tc_assert_same(0, tc_accounts_sweep_unused($store), 'removed after ' . $days . ' days');
        tc_assert(tc_user_find($store, 'anna' . $i) !== null, 'gone after ' . $days . ' days');
    }
});

tc_test('a device that has been in touch recently keeps it', function ($store, $uid) {
    $kept = tc_registered($store, 'anna', TC_TEST_LONG_AGO, 'a1b2c3d4e5f60718');
    touch(tc_user_dir($store, $kept) . '/seen/a1b2c3d4e5f60718');   // it synchronised just now
    tc_assert_same(0, tc_accounts_sweep_unused($store), 'removed');
    clearstatcache();
    tc_assert(filemtime(tc_unused_marker($store, $kept)) > time(),
              'not dated to when it could next be due, so it would be looked at every time');
});

/** Dates a device's token as issued $days ago, in both places it is kept. */
function tc_token_issued($store, $uid, $days)
{
    $at   = time() - (int)round($days * 86400);
    $path = tc_user_dir($store, $uid) . '/user.dat.php';
    $user = tc_read_json($path);
    foreach ($user['devices'] as &$device) {
        $device['iat'] = $at;
        $device['exp'] = $at + TC_TOKEN_TTL;
        $file  = tc_tokens_dir($store) . '/' . $device['token_id'] . '.dat.php';
        $token = tc_read_json($file);
        $token['iat'] = $at;
        $token['exp'] = $at + TC_TOKEN_TTL;
        tc_write_json($file, $token);
    }
    unset($device);
    tc_write_json($path, $user);
}

tc_test('a device listed without a seen entry counts until its token runs out', function ($store, $uid) {
    // tc_token_check cannot apply idle expiry without that entry, so the
    // token lasts its whole life - and the account has to as well. Issued
    // long enough ago that a rule reckoning from when it was issued, rather
    // than from when it runs out, would already have let it go.
    $kept = tc_registered($store, 'anna', 40, 'a1b2c3d4e5f60718');
    tc_token_issued($store, $kept, 40);
    $token = tc_read_json(tc_user_dir($store, $kept) . '/user.dat.php')['devices'][0]['token_id'];
    unlink(tc_user_dir($store, $kept) . '/seen/a1b2c3d4e5f60718');
    tc_assert_same(0, tc_accounts_sweep_unused($store), 'removed while its token still works');
    // Not asked before the sweep: a check that succeeds writes the seen
    // entry again, and the account would be kept for that reason instead.
    tc_assert(is_file(tc_tokens_dir($store) . '/' . $token . '.dat.php'), 'its token went');
});

tc_test('and once it has run out, it counts no longer', function ($store, $uid) {
    // Expired a day longer ago than removal waits: nothing could come back.
    $gone = tc_registered($store, 'anna', TC_TOKEN_TTL / 86400 + TC_TEST_LONG_AGO, 'a1b2c3d4e5f60718');
    tc_token_issued($store, $gone, TC_TOKEN_TTL / 86400 + TC_TEST_LONG_AGO);
    unlink(tc_user_dir($store, $gone) . '/seen/a1b2c3d4e5f60718');
    tc_assert_same(1, tc_accounts_sweep_unused($store), 'kept for a token that ran out long ago');
});

tc_test('a marker is never due from the moment it exists', function ($store, $uid) {
    // Dated before it becomes visible. Otherwise a sweep the lock failed to
    // keep out could, in that instant, take a registration still being
    // written for a removal that had been interrupted.
    $code = tc_invite_create($store)['code'];
    $made = tc_invite_redeem($store, $code, 'anna', TC_TEST_PASSWORD)['uid'];
    clearstatcache();
    tc_assert(filemtime(tc_unused_marker($store, $made)) > time() + TC_UNUSED_SECONDS - 60,
              'the marker was visible before it was dated');
    tc_assert_same([], glob(tc_unused_dir($store) . '/.tmp*') ?: [], 'a temporary file was left');
});

tc_test('a registration still being written is not taken for an interrupted removal', function ($store, $uid) {
    // Its marker there, its record not yet. However the marker came to look
    // due, a young one is left alone.
    $young = tc_account_write($store, 'anna', 'x', [], true)['uid'];
    unlink(tc_account_file($store, 'anna'));
    touch(tc_unused_marker($store, $young), time() - 60);
    tc_accounts_sweep_unused($store);
    tc_assert(is_dir(tc_user_dir($store, $young)), 'the account being made lost its directory');
    tc_assert(is_file(tc_unused_marker($store, $young)), 'and its marker');
});

tc_test('a marker that is not one is thrown away rather than looked at for ever', function ($store, $uid) {
    // An empty one, say, which a touch() at the wrong moment can leave. Left
    // there it would sit at the front of every sweep.
    tc_secure_mkdir(tc_unused_dir($store));
    $junk = tc_unused_dir($store) . '/' . str_repeat('a', 32) . '.dat.php';
    touch($junk, time() - 60);
    tc_accounts_sweep_unused($store);
    tc_assert(!is_file($junk), 'an empty marker was kept');
});

tc_test('anything stored at all keeps it for good', function ($store, $uid) {
    // Even the leftovers of a write that was interrupted: it is not for this
    // to decide what in there matters.
    $kept = tc_registered($store, 'anna', TC_TEST_LONG_AGO);
    tc_secure_mkdir(tc_log_dir($store, $kept));
    file_put_contents(tc_log_dir($store, $kept) . '/.tmp123456', 'x');
    tc_assert_same(0, tc_accounts_sweep_unused($store), 'removed');
    tc_assert(tc_user_find($store, 'anna') !== null, 'an account holding something was removed');
    tc_assert(!is_file(tc_unused_marker($store, $kept)), 'still a candidate, though it is in use');
});

tc_test('and the first thing it stores takes it off the list at once', function ($store, $uid) {
    // Not only at the next registration, which may be months away - until
    // then Show status would call an account in use unused.
    $kept = tc_registered($store, 'anna', 0);
    tc_log_append($store, $kept, 'dev0000000000001', tc_ops(1));
    tc_assert(!is_file(tc_unused_marker($store, $kept)), 'still marked unused after storing something');
});

tc_test('an empty log directory is not something stored', function ($store, $uid) {
    // What a refused snapshot leaves behind. There is nothing in it.
    $gone = tc_registered($store, 'anna', TC_TEST_LONG_AGO);
    tc_secure_mkdir(tc_log_dir($store, $gone));
    tc_assert_same(1, tc_accounts_sweep_unused($store), 'removed');
});

tc_test('an account the operator made is never removed, however unused', function ($store, $uid) {
    $made = tc_account_create($store, 'bert', 'x')['uid'];
    tc_write_json(tc_user_dir($store, $made) . '/user.dat.php', ['disabled' => false, 'devices' => []]);
    tc_assert_same(0, tc_accounts_sweep_unused($store), 'removed');
    tc_assert(tc_user_find($store, 'bert') !== null, 'the operator\'s account went');
});

tc_test('what cannot be read is not taken for empty', function ($store, $uid) {
    if (function_exists('posix_geteuid') && posix_geteuid() === 0) {
        return;   // root reads what nobody else may, so this cannot be shown
    }
    $kept = tc_registered($store, 'anna', TC_TEST_LONG_AGO);
    tc_secure_mkdir(tc_log_dir($store, $kept));
    chmod(tc_log_dir($store, $kept), 0000);
    try {
        tc_assert_same(0, tc_accounts_sweep_unused($store), 'removed');
    } finally {
        chmod(tc_log_dir($store, $kept), 0700);
    }
    tc_assert(tc_user_find($store, 'anna') !== null, 'an account that could not be looked into went');

    $user = tc_user_dir($store, $kept) . '/user.dat.php';
    file_put_contents($user, TC_GUARD . '{"devices": [');
    tc_assert_same(0, tc_accounts_sweep_unused($store), 'removed with a device list it could not read');
});

tc_test('a sweep does a bounded amount of work', function ($store, $uid) {
    // It runs inside somebody's registration.
    for ($i = 0; $i < TC_SWEEP_REMOVE + 3; $i++) {
        tc_registered($store, 'due' . $i, TC_TEST_LONG_AGO);
    }
    tc_assert_same(TC_SWEEP_REMOVE, tc_accounts_sweep_unused($store), 'removed in one');
    tc_assert_same(3, tc_accounts_sweep_unused($store), 'removed in the next');

    // And it looks at no more than its share: markers that turn out not to be
    // due are dated forward as they are looked at, so the count shows.
    for ($i = 0; $i < TC_SWEEP_EXAMINE + 5; $i++) {
        $recent = tc_registered($store, 'recent' . $i, TC_TEST_LONG_AGO, sprintf('b%015x', $i));
        touch(tc_user_dir($store, $recent) . '/seen/' . sprintf('b%015x', $i));
    }
    tc_accounts_sweep_unused($store);
    clearstatcache();
    $lookedAt = 0;
    foreach (glob(tc_unused_dir($store) . '/*.dat.php') as $marker) {
        $lookedAt += filemtime($marker) > time() ? 1 : 0;
    }
    tc_assert_same(TC_SWEEP_EXAMINE, $lookedAt, 'markers looked at');
});

tc_test('a device arriving as its account is removed is refused, and builds nothing up again', function ($store, $uid) {
    // Fresh, so its token works: one due for removal has none that do, but
    // the operator removes accounts too, and a token can outlive its list.
    $gone = tc_registered($store, 'anna', 0);
    $token = tc_token_issue($store, $gone, 'a1b2c3d4e5f60718', 'laptop')['token'];
    tc_assert(tc_token_check($store, $token) !== null, 'the token did not work to begin with');
    // A token the device list never recorded: nothing will revoke it by name.
    $stray = tc_token_issue($store, $gone, 'ffffffffffffffff', 'elsewhere')['token'];
    $user = tc_read_json(tc_user_dir($store, $gone) . '/user.dat.php');
    $user['devices'] = array_values(array_filter($user['devices'], function ($d) {
        return $d['device_uid'] !== 'ffffffffffffffff';
    }));
    tc_write_json(tc_user_dir($store, $gone) . '/user.dat.php', $user);
    unlink(tc_user_dir($store, $gone) . '/seen/ffffffffffffffff');

    tc_account_delete($store, 'anna', $gone);
    tc_assert_same(null, tc_token_check($store, $token), 'its token still let the device in');
    tc_assert_same(null, tc_token_check($store, $stray), 'a token it never listed still let a device in');
    // One that had got past the token check already, and reaches the log now.
    tc_assert_same(['error' => 'account_gone'],
                   tc_log_append($store, $gone, 'a1b2c3d4e5f60718', tc_ops(1)), 'push');
    tc_touch_seen($store, $gone, 'a1b2c3d4e5f60718');
    clearstatcache();
    tc_assert(!is_dir(tc_user_dir($store, $gone)), 'the account\'s directory came back');
});

tc_test('a push waiting for the log while its account is removed is refused afterwards', function ($store, $uid) {
    // It got past every check made before the lock; the account went while
    // it waited. What it finds once it has the lock must stop it.
    $gone = tc_registered($store, 'anna', 0);
    $held = tc_lock(tc_log_lock_path($store, $gone));
    // The child says when it is about to append, so how long it took to
    // start cannot decide which of the two checks this ends up testing.
    $script = sprintf('require %s; require %s; echo "ready\\n"; fflush(STDOUT); '
                      . 'echo json_encode(tc_log_append(%s, %s, "dev0000000000001", '
                      . '[["op" => "task.set", "lc" => 1, "uid" => "0000000000000001", "f" => []]]));',
                      var_export(__DIR__ . '/tc/lib/auth.php', true), var_export(__DIR__ . '/tc/lib/oplog.php', true),
                      var_export($store, true), var_export($gone, true));
    $process = proc_open([PHP_BINARY, '-r', $script], [1 => ['pipe', 'w']], $pipes);
    tc_assert_same("ready\n", fgets($pipes[1]), 'the child did not start');
    usleep(300000);   // past the first check, and waiting for the lock
    tc_account_delete($store, 'anna', $gone);
    tc_unlock($held);
    $answer = json_decode(stream_get_contents($pipes[1]), true);
    proc_close($process);
    tc_assert_same(['error' => 'account_gone'], $answer, 'answer');
    clearstatcache();
    tc_assert(!is_dir(tc_user_dir($store, $gone)), 'the account\'s directory came back');
});

tc_test('a directory that cannot be emptied is still moved out of the way', function ($store, $uid) {
    // Deleting in place would leave the account's directory half there -
    // which on NFS, where a file still open cannot really be deleted, is
    // what every removal would do. Moved in one step, it is gone from where
    // anything looks, and emptying it is left for later.
    if (function_exists('posix_geteuid') && posix_geteuid() === 0) {
        return;   // root deletes what nobody else may, so this cannot be shown
    }
    $gone = tc_registered($store, 'anna', 0);
    $stuck = tc_user_dir($store, $gone) . '/stuck';
    tc_secure_mkdir($stuck);
    file_put_contents($stuck . '/cannot-go', 'x');
    chmod($stuck, 0500);
    try {
        tc_assert_same(true, tc_account_delete($store, 'anna', $gone), 'deleted');
        clearstatcache();
        tc_assert(!is_dir(tc_user_dir($store, $gone)), 'the account\'s directory is still where it was');
        tc_assert_same(null, tc_user_find($store, 'anna'), 'the account is still there');
    } finally {
        @chmod(tc_account_trash($store, $gone) . '/stuck', 0700);
        @chmod($stuck, 0700);
    }
});

tc_test('a removal clears away its own leftovers, whatever else is waiting', function ($store, $uid) {
    // A general tidy takes whichever leftovers come first, and some may not
    // go yet - those must not keep this one's on disk.
    if (function_exists('posix_geteuid') && posix_geteuid() === 0) {
        return;
    }
    $stuck = [];
    for ($i = 0; $i < 8; $i++) {
        $dir = $store . '/users/.gone-' . sprintf('%032d', $i);
        tc_secure_mkdir($dir . '/sub');
        file_put_contents($dir . '/sub/cannot-go', 'x');
        chmod($dir . '/sub', 0500);
        $stuck[] = $dir . '/sub';
    }
    try {
        $gone = tc_registered($store, 'anna', TC_TEST_LONG_AGO);
        tc_assert_same(1, tc_accounts_sweep_unused($store), 'removed');
        clearstatcache();
        tc_assert(!file_exists(tc_account_trash($store, $gone)), 'its own leftovers were left behind');
    } finally {
        foreach ($stuck as $dir) {
            chmod($dir, 0700);
        }
    }
});

tc_test('a token is refused, not deleted, while its account cannot be seen', function ($store, $uid) {
    // One stat() that fails for a moment must not sign a working device out
    // for good.
    $made = tc_registered($store, 'anna', 0, 'a1b2c3d4e5f60718');
    $token = tc_token_issue($store, $made, 'a1b2c3d4e5f60718', 'laptop')['token'];
    $dir = tc_user_dir($store, $made);
    rename($dir, $dir . '.away');
    tc_assert_same(null, tc_token_check($store, $token), 'let in while its account was not there');
    rename($dir . '.away', $dir);
    tc_assert(tc_token_check($store, $token) !== null, 'the token did not survive a moment\'s absence');
});

tc_test('a list of removals that cannot be read is not started afresh', function ($store, $uid) {
    // Rewriting it would lose every name in it - the very thing #587 was
    // about, for the accounts themselves.
    $damaged = TC_GUARD . '{"removed": [{"username": "earlier"';
    file_put_contents(tc_removed_file($store), $damaged);
    tc_registered($store, 'anna', TC_TEST_LONG_AGO);
    tc_assert_same(1, tc_accounts_sweep_unused($store), 'removed');
    tc_assert_same($damaged, file_get_contents(tc_removed_file($store)), 'the list was overwritten');
});

tc_test('a snapshot for an account that is gone is refused too', function ($store, $uid) {
    $gone = tc_registered($store, 'anna', 0);
    tc_account_delete($store, 'anna', $gone);
    tc_assert_same('account_gone', tc_snapshot_put($store, $gone, 'dev0000000000001', 1, tc_document())['error'] ?? null,
                   'answer');
    clearstatcache();
    tc_assert(!is_dir(tc_user_dir($store, $gone)), 'the account\'s directory came back');
});

tc_test('a removal that stopped halfway is finished by the next sweep', function ($store, $uid) {
    // Killed after moving the directory away, before the record went...
    $first = tc_registered($store, 'anna', TC_TEST_LONG_AGO);
    rename(tc_user_dir($store, $first), tc_account_trash($store, $first));
    // ...and after the record went, before the marker did.
    $second = tc_registered($store, 'bert', TC_TEST_LONG_AGO);
    rename(tc_user_dir($store, $second), tc_account_trash($store, $second));
    unlink(tc_account_file($store, 'bert'));

    tc_accounts_sweep_unused($store);
    tc_assert(tc_gone($store, 'anna', $first), 'the first was left as it was');
    tc_assert(tc_gone($store, 'bert', $second), 'the second was left as it was');
    tc_assert_same([], glob($store . '/users/.gone-*') ?: [], 'what was moved away stays');
    tc_assert_same(true, tc_account_delete($store, 'anna', $first), 'a second removal is a failure');
});

tc_test('a name somebody else has taken since is left to them', function ($store, $uid) {
    $old = tc_registered($store, 'anna', TC_TEST_LONG_AGO);
    unlink(tc_account_file($store, 'anna'));
    $new = tc_account_create($store, 'anna', 'x')['uid'];
    tc_accounts_sweep_unused($store);
    tc_assert_same($new, tc_user_find($store, 'anna')['uid'] ?? null, 'the new anna went');
    tc_assert(is_dir(tc_user_dir($store, $new)), 'the new anna lost her directory');
    tc_assert(!is_file(tc_unused_marker($store, $old)), 'the old marker is still there');
});

tc_test('a sweep never waits for a lock', function ($store, $uid) {
    // Neither for the users lock - the next registration will do - nor for an
    // account's log lock, whose holder is using it right now.
    $due = tc_registered($store, 'anna', TC_TEST_LONG_AGO);
    $held = tc_lock(tc_users_lock($store));
    $t = microtime(true);
    tc_assert_same(0, tc_accounts_sweep_unused($store), 'swept under somebody else\'s lock');
    tc_unlock($held);
    tc_assert(microtime(true) - $t < 1, 'it waited for the users lock');

    $held = tc_lock(tc_log_lock_path($store, $due));
    $t = microtime(true);
    tc_assert_same(0, tc_accounts_sweep_unused($store), 'removed while its log was locked');
    tc_unlock($held);
    tc_assert(microtime(true) - $t < 1, 'it waited for the log lock');
    tc_assert_same(1, tc_accounts_sweep_unused($store), 'and not afterwards');
});

tc_test('nothing is swept while the old account list is still there', function ($store, $uid) {
    // Its names are not in accounts/ yet, so every marker would look like
    // an account that is gone.
    tc_registered($store, 'anna', TC_TEST_LONG_AGO);
    tc_write_json(tc_users_file($store), ['users' => []]);
    tc_assert_same(0, tc_accounts_sweep_unused($store), 'swept');
});

tc_test('an account removed by the operator goes the same way, and can be removed twice', function ($store, $uid) {
    $made = tc_account_create($store, 'bert', 'x')['uid'];
    tc_secure_mkdir(tc_tokens_dir($store));
    $token = tc_token_issue($store, $made, 'a1b2c3d4e5f60718', 'laptop')['token'];
    tc_assert(tc_token_check($store, $token) !== null, 'the token did not work to begin with');
    tc_log_append($store, $made, 'a1b2c3d4e5f60718', tc_ops(3));
    tc_assert_same(true, tc_account_delete($store, 'bert', $made), 'deleted');
    tc_trash_tidy($store);
    tc_assert(tc_gone($store, 'bert', $made), 'something of it is left');
    tc_assert_same(null, tc_token_check($store, $token), 'its token still works');
    tc_assert_same([], glob($store . '/users/.gone-*') ?: [], 'its data is still on disk');
    tc_assert_same(true, tc_account_delete($store, 'bert', $made), 'the second time');
});

tc_test('a token its device list cannot record is not handed out', function ($store, $uid) {
    // Nothing could find it again to revoke.
    $made = tc_account_create($store, 'bert', 'x')['uid'];
    tc_secure_mkdir(tc_tokens_dir($store));
    $user = tc_user_dir($store, $made) . '/user.dat.php';
    unlink($user);
    tc_secure_mkdir($user . '/in-the-way');
    tc_assert_same(null, tc_token_issue($store, $made, 'a1b2c3d4e5f60718', 'laptop'), 'issued');
    tc_assert_same([], glob(tc_tokens_dir($store) . '/*.dat.php') ?: [], 'a token was left behind');
});

printf("\n%d tests, %d failed\n", $GLOBALS['tc_tests'], $GLOBALS['tc_failed']);
exit($GLOBALS['tc_failed'] === 0 ? 0 : 1);
