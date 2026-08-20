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

printf("\n%d tests, %d failed\n", $GLOBALS['tc_tests'], $GLOBALS['tc_failed']);
exit($GLOBALS['tc_failed'] === 0 ? 0 : 1);
