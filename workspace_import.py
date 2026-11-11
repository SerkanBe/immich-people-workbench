# Copyright (C) 2026 Serkan Bekdemir
# SPDX-License-Identifier: AGPL-3.0-only
"""Read the data-only wire format for offline workspace results."""
from __future__ import annotations

import json
import re
import stat
import struct
from typing import BinaryIO, TypedDict, cast
import zlib
import base64
from contextlib import contextmanager, nullcontext
import datetime as dt
from pathlib import Path
import secrets
import threading
import time
from typing import Any
import uuid

import workspace_state as ws
from offline_workspace import face_person, person_baseline, face_baseline

RESULTS_FORMAT = "people-workbench.results"
RESULTS_FORMAT_VERSION = 1
MAX_RESULTS_WIRE_BYTES = 16 * 1024 * 1024

_ENTRY_NAME = b"changes.json"
_LOCAL = struct.Struct("<4s5H3I2H")
_CENTRAL = struct.Struct("<4s6H3I5H2I")
_END = struct.Struct("<4s4H2IH")
_PAYLOAD_START = _LOCAL.size + len(_ENTRY_NAME)
_CENTRAL_SIZE = _CENTRAL.size + len(_ENTRY_NAME)
RESULTS_ZIP_OVERHEAD = _PAYLOAD_START + _CENTRAL_SIZE + _END.size
MAX_RESULTS_PAYLOAD_BYTES = MAX_RESULTS_WIRE_BYTES - RESULTS_ZIP_OVERHEAD
_READ_CHUNK_BYTES = 1024 * 1024

_MAX_JSON_DEPTH = 16
_MAX_OBJECT_MEMBERS = 32
_MAX_JSON_NODES = 1_000_000
_MAX_JSON_ALLOCATION_ESTIMATE = 192 * 1024 * 1024
_MAX_TEXT_SCALARS = 4096
_MAX_TEXT_BYTES = 4 * _MAX_TEXT_SCALARS
_MAX_ESCAPED_BYTES = 12 * _MAX_TEXT_SCALARS
_MAX_EVENTS = 50_000
_MAX_OPERATIONS = 50_000
_MAX_KEY_REFERENCES = 200_000

_JSON_WHITESPACE = re.compile(rb"[ \t\r\n]*")
_STRING_BOUNDARY = re.compile(rb'["\\\x00-\x1f]')
_LITERAL = re.compile(rb"true|false|null")
_INTEGER = re.compile(rb"(?:0|[1-9][0-9]{0,15})(?![0-9.eE+-])")
_MAX_SAFE_INTEGER = b"9007199254740991"
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_SNAPSHOT_ID = re.compile(r"[0-9a-f]{32}")

_RESULT_FIELDS = frozenset({"format", "formatVersion", "sourceInstanceId", "exportId",
                            "portableInstanceId", "baselineSha256", "mediaSnapshotId", "events"})
_EVENT_FIELDS = frozenset({"id", "kind", "operations"})
_OPERATION_FIELDS = frozenset({"id", "type", "key", "value"})
_FIELD_CONTEXT = {("root", "events"): "events", ("event", "operations"): "operations",
                  ("operation", "key"): "keys"}
_ARRAY_ITEM_CONTEXT = {"events": "event", "operations": "operation"}
_ERROR_MESSAGES = {
    "invalid_results": "The offline results file is invalid.",
    "unsupported_format": "This offline results format is unsupported.",
    "resource_limit": "The offline results file exceeds a supported limit.",
}


class ResultsError(ValueError):
    """A safe, machine-readable rejection of an offline results file."""

    def __init__(self, code: str):
        super().__init__(_ERROR_MESSAGES[code])
        self.code = code


class ResultOperation(TypedDict):
    id: str
    type: str
    key: list[str]
    value: dict[str, object] | None


class ResultEvent(TypedDict):
    id: str
    kind: str
    operations: list[ResultOperation]


class ValidatedResults(TypedDict):
    format: str
    formatVersion: int
    sourceInstanceId: str
    exportId: str
    portableInstanceId: str
    baselineSha256: str
    mediaSnapshotId: str
    events: list[ResultEvent]


def _read_archive(source: bytes | BinaryIO) -> bytes | bytearray:
    if isinstance(source, bytes):
        if len(source) > MAX_RESULTS_WIRE_BYTES:
            raise ResultsError("resource_limit")
        return source

    archive = bytearray()
    while True:
        remaining = MAX_RESULTS_WIRE_BYTES - len(archive)
        requested = min(_READ_CHUNK_BYTES, remaining + 1)
        try:
            block = source.read(requested)
        except (AttributeError, OSError, TypeError, ValueError):
            raise ResultsError("invalid_results") from None
        if not isinstance(block, bytes):
            raise ResultsError("invalid_results")
        if len(block) > remaining:
            raise ResultsError("resource_limit")
        if len(block) > requested:
            raise ResultsError("invalid_results")
        if not block:
            return archive
        archive.extend(block)


def _payload_end(archive: bytes | bytearray) -> int:
    if len(archive) < RESULTS_ZIP_OVERHEAD:
        raise ResultsError("invalid_results")
    end_offset = len(archive) - _END.size
    central_offset = end_offset - _CENTRAL_SIZE
    payload_size = central_offset - _PAYLOAD_START
    if payload_size > MAX_RESULTS_PAYLOAD_BYTES:
        raise ResultsError("resource_limit")

    if _END.unpack_from(archive, end_offset) != (b"PK\x05\x06", 0, 0, 1, 1, _CENTRAL_SIZE, central_offset, 0):
        raise ResultsError("invalid_results")
    (signature, _creator, version, flags, method, modified_time, modified_date,
     crc, stored_size, size, name_size, extra_size, comment_size, disk, _internal_attrs,
     external_attrs, local_offset) = _CENTRAL.unpack_from(archive, central_offset)
    file_type = stat.S_IFMT(external_attrs >> 16)
    if (signature != b"PK\x01\x02" or version not in (10, 20) or flags & ~0x800 or method != 0
            or stored_size != payload_size or size != payload_size or name_size != len(_ENTRY_NAME)
            or extra_size or comment_size or disk or local_offset
            or file_type not in (0, stat.S_IFREG) or external_attrs & 0x18
            or archive[central_offset + _CENTRAL.size:end_offset] != _ENTRY_NAME):
        raise ResultsError("invalid_results")

    if (_LOCAL.unpack_from(archive) != (b"PK\x03\x04", version, flags, method, modified_time, modified_date,
                                       crc, stored_size, size, len(_ENTRY_NAME), 0)
            or archive[_LOCAL.size:_PAYLOAD_START] != _ENTRY_NAME
            or zlib.crc32(memoryview(archive)[_PAYLOAD_START:central_offset]) != crc):
        raise ResultsError("invalid_results")
    return central_offset


class _JsonScan:
    """Check JSON structure and charge decoded allocation before loading the document."""

    def __init__(self, archive: bytes | bytearray, start: int, end: int):
        self.archive = archive
        self.position = start
        self.end = end
        self.nodes = 0
        # Include wire and worst-case decoded Unicode input, plus charges below
        # for the value graph and parser bookkeeping. This estimates allocation.
        self.allocation = len(archive) + 4 * (end - start)
        self.remaining = {"events": _MAX_EVENTS, "operations": _MAX_OPERATIONS, "keys": _MAX_KEY_REFERENCES}

    def check(self):
        self._value(0, "root")
        self._whitespace()
        if self.position != self.end:
            raise ResultsError("invalid_results")

    def _whitespace(self):
        self.position = _JSON_WHITESPACE.match(self.archive, self.position, self.end).end()

    def _take(self, token: bytes) -> bool:
        if self.archive.startswith(token, self.position, self.end):
            self.position += len(token)
            return True
        return False

    def _charge(self, amount: int):
        self.allocation += amount
        if self.allocation > _MAX_JSON_ALLOCATION_ESTIMATE:
            raise ResultsError("resource_limit")

    def _node(self):
        self.nodes += 1
        if self.nodes > _MAX_JSON_NODES:
            raise ResultsError("resource_limit")
        # Cover value/key object overhead and its share of container storage.
        self._charge(128)

    def _value(self, depth: int, context: str):
        self._whitespace()
        self._node()
        if self._take(b"{"):
            self._check_depth(depth)
            self._object(depth + 1, context)
        elif self._take(b"["):
            self._check_depth(depth)
            self._array(depth + 1, context)
        elif self._take(b'"'):
            self._string()
        elif literal := _LITERAL.match(self.archive, self.position, self.end):
            self.position = literal.end()
        else:
            integer = _INTEGER.match(self.archive, self.position, self.end)
            if (integer is None or (integer.end() - self.position == len(_MAX_SAFE_INTEGER)
                                    and integer.group() > _MAX_SAFE_INTEGER)):
                raise ResultsError("invalid_results")
            self.position = integer.end()

    @staticmethod
    def _check_depth(depth: int):
        if depth >= _MAX_JSON_DEPTH:
            raise ResultsError("resource_limit")

    def _object(self, depth: int, context: str):
        self._whitespace()
        if self._take(b"}"):
            return
        seen = set()
        while True:
            if not self._take(b'"'):
                raise ResultsError("invalid_results")
            if len(seen) >= _MAX_OBJECT_MEMBERS:
                raise ResultsError("resource_limit")
            self._node()
            key = self._string()
            if key in seen:
                raise ResultsError("invalid_results")
            seen.add(key)
            self._whitespace()
            if not self._take(b":"):
                raise ResultsError("invalid_results")
            self._value(depth, _FIELD_CONTEXT.get((context, key), ""))
            self._whitespace()
            if self._take(b"}"):
                return
            if not self._take(b","):
                raise ResultsError("invalid_results")
            self._whitespace()

    def _array(self, depth: int, context: str):
        self._whitespace()
        if self._take(b"]"):
            return
        child_context = _ARRAY_ITEM_CONTEXT.get(context, "")
        while True:
            if context in self.remaining:
                self.remaining[context] -= 1
                if self.remaining[context] < 0:
                    raise ResultsError("resource_limit")
            self._value(depth, child_context)
            self._whitespace()
            if self._take(b"]"):
                return
            if not self._take(b","):
                raise ResultsError("invalid_results")

    def _string(self) -> str:
        start = self.position
        end = min(self.end, start + _MAX_ESCAPED_BYTES + 1)
        cursor = start
        while boundary := _STRING_BOUNDARY.search(self.archive, cursor, end):
            index = boundary.start()
            character = boundary.group()
            if character == b"\\":
                cursor = index + 2
            elif character == b'"':
                self.position = index + 1
                return self._decode_string(start, index, cursor != start)
            else:
                raise ResultsError("invalid_results")
        raise ResultsError("resource_limit" if end < self.end else "invalid_results")

    def _decode_string(self, start: int, end: int, escaped: bool) -> str:
        if not escaped and end - start > _MAX_TEXT_BYTES:
            raise ResultsError("resource_limit")
        try:
            if escaped:
                value = json.loads(self.archive[start - 1:end + 1].decode("utf-8"))
                byte_size = len(value.encode("utf-8"))
            else:
                value = self.archive[start:end].decode("utf-8")
                byte_size = end - start
        except (UnicodeError, json.JSONDecodeError):
            raise ResultsError("invalid_results") from None
        if len(value) > _MAX_TEXT_SCALARS or byte_size > _MAX_TEXT_BYTES:
            raise ResultsError("resource_limit")
        self._charge(4 * len(value))
        return value


def _matches(pattern: re.Pattern[str], value: object) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _validate_envelope(results: object) -> ValidatedResults:
    if not isinstance(results, dict) or results.keys() != _RESULT_FIELDS:
        raise ResultsError("invalid_results")
    if not isinstance(results["format"], str) or type(results["formatVersion"]) is not int:
        raise ResultsError("invalid_results")
    if results["format"] != RESULTS_FORMAT or results["formatVersion"] != RESULTS_FORMAT_VERSION:
        raise ResultsError("unsupported_format")
    if (not all(_matches(_UUID, results[key]) for key in ("sourceInstanceId", "exportId", "portableInstanceId"))
            or results["sourceInstanceId"] == results["portableInstanceId"]
            or not _matches(_SHA256, results["baselineSha256"])
            or not _matches(_SNAPSHOT_ID, results["mediaSnapshotId"])
            or not isinstance(results["events"], list)):
        raise ResultsError("invalid_results")

    seen_ids = set()
    for event in results["events"]:
        if (not isinstance(event, dict) or event.keys() != _EVENT_FIELDS or not isinstance(event["kind"], str)
                or not isinstance(event["operations"], list) or not event["operations"]):
            raise ResultsError("invalid_results")
        _remember_id(event["id"], seen_ids)
        for operation in event["operations"]:
            if (not isinstance(operation, dict) or operation.keys() != _OPERATION_FIELDS
                    or not isinstance(operation["type"], str) or not isinstance(operation["key"], list)
                    or not all(isinstance(key, str) for key in operation["key"])
                    or (operation["value"] is not None and not isinstance(operation["value"], dict))):
                raise ResultsError("invalid_results")
            _remember_id(operation["id"], seen_ids)
    return cast(ValidatedResults, results)


def _remember_id(value: str, seen_ids: set[str]):
    if not _matches(_UUID, value) or value in seen_ids:
        raise ResultsError("invalid_results")
    seen_ids.add(value)


def parse_results(source: bytes | BinaryIO) -> ValidatedResults:
    """Validate a results ZIP from bytes or a binary stream at its current position.

    The caller owns the stream. Operation semantics and references in values
    require validation against the source export before use.
    """
    archive = _read_archive(source)
    end = _payload_end(archive)
    _JsonScan(archive, _PAYLOAD_START, end).check()
    try:
        text = str(memoryview(archive)[_PAYLOAD_START:end], "utf-8")
        results = json.loads(text)
    except (UnicodeError, json.JSONDecodeError):
        raise ResultsError("invalid_results") from None
    return _validate_envelope(results)


class ReadBudget:
    """Deduplicated read pass; transport additionally clamps socket timeouts.

    A stalled OS/DNS call retains the validation slot. The deadline is checked
    after every call and before mutation; this is not OS-call preemption.
    """
    def __init__(self, client, seconds, working_bytes=0):
        self.client, self.deadline = client, time.monotonic() + seconds
        self.cache = {}
        self.bytes = 0
        self.working_bytes = working_bytes

    def check(self):
        if time.monotonic() >= self.deadline:
            raise ws.WorkspaceError("Validation timed out; no changes were applied.", "validation_timeout")

    def read(self, method, item):
        self.check()
        key = method, item
        if key not in self.cache:
            if len(self.cache) >= 1000:
                raise ws.WorkspaceError("Validation read limit exceeded.", "resource_limit")
            try:
                value = getattr(self.client, method)(item)
            except ws.WorkspaceError:
                raise
            except Exception:
                value = None
            self.check()
            size = len(ws.canonical(value))
            self.bytes += size
            self.working_bytes += size * 8 + 256
            if size > 2 * 1024 ** 2 or self.bytes > 64 * 1024 ** 2:
                raise ws.WorkspaceError("Validation data limit exceeded.", "resource_limit")
            if self.working_bytes > 448 * 1024 ** 2:
                raise ws.WorkspaceError("Validation working-data limit exceeded.", "resource_limit")
            self.cache[key] = value
        return self.cache[key]


def _face_index(inventory):
    index = {}
    for aid, faces in inventory.get("faces", {}).items():
        for face in faces:
            fid = face.get("id")
            if fid in index:
                raise ws.WorkspaceError("Saved face inventory is inconsistent.", "source_mismatch")
            index[fid] = (aid, face_person(face))
            if len(index) > 200_000:
                raise ws.WorkspaceError("Face inventory exceeds the dependency limit.", "resource_limit")
    return index


def _domain(result, baseline):
    """Validate exact fixed operation shapes and source-owned references."""
    inv = baseline["inventory"]
    people, assets, faces = inv.get("people", {}), inv.get("assets", {}), _face_index(inv)
    for event in result["events"]:
        if event["kind"] not in {"local-changes", "group-photo", "face-review"}:
            raise ResultsError("invalid_results")
        for op in event["operations"]:
            stem, _, verb = op["type"].rpartition(".")
            entity = ws.OP_ENTITIES.get(stem)
            if entity is None or verb not in {"set", "remove"}:
                raise ResultsError("invalid_results")
            keys, fields, _ = ws.SPECS[entity]
            if len(op["key"]) != len(keys):
                raise ResultsError("invalid_results")
            if entity == "settings":
                if op["key"][0] not in ws.PREFERENCES or op["key"][0] == "group_current":
                    raise ResultsError("invalid_results")
            elif not all(_matches(_UUID, item) for item in op["key"]):
                raise ResultsError("invalid_results")
            value = op["value"]
            if (verb == "remove") != (value is None):
                raise ResultsError("invalid_results")
            if value is not None:
                allowed = set(fields.values()) - {"mergeStep"}
                if entity == "face_detach_pending":
                    allowed.discard("targetPersonId")
                if set(value) != allowed:
                    raise ResultsError("invalid_results")
                for field, item in value.items():
                    if field == "included":
                        valid = type(item) is bool
                    elif field == "skipCount":
                        valid = type(item) is int and 0 <= item <= (1 << 31) - 1
                    elif field in {"targetPersonId", "featureAssetId"}:
                        valid = item is None or _matches(_UUID, item)
                    elif field in {"sourcePersonId", "personId", "assetId"}:
                        valid = _matches(_UUID, item)
                    else:
                        valid = isinstance(item, str)
                    if not valid:
                        raise ResultsError("invalid_results")
                    if field in {"name", "targetName"} and (len(item) > 256 or len(item.encode("utf-8")) > 1024):
                        raise ResultsError("resource_limit")
                if entity == "pending":
                    if value["operation"] not in {"rename", "merge", "hide", "unhide"}:
                        raise ResultsError("invalid_results")
                    if value["operation"] == "rename" and not value["name"].strip():
                        raise ResultsError("invalid_results")
                    if value["operation"] == "merge":
                        if value["targetPersonId"] == op["key"][0] or value["targetPersonId"] not in people:
                            raise ResultsError("invalid_results")
                    elif value["targetPersonId"] is not None:
                        raise ResultsError("invalid_results")
                    if value["operation"] in {"hide", "unhide"} and (value["name"] or value["targetName"]):
                        raise ResultsError("invalid_results")
                if entity == "face_review_drafts" and value["verdict"] not in {"correct", "wrong", "unreviewed"}:
                    raise ResultsError("invalid_results")
                if entity == "group_photo_progress" and value["status"] not in {"done", "deferred", "ready"}:
                    raise ResultsError("invalid_results")
                if entity == "group_photo_drafts" and value["operation"] not in {"rename", "hide"}:
                    raise ResultsError("invalid_results")
                if entity == "group_photo_drafts" and value["operation"] == "hide" and (value["name"].casefold() != "ignore" or value["targetPersonId"] is not None):
                    raise ResultsError("invalid_results")
                if entity == "settings":
                    key, setting = op["key"][0], value["value"]
                    valid = ((key in {"sort", "group_order"} and setting in {"most", "fewest", "updated-newest", "updated-oldest", "random"})
                             or (key == "group_archived" and setting in {"true", "false"})
                             or (key == "group_pass" and setting in {"initial", "deferred"})
                             or (key == "group_min_faces" and setting in {str(i) for i in range(2, 21)}))
                    if not valid:
                        raise ResultsError("invalid_results")
            if entity in {"pending", "progress", "investigate", "face_review_reopened"} and op["key"][0] not in people:
                raise ResultsError("invalid_results")
            if entity.startswith("group_photo_"):
                if op["key"][0] not in assets or (entity == "group_photo_drafts" and op["key"][1] not in people):
                    raise ResultsError("invalid_results")
            if entity in {"face_reviews", "face_review_drafts", "face_detach_pending"}:
                if op["key"][0] not in faces:
                    raise ResultsError("invalid_results")
                if value is not None:
                    aid, pid = faces[op["key"][0]]
                    if value.get("personId", value.get("sourcePersonId")) != pid or value.get("assetId", aid) != aid:
                        raise ResultsError("invalid_results")
            if value is not None:
                if value.get("targetPersonId") is not None and value["targetPersonId"] not in people:
                    raise ResultsError("invalid_results")
                if value.get("featureAssetId") is not None and value["featureAssetId"] not in assets:
                    raise ResultsError("invalid_results")
    final = {(row["entity"], tuple(row["key"])): row["value"] for row in baseline["state"]["rows"] if not row["deleted"]}
    operations = [op for event in result["events"] for op in event["operations"]]
    for op in operations:
        final[ws.OP_ENTITIES[op["type"].rsplit(".", 1)[0]], tuple(op["key"])] = op["value"]
    for fid in faces:
        if final.get(("face_detach_pending", (fid,))) is not None and (final.get(("face_reviews", (fid,))) is not None or final.get(("face_review_drafts", (fid,))) is not None):
            raise ResultsError("invalid_results")
    for op in operations:
        if op["type"] == "group.progress.set" and op["value"]["status"] == "done":
            for face in inv["faces"].get(op["key"][0], []):
                pid = face_person(face)
                person = people.get(pid, {})
                if pid and not person.get("name") and not person.get("isHidden") and final.get(("pending", (pid,))) is None:
                    # A completed photo cannot omit one of its naming decisions.
                    raise ResultsError("invalid_results")
    return faces


def _deps(entity, key, value, faces):
    op = {"type": ws.SPECS[entity][2] + ".set", "key": key, "value": value}
    deps = ws._dependencies(op)
    if entity in {"face_reviews", "face_review_drafts", "face_detach_pending"} and key[0] in faces:
        aid, pid = faces[key[0]]
        deps.update(("asset:" + aid, "person:" + pid))
    return deps


def _fingerprints(capture, faces):
    domains = {}
    refs = 0
    for row in capture["rows"]:
        token = [row["entity"], row["key"], row["revision"], row["deleted"]]
        for dep in _deps(row["entity"], row["key"], row["value"], faces):
            domains.setdefault(dep, []).append(token)
            refs += 1
            if refs > 200_000:
                raise ws.WorkspaceError("Local dependency limit exceeded.", "resource_limit")
    return {key: ws.digest(sorted(value)) for key, value in domains.items()}


def _groups(result, baseline, faces):
    operations = [op for event in result["events"] for op in event["operations"]]
    event_ids = {op["id"]: event["id"] for event in result["events"] for op in event["operations"]}
    links = [[(ws.OP_ENTITIES[op["type"].rsplit(".", 1)[0]], op["key"]) for op in event["operations"]] for event in result["events"]]
    by_dep = {}
    photo_refs = set()
    refs = 0
    for op in operations:
        entity = ws.OP_ENTITIES[op["type"].rsplit(".", 1)[0]]
        row = entity, op["key"]
        for dep in _deps(entity, op["key"], op["value"], faces):
            by_dep.setdefault(dep, []).append(row)
        if entity.startswith("group_photo_"):
            photo_refs.add(op["key"][0])
    for aid in photo_refs:
        linked = list(by_dep.get("asset:" + aid, []))
        for pid in {face_person(face) for face in baseline["inventory"]["faces"].get(aid, [])}:
            linked.extend(by_dep.get("person:" + str(pid), []))
            refs += len(linked)
            if refs > 200_000:
                raise ws.WorkspaceError("Returned dependency limit exceeded.", "resource_limit")
        links.append(linked)
    groups = ws.group_changes(operations, baseline=baseline["state"], export_id=result["exportId"], event_keys=links)
    return groups, event_ids


def _view(inv, deps):
    pids = {dep[7:] for dep in deps if dep.startswith("person:")}
    aids = {dep[6:] for dep in deps if dep.startswith("asset:")}
    # Person-changing operations depend on their included photo face context.
    for aid, faces in inv.get("faces", {}).items():
        if any(face_person(face) in pids for face in faces):
            aids.add(aid)
            if len(aids) + len(pids) > 1000:
                raise ws.WorkspaceError("Validation read limit exceeded.", "resource_limit")
    return {"people": {pid: inv["people"][pid] for pid in sorted(pids) if pid in inv.get("people", {})},
            "assets": {aid: inv["assets"][aid] for aid in sorted(aids) if aid in inv.get("assets", {})},
            "faces": {aid: inv["faces"].get(aid, []) for aid in sorted(aids)}}


def check_view(view, budget, *, representative_only=False):
    for pid, before in view["people"].items():
        current = budget.read("person", pid)
        if current is None or person_baseline(current) != person_baseline(before):
            return False
        if not representative_only and (before.get("assetCount") is None or budget.read("statistics", pid) != before["assetCount"]):
            return False
    for aid, before in view["assets"].items():
        current, faces = budget.read("asset", aid), budget.read("faces", aid)
        if (current is None or faces is None or current.get("isTrashed") or current.get("deletedAt")
                or any(current.get(key) != before.get(key) for key in ("type", "visibility", "isArchived"))
                or face_baseline(faces) != face_baseline(view["faces"].get(aid, []))):
            return False
    return True


def _brief(text, characters=384, octets=768):
    text = "".join(char if ord(char) >= 32 and ord(char) != 127 else " " for char in str(text)[:characters])
    return text.encode("utf-8")[:octets].decode("utf-8", errors="ignore")


def _group_label(group, baseline, current, faces):
    inventory = baseline["inventory"]
    pending = {row["key"][0]: row["value"] for row in current["rows"] if row["entity"] == "pending" and not row["deleted"]}
    photos = {}
    for fid, (aid, pid) in faces.items():
        photos.setdefault(pid, aid)
    def photo(aid):
        return _brief(inventory.get("assets", {}).get(aid, {}).get("originalFileName") or "saved group photo", 120, 320)
    def person(pid):
        name = inventory.get("people", {}).get(pid, {}).get("name")
        if name:
            return _brief(name, 120, 320)
        local = pending.get(pid, {})
        if local.get("operation") == "rename" and local.get("name"):
            return "source Pending name “" + _brief(local["name"], 100, 300) + "”"
        return "unnamed cluster in “" + photo(photos.get(pid)) + "”"
    labels = []
    group_photo = None
    for op in group["operations"]:
        stem, _, verb = op["type"].rpartition(".")
        key, value = op["key"], op["value"] or {}
        if stem == "person.pending":
            context = person(key[0])
            if verb == "remove":
                labels.append("Return " + context + " from Pending")
            elif value["operation"] == "rename":
                labels.append("Name " + context + " “" + value["name"] + "”")
            elif value["operation"] == "merge":
                labels.append("Merge " + context + " into “" + person(value["targetPersonId"]) + "”")
            else:
                labels.append(("Ignore " if value["operation"] == "hide" else "Restore ") + context)
        elif stem == "person.investigate":
            labels.append(("Investigate " if verb == "set" else "Return from Investigate: ") + person(key[0]))
        elif stem == "person.skip":
            labels.append(("Skip " if verb == "set" else "Clear skip for ") + person(key[0]))
        elif stem in {"face.pending", "review.accepted", "review.draft"}:
            aid, pid = faces[key[0]]
            action = {"face.pending": "Separate wrong face", "review.accepted": "Accept face", "review.draft": "Save face review draft"}[stem]
            if verb == "remove":
                action = "Return face decision"
            labels.append(action + " for “" + person(pid) + "” in “" + photo(aid) + "”")
        elif stem == "review.reopened":
            labels.append(("Reopen review for " if verb == "set" else "Finish reopened review for ") + person(key[0]))
        elif stem.startswith("group."):
            group_photo = photo(key[0])
            if stem == "group.draft":
                labels.append("Group name draft for " + person(key[1]))
            elif not labels:
                labels.append("Group photo “" + group_photo + "”: " + value.get("status", "return progress"))
        elif stem == "preference":
            labels.append("Save Group photos settings" if key[0].startswith("group_") else "Save people ordering")
    priority = [label for label in labels if label.startswith(("Name ", "Merge ", "Ignore ", "Restore ", "Separate "))]
    chosen = (priority or labels or ["Saved review progress"])[0]
    if group_photo and not chosen.startswith("Group photo"):
        chosen = "Group photo “" + group_photo + "”: " + chosen
    return _brief(chosen)


def _apply_operation(db, op):
    entity = ws.OP_ENTITIES[op["type"].rsplit(".", 1)[0]]
    keys, fields, _ = ws.SPECS[entity]
    where = " AND ".join(column + "=?" for column in keys)
    if op["value"] is None:
        db.execute("DELETE FROM " + entity + " WHERE " + where, tuple(op["key"]))
        if entity == "face_detach_pending":
            db.execute("DELETE FROM face_reviews WHERE face_id=?", (op["key"][0],))
        return
    value = dict(op["value"])
    changed_intent = False
    if entity == "pending":
        previous = db.execute("SELECT merge_step,operation,name,target_person_id,target_name,feature_asset_id FROM pending WHERE person_id=?", tuple(op["key"])).fetchone()
        if previous and previous[0] != "ready":
            raise ws.WorkspaceError("A merge has already started.", "preview_stale")
        changed_intent = not previous or tuple(previous[1:]) != tuple(value[field] for field in ("operation", "name", "targetPersonId", "targetName", "featureAssetId"))
        value["mergeStep"] = "ready"
    if entity == "face_detach_pending":
        previous = db.execute("SELECT target_person_id FROM face_detach_pending WHERE face_id=?", tuple(op["key"])).fetchone()
        if previous and previous[0]:
            raise ws.WorkspaceError("A face correction has already started.", "preview_stale")
        value["targetPersonId"] = None
        db.execute("DELETE FROM face_reviews WHERE face_id=?", tuple(op["key"]))
    row = dict(zip(keys, op["key"]))
    row.update({column: value[wire] for column, wire in fields.items()})
    columns = {col[1] for col in db.execute("PRAGMA table_info(" + entity + ")")}
    for stamp in ("created_at", "updated_at", "reviewed_at", "reopened_at"):
        if stamp in columns:
            row[stamp] = ws.timestamp()
    updates = [key + "=excluded." + key for key in row if key not in keys and key != "created_at"]
    conflict = "DO UPDATE SET " + ",".join(updates) if updates else "DO NOTHING"
    db.execute("INSERT INTO " + entity + "(" + ",".join(row) + ") VALUES(" + ",".join("?" for _ in row)
               + ") ON CONFLICT(" + ",".join(keys) + ") " + conflict, tuple(row.values()))
    if entity == "pending" and changed_intent:
        for table in ("progress", "investigate"):
            db.execute("DELETE FROM " + table + " WHERE person_id=?", tuple(op["key"]))


class ImportManager:
    def __init__(self, state):
        self.state = state
        self.root = state.store.path.parent / "transfer-previews"
        self.lock = threading.Lock()
        self.commit_lock = threading.Lock()
        self.previews = {}
        self.closed = False

    @contextmanager
    def slot(self):
        if not self.lock.acquire(blocking=False):
            raise ws.WorkspaceError("Offline validation is busy.", "resource_busy")
        try:
            if self.closed:
                raise ws.WorkspaceError("Offline transfer is stopping.", "cancelled")
            yield
        finally:
            self.lock.release()

    def close(self):
        with self.commit_lock:
            self.closed = True

    @contextmanager
    def commit_boundary(self):
        with self.commit_lock:
            if self.closed:
                raise ws.WorkspaceError("Import cancelled before commit.", "cancelled")
            yield

    @contextmanager
    def mutation(self):
        if not self.state.mutation_lock.acquire(blocking=False):
            raise ws.WorkspaceError("Local work is busy.", "mutation_busy")
        try:
            yield
        finally:
            self.state.mutation_lock.release()

    def _source(self, context):
        client = self.state.require_client()
        if self.state.portable_policy()["portable"] or getattr(client, "offline", False):
            raise ws.WorkspaceError("Return changes to the originating connected Workbench.", "portable_restricted")
        if id(client) != context[2]:
            raise ws.WorkspaceError("Workbench connection changed.", "preview_stale")
        return client

    def _expire(self):
        for token, item in list(self.previews.items()):
            if time.monotonic() >= item["until"]:
                for file in (item["wire"], item["index"]):
                    file.unlink(missing_ok=True)
                del self.previews[token]

    def _get(self, token, context):
        self._expire()
        if not isinstance(token, str) or len(token) > 128:
            raise ws.WorkspaceError("Invalid preview handle.", "preview_expired")
        preview = self.previews.get(token)
        if not preview or preview["context"] != context:
            raise ws.WorkspaceError("Preview expired or connection changed; check the file again.", "preview_expired")
        return preview

    def _classify(self, result, context, seconds, selection=None):
        client = self._source(context)
        with self.state.store.connect() as db:
            # Charge trusted baseline/current logical graphs before decoding
            # them; reserve another64MiB for the active bounded remote read.
            length = db.execute("SELECT length(CAST(baseline_json AS BLOB)) FROM workspace_exports WHERE id=?", (result["exportId"],)).fetchone()
            if not length:
                raise ws.WorkspaceError("Unknown source export.", "source_mismatch")
            working = len(ws.canonical(result)) * 8 + int(length[0]) * 8 + 32 * 1024 ** 2
            version_size, version_count = db.execute("SELECT COALESCE(SUM(length(row_key)),0),COUNT(*) FROM workspace_row_versions").fetchone()
            working += int(version_size) * 8 + int(version_count) * 768
            if working > 448 * 1024 ** 2:
                raise ws.WorkspaceError("Validation working-data limit exceeded.", "resource_limit")
            for entity, (keys, fields, _) in ws.SPECS.items():
                expressions = "+".join("COALESCE(length(CAST(" + column + " AS BLOB)),0)" for column in [*keys, *fields])
                clause = " WHERE key IN (" + ",".join("?" for _ in ws.PREFERENCES) + ")" if entity == "settings" else ""
                size, count = db.execute("SELECT COALESCE(SUM(" + expressions + "),0),COUNT(*) FROM " + entity + clause,
                                         ws.PREFERENCES if clause else ()).fetchone()
                working += int(size) * 8 + int(count) * 1024
                if working > 448 * 1024 ** 2:
                    raise ws.WorkspaceError("Validation working-data limit exceeded.", "resource_limit")
            export = ws.read_known_export(db, result["exportId"])
            metadata = getattr(client, "connection_metadata", {})
            if client.base != export["owner"]["url"] or metadata.get("userId") != export["owner"]["userId"]:
                raise ws.WorkspaceError("Current connection does not match the export account.", "source_mismatch")
            if (export["status"] != "ready" or result["sourceInstanceId"] != export["sourceInstanceId"]
                    or result["baselineSha256"] != export["baselineSha256"]
                    or result["mediaSnapshotId"] != export["media"]["snapshotId"]):
                raise ws.WorkspaceError("Results do not match this source export.", "source_mismatch")
            faces = _domain(result, export["baseline"])
            groups, event_ids = _groups(result, export["baseline"], faces)
            current = ws.capture_state(db)
            base_facts = _fingerprints(export["baseline"]["state"], faces)
            current_facts = _fingerprints(current, faces)
            statuses = {group["id"]: ws.acceptance_status(db, result["exportId"], group, event_ids) for group in groups}
            receipt = ws.last_import(db, result["exportId"], ws.digest(result))
        base_rows = {(row["entity"], tuple(row["key"])): row for row in export["baseline"]["state"]["rows"]}
        budget = ReadBudget(client, seconds, working)
        rows, views = [], {}
        with client.validation_budget(budget.deadline) if hasattr(client, "validation_budget") else nullcontext():
            for group in groups:
                status = statuses[group["id"]]
                deps = set()
                for op in group["operations"]:
                    entity = ws.OP_ENTITIES[op["type"].rsplit(".", 1)[0]]
                    deps.update(_deps(entity, op["key"], op["value"], faces))
                    prior = base_rows.get((entity, tuple(op["key"])))
                    if prior:
                        deps.update(_deps(entity, op["key"], prior["value"], faces))
                reason, code = "", ""
                if status == "conflict":
                    reason, code = "Later overlapping work requires a fresh export; previously imported changes are kept.", "overlapping_continuation"
                elif status == "ready":
                    local_changed = any(base_facts.get(dep) != current_facts.get(dep) for dep in deps)
                    global_pass = any(op["type"].startswith("preference.") and op["key"][0] in ws.GROUP_SETTINGS for op in group["operations"])
                    if local_changed or (global_pass and current["groupRevision"] != export["baseline"]["state"]["groupRevision"]):
                        status, reason, code = "conflict", "Source work changed since export. Keep it and prepare a fresh export for overlapping changes.", "local_changed"
                    else:
                        if selection is not None and group["id"] not in selection:
                            rows.append({"id": group["id"], "kind": group["kind"], "label": "Unselected changes", "status": status, "reason": "", "code": ""})
                            continue
                        view = _view(export["baseline"]["inventory"], deps)
                        if not check_view(view, budget):
                            status, reason, code = "conflict", "Immich people/photos changed or could not be verified. Reconcile the source and prepare a fresh export.", "upstream_changed"
                        else:
                            views[group["id"]] = view
                label = _group_label(group, export["baseline"], current, faces)
                if reason:
                    reason = _brief(label, 160, 320) + ": " + reason
                summary = {"id": group["id"], "kind": group["kind"], "label": label, "status": status, "reason": _brief(reason, 512), "code": code}
                while len(ws.canonical(summary)) > 2400:
                    for field in ("label", "reason"):
                        summary[field] = summary[field][:max(1, len(summary[field]) * 3 // 4)]
                rows.append(summary)
            budget.check()
        return export, groups, event_ids, rows, views, current, receipt

    def preview(self, wire, context, *, locked=False):
        if not locked:
            with self.slot():
                return self.preview(wire, context, locked=True)
        self._expire()
        if len(self.previews) >= 4:
            raise ws.WorkspaceError("Too many previews; wait for an existing preview to expire.", "preview_capacity")
        result = parse_results(wire)
        with self.mutation():
            export, _, _, rows, _, _, receipt = self._classify(result, context, 120)
            digest = ws.digest(result)
            # Explicit new preview closes old unused handles for this same file.
            for token, old in list(self.previews.items()):
                if old["digest"] == digest and old["context"] == context:
                    old["wire"].unlink(missing_ok=True)
                    old["index"].unlink(missing_ok=True)
                    del self.previews[token]
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            index = ws.canonical(rows)
            if len(index) > 16 * 1024 ** 2 or sum(file.stat().st_size for file in self.root.iterdir() if file.is_file()) + len(wire) + len(index) > 128 * 1024 ** 2:
                raise ws.WorkspaceError("Preview storage capacity is full.", "preview_capacity")
            token = secrets.token_urlsafe(32)
            stem = str(uuid.uuid4())
            paths = self.root / (stem + ".result"), self.root / (stem + ".index")
            try:
                for path, data in zip(paths, (wire, index)):
                    with path.open("xb") as stream:
                        path.chmod(0o600)
                        stream.write(data)
                expires = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=10)
                self.previews[token] = {"context": context, "until": time.monotonic() + 600, "wire": paths[0], "index": paths[1], "digest": digest}
                answer = {"token": token, "workspaceName": export["name"], "changes": rows[:200],
                    "counts": {status: sum(row["status"] == status for row in rows) for status in ("ready", "conflict", "applied")},
                    "readyIds": [row["id"] for row in rows if row["status"] == "ready"],
                    "nextCursor": self._cursor(token, 200) if len(rows) > 200 else None, "expiresAt": expires.isoformat()}
                if receipt:
                    answer["lastImport"] = receipt
                return answer
            except BaseException:
                for path in paths:
                    path.unlink(missing_ok=True)
                raise

    @staticmethod
    def _cursor(token, offset):
        return str(offset) + ":" + ws.digest([token, offset])

    def page(self, token, cursor, context):
        with self.slot():
            item = self._get(token, context)
            try:
                offset = int(cursor.split(":", 1)[0])
                if offset < 0 or offset % 200 or cursor != self._cursor(token, offset):
                    raise ValueError()
            except (AttributeError, TypeError, ValueError):
                raise ws.WorkspaceError("Invalid preview page.") from None
            rows = json.loads(item["index"].read_bytes())
            return {"changes": rows[offset:offset + 200], "nextCursor": self._cursor(token, offset + 200) if offset + 200 < len(rows) else None}

    def apply(self, token, ids, context):
        with self.slot():
            item = self._get(token, context)
            if not isinstance(ids, list) or not ids or not all(isinstance(value, str) for value in ids) or len(ids) > 50_000 or len(ids) != len(set(ids)):
                raise ws.WorkspaceError("Select at least one ready change.")
            original = {row["id"]: row["status"] for row in json.loads(item["index"].read_bytes())}
            if any(original.get(value) != "ready" for value in ids):
                raise ws.WorkspaceError("Selection was not ready in this preview.", "preview_stale")
            with item["wire"].open("rb") as stream:
                result = parse_results(stream)
            if not self.state.mutation_lock.acquire(blocking=False):
                raise ws.WorkspaceError("Local work is busy.", "mutation_busy")
            try:
                export, groups, event_ids, rows, views, current, _ = self._classify(result, context, 30, set(ids))
                statuses = {row["id"]: row["status"] for row in rows}
                if any(statuses.get(value) != "ready" for value in ids):
                    raise ws.WorkspaceError("Preview changed; check the file again.", "preview_stale")
                wanted = set(ids)
                selected = [group for group in groups if group["id"] in wanted]
                with nullcontext():
                    if self.closed:
                        raise ws.WorkspaceError("Import was cancelled before applying.", "cancelled")
                    with self.state.store.workflow_transaction("import_apply", origin="import", commit_guard=self.commit_boundary) as db:
                        if ws.state_revision(db) != current["stateRevision"]:
                            raise ws.WorkspaceError("Local work changed; check the file again.", "preview_stale")
                        snapshot = ws.create_snapshot(db, "Before " + export["name"][:220], "pre-import")
                        for group in selected:
                            for op in group["operations"]:
                                _apply_operation(db, op)
                            if group["kind"] == "group-photo":
                                for op in group["operations"]:
                                    if op["type"] == "person.pending.set":
                                        db.execute("DELETE FROM group_photo_drafts WHERE person_id=?", (op["key"][0],))
                                    if op["type"] == "group.progress.set":
                                        db.execute("DELETE FROM group_photo_drafts WHERE asset_id=?", (op["key"][0],))
                                        db.execute("UPDATE settings SET value='' WHERE key='group_current' AND value=?", (op["key"][0],))
                            # Bind all surviving queue intents only after all
                            # fixed data operations in the component are done.
                            for op in group["operations"]:
                                entity = ws.OP_ENTITIES[op["type"].rsplit(".", 1)[0]]
                                if op["value"] is not None and entity in {"pending", "face_detach_pending"}:
                                    ws.bind_import_guard(db, entity, op["key"], {"status": "verified", "exportId": export["id"], "view": views[group["id"]]})
                        skipped = len(groups) - len(selected)
                        ws.record_import(db, result["exportId"], selected, snapshot["id"], event_ids=event_ids,
                                         skipped=skipped, result_sha256=item["digest"], observed_counts={key: sum(row["status"] == key for row in rows) for key in ("ready", "conflict", "applied")})
                del self.previews[token]
                item["wire"].unlink(missing_ok=True)
                item["index"].unlink(missing_ok=True)
                self.state.invalidate_group_scan()
                with self.state.lock:
                    self.state.sample_cache = {}
                return {"imported": len(selected), "skipped": skipped, "snapshot": snapshot}
            finally:
                self.state.mutation_lock.release()
                leftover = self.previews.pop(token, None)
                if leftover:
                    leftover["wire"].unlink(missing_ok=True)
                    leftover["index"].unlink(missing_ok=True)

    @staticmethod
    def _guards(guard):
        yield guard
        if isinstance(guard.get("inherited"), dict):
            yield from ImportManager._guards(guard["inherited"])

    def action_ready(self, entity, key):
        with self.state.store.connect() as db:
            action = ws.action_head(db, entity, [key])
        if action:
            for guard in self._guards(action["guard"]):
                if guard.get("status") == "baseline_unverified":
                    raise ws.WorkspaceError("Use Reload, then review Pending again. Your local intent is saved.", "baseline_unverified")
                if guard.get("status") == "conflict":
                    raise ws.WorkspaceError("Imported work changed in Immich; reconcile it before retrying.", "preview_stale")
        return action

    def validate_pending(self, client, rows, faces):
        included = [row for row in [*rows, *faces] if row["included"]]
        with self.state.store.connect() as db:
            # Reserve conservative checkpoint/outcome headroom before writes.
            ws._capacity(db, len(included) * 16384 + 65536, len(included) * 32 + 16)
        budget = ReadBudget(client, 30)
        with client.validation_budget(budget.deadline) if hasattr(client, "validation_budget") else nullcontext():
            for entity, key_field, items in (("pending", "person_id", rows), ("face_detach_pending", "face_id", faces)):
                for row in items:
                    if not row["included"]:
                        continue
                    action = self.action_ready(entity, row[key_field])
                    if not action:
                        continue
                    for guard in self._guards(action["guard"]):
                        if entity == "face_detach_pending" and guard.get("createState") == "inflight" and not row.get("target_person_id"):
                            raise ws.WorkspaceError("Face creation outcome is uncertain. Reconcile it before retrying.", "not_ready")
                        for field in ("view", "extra_view"):
                            if guard.get(field) and not check_view(guard[field], budget, representative_only=row.get("merge_step") == "complete"):
                                raise ws.WorkspaceError("Imported people/photos changed or cannot be verified. Exclude or reconcile this work before Sync.", "preview_stale")
            budget.check()

    @staticmethod
    def _references(entity, key, value):
        pids, aids = set(), set()
        if entity == "pending":
            pids.add(key)
        else:
            pids.add(value["sourcePersonId"])
            aids.add(value["assetId"])
        for field in ("targetPersonId",):
            if value.get(field):
                pids.add(value[field])
        if value.get("featureAssetId"):
            aids.add(value["featureAssetId"])
        return pids, aids

    def refresh_unverified(self, client):
        """Only explicit toolbar Reload calls this; original views stay immutable."""
        with self.slot(), self.mutation():
            budget = ReadBudget(client, 30)
            updates = []
            with self.state.store.connect() as db:
                heads = list(db.execute("SELECT entity,row_key FROM workspace_action_heads"))
                for entity, encoded in heads:
                    key = ws._decode_key(encoded)
                    action = ws.action_head(db, entity, key)
                    if not action or action["disposition"] != "queued" or action["guard"].get("status") != "baseline_unverified":
                        continue
                    value = ws._read_value(db, entity, key)
                    if value is None:
                        continue
                    guard = action["guard"]
                    pids, aids = self._references(entity, key[0], value)
                    for ancestor in self._guards(guard):
                        for field in ("view", "extra_view"):
                            pids -= set(ancestor.get(field, {}).get("people", {}))
                            aids -= set(ancestor.get(field, {}).get("assets", {}))
                    fresh = {"people": {}, "assets": {}, "faces": {}}
                    valid = True
                    with client.validation_budget(budget.deadline) if hasattr(client, "validation_budget") else nullcontext():
                        for pid in pids:
                            person, count = budget.read("person", pid), budget.read("statistics", pid)
                            if person is None or type(count) is not int:
                                valid = False
                                break
                            fresh["people"][pid] = {**person_baseline(person), "id": pid, "assetCount": count}
                        for aid in aids:
                            asset, faces = budget.read("asset", aid), budget.read("faces", aid)
                            if asset is None or faces is None or asset.get("isTrashed") or asset.get("deletedAt"):
                                valid = False
                                break
                            fresh["assets"][aid], fresh["faces"][aid] = asset, faces
                    if valid:
                        existing = guard.get("extra_view", {"people": {}, "assets": {}, "faces": {}})
                        guard["extra_view"] = {field: {**existing[field], **fresh[field]} for field in fresh}
                        guard["status"] = "verified"
                        updates.append((entity, key, guard))
            budget.check()
            with self.state.store.workflow_transaction("reload_verified_guards", origin="local_maintenance") as db:
                for entity, key, guard in updates:
                    ws.set_action_guard(db, entity, key, guard)

    def before_create(self, row):
        with self.state.store.workflow_transaction("face_create_checkpoint", origin="sync_checkpoint") as db:
            key = [row["face_id"]]
            action = ws.action_head(db, "face_detach_pending", key)
            if action is None:
                value = ws._read_value(db, "face_detach_pending", key)
                ws._put_action(db, "face_detach_pending", key, ws._intent("face_detach_pending", value), "source-native", {}, None,
                               ws.row_revision(db, "face_detach_pending", key)[0])
                action = ws.action_head(db, "face_detach_pending", key)
            guard = action["guard"]
            guard["createState"] = "inflight"
            ws.set_action_guard(db, "face_detach_pending", key, guard)

    def create_refused(self, row):
        with self.state.store.workflow_transaction("face_create_refused", origin="sync_checkpoint") as db:
            action = ws.action_head(db, "face_detach_pending", [row["face_id"]])
            if action:
                guard = action["guard"]
                guard.pop("createState", None)
                ws.set_action_guard(db, "face_detach_pending", [row["face_id"]], guard)

    def effect(self, kind, row, target=None):
        """Project only verified own deltas, never wholesale fresh remote state."""
        client = self.state.require_client()
        budget = ReadBudget(client, 30)
        updates = []
        own_entity = "face_detach_pending" if "face_id" in row else "pending"
        own_key = row.get("face_id", row.get("person_id"))
        needles = {str(value) for value in (row.get("source_person_id"), row.get("person_id"), row.get("asset_id"), row.get("feature_asset_id"), row.get("face_id"), target) if value}
        with self.state.store.connect() as db:
            heads = list(db.execute("SELECT h.entity,h.row_key FROM workspace_action_heads h JOIN workspace_actions a ON a.id=h.action_id WHERE a.disposition='queued' AND ((h.entity=? AND h.row_key=?) OR "
                + " OR ".join("a.guard_json LIKE ?" for _ in needles) + ")", (own_entity, ws._key([own_key]), *("%" + value + "%" for value in needles))))
        own_ok = True
        update_bytes = 0
        with client.validation_budget(budget.deadline) if hasattr(client, "validation_budget") else nullcontext():
            for entity, encoded in heads:
                key = ws._decode_key(encoded)
                with self.state.store.connect() as db:
                    action = ws.action_head(db, entity, key)
                if not action or action["disposition"] != "queued":
                    continue
                guard = action["guard"]
                own = entity == own_entity and key[0] == own_key
                if own and kind == "create":
                    guard["createState"] = "complete"
                changed = own and kind == "create"
                try:
                    for ancestor in self._guards(guard):
                        for field in ("view", "extra_view"):
                            view = ancestor.get(field)
                            if not view:
                                continue
                            pid = target if kind == "feature" else row.get("source_person_id", row.get("person_id"))
                            touched = set()
                            if kind == "create" and own:
                                person, count = budget.read("person", target), budget.read("statistics", target)
                                if person is None or person.get("name") or person.get("isHidden") or count != 0:
                                    raise ValueError()
                                view["people"][target] = {**person, "assetCount": 0}
                                changed = True
                            elif kind in {"rename", "hide", "unhide", "feature"} and pid in view["people"]:
                                if kind == "rename":
                                    view["people"][pid]["name"] = row["name"]
                                    if row.get("feature_asset_id"):
                                        view["people"][pid]["featureFaceAssetId"] = row["feature_asset_id"]
                                elif kind == "feature":
                                    view["people"][pid]["featureFaceAssetId"] = row["feature_asset_id"]
                                else:
                                    view["people"][pid]["isHidden"] = kind == "hide"
                                touched.add(pid)
                            elif kind in {"merge", "detach"}:
                                before_members = {aid: {face_person(face) for face in faces} for aid, faces in view["faces"].items()}
                                covered = sum(pid in members for members in before_members.values())
                                source_count = view["people"].get(pid, {}).get("assetCount")
                                for aid, faces in view["faces"].items():
                                    for face in faces:
                                        if (kind == "merge" and face_person(face) == pid) or (kind == "detach" and face.get("id") == row["face_id"]):
                                            face["person"] = {"id": target}
                                            if "personId" in face:
                                                face["personId"] = target
                                            touched.update((pid, target))
                                for aid, before in before_members.items():
                                    after = {face_person(face) for face in view["faces"][aid]}
                                    for person_id in before | after:
                                        person = view["people"].get(person_id)
                                        if person and type(person.get("assetCount")) is int:
                                            person["assetCount"] += int(person_id in after) - int(person_id in before)
                                if kind == "merge" and pid in view["people"]:
                                    view["people"].pop(pid)
                                    if covered != source_count and target in view["people"]:
                                        view["people"][target]["assetCount"] = None
                                    touched.add(target)
                            if touched:
                                changed = True
                                for person_id in touched:
                                    expected = view["people"].get(person_id)
                                    if expected is None:
                                        continue
                                    current = budget.read("person", person_id)
                                    if current is None:
                                        raise ValueError()
                                    before, after = person_baseline(expected), person_baseline(current)
                                    before.pop("updatedAt", None)
                                    after.pop("updatedAt", None)
                                    if before != after:
                                        raise ValueError()
                                    expected["updatedAt"] = current.get("updatedAt")
                                if not check_view(view, budget, representative_only=kind in {"merge", "feature"} and own):
                                    raise ValueError()
                    if changed:
                        update_bytes += len(ws.canonical(guard)) * 8
                        if update_bytes > 128 * 1024 ** 2:
                            raise ws.WorkspaceError("Own-effect working-data limit exceeded; Pending is retained.", "resource_limit")
                        updates.append((entity, key, guard))
                except (ValueError, ws.WorkspaceError):
                    if update_bytes > 128 * 1024 ** 2:
                        raise
                    guard["status"] = "conflict"
                    updates.append((entity, key, guard))
                    if own:
                        own_ok = False
        with self.state.store.workflow_transaction("verified_sync_effect", origin="sync_checkpoint") as db:
            for entity, key, guard in updates:
                ws.set_action_guard(db, entity, key, guard)
            action = ws.action_head(db, own_entity, [own_key])
            if action:
                ws.record_effect(db, action["id"], kind, {"targetPersonId": target})
        return own_ok
