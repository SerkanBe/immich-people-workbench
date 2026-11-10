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


if __name__ == "__main__":
    unittest.main()
