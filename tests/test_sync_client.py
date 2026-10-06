import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from tt import sync_client


class _Response:
    """Stands in for a requests response."""

    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class TestSyncClientPaths(unittest.TestCase):
    """
    Where the credential lives is a correctness question, not a detail: a
    frozen build changes the working directory to wherever the .exe sits, so
    anything resolved relatively would land next to the program - unwritable
    under Program Files, and shared by every account on the machine.
    """

    def test_posix_uses_xdg_config_home(self):
        with patch.object(os, 'name', 'posix'), \
             patch.dict(os.environ, {'XDG_CONFIG_HOME': '/tmp/xdg'}, clear=False):
            self.assertEqual(sync_client.config_dir(), os.path.join('/tmp/xdg', 'TimeControl'))

    def test_posix_falls_back_to_dot_config(self):
        env = {k: v for k, v in os.environ.items() if k != 'XDG_CONFIG_HOME'}
        with patch.object(os, 'name', 'posix'), \
             patch.dict(os.environ, env, clear=True):
            expected = os.path.join(os.path.expanduser('~'), '.config', 'TimeControl')
            self.assertEqual(sync_client.config_dir(), expected)

    def test_windows_uses_appdata(self):
        with patch.object(os, 'name', 'nt'), \
             patch.dict(os.environ, {'APPDATA': r'C:\Users\frank\AppData\Roaming'}, clear=False):
            self.assertEqual(
                sync_client.config_dir(),
                os.path.join(r'C:\Users\frank\AppData\Roaming', 'TimeControl'),
            )

    def test_path_is_absolute_and_outside_the_project(self):
        """It must never resolve against the working directory."""
        self.assertTrue(os.path.isabs(sync_client.config_dir()))
        project = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        self.assertFalse(sync_client.config_dir().startswith(project + os.sep))

    def test_endpoint_accepts_the_forms_people_actually_type(self):
        for given in ("https://x.de/tc", "https://x.de/tc/",
                      "https://x.de/tc/index.php", "  https://x.de/tc//  "):
            self.assertEqual(sync_client._endpoint(given), "https://x.de/tc/index.php", given)


class TestSyncClientCredentials(unittest.TestCase):

    def setUp(self):
        # Redirect the whole credential directory into a temporary one, so no
        # test can touch the real ~/.config/TimeControl.
        self.tmp = tempfile.mkdtemp()
        self._env = patch.dict(os.environ, {'XDG_CONFIG_HOME': self.tmp}, clear=False)
        self._env.start()
        self._posix = patch.object(os, 'name', 'posix')
        self._posix.start()

    def tearDown(self):
        self._posix.stop()
        self._env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_device_identity_is_created_once_and_reused(self):
        first = sync_client.device_identity()
        self.assertRegex(first['device_uid'], r'^[a-f0-9]{16}$')
        self.assertEqual(sync_client.device_identity(), first)

    def test_device_identity_survives_signing_out(self):
        """
        Otherwise every sign-in would look like a new machine to the server,
        pile up device entries and defeat the idempotency that makes a
        repeated sign-in harmless.
        """
        identity = sync_client.device_identity()
        with patch('tt.sync_client.requests.post', return_value=_Response({'ok': True, 'token': 't'})):
            sync_client.login('https://x.de/tc', 'frank', 'pw')
        sync_client.logout()

        self.assertIsNone(sync_client.load_credentials())
        self.assertEqual(sync_client.device_identity()['device_uid'], identity['device_uid'])

    def test_successful_login_stores_the_token(self):
        reply = {'ok': True, 'token': 'tc1.aa.bb', 'expires_at': 1794000000, 'username': 'frank'}
        with patch('tt.sync_client.requests.post', return_value=_Response(reply)) as post:
            result = sync_client.login('https://x.de/tc', 'frank', 'passwort')

        self.assertTrue(result['ok'])
        stored = sync_client.load_credentials()
        self.assertEqual(stored['token'], 'tc1.aa.bb')
        self.assertEqual(stored['base_url'], 'https://x.de/tc/index.php')
        self.assertEqual(stored['username'], 'frank')

        # The device id sent must be the persisted one, not a fresh one.
        sent = json.loads(post.call_args.kwargs['data'])
        self.assertEqual(sent['device_uid'], sync_client.device_identity()['device_uid'])

    def test_failed_login_stores_nothing(self):
        with patch('tt.sync_client.requests.post',
                   return_value=_Response({'ok': False, 'error': 'invalid_credentials'})):
            result = sync_client.login('https://x.de/tc', 'frank', 'falsch')
        self.assertFalse(result['ok'])
        self.assertIsNone(sync_client.load_credentials())

    def test_plain_http_is_refused_before_the_password_is_sent(self):
        with patch('tt.sync_client.requests.post') as post:
            result = sync_client.login('http://x.de/tc', 'frank', 'passwort')
        self.assertEqual(result['error'], 'https_required')
        post.assert_not_called()

    @unittest.skipIf(os.name == 'nt',
                     "chmod only toggles the read-only bit on Windows; what "
                     "keeps the file private there is the ACL on the user's "
                     "own profile directory, which this cannot assert on")
    def test_credential_file_is_owner_only_on_posix(self):
        with patch('tt.sync_client.requests.post', return_value=_Response({'ok': True, 'token': 't'})):
            sync_client.login('https://x.de/tc', 'frank', 'pw')
        path = sync_client._credentials_path()
        self.assertEqual(os.stat(path).st_mode & 0o077, 0,
                         "the credential is readable by someone other than its owner")

    def test_transport_failures_get_their_own_codes(self):
        """
        "Wrong password" and "no network" need different reactions from the
        user, so they must not collapse into one error.
        """
        import requests as real_requests
        cases = [
            (real_requests.exceptions.SSLError, 'tls_failed'),
            (real_requests.exceptions.Timeout, 'timeout'),
            (real_requests.exceptions.ConnectionError, 'unreachable'),
        ]
        for exc, expected in cases:
            with patch('tt.sync_client.requests.post', side_effect=exc()):
                result = sync_client.login('https://x.de/tc', 'frank', 'pw')
            self.assertEqual(result['error'], expected)

    def test_non_json_answer_is_reported_as_such(self):
        """Another application answering on that path, or an HTML error page."""
        with patch('tt.sync_client.requests.post', return_value=_Response(None, status=500)):
            result = sync_client.login('https://x.de/tc', 'frank', 'pw')
        self.assertEqual(result['error'], 'bad_response')

    def test_status_without_a_credential(self):
        self.assertEqual(sync_client.status()['state'], 'not_configured')

    def test_status_reports_a_rejected_token(self):
        with patch('tt.sync_client.requests.post', return_value=_Response({'ok': True, 'token': 't'})):
            sync_client.login('https://x.de/tc', 'frank', 'pw')
        with patch('tt.sync_client.requests.get',
                   return_value=_Response({'ok': False, 'error': 'invalid_token'})):
            self.assertEqual(sync_client.status()['state'], 'rejected')

    def test_status_separates_unreachable_from_rejected(self):
        with patch('tt.sync_client.requests.post', return_value=_Response({'ok': True, 'token': 't'})):
            sync_client.login('https://x.de/tc', 'frank', 'pw')
        import requests as real_requests
        with patch('tt.sync_client.requests.get', side_effect=real_requests.exceptions.Timeout()):
            state = sync_client.status()
        self.assertEqual(state['state'], 'unreachable')
        self.assertEqual(state['error'], 'timeout')

    def test_signing_out_forgets_the_token_even_if_the_server_is_down(self):
        with patch('tt.sync_client.requests.post', return_value=_Response({'ok': True, 'token': 't'})):
            sync_client.login('https://x.de/tc', 'frank', 'pw')
        import requests as real_requests
        with patch('tt.sync_client.requests.get', side_effect=real_requests.exceptions.ConnectionError()):
            sync_client.logout()
        self.assertIsNone(sync_client.load_credentials())

    def test_signing_out_when_never_signed_in(self):
        self.assertEqual(sync_client.logout(), {'ok': True, 'revoked': False})

    def test_login_says_which_field_is_missing(self):
        """
        Two different mistakes with two different remedies, so they must not
        collapse into one message - and neither should reach the network.
        """
        with patch('tt.sync_client.requests.post') as post:
            self.assertEqual(sync_client.login('', 'frank', 'pw')['error'], 'no_server')
            self.assertEqual(sync_client.login('https://x.de/tc', '', 'pw')['error'],
                             'missing_credentials')
            self.assertEqual(sync_client.login('https://x.de/tc', 'frank', '')['error'],
                             'missing_credentials')
        post.assert_not_called()

    def test_a_success_without_a_token_is_not_treated_as_one(self):
        """
        Some other application answering on that path can easily produce a
        body with ok: true in it. Storing that would leave a credential file
        with no token in it, and the failure would surface much later.
        """
        with patch('tt.sync_client.requests.post',
                   return_value=_Response({'ok': True, 'message': 'hello'})):
            result = sync_client.login('https://x.de/tc', 'frank', 'pw')
        self.assertFalse(result['ok'])
        self.assertEqual(result['error'], 'bad_response')
        self.assertIsNone(sync_client.load_credentials())

    def test_status_reports_a_working_token(self):
        with patch('tt.sync_client.requests.post',
                   return_value=_Response({'ok': True, 'token': 't', 'expires_at': 1794000000})):
            sync_client.login('https://x.de/tc', 'frank', 'pw')
        with patch('tt.sync_client.requests.get',
                   return_value=_Response({'ok': True, 'device_uid': 'abc', 'expires_at': 1800000000})):
            state = sync_client.status()

        self.assertEqual(state['state'], 'ok')
        self.assertEqual(state['username'], 'frank')
        self.assertEqual(state['base_url'], 'https://x.de/tc/index.php')
        self.assertEqual(state['device_uid'], 'abc')
        self.assertEqual(state['expires_at'], 1800000000,
                         "the server's answer should win over the stored copy")


class TestRegisteringWithAnInvitation(unittest.TestCase):
    """
    Issue #588. Redeeming a code is a sign-in to an account that did not exist
    a moment ago, and everything after it has to be unable to tell the two
    apart - so these hold register() to what login() already does, and add
    the one thing only it can get wrong: an answer that never arrived.
    """

    CODE = '3f2a9c1bab12cd34'

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._real = sync_client.config_dir
        sync_client.config_dir = lambda: self.tmp
        # The proof of work that goes along (issue #591) is a request of its
        # own, and has its tests below. Out of the way here, so every request
        # these count is the registration's.
        work = patch('tt.sync_client._proof_of_work', return_value=(None, None))
        work.start()
        self.addCleanup(work.stop)

    def tearDown(self):
        sync_client.config_dir = self._real
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _register(self, code=CODE, username='anna', password='long enough, surely'):
        return sync_client.register('https://x.de/tc', code, username, password)

    def test_an_account_made_is_signed_in_to_like_any_other(self):
        reply = {'ok': True, 'token': 'tc1.aa.bb', 'expires_at': 1794000000, 'username': 'anna'}
        with patch('tt.sync_client.requests.post', return_value=_Response(reply)) as post:
            result = self._register(code='3F2A-9C1B-AB12-CD34')

        self.assertTrue(result['ok'])
        self.assertEqual(sync_client.load_credentials()['token'], 'tc1.aa.bb')
        self.assertEqual(sync_client.load_credentials()['username'], 'anna')
        self.assertEqual(post.call_args.kwargs['params'], {'a': 'register'})
        sent = json.loads(post.call_args.kwargs['data'])
        self.assertEqual(sent['code'], self.CODE, "the code was not sent as it was issued")
        self.assertEqual(sent['device_uid'], sync_client.device_identity()['device_uid'])

    def test_a_refusal_stores_nothing(self):
        for error in ('invalid_invite', 'username_taken', 'bad_username'):
            with self.subTest(error=error), \
                 patch('tt.sync_client.requests.post',
                       return_value=_Response({'ok': False, 'error': error}, status=403)) as post:
                self.assertEqual(self._register()['error'], error)
                # A refusal is an answer: nothing here to find out by signing in.
                self.assertEqual(post.call_count, 1)
            self.assertIsNone(sync_client.load_credentials())

    def test_what_can_be_told_here_does_not_reach_the_network(self):
        with patch('tt.sync_client.requests.post') as post:
            self.assertEqual(sync_client.register('', self.CODE, 'anna', 'long enough, surely')['error'],
                             'no_server')
            self.assertEqual(self._register(code=' - ')['error'], 'missing_invite')
            self.assertEqual(self._register(username='')['error'], 'missing_credentials')
            self.assertEqual(self._register(password='short')['error'], 'weak_password')
            self.assertEqual(sync_client.register('http://x.de/tc', self.CODE, 'anna',
                                                  'long enough, surely')['error'],
                             'https_required')
        post.assert_not_called()

    def test_an_answer_that_never_came_is_followed_by_signing_in(self):
        """
        The server finishes creating the account whether or not anybody is
        left to hear about it, and then the code is spent. Trying again would
        say so and nothing else; signing in finds out, and is right either way.
        """
        import requests as real_requests
        answers = [real_requests.exceptions.Timeout(),
                   _Response({'ok': True, 'token': 'tc1.cc.dd', 'expires_at': 1794000000})]
        with patch('tt.sync_client.requests.post', side_effect=answers) as post:
            result = self._register()

        self.assertTrue(result['ok'])
        self.assertTrue(result.get('recovered'))
        self.assertEqual(sync_client.load_credentials()['token'], 'tc1.cc.dd')
        self.assertEqual(post.call_args.kwargs['params'], {'a': 'login'})
        sent = json.loads(post.call_args.kwargs['data'])
        self.assertEqual((sent['username'], sent['password']), ('anna', 'long enough, surely'))

    def test_and_when_that_fails_too_the_first_answer_stands(self):
        """
        The first failure is the one worth reporting. "Wrong username or
        password" for an account that was never made would be a riddle.
        """
        import requests as real_requests
        answers = [real_requests.exceptions.ConnectionError(),
                   _Response({'ok': False, 'error': 'invalid_credentials'}, status=401)]
        with patch('tt.sync_client.requests.post', side_effect=answers) as post:
            result = self._register()
        self.assertEqual(result['error'], 'unreachable')
        self.assertIsNone(sync_client.load_credentials())
        # And signing in really was tried - otherwise this would pass for a
        # register() that never looked.
        self.assertEqual(post.call_count, 2)
        self.assertEqual(post.call_args.kwargs['params'], {'a': 'login'})

    def test_an_answer_that_could_not_be_read_counts_as_none(self):
        """
        A proxy's error page in place of the reply is the same situation: the
        server may have finished behind it.
        """
        answers = [_Response(None, status=502),
                   _Response({'ok': True, 'token': 'tc1.ee.ff', 'expires_at': 1794000000})]
        with patch('tt.sync_client.requests.post', side_effect=answers):
            result = self._register()
        self.assertTrue(result['ok'])
        self.assertEqual(sync_client.load_credentials()['token'], 'tc1.ee.ff')

    def test_a_code_reads_the_same_however_it_was_pasted(self):
        # The server's rule (tc_invite_normalise), which is the one that counts.
        for pasted in ('3F2A-9C1B-AB12-CD34', ' 3f2a 9c1b ab12 cd34\n',
                       '3f2a 9c1b–ab12‑cd34', '3f2a​9c1bab12cd34',
                       # out of a sentence, with its quotes and full stop
                       '"3f2a-9c1b-ab12-cd34".', '„3f2a9c1bab12cd34“'):
            with self.subTest(pasted=pasted):
                self.assertEqual(sync_client.normalise_invite_code(pasted), self.CODE)
        # A letter is never taken out: "code" stays, and so does a stray x,
        # and the server refuses the result rather than reading it as another
        # code - which dropping everything but hex would do.
        self.assertEqual(sync_client.normalise_invite_code('Code: 3f2a'), 'code3f2a')
        self.assertEqual(sync_client.normalise_invite_code('3f2a x9c1b'), '3f2ax9c1b')
        self.assertEqual(sync_client.normalise_invite_code(None), '')


def _zero_bits(challenge, nonce):
    """The server's check, written out again rather than borrowed from the client."""
    digest = hashlib.sha256((challenge + ':' + nonce).encode('ascii')).digest()
    value = int.from_bytes(digest, 'big')
    return 256 - value.bit_length()


class TestSolvingAChallenge(unittest.TestCase):
    """Issue #591: the search for a nonce, on its own."""

    CHALLENGE = '1.register.1791131602.12.0123456789abcdef.' + 'ab' * 32

    def test_the_count_of_leading_zero_bits(self):
        # Ones with bytes after the first that is not zero, which a count
        # that did not stop there would go on adding.
        for digest, bits in ((b'\x80', 0), (b'\x01', 7), (b'\x00\x0f', 12),
                             (b'\x00\x00\x00\x08', 28), (bytes(32), 256),
                             (b'\x0f\x00', 4), (b'\x01\xff', 7), (b'\x00\x10\x00\x00', 11),
                             (b'\x80' + bytes(31), 0)):
            with self.subTest(digest=digest):
                self.assertEqual(sync_client.leading_zero_bits(digest), bits)

    def test_what_it_finds_meets_the_server_s_rule(self):
        for bits in (0, 1, 7, 8, 9, 12, 16):
            with self.subTest(bits=bits):
                nonce = sync_client.solve_challenge(self.CHALLENGE, bits, 30)
                self.assertRegex(nonce, r'^[0-9]{1,64}$', "not a nonce the server takes")
                self.assertGreaterEqual(_zero_bits(self.CHALLENGE, nonce), bits)

    def test_and_it_is_the_first_that_does(self):
        """
        Asking more of itself than the server does would pass every test
        above and cost sixteen times the work for four bits too many.
        """
        for bits in range(0, 13):
            with self.subTest(bits=bits):
                nonce = int(sync_client.solve_challenge(self.CHALLENGE, bits, 30))
                earlier = [n for n in range(nonce) if _zero_bits(self.CHALLENGE, str(n)) >= bits]
                self.assertEqual(earlier, [])

    def test_it_gives_up_when_the_time_is_over(self):
        # Sixty bits would take this machine millennia; a challenge expires
        # long before. And it gives up then, not some while later: this runs
        # while somebody looks at the form.
        started = time.monotonic()
        self.assertIsNone(sync_client.solve_challenge(self.CHALLENGE, 60, 0.2))
        self.assertLess(time.monotonic() - started, 0.7)


class TestAProofOfWorkGoesAlong(unittest.TestCase):
    """
    Issue #591. The server does not demand one while a code is needed to
    register, but checks one that is sent - so the client sends one, and has
    to cope with a server that does not know what that is.
    """

    CODE = '3f2a9c1bab12cd34'

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._real = sync_client.config_dir
        sync_client.config_dir = lambda: self.tmp
        self.sent = []

    def tearDown(self):
        sync_client.config_dir = self._real
        shutil.rmtree(self.tmp, ignore_errors=True)

    @staticmethod
    def challenge(n=0, bits=8, expires_in=300):
        return _Response({'ok': True, 'bits': bits, 'expires_in': expires_in,
                          'challenge': '1.register.%d.%d.0123456789abcdef.%s' % (1791131602 + n, bits, 'c' * 64)})

    def run_register(self, challenges, registrations):
        """Answers each kind of request from its own list, and keeps what was sent."""
        answers = {'challenge': list(challenges), 'register': list(registrations)}

        def post(url, params, headers, data, timeout, allow_redirects):
            self.sent.append((params['a'], json.loads(data)))
            return answers[params['a']].pop(0)

        with patch('tt.sync_client.requests.post', side_effect=post):
            return sync_client.register('https://x.de/tc', self.CODE, 'anna', 'long enough, surely')

    def registrations(self):
        return [body for action, body in self.sent if action == 'register']

    def test_a_solved_challenge_is_sent_with_the_registration(self):
        result = self.run_register([self.challenge()], [_Response({'ok': True, 'token': 'tc1.a.b'})])
        self.assertTrue(result['ok'])
        self.assertEqual([a for a, _ in self.sent], ['challenge', 'register'])
        self.assertEqual(self.sent[0][1], {'purpose': 'register'})
        work = self.registrations()[0]['pow']
        self.assertEqual(work['challenge'], self.challenge().json()['challenge'])
        self.assertGreaterEqual(_zero_bits(work['challenge'], work['nonce']), 8)

    def test_a_server_from_before_it_is_registered_with_all_the_same(self):
        result = self.run_register([_Response({'ok': False, 'error': 'unknown_action'}, status=404)],
                                   [_Response({'ok': True, 'token': 'tc1.a.b'})])
        self.assertTrue(result['ok'])
        self.assertNotIn('pow', self.registrations()[0])

    def test_a_refused_solution_gets_one_fresh_challenge(self):
        # Too late, already used, or no longer taken: the difficulty raised
        # or the key replaced while it was on its way.
        for refusal in ('pow_expired', 'pow_used', 'pow_invalid'):
            with self.subTest(refusal=refusal):
                self.sent = []
                result = self.run_register(
                    [self.challenge(1), self.challenge(2)],
                    [_Response({'ok': False, 'error': refusal}, status=403),
                     _Response({'ok': True, 'token': 'tc1.a.b'})])
                self.assertTrue(result['ok'])
                first, second = self.registrations()
                self.assertNotEqual(first['pow']['challenge'], second['pow']['challenge'])

    def test_but_only_one(self):
        result = self.run_register(
            [self.challenge(1), self.challenge(2)],
            [_Response({'ok': False, 'error': 'pow_used'}, status=403)] * 2)
        self.assertEqual(result['error'], 'pow_used')
        self.assertEqual(len(self.registrations()), 2)

    def test_and_never_with_the_solution_just_refused(self):
        # No fresh challenge to be had: then without one, which a code is
        # enough for, rather than with the one the server just turned down.
        result = self.run_register(
            [self.challenge(1), _Response({'ok': False, 'error': 'busy'}, status=503)],
            [_Response({'ok': False, 'error': 'pow_expired'}, status=403),
             _Response({'ok': True, 'token': 'tc1.a.b'})])
        self.assertTrue(result['ok'])
        self.assertNotIn('pow', self.registrations()[1])

    def test_a_refusal_of_anything_else_is_not_retried(self):
        result = self.run_register([self.challenge()],
                                   [_Response({'ok': False, 'error': 'username_taken'}, status=409)])
        self.assertEqual(result['error'], 'username_taken')
        self.assertEqual(len(self.registrations()), 1)

    def test_a_server_that_cannot_be_reached_is_not_asked_twice_more(self):
        """
        The challenge goes first; when that finds nobody there, registering
        would only find the same - and nothing was registered, so there is
        no sign-in to try either. Three timeouts in a row is a minute of a
        frozen form.
        """
        import requests as real_requests
        for failure, error in ((real_requests.exceptions.Timeout(), 'timeout'),
                               (real_requests.exceptions.ConnectionError(), 'unreachable'),
                               (real_requests.exceptions.SSLError(), 'tls_failed'),
                               (_Response({}, status=301), 'address_redirects')):
            with self.subTest(error=error), \
                 patch('tt.sync_client.requests.post', side_effect=[failure]) as post:
                result = sync_client.register('https://x.de/tc', self.CODE, 'anna', 'long enough, surely')
            self.assertEqual(result, {'ok': False, 'error': error})
            self.assertEqual(post.call_count, 1)

    def test_a_busy_or_odd_challenge_answer_does_not_stop_the_registration(self):
        for answer in (_Response({'ok': False, 'error': 'busy'}, status=503), _Response(None, status=500)):
            with self.subTest(answer=answer._payload):
                self.sent = []
                result = self.run_register([answer], [_Response({'ok': True, 'token': 'tc1.a.b'})])
                self.assertTrue(result['ok'])
                self.assertNotIn('pow', self.registrations()[0])

    def test_the_hardest_challenge_it_takes_on_is_attempted(self):
        with patch('tt.sync_client.solve_challenge', return_value='0') as solve:
            self.run_register([self.challenge(bits=sync_client.POW_MAX_BITS)],
                              [_Response({'ok': True, 'token': 'tc1.a.b'})])
        solve.assert_called_once()
        self.assertIn('pow', self.registrations()[0])

    def test_a_challenge_out_of_reach_is_not_attempted(self):
        # Raised beyond what this machine could solve in the time it has: left
        # to the server, which today does not insist.
        started = time.monotonic()
        result = self.run_register([self.challenge(bits=sync_client.POW_MAX_BITS + 1)],
                                   [_Response({'ok': True, 'token': 'tc1.a.b'})])
        self.assertTrue(result['ok'])
        self.assertNotIn('pow', self.registrations()[0])
        self.assertLess(time.monotonic() - started, 2)

    def test_an_answer_that_does_not_look_like_one_is_not_trusted(self):
        good = self.challenge().json()
        # True among them: Python counts it as the number 1.
        for broken in (dict(good, bits='8'), dict(good, bits=-1), dict(good, bits=True),
                       dict(good, expires_in=True), dict(good, challenge=None),
                       dict(good, challenge='1.register.ü'), dict(good, expires_in=None)):
            with self.subTest(answer=broken):
                self.sent = []
                result = self.run_register([_Response(broken)], [_Response({'ok': True, 'token': 'tc1.a.b'})])
                self.assertTrue(result['ok'])
                self.assertNotIn('pow', self.registrations()[0])

    def test_solving_is_given_the_time_the_challenge_lasts_less_the_margin(self):
        with patch('tt.sync_client.solve_challenge', return_value='0') as solve:
            self.run_register([self.challenge(expires_in=300)], [_Response({'ok': True, 'token': 'tc1.a.b'})])
        self.assertEqual(solve.call_args.args[2], 300 - sync_client.POW_MARGIN)


@unittest.skipUnless(shutil.which('php'), "php is not installed")
class TestTheServerTakesWhatTheClientFinds(unittest.TestCase):
    """
    Each side tested on its own could agree with itself and not with the
    other - a colon too many, the nonce encoded differently. So a challenge
    the server's own code issued is solved here and checked by the server's
    own code.
    """

    LIB = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'php-server', 'tc', 'lib')

    def php(self, store, body):
        script = ("require %r; require %r; require %r; $store = %r; %s"
                  % (os.path.join(self.LIB, 'store.php'), os.path.join(self.LIB, 'auth.php'),
                     os.path.join(self.LIB, 'pow.php'), store, body))
        done = subprocess.run(['php', '-r', script], capture_output=True, text=True,
                              encoding='utf-8', timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def spend(self, store, challenge, nonce):
        return self.php(store, "var_export(tc_pow_spend($store, %r, %r, 'register', null, 12));"
                        % (challenge, nonce))

    def test_a_solution_found_here_is_accepted_there_once(self):
        store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, store, True)
        issued = json.loads(self.php(store, "echo json_encode(tc_pow_challenge($store, 'register', null, 12));"))
        nonce = sync_client.solve_challenge(issued['challenge'], issued['bits'], 30)
        self.assertEqual(self.spend(store, issued['challenge'], nonce), 'NULL')
        self.assertEqual(self.spend(store, issued['challenge'], nonce), "'pow_used'")

    def test_and_one_a_bit_short_of_it_is_not(self):
        # Counted by this file's own reckoning, so neither side's count is
        # what decides whether the two agree.
        store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, store, True)
        issued = json.loads(self.php(store, "echo json_encode(tc_pow_challenge($store, 'register', null, 12));"))
        short = next(str(n) for n in range(10 ** 6) if _zero_bits(issued['challenge'], str(n)) == 11)
        self.assertEqual(self.spend(store, issued['challenge'], short), "'pow_invalid'")


class TestTheRequestsTheLogEndpointsBuild(unittest.TestCase):
    """
    The seam between this application and the server.

    Every test of the sync cycle replaces head/push/pull with a stand-in, so
    without these the functions that actually assemble the request are never
    run at all - and a wrong key or a parameter in the body instead of the
    query string would only show up against the live server.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._real = sync_client.config_dir
        sync_client.config_dir = lambda: self.tmp
        with patch('tt.sync_client.requests.post',
                   return_value=_Response({'ok': True, 'token': 'tok'})):
            sync_client.login('https://x.de/tc', 'frank', 'pw')

    def tearDown(self):
        sync_client.config_dir = self._real
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_none_of_them_work_without_a_credential(self):
        sync_client.clear_credentials()
        with patch('tt.sync_client.requests.get') as get, \
             patch('tt.sync_client.requests.post') as post:
            for call in (lambda: sync_client.head(),
                         lambda: sync_client.push(0, []),
                         lambda: sync_client.pull(0)):
                self.assertEqual(call()['error'], 'not_signed_in')
        get.assert_not_called()
        post.assert_not_called()

    def test_head_is_a_get_carrying_the_token(self):
        with patch('tt.sync_client.requests.get',
                   return_value=_Response({'ok': True, 'head': 7})) as get:
            self.assertEqual(sync_client.head()['head'], 7)

        self.assertEqual(get.call_args.args[0], 'https://x.de/tc/index.php')
        self.assertEqual(get.call_args.kwargs['params'], {'a': 'head'})
        self.assertEqual(get.call_args.kwargs['headers']['X-TC-Token'], 'tok')

    def test_push_sends_the_batch_in_the_body(self):
        ops = [{'op': 'task.set', 'lc': 1, 'uid': 'a' * 16, 'f': {'priority': 3}}]
        with patch('tt.sync_client.requests.post',
                   return_value=_Response({'ok': True, 'head': 1, 'assigned': [[1, 1]]})) as post:
            sync_client.push(12, ops)

        self.assertEqual(post.call_args.kwargs['params'], {'a': 'push'})
        body = json.loads(post.call_args.kwargs['data'])
        self.assertEqual(body['base_seq'], 12)
        self.assertEqual(body['ops'], ops)
        self.assertEqual(post.call_args.kwargs['headers']['X-TC-Token'], 'tok')

    def test_pull_puts_since_and_limit_in_the_query_string(self):
        """
        The server reads both from the query string. Sent in the body they
        would be ignored, since would stay at nought, and every cycle would
        fetch the whole log from the beginning.
        """
        with patch('tt.sync_client.requests.get',
                   return_value=_Response({'ok': True, 'head': 9, 'ops': []})) as get:
            sync_client.pull(40, limit=25)

        self.assertEqual(get.call_args.kwargs['params'],
                         {'a': 'pull', 'since': 40, 'limit': 25})

    def test_pull_asks_for_no_more_than_the_server_will_give(self):
        with patch('tt.sync_client.requests.get',
                   return_value=_Response({'ok': True, 'head': 0, 'ops': []})) as get:
            sync_client.pull(0)
        self.assertEqual(get.call_args.kwargs['params']['limit'],
                         sync_client.MAX_OPS_PER_CALL)

    def test_the_numbers_are_sent_as_numbers(self):
        """A string reaching the server would be compared as one."""
        with patch('tt.sync_client.requests.get',
                   return_value=_Response({'ok': True, 'head': 0, 'ops': []})) as get:
            sync_client.pull('40')
        self.assertEqual(get.call_args.kwargs['params']['since'], 40)

        with patch('tt.sync_client.requests.post',
                   return_value=_Response({'ok': True, 'head': 0})) as post:
            sync_client.push('3', [])
        self.assertEqual(json.loads(post.call_args.kwargs['data'])['base_seq'], 3)

    def test_a_transport_failure_reaches_the_caller_as_a_code(self):
        import requests as real_requests
        with patch('tt.sync_client.requests.post',
                   side_effect=real_requests.exceptions.SSLError()):
            self.assertEqual(sync_client.push(0, [])['error'], 'tls_failed')

    def test_a_rejected_token_is_passed_through_untouched(self):
        """
        The engine keys its backoff on this code, so it has to survive the
        trip rather than being folded into a generic failure.
        """
        with patch('tt.sync_client.requests.get',
                   return_value=_Response({'ok': False, 'error': 'invalid_token'}, status=401)):
            self.assertEqual(sync_client.pull(0)['error'], 'invalid_token')


class TestABatchThatWillActuallyArrive(unittest.TestCase):
    """
    The server reads a bounded amount of request body and treats anything
    longer as an EMPTY request - appending nothing, answering "ok", and
    leaving the client to strike the operations off as delivered. Nothing is
    reported at either end. Counting operations is no protection: five
    hundred carrying notes and task names go well past the limit.
    """

    def _op(self, n, padding=0):
        return {'op': 'task.set', 'lc': n, 'uid': 'a' * 16,
                'f': {'note': 'x' * padding}}

    def test_a_batch_is_cut_by_size_not_only_by_count(self):
        ops = [self._op(n, padding=4000) for n in range(1, 501)]
        batch = sync_client.fit_batch(ops)

        self.assertLess(len(batch), 500, "it still counts operations only")
        body = json.dumps({'base_seq': 0, 'ops': batch}, ensure_ascii=False)
        self.assertLessEqual(len(body.encode('utf-8')), sync_client.MAX_BYTES_PER_CALL * 1.1)

    def test_small_operations_still_go_five_hundred_at_a_time(self):
        ops = [self._op(n) for n in range(1, 900)]
        self.assertEqual(len(sync_client.fit_batch(ops)), sync_client.MAX_OPS_PER_CALL)

    def test_one_operation_too_large_on_its_own_is_still_sent(self):
        """
        Held back, it would sit at the head of the queue and block everything
        behind it for ever. Better to send it and be told.
        """
        ops = [self._op(1, padding=sync_client.MAX_BYTES_PER_CALL * 2), self._op(2)]
        batch = sync_client.fit_batch(ops)
        self.assertEqual(len(batch), 1)
        self.assertEqual(batch[0]['lc'], 1)

    def test_the_order_is_never_disturbed(self):
        """
        The server stamps a batch in the order it receives it and refuses
        anything at or below the highest number it has seen, so a gap would
        strand everything it skipped.
        """
        ops = [self._op(n, padding=3000) for n in range(1, 400)]
        batch = sync_client.fit_batch(ops)
        self.assertEqual([o['lc'] for o in batch], list(range(1, len(batch) + 1)))

    def test_an_empty_queue_yields_an_empty_batch(self):
        self.assertEqual(sync_client.fit_batch([]), [])

    def test_the_limit_leaves_room_for_what_the_client_does_not_count(self):
        """The envelope, and whatever the transfer adds on top."""
        self.assertLess(sync_client.MAX_BYTES_PER_CALL, 1048576)


class TestTheCallCannotHangForEver(unittest.TestCase):
    """
    requests' own timeout starts once the address has been resolved, so it
    does not bound the DNS lookup. That is the hang issue #539 was about, and
    a sync wedged inside it would stop syncing with nothing on screen to say
    so.
    """

    def setUp(self):
        # login() reaches for the device identity, which is written to disk on
        # first use. Without this the suite creates ~/.config/TimeControl on
        # the machine running it.
        self.tmp = tempfile.mkdtemp()
        self._real = sync_client.config_dir
        sync_client.config_dir = lambda: self.tmp

    def tearDown(self):
        sync_client.config_dir = self._real
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_call_that_never_returns_is_abandoned(self):
        import threading
        original = sync_client.DEADLINE
        sync_client.DEADLINE = 0.3
        try:
            with patch('tt.sync_client.requests.post',
                       side_effect=lambda *a, **k: threading.Event().wait()):
                result = sync_client.login('https://x.de/tc', 'frank', 'pw')
        finally:
            sync_client.DEADLINE = original
        self.assertEqual(result['error'], 'timeout')

    def test_the_deadline_is_above_the_request_s_own_worst_case(self):
        """
        Below it, the deadline would fire on ordinary slowness and report a
        hang where there was none. timeout applies to connect and read
        separately, hence twice.
        """
        self.assertGreater(sync_client.DEADLINE, 2 * sync_client.TIMEOUT)


class TestWhichAddressIsActuallyInUse(unittest.TestCase):
    """
    The setting and the credential are two addresses, and only one of them is
    being used. Editing the setting cannot move an existing token, so the
    difference has to be something the interface can ask about rather than
    something the user has to deduce from the sync failing.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._real = sync_client.config_dir
        sync_client.config_dir = lambda: self.tmp

    def tearDown(self):
        sync_client.config_dir = self._real
        shutil.rmtree(self.tmp, ignore_errors=True)

    def sign_in(self, address):
        with patch('tt.sync_client.requests.post',
                   return_value=_Response({'ok': True, 'token': 'tok'})):
            sync_client.login(address, 'frank', 'pw')

    def test_nothing_is_in_use_before_signing_in(self):
        self.assertIsNone(sync_client.active_base_url())

    def test_the_address_in_use_is_the_one_the_token_was_issued_for(self):
        self.sign_in('https://first.example/tc/')
        self.assertEqual(sync_client.active_base_url(),
                         'https://first.example/tc/index.php')

    def test_editing_the_setting_does_not_move_it(self):
        """
        The behaviour the issue is about. It stays deliberately: what changes
        is that it is now visible and reported.
        """
        self.sign_in('https://first.example/tc/')
        self.assertEqual(sync_client.active_base_url(),
                         'https://first.example/tc/index.php')
        self.assertTrue(sync_client.address_changed('https://second.example/tc/'))

    def test_signing_in_again_is_what_switches_over(self):
        self.sign_in('https://first.example/tc/')
        self.sign_in('https://second.example/tc/')

        self.assertEqual(sync_client.active_base_url(),
                         'https://second.example/tc/index.php')
        self.assertFalse(sync_client.address_changed('https://second.example/tc/'))

    def test_the_same_server_written_differently_is_not_a_change(self):
        """
        The setting holds what was typed, the credential holds the normalised
        endpoint. These differ as strings on every ordinary installation, so
        comparing them literally would report a move that has not happened.
        """
        self.sign_in('https://host.example/tc/')
        for spelling in ('https://host.example/tc',
                         'https://host.example/tc/',
                         'https://host.example/tc/index.php',
                         '  https://host.example/tc/  ',
                         'https://HOST.example/tc/',
                         'HTTPS://host.example/tc/'):
            with self.subTest(spelling=spelling):
                self.assertFalse(sync_client.address_changed(spelling))

    def test_a_different_path_on_the_same_host_is_a_change(self):
        """
        Paths are case-sensitive on most servers and two directories on one
        host are two installations, so this is not folded away.
        """
        self.sign_in('https://host.example/tc/')
        self.assertTrue(sync_client.address_changed('https://host.example/other/'))
        self.assertTrue(sync_client.address_changed('https://host.example/TC/'))

    def test_there_is_nothing_to_disagree_about_when_not_signed_in(self):
        self.assertFalse(sync_client.address_changed('https://anywhere.example/tc/'))

    def test_or_when_no_address_is_configured(self):
        self.sign_in('https://host.example/tc/')
        self.assertFalse(sync_client.address_changed(''))
        self.assertFalse(sync_client.address_changed('   '))
        self.assertFalse(sync_client.address_changed(None))


class TestTheSnapshotEndpoint(unittest.TestCase):
    """
    The one request that carries a whole document, and the only one whose
    body is not an envelope of its own.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._real = sync_client.config_dir
        sync_client.config_dir = lambda: self.tmp
        with patch('tt.sync_client.requests.post',
                   return_value=_Response({'ok': True, 'token': 'tok'})):
            sync_client.login('https://x.de/tc', 'frank', 'pw')

    def tearDown(self):
        sync_client.config_dir = self._real
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fetching_one_is_a_get_naming_the_action(self):
        with patch('tt.sync_client.requests.get',
                   return_value=_Response({'ok': True, 'seq': 12, 'document': {}})) as get:
            result = sync_client.get_snapshot()

        self.assertTrue(result['ok'])
        self.assertEqual(get.call_args.kwargs['params'], {'a': 'snapshot'})
        self.assertEqual(get.call_args.kwargs['headers']['X-TC-Token'], 'tok')

    def test_offering_one_puts_the_document_in_the_body_and_the_number_in_the_query(self):
        """
        The sequence number travels in the query string so the body is the
        document and nothing else - which is what lets the server store the
        bytes exactly as they arrived.
        """
        document = {'schema_version': 2, 'projects': [{'uid': 'a' * 16}]}
        with patch('tt.sync_client.requests.post',
                   return_value=_Response({'ok': True, 'snapshot_seq': 12})) as post:
            sync_client.put_snapshot(12, document)

        self.assertEqual(post.call_args.kwargs['params'], {'a': 'snapshot', 'seq': 12})
        self.assertEqual(json.loads(post.call_args.kwargs['data'].decode('utf-8')), document)

    def test_a_document_the_server_will_never_take_is_not_sent(self):
        """
        There is nothing the client can do to make it smaller, so discovering
        this as a 413 would mean rediscovering it on every attempt for the
        rest of the installation's life.
        """
        huge = {'projects': [{'uid': 'a' * 16, 'note': 'x' * sync_client.MAX_SNAPSHOT_BYTES}]}
        with patch('tt.sync_client.requests.post') as post:
            result = sync_client.put_snapshot(1, huge)

        self.assertFalse(result['ok'])
        self.assertEqual(result['error'], 'snapshot_too_large')
        post.assert_not_called()

    @staticmethod
    def heavy_document():
        """
        The shape of the document that outgrew the old 4 MiB: a few thousand
        tasks, a good many with long notes - 5.3 MB in all.
        """
        note = 'Lange Notiz aus einer E-Mail, mit Umlauten: äöü ß. ' * 40
        return {'schema_version': 2, 'next_id': 3000, '_deleted': [],
                'projects': [{'uid': '%016x' % p, 'main_project_name': 'Projekt %d' % p,
                              'status': 'open',
                              'tasks': [{'uid': '%016x' % (p * 1000 + t), 'id': p * 1000 + t,
                                         'task_name': 'Aufgabe %d' % t, 'note': note,
                                         'time_entries': []}
                                        for t in range(75)]}
                             for p in range(35)]}

    def test_a_document_of_a_few_megabytes_is_offered(self):
        """
        The real one that was refused for a week, until its account was full
        and every push was refused as well. Nothing about
        a document that size is wrong; the limit was.
        """
        document = self.heavy_document()
        size = len(json.dumps(document, ensure_ascii=False).encode('utf-8'))
        self.assertGreater(size, 5 * 1024 * 1024, "the test document is too small to mean anything")
        with patch('tt.sync_client.requests.post',
                   return_value=_Response({'ok': True, 'snapshot_seq': 12})) as post:
            result = sync_client.put_snapshot(12, document)
        self.assertTrue(result['ok'])
        post.assert_called_once()

    def test_the_client_s_limit_is_the_server_s(self):
        """
        Smaller here, and documents the server would take are never sent;
        larger, and each one is sent only to be refused.
        """
        oplog = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             '..', 'php-server', 'tc', 'lib', 'oplog.php')
        with open(oplog, encoding='utf-8') as handle:
            server = re.search(r'const TC_SNAPSHOT_MAX_BYTES\s*=\s*(\d+);', handle.read())
        self.assertIsNotNone(server, "the server's constant could not be found")
        self.assertEqual(int(server.group(1)), sync_client.MAX_SNAPSHOT_BYTES)

    def test_a_large_one_is_given_the_time_it_takes_to_send(self):
        """
        Every other call has DEADLINE, which a few megabytes over a slow
        uplink would run past: the snapshot would be cut off half-sent, every
        time, and the log never compacted.
        """
        document = self.heavy_document()
        size = len(json.dumps(document, ensure_ascii=False).encode('utf-8'))
        with patch('tt.sync_client._call_with_deadline',
                   side_effect=lambda send, deadline: _Response({'ok': True})) as call:
            sync_client.put_snapshot(12, document)
        allowed = call.call_args.args[1]
        self.assertGreaterEqual(allowed, sync_client.DEADLINE + size / sync_client.SNAPSHOT_MIN_RATE)
        # Half a megabit a second still gets it there.
        self.assertGreater(allowed, size / (512 * 1024 / 8))

    def test_and_fetching_one_the_time_the_largest_would_take(self):
        with patch('tt.sync_client._call_with_deadline',
                   side_effect=lambda send, deadline: _Response({'ok': True})) as call:
            sync_client.get_snapshot()
        self.assertEqual(call.call_args.args[1],
                         sync_client.DEADLINE + sync_client.MAX_SNAPSHOT_BYTES / sync_client.SNAPSHOT_MIN_RATE)

    def test_everything_else_keeps_the_ordinary_deadline(self):
        with patch('tt.sync_client._call_with_deadline',
                   side_effect=lambda send, deadline: _Response({'ok': True, 'head': 1})) as call:
            sync_client.head()
            sync_client.push(0, [])
        self.assertEqual([c.args[1] for c in call.call_args_list], [sync_client.DEADLINE] * 2)


class TestWhatIsMeasuredIsWhatIsSent(unittest.TestCase):
    """
    The budget in fit_batch exists because the server turns an over-long body
    into an empty one - accepted, acknowledged, and carrying nothing. That
    only holds if the bytes it counts are the bytes that go out.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._real = sync_client.config_dir
        sync_client.config_dir = lambda: self.tmp
        with patch('tt.sync_client.requests.post',
                   return_value=_Response({'ok': True, 'token': 'tok'})):
            sync_client.login('https://x.de/tc', 'frank', 'pw')

    def tearDown(self):
        sync_client.config_dir = self._real
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_umlauts_do_not_cost_more_on_the_wire_than_they_were_counted(self):
        """
        Escaped as \\uXXXX a German task name weighs three times what UTF-8
        charges for it, and fit_batch counts the UTF-8 figure. Counting one
        way and sending the other is exactly how a batch slips past the
        server's limit and is silently dropped.
        """
        ops = [{'op': 'task.set', 'lc': n, 'uid': '%016x' % n,
                'f': {'task_name': 'Übersicht Prüfstände Größenänderung ' * 20}}
               for n in range(1, 400)]
        batch = sync_client.fit_batch(ops)

        with patch('tt.sync_client.requests.post',
                   return_value=_Response({'ok': True})) as post:
            sync_client.push(0, batch)

        sent = len(post.call_args.kwargs['data'])
        counted = sum(len(json.dumps(op, ensure_ascii=False).encode('utf-8')) + 1
                      for op in batch)

        # Not exact, and it does not need to be: fit_batch allows one byte
        # per operation for the separator where the encoder writes two, and
        # the envelope around the list costs a few dozen more. What matters
        # is that the gap is that - a byte an operation - rather than a
        # factor, which is what escaping every umlaut would have made it.
        self.assertLessEqual(sent - counted, len(batch) + 100,
                             "the request is heavier than fit_batch was told")
        self.assertLess(sent, 1048576, "past what the server will read")

    def test_the_body_goes_out_as_utf8_bytes(self):
        """
        Handed over as a str, requests encodes it latin-1, which German task
        names are not - that is a UnicodeEncodeError on a perfectly ordinary
        entry rather than anything to do with size.
        """
        with patch('tt.sync_client.requests.post',
                   return_value=_Response({'ok': True})) as post:
            sync_client.push(0, [{'op': 'task.set', 'lc': 1, 'uid': 'a' * 16,
                                  'f': {'task_name': 'Prüfstände'}}])

        body = post.call_args.kwargs['data']
        self.assertIsInstance(body, bytes)
        self.assertIn('Prüfstände', body.decode('utf-8'))

class TestWhereAPasswordMayBeSent(unittest.TestCase):
    """
    login() refuses to put a password on the wire in the clear. The one
    exception is a loopback address, because that is not a wire - and it is
    the only way to sign the shipped client in to the real php-server/tc
    code, which PHP's built-in server can only serve over plain http.

    Relaxing this does not open a way in: index.php keeps its own, separate
    refusal, so a deployment reached over http still turns the request away.
    What it enables is the local check the sync work had been missing.
    """

    ERLAUBT = ('https://example.invalid/tc',
               'https://example.invalid/tc/index.php',
               'http://127.0.0.1:8000/tc',
               'http://localhost:8000/tc',
               'http://LOCALHOST:8000/tc',
               'http://[::1]:8000/tc')

    VERWEIGERT = ('http://example.invalid/tc',
                  # Hosts that merely begin with a loopback name. The match is
                  # exact for this reason: these are ordinary internet hosts.
                  'http://127.0.0.1.example.invalid/tc',
                  'http://localhost.example.invalid/tc',
                  # Not http at all.
                  'ftp://127.0.0.1/tc',
                  'file:///etc/passwd')

    def test_the_addresses_a_password_may_go_to(self):
        for url in self.ERLAUBT:
            with self.subTest(url=url):
                self.assertTrue(sync_client._transport_is_safe(url))

    def test_the_addresses_it_may_not(self):
        for url in self.VERWEIGERT:
            with self.subTest(url=url):
                self.assertFalse(sync_client._transport_is_safe(url))

    def test_login_says_so_rather_than_sending_anything(self):
        """The point of the guard: nothing leaves before it has decided."""
        with unittest.mock.patch('tt.sync_client.requests.post') as post:
            result = sync_client.login('http://example.invalid/tc', 'u', 'p')
        self.assertEqual(result, {'ok': False, 'error': 'https_required'})
        post.assert_not_called()

    def test_a_loopback_sign_in_is_attempted(self):
        """
        The other half: the guard must not simply be unreachable. Without
        this, replacing _transport_is_safe with `return False` would still
        pass every test above.
        """
        with unittest.mock.patch('tt.sync_client.requests.post') as post:
            post.return_value = _Response({'ok': True, 'token': 't',
                                           'device_uid': 'd'})
            with unittest.mock.patch('tt.sync_client._write_private'):
                sync_client.login('http://127.0.0.1:8000/tc', 'u', 'p')
        post.assert_called_once()

    def test_a_host_is_loopback_only_by_its_name_not_its_scheme(self):
        self.assertTrue(sync_client.is_loopback('http://127.0.0.1/tc'))
        self.assertTrue(sync_client.is_loopback('https://127.0.0.1/tc'))
        self.assertFalse(sync_client.is_loopback('https://example.invalid/tc'))


class TestARedirectIsNotFollowed(unittest.TestCase):
    """
    The token travels in X-TC-Token, which requests knows nothing about: it
    strips Authorization when a redirect changes host, and would carry this
    one onwards untouched - to plain http, if that is where the redirect
    pointed. So the redirect is refused rather than followed, and the address
    that caused it is reported as the thing to correct.
    """

    def _call(self, status):
        with unittest.mock.patch('tt.sync_client.requests.post') as post:
            post.return_value = _Response(None, status=status)
            result = sync_client._post('https://example.invalid/tc', 'login',
                                       {'username': 'u'}, token='secret-token')
        return result, post

    def test_every_redirect_is_reported_as_one(self):
        for status in (301, 302, 303, 307, 308):
            result, _post = self._call(status)
            self.assertEqual(result.get('error'), 'address_redirects',
                             '%d was not recognised as a redirect' % status)
            self.assertFalse(result.get('ok'))

    def test_requests_is_told_not_to_follow_it_itself(self):
        """
        The report above is the second line of defence. The first is that the
        request never goes to the redirect's target at all - by the time a
        response came back from there, the token would already have been sent.
        """
        _result, post = self._call(302)
        self.assertIs(post.call_args.kwargs.get('allow_redirects'), False)

    def test_the_same_holds_for_a_plain_get(self):
        with unittest.mock.patch('tt.sync_client.requests.get') as get:
            get.return_value = _Response(None, status=301)
            result = sync_client._post('https://example.invalid/tc', 'head',
                                       token='secret-token')
        self.assertEqual(result.get('error'), 'address_redirects')
        self.assertIs(get.call_args.kwargs.get('allow_redirects'), False)

    def test_an_ordinary_answer_is_untouched(self):
        with unittest.mock.patch('tt.sync_client.requests.post') as post:
            post.return_value = _Response({'ok': True, 'head': 7})
            result = sync_client._post('https://example.invalid/tc', 'push', {})
        self.assertEqual(result, {'ok': True, 'head': 7})

    def test_a_not_modified_is_not_mistaken_for_a_redirect(self):
        """304 is a 3xx that redirects nothing, and says so by carrying a body."""
        with unittest.mock.patch('tt.sync_client.requests.post') as post:
            post.return_value = _Response({'ok': True}, status=304)
            result = sync_client._post('https://example.invalid/tc', 'push', {})
        self.assertEqual(result, {'ok': True})

    def test_the_code_has_something_to_say_for_itself(self):
        from tt.sync_messages import sync_error_message
        message = sync_error_message('address_redirects')
        self.assertNotIn('Unexpected error', message)


if __name__ == '__main__':
    unittest.main()
