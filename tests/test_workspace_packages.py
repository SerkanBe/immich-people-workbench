# Copyright (C) 2026 Serkan Bekdemir
# SPDX-License-Identifier: AGPL-3.0-only
"""Synthetic fixtures for the bounded canonical results ZIP writer."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
import uuid
import zipfile


sys.path.insert(0, str(Path(__file__).parents[1]))
import workspace_packages as PACKAGES  # noqa: E402


def entity_id(value):
    return str(uuid.UUID(int=value))


def synthetic_results():
    return {
        "format": PACKAGES.RESULTS_FORMAT,
        "formatVersion": PACKAGES.RESULTS_VERSION,
        "sourceInstanceId": entity_id(1),
        "exportId": entity_id(2),
        "portableInstanceId": entity_id(3),
        "baselineSha256": "a" * 64,
        "mediaSnapshotId": "b" * 32,
        "events": [
            {
                "id": entity_id(4),
                "kind": "person-name",
                "operations": [
                    {
                        "id": entity_id(5),
                        "type": "person.pending.set",
                        "key": [entity_id(6)],
                        "value": {
                            "operation": "rename",
                            "name": "Synthetic Person",
                            "targetPersonId": None,
                            "featureAssetId": None,
                            "included": True,
                        },
                    },
                ],
            },
        ],
    }


def encoded_length(value):
    encoder = json.JSONEncoder(ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return sum(len(part.encode("utf-8")) for part in encoder.iterencode(value))


def sized_results(target_bytes):
    """Make a valid near-ceiling v1 mapping without unknown padding fields."""
    count = 32_000
    events = []
    for index in range(count):
        events.append({
            "id": entity_id(10_000 + index),
            "kind": "person-name",
            "operations": [{
                "id": entity_id(100_000 + index),
                "type": "person.pending.set",
                "key": [entity_id(200_000 + index)],
                "value": {
                    "operation": "rename",
                    "name": "",
                    "targetPersonId": None,
                    "featureAssetId": None,
                    "included": True,
                },
            }],
        })
    result = synthetic_results()
    result["events"] = events
    extra = target_bytes - encoded_length(result)
    base, remainder = divmod(extra, count)
    if extra < 0 or base > PACKAGES.MAX_NAME_CHARS:
        raise AssertionError("Fixture shape cannot reach requested payload size")
    for index, event in enumerate(events):
        size = base + (1 if index < remainder else 0)
        event["operations"][0]["value"]["name"] = "x" * size
    if encoded_length(result) != target_bytes:
        raise AssertionError("Fixture does not match requested payload size")
    return result


class ResultsPackageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.state_dir = Path(self.directory.name) / "state"
        self.state_dir.mkdir()

    def test_writes_one_canonical_stored_entry_and_reports_hashes(self):
        result = synthetic_results()
        before = copy.deepcopy(result)
        expected = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")

        package = PACKAGES.write_results_zip(result, self.state_dir)

        self.assertEqual(result, before, "writing a return package must preserve all local work")
        self.assertEqual(package.payload_bytes, len(expected))
        self.assertEqual(package.archive_bytes, len(expected) + 122)
        self.assertEqual(package.payload_sha256, hashlib.sha256(expected).hexdigest())
        self.assertEqual((package.event_count, package.operation_count), (1, 1))
        self.assertEqual(package.path.parent, self.state_dir.resolve())
        self.assertTrue(package.path.name.startswith(".offline-results-"))
        self.assertTrue(package.path.name.endswith(".zip.part"))
        with zipfile.ZipFile(package.path) as archive:
            self.assertEqual(archive.namelist(), ["changes.json"])
            self.assertEqual(archive.comment, b"")
            info = archive.getinfo("changes.json")
            self.assertEqual(info.compress_type, zipfile.ZIP_STORED)
            self.assertEqual(info.file_size, len(expected))
            self.assertEqual(info.flag_bits, 0)
            self.assertEqual(info.date_time, (1980, 1, 1, 0, 0, 0))
            self.assertEqual(info.extra, b"")
            self.assertEqual(info.comment, b"")
            self.assertEqual(info.external_attr >> 16, 0o100600)
            self.assertEqual(archive.read(info), expected)
        self.assertEqual(sorted(path.name for path in self.state_dir.iterdir()), [package.path.name])

    def test_writer_is_byte_deterministic_for_the_same_mapping(self):
        first = PACKAGES.write_results_zip(synthetic_results(), self.state_dir)
        second = PACKAGES.write_results_zip(synthetic_results(), self.state_dir)
        self.assertEqual(first.path.read_bytes(), second.path.read_bytes())

    def test_exact_maximum_json_and_wire_payload_is_accepted(self):
        result = sized_results(PACKAGES.MAX_JSON_BYTES)
        package = PACKAGES.write_results_zip(result, self.state_dir)

        self.assertEqual(package.payload_bytes, PACKAGES.MAX_JSON_BYTES)
        self.assertEqual(package.archive_bytes, PACKAGES.MAX_WIRE_BYTES)
        self.assertEqual((package.event_count, package.operation_count), (32_000, 32_000))
        with zipfile.ZipFile(package.path) as archive:
            info = archive.getinfo("changes.json")
            self.assertEqual(archive.namelist(), ["changes.json"])
            self.assertEqual(info.compress_type, zipfile.ZIP_STORED)
            self.assertEqual(info.file_size, PACKAGES.MAX_JSON_BYTES)
            # Reading to EOF verifies the CRC without retaining the whole JSON.
            total = 0
            with archive.open(info) as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    total += len(block)
            self.assertEqual(total, PACKAGES.MAX_JSON_BYTES)

    def test_one_byte_over_payload_limit_is_rejected_and_partial_removed(self):
        result = sized_results(PACKAGES.MAX_JSON_BYTES + 1)
        original_last_name = result["events"][-1]["operations"][0]["value"]["name"]

        with self.assertRaisesRegex(PACKAGES.ResultsPackageError, "payload limit"):
            PACKAGES.write_results_zip(result, self.state_dir)

        self.assertEqual(list(self.state_dir.iterdir()), [])
        self.assertEqual(result["events"][-1]["operations"][0]["value"]["name"], original_last_name)

    def test_rejects_invalid_format_json_values_and_depth_before_staging(self):
        invalid = []
        wrong_format = synthetic_results()
        wrong_format["formatVersion"] = 2
        invalid.append(wrong_format)

        floating_point = synthetic_results()
        floating_point["events"][0]["operations"][0]["value"]["number"] = 1.5
        invalid.append(floating_point)

        large_integer = synthetic_results()
        large_integer["events"][0]["operations"][0]["value"]["number"] = PACKAGES.MAX_SAFE_INTEGER + 1
        invalid.append(large_integer)

        too_deep = synthetic_results()
        nested = "end"
        for _ in range(PACKAGES.MAX_JSON_DEPTH + 1):
            nested = {"nested": nested}
        too_deep["events"][0]["operations"][0]["value"]["nested"] = nested
        invalid.append(too_deep)

        too_long = synthetic_results()
        too_long["events"][0]["operations"][0]["value"]["extra"] = "x" * (PACKAGES.MAX_TEXT_CHARS + 1)
        invalid.append(too_long)

        too_many_object_keys = synthetic_results()
        too_many_object_keys["events"][0]["operations"][0]["value"].update({
            f"extra-{index}": None for index in range(PACKAGES.MAX_OBJECT_KEYS)
        })
        invalid.append(too_many_object_keys)

        too_long_name = synthetic_results()
        too_long_name["events"][0]["operations"][0]["value"]["name"] = "x" * (PACKAGES.MAX_NAME_CHARS + 1)
        invalid.append(too_long_name)

        duplicate_operation = synthetic_results()
        duplicate_operation["events"][0]["operations"].append(copy.deepcopy(duplicate_operation["events"][0]["operations"][0]))
        invalid.append(duplicate_operation)

        for result in invalid:
            with self.subTest(result=result):
                with self.assertRaises(PACKAGES.ResultsPackageError):
                    PACKAGES.write_results_zip(result, self.state_dir)
                self.assertEqual(list(self.state_dir.iterdir()), [])

    def test_rejects_operation_count_over_limit(self):
        result = synthetic_results()
        operation = result["events"][0]["operations"][0]
        result["events"] = [{"id": entity_id(10), "kind": "bulk", "operations": [
            dict(operation, id=entity_id(100 + index)) for index in range(PACKAGES.MAX_OPERATIONS + 1)
        ]}]

        with self.assertRaises(PACKAGES.ResultsPackageError):
            PACKAGES.write_results_zip(result, self.state_dir)
        self.assertEqual(list(self.state_dir.iterdir()), [])

    def test_rejects_reference_count_over_limit(self):
        result = synthetic_results()
        result["events"][0]["operations"][0]["key"] = [entity_id(20)] * (PACKAGES.MAX_REFERENCES + 1)

        with self.assertRaisesRegex(PACKAGES.ResultsPackageError, "reference limit"):
            PACKAGES.write_results_zip(result, self.state_dir)
        self.assertEqual(list(self.state_dir.iterdir()), [])




# Full portable packages use only isolated synthetic state and generated media.
from contextlib import closing, contextmanager
import dataclasses
import shutil
import sqlite3
import subprocess
from types import SimpleNamespace
from unittest.mock import patch
import threading

import people_workbench as app
import workspace_state as ws


class PortablePackageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.runtime = self.root / 'runtime'
        self.runtime.mkdir()
        (self.runtime / 'web').mkdir()
        source = Path(__file__).parents[1]
        for name in (*PACKAGES.RUNTIME_FILES, *('web/' + n for n in PACKAGES.WEB_FILES)):
            shutil.copyfile(source / name, self.runtime / name)
        self.canary = 'SYNTHETIC-PRIVATE-CANARY-' + 'z' * 200
        (self.runtime / '.env').write_text(self.canary)
        (self.runtime / 'private.key').write_text(self.canary)
        self.state_dir = self.root / 'source-state'
        self.state_dir.mkdir()
        self.state = app.AppState(app.Store(self.state_dir / 'state.sqlite3'), self.state_dir / 'reports')
        self.owner = {'url': 'https://photos.example.test/api', 'userId': entity_id(500)}
        self.state.store.set_setting('owner', ws.canonical(self.owner).decode())
        self.state.store.set_setting('legacy_secret', self.canary)
        with self.state.store.connect() as db:
            db.execute('PRAGMA secure_delete=OFF')
            db.execute("INSERT INTO settings VALUES('deleted_secret',?)", (self.canary * 100,))
            db.execute("DELETE FROM settings WHERE key='deleted_secret'")
        self.client = SimpleNamespace(base=self.owner['url'], connection_metadata={'userId': self.owner['userId']})
        self.state.client = self.client
        self.sid = 'f' * 32
        self.asset, self.person, self.face = (entity_id(i) for i in (510, 511, 512))
        self.snapshot_root = self.state.offline.root / 'snapshots' / self.sid
        (self.snapshot_root / 'originals').mkdir(parents=True)
        (self.snapshot_root / 'originals' / self.asset).write_bytes(b'synthetic-original')
        data = b'synthetic-original'
        self.snapshot = {
            'formatVersion': 1, 'id': self.sid, 'owner': self.owner,
            'createdAt': '2026-11-11T00:00:00Z', 'serverVersion': 'synthetic',
            'people': {self.person: {'id': self.person, 'name': '', 'isHidden': False, 'assetCount': 2, 'privateField': self.canary}},
            'assets': {self.asset: {'id': self.asset, 'type': 'IMAGE', 'originalFileName': 'synthetic.jpg', 'originalPath': self.canary}},
            'faces': {self.asset: [{'id': self.face, 'assetId': self.asset, 'person': {'id': self.person},
                'boundingBoxX1': 1, 'boundingBoxX2': 3, 'boundingBoxY1': 1, 'boundingBoxY2': 3, 'imageWidth': 10, 'imageHeight': 10}]},
            'media': {'originals': {self.asset: {'path': 'originals/' + self.asset, 'size': len(data),
                'sha256': hashlib.sha256(data).hexdigest(), 'mime': 'image/jpeg'}}, 'previews': {}, 'people': {}},
            'assetCount': 1, 'personCount': 1, 'totalBytes': len(data),
        }
        self.save_snapshot()
        self.state.store.upsert_pending({'personId': self.person, 'operation': 'rename', 'name': 'Synthetic Name'})
        self.manager = PACKAGES.PackageManager(self.state, self.runtime)
        self.addCleanup(self.manager.close)

    def save_snapshot(self):
        (self.snapshot_root / 'snapshot.json').write_bytes(ws.canonical(self.snapshot))
        (self.state.offline.root / 'active.json').write_bytes(ws.canonical({'id': self.sid}))

    def wait(self, manager=None):
        manager = manager or self.manager
        manager.worker.join(15)
        self.assertFalse(manager.worker.is_alive())
        return manager.list()[0]

    def build(self):
        result = self.manager.start('Synthetic trip')
        row = self.wait()
        self.assertEqual(row['id'], result['id'])
        self.assertEqual(row['status'], 'ready', row)
        self.assertTrue(row['downloadReady'])
        return row, self.manager.download(row['id'])

    def test_portable_zip_is_allowlisted_clean_and_runs_from_extraction(self):
        row, path = self.build()
        self.assertEqual(row['mediaBytes'], len(b'synthetic-original'))
        self.assertEqual(row['importSummary']['importedGroups'], 0)
        extracted = self.root / 'extracted'
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            self.assertIn('portable-workspace.json', names)
            self.assertIn('.people-workbench/state.sqlite3', names)
            self.assertIn('START_HERE.txt', names)
            self.assertFalse(any(name.endswith(('.env', 'private.key', '-wal', '-shm')) for name in names))
            self.assertEqual(len(names), len(set(names)))
            manifest = json.loads(archive.read('package-manifest.json'))
            for name, item in manifest['files'].items():
                data = archive.read(name)
                self.assertEqual(hashlib.sha256(data).hexdigest(), item['sha256'])
                self.assertEqual(len(data), item['size'])
                self.assertNotIn(self.canary.encode(), data, name)
            archive.extractall(extracted)  # Trusted, self-produced fixture only.
        with closing(sqlite3.connect(extracted / '.people-workbench/state.sqlite3')) as db:
            self.assertIsNone(db.execute("SELECT value FROM settings WHERE key='legacy_secret'").fetchone())
            self.assertEqual(db.execute('SELECT name FROM pending').fetchone()[0], 'Synthetic Name')
            descriptor = json.loads((extracted / 'portable-workspace.json').read_text())
            self.assertEqual(ws.portable_descriptor(db), descriptor)
        code = """
import json
from pathlib import Path
import urllib.request
urllib.request.OpenerDirector.open = lambda *a, **k: (_ for _ in ()).throw(AssertionError('network fallback'))
import people_workbench as app
import workspace_state as ws
state = app.AppState(app.Store(Path('.people-workbench/state.sqlite3')), Path('.people-workbench/reports'))
client = state.offline.open_client()
assert len(client.all_people()) == 1
assert not client.coverage(client.all_people()[0]['id'])['complete']
with state.store.connect() as db:
    assert ws.portable_descriptor(db)['format'] == 'people-workbench.portable'
print('extracted portable runtime and partial coverage passed')
"""
        done = subprocess.run([sys.executable, '-B', '-c', code], cwd=extracted, capture_output=True, text=True, timeout=15)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn('extracted portable runtime', done.stdout)

    def test_large_original_uses_file_stream_without_normal_media_cap(self):
        data = b'x' * (21 * 1024 * 1024)
        (self.snapshot_root / 'originals' / self.asset).write_bytes(data)
        item = self.snapshot['media']['originals'][self.asset]
        item.update(size=len(data), sha256=hashlib.sha256(data).hexdigest())
        self.save_snapshot()
        _, path = self.build()
        with zipfile.ZipFile(path) as archive:
            name = '.people-workbench/offline/snapshots/' + self.sid + '/originals/' + self.asset
            self.assertEqual(archive.getinfo(name).file_size, len(data))
            self.assertEqual(hashlib.sha256(archive.read(name)).hexdigest(), item['sha256'])

    def test_cancel_keeps_prior_ready_archive_and_rejects_duplicate_start(self):
        first, path = self.build()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        entered, release = threading.Event(), threading.Event()
        original = self.manager._write_member
        def blocked(*args, **kwargs):
            entered.set()
            release.wait(5)
            return original(*args, **kwargs)
        with patch.object(self.manager, '_write_member', side_effect=blocked):
            second = self.manager.start('Cancelled trip')
            self.assertTrue(entered.wait(5))
            with self.assertRaises(ws.WorkspaceError) as busy:
                self.manager.start('Duplicate')
            self.assertEqual(busy.exception.code, 'resource_busy')
            self.manager.cancel(second['id'])
            release.set()
            self.manager.worker.join(10)
        rows = {row['id']: row for row in self.manager.list()}
        self.assertEqual(rows[second['id']]['error'], 'cancelled')
        self.assertFalse(rows[second['id']]['downloadReady'])
        self.assertTrue(rows[first['id']]['downloadReady'])
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)

    def test_client_rotation_prevents_ready_publication(self):
        entered, release = threading.Event(), threading.Event()
        original = self.manager._write_member
        def blocked(*args, **kwargs):
            entered.set()
            release.wait(5)
            return original(*args, **kwargs)
        with patch.object(self.manager, '_write_member', side_effect=blocked):
            self.manager.start('Rotated trip')
            self.assertTrue(entered.wait(5))
            self.state.client = SimpleNamespace(base=self.owner['url'])
            release.set()
            row = self.wait()
        self.assertEqual(row['status'], 'error')
        self.assertEqual(row['error'], 'source_mismatch')
        self.assertEqual(list(self.manager.root.glob('*.zip')), [])

    def test_checksum_corruption_is_not_published(self):
        (self.snapshot_root / 'originals' / self.asset).write_bytes(b'bad-original-bytes')
        # Size is checked at capture too; keep it equal for the streaming check.
        item = self.snapshot['media']['originals'][self.asset]
        item['size'] = len(b'bad-original-bytes')
        self.save_snapshot()
        self.manager.start('Corrupt trip')
        row = self.wait()
        self.assertEqual(row['status'], 'error')
        self.assertFalse(row['downloadReady'])
        self.assertEqual(list(self.manager.root.glob('*.zip')), [])

    def test_no_space_and_history_quota_leave_source_and_registry_intact(self):
        with patch.object(PACKAGES.shutil, 'disk_usage', return_value=SimpleNamespace(free=0)):
            with self.assertRaises(ws.WorkspaceError) as full:
                self.manager.start('No space')
        self.assertEqual(full.exception.code, 'insufficient_storage')
        self.assertEqual(self.manager.list(), [])
        self.manager.state_limits = dataclasses.replace(ws.DEFAULT_LIMITS, ready_archive_count=0)
        with self.assertRaises(ws.WorkspaceError) as quota:
            self.manager.start('No quota')
        self.assertEqual(quota.exception.code, 'storage_quota')
        self.assertEqual(self.manager.list(), [])
        self.assertEqual(len(self.state.store.pending()), 1)

    def test_restart_marks_interrupted_job_failed_and_preserves_orphan(self):
        with self.state.store.workflow_transaction('test-reserve') as db:
            capture = ws.capture_state(db)
            record = ws.reserve_export(db, 'Interrupted', capture, {
                'snapshotId': self.sid, 'sha256': 'a' * 64, 'assetCount': 1, 'mediaBytes': 1}, inventory=self.snapshot)
        orphan = self.manager.root / (record['id'] + '.zip')
        orphan.write_bytes(b'synthetic-unpublished-archive')
        self.manager.close()
        reopened = PACKAGES.PackageManager(self.state, self.runtime)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.list()[0]['error'], 'cancelled')
        with self.assertRaises(ws.WorkspaceError):
            reopened.download(record['id'])
        self.assertTrue(orphan.exists())

    def test_symlink_media_and_database_limit_fail_safely(self):
        path = self.snapshot_root / 'originals' / self.asset
        external = self.root / 'outside-original'
        path.rename(external)
        path.symlink_to(external)
        with self.assertRaises(ws.WorkspaceError):
            self.manager.start('Unsafe')
        path.unlink()
        external.rename(path)
        self.manager.limits = dataclasses.replace(PACKAGES.PackageLimits(), database_bytes=1)
        self.manager.start('Tiny database limit')
        row = self.wait()
        self.assertEqual(row['error'], 'resource_limit')

    def test_python_and_docker_runtime_allowlists_match(self):
        source = Path(__file__).parents[1]
        dockerfile = (source / 'Dockerfile').read_text()
        ignore = (source / '.dockerignore').read_text().splitlines()
        for name in PACKAGES.RUNTIME_FILES:
            self.assertIn(name, dockerfile)
            self.assertIn('!' + name, ignore)
        for name in PACKAGES.WEB_FILES:
            self.assertIn('!web/' + name, ignore)


    def test_extracted_offline_assets_preserve_order_and_dimension_metadata(self):
        second = entity_id(509)  # sorts before the original ID but is the older photo
        self.snapshot['assets'][self.asset].update(width=4000, height=3000, originalMimeType='image/jpeg',
            fileCreatedAt='2026-11-11T08:00:00Z', localDateTime='2026-11-11T08:00:00Z', updatedAt='2026-11-11T09:00:00Z',
            exifInfo={'exifImageWidth': 4000, 'exifImageHeight': 3000, 'orientation': 6, 'latitude': self.canary})
        self.snapshot['assets'][second] = {'id': second, 'type': 'IMAGE', 'fileCreatedAt': '2026-11-10T08:00:00Z'}
        self.snapshot['faces'][second] = [dict(self.snapshot['faces'][self.asset][0], id=entity_id(513), assetId=second)]
        item = dict(self.snapshot['media']['originals'][self.asset], path='originals/' + second)
        self.snapshot['media']['originals'][second] = item
        (self.snapshot_root / 'originals' / second).write_bytes(b'synthetic-original')
        # The source preparation preserves its photo-page order in JSON.
        (self.snapshot_root / 'snapshot.json').write_text(json.dumps(self.snapshot))
        _, path = self.build()
        extracted = self.root / 'metadata-extracted'
        with zipfile.ZipFile(path) as archive:
            archive.extractall(extracted)
        code = """
from pathlib import Path
import people_workbench as app
state = app.AppState(app.Store(Path('.people-workbench/state.sqlite3')), Path('.people-workbench/reports'))
client = state.offline.open_client()
person = client.all_people()[0]['id']
rows, more, total = client.person_assets_page(person, 1, 100)
assert [a['fileCreatedAt'] for a in rows] == ['2026-11-11T08:00:00Z', '2026-11-10T08:00:00Z']
assert rows[0]['width'] == 4000 and rows[0]['height'] == 3000
assert rows[0]['originalMimeType'] == 'image/jpeg'
assert rows[0]['localDateTime'] == '2026-11-11T08:00:00Z'
assert rows[0]['updatedAt'] == '2026-11-11T09:00:00Z'
assert rows[0]['exifInfo'] == {'exifImageWidth': 4000, 'exifImageHeight': 3000, 'orientation': 6}
print('offline ordering and dimensions passed')
"""
        done = subprocess.run([sys.executable, '-B', '-c', code], cwd=extracted, capture_output=True, text=True, timeout=15)
        self.assertEqual(done.returncode, 0, done.stderr)


    def test_deadline_prevents_publication(self):
        original = self.manager._write_member
        def expired(archive, name, chunks, job, expected=None):
            job['deadline'] = 0
            return original(archive, name, chunks, job, expected)
        with patch.object(self.manager, '_write_member', side_effect=expired):
            self.manager.start('Expired trip')
            row = self.wait()
        self.assertEqual(row['error'], 'export_timeout')
        self.assertFalse(row['downloadReady'])
        self.assertEqual(list(self.manager.root.glob('*.zip')), [])

    def test_snapshot_change_during_build_prevents_publication(self):
        original = self.manager._write_member
        def replace_snapshot(*args, **kwargs):
            (self.state.offline.root / 'active.json').write_text(json.dumps({'id': self.sid, 'stale': True}))
            return original(*args, **kwargs)
        with patch.object(self.manager, '_write_member', side_effect=replace_snapshot):
            self.manager.start('Stale trip')
            row = self.wait()
        self.assertEqual(row['status'], 'error')
        self.assertFalse(row['downloadReady'])
        self.assertEqual(list(self.manager.root.glob('*.zip')), [])

    def test_failed_publication_transaction_retains_undownloadable_orphan(self):
        original = self.state.store.workflow_transaction
        @contextmanager
        def fail_commit(kind, *args, **kwargs):
            with original(kind, *args, **kwargs) as db:
                yield db
                if kind == 'export-publish':
                    raise RuntimeError('synthetic precommit failure after rename')
        with patch.object(self.state.store, 'workflow_transaction', side_effect=fail_commit):
            result = self.manager.start('Failed commit trip')
            row = self.wait()
        self.assertEqual(row['status'], 'error')
        self.assertFalse(row['downloadReady'])
        orphan = self.manager.root / (result['id'] + '.zip')
        self.assertTrue(orphan.is_file())
        with self.assertRaises(ws.WorkspaceError):
            self.manager.download(result['id'])
        self.assertGreaterEqual(self.manager._disk_usage(), orphan.stat().st_size)


if __name__ == "__main__":
    unittest.main()
