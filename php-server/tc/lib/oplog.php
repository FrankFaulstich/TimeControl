<?php
/**
 * The operation log.
 *
 * Clients do not exchange documents, they exchange intentions: "set the
 * priority of task X to 3", "close entry Y at 14:02". The server does not
 * understand any of them. It appends what it is given, stamps each one with
 * the next sequence number, and hands back everything a caller has not seen.
 *
 * That sequence number is the whole conflict story. There are no vector
 * clocks and no comparing of wall clocks - deliberately, because the
 * timestamps this app records are naive local time and both machines are
 * allowed to disagree by minutes. Ordering by "who reached the server first"
 * is something both replicas can compute identically, which is exactly what
 * comparing two unsynchronised clocks can never be.
 */

require_once __DIR__ . '/store.php';

// The twelve intentions a client may express. The server does not act on
// them, but it refuses anything outside the list: an unknown verb would
// travel to the other machine and confuse a client that has no rule for it.
const TC_OPS = [
    'project.create', 'project.set', 'project.delete',
    'task.create', 'task.set', 'task.move', 'task.delete',
    'entry.add', 'entry.close', 'entry.set', 'entry.move', 'entry.delete',
];

const TC_SEG_MAX_OPS   = 1000;
const TC_SEG_MAX_BYTES = 1048576;   // 1 MiB
const TC_PUSH_MAX_OPS  = 500;
const TC_PULL_MAX_OPS  = 500;

function tc_log_dir($store, $uid)   { return tc_user_dir($store, $uid) . '/log'; }
function tc_log_lock_path($store, $uid) { return tc_user_dir($store, $uid) . '/log.lock'; }
function tc_log_state_path($store, $uid) { return tc_log_dir($store, $uid) . '/state.dat.php'; }

/**
 * Reads the log's bookkeeping, creating it on first use.
 *
 * `segments` records, per file, the first and last sequence number it holds
 * and how many bytes of it are known-good. That last number is what makes a
 * half-finished append recoverable rather than corrupting: see
 * tc_log_reconcile().
 */
function tc_log_state($store, $uid)
{
    $state = tc_read_json(tc_log_state_path($store, $uid));
    if (!is_array($state) || !isset($state['head'])) {
        $state = ['head' => 0, 'segments' => [], 'devices' => (object)[]];
    }
    if (!isset($state['segments']) || !is_array($state['segments'])) {
        $state['segments'] = [];
    }
    $state['devices'] = (array)($state['devices'] ?? []);
    return $state;
}

/**
 * Discards anything an interrupted append left behind.
 *
 * A request can be killed part-way through by max_execution_time. The log is
 * written before the pointer that describes it, so what survives such a kill
 * is a segment with more bytes in it than the state file admits to - possibly
 * ending in half a line. Those bytes are cut away rather than adopted: the
 * pushing client never received its acknowledgement, so it will send the same
 * operations again, and the per-device counter below makes that retry land
 * exactly once. Keeping them would mean a sequence number describing
 * different content on different machines, which nothing could repair.
 */
function tc_log_reconcile($store, $uid, array $state)
{
    if (!$state['segments']) {
        return $state;
    }
    $last = $state['segments'][count($state['segments']) - 1];
    $path = tc_log_dir($store, $uid) . '/' . $last['f'];
    $size = @filesize($path);
    if ($size !== false && $size > $last['bytes']) {
        $fh = @fopen($path, 'r+');
        if ($fh) {
            @ftruncate($fh, $last['bytes']);
            @fclose($fh);
        }
    }
    return $state;
}

/**
 * Appends operations and gives each one its place in the order.
 *
 * @param string $deviceUid Taken from the caller's token, never from the
 *                          request body - a device must not be able to
 *                          submit work under another device's name, which
 *                          would corrupt that device's duplicate counter.
 * @param array  $ops       Each needs 'op', and 'lc' - a counter the client
 *                          increments per operation and never reuses.
 * @return array{assigned: array, dups: array, head: int}|null
 */
function tc_log_append($store, $uid, $deviceUid, array $ops)
{
    $lock = tc_lock(tc_log_lock_path($store, $uid));
    if (!$lock) {
        return null;
    }
    try {
        $dir = tc_log_dir($store, $uid);
        if (!is_dir($dir) && !tc_secure_mkdir($dir)) {
            return null;
        }
        $state = tc_log_reconcile($store, $uid, tc_log_state($store, $uid));

        $maxLc    = (int)($state['devices'][$deviceUid]['max_lc'] ?? 0);
        $assigned = [];
        $dups     = [];
        $lines    = '';
        $head     = (int)$state['head'];
        $newMaxLc = $maxLc;

        foreach ($ops as $op) {
            $lc = isset($op['lc']) ? (int)$op['lc'] : 0;
            // Anything at or below the high-water mark has already been
            // recorded - this is a retry of a push whose response was lost.
            // Reporting it rather than appending it is what makes the whole
            // exchange safe to repeat.
            if ($lc <= $maxLc) {
                $dups[] = $lc;
                continue;
            }
            $head++;
            $entry = [
                's'   => $head,
                'op'  => $op['op'],
                'dev' => $deviceUid,
                'lc'  => $lc,
            ];
            foreach (['uid', 'f', 'ts', 'project', 'task', 'start', 'end'] as $k) {
                if (array_key_exists($k, $op)) {
                    $entry[$k] = $op[$k];
                }
            }
            $lines .= json_encode($entry, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE) . "\n";
            $assigned[] = [$lc, $head];
            if ($lc > $newMaxLc) {
                $newMaxLc = $lc;
            }
        }

        if ($lines !== '') {
            $seg = tc_log_current_segment($state, $head - count($assigned) + 1);
            $path = $dir . '/' . $seg['f'];
            if (!is_file($path)) {
                @file_put_contents($path, TC_GUARD);
                @chmod($path, 0600);
                $seg['bytes'] = strlen(TC_GUARD);
            }
            $fh = @fopen($path, 'ab');
            if (!$fh) {
                return null;
            }
            $written = @fwrite($fh, $lines);
            @fflush($fh);
            @fclose($fh);
            if ($written !== strlen($lines)) {
                // Partial write. Leave head where it was; the truncation on
                // the next call removes the fragment and the client retries.
                return null;
            }

            $seg['last']  = $head;
            $seg['bytes'] = (int)$seg['bytes'] + strlen($lines);
            $seg['n']     = (int)($seg['n'] ?? 0) + count($assigned);
            tc_log_put_segment($state, $seg);

            $state['head'] = $head;
            $state['devices'][$deviceUid] = ['max_lc' => $newMaxLc, 'seen' => time()];
            // Written only after the log itself is safely on disk.
            tc_write_json(tc_log_state_path($store, $uid), $state);
        }

        return ['assigned' => $assigned, 'dups' => $dups, 'head' => (int)$state['head']];
    } finally {
        tc_unlock($lock);
    }
}

/**
 * Returns the segment currently being written, starting a new one when the
 * open segment has grown past its limits. Segments keep any single read
 * bounded, which matters when the execution limit is short.
 *
 * The limits are checked once per batch, not once per operation, so a
 * segment can overshoot by up to one batch - a push of 500 arriving at a
 * segment holding 999 leaves 1499 in it. That is deliberate: splitting a
 * batch across two files would mean two appends to keep consistent instead
 * of one, and the overshoot is bounded by TC_PUSH_MAX_OPS either way. Do not
 * "fix" a segment that is over TC_SEG_MAX_OPS; it is working as intended.
 */
function tc_log_current_segment(array &$state, $nextSeq)
{
    $n = count($state['segments']);
    if ($n > 0) {
        $seg = $state['segments'][$n - 1];
        if ((int)$seg['n'] < TC_SEG_MAX_OPS && (int)$seg['bytes'] < TC_SEG_MAX_BYTES) {
            return $seg;
        }
    }
    return [
        'f'     => sprintf('seg-%07d.log.php', $n + 1),
        'first' => $nextSeq,
        'last'  => $nextSeq - 1,
        'bytes' => 0,
        'n'     => 0,
    ];
}

function tc_log_put_segment(array &$state, array $seg)
{
    foreach ($state['segments'] as $i => $existing) {
        if ($existing['f'] === $seg['f']) {
            $state['segments'][$i] = $seg;
            return;
        }
    }
    $state['segments'][] = $seg;
}

/**
 * Reads operations newer than $since.
 *
 * @param string|null $excludeDevice Operations this device submitted itself
 *                                   are left out. It already holds their
 *                                   bodies and only needs to be told which
 *                                   sequence numbers they got, which the push
 *                                   response carries - sending them back
 *                                   would double the traffic for nothing.
 * @return array{ops: array, head: int, more: bool}
 */
function tc_log_read($store, $uid, $since, $limit, $excludeDevice = null)
{
    $state = tc_log_state($store, $uid);
    $head  = (int)$state['head'];
    $out   = [];
    $more  = false;

    foreach ($state['segments'] as $seg) {
        if ((int)$seg['last'] <= $since) {
            continue;   // wholly in the past
        }
        $path = tc_log_dir($store, $uid) . '/' . $seg['f'];
        $fh   = @fopen($path, 'rb');
        if (!$fh) {
            continue;
        }
        // Only the bytes the state file vouches for; anything past that is
        // the tail of an interrupted append.
        $budget = (int)$seg['bytes'];
        $read   = 0;
        while (($line = fgets($fh)) !== false) {
            $read += strlen($line);
            if ($read > $budget) {
                break;
            }
            if ($line === '' || $line[0] !== '{') {
                continue;   // the guard line
            }
            $entry = json_decode($line, true);
            if (!is_array($entry) || (int)($entry['s'] ?? 0) <= $since) {
                continue;
            }
            if ($excludeDevice !== null && ($entry['dev'] ?? null) === $excludeDevice) {
                continue;
            }
            if (count($out) >= $limit) {
                $more = true;
                break 2;
            }
            $out[] = $entry;
        }
        @fclose($fh);
    }

    return ['ops' => $out, 'head' => $head, 'more' => $more];
}

/**
 * Rejects anything that is not a well-formed operation.
 *
 * The server does not interpret operations, but it does refuse to store
 * nonsense: whatever it accepts here it will hand to the other machine, and
 * a client meeting a verb it has no rule for can only stop.
 *
 * @return string|null An error code, or null when the batch is acceptable.
 */
function tc_ops_validate($ops)
{
    if (!is_array($ops)) {
        return 'ops_not_a_list';
    }
    if (count($ops) > TC_PUSH_MAX_OPS) {
        return 'too_many_ops';
    }
    foreach ($ops as $op) {
        if (!is_array($op)) {
            return 'op_not_an_object';
        }
        if (!isset($op['op']) || !in_array($op['op'], TC_OPS, true)) {
            return 'unknown_op';
        }
        if (!isset($op['lc']) || !is_int($op['lc']) || $op['lc'] < 1) {
            return 'bad_lc';
        }
        foreach (['uid', 'project', 'task'] as $k) {
            if (isset($op[$k]) && !preg_match('/^[a-f0-9]{16}$/', (string)$op[$k])) {
                return 'bad_uid';
            }
        }
        if (isset($op['f']) && !is_array($op['f'])) {
            return 'bad_fields';
        }
    }
    return null;
}
