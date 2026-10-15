# Copyright (C) 2026 Serkan Bekdemir
# SPDX-License-Identifier: AGPL-3.0-only
"""Deployment checks use loopback HTTP and synthetic data only."""
import http.client
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import textwrap
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import people_workbench as workbench
from test_people_workbench import FakeImmich


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.state = workbench.AppState(workbench.Store(root / "state.sqlite3"), root / "reports")
        self.auth = workbench.AdminSession(self.state)
        for name, value in (("STATE", self.state), ("AUTH", self.auth)):
            patcher = mock.patch.object(workbench, name, value, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)

        class QuietHandler(workbench.Handler):
            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.server.request_policy = workbench.RequestPolicy("http://workbench.example:8766")

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, path, method="GET", body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        values = {"Host": "workbench.example:8766", **(headers or {})}
        if body is not None:
            values["Content-Type"] = "application/json"
        try:
            connection.request(method, path, json.dumps(body).encode() if body is not None else None, values)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def sign_in_synthetically(self):
        self.auth.cookie = "synthetic-workbench-session"
        self.auth.expires = time.monotonic() + 1000
        self.auth.checked = time.monotonic()
        fake = FakeImmich()
        self.state.client = fake
        self.auth.user_id = fake.current_admin()
        self.state.load()
        return fake, {"Cookie": "people_workbench_session=synthetic-workbench-session",
                      "Origin": "http://workbench.example:8766"}

    def test_health_has_no_private_state_or_immich_dependencies(self):
        with mock.patch.object(self.state, "summary", side_effect=AssertionError("private state read")), \
                mock.patch.object(self.state, "require_client", side_effect=AssertionError("Immich contact")):
            status, _, body = self.request("/healthz", headers={"Host": "127.0.0.1"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"ok": True})
        self.assertFalse(self.auth.cookie)

    def test_shutdown_rejects_new_handler_admission(self):
        server = workbench.WorkbenchServer(("127.0.0.1", 0), workbench.Handler)
        try:
            server.stop_admission()
            with mock.patch.object(server, "shutdown_request") as reject, \
                    mock.patch.object(threading.Thread, "start", side_effect=AssertionError("new handler")):
                request = object()
                server.process_request(request, ("127.0.0.1", 12345))
                reject.assert_called_once_with(request)
            self.assertEqual(server.active_handlers, 0)
            self.assertTrue(server.drain(time.monotonic()))
        finally:
            server.server_close()

    def test_remote_host_requires_configured_origin_and_authentication(self):
        self.assertEqual(self.request("/login")[0], 200)
        self.assertEqual(self.request("/api/state")[0], 401)
        self.assertEqual(self.request("/media/person/11111111-1111-4111-8111-111111111111")[0], 401)
        for host in ("other.example:8766", "workbench.example:8767", "workbench.example:8766/", "localhost:8766"):
            with self.subTest(host=host):
                self.assertEqual(self.request("/login", headers={"Host": host})[0], 403)
        self.server.request_policy = workbench.RequestPolicy()
        self.assertEqual(self.request("/login")[0], 403)
        self.assertEqual(self.request("/login", headers={"Host": "localhost:8766"})[0], 200)
        self.assertEqual(self.request("/login", headers={"Host": "[::1]:8766"})[0], 200)

    def test_forged_forwarded_headers_do_not_enable_https(self):
        self.server.request_policy = workbench.RequestPolicy("https://workbench.example")
        self.assertEqual(self.request("/login", headers={"Host": "workbench.example", "X-Forwarded-Proto": "https"})[0], 403)
        self.server.request_policy = workbench.RequestPolicy("https://workbench.example", ["192.0.2.4/32"])
        self.assertEqual(self.request("/login", headers={"Host": "workbench.example", "X-Forwarded-Proto": "https"})[0], 403)

    def test_trusted_https_proxy_login_sets_secure_cookie_and_rejects_cross_origin(self):
        self.server.request_policy = workbench.RequestPolicy("https://workbench.example", ["127.0.0.1/32"])
        headers = {"Host": "workbench.example", "Origin": "https://workbench.example", "X-Forwarded-Proto": "https"}
        for path, method in (("/api/login", "login"), ("/api/connect-key", "connect_key")):
            with self.subTest(path=path), \
                    mock.patch.object(self.auth, method, return_value=("synthetic", "synthetic-token")) as connect, \
                    mock.patch.object(self.state, "load"):
                status, response_headers, _ = self.request(path, "POST", {}, headers)
                self.assertEqual(status, 200)
                self.assertIn("; Secure", response_headers["Set-Cookie"])
                self.assertIn("HttpOnly", response_headers["Set-Cookie"])
                self.assertIn("SameSite=Strict", response_headers["Set-Cookie"])
                for invalid in ({"Origin": "https://evil.example"}, {"Origin": ""},
                                {"Sec-Fetch-Site": "cross-site"}, {"X-Forwarded-Proto": "https,http"},
                                {"X-Forwarded-Proto": "http"}, {"X-Forwarded-Host": "workbench.example", "Host": "localhost"}):
                    with self.subTest(headers=invalid):
                        self.assertEqual(self.request(path, "POST", {}, {**headers, **invalid})[0], 403)
                self.assertEqual(connect.call_count, 1)

    def test_key_login_ignores_untrusted_forwarded_scheme_on_http(self):
        headers = {"Origin": "http://workbench.example:8766", "X-Forwarded-Proto": "https"}
        with mock.patch.object(self.auth, "connect_key", return_value=("synthetic", "synthetic-token")), \
                mock.patch.object(self.state, "load"):
            status, response_headers, _ = self.request("/api/connect-key", "POST", {}, headers)
        self.assertEqual(status, 200)
        self.assertNotIn("; Secure", response_headers["Set-Cookie"])

    def test_new_runtime_assets_keep_authentication_boundary(self):
        status, _, body = self.request("/login-key.css")
        self.assertEqual(status, 200)
        self.assertTrue(body)
        self.assertEqual(self.request("/navigation.js")[0], 401)
        _, headers = self.sign_in_synthetically()
        status, _, body = self.request("/navigation.js", headers=headers)
        self.assertEqual(status, 200)
        self.assertIn(b"createNavigationDrawer", body)

    def test_duplicate_host_and_origin_headers_are_rejected(self):
        for duplicate in ("Host", "Origin"):
            with self.subTest(header=duplicate):
                connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
                try:
                    connection.putrequest("POST", "/api/login", skip_host=True)
                    connection.putheader("Host", "workbench.example:8766")
                    connection.putheader("Origin", "http://workbench.example:8766")
                    connection.putheader(duplicate, "workbench.example:8766" if duplicate == "Host" else "http://workbench.example:8766")
                    connection.putheader("Content-Length", "2")
                    connection.endheaders(b"{}")
                    response = connection.getresponse()
                    self.assertEqual(response.status, 403)
                    response.read()
                finally:
                    connection.close()

    def test_remote_queue_review_and_reload_write_only_after_explicit_sync(self):
        fake, headers = self.sign_in_synthetically()
        person_id = fake.rows[0]["id"]
        status, _, _ = self.request("/api/queue", "POST", {"personId": person_id, "operation": "rename", "name": "Synthetic Person"}, headers)
        self.assertEqual(status, 200)
        self.assertEqual(fake.calls, [])
        self.assertEqual(self.request("/api/pending", headers=headers)[0], 200)
        self.assertEqual(fake.calls, [])
        restored = workbench.AppState(workbench.Store(self.state.store.path), self.state.reports)
        self.assertEqual(restored.store.pending()[0]["name"], "Synthetic Person")
        self.assertIsNone(restored.client)
        self.assertFalse(workbench.AdminSession(restored).cookie)
        self.assertEqual(fake.calls, [])
        self.assertEqual(self.request("/api/sync", "POST", {}, headers)[0], 400)
        self.assertEqual(self.request("/api/sync", "POST", {"confirmation": "SYNC"}, {**headers, "Origin": "http://evil.example"})[0], 403)
        self.assertEqual(fake.calls, [])
        token = self.state.pending_data()["reviewToken"]
        self.assertEqual(self.request("/api/sync", "POST", {"confirmation": "SYNC", "reviewToken": token}, headers)[0], 200)
        self.assertEqual(fake.calls, [("update", person_id, {"name": "Synthetic Person"})])
        self.assertEqual(restored.store.pending(), [])
        self.assertEqual(len(list(self.state.reports.glob("sync-*.json"))), 1)

    def test_invalid_public_configuration_fails_before_creating_state(self):
        for origin in ("ftp://example.test", "https://example.test/people", "https://user:secret@example.test",
                       "https://example.test?", "https://example.test#", "https://example.test:bad", "https://example.test:0"):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                workbench.RequestPolicy(origin)
        with self.assertRaises(ValueError):
            workbench.RequestPolicy("https://example.test", ["not-a-network"])
        with self.assertRaises(ValueError):
            workbench.RequestPolicy("", ["127.0.0.1"])
        self.assertEqual(workbench.RequestPolicy("https://[::1]:443/").origin, "https://[::1]")
        path = Path(self.directory.name) / "should-not-exist"
        env = {key: value for key, value in os.environ.items() if not key.startswith("PEOPLE_WORKBENCH_")}
        result = subprocess.run([sys.executable, str(workbench.ROOT / "people_workbench.py"), "--host", "0.0.0.0",
                                 "--state-dir", str(path), "--no-browser"], capture_output=True, text=True, env=env, timeout=5)
        self.assertEqual(result.returncode, 2)
        self.assertIn("requires --public-url", result.stderr)
        self.assertFalse(path.exists())

    @unittest.skipIf(sys.platform == "win32", "POSIX SIGTERM behavior is checked on Linux")
    def test_sigterm_drains_active_sync_and_bounds_stalled_sync(self):
        for release_write in (True, False):
            with self.subTest(release_write=release_write), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                class DelayedUpstream(BaseHTTPRequestHandler):
                    def log_message(self, *_args):
                        pass

                    def do_POST(self):
                        self.rfile.read(int(self.headers["Content-Length"]))
                        (root / "upstream-write").touch()
                        while not (root / "release-write").exists():
                            time.sleep(.02)
                        self.send_response(200)
                        self.send_header("Content-Length", "0")
                        self.end_headers()

                upstream = ThreadingHTTPServer(("127.0.0.1", 0), DelayedUpstream)
                upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
                upstream_thread.start()
                runner = root / "runner.py"
                runner.write_text(textwrap.dedent('''
                    import sys, time, urllib.request, concurrent.futures
                    from pathlib import Path
                    sys.path.insert(0, sys.argv[1])
                    sys.path.insert(0, sys.argv[1] + '/tests')
                    import people_workbench as w
                    from test_people_workbench import FakeImmich
                    root = Path(sys.argv[2])
                    upstream_url = sys.argv[3]
                    class Delayed(FakeImmich):
                        def update_person(self, person_id, body):
                            request = urllib.request.Request(upstream_url, data=b'{}', method='POST')
                            def write():
                                with urllib.request.urlopen(request, timeout=10):
                                    pass
                            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                                pool.submit(write).result()
                            super().update_person(person_id, body)
                    original = w.AdminSession
                    class Session(original):
                        def __init__(self, state):
                            super().__init__(state)
                            self.cookie = 'synthetic-session'
                            self.expires = time.monotonic() + 1000
                            self.checked = time.monotonic()
                            state.client = Delayed()
                            self.user_id = state.client.current_admin()
                            state.load()
                            state.queue({'personId': state.client.rows[0]['id'],
                                         'operation': 'rename', 'name': 'Synthetic Drain'})
                    w.AdminSession = Session
                    sys.argv = ['workbench', '--port', '0', '--no-browser',
                                '--state-dir', str(root), '--shutdown-timeout', '2']
                    raise SystemExit(w.main())
                '''))
                env = {key: value for key, value in os.environ.items() if not key.startswith("PEOPLE_WORKBENCH_")}
                process = subprocess.Popen([sys.executable, "-u", str(runner), str(workbench.ROOT), directory,
                                            f"http://127.0.0.1:{upstream.server_port}/synthetic-write"],
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
                request_errors = []
                def sync_request(port):
                    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
                    headers = {"Cookie": "people_workbench_session=synthetic-session"}
                    try:
                        connection.request("GET", "/api/pending", headers=headers)
                        response = connection.getresponse()
                        token = json.loads(response.read())["reviewToken"]
                        headers.update({"Origin": f"http://127.0.0.1:{port}", "Content-Type": "application/json"})
                        connection.request("POST", "/api/sync", json.dumps({"confirmation": "SYNC", "reviewToken": token}), headers)
                        response = connection.getresponse()
                        self.assertEqual(response.status, 200)
                        response.read()
                    except Exception as exc:
                        request_errors.append(exc)
                    finally:
                        connection.close()
                try:
                    url = process.stdout.readline().strip().split(" ", 3)[-1]
                    self.assertTrue(url.startswith("http://127.0.0.1:"), url)
                    port = int(url.rstrip("/").rsplit(":", 1)[-1])
                    thread = threading.Thread(target=sync_request, args=(port,))
                    thread.start()
                    deadline = time.monotonic() + 5
                    while not (root / "upstream-write").exists() and time.monotonic() < deadline:
                        time.sleep(.02)
                    self.assertTrue((root / "upstream-write").exists(), request_errors)
                    started = time.monotonic()
                    process.send_signal(signal.SIGTERM)
                    time.sleep(.7)
                    self.assertIsNone(process.poll(), "active Sync was cut off before its deadline")
                    if release_write:
                        (root / "release-write").touch()
                    _, stderr = process.communicate(timeout=6)
                    thread.join(timeout=5)
                    self.assertFalse(thread.is_alive())
                    store = workbench.Store(root / "state.sqlite3")
                    if release_write:
                        self.assertEqual(process.returncode, 0, stderr)
                        self.assertEqual(request_errors, [])
                        self.assertEqual(store.pending(), [])
                        self.assertEqual(len(list((root / "reports").glob("sync-*.json"))), 1)
                    else:
                        self.assertEqual(process.returncode, 1, stderr)
                        self.assertIn("Shutdown deadline expired", stderr)
                        self.assertLess(time.monotonic() - started, 5)
                        self.assertEqual(len(store.pending()), 1)
                        self.assertEqual(list((root / "reports").glob("sync-*.json")), [])
                finally:
                    (root / "release-write").touch()
                    upstream.shutdown()
                    upstream.server_close()
                    upstream_thread.join(timeout=5)
                    if process.poll() is None:
                        process.kill()
                        process.communicate()

    @unittest.skipIf(sys.platform == "win32", "POSIX SIGTERM behavior is checked on Linux")
    def test_sigterm_stops_server_without_sync_and_preserves_pending(self):
        fake, _ = self.sign_in_synthetically()
        self.state.queue({"personId": fake.rows[0]["id"], "operation": "rename", "name": "Synthetic Restart"})
        env = {key: value for key, value in os.environ.items() if not key.startswith("PEOPLE_WORKBENCH_")}
        process = subprocess.Popen([sys.executable, "-u", str(workbench.ROOT / "people_workbench.py"), "--port", "0",
                                    "--state-dir", self.directory.name, "--no-browser"], stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, env=env)
        try:
            url = process.stdout.readline().strip().split(" ", 3)[-1]
            self.assertTrue(url.startswith("http://127.0.0.1:"), url)
            process.send_signal(signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0, stderr)
            self.assertNotIn("Synthetic Restart", stdout + stderr)
            self.assertEqual(self.state.store.pending()[0]["name"], "Synthetic Restart")
            self.assertEqual(fake.calls, [])
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()


if __name__ == "__main__":
    unittest.main()
