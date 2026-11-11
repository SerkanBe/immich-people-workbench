"""Synthetic source/portable results and explicit Sync boundary checks."""
import copy
import json
from pathlib import Path
import unittest
from unittest import mock

import people_workbench as app
import workspace_state as ws
import workspace_import as transfer
from workspace_packages import write_results_zip
from offline_workspace import OfflineClient
from test_offline_workspaces import OfflineFixture, P1, P2, P3, A1, A2, F1, F2, OWNER, NEW


class ReturnImportTests(OfflineFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        snapshot = self.prepare()
        with self.state.store.workflow_transaction("test_export") as db:
            self.export = ws.reserve_export(db, "Synthetic trip", ws.capture_state(db),
                {"snapshotId": snapshot["id"], "sha256": ws.digest(snapshot), "assetCount": snapshot["assetCount"], "mediaBytes": snapshot["totalBytes"]},
                inventory={key: snapshot[key] for key in ("people", "assets", "faces")})
            ws.publish_export(db, self.export["id"], self.export["id"] + ".zip", 123, baseline_sha256=self.export["baselineSha256"])
        root = self.directory / "portable"
        root.mkdir()
        descriptor = ws.materialize_portable(self.export["baseline"]["state"], self.export, root / "state.sqlite3")
        (root / "portable-workspace.json").write_bytes(ws.canonical(descriptor))
        self.local = app.AppState(app.Store(root / "state.sqlite3"), root / "reports")
        self.local.source_root = root
        self.local.client = OfflineClient(self.local.offline, snapshot)
        self.local.people = copy.deepcopy(snapshot["people"])
        self.context = (1, OWNER["userId"], id(self.client))

    def wire(self, change=None):
        with self.local.store.workflow_transaction("results") as db:
            result = ws.compute_changes(db)
            if change:
                change(result)
            artifact = write_results_zip(result, self.local.store.path.parent)
            body = artifact.path.read_bytes()
            artifact.path.unlink()
            return body

    def name(self, pid=P1, value="Synthetic returned name"):
        self.local.queue({"personId": pid, "operation": "rename", "name": value})

    def preview(self, body=None):
        return self.state.transfers.preview(body if body is not None else self.wire(), self.context)

    def apply(self, preview):
        return self.state.transfers.apply(preview["token"], preview["readyIds"], self.context)

    def test_name_roundtrip_conflict_selection_duplicate_and_selected_sync(self):
        self.name()
        self.local.store.mark_investigate(P2)
        body = self.wire()
        self.state.queue({"personId": P2, "operation": "rename", "name": "Independent source name"})
        preview = self.preview(body)
        self.assertEqual(preview["counts"], {"ready": 1, "conflict": 1, "applied": 0})
        conflict = next(row for row in preview["changes"] if row["status"] == "conflict")
        self.assertIn("Investigate", conflict["label"])
        self.assertIn("Independent source name", conflict["label"])
        self.assertIn("Independent source name", conflict["reason"])
        self.assertEqual(self.client.writes, [])
        applied = self.apply(preview)
        self.assertEqual(applied["imported"], 1)
        self.assertEqual(applied["snapshot"]["reason"], "pre-import")
        self.assertEqual(self.client.writes, [])
        self.assertEqual({row["person_id"] for row in self.state.store.pending()}, {P1, P2})
        repeated = self.preview(body)
        self.assertEqual(repeated["counts"]["applied"], 1)
        self.state.store.set_included(P2, False)
        self.state.sync(self.state.pending_data()["reviewToken"])
        self.assertEqual(self.client.writes, [("person", P1, {"name": "Synthetic returned name"})])
        self.assertEqual(self.preview(body)["counts"]["applied"], 1)

    def test_aba_and_stale_apply_preserve_work_without_snapshot(self):
        self.name()
        preview = self.preview()
        self.state.store.skip(P1)
        self.state.store.clear_progress(P1)
        with self.assertRaises(ws.WorkspaceError) as error:
            self.apply(preview)
        self.assertEqual(error.exception.code, "preview_stale")
        self.assertEqual(self.preview()["counts"]["conflict"], 1)
        with self.state.store.connect() as db:
            self.assertEqual(ws.list_snapshots(db), [])
        self.assertEqual(self.client.writes, [])

    def test_wrong_source_account_and_forged_inventory_reject_without_mutation(self):
        self.name()
        for change in (
            lambda result: result.update(sourceInstanceId=NEW),
            lambda result: result.update(exportId=NEW),
            lambda result: result["events"][0]["operations"][0]["key"].__setitem__(0, NEW),
            lambda result: result["events"][0]["operations"][0]["value"].update(last_error="forged"),
        ):
            with self.subTest(change=change):
                with self.assertRaises((ws.WorkspaceError, transfer.ResultsError)):
                    self.preview(self.wire(change))
        old = self.client.connection_metadata
        self.client.connection_metadata = {**old, "userId": NEW}
        try:
            with self.assertRaises(ws.WorkspaceError):
                self.preview()
        finally:
            self.client.connection_metadata = old
        self.assertEqual(self.state.store.pending(), [])
        self.assertEqual(self.client.writes, [])

    def test_failed_ledger_rolls_back_snapshot_and_all_operations(self):
        self.name()
        preview = self.preview()
        with mock.patch.object(ws, "record_import", side_effect=ws.WorkspaceError("Synthetic failure", "import_failed")):
            with self.assertRaises(ws.WorkspaceError):
                self.apply(preview)
        self.assertEqual(self.state.store.pending(), [])
        with self.state.store.connect() as db:
            self.assertEqual(ws.list_snapshots(db), [])
        self.assertEqual(self.client.writes, [])

    def test_upstream_change_before_sync_blocks_all_included_writes(self):
        self.name()
        self.apply(self.preview())
        self.client.rows[P1]["name"] = "External change"
        with self.assertRaises(ws.WorkspaceError):
            self.state.sync(self.state.pending_data()["reviewToken"])
        self.assertEqual(self.client.writes, [])
        self.state.store.set_included(P1, False)
        self.state.queue({"personId": P2, "operation": "rename", "name": "Unrelated"})
        self.state.sync(self.state.pending_data()["reviewToken"])
        self.assertEqual(self.client.writes, [("person", P2, {"name": "Unrelated"})])

    def test_native_requeue_requires_explicit_reload_and_new_review_token(self):
        self.name()
        body = self.wire()
        self.apply(self.preview(body))
        self.state.store.remove_pending(P1)
        self.state.queue({"personId": P1, "operation": "rename", "name": "Native requeue"})
        old = self.state.pending_data()["reviewToken"]
        self.assertEqual(self.state.pending_data()["pending"][0]["guardStatus"], "baseline_unverified")
        with self.assertRaises(ws.WorkspaceError) as error:
            self.state.sync(old)
        self.assertEqual(error.exception.code, "baseline_unverified")
        self.state.load(refresh_guards=False)
        self.assertEqual(self.state.pending_data()["reviewToken"], old)
        self.state.load(refresh_guards=True)
        current = self.state.pending_data()
        self.assertNotEqual(current["reviewToken"], old)
        with self.assertRaises(app.SyncInProgress):
            self.state.sync(old)
        self.state.sync(current["reviewToken"])
        self.assertEqual(self.client.writes, [("person", P1, {"name": "Native requeue"})])
        self.assertEqual(self.preview(body)["counts"]["applied"], 1)

    def test_face_target_retry_uses_one_create_and_keeps_checkpoint(self):
        self.local.queue_face_detach({"personId": P1, "faceId": F1, "assetId": A1})
        self.apply(self.preview())
        with mock.patch.object(self.client, "reassign_face", side_effect=app.ToolError("Synthetic failure")):
            self.assertEqual(self.state.sync()["failed"], 1)
        self.assertEqual(self.state.store.face_detaches()[0]["target_person_id"], NEW)
        self.assertEqual(self.state.sync()["failed"], 0)
        self.assertEqual(sum(call[0] == "create" for call in self.client.writes), 1)
        self.assertEqual(sum(call[0] == "face" for call in self.client.writes), 1)

    def test_unknown_face_create_is_never_automatically_replayed(self):
        self.local.queue_face_detach({"personId": P1, "faceId": F1, "assetId": A1})
        self.apply(self.preview())
        create = self.client.create_person
        def lost_response():
            create()
            raise ConnectionError("Synthetic lost response")
        with mock.patch.object(self.client, "create_person", side_effect=lost_response):
            self.assertEqual(self.state.sync()["failed"], 1)
        with self.assertRaises(ws.WorkspaceError):
            self.state.sync()
        self.assertEqual(sum(call[0] == "create" for call in self.client.writes), 1)

    def test_partial_named_review_import_never_imports_completeness(self):
        self.local.set_face_reviewed({"personId": P3, "faceId": F2, "reviewed": True})
        self.apply(self.preview())
        self.assertEqual(self.state.store.reviewed_face_ids(P3), {F2})
        self.assertFalse(self.state.face_review_progress(P3, self.state.people[P3])["personReviewed"])
        self.assertEqual(self.client.writes, [])

    def test_completed_merge_photo_retry_does_not_validate_or_replay_deleted_source(self):
        self.local.queue({"personId": P1, "operation": "merge", "targetPersonId": P3, "featureAssetId": A1})
        self.apply(self.preview())
        calls = []
        def merge(target, source):
            calls.append((target, source))
            del self.client.rows[source]
            self.client.rows[target]["updatedAt"] = "own-merge"
            for face in self.client.face_rows[A1]:
                if face["person"]["id"] == source:
                    face["person"] = {"id": target}
        self.client.merge = merge
        update = self.client.update_person
        with mock.patch.object(self.client, "update_person", side_effect=app.ToolError("Synthetic photo failure")):
            self.assertEqual(self.state.sync()["failed"], 1)
        self.assertEqual(self.state.store.pending()[0]["merge_step"], "complete")
        self.assertEqual(self.state.sync()["failed"], 0)
        self.assertEqual(calls, [(P3, P1)])
        self.assertEqual(self.client.writes[-1], ("person", P3, {"featureFaceAssetId": A1}))

    def test_expired_foreign_and_empty_selections_never_snapshot(self):
        self.name()
        preview = self.preview()
        with self.assertRaises(ws.WorkspaceError):
            self.state.transfers.apply(preview["token"], [], self.context)
        with self.assertRaises(ws.WorkspaceError):
            self.state.transfers.apply(preview["token"], preview["readyIds"], (2, *self.context[1:]))
        self.state.transfers.previews[preview["token"]]["until"] = 0
        with self.assertRaises(ws.WorkspaceError):
            self.apply(preview)
        with self.state.store.connect() as db:
            self.assertEqual(ws.list_snapshots(db), [])
        self.assertEqual(self.client.writes, [])


if __name__ == "__main__":
    unittest.main()
