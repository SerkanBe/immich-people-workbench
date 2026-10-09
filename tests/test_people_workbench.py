# Copyright (C) 2026 Serkan Bekdemir
# SPDX-License-Identifier: AGPL-3.0-only
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import json
import sqlite3
from http.server import ThreadingHTTPServer
from pathlib import Path
import sys
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import people_workbench as workbench
from people_workbench import AdminSession, AppState, Handler, Immich, Store, ToolError, group_asset_allowed, name_fragment_matches, paginate, sort_group_assets, sort_people


class FakeImmich:
    def __init__(self):
        self.calls = []
        self.reassign_fail = False
        self.rows = [
            {"id": "11111111-1111-4111-8111-111111111111", "name": "", "isHidden": False, "updatedAt": "2026-01-01T00:00:00Z"},
            {"id": "22222222-2222-4222-8222-222222222222", "name": "Existing Person", "isHidden": False, "updatedAt": "2026-01-02T00:00:00Z"},
        ]

    def update_person(self, person_id, payload):
        self.calls.append(("update", person_id, payload))

    def merge(self, target, source):
        self.calls.append(("merge", target, source))

    def all_people(self):
        return list(self.rows)

    def statistics(self, person_id):
        return 7

    def person_assets(self, person_id, limit=12):
        return [
            {"id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "originalFileName": "family-photo.jpg", "originalPath": "/library/family-photo.jpg"},
            {"id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "originalFileName": "holiday.png", "originalPath": "/library/trip/holiday.png"},
        ][:limit]

    def person_assets_page(self, person_id, page=1, size=50):
        rows = self.person_assets(person_id, size)
        return rows, False, len(rows)

    def faces(self, asset_id):
        source = self.rows[0]["id"]
        ids = {
            "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
            "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb": "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
        }
        return [{
            "id": ids[asset_id], "person": {"id": source},
            "boundingBoxX1": 10, "boundingBoxX2": 90,
            "boundingBoxY1": 20, "boundingBoxY2": 100,
            "imageWidth": 200, "imageHeight": 160,
        }]

    def create_person(self):
        person = {"id": "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee", "name": "", "isHidden": False}
        self.rows.append(person)
        self.calls.append(("create-person", person["id"]))
        return person

    def reassign_face(self, person_id, face_id):
        self.calls.append(("reassign-face", person_id, face_id))
        if self.reassign_fail:
            raise RuntimeError("synthetic reassign failure")

    def closest_people(self, person_id, limit=20):
        return list(reversed(self.rows))[:limit]


class PeopleWorkbenchTests(unittest.TestCase):
    def test_section_routes_serve_the_app_shell(self):
        class QuietHandler(Handler):
            def log_message(self, *_args):
                pass

        with tempfile.TemporaryDirectory() as directory:
            state = AppState(Store(Path(directory) / "state.sqlite3"), Path(directory) / "reports")
            auth = AdminSession(state)
            auth.cookie = "synthetic-session"
            auth.expires = time.monotonic() + 1000
            auth.checked = time.monotonic()
            with mock.patch.object(workbench, "STATE", state, create=True), mock.patch.object(workbench, "AUTH", auth, create=True):
                server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    self._check_section_routes(server.server_port)
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join()

    def _check_section_routes(self, port):
        base = f"http://127.0.0.1:{port}"
        opener = urllib.request.build_opener()
        opener.addheaders = [("Cookie", "people_workbench_session=synthetic-session")]
        for route in ("/", "/unnamed", "/naming/clusters", "/naming/group-photos", "/merge", "/investigate", "/faces", "/pending", "/named", "/ignored"):
            with self.subTest(route=route), opener.open(base + route) as response:
                self.assertEqual(response.status, 200)
                if route == "/merge":
                    self.assertEqual(response.geturl(), base + "/naming/clusters")
                self.assertIn(b'nav aria-label="People sections"', response.read())
        with opener.open(base + "/merge/") as response:
            self.assertEqual(response.geturl(), base + "/naming/clusters")
        for path, signature in (
            ("/merge.js", b"export function createMergeWorkbench"),
            ("/face-review.js", b"export function createFaceReview"),
            ("/naming.js", b"export function createNaming"),
            ("/group-photos.js", b"export function createGroupPhotos"),
            ("/investigate.js", b"export function createInvestigate"),
            ("/pending.js", b"export function createPending"),
            ("/browser-storage.js", b"export function readMergeCanvasState"),
            ("/asset-preview.js", b"export function createAssetPreview"),
        ):
            with self.subTest(path=path), opener.open(base + path) as response:
                self.assertEqual(response.status, 200)
                self.assertIn(signature, response.read())
        with self.assertRaises(urllib.error.HTTPError) as error:
            opener.open(base + "/not-a-section")
        self.assertEqual(error.exception.code, 404)

    def test_immich_login_requires_admin(self):
        client = Immich("https://immich.example")
        with mock.patch.object(client, "json", return_value={"isAdmin": False, "accessToken": "synthetic-token", "userId": "user-1"}):
            with self.assertRaisesRegex(ToolError, "Only Immich admins"):
                client.login("admin@example.test", "synthetic-password")
        with mock.patch.object(client, "json", return_value={"isAdmin": "true", "accessToken": "synthetic-token", "userId": "user-1"}):
            with self.assertRaisesRegex(ToolError, "Only Immich admins"):
                client.login("admin@example.test", "synthetic-password")
        with mock.patch.object(client, "json", return_value={"isAdmin": True, "accessToken": "synthetic-token", "userId": "user-1"}):
            self.assertEqual(client.login("admin@example.test", "synthetic-password"), ("synthetic-token", "user-1"))

    def test_immich_session_token_is_sent_as_user_token(self):
        class FakeResponse:
            headers = mock.Mock()

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                pass

            def read(self, _limit):
                return b'{}'

        FakeResponse.headers.get_content_type.return_value = "application/json"
        client = Immich("https://immich.example", "synthetic-token", session=True)
        with mock.patch.object(workbench.urllib.request, "urlopen", return_value=FakeResponse()) as opened:
            client.request("GET", "/users/me")
        request = opened.call_args.args[0]
        self.assertEqual(request.get_header("X-immich-user-token"), "synthetic-token")
        self.assertIsNone(request.get_header("X-api-key"))

    def test_group_photo_search_uses_person_lookup_with_archive_and_filters_assets_locally(self):
        client = Immich("https://immich.example")
        with mock.patch.object(client, "json", return_value={"assets": {"items": [], "total": 0}}) as searched:
            client.person_assets_page("synthetic-person", group_photos=True)
        payload = searched.call_args.args[2]
        self.assertEqual(payload["personIds"], ["synthetic-person"])
        self.assertTrue(payload["withArchived"])
        self.assertNotIn("type", payload)
        self.assertNotIn("visibility", payload)
        self.assertNotIn("withDeleted", payload)

    def test_http_login_guards_pages_api_media_and_logout(self):
        class QuietHandler(Handler):
            def log_message(self, *_args):
                pass

        class FakeLoginImmich:
            admin = True

            def __init__(self, url, credential="", insecure=False, *, session=False):
                self.base = url.rstrip("/") + "/api"
                self.session = session

            def login(self, email, password):
                if not self.admin:
                    raise ToolError("Only Immich admins can sign in to People Workbench")
                return "synthetic-immich-token", "admin-1"

            def current_admin(self):
                if not self.admin:
                    raise ToolError("Immich admin access is no longer available")
                return "admin-1"

            def version(self):
                return "synthetic"

        with tempfile.TemporaryDirectory() as directory:
            state = AppState(Store(Path(directory) / "state.sqlite3"), Path(directory) / "reports")
            auth = AdminSession(state)
            with mock.patch.object(workbench, "STATE", state, create=True), mock.patch.object(workbench, "AUTH", auth, create=True), mock.patch.object(workbench, "Immich", FakeLoginImmich), mock.patch.object(state, "load"):
                server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    base = f"http://127.0.0.1:{server.server_port}"

                    def post(path, data, cookie="", origin=True):
                        headers = {"Content-Type": "application/json"}
                        if origin:
                            headers["Origin"] = base
                        if cookie:
                            headers["Cookie"] = cookie
                        return urllib.request.urlopen(urllib.request.Request(base + path, json.dumps(data).encode(), headers, method="POST"))

                    for path in ("/api/state", "/api/group-photos", "/api/group-photos/progress", "/media/person/11111111-1111-4111-8111-111111111111", "/app.js"):
                        with self.subTest(path=path), self.assertRaises(urllib.error.HTTPError) as error:
                            urllib.request.urlopen(base + path)
                        self.assertEqual(error.exception.code, 401)
                    with urllib.request.urlopen(base + "/merge") as response:
                        self.assertEqual(response.geturl(), base + "/login?next=/naming/clusters")
                        self.assertIn(b"Sign in", response.read())
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        post("/api/sync", {"confirmation": "SYNC"})
                    self.assertEqual(error.exception.code, 401)
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        post("/api/login", {"url": "https://immich.example", "email": "admin@example.test", "password": "synthetic"}, origin=False)
                    self.assertEqual(error.exception.code, 403)

                    FakeLoginImmich.admin = False
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        post("/api/login", {"url": "https://immich.example", "email": "other@example.test", "password": "synthetic"})
                    self.assertEqual(error.exception.code, 403)
                    self.assertFalse(auth.cookie)

                    FakeLoginImmich.admin = True
                    with post("/api/login", {"url": "https://immich.example", "email": "admin@example.test", "password": "synthetic"}) as response:
                        self.assertEqual(response.status, 200)
                        cookie = response.headers["Set-Cookie"].split(";", 1)[0]
                        self.assertIn("HttpOnly", response.headers["Set-Cookie"])
                        self.assertIn("SameSite=Strict", response.headers["Set-Cookie"])
                    with urllib.request.urlopen(urllib.request.Request(base + "/merge", headers={"Cookie": cookie})) as response:
                        self.assertEqual(response.status, 200)
                        self.assertEqual(response.geturl(), base + "/naming/clusters")
                        self.assertIn(b'nav aria-label="People sections"', response.read())
                    with urllib.request.urlopen(urllib.request.Request(base + "/api/state", headers={"Cookie": cookie})) as response:
                        self.assertEqual(response.status, 200)
                    with urllib.request.urlopen(urllib.request.Request(base + "/api/group-photos", headers={"Cookie": cookie})) as response:
                        self.assertEqual(response.status, 200)
                        self.assertIn(b'"minFaces": 5', response.read())
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        post("/api/login", {"url": "https://other.example", "email": "admin@example.test", "password": "synthetic"})
                    self.assertEqual(error.exception.code, 403)
                    with urllib.request.urlopen(urllib.request.Request(base + "/api/state", headers={"Cookie": cookie})) as response:
                        self.assertEqual(response.status, 200)
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        post("/api/sync", {"confirmation": "SYNC"}, cookie, origin=False)
                    self.assertEqual(error.exception.code, 403)
                    with post("/api/logout", {}, cookie) as response:
                        self.assertEqual(response.status, 200)
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        urllib.request.urlopen(urllib.request.Request(base + "/api/state", headers={"Cookie": cookie}))
                    self.assertEqual(error.exception.code, 401)
                    with post("/api/login", {"url": "https://immich.example", "email": "admin@example.test", "password": "synthetic"}) as response:
                        cookie = response.headers["Set-Cookie"].split(";", 1)[0]
                    auth.checked = time.monotonic() - workbench.ADMIN_CHECK_SECONDS - 1
                    FakeLoginImmich.admin = False
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        urllib.request.urlopen(urllib.request.Request(base + "/api/state", headers={"Cookie": cookie}))
                    self.assertEqual(error.exception.code, 401)
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join()

    def test_name_matching_is_case_insensitive_fragment(self):
        self.assertTrue(name_fragment_matches("Lorem Ipsum", "OREM"))
        self.assertFalse(name_fragment_matches("Lorem Ipsum", "amet"))

    def test_sort_people_by_count_and_update(self):
        rows = [
            {"id": "a", "assetCount": 2, "updatedAt": "2026-01-03"},
            {"id": "b", "assetCount": 9, "updatedAt": "2026-01-01"},
        ]
        self.assertEqual([row["id"] for row in sort_people(rows, "most")], ["b", "a"])
        self.assertEqual([row["id"] for row in sort_people(rows, "fewest")], ["a", "b"])
        self.assertEqual([row["id"] for row in sort_people(rows, "updated-newest")], ["a", "b"])

    def test_paginate_clamps_page_and_reports_range(self):
        rows = [{"id": str(index)} for index in range(1, 54)]

        second = paginate(rows, 2, 24, "people")
        beyond_end = paginate(rows, 99, 24, "people")
        empty = paginate([], 4, 24, "pending")

        self.assertEqual([item["id"] for item in second["people"]], [str(index) for index in range(25, 49)])
        self.assertEqual((second["page"], second["pages"], second["from"], second["to"]), (2, 3, 25, 48))
        self.assertTrue(second["hasPrevious"])
        self.assertTrue(second["hasNext"])
        self.assertEqual((beyond_end["page"], beyond_end["from"], beyond_end["to"]), (3, 49, 53))
        self.assertEqual((empty["page"], empty["pages"], empty["from"], empty["to"]), (1, 1, 0, 0))

    def test_people_page_filters_before_slicing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = AppState(Store(root / "state.sqlite3"), root / "reports")
            app.people = {
                str(index): {
                    "id": str(index), "name": f"Person {index}" if index % 2 else "",
                    "isHidden": False, "assetCount": index,
                }
                for index in range(1, 31)
            }

            named = app.people_page("named", "most", 1, 5, "Person 1")
            unnamed = app.people_page("review", "most", 2, 4, "unnamed")

            self.assertEqual(named["total"], 6)
            self.assertEqual(len(named["people"]), 5)
            self.assertEqual(unnamed["total"], 15)
            self.assertEqual(unnamed["page"], 2)
            self.assertEqual(len(unnamed["people"]), 4)

            named_review = app.people_page("review", "most", 2, 4, name_filter="named")
            unnamed_review = app.people_page("review", "most", 2, 4, name_filter="unnamed")
            fewest_named = app.people_page("review", "fewest", 1, 4, name_filter="named")
            most_named = app.people_page("review", "most", 1, 4, name_filter="named")
            self.assertEqual((named_review["total"], unnamed_review["total"]), (15, 15))
            self.assertEqual((len(named_review["people"]), len(unnamed_review["people"])), (4, 4))
            self.assertEqual([person["id"] for person in fewest_named["people"]], ["1", "3", "5", "7"])
            self.assertEqual([person["id"] for person in most_named["people"]], ["29", "27", "25", "23"])
            self.assertTrue(all(person["name"] for person in named_review["people"]))
            self.assertTrue(all(not person["name"] for person in unnamed_review["people"]))
            self.assertEqual(app.people_page("review", "most", 1, 5, "Person 1", name_filter="unnamed")["total"], 0)
            app.store.set_face_reviewed("face-1", "1", True)
            app.store.record_face_review_page("1", 1, 1, [{"faceId": "face-1"}], False, 1, "")
            filtered = app.people_page("review", "most", 1, 5, hide_completed=True, name_filter="named")
            self.assertEqual(filtered["total"], 14)
            self.assertNotIn("1", {person["id"] for person in filtered["people"]})
            with self.assertRaisesRegex(ToolError, "name filter"):
                app.people_page("review", "most", 1, 5, name_filter="invalid")

    def test_review_page_hides_only_completed_people_before_pagination(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "state.sqlite3")
            app = AppState(store, Path(directory) / "reports")
            app.people = {
                person_id: {"id": person_id, "name": name, "isHidden": False, "assetCount": count}
                for person_id, name, count in (
                    ("complete", "Complete", 1), ("partial", "Partial", 2),
                    ("submitted", "Submitted", 1), ("draft", "Draft", 1),
                    ("empty", "Empty", 0),
                )
            }
            store.set_face_reviewed("face-complete", "complete", True)
            store.set_face_reviewed("face-partial", "partial", True)
            store.queue_face_detach("face-submitted", "submitted", "asset-submitted", "synthetic.jpg")
            store.set_face_review_drafts("draft", [{
                "faceId": "face-draft", "assetId": "asset-draft",
                "fileName": "synthetic.jpg", "verdict": "correct",
            }])
            store.record_face_review_page("complete", 1, 1, [{"faceId": "face-complete"}], False, 1, "")
            store.record_face_review_page("partial", 1, 2, [{"faceId": "face-partial"}, {"faceId": "face-untouched"}], False, 2, "")
            store.record_face_review_page("submitted", 1, 1, [{"faceId": "face-submitted"}], False, 1, "")
            store.record_face_review_page("draft", 1, 1, [{"faceId": "face-draft"}], False, 1, "")

            self.assertEqual(app.people_page("review", "most", 1, 5)["total"], 5)
            first = app.people_page("review", "most", 1, 1, hide_completed=True)
            second = app.people_page("review", "most", 2, 1, hide_completed=True)
            third = app.people_page("review", "most", 3, 1, hide_completed=True)
            self.assertEqual((first["total"], first["pages"]), (3, 3))
            self.assertEqual([page["people"][0]["id"] for page in (first, second, third)], ["partial", "draft", "empty"])
            self.assertEqual(app.people_page("review", "most", 1, 4, "Complete", True)["total"], 0)

    def test_store_persists_skips_and_pending_exclusions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite3"
            store = Store(path)
            self.assertEqual(store.skip("person-a"), 1)
            self.assertEqual(store.skip("person-a"), 2)
            store.upsert_pending({"personId": "person-a", "operation": "rename", "name": "A Name"})
            store.set_included("person-a", False)
            reopened = Store(path)
            self.assertEqual(reopened.skips()["person-a"], 2)
            self.assertEqual(reopened.pending()[0]["name"], "A Name")
            self.assertEqual(reopened.pending()[0]["included"], 0)

    def test_face_review_state_persists_and_counts_by_person(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite3"
            store = Store(path)
            store.set_face_reviewed("face-a", "person-a", True)
            store.set_face_reviewed("face-b", "person-a", True)
            store.set_face_reviewed("face-c", "person-b", True)

            reopened = Store(path)
            self.assertEqual(reopened.reviewed_face_ids("person-a"), {"face-a", "face-b"})
            self.assertEqual(reopened.face_review_counts(), {"person-a": 2, "person-b": 1})

            reopened.set_face_reviewed("face-a", "person-a", False)
            self.assertEqual(reopened.reviewed_face_ids("person-a"), {"face-b"})

    def test_sync_applies_checked_items_and_keeps_excluded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Store(root / "state.sqlite3")
            app = AppState(store, root / "reports")
            fake = FakeImmich()
            app.client = fake
            app.people = {row["id"]: dict(row, assetCount=7) for row in fake.rows}
            source, target = fake.rows[0]["id"], fake.rows[1]["id"]
            excluded = "33333333-3333-4333-8333-333333333333"
            store.upsert_pending({"personId": source, "operation": "merge", "targetPersonId": target, "targetName": "Existing Person", "name": "Existing Person"})
            store.upsert_pending({"personId": excluded, "operation": "rename", "name": "Do Not Sync"})
            store.set_included(excluded, False)
            result = app.sync()
            self.assertEqual(result["failed"], 0)
            self.assertIn(("merge", target, source), fake.calls)
            remaining = store.pending()
            self.assertEqual(len(remaining), 1)
            self.assertEqual(remaining[0]["person_id"], excluded)
            self.assertEqual(remaining[0]["included"], 0)

    def test_pending_rename_is_immediately_available_as_a_name(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Store(root / "state.sqlite3")
            app = AppState(store, root / "reports")
            fake = FakeImmich()
            app.client = fake
            app.people = {row["id"]: dict(row, assetCount=7) for row in fake.rows}
            source = fake.rows[0]["id"]
            app.queue({"personId": source, "operation": "rename", "name": "New Local Name"})
            names = app.named_people()
            local = next(person for person in names if person["id"] == source)
            self.assertEqual(local["name"], "New Local Name")
            self.assertTrue(local["isPending"])

    def test_merge_can_target_a_pending_name(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Store(root / "state.sqlite3")
            app = AppState(store, root / "reports")
            fake = FakeImmich()
            third = {"id": "33333333-3333-4333-8333-333333333333", "name": "", "isHidden": False}
            app.client = fake
            app.people = {row["id"]: dict(row, assetCount=7) for row in [*fake.rows, third]}
            first = fake.rows[0]["id"]
            app.queue({"personId": first, "operation": "rename", "name": "Pending Target"})
            app.queue({"personId": third["id"], "operation": "merge", "targetPersonId": first})
            merge = next(item for item in store.pending() if item["person_id"] == third["id"])
            self.assertEqual(merge["target_name"], "Pending Target")

    def test_merge_group_queues_one_rename_and_the_remaining_merges(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Store(root / "state.sqlite3")
            app = AppState(store, root / "reports")
            first = "11111111-1111-4111-8111-111111111111"
            second = "33333333-3333-4333-8333-333333333333"
            app.people = {
                first: {"id": first, "name": "", "isHidden": False, "assetCount": 12},
                second: {"id": second, "name": "", "isHidden": False, "assetCount": 4},
            }

            result = app.queue_merge_group({"personIds": [first, second], "survivorPersonId": first, "name": "Grouped Person"})
            pending = {item["person_id"]: item for item in store.pending()}

            self.assertEqual(result["targetPersonId"], first)
            self.assertEqual(pending[first]["operation"], "rename")
            self.assertEqual(pending[first]["name"], "Grouped Person")
            self.assertEqual(pending[second]["operation"], "merge")
            self.assertEqual(pending[second]["target_person_id"], first)

    def test_merge_group_can_merge_one_cluster_into_an_existing_person(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Store(root / "state.sqlite3")
            app = AppState(store, root / "reports")
            fake = FakeImmich()
            app.people = {row["id"]: dict(row, assetCount=7) for row in fake.rows}
            source, target = fake.rows[0]["id"], fake.rows[1]["id"]

            result = app.queue_merge_group({"personIds": [source], "targetPersonId": target, "name": "Existing Person"})
            pending = store.pending()

            self.assertEqual(result["merged"], 1)
            self.assertFalse(result["renamed"])
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0]["operation"], "merge")
            self.assertEqual(pending[0]["target_person_id"], target)

    def test_merge_group_without_name_queues_only_merges_and_hides_survivor_until_sync(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Store(root / "state.sqlite3")
            app = AppState(store, root / "reports")
            survivor = "11111111-1111-4111-8111-111111111111"
            source = "33333333-3333-4333-8333-333333333333"
            app.people = {
                survivor: {"id": survivor, "name": "", "isHidden": False, "assetCount": 12},
                source: {"id": source, "name": "", "isHidden": False, "assetCount": 4},
            }

            result = app.queue_merge_group({"personIds": [survivor, source], "survivorPersonId": survivor})
            pending = store.pending()

            self.assertEqual(result["targetPersonId"], survivor)
            self.assertEqual(result["targetName"], "")
            self.assertFalse(result["renamed"])
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0]["person_id"], source)
            self.assertEqual(pending[0]["operation"], "merge")
            self.assertEqual(pending[0]["target_person_id"], survivor)
            self.assertEqual(app.list_people("unnamed", "most"), [])
            self.assertEqual(app.summary()["counts"]["unnamed"], 0)

    def test_discard_pending_keeps_skips_and_reloads_immich_names(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Store(root / "state.sqlite3")
            app = AppState(store, root / "reports")
            fake = FakeImmich()
            app.client = fake
            app.people = {row["id"]: dict(row, assetCount=7) for row in fake.rows}
            person_id = fake.rows[0]["id"]
            store.skip(person_id)
            app.queue({"personId": person_id, "operation": "rename", "name": "Unsynced Local Name"})
            store.skip(person_id)
            app.sample_cache[person_id] = [{"assetId": "cached"}]
            fake.rows[0] = {**fake.rows[0], "name": "Imported Immich Name"}

            self.assertEqual(app.discard_pending(), 1)
            app.load()

            self.assertEqual(store.pending(), [])
            self.assertEqual(store.skips()[person_id], 1)
            self.assertEqual(app.sample_cache, {})
            reloaded = next(person for person in app.named_people() if person["id"] == person_id)
            self.assertEqual(reloaded["name"], "Imported Immich Name")

    def test_investigate_queue_is_persistent_and_exposes_immich_filenames(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Store(root / "state.sqlite3")
            app = AppState(store, root / "reports")
            fake = FakeImmich()
            app.client = fake
            app.people = {row["id"]: dict(row, assetCount=7) for row in fake.rows}
            person_id = fake.rows[0]["id"]

            store.mark_investigate(person_id)
            self.assertIn(person_id, Store(root / "state.sqlite3").investigate_ids())
            self.assertNotIn(person_id, {person["id"] for person in app.list_people("unnamed", "most")})
            self.assertIn(person_id, {person["id"] for person in app.list_people("investigate", "most")})
            self.assertEqual([item["fileName"] for item in app.files(person_id)], ["family-photo.jpg", "holiday.png"])

            app.queue({"personId": person_id, "operation": "rename", "name": "Resolved Person"})
            self.assertNotIn(person_id, store.investigate_ids())

    def test_all_investigate_items_can_return_to_unnamed_at_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Store(root / "state.sqlite3")
            app = AppState(store, root / "reports")
            fake = FakeImmich()
            app.people = {row["id"]: dict(row, assetCount=7) for row in fake.rows}
            unnamed_id = fake.rows[0]["id"]
            other_id = "33333333-3333-4333-8333-333333333333"
            app.people[other_id] = {"id": other_id, "name": "", "isHidden": False, "assetCount": 2}
            store.mark_investigate(unnamed_id)
            store.mark_investigate(other_id)

            self.assertEqual(store.clear_investigate(), 2)
            self.assertEqual(store.investigate_ids(), set())
            self.assertEqual(
                {person["id"] for person in app.list_people("unnamed", "most")},
                {unnamed_id, other_id},
            )

    def test_face_review_returns_each_face_with_queue_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Store(root / "state.sqlite3")
            app = AppState(store, root / "reports")
            fake = FakeImmich()
            app.client = fake
            app.people = {row["id"]: dict(row, assetCount=7) for row in fake.rows}
            person_id = fake.rows[0]["id"]
            queued_face = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
            store.queue_face_detach(queued_face, person_id, "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "family-photo.jpg")

            result = app.faces_for_person(person_id)

            self.assertEqual(len(result["faces"]), 2)
            self.assertTrue(next(face for face in result["faces"] if face["faceId"] == queued_face)["queued"])
            self.assertFalse(next(face for face in result["faces"] if face["faceId"] == queued_face)["reviewed"])
            self.assertFalse(next(face for face in result["faces"] if face["faceId"] != queued_face)["queued"])

    def test_all_reviewed_faces_mark_the_person_reviewed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Store(root / "state.sqlite3")
            app = AppState(store, root / "reports")
            fake = FakeImmich()
            app.client = fake
            app.people = {row["id"]: dict(row, assetCount=2) for row in fake.rows}
            person_id = fake.rows[0]["id"]

            app.faces_for_person(person_id)

            first = app.set_face_reviewed({"personId": person_id, "faceId": "cccccccc-cccc-4ccc-8ccc-cccccccccccc", "reviewed": True})
            second = app.set_face_reviewed({"personId": person_id, "faceId": "dddddddd-dddd-4ddd-8ddd-dddddddddddd", "reviewed": True})

            self.assertFalse(first["personReviewed"])
            self.assertEqual(first["reviewedCount"], 1)
            self.assertTrue(second["personReviewed"])
            reviewed_person = next(person for person in app.list_people("review", "most") if person["id"] == person_id)
            self.assertTrue(reviewed_person["reviewed"])
            self.assertEqual(reviewed_person["reviewedCount"], 2)
            store.invalidate_face_review_inventory(person_id)
            self.assertFalse(next(person for person in app.list_people("review", "most") if person["id"] == person_id)["reviewed"])
            self.assertTrue(app.faces_for_person(person_id)["personReviewed"])

    def test_two_faces_on_one_photo_need_two_decisions(self):
        class TwoFaces(FakeImmich):
            extra_face = False

            def person_assets_page(self, person_id, page=1, size=50):
                return self.person_assets(person_id, 1), False, 1

            def faces(self, asset_id):
                first = super().faces(asset_id)[0]
                faces = [first, dict(first, id="eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")]
                if self.extra_face:
                    faces.append(dict(first, id="ffffffff-ffff-4fff-8fff-ffffffffffff"))
                return faces

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "state.sqlite3"
            store = Store(path)
            app = AppState(store, root / "reports")
            fake = TwoFaces()
            app.client = fake
            app.people = {row["id"]: dict(row, assetCount=1) for row in fake.rows}
            person_id = fake.rows[0]["id"]
            first = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
            second = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"

            loaded = app.faces_for_person(person_id)
            self.assertEqual((loaded["totalAssets"], loaded["reviewTotal"]), (1, 2))
            self.assertEqual(len(loaded["faces"]), 2)
            app.set_face_review_drafts({"personId": person_id, "items": [{
                "faceId": first, "assetId": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "fileName": "synthetic.jpg", "verdict": "correct",
            }]})
            one = app.submit_face_review({"personId": person_id})
            self.assertEqual((one["reviewedCount"], one["untouchedCount"]), (1, 1))
            self.assertFalse(one["personReviewed"])
            self.assertIn(person_id, {person["id"] for person in app.people_page("review", "most", 1, 10, hide_completed=True)["people"]})

            app.set_face_review_drafts({"personId": person_id, "items": [{
                "faceId": second, "assetId": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "fileName": "synthetic.jpg", "verdict": "correct",
            }]})
            two = app.submit_face_review({"personId": person_id})
            self.assertEqual((two["reviewedCount"], two["reviewTotal"]), (2, 2))
            self.assertTrue(two["personReviewed"])
            self.assertNotIn(person_id, {person["id"] for person in app.people_page("review", "most", 1, 10, hide_completed=True)["people"]})
            self.assertEqual(Store(path).face_review_inventory(person_id)[0], {first, second})
            restarted = AppState(Store(path), root / "reports")
            restarted.people = app.people
            self.assertTrue(next(person for person in restarted.list_people("review", "most") if person["id"] == person_id)["reviewed"])
            fake.extra_face = True
            changed = app.faces_for_person(person_id)
            self.assertEqual((changed["reviewedCount"], changed["reviewTotal"]), (2, 3))
            self.assertFalse(changed["personReviewed"])
            self.assertEqual(fake.calls, [])

    def test_review_stays_incomplete_until_last_asset_page_is_loaded(self):
        class PagedFaces(FakeImmich):
            fail_second = False

            def person_assets_page(self, person_id, page=1, size=50):
                rows = self.person_assets(person_id, 2)
                start = (page - 1) * size
                return rows[start:start + size], start + size < len(rows), len(rows)

            def faces(self, asset_id):
                if self.fail_second and asset_id == "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb":
                    raise RuntimeError("Synthetic face lookup failure")
                return super().faces(asset_id)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = AppState(Store(root / "state.sqlite3"), root / "reports")
            fake = PagedFaces()
            app.client = fake
            app.people = {row["id"]: dict(row, assetCount=2) for row in fake.rows}
            person_id = fake.rows[0]["id"]
            first = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
            second = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"

            first_page = app.faces_for_person(person_id, 1, 1)
            self.assertTrue(first_page["hasMore"])
            self.assertFalse(first_page["inventoryComplete"])
            app.set_face_reviewed({"personId": person_id, "faceId": first, "reviewed": True})
            self.assertFalse(app.face_review_progress(person_id, app.people[person_id])["personReviewed"])
            self.assertIn(person_id, {person["id"] for person in app.people_page("review", "most", 1, 10, hide_completed=True)["people"]})

            fake.fail_second = True
            with self.assertRaisesRegex(RuntimeError, "Synthetic face lookup failure"):
                app.faces_for_person(person_id, 2, 1)
            self.assertFalse(app.face_review_progress(person_id, app.people[person_id])["inventoryComplete"])
            fake.fail_second = False
            app.faces_for_person(person_id, 2, 2)
            self.assertFalse(app.face_review_progress(person_id, app.people[person_id])["inventoryComplete"])
            final_page = app.faces_for_person(person_id, 2, 1)
            self.assertTrue(final_page["inventoryComplete"])
            self.assertEqual((final_page["reviewedCount"], final_page["reviewTotal"]), (1, 2))
            app.set_face_reviewed({"personId": person_id, "faceId": second, "reviewed": True})
            self.assertTrue(app.face_review_progress(person_id, app.people[person_id])["personReviewed"])
            app.people[person_id]["updatedAt"] = "new assignment"
            self.assertFalse(app.list_people("review", "most")[0]["reviewed"])

    def test_background_face_count_resumes_and_retries_without_immich_writes(self):
        class PagedFaces(FakeImmich):
            fail_second = False

            def person_assets_page(self, person_id, page=1, size=50):
                rows = self.person_assets(person_id, 2)
                start = (page - 1) * size
                return rows[start:start + size], start + size < len(rows), len(rows)

            def faces(self, asset_id):
                if self.fail_second and asset_id == "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb":
                    raise RuntimeError("Synthetic read failure")
                return super().faces(asset_id)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Store(root / "state.sqlite3")
            app = AppState(store, root / "reports")
            fake = PagedFaces()
            app.client = fake
            person = dict(fake.rows[0], assetCount=2)
            app.people = {person["id"]: person}
            app.faces_for_person(person["id"], 1, 1)
            store.set_face_reviewed("cccccccc-cccc-4ccc-8ccc-cccccccccccc", person["id"], True)
            self.assertEqual(app.list_people("review", "most")[0]["reviewedCount"], 1)

            fake.fail_second = True
            failed = app.scan_face_review_page()
            self.assertEqual((failed["complete"], failed["failed"], failed["done"]), (0, 1, True))
            self.assertFalse(app.face_review_progress(person["id"], person)["inventoryComplete"])
            fake.fail_second = False
            counted = app.scan_face_review_page(retry_failed=True)
            self.assertEqual((counted["complete"], counted["failed"], counted["done"]), (1, 0, True))
            self.assertEqual((counted["progress"]["reviewedCount"], counted["progress"]["reviewTotal"]), (1, 2))
            self.assertEqual(fake.calls, [])

            restarted = AppState(Store(root / "state.sqlite3"), root / "reports")
            restarted.client = fake
            restarted.people = app.people
            self.assertEqual((restarted.scan_face_review_page()["complete"], restarted.scan_face_review_page()["done"]), (1, True))

    def test_reopened_review_survives_restart_and_keeps_submitted_decisions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "state.sqlite3"
            store = Store(path)
            app = AppState(store, root / "reports")
            fake = FakeImmich()
            app.client = fake
            app.people = {row["id"]: dict(row, assetCount=2) for row in fake.rows}
            person_id = fake.rows[0]["id"]
            accepted = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
            dismissed = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
            app.faces_for_person(person_id)
            app.set_face_review_drafts({"personId": person_id, "items": [
                {"faceId": accepted, "assetId": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "fileName": "a.jpg", "verdict": "correct"},
                {"faceId": dismissed, "assetId": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "fileName": "b.jpg", "verdict": "wrong"},
            ]})
            submitted = app.submit_face_review({"personId": person_id})
            self.assertTrue(submitted["personReviewed"])
            self.assertTrue(next(p for p in app.list_people("review", "most") if p["id"] == person_id)["reviewed"])
            self.assertNotIn(person_id, {p["id"] for p in app.people_page("review", "most", 1, 20, hide_completed=True)["people"]})
            self.assertTrue(app.faces_for_person(person_id)["personReviewed"])

            reopened = app.set_review_reopened({"personId": person_id, "reopened": True})
            self.assertTrue(reopened["reviewReopened"])
            self.assertFalse(reopened["personReviewed"])
            self.assertFalse(next(p for p in app.list_people("review", "most") if p["id"] == person_id)["reviewed"])
            self.assertIn(person_id, {p["id"] for p in app.people_page("review", "most", 1, 20, hide_completed=True)["people"]})
            restarted = Store(path)
            self.assertEqual(restarted.reopened_review_ids(), {person_id})
            self.assertEqual(restarted.reviewed_face_ids(person_id), {accepted})
            self.assertEqual([row["face_id"] for row in restarted.face_detaches()], [dismissed])

            finished = app.set_review_reopened({"personId": person_id, "reopened": False})
            self.assertTrue(finished["personReviewed"])
            self.assertNotIn(person_id, {p["id"] for p in app.people_page("review", "most", 1, 20, hide_completed=True)["people"]})
            self.assertFalse(restarted.reopened_review_ids())
            self.assertEqual(fake.calls, [])

    def test_reopened_review_requires_completion_and_submission_clears_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Store(root / "state.sqlite3")
            app = AppState(store, root / "reports")
            fake = FakeImmich()
            app.client = fake
            app.people = {row["id"]: dict(row, assetCount=2) for row in fake.rows}
            person_id = fake.rows[0]["id"]
            first = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
            second = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
            app.faces_for_person(person_id)
            with self.assertRaisesRegex(ToolError, "completed"):
                app.set_review_reopened({"personId": person_id, "reopened": True})
            app.set_face_reviewed({"personId": person_id, "faceId": first, "reviewed": True})
            app.set_face_reviewed({"personId": person_id, "faceId": second, "reviewed": True})
            app.set_review_reopened({"personId": person_id, "reopened": True})
            app.set_face_review_drafts({"personId": person_id, "items": [{
                "faceId": first, "assetId": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "fileName": "a.jpg", "verdict": "unreviewed",
            }]})
            with self.assertRaisesRegex(ToolError, "remaining faces"):
                app.set_review_reopened({"personId": person_id, "reopened": False})
            app.submit_face_review({"personId": person_id})
            self.assertFalse(store.reopened_review_ids())
            self.assertFalse(app.face_review_progress(person_id, app.people[person_id])["personReviewed"])
            self.assertEqual(store.reviewed_face_ids(person_id), {second})
            self.assertEqual(fake.calls, [])

    def test_bulk_review_saves_locally_and_rejects_invalid_batches(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Store(root / "state.sqlite3")
            app = AppState(store, root / "reports")
            fake = FakeImmich()
            app.client = fake
            app.people = {row["id"]: dict(row, assetCount=2) for row in fake.rows}
            person_id = fake.rows[0]["id"]
            ids = ["cccccccc-cccc-4ccc-8ccc-cccccccccccc", "dddddddd-dddd-4ddd-8ddd-dddddddddddd"]
            app.faces_for_person(person_id)

            with self.assertRaises(ToolError):
                app.set_faces_reviewed({"personId": person_id, "faceIds": ids + ["invalid"], "reviewed": True})
            self.assertEqual(store.reviewed_face_ids(person_id), set())
            with self.assertRaises(ToolError):
                app.set_faces_reviewed({"personId": person_id, "faceIds": [ids[0], ids[0]], "reviewed": True})
            self.assertEqual(store.reviewed_face_ids(person_id), set())

            result = app.set_faces_reviewed({"personId": person_id, "faceIds": ids, "reviewed": True})
            self.assertTrue(result["personReviewed"])
            self.assertEqual(store.reviewed_face_ids(person_id), set(ids))
            self.assertEqual(fake.calls, [])
            self.assertFalse(store.face_detaches())

            app.set_faces_reviewed({"personId": person_id, "faceIds": [ids[0]], "reviewed": False})
            self.assertEqual(store.reviewed_face_ids(person_id), {ids[1]})

    def test_bulk_review_progress_counts_pending_detaches(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Store(root / "state.sqlite3")
            app = AppState(store, root / "reports")
            fake = FakeImmich()
            app.client = fake
            app.people = {row["id"]: dict(row, assetCount=2) for row in fake.rows}
            person_id = fake.rows[0]["id"]
            first = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
            second = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
            app.faces_for_person(person_id)
            store.queue_face_detach(first, person_id, "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "synthetic.jpg")

            result = app.set_faces_reviewed({"personId": person_id, "faceIds": [second], "reviewed": True})
            self.assertEqual(result["reviewedCount"], 2)
            self.assertTrue(result["personReviewed"])
            self.assertEqual(fake.calls, [])

    def test_face_detach_is_queued_locally_then_synced_to_new_unnamed_person(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Store(root / "state.sqlite3")
            app = AppState(store, root / "reports")
            fake = FakeImmich()
            app.client = fake
            app.people = {row["id"]: dict(row, assetCount=7) for row in fake.rows}
            source = fake.rows[0]["id"]
            face_id = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
            asset_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"

            app.faces_for_person(source)
            self.assertIsNotNone(store.face_review_inventory(source)[1])
            app.queue_face_detach({"personId": source, "faceId": face_id, "assetId": asset_id, "fileName": "family-photo.jpg"})
            self.assertEqual(len(store.face_detaches()), 1)
            self.assertNotIn(face_id, store.reviewed_face_ids(source))
            self.assertFalse(any(call[0] == "reassign-face" for call in fake.calls))

            result = app.sync()

            target = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
            self.assertEqual(result["failed"], 0)
            self.assertIn(("create-person", target), fake.calls)
            self.assertIn(("reassign-face", target, face_id), fake.calls)
            self.assertEqual(store.face_detaches(), [])
            self.assertNotIn(face_id, store.reviewed_face_ids(source))
            self.assertIsNone(store.face_review_inventory(source)[1])

    def test_pending_person_can_be_reviewed_before_sync_and_drafts_survive_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "state.sqlite3"
            store = Store(path)
            app = AppState(store, root / "reports")
            fake = FakeImmich()
            app.client = fake
            app.people = {row["id"]: dict(row, assetCount=2) for row in fake.rows}
            source, target = (row["id"] for row in fake.rows)
            first = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
            second = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
            app.queue({"personId": source, "operation": "merge", "targetPersonId": target, "name": "Existing Person"})

            review_person = next(person for person in app.list_people("review", "most") if person["id"] == source)
            self.assertEqual(review_person["pendingOperation"], "merge")
            app.set_face_review_drafts({"personId": source, "items": [
                {"faceId": first, "assetId": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "fileName": "synthetic-a.jpg", "verdict": "wrong"},
                {"faceId": second, "assetId": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "fileName": "synthetic-b.jpg", "verdict": "correct"},
            ]})

            reopened = Store(path)
            self.assertEqual({row["verdict"] for row in reopened.face_review_drafts(source)}, {"wrong", "correct"})
            result = app.faces_for_person(source)
            self.assertEqual({face["draftVerdict"] for face in result["faces"]}, {"wrong", "correct"})
            self.assertEqual(result["draftCount"], 2)
            self.assertEqual(fake.calls, [])

            submitted = app.submit_face_review({"personId": source})
            self.assertEqual(submitted["submitted"], {"correct": 1, "wrong": 1, "unreviewed": 0})
            self.assertEqual(reopened.face_review_drafts(source), [])
            self.assertEqual(reopened.reviewed_face_ids(source), {second})
            self.assertEqual([row["face_id"] for row in reopened.face_detaches()], [first])
            self.assertEqual({row["operation"] for row in app.pending_public()}, {"merge", "detach-face"})
            self.assertEqual(fake.calls, [])

    def test_failed_or_excluded_face_correction_blocks_dependent_merge(self):
        for excluded in (False, True):
            with self.subTest(excluded=excluded), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                store = Store(root / "state.sqlite3")
                app = AppState(store, root / "reports")
                fake = FakeImmich()
                fake.reassign_fail = not excluded
                app.client = fake
                app.people = {row["id"]: dict(row, assetCount=2) for row in fake.rows}
                source, target = (row["id"] for row in fake.rows)
                face_id = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
                app.queue({"personId": source, "operation": "merge", "targetPersonId": target, "name": "Existing Person"})
                app.set_face_review_drafts({"personId": source, "items": [{
                    "faceId": face_id, "assetId": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                    "fileName": "synthetic.jpg", "verdict": "wrong",
                }]})
                app.submit_face_review({"personId": source})
                if excluded:
                    store.set_face_detach_included(face_id, False)

                first = app.sync()
                self.assertNotIn(("merge", target, source), fake.calls)
                self.assertEqual(len(store.face_detaches()), 1)
                self.assertEqual(len(store.pending()), 1)
                self.assertTrue(any(row["operation"] == "merge" and row["result"] == "failed" for row in first["results"]))

                fake.reassign_fail = False
                store.set_face_detach_included(face_id, True)
                second = app.sync()
                self.assertEqual(second["failed"], 0)
                self.assertLess(
                    fake.calls.index(("reassign-face", "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee", face_id)),
                    fake.calls.index(("merge", target, source)),
                )
                self.assertEqual(store.face_detaches(), [])
                self.assertEqual(store.pending(), [])

    def test_unsubmitted_review_blocks_person_change_and_stale_face_stays_draft(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Store(root / "state.sqlite3")
            app = AppState(store, root / "reports")
            fake = FakeImmich()
            app.client = fake
            app.people = {row["id"]: dict(row, assetCount=2) for row in fake.rows}
            source = fake.rows[0]["id"]
            app.queue({"personId": source, "operation": "rename", "name": "Synthetic Person"})
            app.set_face_review_drafts({"personId": source, "items": [{
                "faceId": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
                "assetId": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "fileName": "synthetic.jpg", "verdict": "wrong",
            }]})
            result = app.sync()
            self.assertEqual(result["failed"], 1)
            self.assertFalse(any(call[0] == "update" for call in fake.calls))

            with mock.patch.object(fake, "faces", return_value=[]):
                with self.assertRaisesRegex(ToolError, "no longer assigned"):
                    app.submit_face_review({"personId": source})
            self.assertEqual(len(store.face_review_drafts(source)), 1)
            self.assertEqual(store.face_detaches(), [])

            app.submit_face_review({"personId": source})
            synced = app.sync()
            self.assertEqual(synced["failed"], 0)
            self.assertTrue(any(call[0] == "update" for call in fake.calls))

    def test_concurrent_sync_rejects_second_request_and_detaches_once(self):
        class BlockingImmich(FakeImmich):
            def __init__(self):
                super().__init__()
                self.creating = threading.Event()
                self.continue_creating = threading.Event()

            def create_person(self):
                self.creating.set()
                if not self.continue_creating.wait(5):
                    raise RuntimeError("Synthetic create-person timeout")
                return super().create_person()

        class QuietHandler(Handler):
            def log_message(self, *_args):
                pass

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Store(root / "state.sqlite3")
            app = AppState(store, root / "reports")
            fake = BlockingImmich()
            app.client = fake
            app.people = {row["id"]: dict(row, assetCount=7) for row in fake.rows}
            face_id = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
            app.queue_face_detach({
                "personId": fake.rows[0]["id"], "faceId": face_id,
                "assetId": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            })
            auth = AdminSession(app)
            auth.cookie = "synthetic-session"
            auth.expires = time.monotonic() + 1000
            auth.checked = time.monotonic()
            with mock.patch.object(workbench, "STATE", app, create=True), mock.patch.object(workbench, "AUTH", auth, create=True):
                server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
                serving = threading.Thread(target=server.serve_forever, daemon=True)
                serving.start()
                base = f"http://127.0.0.1:{server.server_port}"

                def post_sync():
                    request = urllib.request.Request(
                        base + "/api/sync", data=json.dumps({"confirmation":"SYNC", "reviewToken":app.pending_data()["reviewToken"]}).encode(), method="POST",
                        headers={"Content-Type": "application/json", "Origin": base,
                                 "Cookie": "people_workbench_session=synthetic-session"},
                    )
                    with urllib.request.urlopen(request, timeout=5) as response:
                        return json.load(response)

                first_result = []
                first_error = []

                def run_first():
                    try:
                        first_result.append(post_sync())
                    except Exception as exc:
                        first_error.append(exc)

                first = threading.Thread(target=run_first)
                first.start()
                try:
                    self.assertTrue(fake.creating.wait(5))
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        post_sync()
                    self.assertEqual(error.exception.code, 409)
                    self.assertEqual(json.load(error.exception)["error"], "Sync already in progress")
                finally:
                    fake.continue_creating.set()
                    first.join(5)
                    server.shutdown()
                    server.server_close()
                    serving.join(5)

                self.assertFalse(first.is_alive())
                self.assertFalse(first_error)
                self.assertEqual(first_result[0]["failed"], 0)
                self.assertEqual(len([call for call in fake.calls if call[0] == "create-person"]), 1)
                self.assertEqual(len([call for call in fake.calls if call[0] == "reassign-face"]), 1)
                self.assertEqual(store.face_detaches(), [])

    def test_failed_face_detach_reuses_the_created_unnamed_person_on_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Store(root / "state.sqlite3")
            app = AppState(store, root / "reports")
            fake = FakeImmich()
            fake.reassign_fail = True
            app.client = fake
            app.people = {row["id"]: dict(row, assetCount=7) for row in fake.rows}
            source = fake.rows[0]["id"]
            face_id = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
            asset_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
            app.queue_face_detach({"personId": source, "faceId": face_id, "assetId": asset_id})

            first = app.sync()
            queued = store.face_detaches()[0]
            self.assertEqual(first["failed"], 1)
            self.assertEqual(queued["target_person_id"], "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")

            fake.reassign_fail = False
            second = app.sync()
            self.assertEqual(second["failed"], 0)
            self.assertEqual(len([call for call in fake.calls if call[0] == "create-person"]), 1)
            self.assertEqual(store.face_detaches(), [])

    def test_similar_people_keeps_immich_order_and_only_available_unnamed_clusters(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Store(root / "state.sqlite3")
            app = AppState(store, root / "reports")
            fake = FakeImmich()
            extra = {"id": "33333333-3333-4333-8333-333333333333", "name": "", "isHidden": False}
            fake.rows.append(extra)
            app.client = fake
            app.people = {row["id"]: dict(row, assetCount=7) for row in fake.rows}
            anchor = fake.rows[0]["id"]

            result = app.similar_people(anchor, 10)

            self.assertEqual([person["id"] for person in result], [extra["id"]])

    def test_similarity_graph_uses_rank_weights_and_mutual_neighbors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Store(root / "state.sqlite3")
            app = AppState(store, root / "reports")
            fake = FakeImmich()
            first = fake.rows[0]["id"]
            second = "33333333-3333-4333-8333-333333333333"
            third = "44444444-4444-4444-8444-444444444444"
            named = fake.rows[1]["id"]
            fake.rows.extend([
                {"id": second, "name": "", "isHidden": False},
                {"id": third, "name": "", "isHidden": False},
            ])
            rankings = {
                first: [second, third, named],
                second: [first, third, named],
                third: [first, second, named],
            }
            by_id = {row["id"]: row for row in fake.rows}
            fake.closest_people = lambda person_id, limit=20: [by_id[value] for value in rankings.get(person_id, [])][:limit]
            app.client = fake
            app.people = {row["id"]: dict(row, assetCount=7) for row in fake.rows}

            graph = app.similarity_graph(3)

            self.assertEqual(set(graph["nodes"]), {first, second, third})
            self.assertEqual(graph["failed"], 0)
            edges = {(edge["source"], edge["target"]): edge for edge in graph["edges"]}
            first_second = edges[tuple(sorted((first, second)))]
            self.assertTrue(first_second["mutual"])
            self.assertEqual(first_second["weight"], 2.0)
            self.assertNotIn(named, {value for edge in graph["edges"] for value in (edge["source"], edge["target"])})


class GroupPhotoTests(unittest.TestCase):
    FIRST = "11111111-1111-4111-8111-111111111111"
    SECOND = "33333333-3333-4333-8333-333333333333"
    NAMED = "22222222-2222-4222-8222-222222222222"
    IGNORED = "44444444-4444-4444-8444-444444444444"
    PHOTO = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    ARCHIVE = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.path = root / "state.sqlite3"
        self.store = Store(self.path)
        self.app = AppState(self.store, root / "reports")
        self.app.phase = "ready"
        self.app.people = {
            self.FIRST: {"id": self.FIRST, "name": "", "isHidden": False},
            self.SECOND: {"id": self.SECOND, "name": "", "isHidden": False},
            self.NAMED: {"id": self.NAMED, "name": "Known Person", "isHidden": False},
            self.IGNORED: {"id": self.IGNORED, "name": "", "isHidden": True},
        }
        owner = self

        class GroupFake:
            def __init__(self):
                self.calls = []
                self.failed = False
                self.hold = None
                self.hold_search = None
                self.assets = [
                    {"id": owner.PHOTO, "type": "IMAGE", "visibility": "timeline", "updatedAt": "2026-01-02", "originalFileName": "group-one.jpg"},
                    {"id": owner.ARCHIVE, "type": "IMAGE", "visibility": "archive", "updatedAt": "2026-01-01", "originalFileName": "group-two.jpg"},
                ]
                self.faces_by_asset = {
                    owner.PHOTO: [self.face(f"face-{index}", person) for index, person in enumerate([owner.FIRST, owner.FIRST, owner.SECOND, owner.NAMED, owner.IGNORED, None])],
                    owner.ARCHIVE: [self.face(f"arch-{index}", person) for index, person in enumerate([owner.SECOND, owner.NAMED, None, owner.IGNORED, owner.NAMED])],
                }

            @staticmethod
            def face(face_id, person):
                return {"id": face_id, "person": {"id": person} if person else None,
                        "boundingBoxX1": 10, "boundingBoxY1": 15, "boundingBoxX2": 60, "boundingBoxY2": 75,
                        "imageWidth": 200, "imageHeight": 150}

            def person_assets_page(self, person_id, page=1, size=100, *, group_photos=False):
                self.calls.append(("search", person_id, group_photos))
                if self.hold_search:
                    self.hold_search.wait(2)
                return (self.assets if page == 1 else []), False, len(self.assets)

            def faces(self, asset_id):
                self.calls.append(("faces", asset_id))
                if self.hold and asset_id == owner.ARCHIVE:
                    self.hold.wait(2)
                if self.failed and asset_id == owner.ARCHIVE:
                    raise RuntimeError("synthetic scan failure")
                return self.faces_by_asset[asset_id]

            def asset(self, asset_id):
                self.calls.append(("asset", asset_id))
                return next(asset for asset in self.assets if asset["id"] == asset_id)

            def update_person(self, person_id, payload):
                self.calls.append(("update", person_id, payload))
                owner.app.people[person_id].update(payload)

            def all_people(self):
                return list(owner.app.people.values())

            def statistics(self, _person_id):
                return 2

        self.fake = GroupFake()
        self.app.client = self.fake

    def wait_scan(self):
        for _ in range(300):
            with self.app.lock:
                if self.app.group_complete:
                    return
            time.sleep(.01)
        self.fail("Group photo scan did not finish")

    def test_group_asset_rules_and_ordering(self):
        self.assertTrue(group_asset_allowed({"type": "IMAGE", "visibility": "archive"}, True))
        self.assertFalse(group_asset_allowed({"type": "IMAGE", "visibility": "archive"}, False))
        self.assertTrue(group_asset_allowed({"type": "IMAGE", "visibility": "timeline", "isArchived": True}, True))
        self.assertFalse(group_asset_allowed({"type": "IMAGE", "visibility": "timeline", "isArchived": True}, False))
        self.assertFalse(group_asset_allowed({"type": "IMAGE", "visibility": "hidden", "isArchived": True}, True))
        for asset in ({"type": "VIDEO"}, {"visibility": "hidden"}, {"visibility": "locked"}, {"deletedAt": "now"}, {"isTrashed": True}):
            self.assertFalse(group_asset_allowed(asset, True))
        rows = [{"id": "a", "unnamedCount": 2, "updatedAt": "2026-01-01"}, {"id": "b", "unnamedCount": 1, "updatedAt": "2026-01-02"}]
        self.assertEqual([row["id"] for row in sort_group_assets(rows, "most")], ["a", "b"])
        self.assertEqual([row["id"] for row in sort_group_assets(rows, "fewest")], ["b", "a"])
        self.assertEqual([row["id"] for row in sort_group_assets(rows, "updated-newest")], ["b", "a"])
        self.assertEqual([row["id"] for row in sort_group_assets(rows, "updated-oldest")], ["a", "b"])
        self.assertEqual(sort_group_assets(rows, "random"), sort_group_assets(rows, "random"))

    def test_scan_explains_why_found_photos_are_not_eligible(self):
        self.fake.assets.append({"id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc", "type": "VIDEO", "visibility": "timeline"})
        self.fake.faces_by_asset[self.PHOTO] = self.fake.faces_by_asset[self.PHOTO][:4]
        self.fake.faces_by_asset[self.ARCHIVE] = self.fake.faces_by_asset[self.ARCHIVE][:4]
        self.app.start_group_scan(); self.wait_scan()
        scan = self.app.group_photo_state()["scan"]
        self.assertEqual(scan["sources"], 2)
        self.assertEqual(scan["discovered"], 3)
        self.assertEqual(scan["filtered"], 1)
        self.assertEqual(scan["examined"], 2)
        self.assertEqual(scan["belowMinimum"], 2)
        self.assertEqual(scan["indexed"], 0)
        self.assertTrue(self.app.group_photo_state()["checkpoint"])

    def test_changing_discovery_settings_returns_to_unseen_pass(self):
        self.store.group_advance(self.PHOTO, "deferred", [])
        self.store.start_group_deferred()
        self.assertEqual(self.store.group_progress()[self.PHOTO], "ready")
        self.app.set_group_settings({"minFaces": 6, "includeArchived": True, "order": "most"})
        self.assertEqual(self.store.setting("group_pass"), "initial")
        self.assertEqual(self.store.group_progress()[self.PHOTO], "deferred")
        for minimum in (1, 21):
            with self.assertRaisesRegex(ToolError, "Invalid Group photos settings"):
                self.app.set_group_settings({"minFaces": minimum, "includeArchived": True, "order": "most"})

    def test_discovery_counts_all_faces_including_investigate_and_publishes_early(self):
        self.store.mark_investigate(self.FIRST)
        self.fake.hold = threading.Event()
        self.app.start_group_scan()
        for _ in range(200):
            with self.app.lock:
                if self.PHOTO in self.app.group_candidates:
                    break
            time.sleep(.01)
        with self.app.lock:
            self.assertIn(self.PHOTO, self.app.group_candidates)
            self.assertTrue(self.app.group_scanning)
        self.assertEqual(self.app.group_photo_state()["photo"]["id"], self.PHOTO)
        self.fake.hold.set(); self.wait_scan()
        state = self.app.group_photo_state()
        self.assertEqual(state["photo"]["id"], self.PHOTO)
        self.assertEqual(state["photo"]["faceCount"], 6)
        self.assertEqual(state["photo"]["unnamedCount"], 3)
        self.assertEqual([face["status"] for face in state["photo"]["faces"]], ["actionable", "actionable", "actionable", "named", "ignored", "unassigned"])
        self.assertEqual(set(self.app.group_candidates), {self.PHOTO, self.ARCHIVE})
        self.assertTrue(all(call[2] for call in self.fake.calls if call[0] == "search"))
        self.assertEqual(sum(call == ("faces", self.PHOTO) for call in self.fake.calls[:4]), 1)

    def test_saved_current_photo_appears_before_search_finishes(self):
        self.store.set_setting("group_current", self.PHOTO)
        self.fake.hold_search = threading.Event()
        self.app.start_group_scan()
        for _ in range(200):
            with self.app.lock:
                if self.PHOTO in self.app.group_candidates:
                    break
            time.sleep(.01)
        with self.app.lock:
            self.assertIn(self.PHOTO, self.app.group_candidates)
            self.assertTrue(self.app.group_scanning)
        self.assertEqual(self.app.group_photo_state()["photo"]["id"], self.PHOTO)
        self.fake.hold_search.set(); self.wait_scan()
        self.assertEqual(sum(call == ("faces", self.PHOTO) for call in self.fake.calls), 2)

    def test_zero_partial_full_deferred_pass_and_persistence(self):
        self.app.start_group_scan(); self.wait_scan()
        first = self.app.group_photo_state()["photo"]
        self.assertEqual(first["id"], self.PHOTO)
        self.app.save_group_draft({"assetId": self.PHOTO, "personId": self.FIRST, "name": "Draft Name"})
        reopened = AppState(Store(self.path), Path(self.directory.name) / "reports")
        reopened.client = self.fake; reopened.phase = "ready"; reopened.people = self.app.people
        reopened.start_group_scan();
        for _ in range(300):
            with reopened.lock:
                if reopened.group_complete:
                    break
            time.sleep(.01)
        self.assertEqual(reopened.group_photo_state()["photo"]["drafts"][0]["name"], "Draft Name")
        self.assertEqual(reopened.advance_group_photo({"assetId": self.PHOTO, "decisions": []})["status"], "deferred")
        self.assertEqual(self.store.pending(), [])
        self.assertEqual(reopened.group_photo_state()["photo"]["id"], self.ARCHIVE)
        self.assertEqual(reopened.advance_group_photo({"assetId": self.ARCHIVE, "decisions": [{"personId": self.SECOND, "name": "Another Name"}]})["status"], "done")
        self.assertEqual(reopened.group_photo_state()["checkpoint"], True)
        self.assertEqual(reopened.group_photo_state()["deferred"], 1)
        reopened.start_group_deferred()
        self.assertEqual(reopened.group_photo_state()["photo"]["id"], self.PHOTO)
        self.assertEqual(reopened.advance_group_photo({"assetId": self.PHOTO, "decisions": [{"personId": self.FIRST, "name": "New Name"}]})["status"], "done")
        self.assertEqual(len(self.store.pending()), 2)
        self.assertEqual(reopened.group_photo_state()["checkpoint"], True)
        self.assertEqual(reopened.group_photo_state()["deferred"], 0)
        self.assertEqual(self.fake.calls.count(("update", self.FIRST)), 0)

    def test_partial_submission_queues_name_and_defers_remaining_cluster(self):
        self.app.start_group_scan(); self.wait_scan(); self.app.group_photo_state()
        result = self.app.advance_group_photo({"assetId": self.PHOTO, "decisions": [{"personId": self.FIRST, "name": "New Name"}]})
        self.assertEqual(result, {"queued": 1, "status": "deferred"})
        self.assertEqual(self.store.group_progress()[self.PHOTO], "deferred")
        self.assertEqual([row["person_id"] for row in self.store.pending()], [self.FIRST])
        self.assertEqual(self.app.pending_public()[0]["name"], "New Name")
        self.assertFalse(any(call[0] == "update" for call in self.fake.calls))
        result = self.app.sync()
        self.assertEqual(result["failed"], 0)
        self.assertIn(("update", self.FIRST, {"name": "New Name"}), self.fake.calls)

    def test_batch_validates_every_decision_and_queues_atomically(self):
        self.app.start_group_scan(); self.wait_scan(); self.app.group_photo_state()
        bad = [{"personId": self.FIRST, "name": "One"}, {"personId": self.NAMED, "name": "Wrong"}]
        with self.assertRaisesRegex(ToolError, "Stale"):
            self.app.advance_group_photo({"assetId": self.PHOTO, "decisions": bad})
        self.assertEqual(self.store.pending(), [])
        self.assertEqual(self.store.group_progress(), {})
        with self.assertRaisesRegex(ToolError, "Photo changed"):
            self.app.advance_group_photo({"assetId": self.ARCHIVE, "decisions": []})
        result = self.app.advance_group_photo({"assetId": self.PHOTO, "decisions": [
            {"personId": self.FIRST, "name": "New Person"},
            {"personId": self.SECOND, "name": "Known Person", "targetPersonId": self.NAMED},
        ]})
        self.assertEqual(result, {"queued": 2, "status": "done"})
        self.assertEqual({row["operation"] for row in self.store.pending()}, {"rename", "merge"})
        self.assertEqual(self.store.group_progress()[self.PHOTO], "done")
        self.assertFalse(any(call[0] in {"update", "merge"} for call in self.fake.calls))

    def test_ignore_suggestion_draft_and_batch_stay_pending_until_sync(self):
        self.app.start_group_scan(); self.wait_scan(); self.app.group_photo_state()
        self.app.save_group_draft({"assetId": self.PHOTO, "personId": self.FIRST,
                                   "name": "Ignore", "operation": "hide", "targetPersonId": None})
        saved = Store(self.path).group_drafts(self.PHOTO)
        self.assertEqual(saved[0]["operation"], "hide")
        with self.assertRaisesRegex(ToolError, "Invalid ignore decision"):
            self.app.advance_group_photo({"assetId": self.PHOTO, "decisions": [
                {"personId": self.FIRST, "operation": "hide", "name": "Ignore"}]})
        self.assertEqual(self.store.pending(), [])
        result = self.app.advance_group_photo({"assetId": self.PHOTO, "decisions": [
            {"personId": self.FIRST, "operation": "hide", "name": ""},
            {"personId": self.SECOND, "operation": "rename", "name": "Another Person"},
        ]})
        self.assertEqual(result, {"queued": 2, "status": "done"})
        self.assertEqual({row["person_id"]: row["operation"] for row in self.store.pending()},
                         {self.FIRST: "hide", self.SECOND: "rename"})
        self.assertFalse(any(call[0] == "update" for call in self.fake.calls))
        self.assertEqual(self.app.sync()["failed"], 0)
        self.assertIn(("update", self.FIRST, {"isHidden": True}), self.fake.calls)

    def test_literal_ignore_name_remains_a_rename(self):
        self.app.start_group_scan(); self.wait_scan(); self.app.group_photo_state()
        self.app.advance_group_photo({"assetId": self.PHOTO, "decisions": [
            {"personId": self.FIRST, "operation": "rename", "name": "Ignore"}]})
        queued = self.store.pending()[0]
        self.assertEqual((queued["operation"], queued["name"]), ("rename", "Ignore"))

    def test_existing_group_drafts_migrate_to_rename_operation(self):
        legacy_path = Path(self.directory.name) / "legacy.sqlite3"
        with sqlite3.connect(legacy_path) as db:
            db.execute("CREATE TABLE group_photo_drafts (asset_id TEXT NOT NULL, person_id TEXT NOT NULL, name TEXT NOT NULL, target_person_id TEXT, PRIMARY KEY(asset_id,person_id))")
            db.execute("INSERT INTO group_photo_drafts VALUES(?,?,?,NULL)", (self.PHOTO, self.FIRST, "Old Draft"))
        migrated = Store(legacy_path)
        self.assertEqual(migrated.group_drafts(self.PHOTO)[0]["operation"], "rename")

    def test_failed_progress_write_rolls_back_pending_batch(self):
        self.app.start_group_scan(); self.wait_scan(); self.app.group_photo_state()
        with self.store.connect() as db:
            db.execute("CREATE TRIGGER fail_group_progress BEFORE INSERT ON group_photo_progress BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END")
        with self.assertRaisesRegex(Exception, "synthetic failure"):
            self.app.advance_group_photo({"assetId": self.PHOTO, "decisions": [{"personId": self.FIRST, "name": "New Name"}]})
        self.assertEqual(self.store.pending(), [])
        self.assertEqual(self.store.setting("group_current"), self.PHOTO)

    def test_archive_setting_stale_eligibility_and_retry_failure(self):
        self.app.set_group_settings({"minFaces": 5, "includeArchived": False, "order": "fewest"})
        self.assertEqual(self.app.group_settings()["order"], "fewest")
        self.assertEqual(self.store.setting("sort", "most"), "most")
        self.app.start_group_scan(); self.wait_scan()
        self.assertEqual(set(self.app.group_candidates), {self.PHOTO})
        self.app.set_group_settings({"minFaces": 5, "includeArchived": True, "order": "most"})
        self.assertEqual(Store(self.path).setting("group_archived"), "true")
        self.fake.failed = True
        self.app.start_group_scan(); self.wait_scan()
        data = self.app.group_photo_state()
        self.assertGreater(data["scan"]["failures"], 0)
        self.app.advance_group_photo({"assetId": self.PHOTO, "decisions": []})
        self.assertFalse(self.app.group_photo_state()["checkpoint"])
        self.fake.failed = False
        self.app.start_group_scan(retry=True); self.wait_scan()
        self.assertEqual(self.app.group_photo_state()["photo"]["id"], self.ARCHIVE)
        self.store.upsert_pending({"personId": self.SECOND, "operation": "rename", "name": "Queued Elsewhere"})
        self.assertTrue(self.app.group_photo_state()["checkpoint"])
        self.assertEqual(self.store.group_progress()[self.ARCHIVE], "done")

    def test_photo_visibility_is_rechecked_before_display_and_submission(self):
        self.app.start_group_scan(); self.wait_scan()
        self.assertEqual(self.app.group_photo_state()["photo"]["id"], self.PHOTO)
        self.fake.assets[0]["visibility"] = "hidden"
        with self.assertRaisesRegex(ToolError, "no longer eligible"):
            self.app.advance_group_photo({"assetId": self.PHOTO, "decisions": [{"personId": self.FIRST, "name": "New Name"}]})
        self.assertEqual(self.store.pending(), [])
        self.assertEqual(self.app.group_photo_state()["photo"]["id"], self.ARCHIVE)
        self.assertEqual(self.store.group_progress()[self.PHOTO], "done")

    def test_discarding_pending_reopens_group_photos_and_keeps_settings(self):
        self.app.set_group_settings({"minFaces": 6, "includeArchived": True, "order": "fewest"})
        self.app.start_group_scan(); self.wait_scan(); self.app.group_photo_state()
        self.app.advance_group_photo({"assetId": self.PHOTO, "decisions": [{"personId": self.FIRST, "name": "New Name"}]})
        self.assertEqual(self.app.discard_pending(), 1)
        self.assertEqual(self.store.group_progress(), {})
        self.assertEqual(self.app.group_settings(), {"minFaces": 6, "includeArchived": True, "order": "fewest"})

    def test_returned_pending_name_reopens_affected_photo_without_clearing_other_progress(self):
        self.app.start_group_scan(); self.wait_scan(); self.app.group_photo_state()
        self.app.advance_group_photo({"assetId": self.PHOTO, "decisions": [
            {"personId": self.FIRST, "name": "First Name"},
            {"personId": self.SECOND, "name": "Second Name"},
        ]})
        unrelated = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
        self.store.group_advance(unrelated, "done", [])
        self.store.remove_pending(self.FIRST)
        self.app.invalidate_group_scan()
        self.app.start_group_scan(); self.wait_scan()
        self.assertEqual(self.app.group_photo_state()["photo"]["id"], self.PHOTO)
        self.assertNotIn(self.PHOTO, self.store.group_progress())
        self.assertEqual(self.store.group_progress()[unrelated], "done")

    def test_resolved_deferred_photo_does_not_leave_an_endless_checkpoint(self):
        self.app.start_group_scan(); self.wait_scan(); self.app.group_photo_state()
        self.app.advance_group_photo({"assetId": self.PHOTO, "decisions": []})
        self.store.upsert_pending_many([
            {"personId": self.FIRST, "operation": "rename", "name": "First Name"},
            {"personId": self.SECOND, "operation": "rename", "name": "Second Name"},
        ])
        self.app.invalidate_group_scan()
        self.app.start_group_scan(); self.wait_scan()
        state = self.app.group_photo_state()
        self.assertTrue(state["checkpoint"])
        self.assertEqual(state["deferred"], 0)
        self.assertEqual(self.store.group_progress()[self.PHOTO], "done")


class ReleaseSafetyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.store = Store(self.root / "state.sqlite3")
        self.app = AppState(self.store, self.root / "reports")
        self.fake = FakeImmich()
        self.app.client = self.fake
        self.app.people = {row["id"]: {**row, "assetCount": 7} for row in self.fake.rows}
        self.source, self.target = [row["id"] for row in self.fake.rows]

    def queue_merge(self, feature=None):
        self.app.queue({"personId": self.source, "operation": "merge", "targetPersonId": self.target,
                        "name": "Existing Person", "featureAssetId": feature})

    def transport(self, response, version="3.3.1"):
        client = Immich("http://synthetic.invalid", "synthetic")
        client.server_version = version
        client.json = mock.Mock(return_value=response)
        return client

    def test_merge_validates_source_target_and_legacy_contract(self):
        source = {"id": self.source, "success": True}
        target = {"id": self.target, "success": True}
        for version, response in [("3.3.1", [target, source]), ("2.0.0", [source]),
                                  ("2.0.0", [source, target]), ("3.0.0", [source]),
                                  ("3.2.0", [source]), ("3.2.1", [source]), ("3.3.0", [target, source])]:
            with self.subTest(version=version, response=response):
                self.transport(response, version).merge(self.target, self.source)
        invalid = [None, {}, [], [target], [source], [source, source],
                   [source, {"id": "unrelated", "success": True}],
                   [target, {"id": self.source, "success": "true"}],
                   [target, {"id": self.source, "success": 1}],
                   [target, {"id": self.source, "success": True, "error": "UNKNOWN"}],
                   [target, {"id": self.source, "success": False, "error": "UNKNOWN"}],
                   [source, {"id": self.target, "success": False, "error": "NO_PERMISSION"}],
                   [target, source, {"id": "other", "success": True}],
                   [target, {"id": [], "success": True}]]
        for response in invalid:
            with self.subTest(response=response), self.assertRaises(ToolError):
                self.transport(response).merge(self.target, self.source)
        with self.assertRaises(ToolError):
            self.transport([source, {"id": self.target, "success": False, "error": "NO_PERMISSION"}], "2.0.0").merge(self.target, self.source)

    def test_failed_merge_response_stays_pending_and_can_retry_definite_refusal(self):
        self.queue_merge()
        client = self.transport([{"id": self.target, "success": True},
                                 {"id": self.source, "success": False, "error": "NO_PERMISSION"}])
        self.fake.merge = client.merge
        first = self.app.sync()
        self.assertEqual(first["failed"], 1)
        self.assertEqual(self.store.pending()[0]["merge_step"], "ready")
        self.assertIn("NO_PERMISSION", self.store.pending()[0]["last_error"])
        client.json.return_value = [{"id": self.target, "success": True}, {"id": self.source, "success": True}]
        self.assertEqual(self.app.sync()["failed"], 0)
        self.assertEqual(self.store.pending(), [])

    def test_uncertain_merge_is_not_replayed_after_restart(self):
        for response in [[], [{"id": self.target, "success": True},
                               {"id": self.source, "success": False, "error": "UNKNOWN"}], None]:
            with self.subTest(response=response):
                self.store.clear_pending(); self.queue_merge()
                client = self.transport(response)
                if response is None:
                    client.json.side_effect = TimeoutError("synthetic lost reply")
                self.fake.merge = client.merge
                self.assertEqual(self.app.sync()["failed"], 1)
                self.assertEqual(self.store.pending()[0]["merge_step"], "uncertain")
                restarted = AppState(Store(self.store.path), self.root / "reports")
                restarted.client = self.fake; restarted.people = self.app.people.copy()
                self.assertEqual(restarted.sync()["failed"], 1)
                self.assertEqual(client.json.call_count, 1)
                self.assertEqual(restarted.pending_public()[0]["mergeStep"], "uncertain")
                with self.assertRaises(ToolError):
                    restarted.queue({"personId": self.source, "operation": "rename", "name": "Changed"})

    def test_not_found_or_failed_target_is_uncertain_and_never_replayed(self):
        responses = [
            [{"id": self.target, "success": True}, {"id": self.source, "success": False, "error": "NOT_FOUND"}],
            [{"id": self.target, "success": False, "error": "NOT_FOUND"}, {"id": self.source, "success": True}],
            [{"id": self.target, "success": False, "error": "NO_PERMISSION"}, {"id": self.source, "success": True}],
            [{"id": self.target, "success": False, "error": "NO_PERMISSION"}, {"id": self.source, "success": False, "error": "NO_PERMISSION"}],
        ]
        for response in responses:
            with self.subTest(response=response):
                self.store.clear_pending(); self.queue_merge()
                client = self.transport(response); self.fake.merge = client.merge
                self.assertEqual(self.app.sync()["failed"], 1)
                self.assertEqual(self.store.pending()[0]["merge_step"], "uncertain")
                self.assertEqual(self.app.sync()["failed"], 1)
                self.assertEqual(client.json.call_count, 1)
        legacy = self.transport([{"id": self.source, "success": False, "error": "NO_PERMISSION"}], "2.0.0")
        with self.assertRaises(workbench.MergeRejected): legacy.merge(self.target, self.source)

    def test_crash_checkpoint_requires_reconciliation(self):
        self.queue_merge()
        self.store.set_merge_step(self.source, "inflight")
        restarted = AppState(Store(self.store.path), self.root / "reports")
        restarted.client = self.fake; restarted.people = self.app.people.copy()
        self.assertIn("uncertain", restarted.pending_public()[0]["lastError"])
        self.assertEqual(restarted.sync()["failed"], 1)
        self.assertEqual(restarted.store.pending()[0]["merge_step"], "uncertain")
        self.assertFalse(self.fake.calls)

    def test_confirmed_merge_retries_only_photo_step_after_restart(self):
        self.queue_merge("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
        original_merge = self.fake.merge
        def merge(target, source):
            original_merge(target, source)
            self.fake.rows = [row for row in self.fake.rows if row["id"] != source]
        self.fake.merge = merge
        self.fake.update_person = mock.Mock(side_effect=ToolError("synthetic photo error"))
        self.assertEqual(self.app.sync()["failed"], 1)
        self.assertEqual(self.store.pending()[0]["merge_step"], "complete")
        self.assertNotIn(self.source, self.app.people)
        self.assertIn("Merge completed", self.store.pending()[0]["last_error"])
        self.store.set_included(self.source, False)
        self.store.set_included(self.source, True)
        self.assertEqual(self.store.pending()[0]["merge_step"], "complete")
        with self.assertRaises(ToolError):
            self.store.upsert_pending({"personId": self.source, "operation": "rename", "name": "Different"})
        restarted = AppState(Store(self.store.path), self.root / "reports")
        restarted.client = self.fake; restarted.people = self.app.people.copy()
        self.fake.update_person.side_effect = None
        self.assertEqual(restarted.sync()["failed"], 0)
        self.assertEqual(len([call for call in self.fake.calls if call[0] == "merge"]), 1)
        self.assertEqual(restarted.store.pending(), [])

    def test_legacy_pending_schema_migrates_without_changing_operation(self):
        legacy = self.root / "legacy.sqlite3"
        with sqlite3.connect(legacy) as db:
            db.execute("CREATE TABLE pending(person_id TEXT PRIMARY KEY, operation TEXT, name TEXT DEFAULT '', target_person_id TEXT, target_name TEXT DEFAULT '', feature_asset_id TEXT, included INTEGER DEFAULT 1, last_error TEXT DEFAULT '', created_at TEXT, updated_at TEXT)")
            db.execute("INSERT INTO pending(person_id,operation,created_at,updated_at) VALUES(?,?,?,?)", (self.source,"hide","old","old"))
        store = Store(legacy)
        self.assertEqual(store.pending()[0]["operation"], "hide")
        self.assertEqual(store.pending()[0]["merge_step"], "ready")

    def test_review_token_covers_all_pages_payloads_and_both_queues(self):
        self.queue_merge()
        first = self.app.pending_page(1, 1)
        self.assertEqual(first["reviewToken"], self.app.pending_data()["reviewToken"])
        self.store.queue_face_detach("cccccccc-cccc-4ccc-8ccc-cccccccccccc", self.source,
                                     "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "synthetic.jpg")
        second = self.app.pending_page(2, 1)
        self.assertNotEqual(first["reviewToken"], second["reviewToken"])
        self.assertEqual(second["reviewToken"], self.app.pending_page(1, 1)["reviewToken"])
        token = second["reviewToken"]
        self.store.set_face_detach_included("cccccccc-cccc-4ccc-8ccc-cccccccccccc", False)
        self.assertNotEqual(token, self.app.pending_data()["reviewToken"])
        token = self.app.pending_data()["reviewToken"]
        self.store.upsert_pending({"personId": self.source, "operation": "rename", "name": "New payload"})
        self.assertNotEqual(token, self.app.pending_data()["reviewToken"])
        token = self.app.pending_data()["reviewToken"]
        self.store.set_merge_step(self.source, "uncertain")
        self.assertNotEqual(token, self.app.pending_data()["reviewToken"])
        self.assertFalse(self.fake.calls)

    def test_review_token_rejects_late_include_and_second_client_queue_before_write(self):
        self.app.queue({"personId": self.source, "operation": "rename", "name": "Original"})
        self.store.set_included(self.source, False)
        token = self.app.pending_data()["reviewToken"]
        # An ambiguous failed reply may be followed by a stale read, then the
        # delayed original include commits. The stale token cannot authorize it.
        self.store.set_included(self.source, True)
        with self.assertRaisesRegex(workbench.SyncInProgress, "Pending changed"):
            self.app.sync(token)
        self.assertFalse(self.fake.calls)
        token = self.app.pending_data()["reviewToken"]
        self.app.queue({"personId": self.target, "operation": "hide"})
        with self.assertRaisesRegex(workbench.SyncInProgress, "Pending changed"):
            self.app.sync(token)
        self.assertFalse(self.fake.calls)
        self.assertEqual(self.app.sync(self.app.pending_data()["reviewToken"])["failed"], 0)

    def test_pending_token_and_visible_data_use_one_sqlite_snapshot(self):
        self.app.queue({"personId": self.source, "operation": "hide"})
        face = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
        self.store.queue_face_detach(face, self.source, "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "synthetic.jpg")
        before_rows, before_faces = self.store.pending_snapshot()
        original_connect = self.store.connect
        store = self.store
        class InterleavedConnection:
            def __init__(self): self.db = original_connect()
            def __enter__(self): self.db.__enter__(); return self
            def __exit__(self, *args): return self.db.__exit__(*args)
            def execute(self, sql, *args):
                if "SELECT * FROM face_detach_pending" in sql:
                    with sqlite3.connect(store.path) as writer:
                        writer.execute("UPDATE pending SET included=0")
                        writer.execute("UPDATE face_detach_pending SET included=0")
                return self.db.execute(sql, *args)
        with mock.patch.object(self.store, "connect", side_effect=InterleavedConnection):
            shown = self.app.pending_data()
        self.assertTrue(all(row["included"] for row in shown["pending"]))
        self.assertEqual(shown["reviewToken"], workbench.pending_review_token(before_rows, before_faces))
        self.assertFalse(any(row["included"] for row in self.app.pending_data()["pending"]))
        self.assertNotEqual(shown["reviewToken"], self.app.pending_data()["reviewToken"])

    def test_http_sync_requires_current_non_null_review_token(self):
        self.app.queue({"personId": self.source, "operation": "hide"})
        class QuietHandler(Handler):
            def log_message(self, *_args): pass
        auth = AdminSession(self.app)
        auth.cookie = "synthetic-session"; auth.expires=time.monotonic()+1000; auth.checked=time.monotonic()
        with mock.patch.object(workbench, "STATE", self.app, create=True), mock.patch.object(workbench, "AUTH", auth, create=True):
            server=ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
            thread=threading.Thread(target=server.serve_forever);thread.start()
            base=f"http://127.0.0.1:{server.server_port}"
            def post(body):
                return urllib.request.urlopen(urllib.request.Request(base+"/api/sync", data=json.dumps(body).encode(), method="POST",
                    headers={"Origin":base, "Content-Type":"application/json", "Cookie":"people_workbench_session=synthetic-session"}))
            try:
                stale=self.app.pending_data()["reviewToken"]
                self.store.set_included(self.source, False)
                for value in [None, "", stale, "malformed", "nonascii-\u2192", {}, []]:
                    with self.subTest(value=value), self.assertRaises(urllib.error.HTTPError) as caught:
                        post({"confirmation":"SYNC", "reviewToken":value})
                    self.assertEqual(caught.exception.code, 409)
                    self.assertIn("Pending changed", json.loads(caught.exception.read())["error"])
                with self.assertRaises(urllib.error.HTTPError) as caught: post({"confirmation":"SYNC"})
                self.assertEqual(caught.exception.code, 409);self.assertFalse(self.fake.calls)
                self.store.set_included(self.source, True)
                with post({"confirmation":"SYNC", "reviewToken":self.app.pending_data()["reviewToken"]}) as response:
                    self.assertEqual(json.load(response)["failed"], 0)
                self.assertEqual(self.fake.calls, [("update", self.source, {"isHidden": True})])
            finally:
                server.shutdown();server.server_close();thread.join(5)

    def begin_delayed_sync(self):
        started, release = threading.Event(), threading.Event()
        def update(person_id, payload):
            started.set()
            if not release.wait(10):
                raise RuntimeError("synthetic test release timed out")
            self.fake.calls.append(("update", person_id, payload))
        self.fake.update_person = update
        self.app.queue({"personId": self.source, "operation": "rename", "name": "Original"})
        results = []
        errors = []
        def run():
            try: results.append(self.app.sync())
            except Exception as exc: errors.append(exc)
        thread = threading.Thread(target=run)
        thread.start()
        self.assertTrue(started.wait(5))
        self.addCleanup(lambda: (release.set(), thread.join(10)))
        return release, thread, results, errors

    def test_all_direct_pending_review_group_mutations_reject_active_sync(self):
        release, thread, results, errors = self.begin_delayed_sync()
        mutations = [
            lambda: self.app.queue({"personId": self.source, "operation": "rename", "name": "Replacement"}),
            lambda: self.app.queue_merge_group({}), lambda: self.app.queue_face_detach({}),
            lambda: self.app.discard_pending(), lambda: self.app.set_group_settings({}),
            lambda: self.app.save_group_draft({}), lambda: self.app.advance_group_photo({}),
            lambda: self.app.start_group_deferred(), lambda: self.app.set_face_review_drafts({}),
            lambda: self.app.submit_face_review({}), lambda: self.app.set_faces_reviewed({}),
            lambda: self.app.set_review_reopened({}),
            lambda: self.store.set_included(self.source, False), lambda: self.store.remove_pending(self.source),
            lambda: self.store.clear_pending(), lambda: self.store.set_face_detach_included("synthetic", False),
            lambda: self.store.remove_face_detach("synthetic"),
            lambda: self.store.group_advance("synthetic", "done", []),
            lambda: self.store.upsert_pending({"personId": self.source, "operation": "hide"}),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index), self.assertRaises(workbench.SyncInProgress): mutate()
        self.assertEqual(self.store.pending()[0]["name"], "Original")
        self.assertEqual(self.store.pending()[0]["included"], 1)
        release.set(); thread.join(10)
        self.assertFalse(thread.is_alive()); self.assertFalse(errors)
        self.assertEqual(results[0]["failed"], 0)
        self.assertEqual(self.fake.calls, [("update", self.source, {"name": "Original"})])
        self.app.queue({"personId": self.source, "operation": "rename", "name": "Replacement"})
        self.assertEqual(self.store.pending()[0]["name"], "Replacement")

    def test_multiple_http_clients_get_busy_while_reads_remain_available(self):
        release, sync_thread, results, errors = self.begin_delayed_sync()
        class QuietHandler(Handler):
            def log_message(self, *_args): pass
        auth = AdminSession(self.app)
        auth.cookie = "synthetic-session"; auth.expires = time.monotonic()+1000; auth.checked = time.monotonic()
        with mock.patch.object(workbench, "STATE", self.app, create=True), mock.patch.object(workbench, "AUTH", auth, create=True):
            server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
            server_thread = threading.Thread(target=server.serve_forever); server_thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                paths = ["/api/queue", "/api/pending/include", "/api/pending/return", "/api/reset",
                         "/api/face-detach/queue", "/api/face-detach/include", "/api/face-detach/return",
                         "/api/face-review/draft", "/api/face-review/submit", "/api/face-review/reviewed/bulk",
                         "/api/group-photos/settings", "/api/group-photos/draft", "/api/group-photos/advance",
                         "/api/group-photos/deferred", "/api/queue/group", "/api/investigate/add",
                         "/api/logout", "/api/login", "/api/reload", "/api/sync"]
                for path in paths:
                    with self.subTest(path=path):
                        request = urllib.request.Request(base+path, data=json.dumps({"confirmation":"SYNC"}).encode(),
                            headers={"Origin": base, "Cookie":"people_workbench_session=synthetic-session", "Content-Type":"application/json"}, method="POST")
                        with self.assertRaises(urllib.error.HTTPError) as caught: urllib.request.urlopen(request)
                        self.assertEqual(caught.exception.code, 409)
                        self.assertIn("error", json.loads(caught.exception.read()))
                for path in ["/api/state", "/api/pending", "/api/people", "/api/names"]:
                    with urllib.request.urlopen(urllib.request.Request(base+path, headers={"Cookie":"people_workbench_session=synthetic-session"})) as response:
                        self.assertEqual(response.status, 200)
            finally:
                server.shutdown(); server.server_close(); server_thread.join(5)
        release.set(); sync_thread.join(10)
        self.assertFalse(errors); self.assertEqual(results[0]["failed"], 0)


if __name__ == "__main__":
    unittest.main()
