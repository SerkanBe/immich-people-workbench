# Copyright (C) 2026 Serkan Bekdemir
# SPDX-License-Identifier: AGPL-3.0-only
"""Write bounded, data-only offline results packages.

No application import, default state access, or network calls occur here.
The results writer accepts a captured v1 mapping and returns a private staging
file. PackageManager uses workspace_state's explicit capture/registry APIs to
build portable ZIPs under the supplied AppState's state volume.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import errno
import os
from pathlib import Path
import re
import stat
import shutil
import tempfile
import threading
import time
from typing import Any
import uuid
import zipfile
import workspace_state as ws


MAX_WIRE_BYTES = 16 * 1024 * 1024
# A seekable, single-member ZIP with no extras/comments has 30-byte local
# header + 12-byte filename, 46-byte central record + 12-byte filename, and
# the 22-byte EOCD record.
ZIP_OVERHEAD_BYTES = 122
MAX_JSON_BYTES = MAX_WIRE_BYTES - ZIP_OVERHEAD_BYTES
MAX_EVENTS = 50_000
MAX_OPERATIONS = 50_000
MAX_REFERENCES = 200_000
MAX_JSON_DEPTH = 16
MAX_OBJECT_KEYS = 32
MAX_JSON_NODES = 1_000_000
MAX_NAME_CHARS = 256
MAX_NAME_UTF8_BYTES = 1_024
MAX_TEXT_CHARS = 4_096
MAX_TEXT_UTF8_BYTES = 16_384
MAX_SAFE_INTEGER = (1 << 53) - 1

RESULTS_FORMAT = "people-workbench.results"
RESULTS_VERSION = 1
ENTRY_NAME = "changes.json"

_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")
_SNAPSHOT_ID_RE = re.compile(r"^[0-9a-fA-F-]{20,50}$")
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_ROOT_FIELDS = frozenset({
    "format", "formatVersion", "sourceInstanceId", "exportId",
    "portableInstanceId", "baselineSha256", "mediaSnapshotId", "events",
})


class ResultsPackageError(ValueError):
    """A results mapping cannot be represented by the bounded v1 format."""


@dataclass(frozen=True)
class ResultsPackage:
    """Metadata for a completed private staging file."""

    path: Path
    archive_bytes: int
    payload_bytes: int
    payload_sha256: str
    event_count: int
    operation_count: int


def _utf8_size(value: str) -> int:
    try:
        return len(value.encode("utf-8", errors="strict"))
    except UnicodeEncodeError:
        raise ResultsPackageError("Results contain invalid Unicode") from None


def _validate_string(value: str, key: str | None) -> None:
    encoded_size = _utf8_size(value)
    if key == "name":
        if len(value) > MAX_NAME_CHARS or encoded_size > MAX_NAME_UTF8_BYTES:
            raise ResultsPackageError("A results name exceeds its v1 limit")
    elif len(value) > MAX_TEXT_CHARS or encoded_size > MAX_TEXT_UTF8_BYTES:
        raise ResultsPackageError("A results text field exceeds its v1 limit")


def _validate_results(results: Mapping[str, Any]) -> tuple[int, int]:
    if not isinstance(results, Mapping):
        raise ResultsPackageError("Results must be a v1 mapping")
    # Copy just the eight envelope entries. Nested values are read-only by
    # contract: A captures the mapping before this potentially long write.
    try:
        root = dict(results)
    except Exception:
        raise ResultsPackageError("Results mapping could not be read") from None
    if set(root) != _ROOT_FIELDS:
        raise ResultsPackageError("Results have an unsupported v1 envelope")
    if root.get("format") != RESULTS_FORMAT or type(root.get("formatVersion")) is not int or root["formatVersion"] != RESULTS_VERSION:
        raise ResultsPackageError("Results format is unsupported")
    for key in ("sourceInstanceId", "exportId", "portableInstanceId"):
        value = root.get(key)
        if not isinstance(value, str) or not _UUID_RE.fullmatch(value):
            raise ResultsPackageError("Results contain an invalid identity")
    if not isinstance(root.get("mediaSnapshotId"), str) or not _SNAPSHOT_ID_RE.fullmatch(root["mediaSnapshotId"]):
        raise ResultsPackageError("Results contain an invalid snapshot identity")
    if not isinstance(root.get("baselineSha256"), str) or not _SHA256_RE.fullmatch(root["baselineSha256"]):
        raise ResultsPackageError("Results contain an invalid baseline digest")
    events = root.get("events")
    if type(events) is not list or len(events) > MAX_EVENTS:
        raise ResultsPackageError("Results exceed the event limit")

    operation_count = 0
    for event in events:
        if type(event) is not dict or type(event.get("operations")) is not list or not event["operations"]:
            raise ResultsPackageError("A results event must contain typed operations")
        operation_count += len(event["operations"])
        if operation_count > MAX_OPERATIONS:
            raise ResultsPackageError("Results exceed the operation limit")

    event_ids: set[str] = set()
    operation_ids: set[str] = set()
    references = 0
    nodes = 0

    # Iterative traversal enforces depth before the standard encoder recurses.
    pending: list[tuple[Any, str | None, int]] = [(root, None, 0)]
    while pending:
        value, key, depth = pending.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES:
            raise ResultsPackageError("Results exceed the structural node limit")
        if depth > MAX_JSON_DEPTH:
            raise ResultsPackageError("Results exceed the JSON nesting limit")

        if type(value) is dict:
            if len(value) > MAX_OBJECT_KEYS:
                raise ResultsPackageError("A results object exceeds its key limit")
            for child_key, child_value in value.items():
                if not isinstance(child_key, str):
                    raise ResultsPackageError("Results object keys must be strings")
                _validate_string(child_key, None)
                nodes += 1  # JSON object keys are tokens too.
                if nodes > MAX_JSON_NODES:
                    raise ResultsPackageError("Results exceed the structural node limit")
                pending.append((child_value, child_key, depth + 1))
                if child_key == "name" and type(child_value) is str:
                    _validate_string(child_value, child_key)
                if child_key.endswith("Id") and child_value is not None:
                    if type(child_value) is not str or not _SNAPSHOT_ID_RE.fullmatch(child_value):
                        raise ResultsPackageError("Results contain an invalid entity reference")
                    references += 1
                if child_key in {"key", "references", "dependencies", "ids"} and type(child_value) is list:
                    references += len(child_value)
                    if references > MAX_REFERENCES:
                        raise ResultsPackageError("Results exceed the reference limit")
        elif type(value) is list:
            pending.extend((item, None, depth + 1) for item in value)
        elif type(value) is str:
            _validate_string(value, key)
            if key is not None and key.endswith("Sha256") and not _SHA256_RE.fullmatch(value):
                raise ResultsPackageError("Results contain an invalid baseline digest")
            if key in {"sourceInstanceId", "exportId", "portableInstanceId", "id"}:
                if not _UUID_RE.fullmatch(value):
                    raise ResultsPackageError("Results contain an invalid UUID")
        elif value is None or type(value) is bool:
            pass
        elif type(value) is int:
            if value < 0 or value > MAX_SAFE_INTEGER:
                raise ResultsPackageError("A results integer is outside its v1 range")
        else:
            # Floats (including NaN/Infinity), tuples, and custom objects are
            # deliberately outside the canonical JSON v1 value model.
            raise ResultsPackageError("Results contain an unsupported JSON value")

    for event in events:
        if type(event) is not dict or not {"id", "kind", "operations"}.issubset(event):
            raise ResultsPackageError("A results event has an invalid shape")
        if set(event) != {"id", "kind", "operations"}:
            raise ResultsPackageError("A results event has unsupported fields")
        event_id, kind, operations = event["id"], event["kind"], event["operations"]
        if type(event_id) is not str or not _UUID_RE.fullmatch(event_id) or event_id in event_ids:
            raise ResultsPackageError("Results contain a duplicate or invalid event ID")
        if type(kind) is not str or type(operations) is not list or not operations:
            raise ResultsPackageError("A results event must contain typed operations")
        event_ids.add(event_id)
        for operation in operations:
            if type(operation) is not dict or type(operation.get("id")) is not str or type(operation.get("type")) is not str:
                raise ResultsPackageError("A results operation has an invalid shape")
            operation_id = operation["id"]
            if not _UUID_RE.fullmatch(operation_id) or operation_id in operation_ids:
                raise ResultsPackageError("Results contain a duplicate or invalid operation ID")
            operation_ids.add(operation_id)

    return len(events), operation_count


def _encoder() -> json.JSONEncoder:
    return json.JSONEncoder(
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def write_results_zip(results: Mapping[str, Any], state_dir: str | os.PathLike[str]) -> ResultsPackage:
    """Write a seekable canonical result ZIP into ``state_dir``.

    The returned opaque ``.part`` path is private staging material. A caller
    must register it and perform the contract's guarded atomic ready rename.
    This function reads but never clears or modifies ``results``.
    """
    captured = dict(results) if isinstance(results, Mapping) else results
    event_count, operation_count = _validate_results(captured)
    root = Path(state_dir)
    try:
        root = root.resolve(strict=True)
        if not root.is_dir():
            raise ResultsPackageError("The state volume must be a directory")
        fd, raw_path = tempfile.mkstemp(prefix=".offline-results-", suffix=".zip.part", dir=root)
    except ResultsPackageError:
        raise
    except OSError:
        raise ResultsPackageError("Could not create results staging file") from None

    path = Path(raw_path)
    payload_digest = hashlib.sha256()
    payload_bytes = 0
    try:
        with os.fdopen(fd, "w+b") as stream:
            if not stream.seekable():
                raise ResultsPackageError("Results staging must be seekable")
            with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED, allowZip64=False) as archive:
                archive.comment = b""
                info = zipfile.ZipInfo(ENTRY_NAME, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_STORED
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | 0o600) << 16
                info.flag_bits = 0
                info.extra = b""
                info.comment = b""
                with archive.open(info, "w", force_zip64=False) as member:
                    for text in _encoder().iterencode(captured):
                        chunk = text.encode("utf-8", errors="strict")
                        next_size = payload_bytes + len(chunk)
                        if next_size > MAX_JSON_BYTES:
                            raise ResultsPackageError("Results exceed the v1 payload limit")
                        member.write(chunk)
                        payload_digest.update(chunk)
                        payload_bytes = next_size
            stream.flush()
            os.fsync(stream.fileno())

        archive_bytes = path.stat().st_size
        if archive_bytes != payload_bytes + ZIP_OVERHEAD_BYTES or archive_bytes > MAX_WIRE_BYTES:
            raise ResultsPackageError("Results ZIP does not match the canonical v1 layout")
        return ResultsPackage(
            path=path,
            archive_bytes=archive_bytes,
            payload_bytes=payload_bytes,
            payload_sha256=payload_digest.hexdigest(),
            event_count=event_count,
            operation_count=operation_count,
        )
    except ResultsPackageError:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    except Exception:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise ResultsPackageError("Could not write the bounded results package") from None


# These files are available in both a source checkout and the Docker /app root.
RUNTIME_FILES = (
    'people_workbench.py', 'offline_workspace.py', 'workspace_state.py',
    'workspace_packages.py', 'workspace_import.py', 'LICENSE',
)
WEB_FILES = (
    'index.html', 'login.html', 'styles.css', 'login-key.css', 'app.js',
    'login.js', 'navigation.js', 'asset-preview.js', 'browser-storage.js',
    'face-review.js', 'group-photos.js', 'investigate.js', 'merge.js',
    'naming.js', 'pending.js', 'offline.js',
)
START_HERE = (
    'Extract this ZIP into a new folder on local storage.\n'
    'Install Python 3.10 or newer; no extra packages are required.\n'
    'From this folder run: python people_workbench.py\n'
    'Choose Open saved offline workspace. No Immich login is needed.\n'
    'Review work stays local. Download offline changes and return that small\n'
    'file to the original Workbench for import, Pending review and explicit Sync.\n'
    'This folder contains private photos and names. Protect it like the originals.\n'
).encode('utf-8')


@dataclass(frozen=True)
class PackageLimits:
    archive_bytes: int = 512 * 1024 ** 3
    members: int = 100_000
    directory_bytes: int = 16 * 1024 ** 2
    runtime_bytes: int = 16 * 1024 ** 2
    database_bytes: int = 512 * 1024 ** 2
    json_bytes: int = 64 * 1024 ** 2
    reserve_bytes: int = 64 * 1024 ** 2
    deadline_seconds: float = 4 * 60 * 60


def _package_error(message, code='not_ready'):
    return ws.WorkspaceError(message, code)


def _regular(root, relative):
    """Resolve only fixed/generated relative paths; forbid every symlink part."""
    relative = Path(relative)
    if relative.is_absolute() or '..' in relative.parts:
        raise _package_error('Prepared package path is invalid')
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise _package_error('Prepared package path is unsafe')
    if not current.is_file() or not current.resolve().is_relative_to(root.resolve()):
        raise _package_error('Prepared package file is unavailable')
    return current


class PackageManager:
    """One bounded export worker for one AppState/state directory.

    HTTP/auth admission belongs to the caller. No operation here contacts Immich.
    Run one process/manager per state directory, as required by Store's locks.
    """

    def __init__(self, state, source_root, *, limits=PackageLimits(), state_limits=ws.DEFAULT_LIMITS):
        self.state, self.source_root = state, Path(source_root).resolve()
        self.limits, self.state_limits = limits, state_limits
        self.root = state.store.path.parent / 'offline' / 'packages'
        self.lock = threading.RLock()
        self.worker = None
        self.job = None
        self.closed = False
        if (not 0 < limits.archive_bytes <= 512 * 1024 ** 3
                or not 0 < limits.members <= 100_000
                or not 0 < limits.directory_bytes <= 16 * 1024 ** 2
                or not 0 < limits.runtime_bytes <= 16 * 1024 ** 2
                or not 0 < limits.database_bytes <= 512 * 1024 ** 2
                or not 0 < limits.json_bytes <= 64 * 1024 ** 2
                or limits.reserve_bytes < 64 * 1024 ** 2 or not 0 < limits.deadline_seconds <= 4 * 60 * 60):
            raise ValueError('Invalid package limits')
        with state.mutation_lock:
            for directory in (state.store.path.parent / 'offline', self.root):
                if directory.is_symlink():
                    raise _package_error('Package storage is unsafe')
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            if self.root.is_symlink() or not self.root.resolve().is_relative_to(state.store.path.parent.resolve()):
                raise _package_error('Package storage is unsafe')
            # No process can still own a preparing record after a supported
            # single-process restart. Preserve all files; charge them to quota.
            with state.store.workflow_transaction('export-recovery') as db:
                for (export_id,) in db.execute("SELECT id FROM workspace_exports WHERE status='preparing'").fetchall():
                    ws.fail_export(db, export_id, 'cancelled')

    def _owner(self):
        try:
            owner = json.loads(self.state.store.setting('owner'))
            if (not isinstance(owner, dict) or set(owner) != {'url', 'userId'} or
                    not all(isinstance(value, str) and value for value in owner.values())):
                raise ValueError('invalid owner')
            return owner
        except (ValueError, TypeError):
            raise _package_error('A verified source account is required', 'source_mismatch') from None

    def _read_json(self, relative, limit):
        path = _regular(self.state.store.path.parent, relative)
        with path.open('rb') as stream:
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise _package_error('Prepared metadata exceeds the package limit', 'resource_limit')
        try:
            return json.loads(data)
        except (ValueError, UnicodeError):
            raise _package_error('Prepared metadata is invalid') from None

    def _snapshot(self):
        pointer = self._read_json('offline/active.json', 4096)
        if not isinstance(pointer, dict) or pointer.get('stale'):
            raise _package_error('Prepare a complete current offline workspace first')
        sid = pointer.get('id')
        if not isinstance(sid, str) or not _SNAPSHOT_ID_RE.fullmatch(sid):
            raise _package_error('Prepared workspace identity is invalid')
        snapshot = self._read_json('offline/snapshots/' + sid + '/snapshot.json', self.limits.json_bytes)
        if (not isinstance(snapshot, dict) or snapshot.get('formatVersion') != 1
                or snapshot.get('id') != sid or snapshot.get('owner') != self._owner()):
            raise _package_error('Prepare a complete current offline workspace first')
        return snapshot

    def _runtime(self):
        result, size = {}, 0
        for name in (*RUNTIME_FILES, *('web/' + name for name in WEB_FILES)):
            path = _regular(self.source_root, name)
            with path.open('rb') as stream:
                data = stream.read(self.limits.runtime_bytes - size + 1)
            size += len(data)
            if size > self.limits.runtime_bytes:
                raise _package_error('Application runtime exceeds the package limit', 'resource_limit')
            result[name] = data
        return result

    def _media(self, snapshot):
        """Freeze a bounded manifest; actual bytes/hash are verified while writing."""
        sid = snapshot['id']
        if not isinstance(sid, str) or not _SNAPSHOT_ID_RE.fullmatch(sid):
            raise _package_error('Prepared workspace identity is invalid')
        source = self.state.offline.root / 'snapshots' / sid
        if source.is_symlink():
            raise _package_error('Prepared workspace storage is unsafe')
        if not isinstance(snapshot.get('assets'), dict) or not isinstance(snapshot.get('media'), dict):
            raise _package_error('Prepared media manifest is invalid')
        if any(not isinstance(snapshot['media'].get(kind, {}), dict) for kind in ('originals', 'previews', 'people')):
            raise _package_error('Prepared media manifest is invalid')
        originals = snapshot['media'].get('originals', {})
        if set(originals) != set(snapshot.get('assets', {})):
            raise _package_error('Prepared originals are incomplete')
        result, manifest, size = [], {}, 0
        for kind in ('originals', 'previews', 'people'):
            manifest[kind] = {}
            for item_id, item in snapshot.get('media', {}).get(kind, {}).items():
                if not isinstance(item, dict):
                    raise _package_error('Prepared media manifest is invalid')
                if not isinstance(item_id, str) or not _SNAPSHOT_ID_RE.fullmatch(item_id):
                    raise _package_error('Prepared media identity is invalid')
                relative = kind + '/' + item_id
                if item.get('path') != relative or type(item.get('size')) is not int or item['size'] <= 0:
                    raise _package_error('Prepared media manifest is invalid')
                if not isinstance(item.get('sha256'), str) or not _SHA256_RE.fullmatch(item['sha256']):
                    raise _package_error('Prepared media checksum is missing')
                if item['size'] > (10 * 1024 ** 3 if kind == 'originals' else 20_000_000):
                    raise _package_error('Prepared media exceeds the file limit', 'resource_limit')
                path = _regular(self.state.store.path.parent, Path('offline/snapshots') / sid / relative)
                if path.stat().st_size != item['size']:
                    raise _package_error('Prepared media changed; prepare again')
                mime = item.get('mime', '')
                if not isinstance(mime, str) or not re.fullmatch(r'[a-zA-Z0-9.+-]+/[a-zA-Z0-9.+-]+', mime):
                    raise _package_error('Prepared media type is invalid')
                projected = {key: item[key] for key in ('path', 'size', 'sha256', 'mime')}
                manifest[kind][item_id] = projected
                member = '.people-workbench/offline/snapshots/' + sid + '/' + relative
                result.append((member, path, projected))
                size += item['size']
                if len(result) > self.limits.members or size > self.limits.archive_bytes:
                    raise _package_error('Prepared media exceeds the package limit', 'resource_limit')
        return result, manifest, size

    def _assets(self, snapshot, inventory):
        # Display/order facts are deliberately separate from A's conflict
        # inventory. Preserve prepared ordering and only documented safe fields.
        projected, charged = {}, 0
        for asset_id, original in snapshot['assets'].items():
            row = dict(inventory['assets'][asset_id])
            for field in ('width', 'height'):
                value = original.get(field)
                if type(value) is int and 0 < value <= 1_000_000:
                    row[field] = value
            mime = original.get('originalMimeType')
            if isinstance(mime, str) and re.fullmatch(r'[a-zA-Z0-9.+-]+/[a-zA-Z0-9.+-]+', mime):
                row['originalMimeType'] = mime
            for field in ('fileCreatedAt', 'localDateTime', 'updatedAt'):
                value = original.get(field)
                if isinstance(value, str) and len(value) <= 128 and re.match(r'^\d{4}-\d\d-\d\d[T ]', value):
                    row[field] = value
            exif = original.get('exifInfo')
            if isinstance(exif, dict):
                safe = {}
                for field in ('exifImageWidth', 'exifImageHeight', 'imageWidth', 'imageHeight', 'orientation', 'fileSizeInByte'):
                    value = exif.get(field)
                    if type(value) is int and 0 <= value <= MAX_SAFE_INTEGER:
                        safe[field] = value
                    elif field == 'orientation' and isinstance(value, str) and re.fullmatch(r'[1-8]', value):
                        safe[field] = value
                if safe:
                    row['exifInfo'] = safe
            charged += len(ws.canonical(row)) + len(asset_id) + 8
            if charged > self.limits.json_bytes:
                raise _package_error('Portable metadata exceeds the package limit', 'resource_limit')
            projected[asset_id] = row
        return projected

    def _disk_usage(self):
        size, count = 0, 0
        for parent, dirs, files in os.walk(self.root, followlinks=False):
            for name in dirs + files:
                path = Path(parent) / name
                if path.is_symlink():
                    raise _package_error('Package storage requires inspection', 'storage_quota')
                count += 1
                if count > self.state_limits.export_count * 12 + self.limits.members:
                    raise _package_error('Package storage capacity is full', 'storage_quota')
                if path.is_file():
                    size += path.stat().st_size
        return size

    def _space(self, remaining):
        if shutil.disk_usage(self.root).free < remaining + self.limits.reserve_bytes:
            raise _package_error('Not enough free space for the portable ZIP', 'insufficient_storage')

    def start(self, name):
        with self.state.mutation_lock, self.lock:
            if self.closed or (self.worker and self.worker.is_alive()):
                raise _package_error('A package worker is busy', 'resource_busy')
            client = self.state.client
            if client is None or getattr(client, 'offline', False):
                raise _package_error('Connect to the originating source first', 'source_mismatch')
            snapshot = self._snapshot()
            if (getattr(client, 'base', None) != snapshot['owner']['url'] or
                    getattr(client, 'connection_metadata', {}).get('userId') != snapshot['owner']['userId']):
                raise _package_error('Source client identity changed', 'source_mismatch')
            media, manifest, media_bytes = self._media(snapshot)
            runtime = self._runtime()
            with self.state.store.workflow_transaction('export-capture') as db:
                capture = ws.capture_state(db, limits=self.state_limits)
                if snapshot['owner'] != capture['owner']:
                    raise _package_error('Prepared source account changed', 'source_mismatch')
                # The immutable A baseline is also the portable safe metadata
                # projection. No arbitrary API response dictionary travels.
                media_ref = {'snapshotId': snapshot['id'], 'sha256': ws.digest(manifest),
                             'assetCount': len(snapshot['assets']), 'mediaBytes': media_bytes}
                record = ws.reserve_export(db, name, capture, media_ref, inventory=snapshot, limits=self.state_limits)
                inventory = record['baseline']['inventory']
                portable_snapshot = {
                    'formatVersion': 1, 'id': snapshot['id'], 'owner': capture['owner'],
                    'createdAt': record['createdAt'], 'serverVersion': 'offline',
                    'people': inventory['people'], 'assets': self._assets(snapshot, inventory), 'faces': inventory['faces'],
                    'media': manifest, 'assetCount': len(snapshot['assets']),
                    'personCount': len(inventory['people']), 'totalBytes': media_bytes,
                }
                metadata_bytes = len(ws.canonical(portable_snapshot)) + len(ws.canonical(record['baseline']))
                db_estimate = min(self.limits.database_bytes, max(1024 ** 2, metadata_bytes * 4))
                estimate = media_bytes + sum(map(len, runtime.values())) + db_estimate + metadata_bytes + 16 * 1024 ** 2
                ready_count, ready_bytes = db.execute("SELECT COUNT(*),COALESCE(SUM(archive_bytes),0) FROM workspace_exports WHERE status='ready'").fetchone()
                if (ready_count >= self.state_limits.ready_archive_count or
                        max(ready_bytes, self._disk_usage()) + estimate > self.state_limits.archive_bytes):
                    raise _package_error('Archive storage capacity is full', 'storage_quota')
                if estimate > self.limits.archive_bytes:
                    raise _package_error('Portable ZIP exceeds the package limit', 'resource_limit')
                self._space(estimate + db_estimate)
            job = {'id': record['id'], 'cancel': threading.Event(), 'client': client,
                   'record': record, 'snapshot': portable_snapshot, 'snapshotId': snapshot['id'],
                   'media': media, 'runtime': runtime, 'snapshotDigest': ws.digest([snapshot, list(snapshot['assets'])]), 'bytes': 0, 'files': 0,
                   'deadline': time.monotonic() + self.limits.deadline_seconds}
            self.job = job
            self.worker = threading.Thread(target=self._run, args=(job,), daemon=True, name='portable-package')
            self.worker.start()
            return {'id': record['id']}

    def _check(self, job):
        if job['cancel'].is_set() or self.closed:
            raise _package_error('Portable ZIP preparation cancelled', 'cancelled')
        if time.monotonic() >= job['deadline']:
            raise _package_error('Portable ZIP preparation timed out', 'export_timeout')

    def _write_member(self, archive, name, chunks, job, expected=None):
        self._check(job)
        digest, size = hashlib.sha256(), 0
        info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
        info.compress_type, info.create_system = zipfile.ZIP_STORED, 3
        info.external_attr = (stat.S_IFREG | 0o600) << 16
        with archive.open(info, 'w', force_zip64=True) as output:
            for chunk in (part[offset:offset + 1024 * 1024] for part in chunks
                          for offset in range(0, len(part), 1024 * 1024)):
                self._check(job)
                size += len(chunk)
                if expected and size > expected['size']:
                    raise _package_error('Prepared media changed; prepare again')
                if archive.fp.tell() + len(chunk) + self.limits.directory_bytes > self.limits.archive_bytes:
                    raise _package_error('Portable ZIP exceeds the package limit', 'resource_limit')
                self._space(len(chunk))
                output.write(chunk)
                digest.update(chunk)
                with self.lock:
                    job['bytes'] += len(chunk)
        if expected and (size != expected['size'] or digest.hexdigest() != expected['sha256']):
            raise _package_error('Prepared media checksum changed; prepare again')
        with self.lock:
            job['files'] += 1
        return {'size': size, 'sha256': digest.hexdigest()}

    def _json(self, value, *, preserve_order=False):
        data = (json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
                if preserve_order else ws.canonical(value))
        if len(data) > self.limits.json_bytes:
            raise _package_error('Portable metadata exceeds the package limit', 'resource_limit')
        return data

    @staticmethod
    def _chunks(path):
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(fd, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise _package_error('Prepared package file is unsafe')
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                yield chunk

    def _fence(self, job):
        self._check(job)
        record = job['record']
        if (self.state.client is not job['client'] or self._owner() != record['owner'] or
                getattr(job['client'], 'base', None) != record['owner']['url'] or
                getattr(job['client'], 'connection_metadata', {}).get('userId') != record['owner']['userId']):
            raise _package_error('Source connection changed during export', 'source_mismatch')
        with self.state.store.connect() as db:
            identity = ws.instance_identity(db)
        if identity['mode'] != 'source' or identity['instanceId'] != record['sourceInstanceId']:
            raise _package_error('Source instance changed during export', 'source_mismatch')
        active = self._snapshot()
        if not active or active['id'] != job['snapshotId'] or ws.digest([active, list(active['assets'])]) != job['snapshotDigest']:
            raise _package_error('Prepared workspace changed during export', 'not_ready')

    def _run(self, job):
        work = self.root / ('.build-' + job['id'])
        final = self.root / (job['id'] + '.zip')
        owned_work = False
        try:
            work.mkdir(mode=0o700)
            owned_work = True
            database = work / 'state.sqlite3'
            self._check(job)
            record = job['record']
            descriptor = ws.materialize_portable(record['baseline']['state'], record, database)
            if database.stat().st_size > self.limits.database_bytes:
                raise _package_error('Portable database exceeds the package limit', 'resource_limit')
            sid = job['snapshotId']
            generated = {
                'START_HERE.txt': START_HERE,
                'portable-workspace.json': self._json(descriptor),
                '.people-workbench/offline/active.json': self._json({'id': sid}),
                '.people-workbench/offline/snapshots/' + sid + '/snapshot.json': self._json(job['snapshot'], preserve_order=True),
                '.people-workbench/offline/snapshots/' + sid + '/baseline.json': self._json({
                    'people': job['snapshot']['people'], 'faces': job['snapshot']['faces']}),
            }
            paths = [*job['runtime'], *generated, '.people-workbench/state.sqlite3',
                     *(name for name, _, _ in job['media']), 'package-manifest.json']
            if len(paths) != len(set(paths)) or len(paths) > self.limits.members:
                raise _package_error('Portable ZIP has too many members', 'resource_limit')
            if sum(46 + len(name.encode('utf-8')) + 28 for name in paths) > self.limits.directory_bytes:
                raise _package_error('Portable ZIP directory exceeds the limit', 'resource_limit')
            manifest = {}
            archive_path = work / 'archive.part'
            with archive_path.open('xb') as stream:
                with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
                    for name, data in {**job['runtime'], **generated}.items():
                        manifest[name] = self._write_member(archive, name, (data,), job)
                    manifest['.people-workbench/state.sqlite3'] = self._write_member(
                        archive, '.people-workbench/state.sqlite3', self._chunks(database), job)
                    for name, path, item in job['media']:
                        # Recheck containment/symlinks immediately before opening.
                        path = _regular(self.state.store.path.parent, Path('offline/snapshots') / sid / item['path'])
                        manifest[name] = self._write_member(archive, name, self._chunks(path), job, item)
                    data = self._json({'format': 'people-workbench.package', 'formatVersion': 1,
                                       'exportId': job['id'], 'files': manifest})
                    self._write_member(archive, 'package-manifest.json', (data,), job)
                stream.flush()
                os.fsync(stream.fileno())
            archive_bytes = archive_path.stat().st_size
            if archive_bytes > self.limits.archive_bytes:
                raise _package_error('Portable ZIP exceeds the package limit', 'resource_limit')
            with self.state.mutation_lock, self.lock:
                self._fence(job)
                if final.exists() or final.is_symlink():
                    raise _package_error('Archive publication path already exists')
                with self.state.store.workflow_transaction('export-publish') as db:
                    ws.publish_export(db, job['id'], final.name, archive_bytes,
                                      baseline_sha256=record['baselineSha256'], limits=self.state_limits)
                    archive_path.rename(final)
                    if os.name == 'posix':
                        directory_fd = os.open(self.root, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
                        try:
                            os.fsync(directory_fd)
                        finally:
                            os.close(directory_fd)
        except Exception as exc:
            code = exc.code if isinstance(exc, ws.WorkspaceError) else (
                'insufficient_storage' if isinstance(exc, OSError) and exc.errno == errno.ENOSPC else 'not_ready')
            try:
                with self.state.mutation_lock:
                    with self.state.store.workflow_transaction('export-failed') as db:
                        ws.fail_export(db, job['id'], code)
            except Exception:
                # Retained preparing status still blocks admission; never claim
                # success or discard unknown state after a registry failure.
                pass
        finally:
            # Only this worker's generated ephemeral paths are disposable.
            owned_work = owned_work and not work.is_symlink() and work.parent.resolve() == self.root.resolve()
            for name in (() if not owned_work else ('archive.part', 'state.sqlite3', 'state.sqlite3-journal', 'state.sqlite3-wal', 'state.sqlite3-shm')):
                try:
                    (work / name).unlink(missing_ok=True)
                except OSError:
                    pass
            try:
                if owned_work:
                    work.rmdir()
            except OSError:
                pass
            with self.lock:
                for key in ('record', 'snapshot', 'media', 'runtime', 'client'):
                    job.pop(key, None)
            # If file rename succeeded but DB commit failed, retain the orphan
            # archive and charge it to quota; it is never downloadable as ready.

    def list(self):
        with self.state.mutation_lock:
            owner = self._owner()
            with self.state.store.connect() as db:
                exports = ws.list_exports(db, owner)
            rows = []
            for record in exports:
                ready = False
                if record['status'] == 'ready':
                    try:
                        ready = self._download_path(record).is_file()
                    except ws.WorkspaceError:
                        pass
                row = {key: record[key] for key in ('id', 'name', 'createdAt', 'status', 'importSummary')}
                row.update(assetCount=record['media']['assetCount'], mediaBytes=record['media']['mediaBytes'],
                           archiveBytes=record['archiveBytes'], downloadReady=ready)
                if record['error']:
                    row['error'] = record['error']
                with self.lock:
                    if self.job and self.job['id'] == record['id'] and record['status'] == 'preparing':
                        row['progress'] = {'bytes': self.job['bytes'], 'files': self.job['files']}
                rows.append(row)
            return rows

    def _download_path(self, record):
        if record['status'] != 'ready' or record['archiveRef'] != record['id'] + '.zip':
            raise _package_error('Portable ZIP is not ready')
        path = _regular(self.root, record['archiveRef'])
        if path.stat().st_size != record['archiveBytes']:
            raise _package_error('Portable ZIP is unavailable')
        return path

    def download(self, export_id):
        with self.state.mutation_lock:
            with self.state.store.connect() as db:
                record = ws.read_export_summary(db, export_id, self._owner())
            return self._download_path(record)

    def cancel(self, export_id):
        with self.state.mutation_lock, self.lock:
            if not self.job or self.job['id'] != export_id or not self.worker or not self.worker.is_alive():
                raise _package_error('Portable ZIP is no longer preparing')
            with self.state.store.connect() as db:
                record = ws.read_export_summary(db, export_id, self._owner())
            if record['status'] != 'preparing':
                raise _package_error('Portable ZIP is no longer preparing')
            self.job['cancel'].set()
            return {'id': export_id, 'cancelled': True}

    def close(self, timeout=5):
        with self.state.mutation_lock, self.lock:
            self.closed = True
            if self.job:
                self.job['cancel'].set()
            worker = self.worker
        if worker and worker is not threading.current_thread():
            worker.join(max(0, min(timeout, 10)))
