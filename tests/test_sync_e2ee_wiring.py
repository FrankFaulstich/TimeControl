"""
What actually leaves this machine, and what it refuses to swallow.

The cryptography is covered next door in test_sync_crypto.py and the key
store in test_sync_secret.py. What is checked here is the wiring: that the
switch reaches the cycle, that nothing readable goes out while it is on, that
nothing goes out at all while this machine cannot take part, and - the point
of the whole exercise - that a machine meeting something it cannot read stops
instead of quietly skipping it.

The server is a small stand-in rather than the one in test_sync_engine.py.
The files here do not import one another, and copying twenty lines is a better
price than coupling two test modules together.
"""

import base64
import json
import os
import shutil
import sys
import tempfile
import unittest
import unittest.mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from tt import sync_client, sync_crypto, sync_engine, sync_secret
from tt.sync_outbox import Outbox

FAST = {'n': 1 << 10, 'r': 8, 'p': 1}
SALT = 'ab' * 16
ACCOUNT = 'frank'
OTHER_DEVICE = 'bbbbbbbbbbbbbbbb'
T1 = 't' * 16


def config(e2ee=None):
    """A configuration with synchronisation on, and encryption as asked."""
    out = {'sync': {'enabled': True, 'base_url': 'https://example.invalid/tc'}}
    if e2ee is not None:
        out['sync']['e2ee'] = e2ee
    return out


def switched_on(salt=SALT):
    return dict({'enabled': True, 'salt': salt}, **FAST)


class Server:
    """Enough of the log to answer a push and a pull."""

    def __init__(self):
        self.log = []
        self.max_lc = 0
        self.pushed = []          # every batch, exactly as it arrived
        self.calls = []

    def plant(self, entry):
        """An operation already in the log, from the other machine."""
        entry = dict(entry, s=len(self.log) + 1, dev=OTHER_DEVICE)
        self.log.append(entry)
        return entry

    def push(self, base_seq, ops):
        self.calls.append('push')
        self.pushed.append([json.loads(json.dumps(o)) for o in ops])
        assigned = []
        for op in ops:
            lc = int(op['lc'])
            if lc <= self.max_lc:
                continue
            entry = dict(op, s=len(self.log) + 1, dev='aaaaaaaaaaaaaaaa')
            self.log.append(entry)
            assigned.append([lc, entry['s']])
            self.max_lc = max(self.max_lc, lc)
        visible = [e for e in self.log
                   if e['s'] > base_seq and e['dev'] != 'aaaaaaaaaaaaaaaa']
        return {'ok': True, 'head': len(self.log), 'assigned': assigned,
                'dups': [], 'ops': visible, 'more': False,
                'snapshot_seq': 0, 'needs_snapshot': False}

    def pull(self, since, limit=500):
        self.calls.append('pull')
        return {'ok': True, 'head': len(self.log),
                'ops': [e for e in self.log if e['s'] > since],
                'more': False, 'snapshot_seq': 0, 'needs_snapshot': False}

    def head_reply(self):
        self.calls.append('head')
        return {'ok': True, 'head': len(self.log), 'server_time': 0}


class WiringTestCase(unittest.TestCase):

    def setUp(self):
        sync_engine._wake.clear()
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

        saved = {name: getattr(sync_client, name) for name in
                 ('config_dir', 'head', 'push', 'pull', 'load_credentials',
                  'device_identity')}
        self.addCleanup(lambda: [setattr(sync_client, n, v)
                                 for n, v in saved.items()])
        self.addCleanup(sync_engine.stop)

        self.server = Server()
        sync_client.config_dir = lambda: self.tmp
        sync_client.head = self.server.head_reply
        sync_client.push = self.server.push
        sync_client.pull = self.server.pull
        sync_client.load_credentials = lambda: {
            'token': 't', 'base_url': 'https://example.invalid/tc',
            'username': ACCOUNT}
        sync_client.device_identity = lambda: {'device_uid': 'aaaaaaaaaaaaaaaa'}

        self.addCleanup(setattr, sync_engine, '_config_seen',
                        sync_engine._config_seen)
        self.outbox = Outbox()

    # -- helpers -----------------------------------------------------------

    def use(self, cfg):
        """What ensure_started() would have left behind for the cycle."""
        sync_engine._config_seen = cfg
        return cfg

    def hold_key(self, passphrase='gemeinsame Passphrase', salt=SALT):
        """
        Give this machine a key the way unlock() does.

        Through the real writer rather than by putting a file there, so that
        calling it twice does what changing a passphrase does - adds a key and
        keeps the one before. A helper that replaced would quietly make the
        keyring untestable from here.
        """
        key = sync_crypto.derive_key(passphrase, bytes.fromhex(salt), **FAST)
        sync_secret._remember(key, bytes.fromhex(salt), FAST, ACCOUNT)
        return key

    def seal_from_other(self, op, key, lc=900):
        """An operation the other machine sealed and the server now holds."""
        return self.server.plant(sync_crypto.seal(dict(op, lc=lc), key, ACCOUNT))

    def inbox_ops(self):
        path = sync_engine.inbox_path()
        if not os.path.exists(path):
            return []
        out = []
        with open(path, encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    out.extend(json.loads(line).get('ops') or [])
        return out

    def cursor(self):
        return int(sync_engine.read_state().get('base_seq', 0))


class TestOffIsUnchanged(WiringTestCase):

    def test_what_goes_out_is_the_operation_itself(self):
        self.use(config())
        self.outbox.append('task.set', uid=T1, f={'task_name': 'Angebot'})
        self.assertTrue(sync_engine.run_cycle(self.outbox).get('ok'))

        sent = self.server.pushed[0][0]
        self.assertEqual(sent['op'], 'task.set')
        self.assertEqual(sent['f']['task_name'], 'Angebot')

    def test_an_absent_block_needs_no_account_and_no_key(self):
        """Off must not start demanding things that were never required."""
        sync_client.load_credentials = lambda: {'token': 't', 'base_url': 'https://x/'}
        self.use(config())
        self.outbox.append('task.set', uid=T1, f={'today': True})
        self.assertTrue(sync_engine.run_cycle(self.outbox).get('ok'))

    def test_an_explicit_false_behaves_the_same(self):
        self.use(config(dict(switched_on(), enabled=False)))
        self.outbox.append('task.set', uid=T1, f={'today': True})
        self.assertTrue(sync_engine.run_cycle(self.outbox).get('ok'))
        self.assertEqual(self.server.pushed[0][0]['op'], 'task.set')


class TestOnSealsWhatLeaves(WiringTestCase):

    def setUp(self):
        super().setUp()
        self.key = self.hold_key()
        self.use(config(switched_on()))

    def test_nothing_readable_reaches_the_server(self):
        self.outbox.append('task.set', uid=T1,
                           f={'task_name': 'Kundentermin Dlubal',
                              'note': 'Telefon 0123 456789'})
        self.assertTrue(sync_engine.run_cycle(self.outbox).get('ok'))

        body = json.dumps(self.server.pushed[0])
        for secret in ('task.set', T1, 'Kundentermin', 'Dlubal', '0123',
                       'task_name', 'note'):
            self.assertNotIn(secret, body, '%r reached the server' % secret)

    def test_the_envelope_is_what_the_server_accepts(self):
        self.outbox.append('task.delete', uid=T1, ts='2026-09-17 10:00:00')
        sync_engine.run_cycle(self.outbox)
        sent = self.server.pushed[0][0]
        self.assertEqual(sent['op'], sync_crypto.SEALED_OP)
        self.assertIsInstance(sent['f'], dict)
        self.assertEqual(set(sent), {'op', 'lc', 'f'})

    def test_a_deletion_is_sealed_too(self):
        """It carries no fields, which is why sealing 'f' alone was not enough."""
        self.outbox.append('project.delete', uid=T1, ts='2026-09-17 10:00:00')
        sync_engine.run_cycle(self.outbox)
        self.assertNotIn('project.delete', json.dumps(self.server.pushed[0]))

    def test_the_batch_is_measured_after_it_is_sealed(self):
        """
        Otherwise the size that has to fit inside the server's body limit is
        not the size that was checked - sealing adds about a third.
        """
        seen = []
        real = sync_client.fit_batch
        sync_client.fit_batch = lambda ops, **kw: (seen.append(ops) or real(ops, **kw))
        self.addCleanup(setattr, sync_client, 'fit_batch', real)

        self.outbox.append('task.set', uid=T1, f={'note': 'x' * 2000})
        sync_engine.run_cycle(self.outbox)
        self.assertTrue(all(op['op'] == sync_crypto.SEALED_OP
                            for op in seen[0]))

    def test_this_machines_own_work_is_filed_in_the_clear(self):
        """
        The inbox is local, like data.json and the queue. Filing ciphertext
        there would mean the key is needed to read back what this very machine
        just did.
        """
        self.outbox.append('task.set', uid=T1, f={'task_name': 'Eigenes'})
        sync_engine.run_cycle(self.outbox)
        mine = [op for op in self.inbox_ops() if op.get('uid') == T1]
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0]['op'], 'task.set')
        self.assertEqual(mine[0]['f']['task_name'], 'Eigenes')


class TestOnOpensWhatArrives(WiringTestCase):

    def setUp(self):
        super().setUp()
        self.key = self.hold_key()
        self.use(config(switched_on()))

    def test_a_sealed_operation_is_opened_before_it_is_filed(self):
        self.seal_from_other({'op': 'task.set', 'uid': T1,
                              'f': {'task_name': 'Von driiben'}}, self.key)
        self.assertTrue(sync_engine.run_cycle(self.outbox).get('ok'))

        filed = [op for op in self.inbox_ops() if op.get('uid') == T1]
        self.assertEqual(len(filed), 1)
        self.assertEqual(filed[0]['op'], 'task.set')
        self.assertEqual(filed[0]['f']['task_name'], 'Von driiben')

    def test_the_servers_own_stamps_survive_the_opening(self):
        """
        `s` decides the order and `dev` recognises this machine's own work.
        Both are added by the server outside the seal, so both have to be
        carried across or the operation is unusable once opened.
        """
        planted = self.seal_from_other({'op': 'task.set', 'uid': T1,
                                        'f': {'today': True}}, self.key)
        sync_engine.run_cycle(self.outbox)
        filed = [op for op in self.inbox_ops() if op.get('uid') == T1][0]
        self.assertEqual(filed['s'], planted['s'])
        self.assertEqual(filed['dev'], OTHER_DEVICE)

    def test_plain_operations_from_a_machine_not_yet_switched_over_are_kept(self):
        """
        The state an account is in while it is being changed over. Refusing
        these would lose that machine's work rather than protect anything.
        """
        self.server.plant({'op': 'task.set', 'uid': T1, 'f': {'priority': 2}})
        self.assertTrue(sync_engine.run_cycle(self.outbox).get('ok'))
        filed = [op for op in self.inbox_ops() if op.get('uid') == T1]
        self.assertEqual(filed[0]['f']['priority'], 2)


class TestItStopsRatherThanSkipping(WiringTestCase):
    """
    The failure this whole arrangement exists to prevent: an operation that
    cannot be read is ignored, the cursor moves past it, and the machine goes
    on synchronising while never receiving anything again.
    """

    def test_sealed_data_arriving_while_the_switch_is_off_stops_the_cycle(self):
        elsewhere = sync_crypto.derive_key('anderswo', bytes.fromhex(SALT), **FAST)
        self.seal_from_other({'op': 'task.set', 'uid': T1,
                              'f': {'task_name': 'Geheim'}}, elsewhere)
        self.use(config())

        outcome = sync_engine.run_cycle(self.outbox)
        self.assertFalse(outcome.get('ok'))
        self.assertEqual(outcome.get('error'), 'e2ee_sealed_but_off')
        self.assertEqual(self.inbox_ops(), [])
        self.assertEqual(self.cursor(), 0)

    def test_data_sealed_with_a_key_we_do_not_hold_stops_the_cycle(self):
        """
        Reported apart from damage, because the two want different things: a
        passphrase changed elsewhere is fixed by typing it here, and telling
        somebody to "check the passphrase" over a corrupted byte sends them
        towards actions that cannot be undone.
        """
        self.hold_key('die richtige')
        wrong = sync_crypto.derive_key('die falsche', bytes.fromhex(SALT), **FAST)
        self.seal_from_other({'op': 'task.set', 'uid': T1,
                              'f': {'task_name': 'Geheim'}}, wrong)
        self.use(config(switched_on()))

        outcome = sync_engine.run_cycle(self.outbox)
        self.assertFalse(outcome.get('ok'))
        self.assertEqual(outcome.get('error'), 'e2ee_unknown_key')
        self.assertEqual(self.inbox_ops(), [])
        self.assertEqual(self.cursor(), 0)

    def test_damaged_data_sealed_with_a_key_we_do_hold_says_so_instead(self):
        key = self.hold_key()
        self.use(config(switched_on()))
        planted = self.seal_from_other({'op': 'task.set', 'uid': T1,
                                        'f': {'task_name': 'Geheim'}}, key)
        blob = bytearray(base64.b64decode(planted['f']['c']))
        blob[-1] ^= 0x01
        planted['f']['c'] = base64.b64encode(bytes(blob)).decode('ascii')

        outcome = sync_engine.run_cycle(self.outbox)
        self.assertEqual(outcome.get('error'), 'e2ee_cannot_open')
        self.assertEqual(self.inbox_ops(), [])

    def test_an_older_key_still_opens_what_it_sealed(self):
        """
        The whole point of keeping them. After a passphrase change the log
        still holds operations sealed with the one before - this machine's
        own among them - and a machine that could only read the newest would
        stop at its own history.
        """
        old = self.hold_key('die alte')
        new = self.hold_key('die neue')      # additive: both are held now
        self.use(config(switched_on()))
        self.seal_from_other({'op': 'task.set', 'uid': T1,
                              'f': {'task_name': 'Alt'}}, old, lc=901)
        self.seal_from_other({'op': 'task.set', 'uid': T1,
                              'f': {'task_name': 'Neu'}}, new, lc=902)

        self.assertTrue(sync_engine.run_cycle(self.outbox).get('ok'))
        names = [op['f']['task_name'] for op in self.inbox_ops()
                 if op.get('uid') == T1]
        self.assertEqual(names, ['Alt', 'Neu'])

    def test_nothing_of_the_batch_is_filed_not_even_the_readable_part(self):
        """Half a batch would move the cursor over the half that was dropped."""
        elsewhere = sync_crypto.derive_key('anderswo', bytes.fromhex(SALT), **FAST)
        self.server.plant({'op': 'task.set', 'uid': T1, 'f': {'priority': 1}})
        self.seal_from_other({'op': 'task.set', 'uid': T1,
                              'f': {'priority': 9}}, elsewhere)
        self.use(config())

        self.assertFalse(sync_engine.run_cycle(self.outbox).get('ok'))
        self.assertEqual(self.inbox_ops(), [])

    def test_the_failure_is_terminal_so_it_does_not_hammer_the_server(self):
        for code in ('e2ee_sealed_but_off', 'e2ee_cannot_open',
                     'e2ee_locked', 'e2ee_stale', 'e2ee_no_salt'):
            self.assertIn(code, sync_engine.TERMINAL_ERRORS)


class TestItDoesNotTalkAtAllWhenItCannotTakePart(WiringTestCase):

    def test_a_machine_without_the_key_says_so_and_stays_quiet(self):
        self.use(config(switched_on()))
        self.outbox.append('task.set', uid=T1, f={'today': True})

        outcome = sync_engine.run_cycle(self.outbox)
        self.assertEqual(outcome.get('error'), 'e2ee_locked')
        self.assertEqual(self.server.calls, [],
                         'it reached the server without being able to seal')

    def test_a_key_belonging_to_another_salt_is_reported_as_such(self):
        self.hold_key(salt='cd' * 16)
        self.use(config(switched_on()))
        self.assertEqual(sync_engine.run_cycle(self.outbox).get('error'),
                         'e2ee_stale')
        self.assertEqual(self.server.calls, [])

    def test_a_missing_salt_is_reported_as_such(self):
        self.hold_key()
        self.use(config(dict(switched_on(), salt='nonsense')))
        self.assertEqual(sync_engine.run_cycle(self.outbox).get('error'),
                         'e2ee_no_salt')
        self.assertEqual(self.server.calls, [])

    def test_not_being_signed_in_uses_the_code_that_already_means_that(self):
        sync_client.load_credentials = lambda: None
        self.use(config(switched_on()))
        self.assertEqual(sync_engine.run_cycle(self.outbox).get('error'),
                         'not_signed_in')

    def test_the_queue_is_not_emptied_while_it_cannot_send(self):
        """Work waiting to go must survive the wait, not be dropped by it."""
        self.use(config(switched_on()))
        self.outbox.append('task.set', uid=T1, f={'today': True})
        sync_engine.run_cycle(self.outbox)
        self.assertEqual(len(self.outbox.pending()), 1)


class TheSnapshot(WiringTestCase):
    """
    A snapshot is the whole document in one piece - everything the individual
    operations are sealed to keep back, in a single upload. It is also what
    stops the log growing for ever, so refusing to make one is not a free
    choice either.
    """

    def setUp(self):
        super().setUp()
        self.offered = []
        self.served = None
        sync_client.put_snapshot = lambda seq, doc: (
            self.offered.append((seq, doc)) or {'ok': True, 'snapshot_seq': seq})
        sync_client.get_snapshot = lambda: (
            self.served if self.served is not None
            else {'ok': False, 'error': 'no_snapshot'})

    DOCUMENT = {'projects': [{'uid': 'p' * 16, 'name': 'Prüfstatik Halle 3',
                              'tasks': [{'uid': T1, 'task_name': 'Angebot',
                                         'note': 'Kunde Müller'}]}]}

    def _stage(self, seq=5, document=None):
        sync_engine.write_state({'snapshot_staged': seq, 'base_seq': seq,
                                 'server_head': seq})
        with open(sync_engine.staged_snapshot_path(), 'w', encoding='utf-8') as f:
            json.dump(document or self.DOCUMENT, f)

    def _ready_to_stage(self, document=None):
        """The state in which offer_snapshot() prepares one."""
        sync_engine.write_state({
            'snapshot_staged': 0, 'base_seq': sync_engine.SNAPSHOT_EVERY,
            'server_head': sync_engine.SNAPSHOT_EVERY, 'snapshot_seq': 0,
            'snapshot_tried': 0})
        tracker = unittest.mock.Mock()
        tracker.data = document or self.DOCUMENT
        # A real one: offer_snapshot asks it whether anything is still
        # waiting, and a Mock would answer with something truthy and be
        # turned away by a gate these tests are not about.
        tracker.op_outbox = self.outbox
        return tracker

    # -- sending it --------------------------------------------------------

    def test_what_goes_up_while_encryption_is_on_is_sealed(self):
        self.hold_key()
        self.use(config(switched_on()))
        self._stage()
        sync_engine._offer_staged_snapshot(5, self.outbox)

        self.assertEqual(len(self.offered), 1, 'nothing was offered at all')
        body = json.dumps(self.offered[0][1], ensure_ascii=False)
        for secret in ('Prüfstatik', 'Müller', 'Angebot', 'projects',
                       'task_name', T1):
            self.assertNotIn(secret, body, '%r reached the server' % secret)

    def test_with_encryption_off_it_goes_up_as_before(self):
        self.use(config())
        self._stage()
        sync_engine._offer_staged_snapshot(5, self.outbox)
        self.assertEqual(self.offered[0][1], self.DOCUMENT)

    def test_one_prepared_before_the_switch_is_sealed_on_its_way_out(self):
        """
        Sealed by what the settings say when it is sent, not when it was
        prepared - otherwise a document staged minutes before somebody
        switched encryption on would go up in the clear.
        """
        self.use(config())
        self._stage()
        self.hold_key()
        self.use(config(switched_on()))
        sync_engine._offer_staged_snapshot(5, self.outbox)
        self.assertTrue(sync_crypto.is_sealed_document(self.offered[0][1]))

    def test_a_machine_that_cannot_seal_offers_nothing(self):
        """Belt and braces: the cycle should never get this far in that state."""
        self.use(config(switched_on()))          # switched on, no key held
        self._stage()
        sync_engine._offer_staged_snapshot(5, self.outbox)
        self.assertEqual(self.offered, [])
        self.assertEqual(int(sync_engine.read_state().get('snapshot_staged') or 0), 0)

    def test_the_size_is_measured_on_the_sealed_form(self):
        """
        The four megabyte limit is the server's, so what has to fit inside it
        is the sealed document. Sealing after the measurement would leave the
        check weighing something the server never sees.
        """
        seen = []
        real = sync_client.put_snapshot
        sync_client.put_snapshot = lambda seq, doc: (seen.append(doc) or real(seq, doc))
        self.hold_key()
        self.use(config(switched_on()))
        self._stage()
        sync_engine._offer_staged_snapshot(5, self.outbox)
        self.assertTrue(sync_crypto.is_sealed_document(seen[0]))

    def test_compression_keeps_a_repetitive_document_from_growing(self):
        """
        base64 over the ciphertext costs a third. A document is repetitive
        JSON, so compressing first more than pays that back - which is what
        keeps a real one inside the server's limit.
        """
        big = {'projects': [{'uid': '%016x' % i, 'name': 'Projekt %d' % i,
                             'tasks': [{'uid': '%016x' % (i * 100 + j),
                                        'task_name': 'Aufgabe %d' % j,
                                        'note': 'Immer derselbe Text. ' * 20}
                                       for j in range(20)]}
                            for i in range(40)]}
        key = self.hold_key()
        plain = len(json.dumps(big, ensure_ascii=False).encode('utf-8'))
        sealed = len(json.dumps(
            sync_crypto.seal_document(big, key, ACCOUNT, 5)).encode('utf-8'))
        self.assertLess(sealed, plain,
                        'sealing made a %d byte document %d bytes' % (plain, sealed))

    def test_one_is_prepared_again_now_that_it_can_be_sealed(self):
        """The refusal that stood in while sealing did not exist is gone."""
        self.hold_key()
        self.use(config(switched_on()))
        self.assertEqual(sync_engine.offer_snapshot(self._ready_to_stage()),
                         sync_engine.SNAPSHOT_EVERY)
        self.assertTrue(os.path.exists(sync_engine.staged_snapshot_path()))

    def test_an_empty_document_is_still_never_prepared(self):
        """
        The server can no longer see this for an encrypted account, so the
        check on this side is the only one left. An emptied data.json offered
        as the snapshot would be handed to every other machine as the truth.
        """
        self.hold_key()
        self.use(config(switched_on()))
        self.assertEqual(
            sync_engine.offer_snapshot(self._ready_to_stage({'projects': []})), 0)

    # -- receiving it ------------------------------------------------------

    def _serve(self, document, seq=9, key=None):
        if key is not None:
            document = sync_crypto.seal_document(document, key, ACCOUNT, seq)
        self.served = {'ok': True, 'seq': seq, 'head': seq, 'document': document}

    def test_a_sealed_snapshot_is_opened_and_taken(self):
        key = self.hold_key()
        self.use(config(switched_on()))
        self._serve(self.DOCUMENT, seq=9, key=key)

        self.assertEqual(sync_engine._take_server_snapshot(0), 9)
        with open(sync_engine.inbox_path(), encoding='utf-8') as handle:
            filed = json.loads(handle.readline())
        self.assertEqual(filed['snapshot'], self.DOCUMENT)

    def test_a_plain_snapshot_still_works(self):
        self.use(config())
        self._serve(self.DOCUMENT, seq=9)
        self.assertEqual(sync_engine._take_server_snapshot(0), 9)

    def test_a_sealed_snapshot_arriving_while_off_stops_the_cycle(self):
        elsewhere = sync_crypto.derive_key('anderswo', bytes.fromhex(SALT), **FAST)
        self.use(config())
        self._serve(self.DOCUMENT, seq=9, key=elsewhere)
        with self.assertRaises(sync_engine._SealedButUnreadable) as caught:
            sync_engine._take_server_snapshot(0)
        self.assertEqual(caught.exception.code, 'e2ee_sealed_but_off')

    def test_a_snapshot_this_machine_cannot_open_stops_the_cycle(self):
        self.hold_key('die richtige')
        wrong = sync_crypto.derive_key('die falsche', bytes.fromhex(SALT), **FAST)
        self.use(config(switched_on()))
        self._serve(self.DOCUMENT, seq=9, key=wrong)
        with self.assertRaises(sync_engine._SealedButUnreadable) as caught:
            sync_engine._take_server_snapshot(0)
        self.assertEqual(caught.exception.code, 'e2ee_unknown_key')

    def test_a_server_cannot_pass_an_old_document_off_as_a_new_one(self):
        """
        The sequence number is baked into the seal. It is the only check there
        is on that claim - the client discards its own queue on the strength
        of it - so a document offered under a number it was not sealed for has
        to be refused rather than adopted.
        """
        key = self.hold_key()
        self.use(config(switched_on()))
        sealed = sync_crypto.seal_document(self.DOCUMENT, key, ACCOUNT, 3)
        self.served = {'ok': True, 'seq': 99, 'head': 99, 'document': sealed}
        with self.assertRaises(sync_engine._SealedButUnreadable) as caught:
            sync_engine._take_server_snapshot(0)
        self.assertEqual(caught.exception.code, 'e2ee_cannot_open')

    def test_nothing_is_thrown_away_before_the_document_is_understood(self):
        """
        clear_inbox() makes the snapshot the only truth this machine has. A
        document found wanting after that line costs the queue that was
        already fetched, so every check has to come first.
        """
        sync_engine._append_inbox({'base_seq': 1, 'ops': [
            {'s': 1, 'op': 'task.set', 'uid': T1, 'f': {'priority': 4}}]})
        before = self.inbox_ops()
        self.assertTrue(before)

        wrong = sync_crypto.derive_key('die falsche', bytes.fromhex(SALT), **FAST)
        self.hold_key('die richtige')
        self.use(config(switched_on()))
        self._serve(self.DOCUMENT, seq=9, key=wrong)
        with self.assertRaises(sync_engine._SealedButUnreadable):
            sync_engine._take_server_snapshot(0)
        self.assertEqual(self.inbox_ops(), before, 'the inbox was cleared anyway')

    def test_an_empty_document_is_refused_on_arrival_too(self):
        key = self.hold_key()
        self.use(config(switched_on()))
        self._serve({'projects': []}, seq=9, key=key)
        self.assertEqual(sync_engine._take_server_snapshot(0), 0)
        self.assertEqual(self.inbox_ops(), [])
class TestTheHistoryThatWasAlreadyThere(WiringTestCase):
    """
    Switching encryption on does not reach back. Everything sent before it
    stays in the server's log exactly as it was, and only a sealed snapshot
    takes those segments out of the reading path.

    Which makes this the one place where the feature could tell an outright
    lie: a screen saying the server holds only ciphertext while it holds the
    entire history.
    """

    def setUp(self):
        super().setUp()
        self.hold_key()
        self.use(config(switched_on()))

    def test_a_switched_on_account_records_where_its_plain_past_ends(self):
        sync_engine.write_state({'base_seq': 140, 'server_head': 140})
        self.outbox.append('task.set', uid=T1, f={'today': True})
        sync_engine.run_cycle(self.outbox)
        self.assertEqual(int(sync_engine.read_state().get('sealed_from')), 140)

    def test_the_mark_is_made_once_and_not_moved_afterwards(self):
        """Or every later cycle would declare the past clean as it went."""
        sync_engine.write_state({'base_seq': 140, 'server_head': 140})
        self.outbox.append('task.set', uid=T1, f={'a': 1})
        sync_engine.run_cycle(self.outbox)
        first = int(sync_engine.read_state().get('sealed_from'))

        self.outbox.append('task.set', uid=T1, f={'a': 2})
        sync_engine.run_cycle(self.outbox)
        self.assertEqual(int(sync_engine.read_state().get('sealed_from')), first)

    def test_an_account_encrypted_from_the_start_has_no_plain_past(self):
        self.outbox.append('task.set', uid=T1, f={'today': True})
        sync_engine.run_cycle(self.outbox)
        self.assertEqual(int(sync_engine.read_state().get('sealed_from')), 0)
        self.assertFalse(sync_engine.history_is_still_readable())

    def test_while_the_old_log_stands_the_history_is_reported_as_readable(self):
        sync_engine.write_state({'sealed_from': 140, 'snapshot_seq': 0})
        self.assertTrue(sync_engine.history_is_still_readable())

    def test_a_snapshot_covering_it_is_what_clears_the_report(self):
        sync_engine.write_state({'sealed_from': 140, 'snapshot_seq': 139})
        self.assertTrue(sync_engine.history_is_still_readable())
        sync_engine.write_state({'snapshot_seq': 140})
        self.assertFalse(sync_engine.history_is_still_readable())

    def test_an_unencrypted_account_never_reports_it(self):
        self.use(config())
        sync_engine.write_state({'sealed_from': 140, 'snapshot_seq': 0})
        self.assertFalse(sync_engine.history_is_still_readable())

    def test_such_an_account_does_not_wait_two_thousand_operations(self):
        """
        The ordinary gate keeps snapshots rare, which is right - but a
        fortnight of it would be a fortnight of the whole history sitting
        readable while the screen claims otherwise.
        """
        sync_engine.write_state({
            'snapshot_staged': 0, 'base_seq': 140, 'server_head': 140,
            'snapshot_seq': 0, 'snapshot_tried': 0, 'sealed_from': 140})
        tracker = unittest.mock.Mock()
        tracker.data = {'projects': [{'uid': 'p' * 16, 'name': 'x'}]}
        tracker.op_outbox = self.outbox

        self.assertEqual(sync_engine.offer_snapshot(tracker), 140)

    def test_without_a_plain_past_the_ordinary_gate_still_applies(self):
        """The shortcut is for the migration, not a new snapshot policy."""
        sync_engine.write_state({
            'snapshot_staged': 0, 'base_seq': 140, 'server_head': 140,
            'snapshot_seq': 0, 'snapshot_tried': 0, 'sealed_from': 0})
        tracker = unittest.mock.Mock()
        tracker.data = {'projects': [{'uid': 'p' * 16, 'name': 'x'}]}
        tracker.op_outbox = self.outbox

        self.assertEqual(sync_engine.offer_snapshot(tracker), 0)


class TestWhatTheServerIsHeldTo(WiringTestCase):
    """
    The server can read nothing, but it still assigns the order, stamps which
    device did what, and chooses what to hand on. These are the parts of that
    it can be held to - and, just as deliberately, the parts it cannot.
    """

    def setUp(self):
        super().setUp()
        self.key = self.hold_key()
        self.use(config(switched_on()))

    def _from(self, device, lc, **fields):
        """An operation that device really sealed, as the server would hold it."""
        op = dict({'op': 'task.set', 'uid': T1, 'f': {'priority': lc}}, **fields)
        wire = sync_crypto.seal(dict(op, lc=lc, dev=device), self.key, ACCOUNT)
        entry = dict(wire, s=len(self.server.log) + 1, dev=device)
        self.server.log.append(entry)
        return entry

    # -- what is enforced --------------------------------------------------

    def test_relabelling_whose_work_it_was_is_caught(self):
        """
        The stamp is the server's own word and nothing signs it. The sealing
        device names itself inside the ciphertext, and the two are held
        against each other.
        """
        planted = self._from(OTHER_DEVICE, 5)
        planted['dev'] = 'cccccccccccccccc'        # the server changes its mind

        outcome = sync_engine.run_cycle(self.outbox)
        self.assertEqual(outcome.get('error'), 'e2ee_wrong_device')
        self.assertEqual(self.inbox_ops(), [])
        self.assertEqual(self.cursor(), 0)

    def test_an_operation_played_a_second_time_is_caught(self):
        self._from(OTHER_DEVICE, 5)
        self.assertTrue(sync_engine.run_cycle(self.outbox).get('ok'))

        self._from(OTHER_DEVICE, 5)               # the same counter once more
        sync_engine.write_state({'next_attempt': 0})
        outcome = sync_engine.run_cycle(self.outbox)
        self.assertEqual(outcome.get('error'), 'e2ee_out_of_order')

    def test_two_swapped_round_are_caught(self):
        self._from(OTHER_DEVICE, 9)
        self._from(OTHER_DEVICE, 4)               # earlier work, later place
        outcome = sync_engine.run_cycle(self.outbox)
        self.assertEqual(outcome.get('error'), 'e2ee_out_of_order')
        self.assertEqual(self.inbox_ops(), [])

    def test_the_counters_are_kept_when_the_work_is_filed(self):
        self._from(OTHER_DEVICE, 5)
        self.assertTrue(sync_engine.run_cycle(self.outbox).get('ok'))
        self.assertEqual(sync_engine.read_state()['device_lc'][OTHER_DEVICE], 5)

    def test_and_not_when_filing_it_fails(self):
        """
        The half that matters. Raised before the work is on disk, the counters
        would believe they had seen operations that are about to arrive all
        over again - and the retry would be turned away as a replay, leaving
        the machine stuck on work it never actually received.
        """
        self._from(OTHER_DEVICE, 5)
        real = sync_engine._append_inbox
        sync_engine._append_inbox = lambda record: (_ for _ in ()).throw(
            OSError('the disk is full'))
        self.addCleanup(setattr, sync_engine, '_append_inbox', real)

        try:
            sync_engine.run_cycle(self.outbox)
        except OSError:
            pass
        self.assertEqual(sync_engine.read_state()['device_lc'], {},
                         'the counters moved over work that was never filed')

    def test_a_log_that_is_not_the_one_we_know_clears_them(self):
        """
        Carried over into a different log, the same numbers would come round
        from the start and every one of them would read as a replay.
        """
        sync_engine.write_state({'device_lc': {OTHER_DEVICE: 900},
                                 'base_seq': 500})
        self._from(OTHER_DEVICE, 5)

        # The cycle that notices finds the cursor pointing past this log and
        # starts again from the beginning; the counters go with it, and this
        # round fetches nothing because the reply was written for the old
        # cursor.
        self.assertTrue(sync_engine.run_cycle(self.outbox).get('ok'))
        self.assertEqual(sync_engine.read_state()['device_lc'], {})
        self.assertEqual(self.cursor(), 0)

        # And a counter that would have been a replay against the old map is
        # simply taken against the cleared one. Asked of the check directly:
        # what happens after a reset depends on how far the reply that
        # triggered it had already been written for the old cursor, and that
        # is not what this is about.
        after = sync_engine.read_state()['device_lc']
        self.assertEqual(
            sync_engine._check_device_sequence(
                [{'dev': OTHER_DEVICE, 'lc': 5}], after),
            {OTHER_DEVICE: 5})

    # -- and what is deliberately not --------------------------------------

    def test_a_gap_is_recorded_but_never_stops_anything(self):
        """
        The one signal that cannot be told from ordinary life: the queue
        raises its counter before it writes the line, so a failed write burns
        a number for good. Stopping the sync over a full disk would be worse
        than the withholding it claims to detect.
        """
        self._from(OTHER_DEVICE, 1)
        self.assertTrue(sync_engine.run_cycle(self.outbox).get('ok'))

        self._from(OTHER_DEVICE, 50)              # forty-eight numbers missing
        sync_engine.write_state({'next_attempt': 0})
        self.assertTrue(sync_engine.run_cycle(self.outbox).get('ok'),
                        'a gap stopped the cycle')
        self.assertEqual(sync_engine.read_state()['device_lc'][OTHER_DEVICE], 50)

    def test_nothing_here_claims_to_order_two_devices_against_each_other(self):
        """
        Which of two machines' edits wins is the sequence number's to decide,
        and the server hands those out. There is no order the clients can work
        out for themselves, so there is nothing to hold it to - and no test
        here should suggest otherwise.
        """
        self._from(OTHER_DEVICE, 1)
        self._from('dddddddddddddddd', 1)         # a second device, same counter
        self.assertTrue(sync_engine.run_cycle(self.outbox).get('ok'),
                        'two devices were held to one shared counter')

    def test_an_unsealed_account_is_held_to_none_of_it(self):
        """Without encryption the stamp is the server's unsupported word."""
        self.use(config())
        self.server.plant({'op': 'task.set', 'uid': T1, 'lc': 9,
                           'f': {'priority': 1}})
        self.server.plant({'op': 'task.set', 'uid': T1, 'lc': 2,
                           'f': {'priority': 2}})
        self.assertTrue(sync_engine.run_cycle(self.outbox).get('ok'))

    def test_operations_sealed_before_this_existed_still_open(self):
        """They carry no claim about a device, and are taken as they always were."""
        self.seal_from_other({'op': 'task.set', 'uid': T1,
                              'f': {'task_name': 'Alt'}}, self.key, lc=7)
        self.assertTrue(sync_engine.run_cycle(self.outbox).get('ok'))
        self.assertTrue([op for op in self.inbox_ops() if op.get('uid') == T1])

    def test_what_this_machine_seals_names_itself(self):
        self.outbox.append('task.set', uid=T1, f={'today': True})
        sync_engine.run_cycle(self.outbox)
        sent = self.server.pushed[0][0]
        opened = sync_crypto.open_sealed(sent, self.key, ACCOUNT)
        self.assertEqual(opened['dev'], 'aaaaaaaaaaaaaaaa')

    def test_both_codes_have_something_to_say(self):
        from tt.sync_messages import sync_error_message
        for code in ('e2ee_wrong_device', 'e2ee_out_of_order'):
            self.assertNotIn('Unexpected error', sync_error_message(code))
            self.assertIn(code, sync_engine.TERMINAL_ERRORS)


class TestChangingThePassphrase(WiringTestCase):
    """
    All or nothing, and that is the whole design.

    The obvious shape - adopt the new key, let the next snapshot catch up -
    leaves the server's newest document sealed with the old passphrase while
    new work is sealed with the new one. That window is not short: a snapshot
    is only accepted at head, another machine pushing wins the race, and every
    lost attempt costs six hours. Inside it the only thing that can read the
    account is a key nobody has been told about any more.
    """

    DOCUMENT = {'projects': [{'uid': 'p' * 16, 'name': 'Prüfstatik',
                              'tasks': [{'uid': T1, 'task_name': 'Angebot'}]}]}

    def setUp(self):
        super().setUp()
        self.offered = []
        self.refuse = None
        sync_client.put_snapshot = lambda seq, doc: (
            {'ok': False, 'error': self.refuse} if self.refuse
            else (self.offered.append((seq, doc)) or {'ok': True, 'snapshot_seq': seq}))
        self.old = self.hold_key('die alte Passphrase')
        self.config = self.use(config(switched_on()))
        self.tracker = unittest.mock.Mock()
        self.tracker.data = self.DOCUMENT
        self.tracker.op_outbox = self.outbox
        self._caught_up(50)

    def _caught_up(self, head):
        sync_engine.write_state({'base_seq': head, 'server_head': head})

    def _change(self, passphrase='die neue Passphrase'):
        return sync_engine.change_passphrase(self.tracker, self.config, passphrase)

    # -- the happy path ----------------------------------------------------

    def test_it_seals_a_document_with_the_new_key_before_adopting_it(self):
        result = self._change()
        self.assertTrue(result.get('ok'), result.get('error'))

        self.assertEqual(len(self.offered), 1, 'nothing was sent to the server')
        seq, document = self.offered[0]
        self.assertEqual(seq, 50)
        new = sync_secret.status(self.config)[1]
        self.assertEqual(sync_crypto.open_document(document, new, ACCOUNT, 50),
                         self.DOCUMENT)

    def test_the_new_key_becomes_the_one_that_seals(self):
        self._change()
        new = sync_secret.status(self.config)[1]
        self.assertNotEqual(new, self.old)
        self.assertEqual(new, sync_crypto.derive_key(
            'die neue Passphrase', bytes.fromhex(SALT), **FAST))

    def test_the_old_key_is_kept_so_the_log_stays_readable(self):
        self._change()
        self.assertIn(self.old, sync_secret.keyring(self.config),
                      'the key that sealed everything so far was thrown away')

    def test_it_reports_the_fingerprint_to_compare_elsewhere(self):
        result = self._change()
        self.assertEqual(result['fingerprint'],
                         sync_secret.fingerprint_of(self.config))

    def test_the_snapshot_it_made_is_recorded_as_the_current_one(self):
        """Or the machine would keep offering another one every few hours."""
        self._change()
        state = sync_engine.read_state()
        self.assertEqual(int(state['snapshot_seq']), 50)
        self.assertEqual(state['snapshot_key'],
                         sync_crypto.key_id(sync_secret.status(self.config)[1]))

    # -- and the refusals --------------------------------------------------

    def test_a_server_that_refuses_leaves_everything_as_it_was(self):
        """
        The point of the ordering. Interrupted here, the account still has the
        old passphrase and nothing on the server has moved.
        """
        self.refuse = 'not_at_head'
        result = self._change()
        self.assertFalse(result.get('ok'))
        self.assertEqual(result.get('error'), 'not_at_head')
        self.assertEqual(sync_secret.status(self.config)[1], self.old,
                         'the new key was adopted although nothing was stored')
        self.assertEqual(sync_secret.keyring(self.config), [self.old])

    def test_a_machine_that_is_behind_is_told_to_synchronise_first(self):
        sync_engine.write_state({'base_seq': 40, 'server_head': 50})
        self.assertEqual(self._change().get('error'), 'not_caught_up')
        self.assertEqual(self.offered, [])

    def test_unsent_work_blocks_it_too(self):
        self.outbox.append('task.set', uid=T1, f={'today': True})
        self.assertEqual(self._change().get('error'), 'not_caught_up')

    def test_fetched_but_unapplied_work_blocks_it_too(self):
        sync_engine._append_inbox({'base_seq': 50, 'ops': [
            {'s': 50, 'op': 'task.set', 'uid': T1, 'f': {'a': 1}}]})
        self.assertEqual(self._change().get('error'), 'not_caught_up')

    def test_the_same_passphrase_again_is_refused_rather_than_done(self):
        self.assertEqual(self._change('die alte Passphrase').get('error'),
                         'passphrase_unchanged')
        self.assertEqual(self.offered, [])

    def test_an_empty_passphrase_is_refused(self):
        self.assertEqual(self._change('').get('error'), 'no_passphrase')

    def test_an_empty_document_is_not_sealed_as_the_truth(self):
        self.tracker.data = {'projects': []}
        self.assertEqual(self._change().get('error'), 'nothing_to_seal')

    def test_a_machine_without_the_key_cannot_change_it(self):
        sync_secret.forget_key()
        self.assertEqual(self._change().get('error'), 'e2ee_locked')
        self.assertEqual(self.offered, [])

    def test_an_unencrypted_account_has_no_passphrase_to_change(self):
        self.config = self.use(config())
        self.assertEqual(self._change().get('error'), 'e2ee_off')

    def test_every_code_it_returns_has_something_to_say(self):
        from tt.sync_messages import sync_error_message
        for code in ('not_caught_up', 'passphrase_unchanged', 'no_passphrase',
                     'nothing_to_seal', 'e2ee_off', 'e2ee_locked',
                     'e2ee_unknown_key'):
            self.assertNotIn('Unexpected error', sync_error_message(code),
                             '%s has no explanation' % code)


class TestTheSettingReachesTheCycle(WiringTestCase):

    def test_ensure_started_is_what_hands_the_settings_over(self):
        sync_engine._config_seen = None
        cfg = config(switched_on())
        sync_engine.ensure_started(cfg)
        self.assertIs(sync_engine._config_seen, cfg)

    def test_a_key_entered_later_is_picked_up_without_a_restart(self):
        """
        status() is asked afresh every cycle, so typing the passphrase in the
        settings takes effect on the next round.
        """
        self.use(config(switched_on()))
        self.outbox.append('task.set', uid=T1, f={'today': True})
        self.assertEqual(sync_engine.run_cycle(self.outbox).get('error'),
                         'e2ee_locked')

        self.hold_key()
        sync_engine.write_state({'next_attempt': 0})
        self.assertTrue(sync_engine.run_cycle(self.outbox).get('ok'))
        self.assertEqual(self.server.pushed[0][0]['op'], sync_crypto.SEALED_OP)


if __name__ == '__main__':
    unittest.main()
