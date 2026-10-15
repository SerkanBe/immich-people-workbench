# Copyright (C) 2026 Serkan Bekdemir
# SPDX-License-Identifier: AGPL-3.0-only
"""Synthetic SQLite connection ownership and transaction regressions."""
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import people_workbench as workbench


class StoreConnectionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = workbench.Store(Path(directory.name) / "synthetic.sqlite3")

    def assert_closed(self, connection):
        with self.assertRaisesRegex(sqlite3.ProgrammingError, "closed database"):
            connection.execute("SELECT 1")

    def test_successful_context_commits_and_closes(self):
        with self.store.connect() as connection:
            self.assertIsInstance(connection, sqlite3.Connection)
            self.assertIs(connection.row_factory, sqlite3.Row)
            connection.execute("INSERT INTO settings VALUES (?, ?)", ("synthetic", "committed"))
        self.assert_closed(connection)
        self.assertEqual(self.store.setting("synthetic"), "committed")

    def test_failed_context_rolls_back_and_closes(self):
        self.store.set_setting("synthetic", "original")
        with self.assertRaisesRegex(ValueError, "synthetic failure"):
            with self.store.connect() as connection:
                connection.execute("UPDATE settings SET value='uncommitted' WHERE key='synthetic'")
                raise ValueError("synthetic failure")
        self.assert_closed(connection)
        self.assertEqual(self.store.setting("synthetic"), "original")

    def test_failed_commit_preserves_error_and_closes(self):
        with self.store.connect() as connection:
            connection.execute("CREATE TABLE synthetic_parent(id INTEGER PRIMARY KEY)")
            connection.execute("CREATE TABLE synthetic_child(parent_id INTEGER REFERENCES synthetic_parent(id) DEFERRABLE INITIALLY DEFERRED)")
        connection = self.store.connect()
        connection.execute("PRAGMA foreign_keys=ON")
        with self.assertRaises(sqlite3.IntegrityError):
            with connection:
                connection.execute("INSERT INTO synthetic_child VALUES (1)")
        self.assert_closed(connection)
        with self.store.connect() as restored:
            self.assertEqual(restored.execute("SELECT COUNT(*) FROM synthetic_child").fetchone()[0], 0)

    def test_direct_caller_can_manage_connection_explicitly(self):
        connection = self.store.connect()
        try:
            connection.execute("INSERT INTO settings VALUES (?, ?)", ("synthetic", "manual"))
            connection.commit()
            self.assertEqual(connection.execute("SELECT value FROM settings WHERE key='synthetic'").fetchone()["value"], "manual")
        finally:
            connection.close()
        self.assert_closed(connection)
        self.assertEqual(self.store.setting("synthetic"), "manual")

    def test_store_initialization_and_operations_leave_no_open_connections(self):
        original = sqlite3.connect
        opened = []

        def track_connection(*args, **kwargs):
            connection = original(*args, **kwargs)
            opened.append(connection)
            return connection

        with mock.patch.object(workbench.sqlite3, "connect", side_effect=track_connection):
            store = workbench.Store(self.store.path)
            for _ in range(10):
                store.set_setting("synthetic", "stored")
                self.assertEqual(store.setting("synthetic"), "stored")
                self.assertEqual(store.pending_snapshot(), ([], []))
                self.assertEqual(store.skips(), {})
        self.assertGreater(len(opened), 20)
        for connection in opened:
            self.assert_closed(connection)


if __name__ == "__main__":
    unittest.main()
