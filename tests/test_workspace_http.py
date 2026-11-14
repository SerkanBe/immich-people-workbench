"""Real loopback source/portable HTTP, using synthetic media/accounts only."""
import base64
import copy
import http.client
import io
import json
import threading
import time
import unittest
from unittest import mock
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer

import people_workbench as app
import workspace_state as ws
from test_offline_workspaces import OfflineFixture, QuietHandler, P1, P3, OWNER


class WorkspaceHTTPTests(OfflineFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.prepare()

    def tearDown(self):
        self.state.transfers.close()
        if self.state._packages is not None:
            self.state._packages.close()
        super().tearDown()

    def serve(self, action, state=None, *, offline=False):
        state = state or self.state
        auth = app.AdminSession(state)
        if offline:
            cookie = auth.open_offline()
        else:
            cookie = "synthetic-session"
            auth.cookie, auth.mode, auth.user_id = cookie, "login", P3
            auth.expires, auth.checked = time.monotonic() + 600, time.monotonic()
        identity = {"authMode": "login", "userId": P3, "isAdmin": True}
        server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        origin = "http://127.0.0.1:" + str(server.server_port)
        def request(path, value=None, *, headers=None, authenticate=True):
            hdr = {"Origin": origin, "Content-Type": "application/json"}
            if authenticate:
                hdr["Cookie"] = app.SESSION_COOKIE + "=" + cookie
            hdr.update(headers or {})
            data = None if value is None else json.dumps(value).encode()
            req = urllib.request.Request(origin + path, data, hdr)
            try:
                response = urllib.request.urlopen(req, timeout=10)
            except urllib.error.HTTPError as exc:
                response = exc
            with response:
                body = response.read()
                return response.status, json.loads(body) if response.headers.get_content_type() == "application/json" else body, dict(response.headers)
        with mock.patch.object(app, "STATE", state, create=True), mock.patch.object(app, "AUTH", auth, create=True), mock.patch.object(auth, "_verify", return_value=identity):
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                return action(request, origin, cookie)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(5)

    def test_download_package_offline_edits_return_import_and_explicit_sync(self):
        def source_export(request, *_):
            status, result, _ = request("/api/offline/packages", {"name": "Synthetic HTTP trip"})
            self.assertEqual(status, 202)
            self.state._packages.worker.join(10)
            status, listing, _ = request("/api/offline/packages")
            self.assertEqual(status, 200)
            self.assertTrue(listing["workspaces"][0]["downloadReady"], listing)
            status, archive, headers = request("/api/offline/packages/" + result["id"] + "/download")
            self.assertEqual(status, 200)
            self.assertEqual(headers["Cache-Control"], "no-store")
            return archive
        archive = self.serve(source_export)
        self.assertEqual(self.client.writes, [])
        root = self.directory / "extracted"
        # Trusted self-produced fixture only; the return importer never extracts.
        with zipfile.ZipFile(io.BytesIO(archive)) as package:
            package.extractall(root)
        local = app.AppState(app.Store(root / ".people-workbench" / "state.sqlite3"), root / "reports")
        local.source_root = root
        def offline_edit(request, *_):
            status, summary, _ = request("/api/state")
            self.assertEqual(status, 200)
            self.assertTrue(summary["portable"])
            self.assertEqual(request("/api/queue", {"personId": P1, "operation": "rename", "name": "HTTP returned name"})[0], 200)
            for route in ("/api/login", "/api/connect-key", "/api/sync"):
                status, denied, _ = request(route, {})
                self.assertEqual(status, 403)
                self.assertEqual(denied["code"], "portable_restricted")
            status, body, _ = request("/api/offline/results")
            self.assertEqual(status, 200)
            return body
        returned = self.serve(offline_edit, local, offline=True)
        self.assertEqual(self.client.writes, [])
        def import_and_sync(request, *_):
            status, preview, _ = request("/api/offline/import/preview", {"zipBase64": base64.b64encode(returned).decode()})
            self.assertEqual(status, 200, preview)
            self.assertEqual(preview["counts"]["ready"], 1)
            status, applied, _ = request("/api/offline/import/apply", {"token": preview["token"], "ids": preview["readyIds"], "confirmation": "IMPORT_LOCAL"})
            self.assertEqual(status, 200, applied)
            self.assertEqual(self.client.writes, [])
            snapshot_id = applied["snapshot"]["id"]
            self.assertEqual(request("/api/offline/state-snapshots/" + snapshot_id + "/download")[0], 200)
            _, pending, _ = request("/api/pending")
            self.assertEqual(self.client.writes, [])
            status, outcome, _ = request("/api/sync", {"confirmation": "SYNC", "reviewToken": pending["reviewToken"]})
            self.assertEqual(status, 200, outcome)
            _, duplicate, _ = request("/api/offline/import/preview", {"zipBase64": base64.b64encode(returned).decode()})
            self.assertEqual(duplicate["counts"]["applied"], 1)
            self.assertEqual(duplicate["lastImport"]["snapshot"]["id"], snapshot_id)
            _, listing, _ = request("/api/offline/packages")
            self.assertTrue(listing["workspaces"][0]["downloadReady"])
            self.assertEqual(listing["workspaces"][0]["importSummary"]["importedGroups"], 1)
        self.serve(import_and_sync)
        self.assertEqual(self.client.writes, [("person", P1, {"name": "HTTP returned name"})])

    def test_auth_origin_route_caps_and_duplicate_framing(self):
        def check(request, origin, cookie):
            self.assertEqual(request("/api/offline/packages", authenticate=False)[0], 401)
            self.assertEqual(request("/api/offline/import/preview", {}, headers={"Origin": "https://foreign.example"})[0], 403)
            status, error, _ = request("/api/offline/import/preview", {}, headers={"Content-Length": str(24 * 1024 ** 2 + 1)})
            self.assertEqual((status, error["code"]), (413, "resource_limit"))
            status, error, _ = request("/api/skip", {"personId": P1}, headers={"Content-Length": "1000001"})
            self.assertEqual((status, error["code"]), (413, "resource_limit"))
            address = urllib.parse.urlsplit(origin)
            connection = http.client.HTTPConnection(address.hostname, address.port, timeout=5)
            connection.putrequest("POST", "/api/offline/import/preview")
            for name, value in (("Origin", origin), ("Cookie", app.SESSION_COOKIE + "=" + cookie), ("Content-Length", "2"), ("Content-Length", "2")):
                connection.putheader(name, value)
            connection.endheaders(b"{}")
            response = connection.getresponse()
            self.assertEqual(response.status, 400)
            response.read()
            connection.close()
            self.assertEqual(self.client.writes, [])
        self.serve(check)

    def test_damaged_marker_blocks_login_before_client_construction(self):
        root = self.directory / "damaged-package"
        root.mkdir()
        (root / "portable-workspace.json").write_text("{broken", encoding="utf-8")
        self.state.source_root = root
        def check(request, *_):
            with mock.patch.object(app, "Immich") as upstream:
                status, state, _ = request("/api/offline/status", authenticate=False)
                self.assertEqual(status, 200)
                self.assertTrue(state["portable"])
                self.assertFalse(state["ready"])
                self.assertNotIn("storagePath", state)
                for route in ("/api/login", "/api/connect-key"):
                    status, denied, _ = request(route, {}, authenticate=False)
                    self.assertEqual((status, denied["code"]), (403, "portable_invalid"))
                self.assertFalse(upstream.called)
        self.serve(check)


if __name__ == "__main__":
    unittest.main()
