"""
The cryptographic core: what it protects, and what it refuses.

The key derivation is deliberately slow - that is its job - so it is done once
per class and the derived keys are reused. Only one test exercises the real
default parameters end to end; the rest pass weaker ones explicitly, which is
safe here because what they check is the construction, not its cost.
"""

import base64
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from tt import sync_crypto
from tt.sync_crypto import OpenError, SealError

# Weak on purpose: fast enough to run hundreds of times, and nothing these
# tests assert depends on the work factor.
FAST = {'n': 1 << 10, 'r': 8, 'p': 1}

ACCOUNT = 'frank@example'


def _key(passphrase='correct horse battery staple', salt=b'\x01' * 16):
    return sync_crypto.derive_key(passphrase, salt, **FAST)


class TestAnOperationSurvivesTheRoundTrip(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.key = _key()

    def _round(self, op):
        wire = sync_crypto.seal(op, self.key, ACCOUNT)
        return sync_crypto.open_sealed(wire, self.key, ACCOUNT)

    def test_a_plain_operation_comes_back_unchanged(self):
        op = {'op': 'task.set', 'lc': 7, 'uid': 'a1b2c3d4e5f60718',
              'f': {'task_name': 'Angebot schreiben', 'priority': 3}}
        self.assertEqual(self._round(op), op)

    def test_a_deletion_carries_no_fields_and_still_survives(self):
        """The case that made sealing the whole operation necessary."""
        op = {'op': 'task.delete', 'lc': 12, 'uid': 'a1b2c3d4e5f60718',
              'ts': '2026-09-17 10:04:00'}
        self.assertEqual(self._round(op), op)

    def test_umlauts_and_other_unicode_survive(self):
        op = {'op': 'project.set', 'lc': 3,
              'f': {'name': 'Müller & Söhne – Prüfstatik ✓'}}
        self.assertEqual(self._round(op), op)

    def test_none_is_not_turned_into_something_else(self):
        op = {'op': 'task.set', 'lc': 4,
              'f': {'due_date': None, 'today': False, 'priority': 0}}
        back = self._round(op)
        self.assertIsNone(back['f']['due_date'])
        self.assertIs(back['f']['today'], False)
        self.assertEqual(back['f']['priority'], 0)

    def test_a_long_note_survives(self):
        """A forwarded mail body is what actually goes in here."""
        op = {'op': 'task.set', 'lc': 5, 'f': {'note': 'Zeile\n' * 5000}}
        self.assertEqual(self._round(op), op)


class TestWhatTheServerGetsToSee(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.key = _key()

    def test_no_part_of_the_operation_appears_on_the_wire(self):
        """
        The test this module exists for. Every distinctive string in the
        operation - the verb, the identifiers, the names, the timestamp - must
        be absent from the envelope in any form.
        """
        op = {'op': 'task.set', 'lc': 9, 'uid': 'deadbeefcafe0001',
              'project': 'feedfacefeed0002', 'ts': '2026-09-17 11:00:00',
              'f': {'task_name': 'Kundentermin Dlubal',
                    'note': 'Telefonnummer 0123 456789'}}
        wire = json.dumps(sync_crypto.seal(op, self.key, ACCOUNT))
        for secret in ('task.set', 'deadbeefcafe0001', 'feedfacefeed0002',
                       '2026-09-17', 'Kundentermin', 'Dlubal',
                       'Telefonnummer', '0123', 'task_name', 'note'):
            self.assertNotIn(secret, wire,
                             '%r reached the wire in the clear' % secret)

    def test_the_envelope_carries_only_what_the_server_needs(self):
        op = {'op': 'entry.close', 'lc': 2, 'uid': 'aaaabbbbccccdddd',
              'end': '2026-09-17 17:30:00'}
        wire = sync_crypto.seal(op, self.key, ACCOUNT)
        self.assertEqual(set(wire), {'op', 'lc', 'f'})
        self.assertEqual(set(wire['f']), {'v', 'k', 'c'})

    def test_the_verb_is_the_same_placeholder_for_every_operation(self):
        """Otherwise the stream of verbs is still a readable diary."""
        verbs = {sync_crypto.seal(op, self.key, ACCOUNT)['op'] for op in (
            {'op': 'task.create', 'lc': 1},
            {'op': 'task.delete', 'lc': 2},
            {'op': 'entry.add', 'lc': 3},
            {'op': 'project.set', 'lc': 4},
        )}
        self.assertEqual(verbs, {sync_crypto.SEALED_OP})

    def test_the_fields_stay_an_object(self):
        """
        The server refuses anything else with 'bad_fields'
        (php-server/tc/lib/oplog.php:600-602), so this is what lets the
        operation path work without a server change.
        """
        wire = sync_crypto.seal({'op': 'task.set', 'lc': 1}, self.key, ACCOUNT)
        self.assertIsInstance(wire['f'], dict)

    def test_the_envelope_uses_only_keys_the_server_keeps(self):
        """
        The server copies a fixed list of keys onto the entry it stores and
        drops everything else without a word (php-server/tc/lib/oplog.php:165).
        A field added to the envelope later would therefore not be rejected -
        it would arrive nowhere, and only the other machine would notice, by
        failing to open what it was sent. Pinned here because the drop is
        silent and this is the side that can see it coming.
        """
        kept = {'op', 'lc', 'uid', 'f', 'ts', 'project', 'task', 'start', 'end'}
        wire = sync_crypto.seal({'op': 'task.set', 'lc': 1}, self.key, ACCOUNT)
        self.assertLessEqual(set(wire), kept,
                             'the envelope grew a field the server discards')

    def test_lc_travels_in_the_clear_because_the_server_dedups_on_it(self):
        wire = sync_crypto.seal({'op': 'task.set', 'lc': 41}, self.key, ACCOUNT)
        self.assertEqual(wire['lc'], 41)

    def test_two_seals_of_the_same_operation_differ(self):
        """A repeated nonce would leak that two operations are identical."""
        op = {'op': 'task.set', 'lc': 1, 'f': {'today': True}}
        blobs = {sync_crypto.seal(op, self.key, ACCOUNT)['f']['c']
                 for _ in range(50)}
        self.assertEqual(len(blobs), 50)

    def test_the_envelope_is_json_serialisable(self):
        """It has to survive the server's decode and re-encode."""
        op = {'op': 'project.set', 'lc': 1, 'f': {'name': 'Ätsch'}}
        wire = sync_crypto.seal(op, self.key, ACCOUNT)
        again = json.loads(json.dumps(wire, ensure_ascii=False))
        self.assertEqual(sync_crypto.open_sealed(again, self.key, ACCOUNT), op)


class TestItRefusesWhatItCannotTrust(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.key = _key()
        cls.other = _key('a different passphrase')
        cls.op = {'op': 'task.set', 'lc': 6, 'f': {'task_name': 'Geheim'}}

    def _wire(self):
        return sync_crypto.seal(self.op, self.key, ACCOUNT)

    def test_the_wrong_passphrase_is_reported_not_swallowed(self):
        with self.assertRaises(OpenError):
            sync_crypto.open_sealed(self._wire(), self.other, ACCOUNT)

    def test_a_different_salt_is_a_different_key(self):
        elsewhere = sync_crypto.derive_key(
            'correct horse battery staple', b'\x02' * 16, **FAST)
        with self.assertRaises(OpenError):
            sync_crypto.open_sealed(self._wire(), elsewhere, ACCOUNT)

    def test_an_altered_ciphertext_is_refused(self):
        wire = self._wire()
        blob = bytearray(base64.b64decode(wire['f']['c']))
        blob[-1] ^= 0x01
        wire['f']['c'] = base64.b64encode(bytes(blob)).decode('ascii')
        with self.assertRaises(OpenError):
            sync_crypto.open_sealed(wire, self.key, ACCOUNT)

    def test_moving_an_operation_to_another_account_is_refused(self):
        """The account is bound into the ciphertext, so this cannot verify."""
        with self.assertRaises(OpenError):
            sync_crypto.open_sealed(self._wire(), self.key, 'someone@else')

    def test_changing_the_lc_on_the_envelope_is_refused(self):
        """
        lc has to stay readable for the server's duplicate suppression, so it
        is bound into the authenticated data instead of hidden. Changing it
        therefore breaks the seal rather than going unnoticed.
        """
        wire = self._wire()
        wire['lc'] = wire['lc'] + 1
        with self.assertRaises(OpenError):
            sync_crypto.open_sealed(wire, self.key, ACCOUNT)

    def test_an_envelope_from_a_later_version_is_refused_not_guessed_at(self):
        wire = self._wire()
        wire['f']['v'] = sync_crypto.ENVELOPE_VERSION + 1
        with self.assertRaises(OpenError):
            sync_crypto.open_sealed(wire, self.key, ACCOUNT)

    def test_a_plain_operation_is_not_mistaken_for_a_sealed_one(self):
        with self.assertRaises(OpenError):
            sync_crypto.open_sealed({'op': 'task.set', 'lc': 1, 'f': {}},
                                    self.key, ACCOUNT)

    def test_rubbish_in_place_of_the_ciphertext_is_refused(self):
        for broken in ('', 'not base64 at all!!', base64.b64encode(b'short').decode()):
            wire = self._wire()
            wire['f']['c'] = broken
            with self.assertRaises(OpenError):
                sync_crypto.open_sealed(wire, self.key, ACCOUNT)

    def test_a_missing_envelope_is_refused(self):
        for broken in ({'op': sync_crypto.SEALED_OP, 'lc': 1},
                       {'op': sync_crypto.SEALED_OP, 'lc': 1, 'f': 'nope'},
                       {'op': sync_crypto.SEALED_OP, 'lc': 1, 'f': []}):
            with self.assertRaises(OpenError):
                sync_crypto.open_sealed(broken, self.key, ACCOUNT)

    def test_an_envelope_whose_inner_lc_was_swapped_is_refused(self):
        """
        Belt and braces behind the authenticated data, which already stops an
        lc being edited in transit. This is the other direction: a sealer that
        took lc from somewhere other than the operation would produce an
        envelope and an operation disagreeing about which push they belong to,
        and the server's duplicate suppression follows the outer one. seal()
        cannot produce this, so it has to be built by hand.
        """
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        inner = {'op': 'task.set', 'lc': 6, 'f': {'task_name': 'x'}}
        nonce = b'\x09' * sync_crypto.NONCE_BYTES
        blob = AESGCM(sync_crypto._subkey(self.key, b'enc')).encrypt(
            nonce, sync_crypto._canonical(inner),
            sync_crypto._associated(ACCOUNT, 5))
        wire = {'op': sync_crypto.SEALED_OP, 'lc': 5,
                'f': {'v': sync_crypto.ENVELOPE_VERSION,
                      'k': sync_crypto.key_id(self.key),
                      'c': base64.b64encode(nonce + blob).decode('ascii')}}
        with self.assertRaises(OpenError):
            sync_crypto.open_sealed(wire, self.key, ACCOUNT)

    def test_an_operation_without_a_usable_lc_is_not_sealed(self):
        for bad in ({'op': 'task.set'}, {'op': 'task.set', 'lc': 0},
                    {'op': 'task.set', 'lc': '3'},
                    {'op': 'task.set', 'lc': True}):
            with self.assertRaises(SealError):
                sync_crypto.seal(bad, self.key, ACCOUNT)

    def test_only_an_object_can_be_sealed(self):
        for bad in ([], 'op', 7, None):
            with self.assertRaises(SealError):
                sync_crypto.seal(bad, self.key, ACCOUNT)


class TestAWholeDocument(unittest.TestCase):
    """
    The snapshot: everything the individual operations hide, in one upload.
    Sealed differently in two ways - compressed first, because base64 over the
    ciphertext would otherwise push a real document past the server's limit,
    and bound to its sequence number, because that number is a claim the
    client has no other way to check.
    """

    @classmethod
    def setUpClass(cls):
        cls.key = _key()
        cls.other = _key('a different passphrase')

    DOCUMENT = {'projects': [{'uid': 'p' * 16, 'name': 'Müller & Söhne',
                              'tasks': [{'uid': 't' * 16, 'note': 'geheim'}]}],
                '_deleted': {}}

    def test_a_document_survives_the_round_trip(self):
        sealed = sync_crypto.seal_document(self.DOCUMENT, self.key, ACCOUNT, 12)
        self.assertEqual(
            sync_crypto.open_document(sealed, self.key, ACCOUNT, 12),
            self.DOCUMENT)

    def test_nothing_of_it_appears_on_the_wire(self):
        sealed = json.dumps(
            sync_crypto.seal_document(self.DOCUMENT, self.key, ACCOUNT, 12))
        for secret in ('projects', 'Müller', 'Söhne', 'geheim', 'tasks',
                       '_deleted', 'p' * 16):
            self.assertNotIn(secret, sealed, '%r reached the wire' % secret)

    def test_the_envelope_is_an_object_the_server_will_accept(self):
        sealed = sync_crypto.seal_document(self.DOCUMENT, self.key, ACCOUNT, 1)
        self.assertEqual(set(sealed), {sync_crypto.DOCUMENT_ENVELOPE})
        self.assertEqual(set(sealed[sync_crypto.DOCUMENT_ENVELOPE]), {'v', 'k', 'c'})
        # It has to survive a decode and re-encode: the server splices the
        # stored bytes straight into its reply.
        again = json.loads(json.dumps(sealed))
        self.assertEqual(
            sync_crypto.open_document(again, self.key, ACCOUNT, 1), self.DOCUMENT)

    def test_a_repetitive_document_does_not_grow(self):
        big = {'projects': [{'uid': '%016x' % i, 'name': 'Projekt %d' % i,
                             'note': 'Immer derselbe Satz. ' * 30}
                            for i in range(200)]}
        plain = len(json.dumps(big, ensure_ascii=False).encode('utf-8'))
        sealed = len(json.dumps(
            sync_crypto.seal_document(big, self.key, ACCOUNT, 1)).encode('utf-8'))
        self.assertLess(sealed, plain)

    def test_offered_under_another_sequence_number_it_is_refused(self):
        """
        The check that matters most here. The client throws its own queue away
        on the strength of the number the server claims, so a document sealed
        for one number must not open under another.
        """
        sealed = sync_crypto.seal_document(self.DOCUMENT, self.key, ACCOUNT, 3)
        with self.assertRaises(OpenError):
            sync_crypto.open_document(sealed, self.key, ACCOUNT, 99)

    def test_the_wrong_key_and_another_account_are_refused(self):
        sealed = sync_crypto.seal_document(self.DOCUMENT, self.key, ACCOUNT, 5)
        with self.assertRaises(OpenError):
            sync_crypto.open_document(sealed, self.other, ACCOUNT, 5)
        with self.assertRaises(OpenError):
            sync_crypto.open_document(sealed, self.key, 'someone@else', 5)

    def test_a_sealed_operation_cannot_be_served_as_a_document(self):
        """
        Different labels in the authenticated data, so the two cannot be
        swapped even by somebody holding both.
        """
        op = sync_crypto.seal({'op': 'task.set', 'lc': 5}, self.key, ACCOUNT)
        self.assertFalse(sync_crypto.is_sealed_document(op))
        with self.assertRaises(OpenError):
            sync_crypto.open_document(op, self.key, ACCOUNT, 5)

    def test_a_document_from_a_later_version_is_refused(self):
        """
        With a whole, valid ciphertext - so that only the version check can be
        what turns it away. Given a damaged one the length check would do it
        instead, and the test would pass with the version check gone.
        """
        sealed = sync_crypto.seal_document(self.DOCUMENT, self.key, ACCOUNT, 5)
        sealed[sync_crypto.DOCUMENT_ENVELOPE]['v'] = sync_crypto.ENVELOPE_VERSION + 1
        with self.assertRaises(OpenError):
            sync_crypto.open_document(sealed, self.key, ACCOUNT, 5)

    def test_documents_and_operations_are_bound_differently(self):
        """
        The two use their own labels, so a ciphertext of one kind cannot
        verify as the other. Today the envelopes also differ in shape, which
        stops the swap earlier - but that is a property of the wrapping, and
        this is the property of the cryptography.
        """
        for n in (1, 5, 99):
            self.assertNotEqual(sync_crypto._associated(ACCOUNT, n),
                                sync_crypto._document_associated(ACCOUNT, n))

    def test_a_damaged_envelope_is_refused_rather_than_guessed_at(self):
        for broken in ({}, {'e2ee': 'nope'}, {'e2ee': {}},
                       {'e2ee': {'v': 1, 'k': 'x', 'c': 'not base64!!'}},
                       {'e2ee': {'v': 99, 'k': 'x', 'c': 'Zm9v'}},
                       {'projects': []}):
            with self.assertRaises(OpenError):
                sync_crypto.open_document(broken, self.key, ACCOUNT, 5)

    def test_an_altered_ciphertext_is_refused(self):
        sealed = sync_crypto.seal_document(self.DOCUMENT, self.key, ACCOUNT, 5)
        blob = bytearray(base64.b64decode(sealed['e2ee']['c']))
        blob[-1] ^= 0x01
        sealed['e2ee']['c'] = base64.b64encode(bytes(blob)).decode('ascii')
        with self.assertRaises(OpenError):
            sync_crypto.open_document(sealed, self.key, ACCOUNT, 5)

    def test_only_an_object_can_be_sealed(self):
        for bad in ([], 'doc', 7, None):
            with self.assertRaises(SealError):
                sync_crypto.seal_document(bad, self.key, ACCOUNT, 1)


class TestWhatTheCiphertextIsBoundTo(unittest.TestCase):
    """
    The authenticated data pins the envelope to its account and its lc. Its
    exact bytes are part of the format - two builds that disagree about them
    cannot read each other - so they are written down here rather than left
    to whatever the code happens to produce.
    """

    def test_the_binding_has_an_exact_shape(self):
        self.assertEqual(
            sync_crypto._associated('acct', 12),
            b'\x00\x00\x00\x09tc-e2ee-1'
            b'\x00\x00\x00\x011'
            b'\x00\x00\x00\x09op.sealed'
            b'\x00\x00\x00\x04acct'
            b'\x00\x00\x00\x0212')

    def test_every_part_is_preceded_by_its_length(self):
        """
        Not decoration. Joining the parts with a separator instead would make
        the binding depend on no part ever containing that separator - true
        today only because lc is an integer and comes last. A field added
        later would quietly make two different bindings collide, and a
        ciphertext could then be moved between them while still verifying.
        """
        for context, lc in (('a', 1), ('a|1', 1), ('a', 11), ('', 1),
                            ('a|1|2', 3), ('a1', 1), ('a', 111)):
            bound = sync_crypto._associated(context, lc)
            self.assertIn(len(context).to_bytes(4, 'big') + context.encode(),
                          bound)
            self.assertTrue(bound.endswith(
                len(str(lc)).to_bytes(4, 'big') + str(lc).encode()))

    def test_different_accounts_and_counters_never_share_a_binding(self):
        seen = {sync_crypto._associated(c, lc)
                for c in ('a', 'a|1', 'a1', '', 'b')
                for lc in (1, 11, 111)}
        self.assertEqual(len(seen), 15)


class TestTheKeyItself(unittest.TestCase):

    def test_the_same_passphrase_and_salt_give_the_same_key(self):
        self.assertEqual(_key(), _key())

    def test_a_different_passphrase_gives_a_different_key(self):
        self.assertNotEqual(_key(), _key('something else'))

    def test_the_real_parameters_work_and_are_not_free(self):
        """
        Exercises the shipped defaults once. The point is not the timing but
        that OpenSSL accepts them: without an explicit memory ceiling this
        call raises "memory limit exceeded" rather than returning a key.
        """
        key = sync_crypto.derive_key('correct horse battery staple', b'\x01' * 16)
        self.assertEqual(len(key), sync_crypto.KEY_BYTES)

    def test_a_salt_is_random_and_the_right_length(self):
        salts = {sync_crypto.new_salt() for _ in range(50)}
        self.assertEqual(len(salts), 50)
        self.assertTrue(all(len(s) == sync_crypto.SALT_BYTES for s in salts))

    def test_a_salt_of_the_wrong_length_is_refused(self):
        for bad in (b'', b'short', 'sixteen chars!!!'):
            with self.assertRaises(SealError):
                sync_crypto.derive_key('passphrase', bad, **FAST)

    def test_an_empty_passphrase_is_refused(self):
        with self.assertRaises(SealError):
            sync_crypto.derive_key('', b'\x01' * 16, **FAST)

    def test_the_key_id_is_stable_and_short(self):
        self.assertEqual(sync_crypto.key_id(_key()), sync_crypto.key_id(_key()))
        self.assertEqual(len(sync_crypto.key_id(_key())), 8)

    def test_a_different_key_has_a_different_id(self):
        self.assertNotEqual(sync_crypto.key_id(_key()),
                            sync_crypto.key_id(_key('other')))

    def test_the_envelope_names_the_key_that_sealed_it(self):
        key = _key()
        wire = sync_crypto.seal({'op': 'task.set', 'lc': 1}, key, ACCOUNT)
        self.assertEqual(sync_crypto.sealed_key_id(wire), sync_crypto.key_id(key))

    def test_the_fingerprint_is_stable_and_readable(self):
        shown = sync_crypto.fingerprint(_key())
        self.assertEqual(shown, sync_crypto.fingerprint(_key()))
        self.assertRegex(shown, r'^[0-9A-F]{4}(-[0-9A-F]{4}){2}$')

    def test_a_mistyped_passphrase_shows_a_different_fingerprint(self):
        self.assertNotEqual(sync_crypto.fingerprint(_key()),
                            sync_crypto.fingerprint(_key('correct horse battery stapl')))

    def test_the_three_derived_values_are_unrelated(self):
        """
        The encryption key, the public identifier and the fingerprint come
        from the same master and must not be the same bytes - a value read
        off a screen should say nothing about the one that travels.
        """
        master = _key()
        enc = sync_crypto._subkey(master, b'enc')
        self.assertNotEqual(enc.hex()[:8], sync_crypto.key_id(master))
        self.assertNotIn(sync_crypto.key_id(master).upper(),
                         sync_crypto.fingerprint(master))
        self.assertNotEqual(enc, master)


if __name__ == '__main__':
    unittest.main()
