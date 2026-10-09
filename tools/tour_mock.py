# Copyright (C) 2026 Serkan Bekdemir
# SPDX-License-Identifier: AGPL-3.0-only
"""Disposable, loopback-only Immich API fixture for People Workbench UI review.

No upstream connection exists. All writes mutate in-memory fictional fixtures.
This implements only the endpoints used by Workbench, not an API compatibility test.
"""
import argparse
import collections
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
NAMESPACE = uuid.UUID("96b787a0-7b74-4a32-a824-97fd1fa9b007")
def uid(value):
    return str(uuid.uuid5(NAMESPACE, value))

class Fixtures:
    def __init__(self):
        self.lock = threading.RLock()
        self.requests = collections.Counter()
        self.writes = []
        self.fail_writes = False
        self.people = {}
        self.assets = {}
        self.faces = {}
        self.rows = []
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
                                    "type": "IMAGE", "width": 1200, "height": 900,
                                    "fileCreatedAt": f"2026-06-{(j % 28)+1:02}T12:00:00Z",
                                    "localDateTime": f"2026-06-{(j % 28)+1:02}T12:00:00Z",
                                    "exifInfo": {"exifImageWidth": 1200, "exifImageHeight": 900},
                                    "_faces": asset_faces, "_shown": shown_ids, "_boxes": boxes}

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
    F.assets[aid] = {"id": aid, "originalFileName": f"synthetic-group-{number + 1}.jpg", "type": "IMAGE",
                     "width": 1200, "height": 900, "updatedAt": f"2026-10-0{number + 1}T12:00:00Z",
                     "fileCreatedAt": "2026-06-12T12:00:00Z", "localDateTime": "2026-06-12T12:00:00Z",
                     "isArchived": number == 2, "visibility": "archive" if number == 2 else "timeline",
                     "exifInfo": {"exifImageWidth": 1200, "exifImageHeight": 900},
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
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def handle_request(self):
        parsed = urlparse(self.path)
        path = parsed.path.removeprefix("/api")
        q = parse_qs(parsed.query)
        method = self.command
        payload = {}
        if self.headers.get("Content-Length"):
            payload = json.loads(self.rfile.read(min(65536, int(self.headers["Content-Length"]))))
        with F.lock:
            F.requests[f"{method} {path}"] += 1
            if path == "/__review__/audit":
                return self.send({"requests": dict(F.requests), "writes": F.writes,
                                  "failWrites": F.fail_writes, "people": len(F.people)})
            if path == "/__review__/settings" and method == "POST":
                F.fail_writes = bool(payload.get("failWrites"))
                return self.send({"failWrites": F.fail_writes})
            if path == "/auth/login":
                if payload.get("email") != "review@example.test" or payload.get("password") != "synthetic-review":
                    return self.send({"message": "Use the documented synthetic review account"}, 401)
                return self.send({"isAdmin": True, "accessToken": "synthetic-review-session",
                                  "userId": uid("admin"), "name": "UI review"})
            if path == "/users/me":
                return self.send({"isAdmin": True, "id": uid("admin"), "email": "review@example.test"})
            if path == "/server/version":
                return self.send({"version": "UI mock"})
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
                F.writes.append({"method": method, "path": path, "kind": "create"})
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
                    if F.fail_writes:
                        return self.send({"message": "Synthetic failure: test retry from Pending"}, 503)
                    F.writes.append({"method": method, "path": path, "kind": suffix or "update"})
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
                if F.fail_writes:
                    return self.send({"message": "Synthetic face reassignment failure"}, 503)
                pid = path.split("/")[-1]; fid = payload["id"]
                F.faces[fid]["person"] = {"id": pid}
                F.people[pid]["_identity"] = F.assets[F.faces[fid]["assetId"]]["_shown"][0]
                F.writes.append({"method": method, "path": path, "kind": "reassign"})
                return self.send({"id": fid})
            if path.startswith("/assets/"):
                aid = path.split("/")[2]; a = F.assets.get(aid)
                if a:
                    if path.endswith("/thumbnail"):
                        return self.send(photo(tuple(a["_shown"]), tuple(a["_boxes"])), mime="image/jpeg")
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
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Synthetic Immich mock: http://127.0.0.1:{args.port}", flush=True)
    print("44 fictional people; no upstream connections; writes are in memory.", flush=True)
    server.serve_forever()
