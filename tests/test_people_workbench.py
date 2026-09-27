# Copyright (C) 2026 Serkan Bekdemir
# SPDX-License-Identifier: AGPL-3.0-only
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import json
from http.server import ThreadingHTTPServer
from pathlib import Path
import sys
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import people_workbench as workbench
from people_workbench import AdminSession, AppState, Handler, Immich, Store, ToolError, name_fragment_matches, paginate, sort_people


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
        for route in ("/", "/unnamed", "/merge", "/investigate", "/faces", "/pending", "/named", "/ignored"):
            with self.subTest(route=route), opener.open(base + route) as response:
                self.assertEqual(response.status, 200)
                self.assertIn(b'nav aria-label="People sections"', response.read())
        for path, signature in (
            ("/merge.js", b"export function createMergeWorkbench"),
            ("/face-review.js", b"export function createFaceReview"),
            ("/naming.js", b"export function createNaming"),
            ("/investigate.js", b"export function createInvestigate"),
            ("/pending.js", b"export function createPending"),
            ("/browser-storage.js", b"export function readMergeCanvasState"),
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

                    for path in ("/api/state", "/media/person/11111111-1111-4111-8111-111111111111", "/app.js"):
                        with self.subTest(path=path), self.assertRaises(urllib.error.HTTPError) as error:
                            urllib.request.urlopen(base + path)
                        self.assertEqual(error.exception.code, 401)
                    with urllib.request.urlopen(base + "/merge") as response:
                        self.assertEqual(response.geturl(), base + "/login?next=/merge")
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
                        self.assertIn(b'nav aria-label="People sections"', response.read())
                    with urllib.request.urlopen(urllib.request.Request(base + "/api/state", headers={"Cookie": cookie})) as response:
                        self.assertEqual(response.status, 200)
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
            store.set_face_reviewed(queued_face, person_id, True)

            result = app.faces_for_person(person_id)

            self.assertEqual(len(result["faces"]), 2)
            self.assertTrue(next(face for face in result["faces"] if face["faceId"] == queued_face)["queued"])
            self.assertTrue(next(face for face in result["faces"] if face["faceId"] == queued_face)["reviewed"])
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

            first = app.set_face_reviewed({"personId": person_id, "faceId": "cccccccc-cccc-4ccc-8ccc-cccccccccccc", "reviewed": True})
            second = app.set_face_reviewed({"personId": person_id, "faceId": "dddddddd-dddd-4ddd-8ddd-dddddddddddd", "reviewed": True})

            self.assertFalse(first["personReviewed"])
            self.assertEqual(first["reviewedCount"], 1)
            self.assertTrue(second["personReviewed"])
            reviewed_person = next(person for person in app.list_people("review", "most") if person["id"] == person_id)
            self.assertTrue(reviewed_person["reviewed"])
            self.assertEqual(reviewed_person["reviewedCount"], 2)

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

            app.queue_face_detach({"personId": source, "faceId": face_id, "assetId": asset_id, "fileName": "family-photo.jpg"})
            self.assertEqual(len(store.face_detaches()), 1)
            self.assertIn(face_id, store.reviewed_face_ids(source))
            self.assertFalse(any(call[0] == "reassign-face" for call in fake.calls))

            result = app.sync()

            target = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
            self.assertEqual(result["failed"], 0)
            self.assertIn(("create-person", target), fake.calls)
            self.assertIn(("reassign-face", target, face_id), fake.calls)
            self.assertEqual(store.face_detaches(), [])
            self.assertNotIn(face_id, store.reviewed_face_ids(source))

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


if __name__ == "__main__":
    unittest.main()
