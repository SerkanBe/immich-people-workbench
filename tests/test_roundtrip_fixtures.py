# Copyright (C) 2026 Serkan Bekdemir
# SPDX-License-Identifier: AGPL-3.0-only
"""Bounded synthetic assets and audit controls for offline round-trip checks."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import urllib.request
import unittest
from http.server import ThreadingHTTPServer
from unittest import mock

try:
    from PIL import Image
except ModuleNotFoundError as exc:
    if exc.name == "PIL" or (exc.name or "").startswith("PIL."):
        raise unittest.SkipTest("Pillow is optional; install/use the documentation environment for roundtrip fixture tests") from exc
    raise

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import people_workbench as app
import tour_mock


class RoundtripFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="pw-roundtrip-fixture-")
        cls.directory = Path(cls.temp.name)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), tour_mock.Handler)
        cls.server.daemon_threads = True
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}/api"
        cls.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def setUp(self):
        self.fixture = tour_mock.Fixtures("roundtrip")
        self.fixture_patch = mock.patch.object(tour_mock, "F", self.fixture)
        self.fixture_patch.start()
        self.client = app.Immich(self.base, "synthetic-owner-key")
        self.client.opener = self.opener
        self.client.connection_metadata = {"authMode": "apiKey", "userId": tour_mock.uid("owner"),
                                            "grantedPermissions": ["all"]}

    def tearDown(self):
        self.fixture_patch.stop()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)
        cls.temp.cleanup()

    def control(self, path, payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(self.base.replace("/api", "") + path, data=data,
                                         headers={"Content-Type": "application/json"} if data is not None else {},
                                         method="POST" if data is not None else "GET")
        with self.opener.open(request, timeout=3) as response:
            return json.load(response)

    def test_roundtrip_profile_is_small_and_encodes_selection_edges(self):
        fixture = self.fixture
        self.assertEqual(fixture.profile, "roundtrip")
        self.assertEqual(len(fixture.people), 8)
        self.assertEqual(len(fixture.assets), 17)
        self.assertEqual(len(fixture.fixture_ids["expectedExcludedAssets"]), 8)
        self.assertEqual(len(fixture.fixture_ids["expectedSelectedAssets"]), 9)
        self.assertEqual(set(fixture.fixture_ids["journey"]["groupParticipants"]),
                         {fixture.fixture_ids["people"][key] for key in ("unnamed-a", "unnamed-b", "named-a", "named-b")})
        manifest = self.control("/__review__/fixtures")
        self.assertEqual(manifest["eligibleAssets"], fixture.fixture_ids["expectedSelectedAssets"])
        self.assertEqual(manifest["journey"], fixture.fixture_ids["journey"])
        self.assertEqual(manifest["partialNamedPerson"], fixture.fixture_ids["partialNamedPerson"])

        unnamed_a = fixture.fixture_ids["people"]["unnamed-a"]
        unnamed_b = fixture.fixture_ids["people"]["unnamed-b"]
        rows_a, _, _ = self.client.person_assets_page(unnamed_a, size=100, group_photos=True)
        rows_b, _, _ = self.client.person_assets_page(unnamed_b, size=100, group_photos=True)
        ids_a = {row["id"] for row in rows_a}
        ids_b = {row["id"] for row in rows_b}
        self.assertIn(fixture.fixture_ids["sharedAsset"], ids_a & ids_b)
        self.assertIn(fixture.fixture_ids["assets"]["archived-context"], ids_a)
        hidden_cluster_asset = fixture.fixture_ids["assets"]["hidden-cluster-only"]
        named_only_asset = fixture.fixture_ids["assets"]["named-only"]
        not_searched_by_unnamed = {hidden_cluster_asset, named_only_asset}
        self.assertTrue((set(fixture.fixture_ids["expectedExcludedAssets"]) - not_searched_by_unnamed)
                        .issubset(ids_a | ids_b))
        self.assertTrue(not_searched_by_unnamed.isdisjoint(ids_a | ids_b))
        independent = fixture.fixture_ids["independentClusters"]
        group_people = {face["person"]["id"] for face_id in fixture.fixture_ids["faces"]["group-photo"]
                        for face in [fixture.faces[face_id]]}
        self.assertTrue(set(independent.values()).isdisjoint(group_people))

        named_id = fixture.fixture_ids["partialNamedPerson"]
        total_named_assets = self.client.statistics(named_id)
        selected_named_assets = sum(
            any(face.get("person", {}).get("id") == named_id for face in fixture.faces.values()
                if face["assetId"] == asset_id)
            for asset_id in fixture.fixture_ids["expectedSelectedAssets"]
        )
        self.assertGreater(total_named_assets, selected_named_assets)
        self.assertEqual(total_named_assets - selected_named_assets, 1)  # named-only original is not selected

    def test_discovery_dedupes_and_excludes_nonportable_media(self):
        state_path = self.directory / "state-discovery.sqlite3"
        state = app.AppState(app.Store(state_path), self.directory / "reports-discovery")
        owner = {"url": self.base, "userId": tour_mock.uid("owner")}
        state.store.set_setting("owner", json.dumps(owner, sort_keys=True))
        state.client = self.client
        manager = state.offline
        manager.start_plan(self.client)
        manager.worker.join(timeout=10)
        self.assertFalse(manager.worker.is_alive())
        self.assertEqual(manager.job["phase"], "planned", manager.job["error"])

        expected = set(self.fixture.fixture_ids["expectedSelectedAssets"])
        self.assertEqual(set(manager.plan["assets"]), expected)
        self.assertTrue(set(self.fixture.fixture_ids["expectedExcludedAssets"]).isdisjoint(manager.plan["assets"]))
        self.assertEqual(manager.job["assetCount"], len(expected))
        self.assertEqual(manager.plan["people"][self.fixture.fixture_ids["partialNamedPerson"]]["assetCount"], 4)
        hidden_person = self.fixture.fixture_ids["hiddenCluster"]
        self.assertIn(hidden_person, manager.plan["people"])
        self.assertEqual(manager.plan["people"][hidden_person]["assetCount"], 1)
        self.assertEqual(self.fixture.requests[
            f"GET /assets/{self.fixture.fixture_ids['sharedAsset']}"], 1)

        manager.start_download(self.client, manager.plan["id"])
        manager.worker.join(timeout=30)
        self.assertFalse(manager.worker.is_alive())
        self.assertEqual(manager.job["phase"], "ready", manager.job["error"])
        snapshot = manager.active(verify=True)
        self.assertEqual(set(snapshot["media"]["originals"]), expected)
        self.assertNotIn(self.fixture.fixture_ids["assets"]["named-only"], snapshot["media"]["originals"])
        large_path = manager.media_path(snapshot, snapshot["media"]["originals"][
            self.fixture.fixture_ids["assets"]["large-original"]]["path"])
        self.assertEqual(large_path.stat().st_size, self.fixture.large_original_bytes)
        with Image.open(large_path) as image:
            self.assertEqual(image.size, (2400, 1800))
            image.verify()
        audit = self.control("/__review__/audit")
        self.assertEqual(audit["appWriteAttempts"], [])
        original_requests = [event["path"] for event in audit["requestLog"]
                             if event["path"].endswith("/original")]
        self.assertNotIn(f"/assets/{self.fixture.fixture_ids['assets']['named-only']}/original", original_requests)

    def test_external_mutations_and_app_write_audit_are_distinct_and_value_level(self):
        person_id = self.fixture.fixture_ids["people"]["named-a"]
        old_name = self.client.person(person_id)["name"]
        external = self.control("/__review__/mutate", {"operation": "rename_person",
                                                          "personId": person_id, "name": "Synthetic upstream edit"})
        self.assertEqual(external["mutation"]["sequence"], 1)
        self.assertEqual(self.client.person(person_id)["name"], "Synthetic upstream edit")

        self.client.update_person(person_id, {"name": "Portable Pending name"})
        audit = self.control("/__review__/audit")
        self.assertEqual(len(audit["externalMutations"]), 1)
        self.assertEqual(len(audit["appWriteAttempts"]), 1)
        attempt = audit["appWriteAttempts"][0]
        self.assertEqual((attempt["method"], attempt["path"], attempt["status"]),
                         ("PUT", f"/people/{person_id}", 200))
        self.assertEqual(attempt["payload"], {"name": "Portable Pending name"})
        self.assertEqual(audit["writes"], [attempt])
        self.assertEqual(self.client.person(person_id)["name"], "Portable Pending name")

        self.control("/__review__/settings", {"failNext": [{"method": "PUT", "path": f"/people/{person_id}",
                                                              "status": 503, "count": 1}]})
        with self.assertRaises(app.UpstreamError):
            self.client.update_person(person_id, {"name": "Must remain unchanged"})
        audit = self.control("/__review__/audit")
        failed = audit["appWriteAttempts"][-1]
        self.assertEqual(failed["status"], 503)
        self.assertEqual(failed["payload"], {"name": "Must remain unchanged"})
        self.assertEqual(len(audit["writes"]), 1)
        self.assertEqual(self.client.person(person_id)["name"], "Portable Pending name")
        self.assertNotEqual(old_name, "Portable Pending name")

        people_before_create = len(self.fixture.people)
        self.control("/__review__/settings", {"failWrites": True})
        with self.assertRaises(app.UpstreamError):
            self.client.create_person()
        audit = self.control("/__review__/audit")
        self.assertEqual(len(self.fixture.people), people_before_create)
        self.assertEqual(audit["appWriteAttempts"][-1]["kind"], "create")
        self.assertEqual(audit["appWriteAttempts"][-1]["payload"], {"name": "", "isHidden": False})
        self.assertEqual(audit["appWriteAttempts"][-1]["status"], 503)
        self.assertEqual(len(audit["writes"]), 1)

    def test_barrier_allows_competing_external_edit_before_app_write(self):
        person_id = self.fixture.fixture_ids["people"]["named-b"]
        response = self.control("/__review__/settings", {"pauseNext": {"method": "PUT",
                                                                           "path": f"/people/{person_id}",
                                                                           "timeout": 6}})
        barrier_id = response["barrierId"]
        request_thread = threading.Thread(
            target=lambda: self.client.update_person(person_id, {"name": "Selected value"}),
            daemon=True,
        )
        request_thread.start()
        deadline = time.monotonic() + 3
        try:
            while time.monotonic() < deadline:
                if self.control(f"/__review__/barriers/{barrier_id}")["state"] == "entered":
                    break
                time.sleep(.01)
            else:
                self.fail("Application request did not reach its configured barrier")
            self.control("/__review__/mutate", {"operation": "rename_person", "personId": person_id,
                                                "name": "Competing upstream value"})
            self.control(f"/__review__/barriers/{barrier_id}/release", {})
            request_thread.join(timeout=3)
            self.assertFalse(request_thread.is_alive())
            self.assertEqual(self.client.person(person_id)["name"], "Selected value")
            audit = self.control("/__review__/audit")
            self.assertEqual(audit["appWriteAttempts"][-1]["payload"], {"name": "Selected value"})
            self.assertEqual(audit["externalMutations"][-1]["name"], "Competing upstream value")
            self.assertEqual(audit["appWriteAttempts"][-1]["status"], 200)
        finally:
            self.control(f"/__review__/barriers/{barrier_id}/release", {})
            request_thread.join(timeout=3)

    def test_read_failure_and_truncated_original_are_deterministic(self):
        named_id = self.fixture.fixture_ids["partialNamedPerson"]
        self.control("/__review__/settings", {"failNext": [{"method": "GET",
                                                              "path": f"/people/{named_id}/statistics",
                                                              "status": 503}]})
        state = app.AppState(app.Store(self.directory / "state-read-failure.sqlite3"),
                             self.directory / "reports-read-failure")
        state.store.set_setting("owner", json.dumps({"url": self.base, "userId": tour_mock.uid("owner")}))
        state.offline.start_plan(self.client)
        state.offline.worker.join(timeout=10)
        self.assertFalse(state.offline.worker.is_alive())
        self.assertEqual(state.offline.job["phase"], "error")
        self.assertEqual(state.offline.job["downloadedFiles"], 0)
        audit = self.control("/__review__/audit")
        failed_read = next(event for event in audit["requestLog"]
                           if event["path"] == f"/people/{named_id}/statistics")
        self.assertEqual(failed_read["status"], 503)
        self.assertEqual(audit["appWriteAttempts"], [])

        large_id = self.fixture.fixture_ids["assets"]["large-original"]
        partial_path = self.directory / "interrupted-original.jpg"
        self.control("/__review__/settings", {"interruptNextStream": {
            "path": f"/assets/{large_id}/original", "afterBytes": 100_000}})
        with self.assertRaisesRegex(app.OfflineError, "incomplete"):
            self.client.download_original(large_id, partial_path, threading.Event(),
                                          self.fixture.large_original_bytes + 1024, 0)
        self.assertFalse(partial_path.exists())
        self.assertFalse(partial_path.with_suffix(".part").exists())
        audit = self.control("/__review__/audit")
        interrupted = next(event for event in audit["requestLog"]
                           if event["path"] == f"/assets/{large_id}/original")
        self.assertTrue(interrupted["truncated"])
        self.assertEqual(interrupted["streamedBytes"], 100_000)

    def test_original_is_valid_and_streams_past_normal_image_limit(self):
        fixture_ids = self.fixture.fixture_ids
        asset_id = fixture_ids["assets"]["large-original"]
        path = self.directory / "streamed-original.jpg"
        size, mime, digest = self.client.download_original(asset_id, path, threading.Event(),
                                                           self.fixture.large_original_bytes + 1024, 0)
        self.assertEqual(size, self.fixture.large_original_bytes)
        self.assertGreater(size, app.MAX_IMAGE)
        self.assertEqual(mime, "image/jpeg")
        self.assertEqual(digest, hashlib.sha256(path.read_bytes()).hexdigest())
        with Image.open(path) as image:
            self.assertEqual(image.size, (2400, 1800))
            image.verify()

    def test_default_documentation_fixture_is_unchanged(self):
        fixture = tour_mock.GUIDE_FIXTURE
        self.assertEqual(fixture.profile, "guide")
        self.assertEqual(len(fixture.people), 44)
        group_assets = [asset for asset in fixture.assets.values()
                        if asset["originalFileName"].startswith("synthetic-group-")]
        self.assertEqual(len(group_assets), 3)
        with mock.patch.object(tour_mock, "F", fixture):
            self.assertEqual(len(self.client.all_people()), 44)
            group_id = tour_mock.uid("group-photo-0")
            self.assertEqual(len(self.client.faces(group_id)), 6)
            rows, _, _ = self.client.person_assets_page(fixture.rows[0], size=100, group_photos=True)
            self.assertIn(group_id, {row["id"] for row in rows})


if __name__ == "__main__":
    unittest.main()
