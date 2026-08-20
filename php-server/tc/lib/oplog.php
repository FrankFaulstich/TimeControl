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

// A snapshot is a whole document, so it is allowed to be larger than an
// ordinary request - but only within reason, and only from a caller who has
// already proved a token. Anything past this is refused with a code the
// client can recognise and stop retrying on.
const TC_SNAPSHOT_MAX_BYTES = 4194304;   // 4 MiB

// How long the segments a snapshot replaced stay on disk. Space is not the
// urgent problem - months of growth is - and a snapshot that turns out to be
// wrong is only discovered by someone noticing, which takes days rather than
// seconds. Deleting immediately would make that mistake unrecoverable to save
// a week of storage.
const TC_SEG_GRACE_SECONDS = 604800;     // 7 days

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
 *
 * `retired` holds segments a snapshot has taken over from. They are out of
 * the reading path but still on disk, waiting out TC_SEG_GRACE_SECONDS.
 *
 * `seg_seq` numbers segment files and only ever counts up. It cannot be
 * derived from the number of segments any more, because retirement removes
 * them from that list and a name would then be handed out twice.
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
    if (!isset($state['retired']) || !is_array($state['retired'])) {
        $state['retired'] = [];
    }
    // Installations that predate snapshots have no counter; theirs starts
    // where the old count-based naming left off, so no name is reused.
    if (!isset($state['seg_seq'])) {
        $state['seg_seq'] = count($state['segments']);
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
            if ((int)($seg['n'] ?? 0) === 0) {
                // A segment the state has never recorded. Usually that means
                // brand new, but a file can already be sitting there: an
                // append that died between creating it and recording it
                // leaves one behind, and adopting those bytes would put a
                // fragment into the log and leave the byte count describing
                // a file that no longer matches it. So the file is written
                // fresh either way. Nothing the state vouches for can be lost
                // here - a recorded segment always has n >= 1 - and the
                // client never received an acknowledgement for the discarded
                // bytes, so it sends them again.
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
 *
 * Names come from a counter that only rises, never from how many segments
 * are currently listed: retirement takes them out of that list, and a name
 * derived from the count would then be issued a second time and append new
 * operations to a file that is waiting to be swept.
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
    // Advanced here, but only reaching disk when the append that follows
    // succeeds and writes the state - so a failed attempt reuses the number.
    $state['seg_seq'] = (int)$state['seg_seq'] + 1;
    return [
        'f'     => sprintf('seg-%07d.log.php', (int)$state['seg_seq']),
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
 * Asking from below the snapshot point returns nothing and says so. Once a
 * snapshot has taken over the early segments they are out of this path, so
 * answering such a caller from what is left would hand them a stream with a
 * hole at the front - and the hole is at the front, where the objects are
 * created, so almost everything after it would be dropped as referring to
 * something unknown. Nothing would report it. The caller has to fetch the
 * snapshot first, and the refusal is what tells them so.
 *
 * @param string|null $excludeDevice Operations this device submitted itself
 *                                   are left out. It already holds their
 *                                   bodies and only needs to be told which
 *                                   sequence numbers they got, which the push
 *                                   response carries - sending them back
 *                                   would double the traffic for nothing.
 * @return array{ops: array, head: int, more: bool, snapshot_seq: int,
 *               needs_snapshot: bool}
 */
function tc_log_read($store, $uid, $since, $limit, $excludeDevice = null)
{
    $state = tc_log_state($store, $uid);
    $head  = (int)$state['head'];
    $out   = [];
    $more  = false;

    $snapshotSeq = (int)($state['snapshot']['seq'] ?? 0);
    if ($snapshotSeq > 0 && $since < $snapshotSeq) {
        return ['ops' => [], 'head' => $head, 'more' => false,
                'snapshot_seq' => $snapshotSeq, 'needs_snapshot' => true];
    }

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

    return ['ops' => $out, 'head' => $head, 'more' => $more,
            'snapshot_seq' => $snapshotSeq, 'needs_snapshot' => false];
}

// ---------------------------------------------------------------------------
// Snapshots.
//
// The log only ever grows, and a machine joining late has to replay all of
// it. A snapshot is the document as it stood at one sequence number: a
// newcomer fetches that and then only the tail.
//
// It is produced by a client and stored here byte for byte. The server does
// not fold operations into a document and must not learn how - those rules
// already exist once, in tt/sync_apply.py, and a second copy here in another
// language would drift from the first. The drift would show up as two
// machines that quietly stopped agreeing, which is the one failure this
// design exists to avoid. So the work here is bookkeeping: check that the
// uploader was at head, store the bytes, and move the segments the snapshot
// speaks for out of the reading path.
// ---------------------------------------------------------------------------

function tc_snapshot_meta($store, $uid)
{
    $state = tc_log_state($store, $uid);
    $snap  = $state['snapshot'] ?? null;
    return (is_array($snap) && !empty($snap['seq']) && !empty($snap['f'])) ? $snap : null;
}

function tc_snapshot_file($store, $uid, array $snap)
{
    return tc_log_dir($store, $uid) . '/' . $snap['f'];
}

/**
 * Rejects anything that is not a document.
 *
 * The same stance tc_ops_validate takes towards operations, and for the same
 * reason: whatever is accepted here is what every other machine will be
 * handed as the truth. An error page that arrived with a 200, or a transfer
 * that stopped early, both look like a body.
 *
 * The decoded value is thrown away - the original bytes are what gets stored
 * - so nothing here needs to understand the format beyond recognising it.
 *
 * @return string|null An error code, or null when it may be stored.
 */
function tc_snapshot_validate($raw)
{
    if ($raw === '') {
        return 'snapshot_empty';
    }
    if (strlen($raw) > TC_SNAPSHOT_MAX_BYTES) {
        return 'snapshot_too_large';
    }
    $doc = json_decode($raw, true, 64);
    if (!is_array($doc)) {
        return 'snapshot_not_json';
    }
    if (!isset($doc['projects']) || !is_array($doc['projects'])) {
        return 'snapshot_shape';
    }
    if (isset($doc['_deleted']) && !is_array($doc['_deleted'])) {
        return 'snapshot_shape';
    }
    // A document with nothing in it, offered for a log that has something in
    // it, is the shape a mistake takes: a client whose data.json was emptied
    // or replaced while its cursor stayed where it was. Such a snapshot would
    // be handed to every other machine as the truth. An account that really
    // is empty loses nothing by being refused - there is nothing there to
    // compact.
    if (!$doc['projects']) {
        return 'snapshot_has_no_projects';
    }
    return null;
}

/**
 * Records a snapshot and retires the segments it covers.
 *
 * The order of the four steps below is the whole of the interruption story.
 * A request can be killed at any point by max_execution_time, so each step
 * has to leave the store in a state the next reader can use.
 *
 * @param int    $seq The sequence number the uploader claims to have held.
 * @param string $raw The document, already validated.
 * @return array|null Null when the log could not be locked or written.
 */
function tc_snapshot_put($store, $uid, $deviceUid, $seq, $raw)
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
        $head  = (int)$state['head'];
        $have  = (int)($state['snapshot']['seq'] ?? 0);

        if ($head <= 0) {
            return ['error' => 'log_empty', 'head' => $head, 'snapshot_seq' => $have];
        }
        // The uploader has to have held the whole log when it built this.
        // Not proof - it is asserting its own position - but it rules out
        // the ordinary accident, which is a machine snapshotting a document
        // it had not finished catching up into. Anything stronger would mean
        // the server checking the document against the operations, which is
        // exactly the knowledge it is meant not to have.
        if ($seq !== $head) {
            return ['error' => 'not_at_head', 'head' => $head, 'snapshot_seq' => $have];
        }
        if ($seq <= $have) {
            return ['error' => 'not_newer', 'head' => $head, 'snapshot_seq' => $have];
        }

        // 1. The bytes. Nothing points at this file yet, so being killed
        //    here leaves an unreferenced file and nothing else.
        $name = sprintf('snap-%010d.json.php', $seq);
        if (!tc_write_secure($dir . '/' . $name, TC_GUARD . $raw)) {
            return null;
        }

        // 2. The pointer, in one atomic write. Before it the previous
        //    snapshot - or none - is in force; after it the new one is.
        //    There is no moment in between, which is what lets every other
        //    step be interrupted harmlessly.
        $superseded = $state['snapshot_previous'] ?? null;
        $state['snapshot_previous'] = $state['snapshot'] ?? null;
        $state['snapshot'] = [
            'seq'   => $seq,
            'f'     => $name,
            'bytes' => strlen($raw),
            'at'    => time(),
            'dev'   => $deviceUid,
        ];
        if (!tc_write_json(tc_log_state_path($store, $uid), $state)) {
            @unlink($dir . '/' . $name);
            return null;
        }

        // 3. Retire what the snapshot now speaks for. Interrupted here, the
        //    segments are simply still in use.
        $retired = tc_snapshot_retire($state, $seq);

        // 4. Delete only what has waited long enough, and the snapshot that
        //    has now been superseded twice over. Unlinking a file that is
        //    already gone costs nothing, so being killed part-way through
        //    leaves the next call to finish the job.
        $deleted = tc_snapshot_sweep($state, $dir);
        if (is_array($superseded) && !empty($superseded['f'])) {
            @unlink($dir . '/' . $superseded['f']);
        }
        tc_write_json(tc_log_state_path($store, $uid), $state);

        return ['head' => $head, 'snapshot_seq' => $seq, 'retired' => $retired,
                'deleted' => $deleted, 'segments' => count($state['segments'])];
    } finally {
        tc_unlock($lock);
    }
}

/**
 * Moves the segments a snapshot speaks for out of the reading path.
 *
 * Wholly below the point only. A segment holding operations on both sides of
 * it stays where it is: everything above the snapshot has to remain readable
 * from the log, and half a segment cannot be handed back.
 *
 * As things stand a snapshot is only accepted at head, so in practice every
 * segment qualifies and the straddling case does not arise. The condition is
 * written for the general case anyway - it is the correct rule, it costs a
 * comparison, and a later change to when snapshots are accepted should not
 * silently start discarding live operations.
 *
 * @param array $state Modified: retired segments move between the two lists.
 * @return int How many segments were retired.
 */
function tc_snapshot_retire(array &$state, $seq)
{
    $keep = [];
    $retired = 0;
    $now = time();
    foreach ($state['segments'] as $segment) {
        if ((int)$segment['last'] <= $seq) {
            $segment['at'] = $now;
            $state['retired'][] = $segment;
            $retired++;
        } else {
            $keep[] = $segment;
        }
    }
    $state['segments'] = array_values($keep);
    return $retired;
}

/**
 * Deletes retired segments that have served out TC_SEG_GRACE_SECONDS.
 *
 * @param array $state Modified: swept segments leave the retired list.
 * @return int How many files were removed.
 */
function tc_snapshot_sweep(array &$state, $dir)
{
    $now = time();
    $keep = [];
    $deleted = 0;
    foreach ($state['retired'] as $segment) {
        if (empty($segment['f'])) {
            continue;
        }
        if ($now - (int)($segment['at'] ?? $now) >= TC_SEG_GRACE_SECONDS) {
            @unlink($dir . '/' . $segment['f']);
            $deleted++;
        } else {
            $keep[] = $segment;
        }
    }
    $state['retired'] = array_values($keep);
    return $deleted;
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
