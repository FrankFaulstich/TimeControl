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


if __name__ == '__main__':
    unittest.main()
