# Copyright (C) 2026 Serkan Bekdemir
# SPDX-License-Identifier: AGPL-3.0-only
"""Interoperability checks use real state/ZIP helpers and disposable SQLite."""
from pathlib import Path
import tempfile
import unittest

import people_workbench as app
import workspace_import as reader
import workspace_packages as packages
import workspace_state as state


PERSON = "11111111-1111-4111-8111-111111111111"
OWNER = {"url": "https://synthetic.example.test/api",
         "userId": "22222222-2222-4222-8222-222222222222"}


class WorkspaceTransferContractTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="workspace-contract-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.source = app.Store(self.root / "source.sqlite3")
        self.source.set_setting("owner", state.canonical(OWNER).decode())
        with self.source.workflow_transaction("export") as db:
            capture = state.capture_state(db)
            self.export = state.reserve_export(db, "Synthetic trip", capture,
                {"snapshotId": "ab" * 16, "sha256": "cd" * 32,
                 "assetCount": 1, "mediaBytes": 123},
                inventory={"people": {PERSON: {"id": PERSON, "name": "", "isHidden": False}}})
            state.publish_export(db, self.export["id"], self.export["id"] + ".zip", 123,
                                 baseline_sha256=self.export["baselineSha256"])
        path = self.root / "portable.sqlite3"
        state.materialize_portable(capture, self.export, path)
        self.portable = app.Store(path)
        self.portable.upsert_pending({"personId": PERSON, "operation": "rename", "name": "Portable name"})

    def result(self):
        with self.portable.workflow_transaction("return-file") as db:
            result = state.compute_changes(db)
            package = packages.write_results_zip(result, self.root)
            state.note_results_emitted(db, result)
        with package.path.open("rb") as stream:
            decoded = reader.parse_results(stream)
        self.assertEqual(decoded, result)
        return decoded

    def test_real_producer_reader_and_permanent_acceptance_interoperate(self):
        result = self.result()
        groups, event_ids = state.group_results(result, self.export["baseline"]["state"])
        self.assertEqual(len(groups), 1)
        with self.source.workflow_transaction("selected-import", origin="import") as db:
            snapshot = state.create_snapshot(db, "Before import", "pre-import")
            self.source.upsert_pending({"personId": PERSON, "operation": "rename", "name": "Portable name"})
            state.bind_import_guard(db, "pending", [PERSON], {"exportId": self.export["id"]})
            state.record_import(db, self.export["id"], groups, snapshot["id"], event_ids=event_ids)
        self.assertEqual(self.source.pending()[0]["name"], "Portable name")
        self.source.remove_pending(PERSON)
        reopened = app.Store(self.source.path)
        with reopened.connect() as db:
            self.assertEqual(state.acceptance_status(db, self.export["id"], groups[0], event_ids), "applied")
        self.assertEqual(reopened.pending(), [])
        self.assertEqual(self.result(), result)

    def test_failure_after_snapshot_rolls_back_work_and_acceptance(self):
        result = self.result()
        groups, event_ids = state.group_results(result, self.export["baseline"]["state"])
        with self.assertRaisesRegex(RuntimeError, "synthetic failure"):
            with self.source.workflow_transaction("selected-import", origin="import") as db:
                snapshot = state.create_snapshot(db, "Before import", "pre-import")
                self.source.upsert_pending({"personId": PERSON, "operation": "rename", "name": "Portable name"})
                state.record_import(db, self.export["id"], groups, snapshot["id"], event_ids=event_ids)
                raise RuntimeError("synthetic failure")
        self.assertEqual(self.source.pending(), [])
        with self.source.connect() as db:
            self.assertEqual(state.list_snapshots(db), [])
            self.assertEqual(state.acceptance_status(db, self.export["id"], groups[0], event_ids), "ready")
