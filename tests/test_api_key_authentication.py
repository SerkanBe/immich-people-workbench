# Copyright (C) 2026 Serkan Bekdemir
# SPDX-License-Identifier: AGPL-3.0-only
"""Synthetic HTTP auth/capability tests. No live Immich or credentials."""
import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import people_workbench as w

OWNER = '11111111-1111-4111-8111-111111111111'
OTHER = '22222222-2222-4222-8222-222222222222'
PERSON = '33333333-3333-4333-8333-333333333333'
CANARY = 'synthetic-canary-key-never-reflect'


class Upstream(BaseHTTPRequestHandler):
    def log_message(self, *_args): pass

    def send(self, data, status=200):
        raw = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def serve(self):
        f = self.server.fixture
        path = self.path.split('?', 1)[0].removeprefix('/api')
        key = self.headers.get('x-api-key')
        token = self.headers.get('x-immich-user-token')
        f['reads'].append((self.command, path, bool(key), bool(token)))
        if path == '/server/version': return self.send({'major': 3, 'minor': 2, 'patch': 1})
        if path == '/auth/login':
            return self.send({'isAdmin': f['admin'], 'accessToken': 'synthetic-login-token', 'userId': OWNER})
        if key not in {CANARY, 'synthetic-rotated-key'} and token != 'synthetic-login-token':
            return self.send({'message': CANARY}, 401)
        if f['revoked'] and key: return self.send({'message': CANARY}, 401)
        if path == '/api-keys/me':
            if f['introspection_status'] != 200: return self.send({'message': CANARY}, f['introspection_status'])
            result = {'id': OTHER, 'name': 'Synthetic key', 'createdAt': '2026-01-01T00:00:00Z',
                      'updatedAt': '2026-01-01T00:00:00Z', 'permissions': list(f['permissions'])}
            if f['metadata_override'] is not None: result = f['metadata_override']
            if f['block_check']:
                f['check_started'].set()
                f['release_check'].wait(5)
            return self.send(result)
        if path == '/users/me': return self.send({'id': f['owner'], 'isAdmin': f['admin'] if token else False, 'name': f['name']})
        if path == '/people' and self.command == 'GET': return self.send({'people': [], 'hasNextPage': False})
        if path.endswith('/statistics'): return self.send({'assets': 0})
        if self.command == 'PUT' and path.startswith('/people/'):
            if f['block_write']:
                f['write_started'].set()
                f['release_write'].wait(5)
            f['writes'].append(path)
            return self.send({})
        return self.send({'message': CANARY}, 404)

    do_GET = serve
    do_POST = serve
    do_PUT = serve


class QuietHandler(w.Handler):
    def log_message(self, *_args): pass


class ApiKeyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = w.AppState(w.Store(Path(self.tmp.name) / 'state.sqlite3'), Path(self.tmp.name) / 'reports')
        self.auth = w.AdminSession(self.state)
        self.f = {'permissions': list(w.REQUIRED_PERMISSIONS), 'owner': OWNER, 'admin': True, 'name': 'Synthetic account',
                  'revoked': False, 'metadata_override': None, 'introspection_status': 200, 'reads': [], 'writes': [],
                  'block_check': False, 'check_started': threading.Event(), 'release_check': threading.Event(),
                  'block_write': False, 'write_started': threading.Event(), 'release_write': threading.Event()}
        self.up = ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
        self.up.fixture = self.f
        self.up_thread = threading.Thread(target=self.up.serve_forever, daemon=True)
        self.up_thread.start()
        self.url = f'http://127.0.0.1:{self.up.server_port}'
        self.patch = mock.patch.multiple(w, STATE=self.state, AUTH=self.auth, create=True)
        self.patch.start()
        self.load_patch = mock.patch.object(self.state, 'load')
        self.load_patch.start()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), QuietHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.f['release_check'].set(); self.f['release_write'].set()
        self.server.shutdown(); self.server.server_close(); self.thread.join(5)
        self.up.shutdown(); self.up.server_close(); self.up_thread.join(5)
        self.load_patch.stop(); self.patch.stop(); self.tmp.cleanup()

    def post(self, path, payload, cookie='', origin=True):
        headers = {'Content-Type': 'application/json'}
        if origin: headers['Origin'] = self.base
        if cookie: headers['Cookie'] = w.SESSION_COOKIE + '=' + cookie
        req = urllib.request.Request(self.base + path, json.dumps(payload).encode(), headers, method='POST')
        try:
            response = urllib.request.urlopen(req, timeout=8)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response), response.headers

    def connect(self, key=CANARY, **payload):
        return self.post('/api/connect-key', {'url': self.url, 'apiKey': key, **payload})

    def seed_queue(self):
        self.state.people = {PERSON: {'id': PERSON, 'name': '', 'isHidden': False}}
        status, result, _ = self.post('/api/queue', {'personId': PERSON, 'operation': 'hide'}, self.auth.cookie)
        self.assertEqual(status, 200, result)
        self.assertFalse(self.f['writes'])
        return self.state.pending_data()['reviewToken']

    def sync(self, token):
        return self.post('/api/sync', {'confirmation': 'SYNC', 'reviewToken': token}, self.auth.cookie)

    def test_ordinary_owner_all_ten_scopes_and_header(self):
        status, data, headers = self.connect()
        self.assertEqual((status, data['version']), (200, '3.2.1'))
        self.assertIn('HttpOnly', headers['Set-Cookie']); self.assertIn('SameSite=Strict', headers['Set-Cookie'])
        connection = self.state.summary()['connection']
        self.assertFalse(connection['isAdmin']); self.assertEqual(connection['authMode'], 'apiKey')
        self.assertEqual(set(connection['grantedPermissions']), set(w.REQUIRED_PERMISSIONS))
        self.assertNotIn(CANARY, json.dumps(self.state.summary()))
        self.assertTrue(all(key and not token for method, path, key, token in self.f['reads'] if path != '/server/version'))
        self.assertFalse(self.f['writes'])

    def test_each_missing_scope_is_rejected_before_owner_binding(self):
        for permission in w.REQUIRED_PERMISSIONS:
            self.f['permissions'] = [p for p in w.REQUIRED_PERMISSIONS if p != permission]
            status, result, _ = self.connect()
            self.assertEqual(status, 403); self.assertEqual(result['missingPermissions'], [permission])
            self.assertEqual(self.state.store.setting('owner'), ''); self.assertIsNone(self.state.client)
        self.assertFalse(self.f['writes'])

    def test_all_and_extra_permissions_warn(self):
        for permissions in [['all'], [*w.REQUIRED_PERMISSIONS, 'asset.delete']]:
            self.f['permissions'] = permissions
            self.assertEqual(self.connect()[0], 200)
            self.assertTrue(self.state.summary()['connection']['overbroad'])

    def test_malformed_introspection_and_unknown_permissions_fail(self):
        for value in [None, [], {}, {'id': OTHER, 'permissions': []}, {'id': OTHER, 'permissions': [*w.REQUIRED_PERMISSIONS, CANARY]}, {'id': 1, 'permissions': list(w.REQUIRED_PERMISSIONS)}]:
            self.f['metadata_override'] = value if value is not None else []
            status, result, _ = self.connect()
            self.assertEqual(status, 403); self.assertEqual(result['code'], 'unsupported_introspection')
            self.assertNotIn(CANARY, json.dumps(result)); self.assertEqual(self.state.store.setting('owner'), '')

    def test_optional_metadata_owner_mismatch(self):
        self.f['metadata_override'] = {'id': OTHER, 'permissions': list(w.REQUIRED_PERMISSIONS), 'userId': OTHER}
        self.assertEqual(self.connect()[1]['code'], 'invalid_key')
        self.assertEqual(self.state.store.setting('owner'), '')

    def test_invalid_revoked_and_unavailable_errors_redact_reflection(self):
        self.assertEqual(self.connect('synthetic-invalid')[1]['code'], 'invalid_key')
        self.f['revoked'] = True
        self.assertEqual(self.connect()[1]['code'], 'invalid_key')
        self.f['revoked'] = False
        for status, code in [(404, 'unsupported_introspection'), (503, 'unavailable')]:
            self.f['introspection_status'] = status
            _, result, _ = self.connect()
            self.assertEqual(result['code'], code); self.assertNotIn(CANARY, json.dumps(result))
        self.assertEqual(self.state.store.setting('owner'), '')

    def test_same_owner_rotation_preserves_queue_and_invalidates_cookie(self):
        self.connect(); old = self.auth.cookie; self.seed_queue()
        self.state.store.save_group_draft(OTHER, PERSON, 'Synthetic draft', None)
        self.state.store.set_face_reviewed(OTHER, PERSON, True)
        self.state.store.skip(PERSON)
        self.assertEqual(self.connect('synthetic-rotated-key')[0], 200)
        self.assertNotEqual(old, self.auth.cookie); self.assertFalse(self.auth.authorized(old))
        self.assertEqual(len(self.state.store.pending()), 1)
        self.assertEqual(json.loads(self.state.store.setting('owner')), {'url': self.url + '/api', 'userId': OWNER})
        self.assertEqual(self.state.store.group_drafts(OTHER)[0]['name'], 'Synthetic draft')
        self.assertEqual(self.state.store.face_review_counts()[PERSON], 1)
        self.assertEqual(self.state.store.skips()[PERSON], 1)

    def test_invalid_replacement_keeps_active_connection_and_work(self):
        self.connect(); self.seed_queue()
        old, client = self.auth.cookie, self.state.client
        for key, permissions in [('synthetic-invalid', list(w.REQUIRED_PERMISSIONS)), (CANARY, ['person.read'])]:
            self.f['permissions'] = permissions
            self.assertEqual(self.connect(key)[0], 403)
            self.assertEqual(self.auth.cookie, old); self.assertIs(self.state.client, client)
            self.assertEqual(len(self.state.store.pending()), 1)

    def test_known_capability_snapshot_guards_queue_boundary(self):
        self.connect()
        self.state.people = {PERSON: {'id': PERSON, 'name': '', 'isHidden': False}}
        self.state.client.connection_metadata['grantedPermissions'].remove('face.update')
        status, result, _ = self.post('/api/queue', {'personId': PERSON, 'operation': 'hide'}, self.auth.cookie)
        self.assertEqual(status, 403); self.assertEqual(result['missingPermissions'], ['face.update'])
        self.assertFalse(self.state.store.pending()); self.assertFalse(self.f['writes'])

    def test_candidate_owner_and_server_mismatch_are_atomic(self):
        self.connect(); old = self.auth.cookie; client = self.state.client; self.seed_queue()
        self.f['owner'] = OTHER
        self.assertEqual(self.connect()[1]['code'], 'owner_mismatch')
        self.f['owner'] = OWNER
        self.assertEqual(self.connect(url=self.url + '/api/')[0], 200)
        old = self.auth.cookie; client = self.state.client
        self.assertEqual(self.connect(url=self.url.replace('127.0.0.1', 'localhost'))[1]['code'], 'owner_mismatch')
        self.assertEqual(old, self.auth.cookie); self.assertIs(client, self.state.client)
        self.assertEqual(len(self.state.store.pending()), 1)

    def test_invalid_input_secrets_never_echo(self):
        for value in ['', None, 123, {}, 'synthetic\nheader']:
            self.assertEqual(self.connect(value)[1]['code'], 'invalid_input')
        for value in ['https://example.test/?key=' + CANARY, 'https://user:' + CANARY + '@example.test']:
            _, result, _ = self.connect(url=value)
            self.assertNotIn(CANARY, json.dumps(result)); self.assertEqual(result['code'], 'invalid_input')

    def test_scope_reduction_sync_zero_writes_retained_queue(self):
        self.connect(); token = self.seed_queue()
        self.f['permissions'].remove('face.update')
        status, result, _ = self.sync(token)
        self.assertEqual(status, 403); self.assertEqual(result['missingPermissions'], ['face.update'])
        self.assertFalse(self.f['writes']); self.assertEqual(len(self.state.store.pending()), 1)
        self.assertFalse(self.auth.cookie); self.assertIsNone(self.state.client)
        self.f['permissions'] = list(w.REQUIRED_PERMISSIONS)
        self.connect('synthetic-rotated-key')
        self.assertEqual(self.sync(self.state.pending_data()['reviewToken'])[0], 200)
        self.assertEqual(len(self.f['writes']), 1)

    def test_changed_owner_before_sync_blocks_writes_and_retains_queue(self):
        self.connect(); token = self.seed_queue(); self.f['owner'] = OTHER
        self.assertEqual(self.sync(token)[1]['code'], 'owner_mismatch')
        self.assertFalse(self.f['writes']); self.assertEqual(len(self.state.store.pending()), 1)
        self.assertEqual(json.loads(self.state.store.setting('owner'))['userId'], OWNER)

    def test_revoked_before_sync_is_not_retried(self):
        self.connect(); token = self.seed_queue(); self.f['revoked'] = True
        self.assertEqual(self.sync(token)[1]['code'], 'invalid_key')
        self.assertFalse(self.f['writes']); self.assertEqual(len(self.state.store.pending()), 1)

    def test_stale_review_blocks_writes_with_valid_key(self):
        self.connect(); token = self.seed_queue(); self.state.store.set_included(PERSON, False)
        self.assertEqual(self.sync(token)[0], 409); self.assertFalse(self.f['writes'])

    def test_logout_ttl_restart_keep_persistent_work(self):
        self.connect(); self.seed_queue(); cookie = self.auth.cookie
        self.assertEqual(self.post('/api/logout', {}, cookie)[0], 200)
        self.assertFalse(self.auth.authorized(cookie)); self.assertEqual(len(self.state.store.pending()), 1)
        self.connect(); cookie = self.auth.cookie; self.auth.expires = time.monotonic() - 1
        self.assertFalse(self.auth.authorized(cookie)); self.assertEqual(len(self.state.store.pending()), 1)
        restarted = w.AdminSession(w.AppState(w.Store(Path(self.tmp.name)/'state.sqlite3'), Path(self.tmp.name)/'reports'))
        self.assertFalse(restarted.cookie); self.assertEqual(len(restarted.state.store.pending()), 1)
        raw = (Path(self.tmp.name)/'state.sqlite3').read_bytes()
        self.assertNotIn(CANARY.encode(), raw)

    def test_delayed_old_epoch_check_cannot_clear_new_session(self):
        self.connect(); old = self.auth.cookie; self.auth.checked = 0
        self.f['permissions'].remove('face.update')
        self.f['block_check'] = True
        result = []
        worker = threading.Thread(target=lambda: result.append(self.auth.authorized(old)))
        worker.start(); self.assertTrue(self.f['check_started'].wait(3))
        self.f['permissions'] = list(w.REQUIRED_PERMISSIONS); self.f['block_check'] = False
        self.assertEqual(self.connect('synthetic-rotated-key')[0], 200)
        new = self.auth.cookie
        self.f['release_check'].set(); worker.join(5)
        self.assertEqual(result, [False]); self.assertEqual(self.auth.cookie, new)
        self.assertTrue(self.auth.authorized(new))

    def test_old_background_load_cannot_publish_after_rotation_or_logout(self):
        for action in ['rotate', 'logout']:
            self.connect()
            client = self.state.client
            started, release = threading.Event(), threading.Event()
            def old_people():
                started.set(); release.wait(5)
                return [{'id': PERSON, 'name': 'Synthetic old response', 'isHidden': False}]
            with mock.patch.object(client, 'all_people', side_effect=old_people):
                worker = threading.Thread(target=lambda: w.AppState.load(self.state))
                worker.start(); self.assertTrue(started.wait(3))
                if action == 'rotate': self.connect('synthetic-rotated-key')
                else: self.auth.logout(self.auth.cookie)
                release.set(); worker.join(5)
            self.assertFalse(self.state.people)
            self.assertNotEqual(self.state.phase, 'ready')

    def test_session_expiry_during_fresh_sync_reads_blocks_writes(self):
        self.connect(); token = self.seed_queue()
        self.f['block_check'] = True
        results = []
        worker = threading.Thread(target=lambda: results.append(self.sync(token)))
        worker.start(); self.assertTrue(self.f['check_started'].wait(3))
        # Advancing the monotonic clock avoids a slow real eight-hour wait.
        expired_time = self.auth.expires + 1
        with mock.patch.object(w.time, 'monotonic', return_value=expired_time):
            self.f['release_check'].set(); worker.join(5)
        self.assertEqual(results[0][0], 403)
        self.assertEqual(results[0][1]['code'], 'session_expired')
        self.assertFalse(self.f['writes']); self.assertEqual(len(self.state.store.pending()), 1)

    def test_connect_logout_busy_while_fresh_sync_check_and_write(self):
        self.connect(); token = self.seed_queue(); cookie = self.auth.cookie
        self.f['block_check'] = True; self.f['block_write'] = True
        results = []
        worker = threading.Thread(target=lambda: results.append(self.sync(token)))
        worker.start(); self.assertTrue(self.f['check_started'].wait(3))
        self.assertEqual(self.connect('synthetic-rotated-key')[0], 409)
        self.assertEqual(self.post('/api/logout', {}, cookie)[0], 409)
        self.f['release_check'].set(); self.assertTrue(self.f['write_started'].wait(3))
        self.assertEqual(self.connect('synthetic-rotated-key')[0], 409)
        self.assertEqual(self.post('/api/logout', {}, cookie)[0], 409)
        self.f['release_write'].set(); worker.join(5)
        self.assertEqual(results[0][0], 200); self.assertEqual(cookie, self.auth.cookie)

    def test_password_admin_gate_and_mode_remain(self):
        payload = {'url': self.url, 'email': 'admin@example.test', 'password': 'synthetic-password'}
        self.f['admin'] = False
        self.assertEqual(self.post('/api/login', payload)[0], 403)
        self.f['admin'] = True
        self.assertEqual(self.post('/api/login', payload)[0], 200)
        self.assertEqual(self.state.summary()['connection']['authMode'], 'login')
        token = self.seed_queue(); self.f['admin'] = False
        self.assertEqual(self.sync(token)[0], 403); self.assertFalse(self.f['writes'])

    def test_origin_public_assets_and_protected_media(self):
        self.assertEqual(self.post('/api/connect-key', {'url': self.url, 'apiKey': CANARY}, origin=False)[0], 403)
        with urllib.request.urlopen(self.base+'/login-key.css') as response: self.assertEqual(response.status, 200)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(self.base+'/media/person/'+PERSON)
        with caught.exception:
            self.assertEqual(caught.exception.code, 401)

    def test_metadata_identity_cannot_reflect_key(self):
        self.f['name'] = CANARY
        self.connect()
        self.assertNotIn(CANARY, json.dumps(self.state.summary()))

    def test_redirects_never_forward_credentials(self):
        class Redirect(BaseHTTPRequestHandler):
            def log_message(self, *_args): pass
            def do_GET(self):
                self.send_response(302); self.send_header('Location', self.server.target + '/api/users/me'); self.end_headers()
        server = ThreadingHTTPServer(('127.0.0.1', 0), Redirect); server.target = self.url
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            with self.assertRaises(w.UpstreamError):
                w.Immich(f'http://127.0.0.1:{server.server_port}', CANARY).json('GET', '/users/me')
            self.assertFalse(self.f['reads'])
        finally: server.shutdown(); server.server_close(); thread.join(5)


if __name__ == '__main__': unittest.main()
