"""Preview files are temporary; restart recovery must preserve durable state."""
import io
import unittest
import uuid
import zipfile

import test_workspace_import as fixtures
import workspace_import as transfer
import workspace_state as ws


class PreviewLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ReturnImportTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.managers = [self.fixture.state.transfers]
        self.addCleanup(self.close_managers)
        self.fixture.name()
        self.wire = self.fixture.wire()

    def close_managers(self):
        for manager in self.managers:
            manager.close()

    def restart(self):
        # Simulate loss of the old process's in-memory handles after a crash.
        manager = transfer.ImportManager(self.fixture.state)
        self.managers.append(manager)
        return manager

    def test_close_releases_previews_and_keeps_durable_source_state(self):
        manager = self.managers[0]
        manager.preview(self.wire, self.fixture.context)
        files = list(manager.root.iterdir())
        self.assertTrue(files)
        with self.fixture.state.store.workflow_transaction("manual_snapshot") as db:
            snapshot = ws.create_snapshot(db, "Synthetic durable snapshot", "manual")
        with self.fixture.state.store.connect() as db:
            exports = ws.list_exports(db, fixtures.OWNER)
            saved_snapshot = ws.read_snapshot(db, snapshot["id"])
        manager.close()
        self.assertTrue(all(not path.exists() for path in files))
        with self.fixture.state.store.connect() as db:
            self.assertEqual(ws.list_exports(db, fixtures.OWNER), exports)
            self.assertEqual(ws.read_snapshot(db, snapshot["id"]), saved_snapshot)
        self.assertEqual(self.fixture.state.store.pending(), [])
        self.assertEqual(self.fixture.client.writes, [])

    def test_restart_reclaims_orphans_without_following_links_or_unknown_files(self):
        manager = self.managers[0]
        preview = manager.preview(self.wire, self.fixture.context)
        files = list(manager.root.iterdir())
        unknown = manager.root / "preserve-user-notes.txt"
        unknown.write_text("synthetic retained notes")
        outside = self.fixture.directory / "synthetic-retained-target"
        outside.write_bytes(b"synthetic private state must remain")
        (manager.root / (str(uuid.uuid4()) + ".result")).symlink_to(outside)
        directory = manager.root / (str(uuid.uuid4()) + ".index")
        directory.mkdir()
        (directory / "keep").write_text("synthetic nested content")
        fresh = self.restart()
        self.assertTrue(all(not path.exists() for path in files))
        self.assertEqual(unknown.read_text(), "synthetic retained notes")
        self.assertEqual(outside.read_bytes(), b"synthetic private state must remain")
        self.assertTrue((directory / "keep").is_file())
        with self.assertRaises(ws.WorkspaceError) as error:
            fresh.page(preview["token"], "200:expired", self.fixture.context)
        self.assertEqual(error.exception.code, "preview_expired")
        self.assertEqual(fresh.preview(self.wire, self.fixture.context)["counts"]["ready"], 1)
        self.assertEqual(self.fixture.client.writes, [])

    def test_large_previews_do_not_exhaust_capacity_across_restarts(self):
        with zipfile.ZipFile(io.BytesIO(self.wire)) as archive:
            payload = archive.read("changes.json")
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
            archive.writestr("changes.json", payload + b" " * (16 * 1024 ** 2 - 122 - len(payload)))
        wire = stream.getvalue()
        self.assertEqual(len(wire), 16 * 1024 ** 2)
        for _ in range(8):
            manager = self.restart()
            preview = manager.preview(wire, self.fixture.context)
            self.assertEqual(preview["counts"]["ready"], 1)
        self.assertEqual(self.fixture.state.store.pending(), [])
        self.assertEqual(self.fixture.client.writes, [])

    def test_recovery_does_not_traverse_a_linked_preview_directory(self):
        manager = self.managers[0]
        manager.preview(self.wire, self.fixture.context)
        manager.close()
        manager.root.rename(self.fixture.directory / "previous-preview-directory")
        outside = self.fixture.directory / "synthetic-unrelated-directory"
        outside.mkdir()
        protected = outside / (str(uuid.uuid4()) + ".result")
        protected.write_bytes(b"synthetic unrelated file")
        manager.root.symlink_to(outside, target_is_directory=True)
        try:
            fresh = self.restart()
            fresh.preview(self.wire, self.fixture.context)
        except (ws.WorkspaceError, OSError):
            pass
        self.assertEqual(list(outside.iterdir()), [protected])
        self.assertEqual(protected.read_bytes(), b"synthetic unrelated file")


if __name__ == "__main__":
    unittest.main()
