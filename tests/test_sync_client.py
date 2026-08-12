import json
import os
import shutil
import sys
import tempfile
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


if __name__ == '__main__':
    unittest.main()
