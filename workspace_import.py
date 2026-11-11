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
