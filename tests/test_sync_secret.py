"""
The switch, the salt and the key on this machine.

Every test runs against its own configuration directory. That is not tidiness:
the real one is per user rather than per checkout, and a test reaching it would
be writing into the credential and the key of the installation somebody is
actually using.

The derivation is slow by design, so the parameters are lowered wherever the
test is about the bookkeeping rather than the cryptography.
"""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from tt import sync_client, sync_crypto, sync_secret

FAST = {'n': 1 << 10, 'r': 8, 'p': 1}


def _config(enabled=True, salt='ab' * 16, **extra):
    block = {'enabled': enabled, 'salt': salt}
    block.update(FAST)
    block.update(extra)
    return {'sync': {'enabled': True, 'base_url': 'https://example.invalid/tc',
                     'e2ee': block}}


class Isolated(unittest.TestCase):
    """A configuration directory of its own, and a signed-in account by default."""

    ACCOUNT = 'frank'

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self._before = os.environ.get('TC_CONFIG_DIR')
        os.environ['TC_CONFIG_DIR'] = self.dir
        self.addCleanup(self._restore)

        # enable() derives with whatever the module currently calls its
        # defaults, and those are slow by design - a second of the suite per
        # handful of tests. What is being checked here is the bookkeeping
        # around the key, not the cost of making one; the shipped work factor
        # is exercised once, in test_sync_crypto.
        self.addCleanup(setattr, sync_crypto, 'SCRYPT_N', sync_crypto.SCRYPT_N)
        sync_crypto.SCRYPT_N = FAST['n']

        self._sign_in(self.ACCOUNT)

    def _restore(self):
        if self._before is None:
            os.environ.pop('TC_CONFIG_DIR', None)
        else:
            os.environ['TC_CONFIG_DIR'] = self._before

    def _sign_in(self, username):
        sync_client._write_private(
            os.path.join(self.dir, 'sync_credentials.json'),
            {'version': 1, 'base_url': 'https://example.invalid/tc',
             'username': username, 'token': 'tc1.x.y', 'expires_at': 99999999999})

    def _sign_out(self):
        os.remove(os.path.join(self.dir, 'sync_credentials.json'))


class TestOffIsTheDefaultAndOffMeansOff(Isolated):

    def test_a_configuration_that_says_nothing_is_off(self):
        for config in ({}, {'language': 'en'}, {'sync': {}},
                       {'sync': {'enabled': True, 'base_url': 'https://x/'}}):
            self.assertEqual(sync_secret.status(config), (sync_secret.OFF, None))

    def test_an_explicit_false_is_off(self):
        self.assertFalse(sync_secret.is_enabled(_config(enabled=False)))
        self.assertEqual(sync_secret.status(_config(enabled=False))[0],
                         sync_secret.OFF)

    def test_only_a_real_true_counts(self):
        """A truthy value is not a yes - this decides whether data is readable."""
        for sloppy in ('true', 1, 'yes', [1], {'a': 1}):
            self.assertFalse(sync_secret.is_enabled(_config(enabled=sloppy)))

    def test_rubbish_in_the_block_reads_as_off_rather_than_breaking(self):
        for broken in ({'sync': {'e2ee': 'yes'}}, {'sync': {'e2ee': []}},
                       {'sync': 'nonsense'}, None, 'nonsense', []):
            self.assertEqual(sync_secret.status(broken), (sync_secret.OFF, None))

    def test_being_off_asks_nothing_of_the_account(self):
        """No sign-in, no key, no salt - and still simply off."""
        self._sign_out()
        self.assertEqual(sync_secret.status({'sync': {'enabled': True}}),
                         (sync_secret.OFF, None))


class TestSwitchingItOn(Isolated):

    def test_enabling_writes_a_salt_and_leaves_the_machine_ready(self):
        config, state = sync_secret.enable({}, 'eine gute Passphrase')
        self.assertEqual(state, sync_secret.READY)
        block = config['sync']['e2ee']
        self.assertIs(block['enabled'], True)
        self.assertEqual(len(bytes.fromhex(block['salt'])),
                         sync_crypto.SALT_BYTES)
        self.assertEqual(sync_secret.status(config)[0], sync_secret.READY)

    def test_the_parameters_travel_with_the_salt(self):
        """Or raising them later strands every key derived before the change."""
        config, _ = sync_secret.enable({}, 'passphrase')
        block = config['sync']['e2ee']
        self.assertEqual(block['n'], sync_crypto.SCRYPT_N)
        self.assertEqual(block['r'], sync_crypto.SCRYPT_R)
        self.assertEqual(block['p'], sync_crypto.SCRYPT_P)

    def test_enabling_keeps_the_rest_of_the_configuration(self):
        before = {'language': 'de', 'sync': {'enabled': True, 'interval_minutes': 7}}
        config, _ = sync_secret.enable(before, 'passphrase')
        self.assertEqual(config['language'], 'de')
        self.assertEqual(config['sync']['interval_minutes'], 7)

    def test_enabling_does_not_change_the_configuration_it_was_given(self):
        """The caller saves the result; a mutated argument would save itself."""
        before = {'sync': {'enabled': True}}
        sync_secret.enable(before, 'passphrase')
        self.assertNotIn('e2ee', before['sync'])

    def test_two_accounts_get_different_salts(self):
        one, _ = sync_secret.enable({}, 'passphrase')
        two, _ = sync_secret.enable({}, 'passphrase')
        self.assertNotEqual(one['sync']['e2ee']['salt'],
                            two['sync']['e2ee']['salt'])

    def test_it_cannot_be_switched_on_before_signing_in(self):
        """The account names the ciphertext; without it there is nothing to bind to."""
        self._sign_out()
        config, state = sync_secret.enable({}, 'passphrase')
        self.assertEqual(state, sync_secret.NOT_SIGNED_IN)
        self.assertEqual(sync_secret.settings(config), {})

    def test_an_empty_passphrase_is_refused(self):
        with self.assertRaises(sync_crypto.SealError):
            sync_secret.enable({}, '')

    def test_the_passphrase_itself_is_never_written_down(self):
        secret = 'Sonnenblume-Kartoffel-42'
        config, _ = sync_secret.enable({}, secret)
        for root, _dirs, names in os.walk(self.dir):
            for name in names:
                with open(os.path.join(root, name), 'rb') as f:
                    self.assertNotIn(secret.encode(), f.read(),
                                     '%s holds the passphrase' % name)
        self.assertNotIn(secret, str(config))


class TestTheSecondMachine(Isolated):

    def test_the_same_passphrase_and_salt_give_the_same_key(self):
        config, _ = sync_secret.enable({}, 'gemeinsame Passphrase')
        first = sync_secret.status(config)[1]

        sync_secret.forget_key()
        self.assertEqual(sync_secret.status(config)[0], sync_secret.LOCKED)

        self.assertEqual(sync_secret.unlock(config, 'gemeinsame Passphrase'),
                         sync_secret.READY)
        self.assertEqual(sync_secret.status(config)[1], first)

    def test_a_mistyped_passphrase_gives_a_different_key(self):
        config, _ = sync_secret.enable({}, 'gemeinsame Passphrase')
        right = sync_secret.status(config)[1]
        sync_secret.unlock(config, 'gemeinsame Passphrase ')
        self.assertNotEqual(sync_secret.status(config)[1], right)

    def test_the_fingerprint_is_what_makes_that_visible(self):
        config, _ = sync_secret.enable({}, 'gemeinsame Passphrase')
        right = sync_secret.fingerprint_of(config)
        sync_secret.unlock(config, 'Gemeinsame Passphrase')
        self.assertNotEqual(sync_secret.fingerprint_of(config), right)

    def test_without_a_key_this_machine_is_locked_not_off(self):
        """
        The distinction the sync path depends on: off may send plain text,
        locked may not send anything at all.
        """
        config = _config()
        self.assertEqual(sync_secret.status(config)[0], sync_secret.LOCKED)

    def test_unlocking_an_account_that_is_switched_off_does_nothing(self):
        self.assertEqual(sync_secret.unlock(_config(enabled=False), 'x'),
                         sync_secret.OFF)


class TestWhenTheKeyDoesNotBelongHere(Isolated):

    def test_a_key_from_another_salt_is_stale_not_ready(self):
        """The ordinary way this happens is a config.json copied from elsewhere."""
        config, _ = sync_secret.enable({}, 'passphrase')
        config['sync']['e2ee']['salt'] = 'cd' * 16
        self.assertEqual(sync_secret.status(config)[0], sync_secret.STALE)

    def test_a_key_from_another_account_is_stale(self):
        config, _ = sync_secret.enable({}, 'passphrase')
        self._sign_in('somebody-else')
        self.assertEqual(sync_secret.status(config)[0], sync_secret.STALE)

    def test_a_key_file_from_a_future_version_is_stale(self):
        config, _ = sync_secret.enable({}, 'passphrase')
        record = sync_secret.stored()
        record['version'] = sync_secret.KEY_FILE_VERSION + 1
        sync_client._write_private(sync_secret._key_path(), record)
        self.assertEqual(sync_secret.status(config)[0], sync_secret.STALE)

    def test_a_damaged_key_is_stale_rather_than_an_exception(self):
        config, _ = sync_secret.enable({}, 'passphrase')
        for broken in ('', 'zz', 'ab' * 8):
            record = sync_secret.stored()
            record['key'] = broken
            sync_client._write_private(sync_secret._key_path(), record)
            self.assertEqual(sync_secret.status(config)[0], sync_secret.STALE)

    def test_a_salt_of_the_wrong_shape_is_reported(self):
        for bad in ('', 'xyz', 'ab' * 8, 42, None):
            self.assertEqual(sync_secret.status(_config(salt=bad))[0],
                             sync_secret.NO_SALT)

    def test_signing_out_does_not_make_a_switched_on_account_look_off(self):
        config, _ = sync_secret.enable({}, 'passphrase')
        self._sign_out()
        self.assertEqual(sync_secret.status(config)[0],
                         sync_secret.NOT_SIGNED_IN)

    def test_no_state_but_ready_ever_hands_out_a_key(self):
        config, _ = sync_secret.enable({}, 'passphrase')
        self._sign_in('somebody-else')
        for config in (_config(), _config(enabled=False), _config(salt='xx'),
                       {}, config):
            state, key = sync_secret.status(config)
            if state != sync_secret.READY:
                self.assertIsNone(key, 'state %r handed out a key' % state)


class TestSwitchingItBackOn(Isolated):
    """
    The one way this feature could destroy data by itself.

    disable() keeps the key so that what is already sealed on the server stays
    readable. That is worth nothing if the way back makes a second salt: the
    same passphrase then yields a different key, and every operation sealed
    under the first one is orphaned - unreadable by any machine, with nothing
    on screen to say so.
    """

    def test_the_salt_and_the_key_survive_a_trip_through_off(self):
        config, _ = sync_secret.enable({}, 'meine Passphrase')
        salt, key = config['sync']['e2ee']['salt'], sync_secret.status(config)[1]

        back, state = sync_secret.enable(sync_secret.disable(config))
        self.assertEqual(state, sync_secret.READY)
        self.assertEqual(back['sync']['e2ee']['salt'], salt,
                         'switching back on made a new salt')
        self.assertEqual(sync_secret.status(back)[1], key,
                         'switching back on made a different key')

    def test_a_passphrase_offered_by_mistake_changes_nothing(self):
        """
        A mistyped one would derive a perfectly valid second key and break
        exactly as quietly, so it is not taken at all rather than checked.
        """
        config, _ = sync_secret.enable({}, 'meine Passphrase')
        key = sync_secret.status(config)[1]
        back, _state = sync_secret.enable(sync_secret.disable(config),
                                          'etwas ganz anderes')
        self.assertEqual(sync_secret.status(back)[1], key)

    def test_the_rest_of_the_block_is_carried_over(self):
        config, _ = sync_secret.enable({}, 'meine Passphrase')
        before = dict(config['sync']['e2ee'])
        back, _state = sync_secret.enable(sync_secret.disable(config))
        self.assertEqual(back['sync']['e2ee'], before)

    def test_without_the_key_it_comes_back_locked_rather_than_ready(self):
        """Then the screen asks for the passphrase, which is the safe path."""
        config, _ = sync_secret.enable({}, 'meine Passphrase')
        off = sync_secret.disable(config)
        sync_secret.forget_key()
        back, state = sync_secret.enable(off)
        self.assertEqual(state, sync_secret.LOCKED)
        self.assertEqual(back['sync']['e2ee']['salt'],
                         config['sync']['e2ee']['salt'])

    def test_an_account_with_a_salt_is_recognised_as_set_up(self):
        config, _ = sync_secret.enable({}, 'meine Passphrase')
        self.assertTrue(sync_secret.is_set_up(config))
        self.assertTrue(sync_secret.is_set_up(sync_secret.disable(config)))
        self.assertFalse(sync_secret.is_set_up({}))
        self.assertFalse(sync_secret.is_set_up(_config(salt='nonsense')))

    def test_a_broken_salt_is_not_treated_as_set_up(self):
        """
        Otherwise the account would be stuck: nothing to derive from, and no
        way to make a salt because one is believed to be there already.
        """
        broken = _config(salt='nonsense')
        self.assertFalse(sync_secret.is_set_up(broken))
        fixed, state = sync_secret.enable(broken, 'eine neue Passphrase')
        self.assertEqual(state, sync_secret.READY)
        self.assertNotEqual(fixed['sync']['e2ee']['salt'], 'nonsense')

    def test_setting_up_the_first_time_still_needs_a_passphrase(self):
        with self.assertRaises(sync_crypto.SealError):
            sync_secret.enable({}, '')
        with self.assertRaises(sync_crypto.SealError):
            sync_secret.enable({})


class TestSwitchingItOff(Isolated):

    def test_disabling_leaves_the_switch_off(self):
        config, _ = sync_secret.enable({}, 'passphrase')
        off = sync_secret.disable(config)
        self.assertFalse(sync_secret.is_enabled(off))
        self.assertEqual(sync_secret.status(off), (sync_secret.OFF, None))

    def test_disabling_keeps_the_key_because_the_old_log_still_needs_it(self):
        config, _ = sync_secret.enable({}, 'passphrase')
        key = sync_secret.status(config)[1]
        off = sync_secret.disable(config)
        self.assertIsNotNone(sync_secret.stored())
        # And switching it back on needs no passphrase.
        back = sync_secret.enable  # not called: the block is still there
        self.assertIsNotNone(back)
        on = dict(off)
        on['sync'] = dict(off['sync'])
        on['sync']['e2ee'] = dict(off['sync']['e2ee'], enabled=True)
        self.assertEqual(sync_secret.status(on), (sync_secret.READY, key))

    def test_disabling_keeps_the_salt_so_the_key_still_matches(self):
        config, _ = sync_secret.enable({}, 'passphrase')
        salt = config['sync']['e2ee']['salt']
        self.assertEqual(sync_secret.disable(config)['sync']['e2ee']['salt'], salt)

    def test_forgetting_the_key_is_a_separate_deliberate_act(self):
        config, _ = sync_secret.enable({}, 'passphrase')
        sync_secret.forget_key()
        self.assertIsNone(sync_secret.stored())
        self.assertEqual(sync_secret.status(config)[0], sync_secret.LOCKED)

    def test_forgetting_a_key_that_is_not_there_is_harmless(self):
        sync_secret.forget_key()
        sync_secret.forget_key()

    def test_disabling_an_account_that_was_never_on_changes_nothing(self):
        before = {'language': 'de', 'sync': {'enabled': True}}
        self.assertEqual(sync_secret.disable(before), before)


class TestTheStoredKeyIsKeptPrivate(Isolated):

    @unittest.skipIf(os.name == 'nt', 'POSIX permissions only')
    def test_the_key_file_is_readable_only_by_its_owner(self):
        sync_secret.enable({}, 'passphrase')
        mode = os.stat(sync_secret._key_path()).st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_it_sits_beside_the_token_not_beside_data_json(self):
        sync_secret.enable({}, 'passphrase')
        self.assertEqual(os.path.dirname(sync_secret._key_path()),
                         sync_client.config_dir())

    def test_the_record_names_the_key_it_holds(self):
        config, _ = sync_secret.enable({}, 'passphrase')
        key = sync_secret.status(config)[1]
        self.assertEqual(sync_secret.stored()['key_id'], sync_crypto.key_id(key))


if __name__ == '__main__':
    unittest.main()
