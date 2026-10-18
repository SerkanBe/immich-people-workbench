# Copyright (C) 2026 Serkan Bekdemir
# SPDX-License-Identifier: AGPL-3.0-only
"""Portable, versioned offline snapshots. Credentials never enter this module's files."""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import threading
import urllib.parse

FORMAT_VERSION = 1
ID = re.compile(r"^[0-9a-fA-F-]{20,50}$")
RESERVE_BYTES = 64 * 1024 * 1024
MAX_WORKSPACE_BYTES = 500 * 1024 ** 3
MAX_ORIGINAL_BYTES = 10 * 1024 ** 3


class OfflineError(RuntimeError):
    pass


class OfflineConflict(OfflineError):
    def __init__(self, conflicts):
        self.conflicts = conflicts
        super().__init__("Upstream changes conflict with the offline snapshot. Review the listed people/photos in Immich, then return affected Pending items and prepare a fresh workspace.")


class Cancelled(OfflineError):
    pass


def safe_id(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise OfflineError("Invalid offline item ID")
    return value


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(temp, path)


def face_person(face):
    return (face.get("person") or {}).get("id") or face.get("personId")


def person_baseline(person):
    return {key: person.get(key) for key in ("name", "isHidden", "updatedAt", "featureFaceAssetId", "faceAssetId")}


def face_baseline(faces):
    return sorted((str(f.get("id")), str(face_person(f)), *(str(f.get(k)) for k in (
        "boundingBoxX1", "boundingBoxX2", "boundingBoxY1", "boundingBoxY2", "imageWidth", "imageHeight"))) for f in faces)


class OfflineClient:
    """Read adapter with no network fallback and no upstream mutation methods."""
    offline = True

    def __init__(self, manager, snapshot):
        self.manager, self.snapshot = manager, snapshot
        self.base = snapshot["owner"]["url"]
        self.server_version = snapshot.get("serverVersion", "offline")
        self.connection_metadata = {"authMode": "offline", "displayName": "Offline workspace", "requiredPermissions": [], "grantedPermissions": []}
        self.people = snapshot["people"]
        self.assets = snapshot["assets"]
        self.by_person = {pid: [] for pid in self.people}
        for aid, asset in self.assets.items():
            for pid in {face_person(f) for f in snapshot["faces"].get(aid, [])}:
                if pid in self.by_person:
                    self.by_person[pid].append(asset)

    def version(self):
        return self.server_version

    def all_people(self):
        return copy.deepcopy(list(self.people.values()))

    def person(self, person_id):
        if person_id not in self.people:
            raise OfflineError("Person is not in this offline workspace")
        return copy.deepcopy(self.people[person_id])

    def statistics(self, person_id):
        return self.person(person_id).get("assetCount")

    def coverage(self, person_id):
        available = len(self.by_person.get(person_id, []))
        total = self.people.get(person_id, {}).get("assetCount")
        return {"availableAssets": available, "totalAssets": total,
                "complete": isinstance(total, int) and available == total}

    def person_assets_page(self, person_id, page=1, size=50, *, group_photos=False):
        assets = self.by_person.get(person_id, [])
        page, size = max(1, page), max(1, min(size, 100))
        return copy.deepcopy(assets[(page - 1) * size:page * size]), page * size < len(assets), len(assets)

    def person_assets(self, person_id, limit=12):
        return self.person_assets_page(person_id, 1, limit)[0]

    def asset(self, asset_id):
        if asset_id not in self.assets:
            raise OfflineError("Photo is not in this offline workspace")
        return copy.deepcopy(self.assets[asset_id])

    def faces(self, asset_id):
        self.asset(asset_id)
        return copy.deepcopy(self.snapshot["faces"][asset_id])

    def closest_people(self, person_id, limit=20):
        # Similarity rankings are not part of the snapshot; do not invent them.
        return []

    def samples(self, person_id, limit=12):
        result = []
        for asset in self.person_assets(person_id, limit):
            for face in self.faces(asset["id"]):
                if face_person(face) == person_id:
                    result.append({"assetId": asset["id"], "faceId": face.get("id"), "box": {
                        "x1": face.get("boundingBoxX1"), "x2": face.get("boundingBoxX2"),
                        "y1": face.get("boundingBoxY1"), "y2": face.get("boundingBoxY2"),
                        "width": face.get("imageWidth"), "height": face.get("imageHeight")}})
        return result[:limit]

    def request(self, method, path, payload=None):
        if method != "GET":
            raise OfflineError("Offline work stays local. Reconnect and review Pending before explicit Sync.")
        parsed = urllib.parse.urlsplit(path)
        parts = parsed.path.strip("/").split("/")
        if len(parts) == 3 and parts[2] == "thumbnail" and parts[0] in {"people", "assets"}:
            kind = "people" if parts[0] == "people" else "previews"
            item = self.snapshot.get("media", {}).get(kind, {}).get(safe_id(parts[1]))
            if item:
                file = self.manager.media_path(self.snapshot, item["path"])
                if file.stat().st_size > 20_000_000:
                    raise OfflineError("Cached preview exceeds the safety limit")
                return file.read_bytes(), item["mime"]
        raise OfflineError("This preview is not available offline")

    def __getattr__(self, name):
        if name in {"update_person", "create_person", "reassign_face", "merge", "json"}:
            raise OfflineError("Offline work cannot write to Immich. Reconnect, review Pending and explicitly Sync.")
        raise AttributeError(name)


class OfflineWorkspace:
    def __init__(self, state):
        self.state = state
        self.root = state.store.path.parent / "offline"
        self.lock = threading.RLock()
        self.cancel_event = threading.Event()
        self.worker = None
        self.conflicts = []
        self.job = {"phase": "idle", "planId": "", "assetCount": 0, "personCount": 0,
                    "estimatedBytes": 0, "unknownSizes": 0, "downloadedFiles": 0, "downloadedBytes": 0, "error": ""}
        self.plan = None
        try:
            saved = self._read(self.root / "plan.json")
            self.plan = saved
            self.job.update(saved.get("job", {}))
            if self.job["phase"] in {"planning", "downloading", "cancelling"}:
                self.job["phase"] = "cancelled"
        except (OSError, ValueError, KeyError):
            pass

    @staticmethod
    def _read(path):
        return json.loads(path.read_text(encoding="utf-8"))

    def media_path(self, snapshot, relative):
        base = (self.root / "snapshots" / safe_id(snapshot["id"])).resolve()
        path = (base / relative).resolve()
        if not path.is_relative_to(base) or path == base:
            raise OfflineError("Invalid offline media path")
        return path

    def active(self, verify=False, allow_stale=False):
        try:
            active = self._read(self.root / "active.json")
            if active.get("stale") and not allow_stale:
                return None
            sid = safe_id(active["id"])
            snapshot = self._read(self.root / "snapshots" / sid / "snapshot.json")
            if snapshot.get("formatVersion") != FORMAT_VERSION or snapshot.get("id") != sid:
                return None
            if snapshot["owner"] != json.loads(self.state.store.setting("owner") or "null"):
                return None
            if verify:
                for item in snapshot["media"]["originals"].values():
                    path = self.media_path(snapshot, item["path"])
                    if not path.is_file() or path.stat().st_size != item["size"]:
                        return None
                if len(snapshot["media"]["originals"]) != len(snapshot["assets"]):
                    return None
            return snapshot
        except (OSError, ValueError, KeyError, TypeError, OfflineError):
            return None

    def status(self, private=False):
        snapshot = self.active(verify=True, allow_stale=True)
        try:
            stale = bool(self._read(self.root / "active.json").get("stale"))
        except (OSError, ValueError):
            stale = False
        result = {"available": snapshot is not None, "ready": snapshot is not None and not stale,
                  "offline": bool(private and getattr(self.state.client, "offline", False)), "stale": stale}
        if stale:
            result["staleReason"] = "Changes were synced to Immich. Prepare a fresh workspace before opening offline again. Stored files have been retained."
        if private:
            with self.lock:
                result["job"] = dict(self.job)
                result["conflicts"] = list(self.conflicts)
            result["snapshot"] = ({key: snapshot[key] for key in (
                "id", "createdAt", "assetCount", "personCount", "totalBytes")} if snapshot else None)
        return result

    def _check_cancel(self):
        if self.cancel_event.is_set():
            raise Cancelled("Offline preparation cancelled; completed files can be resumed")

    def _persist_plan(self):
        with self.lock:
            if self.plan:
                self.plan["job"] = dict(self.job)
                atomic_json(self.root / "plan.json", self.plan)

    def _run(self, phase, action):
        with self.lock:
            if self.worker and self.worker.is_alive():
                raise OfflineError("Offline preparation is already running")
            self.cancel_event.clear()
            self.job.update(phase=phase, error="")
            def run():
                try:
                    action()
                except Cancelled as exc:
                    with self.lock:
                        self.job.update(phase="cancelled", error=str(exc))
                except Exception:
                    # Never reflect upstream bodies, paths or credentials into status.
                    with self.lock:
                        self.job.update(phase="error", error="Offline preparation failed. Check connectivity, original download permission and free disk space, then retry.")
                finally:
                    self._persist_plan()
            self.worker = threading.Thread(target=run, daemon=True, name="offline-preparation")
            self.worker.start()

    @staticmethod
    def check_download_permission(client):
        if getattr(client, "offline", False):
            raise OfflineError("Reconnect to Immich before preparing a workspace")
        if not client.session:
            metadata = client.json("GET", "/api-keys/me")
            permissions = metadata.get("permissions", []) if isinstance(metadata, dict) else []
            if "all" not in permissions and "asset.download" not in permissions:
                raise OfflineError("Preparing originals requires the additional asset.download permission. Add it to the key and reconnect.")

    def start_plan(self, client):
        self.check_download_permission(client)
        with self.lock:
            if self.worker and self.worker.is_alive():
                raise OfflineError("Offline preparation is already running")
            rows, faces = self.state.store.pending_snapshot()
            if self.state.store.setting("offlineBaseline") and (rows or faces or self.state.store.face_review_drafts()):
                raise OfflineError("Resolve offline Pending and Face Review drafts before replacing the prepared workspace")
            self.plan = None
            self.job.update(planId="", assetCount=0, personCount=0, estimatedBytes=0, unknownSizes=0,
                            downloadedFiles=0, downloadedBytes=0, error="")
            self.root.mkdir(parents=True, exist_ok=True)
            (self.root / "plan.json").unlink(missing_ok=True)
        self._run("planning", lambda: self._discover(client))

    def _discover(self, client):
        owner = json.loads(self.state.store.setting("owner") or "null")
        if not owner:
            raise OfflineError("A verified account is required")
        people = {}
        for person in client.all_people():
            self._check_cancel()
            pid = safe_id(person["id"])
            person = dict(person)
            # The detailed person contains representative selection and update baseline.
            detail = client.person(pid)
            if detail.get("id") != pid:
                raise OfflineError("Unexpected person metadata")
            person.update(detail)
            person["assetCount"] = client.statistics(pid)
            people[pid] = person
            with self.lock:
                self.job["personCount"] = len(people)
        assets, faces = {}, {}
        unnamed = {pid for pid, p in people.items() if not str(p.get("name") or "").strip() and not p.get("isHidden")}
        for pid in unnamed:
            for page in range(1, 100001):
                self._check_cancel()
                rows, more, _ = client.person_assets_page(pid, page, 100, group_photos=True)
                for row in rows:
                    aid = safe_id(row["id"])
                    if aid in assets:
                        continue
                    asset = client.asset(aid)
                    if asset.get("id") != aid:
                        raise OfflineError("Unexpected photo metadata")
                    if (str(asset.get("type", "IMAGE")).upper() != "IMAGE"
                            or asset.get("isTrashed") or asset.get("deletedAt") or asset.get("isDeleted")
                            or asset.get("isHidden") or asset.get("isLocked")
                            or str(asset.get("status") or "").lower() in {"trashed", "deleted"}
                            or str(asset.get("visibility", "timeline")).lower() not in {"timeline", "archive", "archived"}):
                        continue
                    current_faces = client.faces(aid)
                    for face in current_faces:
                        safe_id(face.get("id"))
                    if not any(face_person(face) in unnamed for face in current_faces):
                        continue
                    assets[aid], faces[aid] = asset, current_faces
                    with self.lock:
                        self.job["assetCount"] = len(assets)
                if not more:
                    break
                if not rows or page == 100000:
                    raise OfflineError("Asset pagination did not terminate")
        sizes = [(a.get("exifInfo") or {}).get("fileSizeInByte") or a.get("originalSize") or a.get("size") for a in assets.values()]
        known = [size for size in sizes if isinstance(size, int) and size > 0]
        plan_id = secrets.token_hex(16)
        with self.lock:
            self.job.update(phase="planned", planId=plan_id, assetCount=len(assets), personCount=len(people),
                            estimatedBytes=sum(known), unknownSizes=len(sizes) - len(known), downloadedFiles=0, downloadedBytes=0)
            self.plan = {"formatVersion": FORMAT_VERSION, "id": plan_id, "owner": owner,
                         "createdAt": dt.datetime.now(dt.timezone.utc).isoformat(), "serverVersion": client.server_version,
                         "people": people, "assets": assets, "faces": faces, "media": {"originals": {}, "previews": {}, "people": {}}}

    def start_download(self, client, plan_id):
        self.check_download_permission(client)
        with self.lock:
            if not self.plan or plan_id != self.plan["id"] or self.job["phase"] not in {"planned", "cancelled", "error"}:
                raise OfflineError("Plan changed or is unavailable. Prepare and review the estimate again.")
            if self.plan["owner"] != json.loads(self.state.store.setting("owner") or "null"):
                raise OfflineError("Offline plan account changed")
        self._run("downloading", lambda: self._download(client))

    def cancel(self):
        with self.lock:
            if self.worker and self.worker.is_alive() and self.job["phase"] in {"planning", "downloading", "cancelling"}:
                self.job["phase"] = "cancelling"
                self.cancel_event.set()

    def _download(self, client):
        snapshot = self.plan
        total_bytes = 0
        for aid, asset in snapshot["assets"].items():
            self._check_cancel()
            item = snapshot["media"]["originals"].get(aid)
            path = self.media_path(snapshot, "originals/" + safe_id(aid))
            path.parent.mkdir(parents=True, exist_ok=True)
            if item and path.is_file() and path.stat().st_size == item["size"] and self._digest(path) == item["sha256"]:
                total_bytes += item["size"]
            else:
                remaining = MAX_WORKSPACE_BYTES - total_bytes
                size, mime, digest = client.download_original(aid, path, self.cancel_event, min(MAX_ORIGINAL_BYTES, remaining), RESERVE_BYTES)
                total_bytes += size
                snapshot["media"]["originals"][aid] = {"path": "originals/" + aid, "size": size, "mime": mime, "sha256": digest}
            if total_bytes > MAX_WORKSPACE_BYTES:
                raise OfflineError("Workspace size limit exceeded")
            self._cache_preview(client, snapshot, "previews", aid, f"/assets/{aid}/thumbnail?size=preview")
            with self.lock:
                self.job.update(downloadedFiles=len(snapshot["media"]["originals"]), downloadedBytes=total_bytes)
            self._persist_plan()
        for pid in snapshot["people"]:
            self._check_cancel()
            # Some people legitimately have no thumbnail; asset previews still work.
            try:
                self._cache_preview(client, snapshot, "people", pid, f"/people/{pid}/thumbnail")
            except Exception as exc:
                self._check_cancel()
                if getattr(exc, "status", None) != 404:
                    raise
        self._check_cancel()
        snapshot.update(assetCount=len(snapshot["assets"]), personCount=len(snapshot["people"]), totalBytes=total_bytes)
        # Commit the new generation only after every original and metadata are durable.
        with self.state.mutation_lock, self.lock:
            # The cancellation decision and pointer publication are one critical
            # section. A cancel received while waiting for local mutations wins.
            self._check_cancel()
            if getattr(self.state.client, "offline", False):
                raise OfflineError("An offline workspace is open. Reconnect before activating a replacement snapshot.")
            identity = getattr(client, "connection_metadata", {})
            if (self.state.client is not client or client.base != snapshot["owner"]["url"]
                    or identity.get("userId") != snapshot["owner"]["userId"]
                    or json.loads(self.state.store.setting("owner") or "null") != snapshot["owner"]):
                raise OfflineError("The connection changed during preparation. Reconnect and explicitly resume the download.")
            rows, faces = self.state.store.pending_snapshot()
            if self.state.store.setting("offlineBaseline") and (rows or faces or self.state.store.face_review_drafts()):
                raise OfflineError("Offline work changed during preparation; resolve Pending before replacing its snapshot")
            atomic_json(self.root / "snapshots" / snapshot["id"] / "snapshot.json", snapshot)
            atomic_json(self.root / "active.json", {"id": snapshot["id"]})
            self.state.store.set_setting("offlineBaseline", "")
            self.job.update(phase="ready", error="")

    @staticmethod
    def _digest(path):
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _cache_preview(self, client, snapshot, kind, item_id, route):
        item = snapshot["media"][kind].get(item_id)
        if item and self.media_path(snapshot, item["path"]).is_file():
            return
        self._check_cancel()
        body, mime = client.request("GET", route)
        if not mime.startswith("image/") or len(body) > 20_000_000:
            raise OfflineError("Unexpected preview response")
        path = self.media_path(snapshot, kind + "/" + safe_id(item_id))
        path.parent.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(path.parent).free < len(body) + RESERVE_BYTES:
            raise OfflineError("Insufficient space for offline previews")
        temp = path.with_suffix(".part")
        temp.write_bytes(body)
        os.replace(temp, path)
        snapshot["media"][kind][item_id] = {"path": kind + "/" + item_id, "mime": mime, "size": len(body),
                                             "sha256": hashlib.sha256(body).hexdigest()}

    def open_client(self):
        snapshot = self.active(verify=True)
        if snapshot is None:
            raise OfflineError("No complete offline workspace is available. Prepare it while connected to Immich.")
        self.state.store.set_setting("offlineBaseline", snapshot["id"])
        baseline = self.root / "snapshots" / snapshot["id"] / "baseline.json"
        if not baseline.is_file():
            atomic_json(baseline, {"people": snapshot["people"], "faces": snapshot["faces"]})
        else:
            known = self._read(baseline)
            snapshot["people"], snapshot["faces"] = known["people"], known["faces"]
        return OfflineClient(self, snapshot)

    def prune_conflicts(self):
        rows, faces = self.state.store.pending_snapshot()
        rows = [r for r in rows if r["included"]]
        faces = [r for r in faces if r["included"]]
        pids = {row["person_id"] for row in rows}
        pids.update(row.get("target_person_id") for row in rows)
        pids.update(row["source_person_id"] for row in faces)
        aids = {row["asset_id"] for row in faces}
        aids.update(row.get("feature_asset_id") for row in rows)
        with self.lock:
            self.conflicts = [c for c in self.conflicts if c["id"] in pids or c["id"] in aids
                              or bool(set(c.get("personIds", [])) & pids)]
            return list(self.conflicts)

    def validate(self, client, rows=None, face_rows=None):
        if getattr(client, "offline", False):
            raise OfflineError("Reconnect to Immich, review Pending and explicitly Sync.")
        sid = self.state.store.setting("offlineBaseline")
        if not sid:
            return []
        try:
            directory = self.root / "snapshots" / safe_id(sid)
            snapshot = self._read(directory / "baseline.json")
        except (OSError, ValueError):
            raise OfflineError("Offline baseline is unavailable. Restore the complete state folder before syncing.") from None
        if rows is None or face_rows is None:
            rows, face_rows = self.state.store.pending_snapshot()
        rows = [r for r in rows if r["included"]]
        face_rows = [r for r in face_rows if r["included"]]
        pids = {row["person_id"] for row in rows}
        pids.update(row["target_person_id"] for row in rows if row.get("target_person_id"))
        pids.update(row["source_person_id"] for row in face_rows)
        aids = {row["asset_id"] for row in face_rows}
        aids.update(row["feature_asset_id"] for row in rows if row.get("feature_asset_id"))
        # Merges and representative selection depend on existing face assignments.
        for aid, faces in snapshot["faces"].items():
            if any(face_person(f) in pids for f in faces):
                aids.add(aid)
        conflicts = []
        for pid in sorted(pids):
            baseline = snapshot["people"].get(pid)
            try:
                current = client.person(pid)
                if baseline is None or person_baseline(current) != person_baseline(baseline):
                    conflicts.append({"type": "person", "id": pid, "reason": "Person name, visibility, representative face or version changed since preparation"})
                elif client.statistics(pid) != baseline.get("assetCount"):
                    conflicts.append({"type": "person", "id": pid, "reason": "Person photo membership changed since preparation"})
            except Exception:
                conflicts.append({"type": "person", "id": pid, "reason": "Person no longer exists or cannot be verified"})
        for aid in sorted(aids):
            try:
                baseline = snapshot["faces"].get(aid)
                asset = client.asset(aid)
                if baseline is None or asset.get("isTrashed") or asset.get("deletedAt") or face_baseline(client.faces(aid)) != face_baseline(baseline):
                    conflicts.append({"type": "asset", "id": aid, "personIds": sorted({face_person(f) for f in baseline or [] if face_person(f)}), "reason": "Photo availability or face assignments changed since preparation"})
            except Exception:
                conflicts.append({"type": "asset", "id": aid, "personIds": sorted({face_person(f) for f in snapshot["faces"].get(aid, []) if face_person(f)}), "reason": "Photo no longer exists or cannot be verified"})
        with self.lock:
            self.conflicts = conflicts
        return conflicts

    def refresh_after_sync(self, client, rows, face_rows, results):
        """Advance only expected own deltas; never bless arbitrary fresh upstream data."""
        successes = [r for r in results if r.get("result") == "synced"]
        if not successes:
            return
        # A snapshot is an offline preparation, not a continuously mirrored library.
        # Keep its files for recovery, but require fresh preparation after writes.
        try:
            active = self._read(self.root / "active.json")
            atomic_json(self.root / "active.json", {"id": safe_id(active["id"]), "stale": True})
        except FileNotFoundError:
            pass
        with self.lock:
            if self.job["phase"] == "ready":
                self.job.update(phase="idle", planId="")
        sid = self.state.store.setting("offlineBaseline")
        if not sid:
            return
        path = self.root / "snapshots" / safe_id(sid) / "baseline.json"
        snapshot = self._read(path)
        expected = copy.deepcopy(snapshot)
        touched, affected_assets, uncertain = set(), set(), set()
        person_rows = {row["person_id"]: row for row in rows}
        detach_rows = {row["face_id"]: row for row in face_rows}
        for result in successes:
            pid = result["personId"]
            touched.add(pid)
            if result.get("faceId"):
                row = detach_rows[result["faceId"]]
                target = result["targetPersonId"]
                touched.add(target)
                expected["people"].setdefault(target, {"id": target, "name": "", "isHidden": False, "assetCount": 0})
                aid = row["asset_id"]
                affected_assets.add(aid)
                for face in expected["faces"].get(aid, []):
                    if face.get("id") == result["faceId"]:
                        face["person"] = {"id": target}
                        if "personId" in face:
                            face["personId"] = target
            else:
                row = person_rows[pid]
                person = expected["people"].get(pid)
                if not person:
                    uncertain.add(pid)
                    continue
                operation = row["operation"]
                if operation == "rename":
                    person["name"] = row["name"]
                elif operation in {"hide", "unhide"}:
                    person["isHidden"] = operation == "hide"
                elif operation == "merge":
                    target = row["target_person_id"]
                    touched.add(target)
                    covered = sum(any(face_person(f) == pid for f in faces) for faces in snapshot["faces"].values())
                    if person.get("assetCount") != covered:
                        # Videos/uncached assets make the full target count uncertain.
                        uncertain.add(target)
                    for aid, faces in expected["faces"].items():
                        for face in faces:
                            if face_person(face) == pid:
                                affected_assets.add(aid)
                                face["person"] = {"id": target}
                                if "personId" in face:
                                    face["personId"] = target
                    expected["people"].pop(pid, None)
                if row.get("feature_asset_id"):
                    # Server versions represent selected faces differently. A fresh
                    # preparation is required rather than guessing the response field.
                    uncertain.add(row.get("target_person_id") if operation == "merge" else pid)
        # Photo counts change once per photo, even when several faces were moved.
        deltas = {}
        for aid in affected_assets:
            before = {face_person(f) for f in snapshot["faces"].get(aid, [])}
            after = {face_person(f) for f in expected["faces"].get(aid, [])}
            for pid in before - after:
                deltas[pid] = deltas.get(pid, 0) - 1
            for pid in after - before:
                deltas[pid] = deltas.get(pid, 0) + 1
        for pid, delta in deltas.items():
            person = expected["people"].get(pid)
            if person and isinstance(person.get("assetCount"), int):
                person["assetCount"] += delta
            elif person:
                uncertain.add(pid)
        conflicts = []
        for pid in touched:
            wanted = expected["people"].get(pid)
            if wanted is None:
                snapshot["people"].pop(pid, None)
                continue
            try:
                current = client.person(pid)
                current["assetCount"] = client.statistics(pid)
                expected_fields, current_fields = person_baseline(wanted), person_baseline(current)
                expected_fields.pop("updatedAt", None)
                current_fields.pop("updatedAt", None)
                if pid in uncertain or current_fields != expected_fields or current["assetCount"] != wanted.get("assetCount"):
                    raise OfflineError("Unexpected upstream change during Sync")
                snapshot["people"][pid] = current
            except Exception:
                conflicts.append({"type": "person", "id": pid, "reason": "Person changed during Sync or its exact resulting membership could not be established. Review it before retrying."})
        for aid in affected_assets:
            try:
                current = client.faces(aid)
                if face_baseline(current) != face_baseline(expected["faces"][aid]):
                    raise OfflineError("Unexpected face change during Sync")
                snapshot["faces"][aid] = current
            except Exception:
                conflicts.append({"type": "asset", "id": aid, "personIds": sorted(touched), "reason": "Photo faces changed during Sync. Review assignments before retrying."})
        with self.lock:
            self.conflicts = conflicts
        atomic_json(path, snapshot)
