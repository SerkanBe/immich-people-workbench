# Copyright (C) 2026 Serkan Bekdemir
# SPDX-License-Identifier: AGPL-3.0-only
"""Write bounded, data-only offline results packages.

This module intentionally has no dependency on the running Workbench or on
workspace_state.py. The caller supplies a captured v1 results mapping and a
private directory on the state volume. The returned ``.part`` file is not a
published/downloadable artifact; its owner performs the registry/final rename.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any
import uuid
import zipfile


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
