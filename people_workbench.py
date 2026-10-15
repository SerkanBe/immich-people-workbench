#!/usr/bin/env python3
# Copyright (C) 2026 Serkan Bekdemir
# SPDX-License-Identifier: AGPL-3.0-only
"""Keyboard-first local people inbox for Immich.

The Immich session token is kept in memory. Names, skips, and pending operations are stored
locally in SQLite so an unfinished review survives restarts. Immich is only
changed by an explicit sync from the review screen.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import hashlib
import http.cookies
import ipaddress
import json
import math
import mimetypes
import os
import random
import re
import secrets
import signal
import sqlite3
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from contextlib import contextmanager, nullcontext
from functools import wraps
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
DEFAULT_STATE_DIR = ROOT / ".people-workbench"
MAX_BODY = 1_000_000
MAX_IMAGE = 20_000_000
UUID_RE = re.compile(r"^[0-9a-fA-F-]{20,50}$")
PAGE_ROUTES = frozenset({"/unnamed", "/naming/clusters", "/naming/group-photos", "/merge", "/investigate", "/faces", "/pending", "/named", "/ignored"})
SESSION_SECONDS = 8 * 60 * 60
ADMIN_CHECK_SECONDS = 60
SESSION_COOKIE = "people_workbench_session"

REQUIRED_PERMISSIONS = ('user.read', 'person.read', 'person.statistics', 'person.update', 'person.create', 'person.merge', 'asset.read', 'asset.view', 'face.read', 'face.update')
CONNECTION_REASONS = frozenset({"missing_permissions", "invalid_key", "session_expired", "unavailable", "unsupported_introspection", "invalid_login", "owner_mismatch", "connection_ended"})
# Official Permission enums at the source commits documented in docs/api-key-authentication.md.
KNOWN_PERMISSIONS = frozenset("""
activity.create activity.delete activity.read activity.statistics activity.update adminAuth.unlinkAll
adminConfig.read adminConfig.update adminSession.read adminUser.create adminUser.delete adminUser.read
adminUser.update album.create album.delete album.download album.read album.share
album.statistics album.update albumAsset.create albumAsset.delete albumUser.create albumUser.delete
albumUser.update all apiKey.create apiKey.delete apiKey.read apiKey.rotate
apiKey.update archive.read asset.copy asset.create asset.delete asset.derive
asset.download asset.edit.create asset.edit.delete asset.edit.get asset.read asset.share
asset.statistics asset.update asset.upload asset.view assetFile.delete assetFile.download
assetFile.read auth.changePassword authDevice.delete backup.delete backup.download backup.list
backup.upload clusterGroup.leave clusterGroup.read clusterGroupRequest.create clusterGroupRequest.delete clusterGroupRequest.read
duplicate.delete duplicate.read face.create face.delete face.read face.update
folder.read job.create job.read library.create library.delete library.read
library.statistics library.update maintenance map.read map.search memory.create
memory.delete memory.read memory.statistics memory.update memoryAsset.create memoryAsset.delete
notification.create notification.delete notification.read notification.update partner.create partner.delete
partner.read partner.update person.create person.delete person.merge person.read
person.reassign person.statistics person.update pinCode.create pinCode.delete pinCode.update
plugin.create plugin.delete plugin.read plugin.update queue.read queue.update
queueJob.create queueJob.delete queueJob.read queueJob.update server.about server.apkLinks
server.statistics server.storage server.versionCheck serverLicense.delete serverLicense.read serverLicense.update
session.create session.delete session.lock session.read session.update sharedLink.create
sharedLink.delete sharedLink.read sharedLink.update stack.create stack.delete stack.read
stack.update sync.stream syncCheckpoint.delete syncCheckpoint.read syncCheckpoint.update systemConfig.read
systemConfig.update systemMetadata.read systemMetadata.update tag.asset tag.create tag.delete
tag.read tag.update timeline.download timeline.read user.read user.update
userConfig.read userLicense.create userLicense.delete userLicense.read userLicense.update userOnboarding.delete
userOnboarding.read userOnboarding.update userPreference.read userPreference.update userProfileImage.create userProfileImage.delete
userProfileImage.read userProfileImage.update workflow.create workflow.delete workflow.logs workflow.read
workflow.update
""".split())


class ToolError(RuntimeError):
    pass


class UpstreamError(ToolError):
    def __init__(self, status: int):
        self.status = status
        super().__init__(f"Immich request failed (HTTP {status})")


class AuthenticationError(ToolError):
    def __init__(self, message: str, code: str, missing: Iterable[str] = (), reconnect: bool = False):
        self.code = code
        self.missing = sorted(set(missing) & set(REQUIRED_PERMISSIONS))[:len(REQUIRED_PERMISSIONS)]
        self.reconnect = reconnect
        super().__init__(message)

    def response(self) -> dict[str, Any]:
        result = {"error": str(self), "code": self.code}
        if self.missing:
            result["missingPermissions"] = self.missing
        if self.reconnect:
            result["reconnectRequired"] = True
        return result


class SyncInProgress(ToolError):
    pass


class MergeRejected(ToolError):
    """A valid response explicitly refused the requested merge."""


class MergeUncertain(ToolError):
    """The response cannot establish whether the requested merge completed."""


@contextmanager
def mutation_guard(lock):
    # Reentrant for Sync's own Store writes. Competing request threads never wait
    # and replay an edit after a job has changed its meaning.
    if not lock.acquire(blocking=False):
        raise SyncInProgress("Local changes are busy. Wait for Sync or the current save to finish, then retry.")
    try:
        yield
    finally:
        lock.release()


def local_mutation(method):
    @wraps(method)
    def guarded(self, *args, **kwargs):
        with mutation_guard(self.mutation_lock):
            return method(self, *args, **kwargs)
    return guarded


def pending_review_token(rows: list[dict[str, Any]], faces: list[dict[str, Any]]) -> str:
    payload = json.dumps({"people": rows, "faces": faces}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def normalized_name(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def name_fragment_matches(name: str, fragment: str) -> bool:
    return normalized_name(fragment).casefold() in normalized_name(name).casefold()


def sort_people(people: Iterable[dict[str, Any]], order: str) -> list[dict[str, Any]]:
    rows = list(people)
    if order == "fewest":
        return sorted(rows, key=lambda p: (p.get("assetCount") is None, p.get("assetCount") or 0, p.get("id", "")))
    if order == "updated-newest":
        return sorted(rows, key=lambda p: (p.get("updatedAt") or "", p.get("id", "")), reverse=True)
    if order == "updated-oldest":
        return sorted(rows, key=lambda p: (p.get("updatedAt") or "9999", p.get("id", "")))
    if order == "random":
        random.Random(dt.date.today().isoformat()).shuffle(rows)
        return rows
    return sorted(rows, key=lambda p: (-(p.get("assetCount") or 0), p.get("id", "")))


GROUP_ORDERS = {"most", "fewest", "updated-newest", "updated-oldest", "random"}


def group_asset_allowed(asset: dict[str, Any], include_archived: bool) -> bool:
    visibility = str(asset.get("visibility") or "timeline").lower()
    return (str(asset.get("type") or "IMAGE").upper() == "IMAGE"
            and visibility in ({"timeline", "archive", "archived"} if include_archived else {"timeline"})
            and (include_archived or not asset.get("isArchived"))
            and not asset.get("isTrashed") and not asset.get("deletedAt") and not asset.get("isDeleted")
            and str(asset.get("status") or "").lower() not in {"trashed", "deleted"}
            and not asset.get("isHidden") and not asset.get("isLocked"))


def group_face_person_id(face: dict[str, Any]) -> str | None:
    person = face.get("person")
    return person.get("id") if isinstance(person, dict) and isinstance(person.get("id"), str) else face.get("personId") if isinstance(face.get("personId"), str) else None


def sort_group_assets(rows: Iterable[dict[str, Any]], order: str) -> list[dict[str, Any]]:
    assets = list(rows)
    if order == "random":
        return sorted(assets, key=lambda row: (hashlib.sha256((dt.date.today().isoformat() + row["id"]).encode()).hexdigest(), row["id"]))
    if order.startswith("updated"):
        return sorted(assets, key=lambda row: (row.get("updatedAt") or "", row["id"]), reverse=order == "updated-newest")
    return sorted(assets, key=lambda row: ((row["unnamedCount"] if order == "fewest" else -row["unnamedCount"]), row["id"]))


def paginate(rows: Iterable[dict[str, Any]], page: int, size: int, key: str) -> dict[str, Any]:
    items = list(rows)
    size = max(1, min(int(size), 100))
    total = len(items)
    pages = max(1, (total + size - 1) // size)
    page = max(1, min(int(page), pages))
    start = (page - 1) * size
    end = min(start + size, total)
    return {
        key: items[start:end],
        "page": page,
        "size": size,
        "total": total,
        "pages": pages,
        "from": start + 1 if total else 0,
        "to": end,
        "hasPrevious": page > 1,
        "hasNext": page < pages,
    }


class NoCredentialRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Immich:
    def __init__(self, url: str, credential: str = "", insecure: bool = False, *, session: bool = False):
        base = url.strip().rstrip("/")
        parsed = urllib.parse.urlparse(base)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password or parsed.fragment or parsed.query:
            raise ToolError("Immich URL must start with http:// or https://")
        if not credential.strip() and session:
            raise ToolError("Immich session token is required")
        self.base = base if base.endswith("/api") else base + "/api"
        self.credential = credential.strip()
        self.session = session
        self.context = ssl._create_unverified_context() if insecure else None
        self.opener = urllib.request.build_opener(NoCredentialRedirect(), urllib.request.HTTPSHandler(context=self.context))
        self.server_version = "unknown"
        self.connection_metadata: dict[str, Any] = {}

    def request(self, method: str, path: str, payload: Any = None) -> tuple[bytes, str]:
        data = json.dumps(payload).encode() if payload is not None else None
        headers = {"Accept": "application/json"}
        if self.credential:
            headers["x-immich-user-token" if self.session else "x-api-key"] = self.credential
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=60) as response:
                body = response.read(MAX_IMAGE + 1)
                if len(body) > MAX_IMAGE:
                    raise ToolError("Immich response exceeded the safety limit")
                return body, response.headers.get_content_type()
        except urllib.error.HTTPError as exc:
            # Never expose upstream bodies: proxies and servers may reflect credentials.
            exc.close()
            raise UpstreamError(exc.code) from None
        except urllib.error.URLError as exc:
            raise ConnectionError("Cannot reach Immich. Check its URL, network and HTTPS certificate.") from None
        except (ValueError, OSError):
            raise ConnectionError("Cannot reach Immich. Check its URL, network and HTTPS certificate.") from None

    def json(self, method: str, path: str, payload: Any = None) -> Any:
        body, _ = self.request(method, path, payload)
        try:
            return json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ToolError("Immich returned invalid JSON") from None

    def version(self) -> str:
        data = self.json("GET", "/server/version")
        if isinstance(data, dict):
            if (isinstance(data.get("version"), str)
                    and re.fullmatch(r"v?\d+\.\d+\.\d+(?:[-+][a-zA-Z0-9.-]+)?", data["version"])
                    and (not self.credential or self.credential not in data["version"])):
                self.server_version = data["version"]
                return self.server_version
            values = [data.get(field) for field in ("major", "minor", "patch")]
            if all(isinstance(value, int) for value in values):
                self.server_version = ".".join(str(value) for value in values)
                return self.server_version
        return "unknown"

    def login(self, email: str, password: str) -> tuple[str, str]:
        data = self.json("POST", "/auth/login", {"email": email, "password": password})
        if not isinstance(data, dict) or data.get("isAdmin") is not True:
            raise ToolError("Only Immich admins can sign in to People Workbench")
        token, user_id = data.get("accessToken"), data.get("userId")
        if not isinstance(token, str) or not token or not isinstance(user_id, str) or not user_id:
            raise ToolError("Immich did not return a valid admin session")
        return token, user_id

    def current_admin(self) -> str:
        data = self.json("GET", "/users/me")
        if not isinstance(data, dict) or data.get("isAdmin") is not True or not isinstance(data.get("id"), str):
            raise ToolError("Immich admin access is no longer available")
        return data["id"]

    def all_people(self) -> list[dict[str, Any]]:
        people: list[dict[str, Any]] = []
        for page in range(1, 10000):
            query = urllib.parse.urlencode({"page": page, "size": 500, "withHidden": "true"})
            data = self.json("GET", "/people?" + query)
            if not isinstance(data, dict) or not isinstance(data.get("people"), list):
                raise ToolError("Unexpected people response from Immich")
            batch = [item for item in data["people"] if isinstance(item, dict) and isinstance(item.get("id"), str)]
            people.extend(batch)
            if not data.get("hasNextPage") or not batch:
                return people
        raise ToolError("People pagination did not terminate")

    def closest_people(self, person_id: str, limit: int = 20) -> list[dict[str, Any]]:
        query = urllib.parse.urlencode({
            "page": 1, "size": max(50, min(limit * 5, 100)), "withHidden": "false", "closestPersonId": person_id,
        })
        data = self.json("GET", "/people?" + query)
        if not isinstance(data, dict) or not isinstance(data.get("people"), list):
            raise ToolError("Unexpected similar-people response from Immich")
        return [item for item in data["people"] if isinstance(item, dict) and isinstance(item.get("id"), str)]

    def person(self, person_id: str) -> dict[str, Any]:
        data = self.json("GET", f"/people/{person_id}")
        if not isinstance(data, dict):
            raise ToolError("Unexpected person response")
        return data

    def statistics(self, person_id: str) -> int | None:
        data = self.json("GET", f"/people/{person_id}/statistics")
        return data.get("assets") if isinstance(data, dict) and isinstance(data.get("assets"), int) else None

    def update_person(self, person_id: str, payload: dict[str, Any]) -> None:
        self.json("PUT", f"/people/{person_id}", payload)

    def create_person(self) -> dict[str, Any]:
        data = self.json("POST", "/people", {"name": "", "isHidden": False})
        if not isinstance(data, dict) or not isinstance(data.get("id"), str):
            raise ToolError("Immich did not return the new unnamed person")
        return data

    def reassign_face(self, person_id: str, face_id: str) -> None:
        self.json("PUT", f"/faces/{person_id}", {"id": face_id})

    def merge(self, target_person_id: str, source_person_id: str) -> None:
        try:
            data = self.json("POST", f"/people/{target_person_id}/merge", {"ids": [source_person_id]})
        except Exception as exc:
            raise MergeUncertain("Merge outcome could not be confirmed. Check both people in Immich before removing or retrying this request.") from exc
        expected = {target_person_id, source_person_id}
        # Through v3.2.1 success responses contain source rows only. v3.3
        # adds a target row, whose failure can mean a different survivor was used.
        version = re.match(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$", self.server_version)
        legacy = bool(version and tuple(map(int, version.groups())) < (3, 3, 0))
        required = {source_person_id} if legacy else expected
        if (not isinstance(data, list) or not data
                or any(not isinstance(item, dict) or not isinstance(item.get("id"), str)
                       or item["id"] not in expected or type(item.get("success")) is not bool
                       or (item["success"] and item.get("error") is not None) for item in data)
                or len({item["id"] for item in data}) != len(data)
                or not required.issubset({item["id"] for item in data})):
            raise MergeUncertain("Unexpected merge response. Check both people in Immich; this request has not been marked successful.")
        failures = [item for item in data if not item["success"]]
        if failures:
            source = next(item for item in data if item["id"] == source_person_id)
            target = next((item for item in data if item["id"] == target_person_id), None)
            # NOT_FOUND is also a default result in newer Immich implementations:
            # group/other-owner effects may have happened without a success row.
            # Only a source permission refusal with a confirmed intended target
            # establishes that the source was excluded before merge processing.
            if (not source["success"] and source.get("error") == "NO_PERMISSION"
                    and ((target is not None and target["success"]) or (legacy and target is None))):
                raise MergeRejected("Immich refused the source merge: NO_PERMISSION")
            raise MergeUncertain("Immich could not confirm the complete merge. Check both people in Immich before removing or retrying this request.")

    def person_assets_page(self, person_id: str, page: int = 1, size: int = 50, *, group_photos: bool = False) -> tuple[list[dict[str, Any]], bool, int | None]:
        payload = {"personIds": [person_id], "page": max(1, page), "size": max(1, min(size, 100))}
        if group_photos:
            payload["withArchived"] = True
        data = self.json("POST", "/search/metadata", payload)
        if isinstance(data, dict) and isinstance(data.get("assets"), dict):
            assets = data["assets"]
        elif isinstance(data, dict):
            assets = data
        else:
            assets = {}
        items = [item for item in assets.get("items", []) if isinstance(item, dict) and isinstance(item.get("id"), str)]
        total = assets.get("total") if isinstance(assets.get("total"), int) else None
        next_page = assets.get("nextPage")
        has_more = bool(next_page) if next_page is not None else total is not None and page * size < total
        return items, has_more, total

    def person_assets(self, person_id: str, limit: int = 12) -> list[dict[str, Any]]:
        items, _, _ = self.person_assets_page(person_id, 1, max(1, min(limit, 50)))
        return items[:limit]

    def faces(self, asset_id: str) -> list[dict[str, Any]]:
        data = self.json("GET", "/faces?" + urllib.parse.urlencode({"id": asset_id}))
        return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []

    def asset(self, asset_id: str) -> dict[str, Any]:
        data = self.json("GET", f"/assets/{asset_id}")
        if not isinstance(data, dict):
            raise ToolError("Unexpected asset response from Immich")
        return data

    def samples(self, person_id: str, limit: int = 12) -> list[dict[str, Any]]:
        samples: list[dict[str, Any]] = []
        for asset in self.person_assets(person_id, limit * 2):
            asset_id = asset["id"]
            try:
                faces = self.faces(asset_id)
            except ToolError:
                continue
            face = next(
                (
                    item
                    for item in faces
                    if isinstance(item.get("person"), dict) and item["person"].get("id") == person_id
                ),
                None,
            )
            if not face:
                continue
            samples.append(
                {
                    "assetId": asset_id,
                    "faceId": face.get("id"),
                    "box": {
                        "x1": face.get("boundingBoxX1"),
                        "x2": face.get("boundingBoxX2"),
                        "y1": face.get("boundingBoxY1"),
                        "y2": face.get("boundingBoxY2"),
                        "width": face.get("imageWidth"),
                        "height": face.get("imageHeight"),
                    },
                }
            )
            if len(samples) >= limit:
                break
        return samples


class StoreConnection(sqlite3.Connection):
    """Keep SQLite's transaction context semantics and close on context exit."""

    def __exit__(self, *args: Any) -> bool:
        try:
            return super().__exit__(*args)
        finally:
            self.close()


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.mutation_lock = threading.RLock()
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS progress (
                    person_id TEXT PRIMARY KEY,
                    skip_count INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS pending (
                    person_id TEXT PRIMARY KEY,
                    operation TEXT NOT NULL,
                    name TEXT NOT NULL DEFAULT '',
                    target_person_id TEXT,
                    target_name TEXT NOT NULL DEFAULT '',
                    feature_asset_id TEXT,
                    included INTEGER NOT NULL DEFAULT 1,
                    last_error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS investigate (
                    person_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS face_detach_pending (
                    face_id TEXT PRIMARY KEY,
                    source_person_id TEXT NOT NULL,
                    asset_id TEXT NOT NULL,
                    file_name TEXT NOT NULL DEFAULT '',
                    target_person_id TEXT,
                    included INTEGER NOT NULL DEFAULT 1,
                    last_error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS face_reviews (
                    face_id TEXT PRIMARY KEY,
                    person_id TEXT NOT NULL,
                    reviewed_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS face_reviews_person_idx ON face_reviews(person_id);
                CREATE TABLE IF NOT EXISTS face_review_drafts (
                    face_id TEXT PRIMARY KEY,
                    person_id TEXT NOT NULL,
                    asset_id TEXT NOT NULL,
                    file_name TEXT NOT NULL DEFAULT '',
                    verdict TEXT NOT NULL CHECK(verdict IN ('correct', 'wrong', 'unreviewed')),
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS face_review_drafts_person_idx ON face_review_drafts(person_id);
                CREATE TABLE IF NOT EXISTS face_review_reopened (
                    person_id TEXT PRIMARY KEY,
                    reopened_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS face_review_inventory (
                    face_id TEXT PRIMARY KEY,
                    person_id TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS face_review_inventory_person_idx ON face_review_inventory(person_id);
                CREATE TABLE IF NOT EXISTS face_review_scan (
                    person_id TEXT PRIMARY KEY,
                    next_page INTEGER NOT NULL,
                    page_size INTEGER NOT NULL,
                    complete INTEGER NOT NULL,
                    asset_count INTEGER,
                    person_updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS group_photo_progress (
                    asset_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL CHECK(status IN ('deferred', 'ready', 'done')),
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS group_photo_drafts (
                    asset_id TEXT NOT NULL,
                    person_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    target_person_id TEXT,
                    operation TEXT NOT NULL DEFAULT 'rename',
                    PRIMARY KEY(asset_id, person_id)
                );
                """
            )
            if "merge_step" not in {row["name"] for row in db.execute("PRAGMA table_info(pending)")}:
                db.execute("ALTER TABLE pending ADD COLUMN merge_step TEXT NOT NULL DEFAULT 'ready'")
            if "operation" not in {row["name"] for row in db.execute("PRAGMA table_info(group_photo_drafts)")}:
                db.execute("ALTER TABLE group_photo_drafts ADD COLUMN operation TEXT NOT NULL DEFAULT 'rename'")

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30, factory=StoreConnection)
        db.row_factory = sqlite3.Row
        return db

    def skips(self) -> dict[str, int]:
        with self.connect() as db:
            return {row["person_id"]: row["skip_count"] for row in db.execute("SELECT person_id, skip_count FROM progress")}

    @local_mutation
    def skip(self, person_id: str) -> int:
        stamp = now()
        with self.connect() as db:
            db.execute(
                """INSERT INTO progress(person_id, skip_count, updated_at) VALUES(?, 1, ?)
                   ON CONFLICT(person_id) DO UPDATE SET skip_count=skip_count+1, updated_at=excluded.updated_at""",
                (person_id, stamp),
            )
            row = db.execute("SELECT skip_count FROM progress WHERE person_id=?", (person_id,)).fetchone()
        return int(row["skip_count"])

    @local_mutation
    def clear_progress(self, person_id: str) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM progress WHERE person_id=?", (person_id,))

    def investigate_ids(self) -> set[str]:
        with self.connect() as db:
            return {row["person_id"] for row in db.execute("SELECT person_id FROM investigate")}

    @local_mutation
    def mark_investigate(self, person_id: str) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO investigate(person_id, created_at) VALUES(?,?) ON CONFLICT(person_id) DO NOTHING",
                (person_id, now()),
            )

    @local_mutation
    def unmark_investigate(self, person_id: str) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM investigate WHERE person_id=?", (person_id,))

    @local_mutation
    def clear_investigate(self) -> int:
        with self.connect() as db:
            cursor = db.execute("DELETE FROM investigate")
            return max(0, cursor.rowcount)

    @local_mutation
    def upsert_pending(self, item: dict[str, Any]) -> None:
        self.upsert_pending_many([item])

    @local_mutation
    def upsert_pending_many(self, items: Iterable[dict[str, Any]]) -> None:
        stamp = now()
        rows = []
        for item in items:
            operation = str(item.get("operation", ""))
            if operation not in {"rename", "merge", "hide", "unhide"}:
                raise ToolError("Unsupported pending operation")
            rows.append(
                (
                    item["personId"], operation, normalized_name(item.get("name")), item.get("targetPersonId"),
                    normalized_name(item.get("targetName")), item.get("featureAssetId"), stamp, stamp,
                )
            )
        if not rows:
            return
        with self.connect() as db:
            self._upsert_pending_rows(db, rows)

    @staticmethod
    def _upsert_pending_rows(db: sqlite3.Connection, rows: list[tuple[Any, ...]]) -> None:
        for row in rows:
            existing = db.execute("SELECT merge_step FROM pending WHERE person_id=?", (row[0],)).fetchone()
            if existing and existing["merge_step"] != "ready":
                raise ToolError("This merge has already started. Review its outcome in Immich and remove the request explicitly before replacing it.")
        db.executemany(
            """INSERT INTO pending(person_id, operation, name, target_person_id, target_name,
                   feature_asset_id, included, last_error, created_at, updated_at)
               VALUES(?,?,?,?,?,?,1,'',?,?)
               ON CONFLICT(person_id) DO UPDATE SET operation=excluded.operation, name=excluded.name,
                   target_person_id=excluded.target_person_id, target_name=excluded.target_name,
                   feature_asset_id=excluded.feature_asset_id, included=1, last_error='',
                   updated_at=excluded.updated_at""",
            rows,
        )

    def group_progress(self) -> dict[str, str]:
        with self.connect() as db:
            return {row["asset_id"]: row["status"] for row in db.execute("SELECT asset_id,status FROM group_photo_progress")}

    @local_mutation
    def clear_group_progress(self, asset_id: str) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM group_photo_progress WHERE asset_id=?", (asset_id,))

    def group_drafts(self, asset_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT person_id,name,target_person_id,operation FROM group_photo_drafts WHERE asset_id=?", (asset_id,))]

    @local_mutation
    def save_group_draft(self, asset_id: str, person_id: str, name: str, target_id: str | None, operation: str = "rename") -> None:
        with self.connect() as db:
            if name:
                db.execute("INSERT INTO group_photo_drafts(asset_id,person_id,name,target_person_id,operation) VALUES(?,?,?,?,?) ON CONFLICT(asset_id,person_id) DO UPDATE SET name=excluded.name,target_person_id=excluded.target_person_id,operation=excluded.operation", (asset_id, person_id, name, target_id, operation))
            else:
                db.execute("DELETE FROM group_photo_drafts WHERE asset_id=? AND person_id=?", (asset_id, person_id))

    @local_mutation
    def group_advance(self, asset_id: str, status: str, entries: list[dict[str, Any]]) -> None:
        stamp = now()
        rows = [(item["personId"], item["operation"], normalized_name(item.get("name")), item.get("targetPersonId"), normalized_name(item.get("targetName")), None, stamp, stamp) for item in entries]
        with self.connect() as db:
            if rows:
                self._upsert_pending_rows(db, rows)
                db.executemany("DELETE FROM investigate WHERE person_id=?", [(item["personId"],) for item in entries])
                db.executemany("DELETE FROM progress WHERE person_id=?", [(item["personId"],) for item in entries])
                db.executemany("DELETE FROM group_photo_drafts WHERE person_id=?", [(item["personId"],) for item in entries])
            db.execute("INSERT INTO group_photo_progress(asset_id,status,updated_at) VALUES(?,?,?) ON CONFLICT(asset_id) DO UPDATE SET status=excluded.status,updated_at=excluded.updated_at", (asset_id, status, stamp))
            db.execute("DELETE FROM group_photo_drafts WHERE asset_id=?", (asset_id,))
            db.execute("UPDATE settings SET value='' WHERE key='group_current' AND value=?", (asset_id,))

    @local_mutation
    def start_group_deferred(self) -> None:
        with self.connect() as db:
            db.execute("UPDATE group_photo_progress SET status='ready' WHERE status='deferred'")
            db.execute("INSERT INTO settings(key,value) VALUES('group_pass','deferred') ON CONFLICT(key) DO UPDATE SET value='deferred'")
            db.execute("INSERT INTO settings(key,value) VALUES('group_current','') ON CONFLICT(key) DO UPDATE SET value='' ")

    @local_mutation
    def restart_group_initial(self) -> None:
        with self.connect() as db:
            db.execute("UPDATE group_photo_progress SET status='deferred' WHERE status='ready'")
            db.execute("INSERT INTO settings(key,value) VALUES('group_pass','initial') ON CONFLICT(key) DO UPDATE SET value='initial'")
            db.execute("INSERT INTO settings(key,value) VALUES('group_current','') ON CONFLICT(key) DO UPDATE SET value='' ")

    @local_mutation
    def reset_group_pass(self) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM group_photo_progress")
            db.execute("DELETE FROM group_photo_drafts")
            db.execute("DELETE FROM settings WHERE key IN ('group_current','group_pass')")

    def pending_snapshot(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        # One SQLite read transaction binds both queues to the token. A write
        # between separate reads must not produce a token for a mixed snapshot.
        with self.connect() as db:
            db.execute("BEGIN")
            rows = db.execute("SELECT * FROM pending ORDER BY created_at, person_id").fetchall()
            faces = db.execute("SELECT * FROM face_detach_pending ORDER BY created_at, face_id").fetchall()
        return [dict(row) for row in rows], [dict(row) for row in faces]

    def pending(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM pending ORDER BY created_at, person_id").fetchall()
        return [dict(row) for row in rows]

    def pending_ids(self) -> set[str]:
        return {item["person_id"] for item in self.pending()}

    @local_mutation
    def set_included(self, person_id: str, included: bool) -> None:
        with self.connect() as db:
            db.execute("UPDATE pending SET included=?, updated_at=? WHERE person_id=?", (int(included), now(), person_id))

    @local_mutation
    def remove_pending(self, person_id: str) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM pending WHERE person_id=?", (person_id,))

    @local_mutation
    def clear_pending(self) -> int:
        with self.connect() as db:
            people = db.execute("DELETE FROM pending").rowcount
            faces = db.execute("DELETE FROM face_detach_pending").rowcount
            return max(0, people) + max(0, faces)

    @local_mutation
    def set_merge_step(self, person_id: str, step: str) -> None:
        if step not in {"ready", "inflight", "uncertain", "complete"}:
            raise ToolError("Invalid merge checkpoint")
        with self.connect() as db:
            db.execute("UPDATE pending SET merge_step=?, updated_at=? WHERE person_id=?", (step, now(), person_id))

    @local_mutation
    def set_error(self, person_id: str, error: str) -> None:
        with self.connect() as db:
            db.execute("UPDATE pending SET last_error=?, updated_at=? WHERE person_id=?", (error[:2000], now(), person_id))

    @local_mutation
    def queue_face_detach(self, face_id: str, source_person_id: str, asset_id: str, file_name: str) -> None:
        stamp = now()
        with self.connect() as db:
            db.execute(
                """INSERT INTO face_detach_pending(
                       face_id, source_person_id, asset_id, file_name, target_person_id,
                       included, last_error, created_at, updated_at
                   ) VALUES(?,?,?,? ,NULL,1,'',?,?)
                   ON CONFLICT(face_id) DO UPDATE SET source_person_id=excluded.source_person_id,
                       asset_id=excluded.asset_id, file_name=excluded.file_name,
                       included=1, last_error='', updated_at=excluded.updated_at""",
                (face_id, source_person_id, asset_id, normalized_name(file_name), stamp, stamp),
            )

    def face_detaches(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM face_detach_pending ORDER BY created_at, face_id").fetchall()
        return [dict(row) for row in rows]

    def face_review_drafts(self, person_id: str | None = None) -> list[dict[str, Any]]:
        with self.connect() as db:
            if person_id is None:
                rows = db.execute("SELECT * FROM face_review_drafts ORDER BY updated_at, face_id").fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM face_review_drafts WHERE person_id=? ORDER BY updated_at, face_id", (person_id,)
                ).fetchall()
        return [dict(row) for row in rows]

    def reopened_review_ids(self) -> set[str]:
        with self.connect() as db:
            rows = db.execute("SELECT person_id FROM face_review_reopened").fetchall()
        return {row["person_id"] for row in rows}

    @local_mutation
    def set_review_reopened(self, person_id: str, reopened: bool) -> None:
        with self.connect() as db:
            if reopened:
                db.execute(
                    "INSERT INTO face_review_reopened(person_id,reopened_at) VALUES(?,?) "
                    "ON CONFLICT(person_id) DO UPDATE SET reopened_at=excluded.reopened_at",
                    (person_id, now()),
                )
            else:
                db.execute("DELETE FROM face_review_reopened WHERE person_id=?", (person_id,))

    @local_mutation
    def set_face_review_drafts(self, person_id: str, items: list[dict[str, str]]) -> None:
        stamp = now()
        with self.connect() as db:
            for item in items:
                if item["verdict"] == "clear":
                    db.execute("DELETE FROM face_review_drafts WHERE face_id=? AND person_id=?", (item["faceId"], person_id))
                else:
                    db.execute(
                        """INSERT INTO face_review_drafts(face_id,person_id,asset_id,file_name,verdict,updated_at)
                           VALUES(?,?,?,?,?,?) ON CONFLICT(face_id) DO UPDATE SET person_id=excluded.person_id,
                           asset_id=excluded.asset_id,file_name=excluded.file_name,verdict=excluded.verdict,
                           updated_at=excluded.updated_at""",
                        (item["faceId"], person_id, item["assetId"], item["fileName"], item["verdict"], stamp),
                    )

    @local_mutation
    def submit_face_review(self, person_id: str, expected: list[dict[str, Any]]) -> dict[str, int]:
        counts = {"correct": 0, "wrong": 0, "unreviewed": 0}
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            drafts = db.execute("SELECT * FROM face_review_drafts WHERE person_id=?", (person_id,)).fetchall()
            identity = lambda rows: sorted((row["face_id"], row["asset_id"], row["verdict"]) for row in rows)
            if identity(drafts) != identity(expected):
                raise ToolError("Review decisions changed in another tab; reload before submitting")
            if any(db.execute("SELECT 1 FROM face_detach_pending WHERE face_id=?", (row["face_id"],)).fetchone()
                   for row in drafts):
                raise ToolError("A selected face is already in Pending; reload before submitting")
            stamp = now()
            for row in drafts:
                verdict = row["verdict"]
                counts[verdict] += 1
                if verdict == "correct":
                    db.execute(
                        """INSERT INTO face_reviews(face_id,person_id,reviewed_at) VALUES(?,?,?)
                           ON CONFLICT(face_id) DO UPDATE SET person_id=excluded.person_id,
                           reviewed_at=excluded.reviewed_at""",
                        (row["face_id"], person_id, stamp),
                    )
                else:
                    db.execute("DELETE FROM face_reviews WHERE face_id=?", (row["face_id"],))
                if verdict == "wrong":
                    db.execute(
                        """INSERT INTO face_detach_pending(face_id,source_person_id,asset_id,file_name,
                           target_person_id,included,last_error,created_at,updated_at)
                           VALUES(?,?,?,?,NULL,1,'',?,?)
                           ON CONFLICT(face_id) DO UPDATE SET source_person_id=excluded.source_person_id,
                           asset_id=excluded.asset_id,file_name=excluded.file_name,included=1,
                           last_error='',updated_at=excluded.updated_at""",
                        (row["face_id"], person_id, row["asset_id"], row["file_name"], stamp, stamp),
                    )
            db.execute("DELETE FROM face_review_drafts WHERE person_id=?", (person_id,))
            db.execute("DELETE FROM face_review_reopened WHERE person_id=?", (person_id,))
        return counts

    @local_mutation
    def set_face_detach_included(self, face_id: str, included: bool) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE face_detach_pending SET included=?, updated_at=? WHERE face_id=?",
                (int(included), now(), face_id),
            )

    @local_mutation
    def set_face_detach_target(self, face_id: str, target_person_id: str) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE face_detach_pending SET target_person_id=?, updated_at=? WHERE face_id=?",
                (target_person_id, now(), face_id),
            )

    @local_mutation
    def set_face_detach_error(self, face_id: str, error: str) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE face_detach_pending SET last_error=?, updated_at=? WHERE face_id=?",
                (error[:2000], now(), face_id),
            )

    @local_mutation
    def remove_face_detach(self, face_id: str) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM face_detach_pending WHERE face_id=?", (face_id,))
            db.execute("DELETE FROM face_reviews WHERE face_id=?", (face_id,))

    @local_mutation
    def set_face_reviewed(self, face_id: str, person_id: str, reviewed: bool) -> None:
        with self.connect() as db:
            if reviewed:
                db.execute(
                    """INSERT INTO face_reviews(face_id, person_id, reviewed_at) VALUES(?,?,?)
                       ON CONFLICT(face_id) DO UPDATE SET person_id=excluded.person_id,
                           reviewed_at=excluded.reviewed_at""",
                    (face_id, person_id, now()),
                )
            else:
                db.execute("DELETE FROM face_reviews WHERE face_id=? AND person_id=?", (face_id, person_id))

    @local_mutation
    def set_faces_reviewed(self, face_ids: list[str], person_id: str, reviewed: bool) -> None:
        with self.connect() as db:
            if reviewed:
                db.executemany(
                    """INSERT INTO face_reviews(face_id, person_id, reviewed_at) VALUES(?,?,?)
                       ON CONFLICT(face_id) DO UPDATE SET person_id=excluded.person_id,
                           reviewed_at=excluded.reviewed_at""",
                    [(face_id, person_id, now()) for face_id in face_ids],
                )
            else:
                db.executemany(
                    "DELETE FROM face_reviews WHERE face_id=? AND person_id=?",
                    [(face_id, person_id) for face_id in face_ids],
                )

    @local_mutation
    def remove_face_review(self, face_id: str) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM face_reviews WHERE face_id=?", (face_id,))

    def reviewed_face_ids(self, person_id: str) -> set[str]:
        with self.connect() as db:
            rows = db.execute("SELECT face_id FROM face_reviews WHERE person_id=?", (person_id,)).fetchall()
        return {row["face_id"] for row in rows}

    def face_review_counts(self) -> dict[str, int]:
        with self.connect() as db:
            rows = db.execute(
                """SELECT person_id,COUNT(*) AS count FROM (
                     SELECT person_id,face_id FROM face_reviews
                       WHERE face_id NOT IN (SELECT face_id FROM face_review_drafts WHERE verdict='unreviewed')
                     UNION SELECT source_person_id,face_id FROM face_detach_pending
                     UNION SELECT person_id,face_id FROM face_review_drafts WHERE verdict IN ('correct','wrong')
                   ) GROUP BY person_id"""
            ).fetchall()
        return {row["person_id"]: int(row["count"]) for row in rows}

    def face_review_draft_counts(self) -> dict[str, int]:
        with self.connect() as db:
            rows = db.execute("SELECT person_id,COUNT(*) AS count FROM face_review_drafts GROUP BY person_id").fetchall()
        return {row["person_id"]: int(row["count"]) for row in rows}

    def record_face_review_page(self, person_id: str, page: int, page_size: int, faces: list[dict[str, Any]],
                                has_more: bool, asset_count: int | None, person_updated_at: str) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            state = db.execute("SELECT * FROM face_review_scan WHERE person_id=?", (person_id,)).fetchone()
            if (state and state["complete"] and state["page_size"] == page_size and state["asset_count"] == asset_count
                    and state["person_updated_at"] == person_updated_at):
                known = {row["face_id"] for row in db.execute(
                    "SELECT face_id FROM face_review_inventory WHERE person_id=?", (person_id,)
                )}
                if all(face["faceId"] in known for face in faces):
                    return
                db.execute("DELETE FROM face_review_inventory WHERE person_id=?", (person_id,))
                db.execute("DELETE FROM face_review_scan WHERE person_id=?", (person_id,))
                if page != 1:
                    return
                state = None
            if page == 1:
                db.execute("DELETE FROM face_review_inventory WHERE person_id=?", (person_id,))
            elif (not state or state["complete"] or state["next_page"] != page or state["page_size"] != page_size
                  or state["asset_count"] != asset_count
                  or state["person_updated_at"] != person_updated_at):
                return
            db.executemany(
                "INSERT INTO face_review_inventory(face_id,person_id) VALUES(?,?) "
                "ON CONFLICT(face_id) DO UPDATE SET person_id=excluded.person_id",
                [(face["faceId"], person_id) for face in faces],
            )
            db.execute(
                """INSERT INTO face_review_scan(person_id,next_page,page_size,complete,asset_count,person_updated_at)
                   VALUES(?,?,?,?,?,?) ON CONFLICT(person_id) DO UPDATE SET next_page=excluded.next_page,
                   page_size=excluded.page_size,complete=excluded.complete,asset_count=excluded.asset_count,
                   person_updated_at=excluded.person_updated_at""",
                (person_id, page + 1, page_size, int(not has_more), asset_count, person_updated_at),
            )

    def face_review_inventory(self, person_id: str) -> tuple[set[str], dict[str, Any] | None]:
        with self.connect() as db:
            state = db.execute("SELECT * FROM face_review_scan WHERE person_id=?", (person_id,)).fetchone()
            rows = db.execute("SELECT face_id FROM face_review_inventory WHERE person_id=?", (person_id,)).fetchall()
        return {row["face_id"] for row in rows}, dict(state) if state else None

    def face_review_inventory_summaries(self) -> dict[str, dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                """SELECT s.person_id,s.next_page,s.page_size,s.complete,s.asset_count,s.person_updated_at,
                          COUNT(i.face_id) AS total,
                          SUM(CASE WHEN d.verdict='unreviewed' THEN 0
                                   WHEN p.face_id IS NOT NULL OR d.verdict IN ('correct','wrong')
                                        OR r.face_id IS NOT NULL THEN 1 ELSE 0 END) AS handled
                   FROM face_review_scan s
                   LEFT JOIN face_review_inventory i ON i.person_id=s.person_id
                   LEFT JOIN face_reviews r ON r.face_id=i.face_id AND r.person_id=s.person_id
                   LEFT JOIN face_review_drafts d ON d.face_id=i.face_id AND d.person_id=s.person_id
                   LEFT JOIN face_detach_pending p ON p.face_id=i.face_id AND p.source_person_id=s.person_id
                   GROUP BY s.person_id"""
            ).fetchall()
        return {row["person_id"]: dict(row) for row in rows}

    def invalidate_face_review_inventory(self, person_id: str) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM face_review_scan WHERE person_id=?", (person_id,))
            db.execute("DELETE FROM face_review_inventory WHERE person_id=?", (person_id,))

    def setting(self, key: str, default: str = "") -> str:
        with self.connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return str(row["value"]) if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )


class AppState:
    def __init__(self, store: Store, reports: Path):
        self.store = store
        self.reports = reports
        self.lock = threading.RLock()
        self.sync_lock = threading.Lock()
        self.mutation_lock = store.mutation_lock
        self.face_scan_lock = threading.Lock()
        self.face_inventory_locks: dict[str, threading.Lock] = {}
        self.face_scan_failures: set[str] = set()
        self.client: Immich | None = None
        self.version = ""
        self.people: dict[str, dict[str, Any]] = {}
        self.phase = "idle"
        self.message = "Connect to Immich"
        self.progress = {"current": 0, "total": None}
        self.sample_cache: dict[str, list[dict[str, Any]]] = {}
        self.similarity_cache: dict[int, tuple[tuple[str, ...], dict[str, Any]]] = {}
        self.group_lock = threading.RLock()
        self.group_face_slots = threading.BoundedSemaphore(4)
        self.group_generation = 0
        self.group_candidates: dict[str, dict[str, Any]] = {}
        self.group_seen: set[str] = set()
        self.group_scanning = False
        self.group_complete = False
        self.group_failures = 0
        self.group_examined = 0
        self.group_sources = 0
        self.group_discovered = 0
        self.group_filtered = 0
        self.group_below_minimum = 0
        self.group_without_actionable = 0

    def invalidate_group_scan(self) -> None:
        with self.lock:
            self.group_generation += 1
            self.group_candidates = {}
            self.group_seen = set()
            self.group_scanning = False
            self.group_complete = False
            self.group_failures = 0
            self.group_examined = 0
            self.group_sources = 0
            self.group_discovered = 0
            self.group_filtered = 0
            self.group_below_minimum = 0
            self.group_without_actionable = 0

    def disconnect(self) -> None:
        with self.lock:
            self.client = None
            self.version = ""
            self.people = {}
            self.sample_cache = {}
            self.similarity_cache = {}
            self.invalidate_group_scan()
            self.face_scan_failures.clear()
            self.phase = "idle"
            self.message = "Sign in to Immich"
            self.progress = {"current": 0, "total": None}

    def set_status(self, phase: str, message: str, current: int = 0, total: int | None = None) -> None:
        with self.lock:
            self.phase, self.message = phase, message
            self.progress = {"current": current, "total": total}

    def set_status_for_client(self, client: Immich, phase: str, message: str, current: int = 0, total: int | None = None) -> bool:
        with self.lock:
            if client is not self.client:
                return False
            self.set_status(phase, message, current, total)
            return True

    def require_client(self) -> Immich:
        with self.lock:
            if self.client is None:
                raise ToolError("Connect to Immich first")
            return self.client

    @staticmethod
    def face_inventory_current(scan: dict[str, Any] | None, person: dict[str, Any]) -> bool:
        return bool(scan and scan["asset_count"] == person.get("assetCount")
                    and scan["person_updated_at"] == str(person.get("updatedAt") or ""))

    def connect(self, client: Immich) -> str:
        version = client.version()
        with self.lock:
            self.client, self.version = client, version
            self.people, self.sample_cache, self.similarity_cache = {}, {}, {}
            self.invalidate_group_scan()
            self.face_scan_failures.clear()
        self.set_status("connected", f"Connected to Immich {version}")
        return version

    def load(self) -> None:
        client: Immich | None = None
        try:
            client = self.require_client()
            if not self.set_status_for_client(client, "loading", "Loading people from Immich"):
                return
            people = client.all_people()
            if not self.set_status_for_client(client, "statistics", "Loading person photo counts", 0, len(people)):
                return
            counts: dict[str, int | None] = {}
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                futures = {pool.submit(client.statistics, person["id"]): person["id"] for person in people}
                for index, future in enumerate(concurrent.futures.as_completed(futures), 1):
                    try:
                        counts[futures[future]] = future.result()
                    except Exception:
                        counts[futures[future]] = None
                    if not self.set_status_for_client(client, "statistics", "Loading person photo counts", index, len(people)):
                        return
            mapped = {}
            for person in people:
                item = dict(person)
                item["name"] = normalized_name(item.get("name"))
                item["assetCount"] = counts.get(item["id"])
                mapped[item["id"]] = item
            with self.lock:
                if client is not self.client:
                    return
                self.people = mapped
                self.similarity_cache = {}
                self.invalidate_group_scan()
                self.face_scan_failures.clear()
                self.set_status_for_client(client, "ready", f"Loaded {len(mapped)} people", len(mapped), len(mapped))
        except Exception as exc:
            if client is not None:
                self.set_status_for_client(client, "error", str(exc))

    def summary(self) -> dict[str, Any]:
        with self.lock:
            people = list(self.people.values())
            connected, version = self.client is not None, self.version
            phase, message, progress = self.phase, self.message, dict(self.progress)
        pending = self.store.pending()
        face_pending = self.store.face_detaches()
        pending_ids = {item["person_id"] for item in pending}
        pending_merge_targets = {
            item["target_person_id"] for item in pending
            if item["operation"] == "merge" and item.get("target_person_id")
        }
        investigate_ids = self.store.investigate_ids()
        return {
            "connected": connected,
            "version": version,
            "connection": dict(getattr(self.client, "connection_metadata", {})) if connected else {},
            "capabilities": {"unnamedGroupMerge": True},
            "phase": phase,
            "message": message,
            "progress": progress,
            "counts": {
                "unnamed": sum(not item.get("name") and not item.get("isHidden") and item["id"] not in pending_ids and item["id"] not in pending_merge_targets and item["id"] not in investigate_ids for item in people),
                "named": sum(bool(item.get("name")) and not item.get("isHidden") for item in people),
                "ignored": sum(bool(item.get("isHidden")) for item in people),
                "faces": sum(not item.get("isHidden") for item in people),
                "pending": len(pending) + len(face_pending),
                "included": sum(bool(item["included"]) for item in [*pending, *face_pending]),
                "investigate": sum(item["id"] in investigate_ids for item in people),
            },
            "sort": self.store.setting("sort", "most"),
        }

    def list_people(self, kind: str, order: str) -> list[dict[str, Any]]:
        with self.lock:
            people = [dict(item) for item in self.people.values()]
        pending = self.store.pending()
        pending_ids = {item["person_id"] for item in pending}
        pending_merge_targets = {
            item["target_person_id"] for item in pending
            if item["operation"] == "merge" and item.get("target_person_id")
        }
        investigate_ids = self.store.investigate_ids()
        skips = self.store.skips()
        if kind == "unnamed":
            people = [p for p in people if not p.get("name") and not p.get("isHidden") and p["id"] not in pending_ids and p["id"] not in pending_merge_targets and p["id"] not in investigate_ids]
        elif kind == "investigate":
            people = [p for p in people if p["id"] in investigate_ids and p["id"] not in pending_ids]
        elif kind == "named":
            people = [p for p in people if p.get("name") and not p.get("isHidden") and p["id"] not in pending_ids]
        elif kind == "ignored":
            people = [p for p in people if p.get("isHidden") and p["id"] not in pending_ids]
        elif kind == "review":
            people = [p for p in people if not p.get("isHidden")]
        else:
            raise ToolError("Unknown people list")
        for person in people:
            person["skipCount"] = skips.get(person["id"], 0)
        if kind == "review":
            inventory = self.store.face_review_inventory_summaries()
            saved_counts = self.store.face_review_counts()
            draft_counts = self.store.face_review_draft_counts()
            reopened_ids = self.store.reopened_review_ids()
            pending_by_id = {item["person_id"]: item for item in pending}
            for person in people:
                scan = inventory.get(person["id"])
                current = self.face_inventory_current(scan, person)
                complete = bool(current and scan["complete"])
                known = int(scan["total"]) if current else 0
                reviewed = int(scan["handled"] or 0) if current and complete else saved_counts.get(person["id"], 0)
                person["reviewedCount"] = reviewed
                person["reviewTotal"] = known if complete else None
                person["inventoryComplete"] = complete
                person["reviewReopened"] = person["id"] in reopened_ids
                person["reviewed"] = complete and known > 0 and reviewed >= known and not draft_counts.get(person["id"]) and not person["reviewReopened"]
                person["draftCount"] = draft_counts.get(person["id"], 0)
                queued = pending_by_id.get(person["id"])
                if queued:
                    person["pendingOperation"] = queued["operation"]
                    person["pendingName"] = queued.get("target_name") or queued.get("name") or ""
                elif person["id"] in pending_merge_targets:
                    person["pendingOperation"] = "merge-target"
        people = sort_people(people, order)
        if kind == "unnamed":
            people.sort(key=lambda p: p["skipCount"])
        return people

    def people_page(self, kind: str, order: str, page: int, size: int, query: str = "", hide_completed: bool = False,
                    name_filter: str = "all") -> dict[str, Any]:
        if name_filter not in {"all", "named", "unnamed"} or (kind != "review" and name_filter != "all"):
            raise ToolError("Invalid people name filter")
        people = self.list_people(kind, order)
        if kind == "review" and name_filter != "all":
            people = [person for person in people if bool(normalized_name(person.get("name"))) == (name_filter == "named")]
        if kind == "review" and hide_completed:
            people = [person for person in people if not person["reviewed"]]
        needle = normalized_name(query).casefold()
        if needle:
            people = [
                person for person in people
                if needle in normalized_name(person.get("name") or "Unnamed person").casefold()
            ]
        return paginate(people, page, size, "people")

    def named_people(self) -> list[dict[str, Any]]:
        with self.lock:
            people = [dict(item) for item in self.people.values()]
        pending = {item["person_id"]: item for item in self.store.pending()}
        rows = []
        for person in people:
            queued = pending.get(person["id"])
            if queued and queued["operation"] in {"merge", "hide"}:
                continue
            if queued and queued["operation"] == "rename":
                person["name"] = queued["name"]
                person["isPending"] = True
            if person.get("name") and not person.get("isHidden"):
                rows.append(person)
        return sorted(rows, key=lambda p: (p["name"].casefold(), p["id"]))

    def group_settings(self) -> dict[str, Any]:
        try:
            minimum = int(self.store.setting("group_min_faces", "5"))
        except ValueError:
            minimum = 5
        order = self.store.setting("group_order", "most")
        return {"minFaces": max(2, min(20, minimum)), "includeArchived": self.store.setting("group_archived", "true") != "false", "order": order if order in GROUP_ORDERS else "most"}

    @local_mutation
    def set_group_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.group_lock:
            try:
                minimum = int(payload.get("minFaces"))
            except (TypeError, ValueError) as exc:
                raise ToolError("Minimum faces must be between 2 and 20") from exc
            order = payload.get("order")
            if minimum < 2 or minimum > 20 or order not in GROUP_ORDERS or not isinstance(payload.get("includeArchived"), bool):
                raise ToolError("Invalid Group photos settings")
            old = self.group_settings()
            self.store.set_setting("group_min_faces", str(minimum))
            self.store.set_setting("group_archived", "true" if payload["includeArchived"] else "false")
            self.store.set_setting("group_order", order)
            if minimum != old["minFaces"] or payload["includeArchived"] != old["includeArchived"]:
                self.store.restart_group_initial()
                self.invalidate_group_scan()
            return self.group_settings()

    def _group_actionable(self, person_id: str | None, pending_ids: set[str], merge_targets: set[str]) -> bool:
        if not person_id or person_id in pending_ids or person_id in merge_targets:
            return False
        person = self.people.get(person_id)
        return bool(person and not person.get("name") and not person.get("isHidden"))

    def _group_face_data(self, faces: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], set[str]]:
        pending = {row["person_id"]: row for row in self.store.pending()}
        pending_ids = set(pending)
        merge_targets = {row["target_person_id"] for row in pending.values() if row["operation"] == "merge" and row.get("target_person_id")}
        with self.lock:
            people = dict(self.people)
        rows: list[dict[str, Any]] = []
        actionable: set[str] = set()
        for face in faces:
            if not isinstance(face.get("id"), str):
                continue
            person_id = group_face_person_id(face)
            person = people.get(person_id) if person_id else None
            queued = pending.get(person_id)
            if person and not person.get("name") and not person.get("isHidden") and person_id not in pending_ids and person_id not in merge_targets:
                status, name = "actionable", ""
                actionable.add(person_id)
            elif queued:
                status, name = "pending", queued.get("target_name") or queued.get("name") or "Pending"
            elif person and person.get("name"):
                status, name = "named", person["name"]
            elif person and person.get("isHidden"):
                status, name = "ignored", "Ignored"
            elif person_id in merge_targets:
                status, name = "pending", "Pending merge"
            else:
                status, name = "unassigned", "Unassigned"
            rows.append({"id": face["id"], "personId": person_id, "status": status, "name": name,
                         "box": {"x1": face.get("boundingBoxX1"), "y1": face.get("boundingBoxY1"),
                                 "x2": face.get("boundingBoxX2"), "y2": face.get("boundingBoxY2"),
                                 "width": face.get("imageWidth"), "height": face.get("imageHeight")}})
        return rows, actionable

    def _group_scan(self, generation: int, client: Immich, person_ids: list[str]) -> None:
        def inspect(asset: dict[str, Any]) -> None:
            try:
                with self.group_face_slots:
                    faces = client.faces(asset["id"])
                _rows, actionable = self._group_face_data(faces)
                with self.lock:
                    if generation != self.group_generation:
                        return
                    self.group_examined += 1
                    if len(faces) < self.group_settings()["minFaces"]:
                        self.group_below_minimum += 1
                    elif not actionable:
                        self.group_without_actionable += 1
                    else:
                        counts = {person_id: sum(group_face_person_id(face) == person_id for face in faces) for person_id in actionable}
                        self.group_candidates[asset["id"]] = {"id": asset["id"], "fileName": asset.get("originalFileName") or "Photo", "updatedAt": asset.get("updatedAt") or asset.get("fileModifiedAt") or "", "unnamedCount": sum(counts.values()), "clusterCounts": counts, "faceCount": len(faces)}
            except Exception:
                with self.lock:
                    if generation == self.group_generation:
                        self.group_failures += 1

        try:
            current_id = self.store.setting("group_current")
            if current_id:
                try:
                    current_asset = client.asset(current_id)
                    with self.lock:
                        if generation != self.group_generation:
                            return
                        self.group_seen.add(current_id)
                        self.group_discovered += 1
                    if group_asset_allowed(current_asset, self.group_settings()["includeArchived"]):
                        inspect(current_asset)
                    else:
                        with self.lock:
                            if generation == self.group_generation:
                                self.group_filtered += 1
                except Exception:
                    with self.lock:
                        if generation == self.group_generation:
                            self.group_failures += 1
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                waiting: set[concurrent.futures.Future[None]] = set()
                for person_id in person_ids:
                    page = 1
                    while True:
                        with self.lock:
                            if generation != self.group_generation:
                                return
                        try:
                            assets, more, _total = client.person_assets_page(person_id, page, 100, group_photos=True)
                        except Exception:
                            with self.lock:
                                if generation == self.group_generation:
                                    self.group_failures += 1
                            break
                        for asset in assets:
                            with self.lock:
                                if generation != self.group_generation:
                                    return
                                if asset["id"] in self.group_seen:
                                    continue
                                self.group_seen.add(asset["id"])
                                self.group_discovered += 1
                            if not group_asset_allowed(asset, self.group_settings()["includeArchived"]):
                                with self.lock:
                                    if generation == self.group_generation:
                                        self.group_filtered += 1
                                continue
                            waiting.add(pool.submit(inspect, asset))
                            if len(waiting) >= 8:
                                done, waiting = concurrent.futures.wait(waiting, return_when=concurrent.futures.FIRST_COMPLETED)
                                for future in done:
                                    future.result()
                        if not more:
                            break
                        page += 1
                for future in concurrent.futures.as_completed(waiting):
                    future.result()
        except Exception:
            with self.lock:
                if generation == self.group_generation:
                    self.group_failures += 1
        finally:
            with self.lock:
                if generation == self.group_generation:
                    self.group_scanning = False
                    self.group_complete = True

    def start_group_scan(self, retry: bool = False) -> None:
        client = self.require_client()
        with self.lock:
            if self.phase != "ready":
                return
            if retry:
                if self.group_scanning:
                    raise ToolError("Wait for the current Group photos scan to finish before retrying")
                self.invalidate_group_scan()
            if self.group_scanning or self.group_complete:
                return
            pending = self.store.pending()
            excluded = {row["person_id"] for row in pending}
            excluded.update(row["target_person_id"] for row in pending if row["operation"] == "merge" and row.get("target_person_id"))
            person_ids = [person_id for person_id, person in self.people.items() if not person.get("name") and not person.get("isHidden") and person_id not in excluded]
            self.group_sources = len(person_ids)
            self.group_scanning = True
            generation = self.group_generation
        threading.Thread(target=self._group_scan, args=(generation, client, person_ids), daemon=True).start()

    @local_mutation
    def group_photo_state(self) -> dict[str, Any]:
        self.start_group_scan()
        with self.group_lock:
            settings = self.group_settings()
            pending = self.store.pending()
            blocked = {row["person_id"] for row in pending}
            blocked.update(row["target_person_id"] for row in pending if row["operation"] == "merge" and row.get("target_person_id"))
            with self.lock:
                candidates = sort_group_assets(
                    ({**row, "unnamedCount": sum(count for person_id, count in row["clusterCounts"].items()
                         if self._group_actionable(person_id, blocked, blocked))} for row in self.group_candidates.values()),
                    settings["order"])
                scanning, complete = self.group_scanning, self.group_complete
                examined, failures = self.group_examined, self.group_failures
                sources, discovered, filtered = self.group_sources, self.group_discovered, self.group_filtered
                below_minimum, without_actionable = self.group_below_minimum, self.group_without_actionable
            progress = self.store.group_progress()
            deferred_pass = self.store.setting("group_pass", "initial") == "deferred"
            current = self.store.setting("group_current")
            indexed_ids = {row["id"] for row in candidates}
            reopened = False
            for candidate in candidates:
                status = progress.get(candidate["id"])
                if status == "done" and candidate["unnamedCount"]:
                    self.store.clear_group_progress(candidate["id"])
                    progress.pop(candidate["id"], None)
                    reopened = True
                elif status != "done" and not candidate["unnamedCount"]:
                    self.store.group_advance(candidate["id"], "done", [])
                    progress[candidate["id"]] = "done"
            if complete and not failures:
                for asset_id, status in list(progress.items()):
                    if status in {"deferred", "ready"} and asset_id not in indexed_ids:
                        self.store.group_advance(asset_id, "done", [])
                        progress[asset_id] = "done"
            if reopened and deferred_pass:
                self.store.restart_group_initial()
                progress = self.store.group_progress()
                deferred_pass = False
                current = ""
            eligible = [row for row in candidates if (progress.get(row["id"]) == "ready" if deferred_pass else row["id"] not in progress)]
            if current:
                eligible.sort(key=lambda row: row["id"] != current)
            photo = None
            for candidate in eligible:
                try:
                    client = self.require_client()
                    asset = client.asset(candidate["id"])
                    if not group_asset_allowed(asset, settings["includeArchived"]):
                        self.store.group_advance(candidate["id"], "done", [])
                        continue
                    faces, actionable = self._group_face_data(client.faces(candidate["id"]))
                except Exception:
                    with self.lock:
                        self.group_failures += 1
                    break
                if len(faces) < settings["minFaces"] or not actionable:
                    self.store.group_advance(candidate["id"], "done", [])
                    continue
                if current != candidate["id"]:
                    self.store.set_setting("group_current", candidate["id"])
                photo = {key: value for key, value in candidate.items() if key != "clusterCounts"}
                photo.update({"faces": faces, "drafts": self.store.group_drafts(candidate["id"]), "unnamedCount": sum(face["status"] == "actionable" for face in faces)})
                break
            progress = self.store.group_progress()
            deferred = sum(status == "deferred" for status in progress.values())
            with self.lock:
                failures = self.group_failures
                indexed = len(self.group_candidates)
            checkpoint = photo is None and complete and failures == 0
            return {"settings": settings, "photo": photo, "scan": {"scanning": scanning, "complete": complete,
                    "examined": examined, "indexed": indexed, "failures": failures, "provisional": not complete,
                    "sources": sources, "discovered": discovered, "filtered": filtered,
                    "belowMinimum": below_minimum, "withoutActionable": without_actionable},
                    "pass": "deferred" if deferred_pass else "initial", "deferred": deferred, "checkpoint": checkpoint}

    def group_scan_progress(self) -> dict[str, Any]:
        self.start_group_scan()
        with self.lock:
            return {"scanning": self.group_scanning, "complete": self.group_complete,
                    "examined": self.group_examined, "indexed": len(self.group_candidates),
                    "failures": self.group_failures, "provisional": not self.group_complete,
                    "sources": self.group_sources, "discovered": self.group_discovered,
                    "filtered": self.group_filtered, "belowMinimum": self.group_below_minimum,
                    "withoutActionable": self.group_without_actionable}

    @local_mutation
    def save_group_draft(self, payload: dict[str, Any]) -> None:
        with self.group_lock:
            asset_id, person_id = payload.get("assetId"), payload.get("personId")
            if not isinstance(asset_id, str) or not UUID_RE.fullmatch(asset_id) or not isinstance(person_id, str) or not UUID_RE.fullmatch(person_id):
                raise ToolError("Invalid photo or cluster ID")
            if asset_id != self.store.setting("group_current"):
                raise ToolError("Photo changed; reload Group photos")
            pending = self.store.pending()
            pending_ids = {row["person_id"] for row in pending}
            merge_targets = {row["target_person_id"] for row in pending if row["operation"] == "merge" and row.get("target_person_id")}
            with self.lock:
                candidate = self.group_candidates.get(asset_id)
                actionable = bool(candidate and person_id in candidate["clusterCounts"] and self._group_actionable(person_id, pending_ids, merge_targets))
            if not actionable:
                raise ToolError("Cluster is no longer available for naming")
            name = normalized_name(payload.get("name"))
            target = payload.get("targetPersonId")
            operation = payload.get("operation", "rename")
            if operation not in {"rename", "hide"} or len(name) > 256 or (target is not None and (not isinstance(target, str) or not UUID_RE.fullmatch(target))):
                raise ToolError("Invalid name draft")
            if operation == "hide" and (name.casefold() != "ignore" or target is not None):
                raise ToolError("Invalid ignore draft")
            self.store.save_group_draft(asset_id, person_id, name, target, operation)

    @local_mutation
    def advance_group_photo(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.group_lock:
            asset_id = payload.get("assetId")
            if not isinstance(asset_id, str) or asset_id != self.store.setting("group_current"):
                raise ToolError("Photo changed; reload Group photos")
            decisions = payload.get("decisions")
            if not isinstance(decisions, list):
                raise ToolError("Invalid photo decisions")
            if not group_asset_allowed(self.require_client().asset(asset_id), self.group_settings()["includeArchived"]):
                raise ToolError("Photo is no longer eligible; reload Group photos")
            faces, actionable = self._group_face_data(self.require_client().faces(asset_id))
            if len(faces) < self.group_settings()["minFaces"] or not actionable:
                raise ToolError("Photo is no longer eligible; reload Group photos")
            entries = []
            used: set[str] = set()
            named = {person["id"]: person for person in self.named_people()}
            for decision in decisions:
                if not isinstance(decision, dict):
                    raise ToolError("Invalid photo decision")
                person_id = decision.get("personId")
                name = normalized_name(decision.get("name"))
                target_id = decision.get("targetPersonId")
                operation = decision.get("operation", "rename")
                if person_id not in actionable or person_id in used or operation not in {"rename", "hide"} or len(name) > 256:
                    raise ToolError("Stale or duplicate photo decision")
                used.add(person_id)
                if operation == "hide":
                    if name or target_id is not None:
                        raise ToolError("Invalid ignore decision")
                    entries.append({"personId": person_id, "operation": "hide", "name": ""})
                    continue
                if not name:
                    raise ToolError("Name is required")
                if target_id is not None:
                    target = named.get(target_id)
                    if not target or target_id == person_id or target["name"] != name:
                        raise ToolError("Merge target changed; review the name again")
                    entries.append({"personId": person_id, "operation": "merge", "name": name, "targetPersonId": target_id, "targetName": name})
                else:
                    entries.append({"personId": person_id, "operation": "rename", "name": name})
            status = "done" if actionable == used else "deferred"
            self.store.group_advance(asset_id, status, entries)
            return {"queued": len(entries), "status": status}

    @local_mutation
    def start_group_deferred(self) -> dict[str, Any]:
        with self.group_lock:
            state = self.group_photo_state()
            if not state["checkpoint"] or state["deferred"] == 0:
                raise ToolError("Deferred photos are not ready for review")
            self.store.start_group_deferred()
            return {"ok": True}

    @local_mutation
    def queue(self, payload: dict[str, Any]) -> None:
        person_id = str(payload.get("personId", ""))
        with self.lock:
            person = self.people.get(person_id)
        if not person:
            raise ToolError("Unknown person")
        operation = str(payload.get("operation", ""))
        name = normalized_name(payload.get("name"))
        target_id = payload.get("targetPersonId")
        if operation == "rename" and not name:
            raise ToolError("Name is required")
        if operation == "merge":
            if not isinstance(target_id, str) or target_id == person_id:
                raise ToolError("A different merge target is required")
            with self.lock:
                target = self.people.get(target_id)
            if not target:
                raise ToolError("Merge target no longer exists")
            target_name = normalized_name(target.get("name"))
            if not target_name:
                target_pending = next((item for item in self.store.pending() if item["person_id"] == target_id), None)
                if target_pending and target_pending["operation"] == "rename":
                    target_name = target_pending["name"]
            if not target_name:
                raise ToolError("Merge target does not have a current or pending name")
            payload["targetName"] = target_name
            payload["name"] = target_name
        self.store.upsert_pending(payload)
        self.store.unmark_investigate(person_id)
        self.store.clear_progress(person_id)

    @local_mutation
    def queue_merge_group(self, payload: dict[str, Any]) -> dict[str, Any]:
        raw_ids = payload.get("personIds")
        if not isinstance(raw_ids, list) or not raw_ids or len(raw_ids) > 100:
            raise ToolError("Select between 1 and 100 source clusters")
        person_ids = [str(value) for value in raw_ids]
        if any(not UUID_RE.fullmatch(value) for value in person_ids) or len(set(person_ids)) != len(person_ids):
            raise ToolError("Invalid or duplicate source cluster")
        name = normalized_name(payload.get("name"))
        target_id = str(payload.get("targetPersonId") or "")
        survivor_id = str(payload.get("survivorPersonId") or "")
        with self.lock:
            people = {person_id: self.people.get(person_id) for person_id in person_ids}
            target = self.people.get(target_id) if target_id else None
        if any(person is None for person in people.values()):
            raise ToolError("A selected cluster no longer exists")
        if any(person.get("name") or person.get("isHidden") for person in people.values() if person):
            raise ToolError("Merge Workbench only accepts visible unnamed source clusters")
        pending = {item["person_id"]: item for item in self.store.pending()}
        if any(person_id in pending for person_id in person_ids):
            raise ToolError("A selected cluster already has a pending change")

        entries: list[dict[str, Any]] = []
        if target_id:
            if not UUID_RE.fullmatch(target_id) or not target or target.get("isHidden") or target_id in person_ids:
                raise ToolError("Existing merge target is not available")
            target_name = normalized_name(target.get("name"))
            target_pending = pending.get(target_id)
            if not target_name and target_pending and target_pending["operation"] == "rename":
                target_name = normalized_name(target_pending["name"])
            if not target_name:
                raise ToolError("Existing merge target has no current or pending name")
            if name and name.casefold() != target_name.casefold():
                raise ToolError("Selected name no longer matches the merge target")
            entries = [
                {"personId": person_id, "operation": "merge", "name": target_name,
                 "targetPersonId": target_id, "targetName": target_name}
                for person_id in person_ids
            ]
            result = {"targetPersonId": target_id, "targetName": target_name, "merged": len(entries), "renamed": False}
        else:
            if len(person_ids) < 2:
                raise ToolError("Select at least two clusters for a new person")
            if not survivor_id:
                survivor_id = max(person_ids, key=lambda value: ((people[value] or {}).get("assetCount") or 0, value))
            if survivor_id not in people:
                raise ToolError("Surviving cluster must be part of the group")
            if name:
                entries.append({"personId": survivor_id, "operation": "rename", "name": name})
            entries.extend(
                {"personId": person_id, "operation": "merge", "name": name,
                 "targetPersonId": survivor_id, "targetName": name}
                for person_id in person_ids if person_id != survivor_id
            )
            result = {"targetPersonId": survivor_id, "targetName": name, "merged": len(person_ids) - 1, "renamed": bool(name)}

        self.store.upsert_pending_many(entries)
        for person_id in person_ids:
            self.store.unmark_investigate(person_id)
            self.store.clear_progress(person_id)
        return result

    def pending_public(self, rows: list[dict[str, Any]] | None = None,
                       face_rows: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        if rows is None or face_rows is None:
            rows, face_rows = self.store.pending_snapshot()
        with self.lock:
            people = self.people.copy()
        result = []
        for row in rows:
            person = people.get(row["person_id"], {})
            target = people.get(row.get("target_person_id"), {}) if row.get("target_person_id") else {}
            recovery = ""
            if row["merge_step"] in {"inflight", "uncertain"}:
                recovery = "Merge outcome is uncertain. Check both people in Immich before removing this request. Sync will not repeat the merge automatically."
            elif row["merge_step"] == "complete":
                recovery = "Merge completed. Sync will finish any remaining representative photo update and local cleanup without merging again."
            result.append({
                "kind": "person",
                "personId": row["person_id"], "operation": row["operation"], "name": row["name"],
                "targetPersonId": row.get("target_person_id"), "targetName": row["target_name"],
                "featureAssetId": row.get("feature_asset_id"), "included": bool(row["included"]),
                "lastError": row["last_error"] or recovery, "assetCount": person.get("assetCount"),
                "mergeStep": row["merge_step"],
                "currentName": person.get("name", ""), "targetAssetCount": target.get("assetCount"),
            })
        for row in face_rows:
            person = people.get(row["source_person_id"], {})
            result.append({
                "kind": "face", "operation": "detach-face", "faceId": row["face_id"],
                "personId": row["source_person_id"], "assetId": row["asset_id"],
                "fileName": row["file_name"], "included": bool(row["included"]),
                "lastError": row["last_error"], "currentName": person.get("name", ""),
                "assetCount": person.get("assetCount"),
            })
        return result

    def pending_data(self) -> dict[str, Any]:
        rows, faces = self.store.pending_snapshot()
        return {"pending": self.pending_public(rows, faces), "reviewToken": pending_review_token(rows, faces)}

    def pending_page(self, page: int, size: int) -> dict[str, Any]:
        data = self.pending_data()
        return {**paginate(data["pending"], page, size, "pending"), "reviewToken": data["reviewToken"]}

    def scan_face_review_page(self, active_person_id: str = "", retry_failed: bool = False) -> dict[str, Any]:
        if active_person_id and not UUID_RE.fullmatch(active_person_id):
            raise ToolError("Invalid active person ID")
        if not self.face_scan_lock.acquire(blocking=False):
            return {"busy": True}
        try:
            with self.lock:
                if retry_failed:
                    self.face_scan_failures.clear()
                people = [dict(person) for person in self.people.values() if not person.get("isHidden")]
                failures = set(self.face_scan_failures)
            self.require_client()
            inventory = self.store.face_review_inventory_summaries()
            incomplete = [person for person in people if not (
                self.face_inventory_current(inventory.get(person["id"]), person)
                and inventory[person["id"]]["complete"]
            )]
            available = [person for person in incomplete if person["id"] not in failures]
            available.sort(key=lambda person: (
                not self.face_inventory_current(inventory.get(person["id"]), person),
                not bool(person.get("name")), person.get("assetCount") or 0, person["id"],
            ))
            if active_person_id and len(available) > 1:
                available.sort(key=lambda person: person["id"] == active_person_id)
            scanned_id = ""
            scanned_progress = None
            if available:
                person = available[0]
                scanned_id = person["id"]
                scan = inventory.get(scanned_id)
                current = self.face_inventory_current(scan, person)
                page = int(scan["next_page"]) if current and scan and not scan["complete"] else 1
                size = int(scan["page_size"]) if current and scan and not scan["complete"] else 60
                try:
                    result = self.faces_for_person(scanned_id, page, size, workers=2)
                    scanned_progress = {key: result[key] for key in (
                        "reviewedCount", "reviewTotal", "inventoryComplete", "correctCount",
                        "wrongDraftCount", "queuedFaceCount", "draftCount", "untouchedCount",
                        "personReviewed", "reviewReopened",
                    )}
                except Exception:
                    with self.lock:
                        self.face_scan_failures.add(scanned_id)
            with self.lock:
                people = [dict(person) for person in self.people.values() if not person.get("isHidden")]
                failures = set(self.face_scan_failures)
            inventory = self.store.face_review_inventory_summaries()
            unfinished_ids = {person["id"] for person in people if not (
                self.face_inventory_current(inventory.get(person["id"]), person)
                and inventory[person["id"]]["complete"]
            )}
            complete = len(people) - len(unfinished_ids)
            failed = len(unfinished_ids & failures)
            return {"busy": False, "personId": scanned_id, "progress": scanned_progress,
                    "complete": complete, "total": len(people), "failed": failed,
                    "done": complete + failed >= len(people)}
        finally:
            self.face_scan_lock.release()

    def faces_for_person(self, person_id: str, page: int = 1, size: int = 40, workers: int = 6) -> dict[str, Any]:
        with self.lock:
            person_lock = self.face_inventory_locks.setdefault(person_id, threading.Lock())
        with person_lock:
            return self._faces_for_person(person_id, page, size, workers)

    def _faces_for_person(self, person_id: str, page: int, size: int, workers: int) -> dict[str, Any]:
        with self.lock:
            person = self.people.get(person_id)
        if not person or person.get("isHidden"):
            raise ToolError("Person is not available for face review")
        page = max(1, page)
        size = max(1, min(size, 60))
        client = self.require_client()
        assets, has_more, total = client.person_assets_page(person_id, page, size)

        def collect(asset: dict[str, Any]) -> list[dict[str, Any]]:
            asset_id = asset["id"]
            file_name = normalized_name(asset.get("originalFileName")) or str(asset.get("originalPath") or "").replace("\\", "/").rsplit("/", 1)[-1]
            rows = []
            for face in client.faces(asset_id):
                linked = face.get("person") if isinstance(face.get("person"), dict) else {}
                linked_id = linked.get("id") or face.get("personId")
                face_id = face.get("id")
                if linked_id != person_id or not isinstance(face_id, str):
                    continue
                rows.append({
                    "faceId": face_id, "assetId": asset_id, "fileName": file_name or "Unknown filename",
                    "box": {
                        "x1": face.get("boundingBoxX1"), "x2": face.get("boundingBoxX2"),
                        "y1": face.get("boundingBoxY1"), "y2": face.get("boundingBoxY2"),
                        "width": face.get("imageWidth"), "height": face.get("imageHeight"),
                    },
                })
            return rows

        faces: list[dict[str, Any]] = []
        if assets:
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(workers, len(assets))) as pool:
                for rows in pool.map(collect, assets):
                    faces.extend(rows)
        queued = {row["face_id"] for row in self.store.face_detaches()}
        reviewed = self.store.reviewed_face_ids(person_id)
        drafts = {row["face_id"]: row for row in self.store.face_review_drafts(person_id)}
        for face in faces:
            face["queued"] = face["faceId"] in queued
            face["reviewed"] = face["faceId"] in reviewed
            face["draftVerdict"] = drafts[face["faceId"]]["verdict"] if face["faceId"] in drafts else None
        self.store.record_face_review_page(person_id, page, size, faces, has_more, person.get("assetCount"),
                                           str(person.get("updatedAt") or ""))
        counts = self.face_review_progress(person_id, person)
        pages = max(1, (total + size - 1) // size)
        return {
            "faces": faces, "page": page, "size": size, "pages": pages,
            "hasPrevious": page > 1, "hasNext": has_more, "hasMore": has_more,
            "from": (page - 1) * size + 1 if total else 0,
            "to": min(page * size, total), "total": total, "totalAssets": total,
            **counts,
        }

    @local_mutation
    def set_face_reviewed(self, payload: dict[str, Any]) -> dict[str, Any]:
        person_id = str(payload.get("personId", ""))
        face_id = str(payload.get("faceId", ""))
        if not all(UUID_RE.fullmatch(value) for value in (person_id, face_id)):
            raise ToolError("Invalid face review item")
        with self.lock:
            person = self.people.get(person_id)
        if not person or person.get("isHidden"):
            raise ToolError("Person is no longer available for face review")
        self.store.set_face_reviewed(face_id, person_id, bool(payload.get("reviewed")))
        return self.face_review_progress(person_id, person)

    def face_review_progress(self, person_id: str, person: dict[str, Any]) -> dict[str, Any]:
        inventory, scan = self.store.face_review_inventory(person_id)
        if not self.face_inventory_current(scan, person):
            inventory, scan = set(), None
        complete = bool(scan and scan["complete"])
        queued = {row["face_id"] for row in self.store.face_detaches() if row["source_person_id"] == person_id}
        drafts = self.store.face_review_drafts(person_id)
        draft_correct = {row["face_id"] for row in drafts if row["verdict"] == "correct"}
        draft_wrong = {row["face_id"] for row in drafts if row["verdict"] == "wrong"}
        draft_unreviewed = {row["face_id"] for row in drafts if row["verdict"] == "unreviewed"}
        correct = (self.store.reviewed_face_ids(person_id) | draft_correct) - draft_wrong - draft_unreviewed - queued
        handled = (correct | draft_wrong | queued) - draft_unreviewed
        count = len(handled & inventory)
        reopened = person_id in self.store.reopened_review_ids()
        return {
            "reviewedCount": count, "reviewTotal": len(inventory) if complete else None,
            "inventoryComplete": complete,
            "correctCount": len(correct), "wrongDraftCount": len(draft_wrong),
            "queuedFaceCount": len(queued), "draftCount": len(drafts),
            "untouchedCount": len(inventory - handled),
            "personReviewed": complete and bool(inventory) and count >= len(inventory) and not drafts and not reopened,
            "reviewReopened": reopened,
        }

    @local_mutation
    def set_review_reopened(self, payload: dict[str, Any]) -> dict[str, Any]:
        person_id = str(payload.get("personId", ""))
        reopened = payload.get("reopened")
        if not UUID_RE.fullmatch(person_id) or not isinstance(reopened, bool):
            raise ToolError("Invalid review state")
        with self.lock:
            person = self.people.get(person_id)
        if not person or person.get("isHidden"):
            raise ToolError("Person is no longer available for face review")
        progress = self.face_review_progress(person_id, person)
        if reopened:
            if not progress["personReviewed"]:
                raise ToolError("Only a completed review can be reopened")
        elif not progress["reviewReopened"] or not progress["inventoryComplete"] or progress["untouchedCount"] or progress["draftCount"]:
            raise ToolError("Review the remaining faces and submit decisions before finishing")
        self.store.set_review_reopened(person_id, reopened)
        return self.face_review_progress(person_id, person)

    @local_mutation
    def set_face_review_drafts(self, payload: dict[str, Any]) -> dict[str, Any]:
        person_id = str(payload.get("personId", ""))
        items = payload.get("items")
        if not UUID_RE.fullmatch(person_id) or not isinstance(items, list) or not 1 <= len(items) <= 500:
            raise ToolError("Invalid face review draft")
        with self.lock:
            person = self.people.get(person_id)
        if not person or person.get("isHidden"):
            raise ToolError("Person is no longer available for face review")
        normalized = []
        seen: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                raise ToolError("Invalid face review draft")
            face_id, asset_id = item.get("faceId"), item.get("assetId")
            verdict = item.get("verdict")
            if (not isinstance(face_id, str) or not UUID_RE.fullmatch(face_id) or face_id in seen
                    or not isinstance(asset_id, str) or not UUID_RE.fullmatch(asset_id)
                    or verdict not in {"correct", "wrong", "unreviewed", "clear"}):
                raise ToolError("Invalid face review draft")
            seen.add(face_id)
            normalized.append({"faceId": face_id, "assetId": asset_id,
                               "fileName": normalized_name(item.get("fileName")), "verdict": verdict})
        queued = {row["face_id"] for row in self.store.face_detaches()}
        if seen & queued:
            raise ToolError("A selected face is already in Pending")
        self.store.set_face_review_drafts(person_id, normalized)
        return self.face_review_progress(person_id, person)

    @local_mutation
    def submit_face_review(self, payload: dict[str, Any]) -> dict[str, Any]:
        person_id = str(payload.get("personId", ""))
        if not UUID_RE.fullmatch(person_id):
            raise ToolError("Invalid person ID")
        with self.lock:
            person = self.people.get(person_id)
        if not person or person.get("isHidden"):
            raise ToolError("Person is no longer available for face review")
        drafts = self.store.face_review_drafts(person_id)
        if not drafts:
            raise ToolError("No review decisions to submit")
        wrong = [row for row in drafts if row["verdict"] == "wrong"]
        faces_by_asset: dict[str, list[dict[str, Any]]] = {}
        client = self.require_client() if wrong else None
        for row in wrong:
            asset_id = row["asset_id"]
            if asset_id not in faces_by_asset:
                faces_by_asset[asset_id] = client.faces(asset_id)
            matching = False
            for face in faces_by_asset[asset_id]:
                if not isinstance(face, dict):
                    continue
                linked = face.get("person") if isinstance(face.get("person"), dict) else {}
                if face.get("id") == row["face_id"] and (linked.get("id") or face.get("personId")) == person_id:
                    matching = True
                    break
            if not matching:
                raise ToolError("A wrong face is no longer assigned to this person; reload before submitting")
        submitted = self.store.submit_face_review(person_id, drafts)
        decisions = [{"faceId": row["face_id"], "verdict": row["verdict"]} for row in drafts]
        return {"submitted": submitted, "submittedFaces": decisions, **self.face_review_progress(person_id, person)}

    @local_mutation
    def set_faces_reviewed(self, payload: dict[str, Any]) -> dict[str, Any]:
        person_id = str(payload.get("personId", ""))
        face_ids = payload.get("faceIds")
        if (not UUID_RE.fullmatch(person_id) or not isinstance(face_ids, list)
                or not 1 <= len(face_ids) <= 500 or any(not isinstance(face_id, str) or not UUID_RE.fullmatch(face_id) for face_id in face_ids)
                or len(set(face_ids)) != len(face_ids)):
            raise ToolError("Invalid face review items")
        with self.lock:
            person = self.people.get(person_id)
        if not person or person.get("isHidden"):
            raise ToolError("Person is no longer available for face review")
        self.store.set_faces_reviewed(face_ids, person_id, bool(payload.get("reviewed")))
        return self.face_review_progress(person_id, person)

    @local_mutation
    def queue_face_detach(self, payload: dict[str, Any]) -> None:
        person_id = str(payload.get("personId", ""))
        face_id = str(payload.get("faceId", ""))
        asset_id = str(payload.get("assetId", ""))
        if not all(UUID_RE.fullmatch(value) for value in (person_id, face_id, asset_id)):
            raise ToolError("Invalid face review item")
        with self.lock:
            person = self.people.get(person_id)
        if not person or person.get("isHidden"):
            raise ToolError("Source person is no longer available")
        matching = False
        for face in self.require_client().faces(asset_id):
            linked = face.get("person") if isinstance(face.get("person"), dict) else {}
            if face.get("id") == face_id and (linked.get("id") or face.get("personId")) == person_id:
                matching = True
                break
        if not matching:
            raise ToolError("This face is no longer assigned to that person")
        self.store.queue_face_detach(face_id, person_id, asset_id, str(payload.get("fileName") or ""))
        self.store.set_face_reviewed(face_id, person_id, False)

    @local_mutation
    def discard_pending(self) -> int:
        with self.group_lock:
            discarded = self.store.clear_pending()
            self.store.reset_group_pass()
            self.invalidate_group_scan()
            with self.lock:
                self.sample_cache = {}
            return discarded

    def samples(self, person_id: str) -> list[dict[str, Any]]:
        with self.lock:
            cached = self.sample_cache.get(person_id)
        if cached is not None:
            return cached
        samples = self.require_client().samples(person_id)
        with self.lock:
            self.sample_cache[person_id] = samples
        return samples

    def files(self, person_id: str, limit: int = 16) -> list[dict[str, Any]]:
        with self.lock:
            if person_id not in self.people:
                raise ToolError("Unknown person")
        rows = []
        for asset in self.require_client().person_assets(person_id, limit):
            path = str(asset.get("originalPath") or "")
            file_name = normalized_name(asset.get("originalFileName")) or path.replace("\\", "/").rsplit("/", 1)[-1]
            rows.append({"assetId": asset["id"], "fileName": file_name or "Unknown filename", "originalPath": path})
        return rows

    def similar_people(self, person_id: str, limit: int = 12) -> list[dict[str, Any]]:
        with self.lock:
            if person_id not in self.people:
                raise ToolError("Unknown person")
        available = {person["id"] for person in self.list_people("unnamed", "most")}
        rows = self.require_client().closest_people(person_id, max(1, min(limit, 100)))
        with self.lock:
            people = self.people.copy()
        result = []
        for row in rows:
            candidate_id = row["id"]
            if candidate_id == person_id or candidate_id not in available or candidate_id not in people:
                continue
            result.append(dict(people[candidate_id]))
            if len(result) >= limit:
                break
        return result

    def similarity_graph(self, neighbors: int = 8) -> dict[str, Any]:
        """Build a read-only k-nearest-neighbor graph from Immich's face ranking.

        Immich exposes the order of nearby face embeddings but not their numeric
        distance. Reciprocal rank therefore becomes the graph weight, with mutual
        neighbor relationships naturally receiving weight from both directions.
        """
        rows = self.list_people("unnamed", "most")
        person_ids = [person["id"] for person in rows]
        available = set(person_ids)
        if len(person_ids) > 2000:
            raise ToolError("Similarity map is limited to 2,000 available clusters")
        neighbors = max(3, min(neighbors, 15))
        signature = tuple(person_ids)
        with self.lock:
            cached = self.similarity_cache.get(neighbors)
        if cached and cached[0] == signature:
            return dict(cached[1])
        client = self.require_client()

        def ranked(person_id: str) -> tuple[str, list[str], str | None]:
            try:
                candidates = client.closest_people(person_id, 100)
                result = []
                for candidate in candidates:
                    candidate_id = candidate.get("id")
                    if candidate_id != person_id and candidate_id in available:
                        result.append(candidate_id)
                    if len(result) >= neighbors:
                        break
                return person_id, result, None
            except Exception as exc:
                return person_id, [], str(exc)

        ranked_by_id: dict[str, list[str]] = {}
        failures: list[dict[str, str]] = []
        if person_ids:
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(person_ids))) as pool:
                for person_id, candidate_ids, error in pool.map(ranked, person_ids):
                    ranked_by_id[person_id] = candidate_ids
                    if error:
                        failures.append({"personId": person_id, "error": error})

        edge_weights: dict[tuple[str, str], dict[str, Any]] = {}
        for source_id, candidate_ids in ranked_by_id.items():
            for rank, target_id in enumerate(candidate_ids, start=1):
                key = tuple(sorted((source_id, target_id)))
                edge = edge_weights.setdefault(key, {"source": key[0], "target": key[1], "weight": 0.0, "directions": 0})
                edge["weight"] += 1.0 / rank
                edge["directions"] += 1

        edges = []
        for edge in edge_weights.values():
            edges.append({
                "source": edge["source"], "target": edge["target"],
                "weight": round(edge["weight"], 6), "mutual": edge["directions"] > 1,
            })
        edges.sort(key=lambda edge: (-edge["weight"], edge["source"], edge["target"]))
        result = {"nodes": person_ids, "edges": edges, "neighbors": neighbors, "failed": len(failures)}
        with self.lock:
            self.similarity_cache[neighbors] = (signature, result)
        return dict(result)

    def sync(self, review_token: str | None = None) -> dict[str, Any]:
        if not self.sync_lock.acquire(blocking=False):
            raise SyncInProgress("Sync already in progress")
        try:
            with mutation_guard(self.mutation_lock):
                return self._sync(review_token)
        finally:
            self.sync_lock.release()

    def _sync(self, review_token: str | None = None) -> dict[str, Any]:
        all_pending, all_face_pending = self.store.pending_snapshot()
        if review_token is not None and (not isinstance(review_token, str)
                or not re.fullmatch(r"[0-9a-f]{64}", review_token)
                or not secrets.compare_digest(review_token, pending_review_token(all_pending, all_face_pending))):
            raise SyncInProgress("Pending changed; review updated selections before syncing again.")
        client = self.require_client()
        draft_people = {row["person_id"] for row in self.store.face_review_drafts()}
        pending_by_id = {row["person_id"]: row for row in all_pending}
        rows = [row for row in all_pending if row["included"]]
        face_rows = [row for row in all_face_pending if row["included"]]
        unresolved_faces = {row["source_person_id"] for row in all_face_pending if not row["included"]}
        priority = {"rename": 0, "unhide": 0, "hide": 0, "merge": 1}
        rows.sort(key=lambda row: (priority.get(row["operation"], 9), row["created_at"], row["person_id"]))
        results = []
        failed_ids: set[str] = set()
        for row in face_rows:
            face_id = row["face_id"]
            try:
                self.store.invalidate_face_review_inventory(row["source_person_id"])
                target_id = row.get("target_person_id")
                if not target_id:
                    target_id = client.create_person()["id"]
                    self.store.set_face_detach_target(face_id, target_id)
                self.store.invalidate_face_review_inventory(target_id)
                client.reassign_face(target_id, face_id)
                self.store.remove_face_detach(face_id)
                self.store.remove_face_review(face_id)
                results.append({
                    "faceId": face_id, "personId": row["source_person_id"],
                    "operation": "detach-face", "result": "synced", "targetPersonId": target_id,
                })
            except Exception as exc:
                unresolved_faces.add(row["source_person_id"])
                self.store.set_face_detach_error(face_id, str(exc))
                results.append({
                    "faceId": face_id, "personId": row["source_person_id"],
                    "operation": "detach-face", "result": "failed", "error": str(exc),
                })
        for row in rows:
            person_id = row["person_id"]
            try:
                operation = row["operation"]
                target_id = row.get("target_person_id") if operation == "merge" else None
                if person_id in draft_people or target_id in draft_people:
                    raise ToolError("Submit Face Review decisions before syncing this person")
                if person_id in unresolved_faces or target_id in unresolved_faces:
                    raise ToolError("A wrong-face correction must sync before this person change")
                if operation == "rename":
                    body: dict[str, Any] = {"name": row["name"]}
                    if row.get("feature_asset_id"):
                        body["featureFaceAssetId"] = row["feature_asset_id"]
                    client.update_person(person_id, body)
                elif operation == "merge":
                    target_id = row.get("target_person_id")
                    if not target_id:
                        raise ToolError("Merge target missing")
                    target_pending = pending_by_id.get(target_id)
                    if target_pending and target_pending["operation"] == "rename" and not target_pending["included"]:
                        raise ToolError("Merge target's pending name is excluded from this sync")
                    if target_id in failed_ids:
                        raise ToolError("Merge target's pending name failed to sync")
                    self.store.invalidate_face_review_inventory(person_id)
                    self.store.invalidate_face_review_inventory(target_id)
                    step = row["merge_step"]
                    if step in {"inflight", "uncertain"}:
                        self.store.set_merge_step(person_id, "uncertain")
                        raise MergeUncertain("Merge outcome is uncertain. Check source and destination in Immich; remove this request only after reconciling the result. Sync will not repeat the merge automatically.")
                    if step != "complete":
                        # Commit before the request: a crash or lost reply must not
                        # automatically replay a possibly completed upstream merge.
                        self.store.set_merge_step(person_id, "inflight")
                        try:
                            client.merge(target_id, person_id)
                        except MergeRejected:
                            self.store.set_merge_step(person_id, "ready")
                            raise
                        except Exception:
                            self.store.set_merge_step(person_id, "uncertain")
                            raise
                        self.store.set_merge_step(person_id, "complete")
                    if row.get("feature_asset_id"):
                        try:
                            client.update_person(target_id, {"featureFaceAssetId": row["feature_asset_id"]})
                        except Exception as exc:
                            raise ToolError("Merge completed; representative photo update failed. Retry Sync to apply only the photo update. " + str(exc)) from exc
                elif operation == "hide":
                    client.update_person(person_id, {"isHidden": True})
                elif operation == "unhide":
                    client.update_person(person_id, {"isHidden": False})
                else:
                    raise ToolError("Unsupported pending operation")
                self.store.remove_pending(person_id)
                self.store.clear_progress(person_id)
                results.append({"personId": person_id, "operation": operation, "result": "synced"})
            except Exception as exc:
                failed_ids.add(person_id)
                self.store.set_error(person_id, str(exc))
                results.append({"personId": person_id, "operation": row["operation"], "result": "failed", "error": str(exc)})
        self.reports.mkdir(parents=True, exist_ok=True)
        report = self.reports / f"sync-{dt.datetime.now().astimezone():%Y%m%d-%H%M%S}.json"
        temp = report.with_suffix(".tmp")
        temp.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temp, report)
        if rows or face_rows:
            self.load()
        return {"results": results, "failed": sum(item["result"] == "failed" for item in results), "report": str(report)}


class AdminSession:
    """One memory-only connection per local database; login remains admin-only.

    Lock order is mutation_lock -> auth.lock -> state.lock. Never perform upstream
    I/O holding auth.lock. Periodic checks use immutable epoch/client snapshots;
    Sync holds mutation_lock through fresh validation and all writes.
    """

    def __init__(self, state: AppState):
        self.state = state
        self.lock = threading.RLock()
        self.cookie = ""
        self.user_id = ""
        self.expires = 0.0
        self.checked = 0.0
        self.epoch = 0
        self.mode = "login"

    @staticmethod
    def key_identity(client: Immich) -> dict[str, Any]:
        try:
            # Introspection requires no apiKey.read, so read it before users/me to
            # report a missing user.read explicitly without making a write probe.
            metadata = client.json("GET", "/api-keys/me")
            permissions = metadata.get("permissions") if isinstance(metadata, dict) else None
            if (not isinstance(metadata, dict) or not isinstance(metadata.get("id"), str)
                    or not UUID_RE.fullmatch(metadata["id"]) or not isinstance(permissions, list)
                    or not permissions or any(not isinstance(p, str) or p not in KNOWN_PERMISSIONS for p in permissions)):
                raise AuthenticationError("Immich key introspection is incompatible. Use Admin login or a supported Immich version.", "unsupported_introspection")
            granted = set(permissions)
            missing = set(REQUIRED_PERMISSIONS) - granted if "all" not in granted else set()
            if missing:
                raise AuthenticationError("Missing permissions: " + ", ".join(sorted(missing)) + ". Add them and re-enter the key.", "missing_permissions", missing)
            user = client.json("GET", "/users/me")
            if (not isinstance(user, dict) or not isinstance(user.get("id"), str)
                    or not UUID_RE.fullmatch(user["id"]) or not isinstance(user.get("isAdmin"), bool)):
                raise AuthenticationError("Immich did not return a verified account. Re-enter a valid key.", "invalid_key")
            if "userId" in metadata and metadata["userId"] != user["id"]:
                raise AuthenticationError("Immich key ownership could not be verified.", "invalid_key")
            display = next((user.get(field) for field in ("name", "email") if isinstance(user.get(field), str) and user[field].strip()), "Account connected")
            # A malformed upstream identity must not reflect the credential.
            display = "Account connected" if client.credential in display else display[:160]
            return {"authMode": "apiKey", "userId": user["id"], "isAdmin": user["isAdmin"],
                    "displayName": display, "requiredPermissions": list(REQUIRED_PERMISSIONS),
                    "grantedPermissions": sorted(granted), "overbroad": bool(granted - set(REQUIRED_PERMISSIONS))}
        except UpstreamError as exc:
            if exc.status in {401, 403}:
                raise AuthenticationError("The API key was rejected or revoked. Re-enter a valid key.", "invalid_key") from None
            if exc.status in {400, 404, 405}:
                raise AuthenticationError("Immich key introspection is unavailable. Use Admin login or a supported Immich version.", "unsupported_introspection") from None
            raise AuthenticationError("Immich is unavailable. Retry when the server is reachable.", "unavailable") from None
        except AuthenticationError:
            raise
        except Exception:
            raise AuthenticationError("Cannot reach or verify Immich. Check its URL, network and HTTPS certificate.", "unavailable") from None

    def _install(self, client: Immich, identity: dict[str, Any], version: str) -> tuple[str, str]:
        # Caller has fully validated its candidate before claiming any owner.
        with mutation_guard(self.state.mutation_lock), self.lock:
            owner = json.dumps({"url": client.base, "userId": identity["userId"]}, sort_keys=True)
            bound_owner = self.state.store.setting("owner")
            if bound_owner and bound_owner != owner:
                raise AuthenticationError("This local state belongs to a different Immich account or server. Use a separate --state-dir.", "owner_mismatch")
            if not bound_owner:
                self.state.store.set_setting("owner", owner)
            client.connection_metadata = identity
            # connect() normally fetches version. It is already verified; avoid
            # network I/O while publishing the new connection under auth.lock.
            with self.state.lock:
                self.state.client, self.state.version = client, version
                self.state.people, self.state.sample_cache, self.state.similarity_cache = {}, {}, {}
                self.state.invalidate_group_scan()
                self.state.face_scan_failures.clear()
                self.state.set_status("connected", f"Connected to Immich {version}")
            self.epoch += 1
            self.mode = identity["authMode"]
            self.cookie = secrets.token_urlsafe(32)
            self.user_id = identity["userId"]
            self.expires = time.monotonic() + SESSION_SECONDS
            self.checked = time.monotonic()
            return version, self.cookie

    def login(self, url: str, email: str, password: str, insecure: bool) -> tuple[str, str]:
        if not email.strip() or not password:
            raise ToolError("Immich email and password are required")
        with mutation_guard(self.state.mutation_lock):
            anonymous = Immich(url, insecure=insecure)
            token, user_id = anonymous.login(email.strip(), password)
            client = Immich(url, token, insecure, session=True)
            if client.current_admin() != user_id:
                raise ToolError("Immich account verification failed")
            identity = {"authMode": "login", "userId": user_id, "isAdmin": True,
                        "displayName": "Admin account connected", "requiredPermissions": [],
                        "grantedPermissions": [], "overbroad": False}
            return self._install(client, identity, client.version())

    def connect_key(self, url: str, key: str, insecure: bool) -> tuple[str, str]:
        if not isinstance(url, str):
            raise AuthenticationError("Enter a valid Immich URL.", "invalid_input")
        if not isinstance(key, str) or not key.strip() or any(ord(c) < 32 or ord(c) == 127 for c in key):
            raise AuthenticationError("Enter an API key.", "invalid_input")
        with mutation_guard(self.state.mutation_lock):
            try:
                client = Immich(url, key, insecure)
                version = client.version()
            except UpstreamError:
                raise AuthenticationError("Immich is unavailable. Retry when the server is reachable.", "unavailable") from None
            except ToolError:
                raise AuthenticationError("Check the Immich URL and HTTPS certificate, then retry.", "invalid_input") from None
            except Exception:
                raise AuthenticationError("Cannot reach Immich. Check its URL, network and HTTPS certificate.", "unavailable") from None
            return self._install(client, self.key_identity(client), version)

    def _snapshot(self, cookie: str):
        with self.lock:
            if not cookie or not cookie.isascii() or not self.cookie or not secrets.compare_digest(cookie, self.cookie):
                return None
            return (self.epoch, self.state.client, self.user_id, self.mode, self.expires, self.checked)

    def _verify(self, snapshot) -> dict[str, Any]:
        epoch, client, user_id, mode, expires, checked = snapshot
        if time.monotonic() >= expires or client is None:
            raise AuthenticationError("Your Workbench connection expired. Reconnect; local work is saved.", "session_expired")
        if mode == "apiKey":
            identity = self.key_identity(client)
            actual_user = identity["userId"]
        else:
            try:
                actual_user = client.current_admin()
            except UpstreamError as exc:
                if exc.status in {401, 403}:
                    raise AuthenticationError("Immich admin access is no longer valid. Sign in again.", "invalid_login") from None
                raise AuthenticationError("Immich is unavailable. Check the server and reconnect; local work is saved.", "unavailable") from None
            except ToolError:
                raise AuthenticationError("Immich admin access is no longer available. Sign in again.", "invalid_login") from None
            except Exception:
                raise AuthenticationError("Immich could not be reached. Check the server and reconnect; local work is saved.", "unavailable") from None
            identity = getattr(client, "connection_metadata", {})
        if actual_user != user_id:
            raise AuthenticationError("Immich account changed. Reconnect to the original account.", "owner_mismatch")
        if time.monotonic() >= expires:
            raise AuthenticationError("Your Workbench connection expired. Reconnect; local work is saved.", "session_expired")
        return identity

    def authorization(self, cookie: str) -> tuple[bool, AuthenticationError | None]:
        snapshot = self._snapshot(cookie)
        if snapshot is None:
            if cookie:
                return False, AuthenticationError("This Workbench connection is no longer recognized. Reconnect; local work is saved.", "connection_ended", reconnect=True)
            return False, None
        epoch, client, user_id, mode, expires, checked = snapshot
        if time.monotonic() < expires and time.monotonic() - checked < ADMIN_CHECK_SECONDS:
            return True, None
        try:
            identity = self._verify(snapshot)
            failure = None
        except AuthenticationError as exc:
            failure = AuthenticationError(str(exc), exc.code, exc.missing, reconnect=True)
            identity = {}
        except Exception:
            failure = AuthenticationError("Immich could not be verified. Check the server and reconnect; local work is saved.", "unavailable", reconnect=True)
            identity = {}
        # A read request must not replace/clear credentials in an active Sync.
        if not self.state.mutation_lock.acquire(blocking=False):
            with self.lock:
                return (failure is None and epoch == self.epoch), None
        try:
            with self.lock:
                if epoch != self.epoch:
                    return False, AuthenticationError("This Workbench connection is no longer recognized. Reconnect; local work is saved.", "connection_ended", reconnect=True)
                if failure is not None:
                    self._clear()
                    return False, failure
                client.connection_metadata = identity
                self.checked = time.monotonic()
                return True, None
        finally:
            self.state.mutation_lock.release()

    def authorized(self, cookie: str) -> bool:
        return self.authorization(cookie)[0]

    def require_capabilities(self, cookie: str) -> None:
        snapshot = self._snapshot(cookie)
        if snapshot is None or time.monotonic() >= snapshot[4]:
            raise AuthenticationError("Reconnect to Immich; local work is saved.", "session_expired")
        if snapshot[3] == "apiKey":
            granted = set(snapshot[1].connection_metadata.get("grantedPermissions", []))
            missing = set(REQUIRED_PERMISSIONS) - granted if "all" not in granted else set()
            if missing:
                raise AuthenticationError("Reconnect with the required permissions; local work is saved.", "missing_permissions", missing)

    def sync(self, cookie: str, review_token: str) -> dict[str, Any]:
        if not self.state.sync_lock.acquire(blocking=False):
            raise SyncInProgress("Sync already in progress")
        try:
            with mutation_guard(self.state.mutation_lock):
                snapshot = self._snapshot(cookie)
                if snapshot is None:
                    raise AuthenticationError("Sign in required", "session_expired")
                try:
                    identity = self._verify(snapshot)
                except AuthenticationError as exc:
                    with self.lock:
                        if snapshot[0] == self.epoch:
                            self._clear()
                            raise AuthenticationError(str(exc), exc.code, exc.missing, reconnect=True) from None
                    raise AuthenticationError("The Workbench connection changed. Reconnect and review Pending again.", "connection_ended", reconnect=True) from None
                with self.lock:
                    if snapshot[0] != self.epoch:
                        raise AuthenticationError("Connection changed. Review Pending and retry explicitly.", "connection_changed")
                    snapshot[1].connection_metadata = identity
                    self.checked = time.monotonic()
                return self.state._sync(review_token)
        finally:
            self.state.sync_lock.release()

    def logout(self, cookie: str) -> None:
        with mutation_guard(self.state.mutation_lock), self.lock:
            if cookie and cookie.isascii() and self.cookie and secrets.compare_digest(cookie, self.cookie):
                self._clear()

    def _clear(self) -> None:
        # Call only under mutation_lock then auth.lock; persistent work is retained.
        self.epoch += 1
        self.cookie = ""
        self.user_id = ""
        self.expires = 0.0
        self.checked = 0.0
        self.state.disconnect()


STATE: AppState
AUTH: AdminSession


def canonical_origin(value: str) -> str:
    """Validate an HTTP origin, including its effective port and IPv6 authority."""
    parsed = urllib.parse.urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
            or any(character.isspace() for character in value) or "\\" in value
            or "?" in value or "#" in value):
        raise ValueError("public URL must be an http:// or https:// origin without a path, credentials, query, or fragment")
    hostname = parsed.hostname.lower()
    if ":" in hostname:
        ipaddress.IPv6Address(hostname)
        hostname = f"[{hostname}]"
    port = parsed.port
    if port == 0:
        raise ValueError("public URL port must be between 1 and 65535")
    if port is not None and port != (443 if parsed.scheme == "https" else 80):
        hostname += f":{port}"
    return f"{parsed.scheme}://{hostname}"


class RequestPolicy:
    """An explicit browser origin; forwarded scheme headers require a trusted peer."""

    def __init__(self, public_url: str = "", trusted_proxies: Iterable[str] = ()):
        self.origin = canonical_origin(public_url) if public_url else ""
        self.trusted_proxies = tuple(ipaddress.ip_network(value, strict=False) for value in trusted_proxies)
        if self.trusted_proxies and not self.origin:
            raise ValueError("trusted proxies require a public URL")

    def scheme(self, headers: Any, peer: str) -> str:
        address = ipaddress.ip_address(peer)
        if any(address in network for network in self.trusted_proxies):
            values = headers.get_all("X-Forwarded-Proto", [])
            if len(values) == 1 and values[0].lower() in {"http", "https"}:
                return values[0].lower()
        return "http"

    def allowed(self, headers: Any, peer: str) -> bool:
        hosts = headers.get_all("Host", [])
        if len(hosts) != 1:
            return False
        if any(character in hosts[0] for character in "/?#\\@"):
            return False
        scheme = self.scheme(headers, peer)
        try:
            origin = canonical_origin(f"{scheme}://{hosts[0]}")
        except ValueError:
            return False
        if self.origin:
            return origin == self.origin
        return urllib.parse.urlsplit(origin).hostname in {"127.0.0.1", "localhost", "::1"}

    def same_origin(self, headers: Any, peer: str) -> bool:
        origins = headers.get_all("Origin", [])
        if len(origins) != 1 or not self.allowed(headers, peer):
            return False
        expected = self.origin or f"{self.scheme(headers, peer)}://{headers.get('Host', '')}"
        return origins[0] == expected and headers.get("Sec-Fetch-Site", "same-origin") == "same-origin"


LOCAL_REQUEST_POLICY = RequestPolicy()


class Handler(BaseHTTPRequestHandler):
    server_version = "ImmichPeopleWorkbench/1"

    def log_message(self, fmt: str, *args: Any) -> None:
        if args and isinstance(args[0], str):
            args = (args[0].split("?", 1)[0], *args[1:])
        super().log_message(fmt, *args)

    def allowed(self) -> bool:
        return self.policy.allowed(self.headers, self.client_address[0])

    @property
    def policy(self) -> RequestPolicy:
        return getattr(self.server, "request_policy", LOCAL_REQUEST_POLICY)

    def session_cookie(self) -> str:
        try:
            cookies = http.cookies.SimpleCookie()
            cookies.load(self.headers.get("Cookie", ""))
            item = cookies.get(SESSION_COOKIE)
            return item.value if item else ""
        except http.cookies.CookieError:
            return ""

    def same_origin(self) -> bool:
        return self.policy.same_origin(self.headers, self.client_address[0])

    def redirect(self, location: str) -> None:
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def login_redirect(self, path: str, failure: AuthenticationError | None = None) -> None:
        routes = {"/", *PAGE_ROUTES}
        safe_path = path if path in routes else "/"
        params = {"next": safe_path}
        if failure and failure.reconnect and failure.code in CONNECTION_REASONS:
            params["reason"] = failure.code
            missing = sorted(set(failure.missing) & set(REQUIRED_PERMISSIONS))[:len(REQUIRED_PERMISSIONS)]
            if failure.code == "missing_permissions" and missing:
                params["missing"] = ",".join(missing)
        self.redirect("/login?" + urllib.parse.urlencode(params, safe="/"))

    def send_json(self, value: Any, status: int = 200, cookie: str | None = None) -> None:
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if cookie is not None:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > MAX_BODY:
            raise ToolError("Invalid request size")
        try:
            value = json.loads(self.rfile.read(length))
        except json.JSONDecodeError as exc:
            raise ToolError("Invalid JSON") from exc
        if not isinstance(value, dict):
            raise ToolError("JSON object required")
        return value

    def static(self, name: str) -> None:
        target = (WEB / name).resolve()
        try:
            target.relative_to(WEB.resolve())
        except ValueError:
            self.send_error(404)
            return
        if not target.is_file():
            self.send_error(404)
            return
        body = target.read_bytes()
        kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", kind + ("; charset=utf-8" if kind.startswith("text/") else ""))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; object-src 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def image(self, body: bytes, kind: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "private, max-age=300")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path, query = parsed.path, urllib.parse.parse_qs(parsed.query)
        # Liveness only: no login, database access, or Immich request.
        if path == "/healthz":
            self.send_json({"ok": True})
            return
        if not self.allowed():
            self.send_error(403)
            return
        try:
            if path.rstrip("/") == "/merge":
                self.redirect("/naming/clusters")
                return
            if path == "/login":
                authorized, failure = AUTH.authorization(self.session_cookie())
                if authorized:
                    self.redirect("/")
                elif failure and failure.reconnect and query.get("reason", [""])[0] not in CONNECTION_REASONS:
                    requested = query.get("next", ["/"])[0]
                    self.login_redirect(requested, failure)
                else:
                    self.static("login.html")
                return
            if path in {"/styles.css", "/login-key.css", "/login.js"}:
                self.static(path[1:])
                return
            authorized, failure = AUTH.authorization(self.session_cookie())
            if not authorized:
                if path == "/" or path.rstrip("/") in PAGE_ROUTES:
                    self.login_redirect(path.rstrip("/") or "/", failure)
                else:
                    response = failure.response() if failure else {"error": "Sign in required"}
                    self.send_json(response, 401)
                return
            if path == "/" or path.rstrip("/") in PAGE_ROUTES:
                self.static("index.html")
            elif path in {"/app.js", "/navigation.js", "/merge.js", "/face-review.js", "/naming.js", "/group-photos.js", "/investigate.js", "/pending.js", "/browser-storage.js", "/asset-preview.js"}:
                self.static(path[1:])
            elif path == "/api/state":
                self.send_json(STATE.summary())
            elif path == "/api/people":
                kind = query.get("kind", ["unnamed"])[0]
                order = query.get("sort", [STATE.store.setting("sort", "most")])[0]
                STATE.store.set_setting("sort", order)
                if "page" in query or "size" in query or "q" in query or "hideCompleted" in query or "nameFilter" in query:
                    try:
                        page = int(query.get("page", ["1"])[0])
                        size = int(query.get("size", ["24"])[0])
                    except ValueError as exc:
                        raise ToolError("Invalid people page") from exc
                    self.send_json(STATE.people_page(kind, order, page, size, query.get("q", [""])[0],
                                                     query.get("hideCompleted", ["0"])[0] == "1",
                                                     query.get("nameFilter", ["all"])[0]))
                else:
                    self.send_json({"people": STATE.list_people(kind, order)})
            elif path == "/api/names":
                self.send_json({"people": STATE.named_people()})
            elif path == "/api/group-photos":
                self.send_json(STATE.group_photo_state())
            elif path == "/api/group-photos/progress":
                self.send_json(STATE.group_scan_progress())
            elif path == "/api/pending":
                if "page" in query or "size" in query:
                    try:
                        page = int(query.get("page", ["1"])[0])
                        size = int(query.get("size", ["24"])[0])
                    except ValueError as exc:
                        raise ToolError("Invalid pending page") from exc
                    self.send_json(STATE.pending_page(page, size))
                else:
                    self.send_json(STATE.pending_data())
            elif path.startswith("/api/samples/"):
                person_id = path.rsplit("/", 1)[-1]
                if not UUID_RE.fullmatch(person_id):
                    raise ToolError("Invalid person ID")
                self.send_json({"samples": STATE.samples(person_id)})
            elif path.startswith("/api/files/"):
                person_id = path.rsplit("/", 1)[-1]
                if not UUID_RE.fullmatch(person_id):
                    raise ToolError("Invalid person ID")
                self.send_json({"files": STATE.files(person_id)})
            elif path.startswith("/api/person-faces/"):
                person_id = path.rsplit("/", 1)[-1]
                if not UUID_RE.fullmatch(person_id):
                    raise ToolError("Invalid person ID")
                try:
                    page = int(query.get("page", ["1"])[0])
                    size = int(query.get("size", ["40"])[0])
                except ValueError as exc:
                    raise ToolError("Invalid face page") from exc
                self.send_json(STATE.faces_for_person(person_id, page, size))
            elif path == "/api/similarity-graph":
                try:
                    neighbors = int(query.get("neighbors", ["8"])[0])
                except ValueError as exc:
                    raise ToolError("Invalid similarity-map neighbor count") from exc
                self.send_json(STATE.similarity_graph(max(3, min(neighbors, 15))))
            elif path.startswith("/api/similar/"):
                person_id = path.rsplit("/", 1)[-1]
                if not UUID_RE.fullmatch(person_id):
                    raise ToolError("Invalid person ID")
                try:
                    limit = int(query.get("limit", ["12"])[0])
                except ValueError as exc:
                    raise ToolError("Invalid similar-person limit") from exc
                self.send_json({"people": STATE.similar_people(person_id, max(1, min(limit, 100)))})
            elif path.startswith("/media/person/"):
                person_id = path.rsplit("/", 1)[-1]
                if not UUID_RE.fullmatch(person_id):
                    raise ToolError("Invalid person ID")
                self.image(*STATE.require_client().request("GET", f"/people/{person_id}/thumbnail"))
            elif path.startswith("/media/asset/"):
                asset_id = path.rsplit("/", 1)[-1]
                if not UUID_RE.fullmatch(asset_id):
                    raise ToolError("Invalid asset ID")
                self.image(*STATE.require_client().request("GET", f"/assets/{asset_id}/thumbnail?size=preview"))
            else:
                self.send_error(404)
        except SyncInProgress as exc:
            self.send_json({"error": str(exc)}, 409)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 502)

    def do_POST(self) -> None:
        if not self.allowed():
            self.send_error(403)
            return
        path = urllib.parse.urlparse(self.path).path
        if not self.same_origin():
            self.send_json({"error": "Invalid request origin"}, 403)
            return
        if path not in {"/api/login", "/api/connect-key"}:
            authorized, failure = AUTH.authorization(self.session_cookie())
            if not authorized:
                self.send_json(failure.response() if failure else {"error": "Sign in required"}, 401)
                return
        try:
            guard = nullcontext() if path in {"/api/sync", "/api/face-review/scan-next", "/api/group-photos/retry"} else mutation_guard(STATE.mutation_lock)
            with guard:
                payload = self.body()
                if path not in {"/api/login", "/api/connect-key", "/api/logout", "/api/sync"}:
                    AUTH.require_capabilities(self.session_cookie())
                if path == "/api/login":
                    try:
                        version, session_cookie = AUTH.login(str(payload.get("url", "")), str(payload.get("email", "")), str(payload.get("password", "")), bool(payload.get("insecureTls")))
                    except Exception as exc:
                        message = str(exc) if str(exc).startswith(("Only Immich admins", "This local state", "Immich email")) else "Immich login failed"
                        self.send_json({"error": message}, 403)
                        return
                    threading.Thread(target=STATE.load, daemon=True).start()
                    secure = "; Secure" if self.policy.scheme(self.headers, self.client_address[0]) == "https" else ""
                    cookie = f"{SESSION_COOKIE}={session_cookie}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_SECONDS}{secure}"
                    self.send_json({"ok": True, "version": version}, 200, cookie)
                elif path == "/api/connect-key":
                    version, session_cookie = AUTH.connect_key(payload.get("url", ""), payload.get("apiKey", ""), bool(payload.get("insecureTls")))
                    threading.Thread(target=STATE.load, daemon=True).start()
                    secure = "; Secure" if self.policy.scheme(self.headers, self.client_address[0]) == "https" else ""
                    cookie = f"{SESSION_COOKIE}={session_cookie}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_SECONDS}{secure}"
                    self.send_json({"ok": True, "version": version}, 200, cookie)
                elif path == "/api/logout":
                    AUTH.logout(self.session_cookie())
                    self.send_json({"ok": True}, cookie=f"{SESSION_COOKIE}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0")
                elif path == "/api/reload":
                    STATE.set_status("loading", "Reloading people from Immich")
                    threading.Thread(target=STATE.load, daemon=True).start()
                    self.send_json({"ok": True}, 202)
                elif path == "/api/reset":
                    if payload.get("confirmation") != "DISCARD":
                        raise ToolError("Discard confirmation missing")
                    discarded = STATE.discard_pending()
                    STATE.set_status("loading", "Discarded local changes; reloading people from Immich")
                    threading.Thread(target=STATE.load, daemon=True).start()
                    self.send_json({"ok": True, "discarded": discarded}, 202)
                elif path == "/api/skip":
                    person_id = str(payload.get("personId", ""))
                    if not UUID_RE.fullmatch(person_id):
                        raise ToolError("Invalid person ID")
                    self.send_json({"skipCount": STATE.store.skip(person_id)})
                elif path == "/api/queue":
                    STATE.queue(payload)
                    self.send_json({"ok": True})
                elif path == "/api/queue/group":
                    self.send_json({"ok": True, **STATE.queue_merge_group(payload)})
                elif path == "/api/group-photos/settings":
                    self.send_json(STATE.set_group_settings(payload))
                elif path == "/api/group-photos/draft":
                    STATE.save_group_draft(payload)
                    self.send_json({"ok": True})
                elif path == "/api/group-photos/advance":
                    self.send_json(STATE.advance_group_photo(payload))
                elif path == "/api/group-photos/deferred":
                    self.send_json(STATE.start_group_deferred())
                elif path == "/api/group-photos/retry":
                    STATE.start_group_scan(retry=True)
                    self.send_json({"ok": True})
                elif path == "/api/pending/include":
                    STATE.store.set_included(str(payload.get("personId", "")), bool(payload.get("included")))
                    self.send_json({"ok": True})
                elif path == "/api/pending/return":
                    person_id = str(payload.get("personId", ""))
                    with STATE.group_lock:
                        STATE.store.remove_pending(person_id)
                        STATE.store.skip(person_id)
                        STATE.invalidate_group_scan()
                    self.send_json({"ok": True})
                elif path == "/api/face-detach/queue":
                    STATE.queue_face_detach(payload)
                    self.send_json({"ok": True})
                elif path == "/api/face-review/reviewed":
                    self.send_json({"ok": True, **STATE.set_face_reviewed(payload)})
                elif path == "/api/face-review/reviewed/bulk":
                    self.send_json({"ok": True, **STATE.set_faces_reviewed(payload)})
                elif path == "/api/face-review/draft":
                    self.send_json({"ok": True, **STATE.set_face_review_drafts(payload)})
                elif path == "/api/face-review/submit":
                    self.send_json({"ok": True, **STATE.submit_face_review(payload)})
                elif path == "/api/face-review/reopen":
                    self.send_json({"ok": True, **STATE.set_review_reopened(payload)})
                elif path == "/api/face-review/scan-next":
                    self.send_json(STATE.scan_face_review_page(str(payload.get("activePersonId") or ""),
                                                               payload.get("retryFailed") is True))
                elif path == "/api/face-detach/include":
                    face_id = str(payload.get("faceId", ""))
                    if not UUID_RE.fullmatch(face_id):
                        raise ToolError("Invalid face ID")
                    STATE.store.set_face_detach_included(face_id, bool(payload.get("included")))
                    self.send_json({"ok": True})
                elif path == "/api/face-detach/return":
                    face_id = str(payload.get("faceId", ""))
                    if not UUID_RE.fullmatch(face_id):
                        raise ToolError("Invalid face ID")
                    STATE.store.remove_face_detach(face_id)
                    self.send_json({"ok": True})
                elif path == "/api/investigate/add":
                    person_id = str(payload.get("personId", ""))
                    if not UUID_RE.fullmatch(person_id):
                        raise ToolError("Invalid person ID")
                    with STATE.lock:
                        person = STATE.people.get(person_id)
                    if not person or person.get("isHidden"):
                        raise ToolError("Person is not available for investigation")
                    STATE.store.mark_investigate(person_id)
                    self.send_json({"ok": True})
                elif path == "/api/investigate/remove":
                    person_id = str(payload.get("personId", ""))
                    if not UUID_RE.fullmatch(person_id):
                        raise ToolError("Invalid person ID")
                    STATE.store.unmark_investigate(person_id)
                    self.send_json({"ok": True})
                elif path == "/api/investigate/clear":
                    if payload.get("confirmation") != "RETURN_ALL":
                        raise ToolError("Return-all confirmation missing")
                    self.send_json({"ok": True, "returned": STATE.store.clear_investigate()})
                elif path == "/api/sync":
                    if payload.get("confirmation") != "SYNC":
                        raise ToolError("Sync confirmation missing")
                    review_token = payload.get("reviewToken")
                    self.send_json(AUTH.sync(self.session_cookie(), review_token if isinstance(review_token, str) else ""))
                else:
                    self.send_error(404)
        except SyncInProgress as exc:
            self.send_json({"error": str(exc)}, 409)
        except AuthenticationError as exc:
            self.send_json(exc.response(), 403)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 400)


class WorkbenchServer(ThreadingHTTPServer):
    """Stop admission on shutdown and keep admitted handlers alive to a deadline."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.handlers = threading.Condition()
        self.active_handlers = 0
        self.draining = False
        super().__init__(*args, **kwargs)

    def process_request(self, request: Any, client_address: Any) -> None:
        with self.handlers:
            if self.draining:
                self.shutdown_request(request)
                return
            self.active_handlers += 1
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.handler_finished()
            raise

    def handler_finished(self) -> None:
        with self.handlers:
            self.active_handlers -= 1
            self.handlers.notify_all()

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.handler_finished()

    def stop_admission(self) -> None:
        with self.handlers:
            self.draining = True

    def drain(self, deadline: float) -> bool:
        with self.handlers:
            while self.active_handlers:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self.handlers.wait(remaining)
        return True


def main() -> int:
    global STATE, AUTH
    parser = argparse.ArgumentParser(description="Keyboard-first local Immich People Workbench")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--shutdown-timeout", type=float, default=110,
                        help="Seconds to drain admitted requests on shutdown (default: 110)")
    parser.add_argument("--public-url", default=os.environ.get("PEOPLE_WORKBENCH_PUBLIC_URL", ""),
                        help="Browser origin required for remote access (or PEOPLE_WORKBENCH_PUBLIC_URL)")
    parser.add_argument("--trusted-proxy", action="append", default=None,
                        help="Trusted reverse-proxy IP/CIDR; repeat as needed (or PEOPLE_WORKBENCH_TRUSTED_PROXIES, comma-separated)")
    args = parser.parse_args()
    if not math.isfinite(args.shutdown_timeout) or args.shutdown_timeout <= 0:
        parser.error("--shutdown-timeout must be a finite positive number")
    if args.host not in {"127.0.0.1", "localhost", "::1"} and not args.public_url:
        parser.error("non-localhost binding requires --public-url or PEOPLE_WORKBENCH_PUBLIC_URL")
    proxies = args.trusted_proxy if args.trusted_proxy is not None else [
        value.strip() for value in os.environ.get("PEOPLE_WORKBENCH_TRUSTED_PROXIES", "").split(",") if value.strip()
    ]
    try:
        policy = RequestPolicy(args.public_url, proxies)
    except ValueError as exc:
        parser.error(str(exc))
    state_dir = args.state_dir.expanduser().resolve()
    STATE = AppState(Store(state_dir / "state.sqlite3"), state_dir / "reports")
    AUTH = AdminSession(STATE)
    server = WorkbenchServer((args.host, args.port), Handler)
    server.request_policy = policy
    url = policy.origin + "/" if policy.origin else f"http://127.0.0.1:{server.server_port}/"
    stopping = threading.Event()
    deadline = None

    def force_exit() -> None:
        print("Shutdown deadline expired; reconcile Pending and Immich before retrying Sync.", file=sys.stderr, flush=True)
        # Executor workers can outlive their daemon handler or background loader.
        # Interpreter cleanup joins them, so normal exit cannot enforce a bound.
        os._exit(1)

    def enforce_deadline() -> None:
        time.sleep(max(0, deadline - time.monotonic()))
        force_exit()

    def stop(_signum: int, _frame: Any) -> None:
        nonlocal deadline
        if not stopping.is_set():
            stopping.set()
            deadline = time.monotonic() + args.shutdown_timeout
            server.stop_admission()
            threading.Thread(target=enforce_deadline, daemon=True).start()
            threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(f"Immich People Workbench: {url}")
    print(f"Local queue: {state_dir}")
    print("Immich login tokens remain in memory and are never written to disk.")
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.stop_admission()
        server.server_close()
    if not server.drain(deadline if deadline is not None else time.monotonic() + args.shutdown_timeout):
        force_exit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
