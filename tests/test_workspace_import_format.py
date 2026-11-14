# Copyright (C) 2026 Serkan Bekdemir
# SPDX-License-Identifier: AGPL-3.0-only
"""Synthetic fixtures for the offline results archive and JSON reader."""
import io
import json
import stat
import struct
import unittest
from unittest.mock import patch
import zipfile

import workspace_import as reader


def identifier(number):
    return f"abcdef00-0000-4000-8000-{number:012x}"


def operation(number=5):
    return {"id": identifier(number), "type": "synthetic.set", "key": ["synthetic-key"],
            "value": {"name": "Example \U0001f600", "enabled": True}}


def envelope():
    return {"format": "people-workbench.results", "formatVersion": 1,
            "sourceInstanceId": identifier(1), "exportId": identifier(2), "portableInstanceId": identifier(3),
            "baselineSha256": "ab" * 32, "mediaSnapshotId": "cd" * 16,
            "events": [{"id": identifier(4), "kind": "synthetic", "operations": [operation()]}]}


def encode(results, *, ensure_ascii=False):
    return json.dumps(results, ensure_ascii=ensure_ascii, sort_keys=True, separators=(",", ":")).encode("utf-8")


def results_zip(payload, *, info=None, method=zipfile.ZIP_STORED):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=method, allowZip64=False) as archive:
        archive.writestr(info if info is not None else "changes.json", payload)
    return buffer.getvalue()


class PartialStream:
    def __init__(self, data):
        self.buffer = io.BytesIO(data)

    def read(self, count):
        if count < 1:
            raise AssertionError("A bounded read is required")
        return self.buffer.read(min(31, count))


class WorkspaceImportFormatTests(unittest.TestCase):
    def assert_rejected(self, wire, code="invalid_results"):
        with self.assertRaises(reader.ResultsError) as caught:
            reader.parse_results(wire)
        self.assertEqual(caught.exception.code, code)
        self.assertNotIn("synthetic", str(caught.exception))
        self.assertLess(len(str(caught.exception)), 100)

    def test_seekable_writer_has_exact_overhead_and_supports_binary_streams(self):
        results = envelope()
        results["events"][0]["operations"].append({"id": identifier(6), "type": "synthetic.remove",
                                                   "key": ["another-key"], "value": None})
        payload = encode(results)
        wire = results_zip(payload)
        self.assertEqual(reader.RESULTS_ZIP_OVERHEAD, 122)
        self.assertEqual(len(wire) - len(payload), 122)
        self.assertEqual(reader.parse_results(wire), results)
        self.assertEqual(reader.parse_results(PartialStream(wire)), results)
        with io.BytesIO(b"already read" + wire) as stream:
            stream.seek(len(b"already read"))
            self.assertEqual(reader.parse_results(stream), results)
            self.assertEqual(stream.read(), b"")

    def test_accepts_explicit_regular_entry_and_empty_results(self):
        results = envelope()
        results["events"] = []
        member = zipfile.ZipInfo("changes.json")
        member.create_system = 3
        member.external_attr = (stat.S_IFREG | 0o600) << 16
        self.assertEqual(reader.parse_results(results_zip(encode(results), info=member)), results)

    def test_maximum_wire_and_payload_sizes(self):
        results = envelope()
        payload = encode(results).ljust(16 * 1024 * 1024 - 122, b" ")
        wire = results_zip(payload)
        self.assertEqual(len(wire), 16 * 1024 * 1024)
        self.assertEqual(reader.parse_results(wire), results)
        oversized = results_zip(payload + b" ")
        self.assert_rejected(oversized, "resource_limit")
        self.assert_rejected(io.BytesIO(oversized), "resource_limit")

    def test_stream_stops_after_one_byte_over_the_limit(self):
        class GeneratedStream:
            read_sizes = []

            def read(self, count):
                self.read_sizes.append(count)
                return bytes(count)

        stream = GeneratedStream()
        self.assert_rejected(stream, "resource_limit")
        self.assertEqual(sum(stream.read_sizes), reader.MAX_RESULTS_WIRE_BYTES + 1)
        self.assertLessEqual(max(stream.read_sizes), 1024 * 1024)
        self.assertEqual(stream.read_sizes[-1], 1)
        self.assert_rejected(io.StringIO("synthetic"))

    def test_rejects_zip_prefix_suffix_truncation_and_compression(self):
        payload = encode(envelope())
        wire = results_zip(payload)
        for data in (b"", b"prefix" + wire, wire + b"\x00", wire[:-1], wire + wire,
                     results_zip(payload, method=zipfile.ZIP_DEFLATED)):
            with self.subTest(size=len(data)):
                self.assert_rejected(data)

    def test_rejects_multiple_members_even_with_forged_end_record_counts(self):
        for extra_name in ("changes.json", "outside.json"):
            with self.subTest(name=extra_name):
                buffer = io.BytesIO()
                with zipfile.ZipFile(buffer, "w") as archive:
                    archive.writestr("changes.json", encode(envelope()))
                    if extra_name == "changes.json":
                        with self.assertWarns(UserWarning):
                            archive.writestr(extra_name, b"{}")
                    else:
                        archive.writestr(extra_name, b"{}")
                wire = bytearray(buffer.getvalue())
                self.assert_rejected(bytes(wire))
                struct.pack_into("<HH", wire, len(wire) - 22 + 8, 1, 1)
                self.assert_rejected(bytes(wire))

    def test_rejects_paths_nonregular_members_and_extra_metadata(self):
        payload = encode(envelope())
        for name in ("../data.json", "/changes.json", "dir\\changes.json", "changes.json/"):
            with self.subTest(name=name):
                self.assert_rejected(results_zip(payload, info=zipfile.ZipInfo(name)))
        for mode in (stat.S_IFLNK, stat.S_IFDIR, stat.S_IFIFO, stat.S_IFSOCK):
            with self.subTest(mode=mode):
                member = zipfile.ZipInfo("changes.json")
                member.create_system = 3
                member.external_attr = (mode | 0o700) << 16
                self.assert_rejected(results_zip(payload, info=member))
        member = zipfile.ZipInfo("changes.json")
        member.create_system = 0
        member.external_attr = 0x10
        self.assert_rejected(results_zip(payload, info=member))
        member.external_attr = 0
        member.extra = struct.pack("<HH", 0xCAFE, 0)
        self.assert_rejected(results_zip(payload, info=member))
        member.extra = b""
        member.comment = b"comment"
        self.assert_rejected(results_zip(payload, info=member))
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("changes.json", payload)
            archive.comment = b"comment"
        self.assert_rejected(buffer.getvalue())

    def test_rejects_zip64_and_streamed_zip_descriptors(self):
        payload = encode(envelope())
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            with archive.open("changes.json", "w", force_zip64=True) as entry:
                entry.write(payload)
        self.assert_rejected(buffer.getvalue())

        class NonSeekableBuffer(io.BytesIO):
            def seek(self, *args):
                raise io.UnsupportedOperation("seek")

        buffer = NonSeekableBuffer()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("changes.json", payload)
        self.assertIn(b"PK\x07\x08", buffer.getvalue())
        self.assert_rejected(buffer.getvalue())

    def test_rejects_inconsistent_headers_sizes_and_crc(self):
        payload = encode(envelope())
        wire = results_zip(payload)
        central_offset = 42 + len(payload)
        end_offset = len(wire) - 22
        edits = (
            ("<H", 6, 1),                                  # Local encryption flag
            ("<H", central_offset + 8, 8),                 # Central descriptor flag
            ("<H", 10, 0),                                 # Local timestamp only
            ("<I", 18, len(payload) - 1),                  # Local stored size
            ("<I", central_offset + 24, 0xFFFFFFFF),       # Central ZIP64 size sentinel
            ("<I", central_offset + 42, 1),                # Central local-header offset
            ("<H", end_offset + 4, 1),                     # Multi-disk end record
            ("<I", end_offset + 12, 57),                   # Central record size
            ("<I", end_offset + 16, 0),                    # Central directory offset
            ("<B", 42, ord("[")),                          # Payload altered without new CRC
        )
        for layout, offset, value in edits:
            with self.subTest(offset=offset):
                changed = bytearray(wire)
                struct.pack_into(layout, changed, offset, value)
                self.assert_rejected(bytes(changed))
        changed = bytearray(wire)
        crc = struct.unpack_from("<I", wire, 14)[0] ^ 1
        struct.pack_into("<I", changed, 14, crc)
        struct.pack_into("<I", changed, central_offset + 16, crc)
        self.assert_rejected(bytes(changed))

    def test_rejects_duplicate_keys_invalid_utf8_surrogates_and_bad_json(self):
        payload = encode(envelope(), ensure_ascii=True)
        for document in (
            b'{"formatVersion":1,' + payload[1:],
            payload.replace(b'"enabled":true', br'"enabled":true,"en\u0061bled":false'),
            payload.replace(b"Example", b"\xff"),
            payload.replace(br"\ud83d\ude00", br"\ud83d"),
            payload.replace(br"\ud83d\ude00", br"\ude00"),
            payload.replace(b"Example", b"\x00"),
            payload.replace(b"Example", br"\q"),
            payload + b"{}",
            b"\xef\xbb\xbf" + payload,
            payload.replace(b'"enabled":true', b'"enabled":true,'),
        ):
            with self.subTest(document=document[:50]):
                self.assert_rejected(results_zip(document))

    def test_accepts_safe_integers_and_rejects_other_numbers(self):
        results = envelope()
        value = results["events"][0]["operations"][0]["value"]
        value.update(low=0, high=9_007_199_254_740_991)
        payload = encode(results)
        self.assertEqual(reader.parse_results(results_zip(payload)), results)
        for number in (b"NaN", b"Infinity", b"-Infinity", b"9007199254740992", b"-1", b"1.0", b"1e0",
                       b"01", b"+1", b"9" * 4096):
            with self.subTest(number=number[:20]):
                self.assert_rejected(results_zip(payload.replace(b'"high":9007199254740991', b'"high":' + number)))

    def test_exact_envelope_fields_types_and_identifiers(self):
        for field, replacement in (
            ("format", 1), ("formatVersion", True), ("sourceInstanceId", identifier(1).upper()),
            ("portableInstanceId", identifier(1)), ("exportId", "synthetic"), ("baselineSha256", "AB" * 32),
            ("mediaSnapshotId", "cd" * 15), ("events", {}), ("schema", "synthetic"),
        ):
            with self.subTest(field=field):
                results = envelope()
                results[field] = replacement
                self.assert_rejected(results_zip(encode(results)))
        for field, replacement in (("format", "synthetic-format"), ("formatVersion", 2)):
            with self.subTest(field=field):
                results = envelope()
                results[field] = replacement
                self.assert_rejected(results_zip(encode(results)), "unsupported_format")

    def test_exact_event_and_operation_shapes_and_unique_ids(self):
        for target, edits in (
            ("event", {"kind": 1}), ("event", {"operations": []}), ("event", {"extra": "synthetic"}),
            ("operation", {"id": identifier(4)}), ("operation", {"id": "synthetic"}),
            ("operation", {"type": None}), ("operation", {"key": "synthetic"}),
            ("operation", {"key": [1]}), ("operation", {"value": []}),
            ("operation", {"extra": "synthetic"}),
        ):
            with self.subTest(target=target, edits=edits):
                results = envelope()
                event = results["events"][0]
                (event if target == "event" else event["operations"][0]).update(edits)
                self.assert_rejected(results_zip(encode(results)))
        results = envelope()
        results["events"].append({"id": identifier(4), "kind": "synthetic", "operations": [operation(6)]})
        self.assert_rejected(results_zip(encode(results)))
        results["events"][1]["id"] = identifier(6)
        results["events"][1]["operations"] = [operation(5)]
        self.assert_rejected(results_zip(encode(results)))

    def test_depth_object_members_and_unicode_string_boundaries(self):
        results = envelope()
        operation_value = results["events"][0]["operations"][0]
        nested = True
        for _ in range(10):
            nested = [nested]
        operation_value["value"] = {"nested": nested}
        self.assertEqual(reader.parse_results(results_zip(encode(results))), results)
        operation_value["value"]["nested"] = [nested]
        self.assert_rejected(results_zip(encode(results)), "resource_limit")
        operation_value["value"] = {str(i): True for i in range(32)}
        self.assertEqual(reader.parse_results(results_zip(encode(results))), results)
        operation_value["value"]["overflow"] = True
        self.assert_rejected(results_zip(encode(results)), "resource_limit")
        operation_value["value"] = {"text": "\U0001f600" * 4096}
        for ensure_ascii in (False, True):
            with self.subTest(ensure_ascii=ensure_ascii):
                self.assertEqual(reader.parse_results(results_zip(encode(results, ensure_ascii=ensure_ascii))), results)
        operation_value["value"]["text"] += "\U0001f600"
        for ensure_ascii in (False, True):
            with self.subTest(ensure_ascii=ensure_ascii):
                self.assert_rejected(results_zip(encode(results, ensure_ascii=ensure_ascii)), "resource_limit")
        operation_value["value"]["text"] = "a" * 4097
        self.assert_rejected(results_zip(encode(results)), "resource_limit")

    def test_lexical_node_and_estimated_allocation_limits_precede_object_loading(self):
        results = envelope()
        results["events"] = []
        wire = results_zip(encode(results))
        # The root object, its eight keys and their eight values total 17 nodes.
        with patch.object(reader, "_MAX_JSON_NODES", 17):
            self.assertEqual(reader.parse_results(wire), results)
        for limit, maximum in (("_MAX_JSON_NODES", 16), ("_MAX_JSON_ALLOCATION_ESTIMATE", 1024)):
            with self.subTest(limit=limit), patch.object(reader, limit, maximum), patch.object(reader.json, "loads") as load:
                self.assert_rejected(wire, "resource_limit")
                load.assert_not_called()

    def test_event_operation_and_aggregate_key_limits_precede_object_loading(self):
        results = envelope()
        results["events"].append({"id": identifier(6), "kind": "synthetic", "operations": [operation(7)]})
        wire = results_zip(encode(results))
        for limit in ("_MAX_EVENTS", "_MAX_OPERATIONS", "_MAX_KEY_REFERENCES"):
            with self.subTest(limit=limit), patch.object(reader, limit, 2):
                self.assertEqual(reader.parse_results(wire), results)
            # Use a literal Unicode string so the only json.loads call would be for the full object.
            with self.subTest(limit=limit), patch.object(reader, limit, 1), patch.object(reader.json, "loads") as load:
                self.assert_rejected(wire, "resource_limit")
                load.assert_not_called()
