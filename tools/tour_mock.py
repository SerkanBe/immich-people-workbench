# Copyright (C) 2026 Serkan Bekdemir
# SPDX-License-Identifier: AGPL-3.0-only
"""Disposable, loopback-only Immich API fixture for People Workbench UI review.

No upstream connection exists. All writes mutate in-memory fictional fixtures.
This implements only the endpoints used by Workbench, not an API compatibility test.
"""
import argparse
import collections
import copy
import io
import json
import threading
import uuid
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent
IDENTITIES = ["mira", "asha", "dorian", "eli", "rowan", "nora"]
NAMES = ["Mira Solis", "Asha Bell", "Dorian Reed", "Eli Tan", "Rowan Finch", "Nora Vale",
         "Mira Solis — studio", "Alexander Morgan-Winterbottom"]
REQUIRED_PERMISSIONS = frozenset({"user.read", "person.read", "person.statistics", "person.update", "person.create", "person.merge", "asset.read", "asset.view", "face.read", "face.update", "asset.download"})
# Invented credentials usable only in this loopback fixture, never an upstream.
KEY_FIXTURES = {
    "synthetic-owner-key": "owner", "synthetic-rotated-key": "owner",
    "synthetic-missing-key": "missing", "synthetic-broad-key": "broad",
    "synthetic-no-original-key": "no-original",
    "synthetic-revoked-key": "revoked", "synthetic-wrong-owner-key": "wrong-owner",
    "synthetic-malformed-key": "malformed",
}
NAMESPACE = uuid.UUID("96b787a0-7b74-4a32-a824-97fd1fa9b007")
def uid(value):
    return str(uuid.uuid5(NAMESPACE, value))

class Fixtures:
    def __init__(self, profile="guide", *, large_original_bytes=20_000_123):
        if profile not in {"guide", "roundtrip"}:
            raise ValueError("Unknown synthetic fixture profile")
        self.profile = profile
        self.lock = threading.RLock()
        self.requests = collections.Counter()
        self.request_log = []
        self.writes = []
        self.app_write_attempts = []
        self.external_mutations = []
        self.fail_writes = False
        self.fail_next = collections.defaultdict(list)
        self.barriers = {}
        self.interrupt_next_streams = {}
        self.large_original_bytes = large_original_bytes
        self.large_original_asset_ids = set()
        self.fixture_ids = {"people": {}, "assets": {}, "faces": {}, "eligibleAssets": [], "excludedAssets": {}}
        self.key_permissions = set(REQUIRED_PERMISSIONS)
        self.key_revoked = False
        self.people = {}
        self.assets = {}
        self.faces = {}
        self.rows = []
        if profile == "roundtrip":
            self._build_roundtrip()
            self._tighten_face_boxes()
            return
        for i in range(44):
            pid = uid(f"person-{i}")
            identity = i % 6
            name = NAMES[i] if i < 8 else ""
            person = {"id": pid, "name": name, "isHidden": i >= 42,
                      "updatedAt": f"2026-09-{(i % 28) + 1:02}T12:00:00Z",
                      "thumbnailPath": "synthetic", "birthDate": None,
                      "_identity": identity, "_index": i}
            self.people[pid] = person
            self.rows.append(pid)
            count = 96 if i == 0 else 54 if i == 1 else max(3, 38 - i)
            for j in range(count):
                aid = uid(f"asset-{i}-{j}")
                shown = (identity + 1) % 6 if j % 7 == 6 else identity
                two = j % 11 == 10
                boxes = [(150, 125, 525, 720), (710, 160, 1085, 755)] if two else [(300, 95, 900, 790)]
                shown_ids = [shown, (shown + 2) % 6] if two else [shown]
                asset_faces = []
                for k, box in enumerate(boxes):
                    fid = uid(f"face-{i}-{j}-{k}")
                    x1, y1, x2, y2 = box
                    face = {"id": fid, "person": {"id": pid}, "assetId": aid,
                            "boundingBoxX1": x1, "boundingBoxY1": y1,
                            "boundingBoxX2": x2, "boundingBoxY2": y2,
                            "imageWidth": 1200, "imageHeight": 900, "sourceType": "machine-learning"}
                    self.faces[fid] = face
                    asset_faces.append(fid)
                self.assets[aid] = {"id": aid, "originalFileName": f"garden-day-{i+1:02}-{j+1:03}.jpg",
                                    "originalMimeType": "image/jpeg", "type": "IMAGE", "width": 2400, "height": 1800,
                                    "fileCreatedAt": f"2026-06-{(j % 28)+1:02}T12:00:00Z",
                                    "localDateTime": f"2026-06-{(j % 28)+1:02}T12:00:00Z",
                                    "exifInfo": {"exifImageWidth": 2400, "exifImageHeight": 1800,
                                                 "fileSizeInByte": 360000},
                                    "_faces": asset_faces, "_shown": shown_ids, "_boxes": boxes}

    def _add_roundtrip_person(self, key, name, identity, *, hidden=False):
        pid = uid("roundtrip-person-" + key)
        person = {"id": pid, "name": name, "isHidden": hidden,
                  "updatedAt": "2026-10-01T12:00:00Z", "thumbnailPath": "synthetic",
                  "birthDate": None, "_identity": identity, "_index": len(self.rows)}
        self.people[pid] = person
        self.rows.append(pid)
        self.fixture_ids["people"][key] = pid
        return pid

    def _add_roundtrip_asset(self, key, faces, *, asset_type="IMAGE", **flags):
        aid = uid("roundtrip-asset-" + key)
        face_ids = []
        for index, person_id in enumerate(faces):
            box = (90 + index * 190, 110, 250 + index * 190, 430)
            fid = uid(f"roundtrip-face-{key}-{index}")
            self.faces[fid] = {"id": fid, "person": {"id": person_id}, "assetId": aid,
                               "boundingBoxX1": box[0], "boundingBoxY1": box[1],
                               "boundingBoxX2": box[2], "boundingBoxY2": box[3],
                               "imageWidth": 1200, "imageHeight": 900,
                               "sourceType": "machine-learning"}
            face_ids.append(fid)
        is_large = key == "large-original"
        declared_size = self.large_original_bytes if is_large else 180_000 + len(face_ids) * 100
        people = [self.people[person_id] for person_id in faces]
        asset = {"id": aid, "originalFileName": f"roundtrip-{key}.jpg",
                 "originalMimeType": "image/jpeg", "type": asset_type,
                 "width": 2400, "height": 1800,
                 "updatedAt": "2026-10-01T12:00:00Z",
                 "fileCreatedAt": "2026-06-12T12:00:00Z", "localDateTime": "2026-06-12T12:00:00Z",
                 "exifInfo": {"exifImageWidth": 2400, "exifImageHeight": 1800,
                              "fileSizeInByte": declared_size},
                 "_faces": face_ids, "_shown": [person["_identity"] for person in people],
                 "_boxes": [(face["boundingBoxX1"], face["boundingBoxY1"],
                             face["boundingBoxX2"], face["boundingBoxY2"])
                            for face in (self.faces[fid] for fid in face_ids)]}
        asset.update(flags)
        self.assets[aid] = asset
        self.fixture_ids["assets"][key] = aid
        self.fixture_ids["faces"][key] = list(face_ids)
        if is_large:
            self.large_original_asset_ids.add(aid)
        is_eligible = (asset_type.upper() == "IMAGE"
                       and not any(asset.get(flag) for flag in ("isTrashed", "deletedAt", "isDeleted", "isHidden", "isLocked"))
                       and str(asset.get("status", "")).lower() not in {"trashed", "deleted"}
                       and str(asset.get("visibility", "timeline")).lower() in {"timeline", "archive", "archived"}
                       and any(not person.get("name") and not person.get("isHidden") for person in people))
        if is_eligible:
            self.fixture_ids["eligibleAssets"].append(aid)
        else:
            self.fixture_ids["excludedAssets"][key] = aid
        return aid

    def _build_roundtrip(self):
        named_a = self._add_roundtrip_person("named-a", "Asha Bell", 1)
        named_b = self._add_roundtrip_person("named-b", "Dorian Reed", 2)
        unnamed_a = self._add_roundtrip_person("unnamed-a", "", 3)
        unnamed_b = self._add_roundtrip_person("unnamed-b", "", 4)
        unnamed_c = self._add_roundtrip_person("unnamed-c", "", 0)
        unnamed_d = self._add_roundtrip_person("unnamed-d", "", 5)
        unnamed_e = self._add_roundtrip_person("unnamed-e", "", 3)
        hidden_person = self._add_roundtrip_person("hidden-cluster", "", 2, hidden=True)

        # The shared asset appears in both unnamed-cluster searches and must dedupe.
        self._add_roundtrip_asset("shared-context", [unnamed_a, unnamed_b, named_a])
        self._add_roundtrip_asset("named-context-a", [unnamed_a, named_a])
        self._add_roundtrip_asset("named-context-b", [unnamed_b, named_b])
        self._add_roundtrip_asset("named-only", [named_a])
        self._add_roundtrip_asset("archived-context", [unnamed_a, named_b], visibility="archive", isArchived=True)
        self._add_roundtrip_asset("large-original", [unnamed_b, named_b])
        self._add_roundtrip_asset("video", [unnamed_a], asset_type="VIDEO")
        self._add_roundtrip_asset("hidden", [unnamed_a], visibility="hidden", isHidden=True)
        self._add_roundtrip_asset("locked", [unnamed_a], isLocked=True)
        self._add_roundtrip_asset("trashed", [unnamed_a], isTrashed=True)
        self._add_roundtrip_asset("deleted", [unnamed_a], deletedAt="2026-10-02T00:00:00Z")
        self._add_roundtrip_asset("deleted-status", [unnamed_b], status="deleted")
        self._add_roundtrip_asset("hidden-cluster-only", [hidden_person], visibility="hidden", isHidden=True)
        self._add_roundtrip_asset("group-photo", [unnamed_a, named_a, unnamed_b, named_b, named_a])
        self._add_roundtrip_asset("solo-cluster-c", [unnamed_c])
        self._add_roundtrip_asset("solo-cluster-d", [unnamed_d])
        self._add_roundtrip_asset("solo-cluster-e", [unnamed_e])
        self.fixture_ids["partialNamedPerson"] = named_a
        self.fixture_ids["sharedAsset"] = self.fixture_ids["assets"]["shared-context"]
        self.fixture_ids["expectedSelectedAssets"] = sorted(self.fixture_ids["eligibleAssets"])
        self.fixture_ids["expectedExcludedAssets"] = sorted(self.fixture_ids["excludedAssets"].values())
        self.fixture_ids["independentClusters"] = {"naming": unnamed_c, "ignore": unnamed_d,
                                                    "investigate": unnamed_e}
        self.fixture_ids["hiddenCluster"] = hidden_person
        self.fixture_ids["journey"] = {"sourceConflict": unnamed_a, "safeNaming": unnamed_c,
                                       "ignore": unnamed_d, "investigate": unnamed_e,
                                       "faceReviewPerson": named_a,
                                       "groupParticipants": [unnamed_a, named_a, unnamed_b, named_b]}

    def _tighten_face_boxes(self):
        for face in self.faces.values():
            x1, y1, x2, y2 = (face[key] for key in ("boundingBoxX1", "boundingBoxY1", "boundingBoxX2", "boundingBoxY2"))
            width, height = x2 - x1, y2 - y1
            face.update(boundingBoxX1=round(x1 + width * .22), boundingBoxX2=round(x2 - width * .22),
                        boundingBoxY1=round(y1 + height * .06), boundingBoxY2=round(y1 + height * .55))

    @staticmethod
    def app_write_kind(method, path):
        if not isinstance(method, str) or not isinstance(path, str):
            return ""
        if method == "POST" and path == "/people":
            return "create"
        if method == "POST" and path.startswith("/people/") and path.endswith("/merge"):
            return "merge"
        if method == "PUT" and path.startswith("/people/") and path.count("/") == 2:
            return "update"
        if method == "PUT" and path.startswith("/faces/") and path.count("/") == 2:
            return "reassign"
        return ""

    def _safe_write_payload(self, kind, payload):
        payload = payload if isinstance(payload, dict) else {}
        if kind == "merge":
            ids = payload.get("ids")
            return {"ids": [item for item in ids[:100] if isinstance(item, str)]} if isinstance(ids, list) else {"ids": []}
        if kind == "reassign":
            return {"id": payload.get("id")} if isinstance(payload.get("id"), str) else {}
        allowed = ("name", "isHidden") if kind == "create" else ("name", "isHidden", "featureFaceAssetId")
        return {key: payload[key] for key in allowed if key in payload and isinstance(payload[key], (str, bool, int, float, type(None)))}

    def record_request(self, method, path, kind, payload):
        with self.lock:
            self.requests[f"{method} {path}"] += 1
            event = {"method": method, "path": path, "status": None}
            self.request_log.append(event)
            attempt = None
            if kind:
                attempt = {"method": method, "path": path, "kind": kind,
                           "payload": self._safe_write_payload(kind, payload), "status": None}
                self.app_write_attempts.append(attempt)
            return event, attempt

    def finish_request(self, event, attempt, status):
        with self.lock:
            if event is not None:
                event["status"] = status
            if attempt is not None:
                attempt["status"] = status
                if status < 400:
                    self.writes.append(copy.deepcopy(attempt))

    def fail_request(self, method, path, kind=""):
        with self.lock:
            rules = self.fail_next.get((method, path), [])
            if rules:
                status = rules[0]["status"]
                rules[0]["remaining"] -= 1
                if rules[0]["remaining"] <= 0:
                    rules.pop(0)
                return status
            return 503 if kind and self.fail_writes else None

    def pause_next_request(self, method, path, timeout=8):
        if (not isinstance(method, str) or method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}
                or not isinstance(path, str) or not path.startswith("/")
                or path.startswith("/__review__") or len(path) > 255):
            raise ValueError("Pause target must be a synthetic upstream request")
        with self.lock:
            barrier_id = uuid.uuid4().hex
            self.barriers[barrier_id] = {"method": method, "path": path, "timeout": timeout,
                                         "state": "queued", "entered": threading.Event(),
                                         "released": threading.Event()}
            return barrier_id

    def wait_at_barrier(self, method, path):
        with self.lock:
            barrier = next((item for item in self.barriers.values()
                            if item["method"] == method and item["path"] == path and item["state"] == "queued"), None)
            if barrier is None:
                return True
            barrier["state"] = "entered"
            barrier["entered"].set()
        if barrier["released"].wait(barrier["timeout"]):
            with self.lock:
                barrier["state"] = "released"
            return True
        with self.lock:
            barrier["state"] = "timed_out"
        return False

    def barrier_status(self, barrier_id):
        with self.lock:
            barrier = self.barriers.get(barrier_id)
            if barrier is None:
                return None
            return {key: barrier[key] for key in ("method", "path", "state")}

    def release_barrier(self, barrier_id):
        with self.lock:
            barrier = self.barriers.get(barrier_id)
            if barrier is None:
                return False
            barrier["released"].set()
            if barrier["state"] == "queued":
                barrier["state"] = "released"
            return True

    def interrupt_stream(self, path, after_bytes):
        if not isinstance(path, str) or not path.startswith("/assets/") or not path.endswith("/original"):
            raise ValueError("Stream interruption target must be a synthetic original")
        if type(after_bytes) is not int or not 1 <= after_bytes < self.large_original_bytes:
            raise ValueError("Invalid stream interruption offset")
        with self.lock:
            self.interrupt_next_streams[path] = after_bytes

    def consume_stream_interrupt(self, path, length):
        with self.lock:
            after_bytes = self.interrupt_next_streams.pop(path, None)
        if after_bytes is not None and after_bytes >= length:
            return None
        return after_bytes

    def record_truncated_stream(self, event, written):
        with self.lock:
            event["streamedBytes"] = written
            event["truncated"] = True

    def apply_external_mutation(self, payload):
        if not isinstance(payload, dict):
            raise ValueError("Mutation must be an object")
        operation = payload.get("operation")
        if operation == "rename_person":
            person = self.people.get(payload.get("personId"))
            if (person is None or not isinstance(payload.get("name"), str)
                    or len(payload["name"]) > 256):
                raise ValueError("Unknown person or invalid name")
            person["name"] = payload["name"]
            person["updatedAt"] = f"external-{len(self.external_mutations) + 1}"
        elif operation == "delete_person":
            person_id = payload.get("personId")
            if person_id not in self.people:
                raise ValueError("Unknown person")
            del self.people[person_id]
        elif operation == "reassign_face":
            face_id, person_id = payload.get("faceId"), payload.get("personId")
            if face_id not in self.faces or person_id not in self.people:
                raise ValueError("Unknown face or person")
            self.faces[face_id]["person"] = {"id": person_id}
            self.assets[self.faces[face_id]["assetId"]]["updatedAt"] = f"external-{len(self.external_mutations) + 1}"
        elif operation == "update_asset":
            asset = self.assets.get(payload.get("assetId"))
            changes = payload.get("changes")
            allowed = {"type", "visibility", "isHidden", "isLocked", "isTrashed", "isDeleted", "deletedAt", "status"}
            if (asset is None or not isinstance(changes, dict) or not changes or set(changes) - allowed
                    or any(not isinstance(value, (str, bool, type(None)))
                           or isinstance(value, str) and len(value) > 256
                           for value in changes.values())):
                raise ValueError("Unknown asset or invalid asset fields")
            asset.update(changes)
            asset["updatedAt"] = f"external-{len(self.external_mutations) + 1}"
            event = {"operation": operation, "assetId": payload["assetId"], "changes": dict(changes)}
        elif operation == "delete_asset":
            asset_id = payload.get("assetId")
            asset = self.assets.pop(asset_id, None)
            if asset is None:
                raise ValueError("Unknown asset")
            for face_id in asset["_faces"]:
                self.faces.pop(face_id, None)
            event = {"operation": operation, "assetId": asset_id}
        else:
            raise ValueError("Unknown synthetic mutation")
        if operation == "rename_person":
            event = {"operation": operation, "personId": payload["personId"], "name": payload["name"]}
        elif operation == "delete_person":
            event = {"operation": operation, "personId": payload["personId"]}
        elif operation == "reassign_face":
            event = {"operation": operation, "faceId": payload["faceId"], "personId": payload["personId"]}
        event["sequence"] = len(self.external_mutations) + 1
        self.external_mutations.append(event)
        return event

    def manifest(self):
        return {"profile": self.profile, "people": copy.deepcopy(self.fixture_ids["people"]),
                "assets": copy.deepcopy(self.fixture_ids["assets"]),
                "faces": copy.deepcopy(self.fixture_ids["faces"]),
                "eligibleAssets": list(self.fixture_ids["expectedSelectedAssets"]),
                "excludedAssets": copy.deepcopy(self.fixture_ids["excludedAssets"]),
                "journey": copy.deepcopy(self.fixture_ids.get("journey", {})),
                "partialNamedPerson": self.fixture_ids.get("partialNamedPerson"),
                "independentClusters": copy.deepcopy(self.fixture_ids.get("independentClusters", {})),
                "hiddenCluster": self.fixture_ids.get("hiddenCluster"),
                "sharedAsset": self.fixture_ids.get("sharedAsset"),
                "largeOriginalBytes": self.large_original_bytes if self.large_original_asset_ids else None}

    def public_person(self, p):
        return {k: v for k, v in p.items() if not k.startswith("_")}

    def public_asset(self, a):
        result = {k: v for k, v in a.items() if not k.startswith("_")}
        grouped = collections.defaultdict(list)
        for fid in a["_faces"]:
            f = self.faces[fid]
            grouped[f["person"]["id"]].append({k:v for k,v in f.items() if k != "person"})
        result["people"] = [dict(self.public_person(self.people[pid]), faces=faces) for pid,faces in grouped.items() if pid in self.people]
        return result

F = Fixtures()
GUIDE_FIXTURE = F

# Three deterministic group-photo examples with named and unnamed clusters.
for number, person_indexes in enumerate(((0, 1, 8, 9, 10, 11), (2, 12, 13, 14, 15), (3, 16, 17, 18, 19, 20))):
    aid = uid(f"group-photo-{number}")
    boxes = [(70 + (k % 3) * 390, 65 + (k // 3) * 420,
              340 + (k % 3) * 390, 430 + (k // 3) * 420) for k in range(len(person_indexes))]
    face_ids = []
    for k, (index, box) in enumerate(zip(person_indexes, boxes)):
        fid = uid(f"group-face-{number}-{k}")
        x1, y1, x2, y2 = box
        F.faces[fid] = {"id": fid, "person": {"id": F.rows[index]}, "assetId": aid,
                        "boundingBoxX1": x1, "boundingBoxY1": y1, "boundingBoxX2": x2, "boundingBoxY2": y2,
                        "imageWidth": 1200, "imageHeight": 900, "sourceType": "machine-learning"}
        face_ids.append(fid)
    F.assets[aid] = {"id": aid, "originalFileName": f"synthetic-group-{number + 1}.jpg",
                     "originalMimeType": "image/jpeg", "type": "IMAGE",
                     "width": 2400, "height": 1800, "updatedAt": f"2026-10-0{number + 1}T12:00:00Z",
                     "fileCreatedAt": "2026-06-12T12:00:00Z", "localDateTime": "2026-06-12T12:00:00Z",
                     "isArchived": number == 2, "visibility": "archive" if number == 2 else "timeline",
                     "exifInfo": {"exifImageWidth": 2400, "exifImageHeight": 1800,
                                  "fileSizeInByte": 500000},
                     "_faces": face_ids, "_shown": [index % 6 for index in person_indexes], "_boxes": boxes}

@lru_cache(maxsize=128)
def portrait(index):
    return (ROOT / "tour-fixtures" / f"{IDENTITIES[index % 6]}.jpg").read_bytes()

@lru_cache(maxsize=128)
def photo(shown, boxes):
    canvas = Image.new("RGB", (1200, 900), (214, 214, 208))
    for identity, box in zip(shown, boxes):
        x1, y1, x2, y2 = box
        im = Image.open(io.BytesIO(portrait(identity)))
        im = ImageOps.fit(im, (x2-x1, y2-y1))
        canvas.paste(im, (x1, y1))
    out = io.BytesIO()
    canvas.save(out, "JPEG", quality=90)
    return out.getvalue()

@lru_cache(maxsize=128)
def original_photo(shown, boxes):
    """Larger synthetic original; preview and face-coordinate fixtures stay unchanged."""
    source = Image.open(io.BytesIO(photo(shown, boxes)))
    canvas = source.resize((2400, 1800), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    canvas.save(out, "JPEG", quality=92)
    return out.getvalue()


def iter_jpeg_with_comment_padding(jpeg, target_size, chunk_size=1024 * 1024):
    """Yield one valid JPEG larger than the preview limit without building it in memory."""
    extra = target_size - len(jpeg)
    if extra < 4:
        raise ValueError("Target original must exceed the generated JPEG by at least four bytes")
    full_segments, remainder = divmod(extra, 65_537)
    segment_sizes = [65_537] * full_segments
    if remainder >= 4:
        segment_sizes.append(remainder)
    elif remainder:
        segment_sizes[-1] -= 4 - remainder
        segment_sizes.append(4)

    yield jpeg[:2]
    for segment_size in segment_sizes:
        payload_size = segment_size - 4
        # JPEG COM payload is at most 65,533 bytes; the marker and length are four.
        if not 0 <= payload_size <= 65_533:
            raise AssertionError("Invalid JPEG comment segment size")
        header = b"\xff\xfe" + (payload_size + 2).to_bytes(2, "big")
        payload = b"R" * payload_size
        yield header
        for offset in range(0, len(payload), chunk_size):
            yield payload[offset:offset + chunk_size]
    yield jpeg[2:]


# Portrait composites use broad placement rectangles; detection boxes cover heads.
# Keeping these separate makes the app's context crop representative of a face.
for face in F.faces.values():
    x1, y1, x2, y2 = (face[key] for key in ("boundingBoxX1", "boundingBoxY1", "boundingBoxX2", "boundingBoxY2"))
    width, height = x2 - x1, y2 - y1
    face.update(boundingBoxX1=round(x1 + width * .22), boundingBoxX2=round(x2 - width * .22),
                boundingBoxY1=round(y1 + height * .06), boundingBoxY2=round(y1 + height * .55))
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def send(self, value, status=200, mime="application/json"):
        data = value if isinstance(value, bytes) else json.dumps(value).encode()
        F.finish_request(getattr(self, "_request_event", None), getattr(self, "_write_attempt", None), status)
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def send_stream(self, chunks, length, status=200, mime="image/jpeg"):
        F.finish_request(getattr(self, "_request_event", None), getattr(self, "_write_attempt", None), status)
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        path = urlparse(self.path).path.removeprefix("/api")
        interrupt_after = F.consume_stream_interrupt(path, length)
        written = 0
        for chunk in chunks:
            if chunk:
                remaining = None if interrupt_after is None else interrupt_after - written
                output = chunk if remaining is None else chunk[:remaining]
                if output:
                    self.wfile.write(output)
                    written += len(output)
                self.wfile.flush()
                if interrupt_after is not None and written >= interrupt_after:
                    F.record_truncated_stream(self._request_event, written)
                    self.close_connection = True
                    return
        if self._request_event is not None:
            self._request_event["streamedBytes"] = written

    def handle_request(self):
        parsed = urlparse(self.path)
        path = parsed.path.removeprefix("/api")
        q = parse_qs(parsed.query)
        method = self.command
        payload = {}
        if self.headers.get("Content-Length"):
            payload = json.loads(self.rfile.read(min(65536, int(self.headers["Content-Length"]))))
        kind = "" if path.startswith("/__review__") else F.app_write_kind(method, path)
        self._request_event, self._write_attempt = F.record_request(method, path, kind, payload)
        if not kind and not path.startswith("/__review__") and not F.wait_at_barrier(method, path):
            return self.send({"message": "Synthetic request barrier timed out"}, 504)
        if kind and not F.wait_at_barrier(method, path):
            return self.send({"message": "Synthetic request barrier timed out"}, 504)
        with F.lock:
            if path == "/__review__/audit":
                return self.send({"profile": F.profile, "requests": dict(F.requests),
                                  "requestLog": copy.deepcopy(F.request_log),
                                  "appWriteAttempts": copy.deepcopy(F.app_write_attempts),
                                  "writes": copy.deepcopy(F.writes),
                                  "externalMutations": copy.deepcopy(F.external_mutations),
                                  "failWrites": F.fail_writes, "people": len(F.people)})
            if path == "/__review__/fixtures":
                return self.send(F.manifest())
            if path == "/__review__/settings" and method == "POST":
                if "failWrites" in payload:
                    F.fail_writes = payload["failWrites"] is True
                if "keyPermissions" in payload:
                    permissions = payload["keyPermissions"]
                    if not isinstance(permissions, list) or any(not isinstance(p, str) for p in permissions):
                        return self.send({"message": "keyPermissions must be a list"}, 400)
                    F.key_permissions = set(permissions)
                if "keyRevoked" in payload:
                    F.key_revoked = payload["keyRevoked"] is True
                if "failNext" in payload:
                    rules = payload["failNext"]
                    if not isinstance(rules, list) or len(rules) > 16:
                        return self.send({"message": "failNext must contain at most 16 rules"}, 400)
                    prepared = collections.defaultdict(list)
                    for rule in rules:
                        if not isinstance(rule, dict):
                            return self.send({"message": "Invalid failure rule"}, 400)
                        verb, route = rule.get("method"), rule.get("path")
                        status, count = rule.get("status", 503), rule.get("count", 1)
                        if (not isinstance(verb, str) or verb not in {"GET", "POST", "PUT", "PATCH", "DELETE"}
                                or not isinstance(route, str) or not route.startswith("/")
                                or route.startswith("/__review__") or len(route) > 255
                                or type(status) is not int or not 400 <= status <= 599
                                or type(count) is not int or not 1 <= count <= 100):
                            return self.send({"message": "Invalid failure rule"}, 400)
                        prepared[(verb, route)].append({"status": status, "remaining": count})
                    F.fail_next = prepared
                barrier_id = None
                if "pauseNext" in payload:
                    rule = payload["pauseNext"]
                    if not isinstance(rule, dict):
                        return self.send({"message": "pauseNext must be an object"}, 400)
                    try:
                        barrier_id = F.pause_next_request(rule.get("method"), rule.get("path"),
                                                         min(20, max(1, int(rule.get("timeout", 8)))))
                    except (TypeError, ValueError):
                        return self.send({"message": "Invalid pause rule"}, 400)
                if "interruptNextStream" in payload:
                    rule = payload["interruptNextStream"]
                    if not isinstance(rule, dict):
                        return self.send({"message": "interruptNextStream must be an object"}, 400)
                    try:
                        F.interrupt_stream(rule.get("path"), rule.get("afterBytes"))
                    except ValueError as exc:
                        return self.send({"message": str(exc)}, 400)
                return self.send({"failWrites": F.fail_writes, "keyRevoked": F.key_revoked,
                                  "keyPermissions": sorted(F.key_permissions), "barrierId": barrier_id})
            if path.startswith("/__review__/barriers/"):
                parts = path.strip("/").split("/")
                if len(parts) == 3 and method == "GET":
                    status = F.barrier_status(parts[2])
                    return self.send(status or {"message": "Unknown barrier"}, 200 if status else 404)
                if len(parts) == 4 and parts[3] == "release" and method == "POST":
                    released = F.release_barrier(parts[2])
                    return self.send({"released": released}, 200 if released else 404)
            if path == "/__review__/mutate" and method == "POST":
                try:
                    mutation = F.apply_external_mutation(payload)
                except (TypeError, ValueError) as exc:
                    return self.send({"message": str(exc)}, 400)
                return self.send({"mutation": mutation})
            if path == "/auth/login":
                if payload.get("email") != "review@example.test" or payload.get("password") != "synthetic-review":
                    return self.send({"message": "Use the documented synthetic review account"}, 401)
                return self.send({"isAdmin": True, "accessToken": "synthetic-review-session",
                                  "userId": uid("admin"), "name": "UI review"})
            if path == "/server/version":
                return self.send({"major": 3, "minor": 3, "patch": 1})
            key = self.headers.get("x-api-key", "")
            token = self.headers.get("x-immich-user-token", "")
            fixture = KEY_FIXTURES.get(key)
            is_key = bool(fixture)
            if is_key:
                if fixture == "revoked" or F.key_revoked:
                    return self.send({"message": "Synthetic key revoked"}, 401)
                permissions = set(F.key_permissions)
                if fixture == "missing": permissions.discard("face.update")
                if fixture == "no-original": permissions.discard("asset.download")
                if fixture == "broad": permissions = {"all"}
            elif token == "synthetic-review-session" and not key:
                permissions = {"all"}
            else:
                return self.send({"message": "Synthetic authentication required"}, 401)
            if path == "/api-keys/me":
                if not is_key: return self.send({"message": "Use an API key"}, 403)
                if fixture == "malformed": return self.send({"permissions": "invalid"})
                return self.send({"id": uid("key-" + fixture), "name": "Synthetic review key",
                                  "createdAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-01-01T00:00:00Z",
                                  "permissions": sorted(permissions)})
            permission = ""
            if path == "/users/me": permission = "user.read"
            elif path.startswith("/people"):
                permission = ("person.statistics" if path.endswith("/statistics") else
                              "person.merge" if path.endswith("/merge") else
                              "person.create" if path == "/people" and method == "POST" else
                              "person.update" if method == "PUT" else "person.read")
            elif path.startswith("/faces"): permission = "face.update" if method == "PUT" else "face.read"
            elif path == "/search/metadata": permission = "asset.read"
            elif path.startswith("/assets/"):
                permission = ("asset.download" if path.endswith("/original") else
                              "asset.view" if path.endswith("/thumbnail") else "asset.read")
            if permission and permission not in permissions and "all" not in permissions:
                return self.send({"message": "Synthetic permission denied"}, 403)
            failed_status = F.fail_request(method, path, kind)
            if failed_status is not None:
                return self.send({"message": "Synthetic failure: test an explicit retry"}, failed_status)
            if path == "/users/me":
                owner = "wrong-owner" if fixture == "wrong-owner" else "owner" if is_key else "admin"
                return self.send({"isAdmin": not is_key, "id": uid(owner), "name": "Synthetic review account", "email": "review@example.test"})
            if path == "/people" and method == "GET":
                rows = list(F.people.values())
                if q.get("withHidden", ["false"])[0] != "true":
                    rows = [r for r in rows if not r["isHidden"]]
                near = q.get("closestPersonId", [None])[0]
                if near and near in F.people:
                    identity = F.people[near]["_identity"]
                    rows.sort(key=lambda r: (r["_identity"] != identity, r["_index"]))
                    rows = [r for r in rows if r["id"] != near]
                size = int(q.get("size", [500])[0]); page = int(q.get("page", [1])[0])
                return self.send({"people": [F.public_person(r) for r in rows[(page-1)*size:page*size]], "hasNextPage": page*size < len(rows)})
            if path == "/people" and method == "POST":
                pid = uid(f"created-{len(F.people)}")
                p = {"id": pid, "name": "", "isHidden": False, "updatedAt": "2026-10-08T12:00:00Z", "_identity": 0, "_index": len(F.people)}
                F.people[pid] = p
                return self.send(F.public_person(p))
            if path.startswith("/people/"):
                parts = path.strip("/").split("/"); pid = parts[1]
                p = F.people.get(pid)
                if not p:
                    return self.send({"message": "Unknown synthetic person"}, 404)
                suffix = parts[2] if len(parts) > 2 else ""
                if suffix == "thumbnail":
                    return self.send(portrait(p["_identity"]), mime="image/jpeg")
                if suffix == "statistics":
                    count = sum(any(F.faces[fid]["person"]["id"] == pid for fid in a["_faces"]) for a in F.assets.values())
                    return self.send({"assets": count})
                if method in {"PUT", "POST"}:
                    if suffix == "merge":
                        for source in payload.get("ids", []):
                            for f in F.faces.values():
                                if f["person"]["id"] == source:
                                    f["person"] = {"id": pid}
                            F.people.pop(source, None)
                        return self.send([{ "id": identity, "success": True } for identity in [pid, *payload.get("ids", [])]])
                    else:
                        p.update({k: v for k, v in payload.items() if k in {"name", "isHidden", "featureFaceAssetId"}})
                    return self.send(F.public_person(p))
                return self.send(F.public_person(p))
            if path == "/search/metadata":
                ids = set(payload.get("personIds", []))
                rows = [a for a in F.assets.values() if any(F.faces[fid]["person"]["id"] in ids for fid in a["_faces"])]
                size = int(payload.get("size", 50)); page = int(payload.get("page", 1))
                return self.send({"assets": {"items": [F.public_asset(a) for a in rows[(page-1)*size:page*size]], "total": len(rows), "nextPage": str(page+1) if page*size<len(rows) else None}})
            if path == "/faces" and method == "GET":
                a = F.assets.get(q.get("id", [""])[0])
                return self.send([F.faces[fid] for fid in a["_faces"]] if a else [])
            if path.startswith("/faces/") and method == "PUT":
                pid = path.split("/")[-1]; fid = payload["id"]
                if fid not in F.faces or pid not in F.people:
                    return self.send({"message": "Unknown synthetic face or person"}, 404)
                F.faces[fid]["person"] = {"id": pid}
                F.people[pid]["_identity"] = F.assets[F.faces[fid]["assetId"]]["_shown"][0]
                return self.send({"id": fid})
            if path.startswith("/assets/"):
                aid = path.split("/")[2]; a = F.assets.get(aid)
                if a:
                    if path.endswith("/thumbnail"):
                        return self.send(photo(tuple(a["_shown"]), tuple(a["_boxes"])), mime="image/jpeg")
                    if path.endswith("/original"):
                        if aid in F.large_original_asset_ids:
                            jpeg = original_photo(tuple(a["_shown"]), tuple(a["_boxes"]))
                            return self.send_stream(iter_jpeg_with_comment_padding(jpeg, F.large_original_bytes),
                                                    F.large_original_bytes)
                        return self.send(original_photo(tuple(a["_shown"]), tuple(a["_boxes"])), mime="image/jpeg")
                    return self.send(F.public_asset(a))
            return self.send({"message": f"Mock endpoint missing: {method} {path}"}, 404)

    def do_GET(self):
        self.handle_request()
    def do_POST(self):
        self.handle_request()
    def do_PUT(self):
        self.handle_request()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18777)
    parser.add_argument("--profile", choices=("guide", "roundtrip"), default="guide",
                        help="Use the existing documentation data or a small offline-roundtrip fixture.")
    args = parser.parse_args()
    if args.profile == "roundtrip":
        F = Fixtures("roundtrip")
        GUIDE_FIXTURE = None
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Synthetic Immich mock: http://127.0.0.1:{args.port}", flush=True)
    print(f"{len(F.people)} fictional people ({F.profile} profile); no upstream connections; writes are in memory.", flush=True)
    server.serve_forever()
