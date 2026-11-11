import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import uuid

import people_workbench as app
import workspace_state as ws


P, Q, R, ASSET, OTHER, USER = (str(uuid.UUID(int=i)) for i in range(1, 7))
OWNER = {"url": "https://photos.example.test/api", "userId": USER}
MEDIA = {"snapshotId": ASSET, "sha256": "a" * 64, "assetCount": 1, "mediaBytes": 123}


def rename(person, name="Demo Person"):
    return {"personId": person, "operation": "rename", "name": name}


class WorkspaceStateTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.source = app.Store(self.root / "source.sqlite3")
        self.source.set_setting("owner", ws.canonical(OWNER).decode())

    def export(self, *, inventory=None, ready=True):
        with self.source.workflow_transaction("reserve_export") as db:
            captured = ws.capture_state(db)
            record = ws.reserve_export(db, "Demo trip", captured, MEDIA, inventory=inventory)
        if ready:
            with self.source.workflow_transaction("publish_export") as db:
                ws.publish_export(db, record["id"], record["id"] + ".zip", 456, baseline_sha256=record["baselineSha256"])
                record = ws.read_known_export(db, record["id"], OWNER)
        return record

    def portable(self, record):
        path = self.root / "portable.sqlite3"
        descriptor = ws.materialize_portable(record["baseline"]["state"], record, path)
        return app.Store(path), descriptor

    def result(self, store, *, emit=False):
        with store.workflow_transaction("results") as db:
            result = ws.compute_changes(db)
            if emit:
                ws.note_results_emitted(db, result)
            return result

    def test_migration_and_reopen_preserve_work_and_identity(self):
        legacy = self.root / "old.sqlite3"
        with sqlite3.connect(legacy) as db:
            db.execute("CREATE TABLE progress(person_id TEXT PRIMARY KEY,skip_count INTEGER NOT NULL,updated_at TEXT NOT NULL)")
            db.execute("INSERT INTO progress VALUES(?,4,'fixture')", (P,))
        old = app.Store(legacy)
        with old.connect() as db:
            capture = ws.capture_state(db)
            self.assertEqual(capture["rows"][0]["value"], {"skipCount": 4})
        reopened = app.Store(legacy)
        with reopened.connect() as db:
            self.assertEqual(ws.capture_state(db), capture)
        with self.source.connect() as db:
            self.assertNotEqual(ws.instance_identity(db)["instanceId"], capture["instanceId"])
        moved = self.root / "moved.sqlite3"
        legacy.rename(moved)
        with app.Store(moved).connect() as db:
            self.assertEqual(ws.instance_identity(db)["instanceId"], capture["instanceId"])

    def test_revisions_detect_aba_and_preserve_tombstones(self):
        self.source.upsert_pending(rename(P))
        with self.source.connect() as db:
            first, _ = ws.row_revision(db, "pending", [P])
        self.source.upsert_pending(rename(P, "Changed"))
        self.source.upsert_pending(rename(P))
        with self.source.connect() as db:
            reverted, _ = ws.row_revision(db, "pending", [P])
        self.source.remove_pending(P)
        with self.source.connect() as db:
            tombstone, deleted = ws.row_revision(db, "pending", [P])
            self.assertTrue(deleted)
        self.source.upsert_pending(rename(P))
        with self.source.connect() as db:
            recreated, deleted = ws.row_revision(db, "pending", [P])
            self.assertFalse(deleted)
            self.assertTrue(first < reverted < tombstone < recreated)
            self.assertEqual(ws.row_revision(db, "pending", [Q]), (0, True))

    def test_state_capture_uses_one_view_and_bounds_tombstones(self):
        self.source.skip(P)
        with self.source.connect() as reader:
            before = ws.capture_state(reader)
            self.source.skip(P)
            self.assertEqual(ws.capture_state(reader), before)
        with self.source.connect() as reader:
            self.assertEqual(ws.capture_state(reader)["rows"][0]["value"], {"skipCount": 2})
        for person in (P, Q):
            self.source.skip(person)
            self.source.clear_progress(person)
        with self.source.connect() as db, self.assertRaisesRegex(ws.WorkspaceError, "size limit"):
            ws.capture_state(db, limits=ws.Limits(registry_records=1))

    def test_settings_and_cache_revision_scope(self):
        self.source.set_setting("sort", "most")
        with self.source.connect() as db:
            revision = ws.state_revision(db)
        self.source.set_setting("sort", "most")
        self.source.set_setting("future-private", "synthetic-secret")
        self.source.record_face_review_page(P, 1, 20, [{"faceId": Q}], False, 1, "fixture")
        with self.source.connect() as db:
            self.assertEqual(ws.state_revision(db), revision)
            self.assertEqual(ws.group_revision(db), 0)
        self.source.set_setting("group_current", ASSET)
        self.source.set_setting("group_min_faces", "4")
        self.source.group_advance(ASSET, "deferred", [])
        with self.source.connect() as db:
            self.assertGreater(ws.group_revision(db), 0)
            state = ws.capture_state(db)
            self.assertNotIn(b"synthetic-secret", ws.canonical(state))
            self.assertNotIn(b"face_review_inventory", ws.canonical(state))

    def test_materialize_creates_clean_portable_database_and_distinct_owner_identity(self):
        canary = "SYNTHETIC-CREDENTIAL-TO-SCRUB"
        self.source.upsert_pending(rename(P))
        self.source.set_error(P, canary)
        self.source.set_setting("legacy-secret-setting", canary)
        with self.source.connect() as db:
            db.execute("PRAGMA secure_delete=OFF")
            db.execute("INSERT INTO settings VALUES('removed-secret',?)", (canary,))
            db.execute("DELETE FROM settings WHERE key='removed-secret'")
        record = self.export(inventory={"people": {P: {"id": P, "name": "Synthetic", "token": canary}}})
        portable, descriptor = self.portable(record)
        self.assertNotEqual(descriptor["portableInstanceId"], record["sourceInstanceId"])
        self.assertEqual(descriptor["owner"], OWNER)
        self.assertNotIn(canary.encode(), portable.path.read_bytes())
        with portable.connect() as db:
            self.assertEqual(ws.portable_descriptor(db), descriptor)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM workspace_exports").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM workspace_accepted").fetchone()[0], 0)
            self.assertEqual(ws.compute_changes(db)["events"], [])
        self.assertEqual(portable.pending()[0]["name"], "Demo Person")
        self.assertEqual(portable.pending()[0]["last_error"], "")
        self.assertFalse(portable.path.with_suffix(".sqlite3-wal").exists())

    def test_export_history_reads_metadata_only_and_checks_known_baseline(self):
        record = self.export()
        with self.source.connect() as db:
            def deny_baseline(action, table, column, _database, _trigger):
                return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_READ and table == "workspace_exports" and column == "baseline_json" else sqlite3.SQLITE_OK
            db.set_authorizer(deny_baseline)
            listed = ws.list_exports(db, OWNER)
            db.set_authorizer(None)
            self.assertEqual(listed[0]["media"], MEDIA)
            self.assertEqual(listed[0]["archiveBytes"], 456)
            self.assertNotIn("baseline", listed[0])
            with self.assertRaises(ws.WorkspaceError):
                ws.read_known_export(db, record["id"], {**OWNER, "userId": Q})
            db.execute("UPDATE workspace_exports SET baseline_json='{}' WHERE id=?", (record["id"],))
            with self.assertRaisesRegex(ws.WorkspaceError, "baseline"):
                ws.read_known_export(db, record["id"])

    def test_group_photo_results_are_atomic_and_stable_across_restart(self):
        self.source.skip(P)
        self.source.mark_investigate(P)
        record = self.export()
        portable, _ = self.portable(record)
        portable.group_advance(ASSET, "done", [rename(P), {"personId": Q, "operation": "hide"}])
        portable.skip(R)
        portable.set_setting("group_current", OTHER)
        result = self.result(portable, emit=True)
        self.assertEqual(result, self.result(app.Store(portable.path)))
        self.assertEqual(ws.canonical(result), ws.canonical(json.loads(ws.canonical(result))))
        groups, _ = ws.group_results(result, record["baseline"]["state"])
        self.assertEqual(len(groups), 2)
        photo = next(group for group in groups if group["kind"] == "group-photo")
        self.assertEqual(sorted(op["type"] for op in photo["operations"]), [
            "group.progress.set", "person.investigate.remove", "person.pending.set", "person.pending.set", "person.skip.remove"])
        self.assertNotIn(b"group_current", ws.canonical(result))

    def test_returned_net_zero_fails_closed_but_unsent_revert_is_empty(self):
        portable, _ = self.portable(self.export())
        portable.upsert_pending(rename(P))
        portable.remove_pending(P)
        self.assertEqual(self.result(portable)["events"], [])
        portable.upsert_pending(rename(P))
        self.result(portable, emit=True)
        portable.remove_pending(P)
        portable.skip(Q)
        with self.assertRaisesRegex(ws.WorkspaceError, "Previously returned") as error:
            self.result(app.Store(portable.path))
        self.assertEqual(error.exception.code, "not_ready")
        self.assertEqual(portable.skips()[Q], 1)

    def test_superseding_edit_preserves_original_group_photo_command(self):
        record = self.export()
        portable, _ = self.portable(record)
        portable.group_advance(ASSET, "done", [rename(P), rename(Q)])
        original = self.result(portable)
        portable.upsert_pending(rename(P, "Edited after submit"))
        result = self.result(app.Store(portable.path))
        groups, _ = ws.group_results(result, record["baseline"]["state"])
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]["operations"]), 3)
        self.assertEqual(len(result["events"]), 1)
        self.assertEqual(result["events"][0]["id"], original["events"][0]["id"])
        changed = next(op for op in groups[0]["operations"] if op["type"] == "person.pending.set" and op["key"] == [P])
        self.assertEqual(changed["value"]["name"], "Edited after submit")

    def test_unchanged_emitted_members_keep_ids_after_superseding_edit(self):
        record = self.export()
        portable, _ = self.portable(record)
        portable.group_advance(ASSET, "done", [rename(P), rename(Q)])
        original = self.result(portable, emit=True)
        original_groups, event_ids = ws.group_results(original, record["baseline"]["state"])
        with self.source.workflow_transaction("apply", origin="import") as db:
            snapshot = ws.create_snapshot(db, "Before group import", "pre-import")
            self.source.group_advance(ASSET, "done", [rename(P), rename(Q)])
            ws.record_import(db, record["id"], original_groups, snapshot["id"], event_ids=event_ids)
        # A same-value queue save changes SQL revision/timestamps, but it does
        # not change the already emitted desired operation or declared event.
        portable.upsert_pending(rename(Q))
        self.assertEqual(self.result(portable), original)
        portable.upsert_pending(rename(P, "Later desired name"))
        portable.skip(R)
        result = self.result(app.Store(portable.path))
        groups, current_ids = ws.group_results(result, record["baseline"]["state"])
        original_by_key = {(op["type"], tuple(op["key"])): op for event in original["events"] for op in event["operations"]}
        current_by_key = {(op["type"], tuple(op["key"])): op for event in result["events"] for op in event["operations"]}
        for key in (("person.pending.set", (Q,)), ("group.progress.set", (ASSET,))):
            self.assertEqual(current_by_key[key], original_by_key[key])
            op_id = original_by_key[key]["id"]
            self.assertEqual(current_ids[op_id], event_ids[op_id])
        with self.source.connect() as db:
            statuses = sorted(ws.acceptance_status(db, record["id"], group, current_ids) for group in groups)
        self.assertEqual(statuses, ["conflict", "ready"])

    def test_shared_member_keeps_transitive_command_closure_after_edit(self):
        record = self.export()
        portable, _ = self.portable(record)
        portable.group_advance(ASSET, "done", [rename(P), rename(Q)])
        portable.group_advance(OTHER, "done", [rename(Q), rename(R)])
        portable.upsert_pending(rename(P, "Changed first member"))
        groups, _ = ws.group_results(self.result(portable), record["baseline"]["state"])
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]["operations"]), 5)

    def test_joining_distinct_emitted_events_is_refused_without_rebinding(self):
        portable, _ = self.portable(self.export())
        portable.upsert_pending(rename(P))
        portable.upsert_pending(rename(Q))
        original = self.result(portable, emit=True)
        self.assertEqual(len(original["events"]), 2)
        portable.group_advance(ASSET, "done", [rename(P), rename(Q)])
        with self.assertRaisesRegex(ws.WorkspaceError, "regrouped") as error:
            self.result(app.Store(portable.path))
        self.assertEqual(error.exception.code, "not_ready")
        self.assertEqual(len(portable.pending()), 2)
        self.assertEqual(portable.group_progress(), {ASSET: "done"})

    def test_cursor_navigation_does_not_join_independent_photo_commands(self):
        record = self.export()
        portable, _ = self.portable(record)
        for asset, person in ((ASSET, P), (OTHER, Q)):
            portable.set_setting("group_current", asset)
            portable.group_advance(asset, "done", [rename(person)])
        result = self.result(portable)
        groups, _ = ws.group_results(result, record["baseline"]["state"])
        self.assertEqual(sorted(len(group["operations"]) for group in groups), [2, 2])

    def test_legacy_emission_without_event_bindings_fails_closed(self):
        portable, _ = self.portable(self.export())
        portable.upsert_pending(rename(P))
        with portable.workflow_transaction("legacy_emission") as db:
            db.execute("INSERT INTO workspace_emitted VALUES(?,?)", ("pending", ws._key([P])))
        with self.assertRaisesRegex(ws.WorkspaceError, "no saved event bindings") as error:
            self.result(app.Store(portable.path))
        self.assertEqual(error.exception.code, "not_ready")

    def test_historical_command_closure_respects_reference_budget(self):
        portable, _ = self.portable(self.export())
        portable.group_advance(ASSET, "done", [rename(P), rename(Q)])
        portable.upsert_pending(rename(P, "Later desired name"))
        with portable.connect() as db, self.assertRaises(ws.WorkspaceError) as error:
            ws.compute_changes(db, limits=ws.Limits(references=2))
        self.assertEqual(error.exception.code, "resource_limit")

    def test_accepted_event_survives_regrouping_and_local_removal(self):
        record = self.export()
        portable, _ = self.portable(record)
        portable.upsert_pending(rename(P))
        first = self.result(portable, emit=True)
        groups, event_ids = ws.group_results(first, record["baseline"]["state"])
        group = groups[0]
        operation = group["operations"][0]
        with self.source.workflow_transaction("apply", origin="import") as db:
            snapshot = ws.create_snapshot(db, "Before import", "pre-import")
            self.source.upsert_pending(rename(P))
            imported_action = ws.bind_import_guard(db, "pending", [P], {"exportId": record["id"]})
            receipt = ws.record_import(db, record["id"], groups, snapshot["id"], event_ids=event_ids, result_sha256=ws.digest(first))
            self.assertEqual(receipt["imported"], 1)
        self.source.set_included(P, False)
        with self.source.connect() as db:
            self.assertEqual(ws.action_head(db, "pending", [P])["id"], imported_action)
            self.assertEqual(ws.last_import(db, record["id"], ws.digest(first))["snapshot"], snapshot)
        self.source.upsert_pending(rename(P, "Source edit"))
        with self.source.connect() as db:
            derived = ws.action_head(db, "pending", [P])
            self.assertEqual((derived["origin"], derived["parentId"]), ("source-derived", imported_action))
            self.assertEqual(derived["guard"]["exportId"], record["id"])
        self.source.remove_pending(P)
        self.source.upsert_pending(rename(P, "Native requeue"))
        with self.source.connect() as db:
            native = ws.action_head(db, "pending", [P])
            self.assertEqual(native["origin"], "source-native")
            self.assertEqual(native["guard"]["status"], "baseline_unverified")
            self.assertEqual(ws.acceptance_status(db, record["id"], group, event_ids), "applied")
            with self.assertRaisesRegex(ws.WorkspaceError, "reused"):
                ws.accepted_operation(db, record["id"], {**operation, "value": {**operation["value"], "name": "Altered"}}, event_ids[operation["id"]])
            with self.assertRaisesRegex(ws.WorkspaceError, "reused"):
                ws.accepted_operation(db, record["id"], operation, OTHER)
        portable.upsert_pending({"personId": Q, "operation": "merge", "targetPersonId": P, "targetName": "Demo Person"})
        portable.skip(R)
        next_result = self.result(portable)
        joined, next_ids = ws.group_results(next_result, record["baseline"]["state"])
        self.assertEqual(next_ids[operation["id"]], event_ids[operation["id"]])
        with self.source.connect() as db:
            self.assertEqual(sorted(ws.acceptance_status(db, record["id"], item, next_ids) for item in joined), ["conflict", "ready"])
            self.assertEqual(ws.read_export_summary(db, record["id"])["importSummary"]["importedGroups"], 1)
        self.assertEqual(self.source.pending()[0]["name"], "Native requeue")

    def test_import_audit_and_snapshot_roll_back_with_operations(self):
        record = self.export()
        portable, _ = self.portable(record)
        portable.skip(P)
        result = self.result(portable)
        groups, event_ids = ws.group_results(result, record["baseline"]["state"])
        with self.assertRaisesRegex(RuntimeError, "synthetic rollback"):
            with self.source.workflow_transaction("apply", origin="import") as db:
                snapshot = ws.create_snapshot(db, "Before import", "pre-import")
                self.source.skip(P)
                ws.record_import(db, record["id"], groups, snapshot["id"], event_ids=event_ids)
                raise RuntimeError("synthetic rollback")
        self.assertEqual(self.source.skips(), {})
        with self.source.connect() as db:
            self.assertEqual(ws.list_snapshots(db), [])
            self.assertEqual(ws.acceptance_status(db, record["id"], groups[0], event_ids), "ready")

    def test_snapshot_quota_and_download_are_local(self):
        self.source.skip(P)
        with self.source.workflow_transaction("snapshot") as db:
            saved = ws.create_snapshot(db, "Before trip", "manual", limits=ws.Limits(snapshot_count=1))
        self.source.skip(P)
        with self.assertRaises(ws.WorkspaceError) as error:
            with self.source.workflow_transaction("snapshot") as db:
                ws.create_snapshot(db, "Another trip", "manual", limits=ws.Limits(snapshot_count=1))
        self.assertEqual(error.exception.code, "snapshot_quota")
        with self.source.connect() as db:
            metadata, payload = ws.read_snapshot(db, saved["id"], OWNER)
            self.assertEqual(metadata, saved)
            self.assertEqual(json.loads(payload)["rows"][0]["value"], {"skipCount": 1})
            self.assertEqual(ws.list_snapshots(db), [saved])

    def test_workflow_sqlite_busy_returns_retryable_error_without_changes(self):
        with sqlite3.connect(self.source.path) as other:
            other.execute("BEGIN IMMEDIATE")
            with patch.object(app, "WORKFLOW_BUSY_TIMEOUT_MS", 5), self.assertRaises(app.SyncInProgress):
                self.source.skip(P)
            self.assertEqual(self.source.skips(), {})
        self.assertEqual(self.source.skip(P), 1)


if __name__ == "__main__":
    unittest.main()
