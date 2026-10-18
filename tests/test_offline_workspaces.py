# Copyright (C) 2026 Serkan Bekdemir
# SPDX-License-Identifier: AGPL-3.0-only
"""Synthetic portable snapshots, local sessions and fail-closed reconciliation."""
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import people_workbench as app
from offline_workspace import OfflineClient, OfflineConflict, OfflineError, OfflineWorkspace, atomic_json


P1 = "11111111-1111-4111-8111-111111111111"
P2 = "22222222-2222-4222-8222-222222222222"
P3 = "33333333-3333-4333-8333-333333333333"
NEW = "99999999-9999-4999-8999-999999999999"
A1 = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
A2 = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
VIDEO = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
F1 = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
F2 = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
OWNER = {"url": "https://synthetic.example/api", "userId": P3}


class SyntheticImmich:
    session = False
    base = OWNER["url"]
    server_version = "3.2.1"
    credential = "synthetic-secret-never-persist"
    connection_metadata = {"authMode": "apiKey", "userId": P3, "grantedPermissions": ["all"]}

    def __init__(self):
        self.permissions = ["all"]
        self.rows = {
            pid: {"id": pid, "name": "Synthetic Named" if pid == P3 else "", "isHidden": False,
                  "updatedAt": "snapshot-v1", "assetCount": 9 if pid == P3 else 2}
            for pid in (P1, P2, P3)}
        self.assets = {aid: {"id": aid, "type": "IMAGE", "originalFileName": "synthetic.jpg",
                             "exifInfo": {"fileSizeInByte": 8}} for aid in (A1, A2)}
        self.assets[VIDEO] = {"id": VIDEO, "type": "VIDEO"}
        self.face_rows = {
            A1: [{"id": F1, "person": {"id": P1}, "imageWidth": 100, "imageHeight": 80},
                 {"id": F2, "person": {"id": P3}, "imageWidth": 100, "imageHeight": 80}],
            A2: [{"id": A2, "person": {"id": P2}, "imageWidth": 100, "imageHeight": 80}]}
        self.page_calls, self.download_calls, self.writes = [], [], []
        self.fail_face = False
        self.cancel_on_download = False

    def all_people(self):
        return copy.deepcopy(list(self.rows.values()))

    def person(self, pid):
        return copy.deepcopy(self.rows[pid])

    def statistics(self, pid):
        return self.rows[pid]["assetCount"]

    def person_assets_page(self, pid, page=1, size=50, *, group_photos=False):
        self.page_calls.append((pid, page, group_photos))
        ids = ([A1] if page == 1 else [A2]) if pid == P1 else [A2, VIDEO]
        return [self.asset(aid) for aid in ids], pid == P1 and page == 1, 2

    def asset(self, aid):
        return copy.deepcopy(self.assets[aid])

    def faces(self, aid):
        if self.fail_face:
            raise app.ToolError("synthetic face metadata unavailable")
        return copy.deepcopy(self.face_rows.get(aid, []))

    def json(self, method, path, payload=None):
        if path == "/api-keys/me":
            return {"permissions": self.permissions}
        raise AssertionError((method, path))

    def request(self, method, path, payload=None):
        return b"preview", "image/jpeg"

    def download_original(self, aid, path, cancel, max_bytes, reserve_bytes):
        self.download_calls.append(aid)
        body = b"original"
        path.write_bytes(body)
        if self.cancel_on_download:
            cancel.set()
        return len(body), "image/jpeg", hashlib.sha256(body).hexdigest()

    def update_person(self, pid, payload):
        self.writes.append(("person", pid, payload))
        self.rows[pid].update(payload)
        self.rows[pid]["updatedAt"] = "own-write"

    def create_person(self):
        self.rows[NEW] = {"id": NEW, "name": "", "isHidden": False, "assetCount": 0}
        self.writes.append(("create", NEW))
        return self.rows[NEW]

    def reassign_face(self, pid, fid):
        for faces in self.face_rows.values():
            for face in faces:
                if face["id"] == fid:
                    old = face["person"]["id"]
                    face["person"] = {"id": pid}
                    self.rows[old]["assetCount"] -= 1
                    self.rows[pid]["assetCount"] += 1
        self.writes.append(("face", pid, fid))


class OfflineFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.state = app.AppState(app.Store(self.directory / "state.sqlite3"), self.directory / "reports")
        self.state.store.set_setting("owner", json.dumps(OWNER, sort_keys=True))
        self.client = SyntheticImmich()
        self.state.client = self.client
        self.state.people = copy.deepcopy(self.client.rows)
        self.manager = self.state.offline

    def tearDown(self):
        if self.manager.worker:
            self.manager.cancel()
            self.manager.worker.join(5)
        self.temp.cleanup()

    def wait_job(self):
        self.manager.worker.join(5)
        self.assertFalse(self.manager.worker.is_alive())
        return self.manager.job["phase"]

    def prepare(self):
        self.manager.start_plan(self.client)
        self.assertEqual(self.wait_job(), "planned")
        self.manager.start_download(self.client, self.manager.job["planId"])
        self.assertEqual(self.wait_job(), "ready")
        return self.manager.active(verify=True)

    def open_offline(self):
        self.prepare()
        auth = app.AdminSession(self.state)
        cookie = auth.open_offline()
        return auth, cookie


class OfflineTests(OfflineFixture, unittest.TestCase):

    def test_all_source_pages_deduplicate_stills_and_no_writes(self):
        snapshot = self.prepare()
        self.assertEqual(set(snapshot["assets"]), {A1, A2})
        self.assertIn((P1, 2, True), self.client.page_calls)
        self.assertEqual(len(self.client.download_calls), 2)
        self.assertEqual(self.manager.job["estimatedBytes"], 16)
        self.assertFalse(self.client.writes)
        for file in self.manager.root.rglob("*.json"):
            self.assertNotIn(self.client.credential, file.read_text(encoding="utf-8"))

    def test_extra_download_scope_only_preparation_and_rechecked_download(self):
        self.client.permissions = list(app.REQUIRED_PERMISSIONS)
        with self.assertRaisesRegex(OfflineError, "asset.download"):
            self.manager.start_plan(self.client)
        self.client.permissions.append("asset.download")
        self.manager.start_plan(self.client)
        self.assertEqual(self.wait_job(), "planned")
        self.client.permissions.remove("asset.download")
        with self.assertRaisesRegex(OfflineError, "asset.download"):
            self.manager.start_download(self.client, self.manager.job["planId"])
        self.assertNotIn("asset.download", app.REQUIRED_PERMISSIONS)

    def test_preparation_failure_preserves_previous_ready_generation(self):
        previous = self.prepare()["id"]
        self.client.fail_face = True
        self.manager.start_plan(self.client)
        self.assertEqual(self.wait_job(), "error")
        self.assertEqual(self.manager.active(verify=True)["id"], previous)
        self.assertTrue(self.manager.status()["ready"])

    def test_cancel_resume_reuses_complete_originals_and_survives_restart(self):
        self.manager.start_plan(self.client)
        self.assertEqual(self.wait_job(), "planned")
        self.client.cancel_on_download = True
        self.manager.start_download(self.client, self.manager.job["planId"])
        self.assertEqual(self.wait_job(), "cancelled")
        self.assertFalse(self.manager.status()["ready"])
        self.client.cancel_on_download = False
        self.manager = OfflineWorkspace(self.state)
        self.state.offline = self.manager
        self.manager.start_download(self.client, self.manager.job["planId"])
        self.assertEqual(self.wait_job(), "ready")
        self.assertEqual(self.client.download_calls.count(A1), 1)
        self.assertEqual(self.client.download_calls.count(A2), 1)

    def test_missing_original_never_claims_ready(self):
        snapshot = self.prepare()
        self.manager.media_path(snapshot, snapshot["media"]["originals"][A1]["path"]).unlink()
        self.assertFalse(self.manager.status()["ready"])
        with self.assertRaises(OfflineError):
            self.manager.open_client()

    def test_relative_media_paths_cannot_escape_snapshot(self):
        snapshot = self.prepare()
        for path in ("../../../../private", str(self.directory / "private")):
            with self.subTest(path=path), self.assertRaises(OfflineError):
                self.manager.media_path(snapshot, path)

    def test_offline_session_reads_local_only_and_sync_refuses(self):
        auth, cookie = self.open_offline()
        self.assertTrue(auth.authorized(cookie))
        self.assertTrue(self.state.summary()["connected"])
        self.assertTrue(self.state.summary()["offline"])
        self.assertEqual(self.state.require_client().asset(A1)["id"], A1)
        self.assertEqual(self.state.require_client().request("GET", f"/assets/{A1}/thumbnail")[0], b"preview")
        self.state.queue({"personId": P1, "operation": "rename", "name": "Offline Draft"})
        with self.assertRaisesRegex(OfflineError, "Reconnect"):
            auth.sync(cookie, self.state.pending_data()["reviewToken"])
        self.assertEqual(len(self.state.store.pending()), 1)
        with self.assertRaises(OfflineError):
            self.state.require_client().request("PUT", "/people/" + P1, {})
        self.assertFalse(self.client.writes)

    def test_named_subset_never_completes_global_face_review(self):
        self.open_offline()
        result = self.state.faces_for_person(P3)
        self.assertEqual(result["offlineCoverage"], {"availableAssets": 1, "totalAssets": 9, "complete": False})
        self.assertFalse(result["inventoryComplete"])
        self.state.set_face_reviewed({"personId": P3, "faceId": F2, "reviewed": True})
        result = self.state.faces_for_person(P3)
        self.assertFalse(result["personReviewed"])
        self.assertFalse(next(p for p in self.state.list_people("review", "most") if p["id"] == P3)["reviewed"])
        self.assertTrue(self.state.scan_face_review_page()["done"])

    def test_pending_investigate_and_drafts_survive_offline_restart(self):
        self.open_offline()
        self.state.queue({"personId": P1, "operation": "rename", "name": "Portable Draft"})
        self.state.store.mark_investigate(P2)
        self.state.store.set_setting("group.includeArchived", "true")
        self.state.store.set_face_review_drafts(P3, [{"faceId": F2, "assetId": A1, "fileName": "synthetic.jpg", "verdict": "correct"}])
        restarted = app.AppState(app.Store(self.directory / "state.sqlite3"), self.directory / "reports")
        auth = app.AdminSession(restarted)
        auth.open_offline()
        self.assertEqual(restarted.store.pending()[0]["name"], "Portable Draft")
        self.assertIn(P2, restarted.store.investigate_ids())
        self.assertEqual(restarted.store.face_review_drafts(P3)[0]["face_id"], F2)
        self.assertEqual(restarted.store.setting("group.includeArchived"), "true")

    def test_person_name_visibility_count_and_feature_conflicts_fail_before_writes(self):
        self.open_offline()
        self.state.queue({"personId": P1, "operation": "rename", "name": "Offline Name"})
        self.state.client = self.client
        for key, value in (("name", "Changed elsewhere"), ("isHidden", True), ("assetCount", 4), ("featureFaceAssetId", A2)):
            before = copy.deepcopy(self.client.rows[P1])
            self.client.rows[P1][key] = value
            with self.subTest(key=key), self.assertRaises(OfflineConflict):
                self.state.sync()
            self.assertFalse(self.client.writes)
            self.assertEqual(len(self.state.store.pending()), 1)
            self.client.rows[P1] = before

    def test_face_assignment_change_blocks_entire_selected_batch(self):
        self.open_offline()
        self.state.queue({"personId": P1, "operation": "rename", "name": "One"})
        self.state.queue({"personId": P2, "operation": "rename", "name": "Two"})
        self.state.client = self.client
        self.client.face_rows[A1][0]["person"]["id"] = P3
        with self.assertRaises(OfflineConflict):
            self.state.sync()
        self.assertFalse(self.client.writes)
        self.assertEqual(len(self.state.store.pending()), 2)

    def test_excluded_conflict_does_not_block_selected_work_and_return_clears(self):
        self.open_offline()
        self.state.queue({"personId": P1, "operation": "rename", "name": "One"})
        self.state.queue({"personId": P2, "operation": "rename", "name": "Two"})
        self.state.client = self.client
        self.client.rows[P1]["name"] = "Changed elsewhere"
        self.assertTrue(self.manager.validate(self.client))
        self.state.store.set_included(P1, False)
        self.assertEqual(self.state.pending_data()["offlineConflicts"], [])
        self.assertEqual(self.state.sync()["failed"], 0)
        self.assertEqual(self.client.writes, [("person", P2, {"name": "Two"})])
        self.assertEqual(self.state.store.pending()[0]["person_id"], P1)

    def test_confirmed_own_rename_updates_only_its_baseline(self):
        self.open_offline()
        self.state.queue({"personId": P1, "operation": "rename", "name": "First"})
        self.state.client = self.client
        self.assertEqual(self.state.sync()["failed"], 0)
        self.state.queue({"personId": P1, "operation": "rename", "name": "Second"})
        self.assertEqual(self.state.sync()["failed"], 0)
        self.client.rows[P2]["name"] = "External"
        self.state.queue({"personId": P2, "operation": "rename", "name": "Unseen"})
        with self.assertRaises(OfflineConflict):
            self.state.sync()
        baseline = next(self.manager.root.rglob("baseline.json"))
        saved = json.loads(baseline.read_text(encoding="utf-8"))
        self.assertEqual(saved["people"][P1]["name"], "Second")
        self.assertEqual(saved["people"][P2]["name"], "")
        self.assertFalse(self.manager.status()["ready"])
        self.assertTrue(self.manager.status()["stale"])
        with self.assertRaises(OfflineError):
            self.manager.open_client()

    def test_face_detach_own_baseline_allows_later_rename(self):
        self.open_offline()
        self.state.queue_face_detach({"personId": P1, "faceId": F1, "assetId": A1})
        self.state.client = self.client
        self.assertEqual(self.state.sync()["failed"], 0)
        self.state.queue({"personId": P1, "operation": "rename", "name": "After correction"})
        self.assertEqual(self.state.sync()["failed"], 0)
        self.assertEqual(len([w for w in self.client.writes if w[0] == "face"]), 1)

    def test_unresolved_offline_work_prevents_snapshot_replacement(self):
        self.open_offline()
        self.state.queue({"personId": P1, "operation": "rename", "name": "Keep baseline"})
        with self.assertRaisesRegex(OfflineError, "Resolve offline Pending"):
            self.manager.start_plan(self.client)

    def test_open_snapshot_cannot_be_replaced_by_finishing_preparation(self):
        previous = self.prepare()["id"]
        self.manager._discover(self.client)
        self.state.client = self.manager.open_client()
        self.assertNotEqual(self.manager.plan["id"], previous)
        with self.assertRaisesRegex(OfflineError, "offline workspace is open"):
            self.manager._download(self.client)
        self.assertEqual(self.manager.active(verify=True)["id"], previous)
        self.assertEqual(self.state.store.setting("offlineBaseline"), previous)

    def block_activation(self):
        """Hold the real mutation lock and observe the worker trying to enter it."""
        original_lock = self.state.mutation_lock
        reached = threading.Event()
        class ObservedLock:
            def __enter__(self):
                reached.set()
                original_lock.acquire()
            def __exit__(self, *_args):
                original_lock.release()
        original_lock.acquire()
        self.state.mutation_lock = ObservedLock()
        self.manager.start_download(self.client, self.manager.job["planId"])
        self.assertTrue(reached.wait(5))
        return original_lock

    def test_cancel_waiting_for_activation_preserves_previous_pointer(self):
        previous = self.prepare()["id"]
        self.manager.start_plan(self.client)
        self.assertEqual(self.wait_job(), "planned")
        lock = self.block_activation()
        try:
            self.manager.cancel()
        finally:
            lock.release()
        self.assertEqual(self.wait_job(), "cancelled")
        self.state.mutation_lock = lock
        self.assertEqual(self.manager.active(verify=True)["id"], previous)
        self.assertNotEqual(self.manager.job["phase"], "ready")

    def test_connection_rotation_waiting_for_activation_preserves_previous_pointer(self):
        previous = self.prepare()["id"]
        self.manager.start_plan(self.client)
        self.assertEqual(self.wait_job(), "planned")
        lock = self.block_activation()
        try:
            # Same owner and URL, but a newly authenticated connection. The old
            # job must not publish after its captured connection was replaced.
            self.state.client = SyntheticImmich()
        finally:
            lock.release()
        self.assertEqual(self.wait_job(), "error")
        self.state.mutation_lock = lock
        self.assertEqual(self.manager.active(verify=True)["id"], previous)

    def test_late_cancel_cannot_turn_published_ready_job_into_cancelling(self):
        self.prepare()
        self.manager.worker = mock.Mock(is_alive=lambda: True)
        self.manager.cancel()
        self.assertEqual(self.manager.job["phase"], "ready")
        self.manager.worker = None

    def test_touched_person_unrelated_change_is_not_blessed_as_own_write(self):
        self.open_offline()
        self.state.queue({"personId": P1, "operation": "hide"})
        self.state.client = self.client
        original_update = self.client.update_person
        def update(pid, payload):
            original_update(pid, payload)
            self.client.rows[pid]["name"] = "Concurrent rename"
        self.client.update_person = update
        self.assertEqual(self.state.sync()["failed"], 0)
        baseline = json.loads(next(self.manager.root.rglob("baseline.json")).read_text(encoding="utf-8"))
        self.assertEqual(baseline["people"][P1]["name"], "")
        self.assertFalse(baseline["people"][P1]["isHidden"])
        self.assertTrue(self.manager.status()["stale"])
        self.state.queue({"personId": P1, "operation": "rename", "name": "Later"})
        with self.assertRaises(OfflineConflict):
            self.state.sync()

    def test_online_sync_also_stales_prepared_snapshot_before_first_offline_open(self):
        snapshot = self.prepare()
        self.assertFalse(self.state.store.setting("offlineBaseline"))
        self.state.queue({"personId": P1, "operation": "rename", "name": "Online change"})
        self.assertEqual(self.state.sync()["failed"], 0)
        status = self.manager.status(private=True)
        self.assertFalse(status["ready"])
        self.assertTrue(status["stale"])
        self.assertEqual(status["snapshot"]["id"], snapshot["id"])
        self.assertTrue(self.manager.media_path(snapshot, snapshot["media"]["originals"][A1]["path"]).is_file())
        with self.assertRaises(OfflineError):
            self.manager.open_client()

    def test_missing_baseline_fails_closed(self):
        self.open_offline()
        self.state.queue({"personId": P1, "operation": "rename", "name": "Name"})
        self.state.client = self.client
        next(self.manager.root.rglob("baseline.json")).unlink()
        with self.assertRaisesRegex(OfflineError, "baseline is unavailable"):
            self.state.sync()
        self.assertFalse(self.client.writes)


class QuietHandler(app.Handler):
    def log_message(self, *_args):
        pass


class OfflineHTTPTests(OfflineFixture, unittest.TestCase):
    def serve(self, action, host="127.0.0.1"):
        auth = app.AdminSession(self.state)
        server = ThreadingHTTPServer((host, 0), QuietHandler)
        with mock.patch.object(app, "STATE", self.state, create=True), mock.patch.object(app, "AUTH", auth, create=True):
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                action(f"http://127.0.0.1:{server.server_port}")
            finally:
                server.shutdown()
                server.server_close()
                thread.join()

    def test_public_status_minimal_and_open_explicit_local_session_required(self):
        self.prepare()
        def run(base):
            with urllib.request.urlopen(base + "/api/offline/status") as response:
                self.assertEqual(json.load(response), {"available": True, "ready": True, "offline": False, "stale": False})
            with self.assertRaises(urllib.error.HTTPError) as denied:
                urllib.request.urlopen(base + "/media/asset/" + A1)
            self.assertEqual(denied.exception.code, 401)
            body = b"{}"
            request = urllib.request.Request(base + "/api/offline/open", body, {"Content-Type": "application/json", "Origin": base})
            with urllib.request.urlopen(request) as response:
                cookie = response.headers["Set-Cookie"].split(";", 1)[0]
                self.assertEqual(json.load(response), {"ok": True, "offline": True})
            request = urllib.request.Request(base + "/api/state", headers={"Cookie": cookie})
            with urllib.request.urlopen(request) as response:
                self.assertTrue(json.load(response)["offline"])
            request = urllib.request.Request(base + "/media/original/" + A1, headers={"Cookie": cookie})
            with urllib.request.urlopen(request) as response:
                self.assertEqual(response.read(), b"original")
                self.assertIn("sandbox", response.headers["Content-Security-Policy"])
            request = urllib.request.Request(base + "/api/state", headers={"Cookie": cookie, "X-Forwarded-For": "192.0.2.1"})
            with self.assertRaises(urllib.error.HTTPError) as denied:
                urllib.request.urlopen(request)
            self.assertEqual(denied.exception.code, 401)
        self.serve(run)

    def test_wildcard_listener_and_cross_origin_cannot_open_private_snapshot(self):
        self.prepare()
        def run(base):
            for origin in (base, "https://unrelated.example"):
                request = urllib.request.Request(base + "/api/offline/open", b"{}", {"Content-Type": "application/json", "Origin": origin})
                with self.subTest(origin=origin), self.assertRaises(urllib.error.HTTPError) as denied:
                    urllib.request.urlopen(request)
                self.assertEqual(denied.exception.code, 403)
        self.serve(run, "0.0.0.0")

    def test_offline_session_can_open_reconnect_form_without_clearing_work(self):
        self.prepare()
        self.state.queue({"personId": P1, "operation": "rename", "name": "Offline work"})
        def run(base):
            request = urllib.request.Request(base + "/api/offline/open", b"{}",
                {"Content-Type": "application/json", "Origin": base})
            with urllib.request.urlopen(request) as response:
                cookie = response.headers["Set-Cookie"].split(";", 1)[0]
                response.read()
            request = urllib.request.Request(base + "/login?next=/pending", headers={"Cookie": cookie})
            with urllib.request.urlopen(request) as response:
                self.assertIn("/login?next=/pending", response.url)
                self.assertIn(b'id="login-form"', response.read())
            self.assertTrue(self.state.client.offline)
            self.assertEqual(self.state.store.pending()[0]["name"], "Offline work")
            self.assertFalse(self.client.writes)
        self.serve(run)


class OriginalStreamTests(unittest.TestCase):
    def test_original_over_normal_image_limit_streams_atomically_and_can_cancel(self):
        class Response(io.BytesIO):
            headers = mock.Mock()
        payload = b"x" * (app.MAX_IMAGE + 123)
        response = Response(payload)
        response.headers.get.return_value = str(len(payload))
        response.headers.get_content_type.return_value = "image/jpeg"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / A1
            client = app.Immich("https://synthetic.example", "synthetic-key")
            client.opener.open = mock.Mock(return_value=response)
            size, mime, digest = client.download_original(A1, path, threading.Event(), 30_000_000, 0)
            self.assertEqual(size, len(payload))
            self.assertEqual(digest, hashlib.sha256(payload).hexdigest())
            self.assertEqual(path.stat().st_size, len(payload))
            self.assertFalse(path.with_suffix(".part").exists())
            event = threading.Event()
            event.set()
            response = Response(payload)
            client.opener.open.return_value = response
            with self.assertRaises(app.Cancelled):
                client.download_original(A2, Path(directory) / A2, event, 30_000_000, 0)
            self.assertFalse((Path(directory) / A2).exists())
            self.assertFalse((Path(directory) / A2).with_suffix(".part").exists())

    def test_incomplete_original_cannot_replace_completed_file(self):
        response = io.BytesIO(b"short")
        response.headers = mock.Mock()
        response.headers.get.return_value = "100"
        response.headers.get_content_type.return_value = "image/jpeg"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / A1
            path.write_bytes(b"existing")
            client = app.Immich("https://synthetic.example", "synthetic-key")
            client.opener.open = mock.Mock(return_value=response)
            with self.assertRaisesRegex(OfflineError, "incomplete"):
                client.download_original(A1, path, threading.Event(), 1000, 0)
            self.assertEqual(path.read_bytes(), b"existing")
            self.assertFalse(path.with_suffix(".part").exists())


if __name__ == "__main__":
    unittest.main()
