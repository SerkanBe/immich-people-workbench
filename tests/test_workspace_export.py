# Copyright (C) 2026 Serkan Bekdemir
# SPDX-License-Identifier: AGPL-3.0-only
"""Portable export tests use only a small synthetic workspace."""
import hashlib
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("export_workspace", Path(__file__).parents[1] / "tools/export_workspace.py")
EXPORT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EXPORT)


class WorkspaceExportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source, self.state, self.target = self.root / "app", self.root / "state", self.root / "portable"
        self.source.mkdir()
        for name in EXPORT.RUNTIME:
            (self.source / name).write_text("synthetic application", encoding="utf-8")
        (self.source / "web").mkdir()
        for name in EXPORT.WEB_FILES:
            (self.source / "web" / name).write_text("synthetic web asset", encoding="utf-8")
        (self.source / "private.key").write_text("synthetic canary")
        self.state.mkdir()
        owner = {"url": "http://synthetic.invalid/api", "userId": "synthetic-owner"}
        with closing(sqlite3.connect(self.state / "state.sqlite3")) as db:
            db.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
            db.execute("INSERT INTO settings VALUES ('owner', ?)", (json.dumps(owner),))
            db.execute("CREATE TABLE pending (name TEXT)")
            db.execute("INSERT INTO pending VALUES ('Synthetic Pending')")
            db.commit()
        self.sid = "a" * 32
        self.snapshot_root = self.state / "offline" / "snapshots" / self.sid
        (self.snapshot_root / "originals").mkdir(parents=True)
        body = b"synthetic-original-photo"
        (self.snapshot_root / "originals" / "photo").write_bytes(body)
        self.snapshot = {"id": self.sid, "formatVersion": 1, "owner": owner, "assets": {"photo": {}},
                         "media": {"originals": {"photo": {"path": "originals/photo", "size": len(body),
                                                          "sha256": hashlib.sha256(body).hexdigest()}}}}
        self.write_snapshot()
        (self.state / "offline" / "active.json").write_text(json.dumps({"id": self.sid}))

    def write_snapshot(self):
        (self.snapshot_root / "snapshot.json").write_text(json.dumps(self.snapshot))

    def test_export_preserves_work_and_originals_but_excludes_unrelated_files(self):
        (self.state / "reports").mkdir()
        (self.state / "reports" / "private-report.txt").write_text("synthetic report")
        (self.snapshot_root / "baseline.json").write_text(json.dumps({"people": {}, "faces": {}}))
        EXPORT.export_workspace(self.target, self.state, self.source)
        self.assertTrue((self.target / "people_workbench.py").is_file())
        self.assertTrue((self.target / "offline_workspace.py").is_file())
        self.assertFalse((self.target / "private.key").exists())
        exported = self.target / ".people-workbench"
        self.assertFalse((exported / "reports").exists())
        self.assertFalse((exported / "offline" / "plan.json").exists())
        self.assertEqual((exported / "offline" / "snapshots" / self.sid / "originals" / "photo").read_bytes(),
                         b"synthetic-original-photo")
        self.assertTrue((exported / "offline" / "snapshots" / self.sid / "baseline.json").exists())
        with closing(sqlite3.connect(exported / "state.sqlite3")) as db:
            self.assertEqual(db.execute("SELECT name FROM pending").fetchone()[0], "Synthetic Pending")
        self.assertTrue((self.state / "state.sqlite3").exists())

    def test_existing_destination_is_never_overwritten(self):
        self.target.mkdir()
        (self.target / "keep.txt").write_text("keep")
        with self.assertRaisesRegex(ValueError, "already exists"):
            EXPORT.export_workspace(self.target, self.state, self.source)
        self.assertEqual((self.target / "keep.txt").read_text(), "keep")

    def test_incomplete_or_escaping_media_is_refused(self):
        for path in ("../outside", "/outside"):
            with self.subTest(path=path):
                self.snapshot["media"]["originals"]["photo"]["path"] = path
                self.write_snapshot()
                with self.assertRaises(ValueError):
                    EXPORT.export_workspace(self.target, self.state, self.source)
                self.assertFalse(self.target.exists())

    def test_corrupted_original_is_not_published(self):
        self.snapshot["media"]["originals"]["photo"]["sha256"] = "0" * 64
        self.write_snapshot()
        with self.assertRaisesRegex(RuntimeError, "checksum"):
            EXPORT.export_workspace(self.target, self.state, self.source)
        self.assertFalse(self.target.exists())

    def test_destination_inside_source_state_is_refused(self):
        with self.assertRaisesRegex(ValueError, "separate"):
            EXPORT.export_workspace(self.state / "portable", self.state, self.source)

    def test_stale_snapshot_is_not_exported_as_ready(self):
        (self.state / "offline" / "active.json").write_text(json.dumps({"id": self.sid, "stale": True}))
        with self.assertRaisesRegex(ValueError, "refreshing"):
            EXPORT.export_workspace(self.target, self.state, self.source)
        self.assertFalse(self.target.exists())


if __name__ == "__main__":
    unittest.main()
