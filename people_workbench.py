#!/usr/bin/env python3
# Copyright (C) 2026 Serkan Bekdemir
# SPDX-License-Identifier: AGPL-3.0-only
"""Keyboard-first local people inbox for Immich.

The API key is kept in memory. Names, skips, and pending operations are stored
locally in SQLite so an unfinished review survives restarts. Immich is only
changed by an explicit sync from the review screen.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
import mimetypes
import os
import random
import re
import secrets
import sqlite3
import ssl
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
DEFAULT_STATE_DIR = ROOT / ".people-workbench"
MAX_BODY = 1_000_000
MAX_IMAGE = 20_000_000
UUID_RE = re.compile(r"^[0-9a-fA-F-]{20,50}$")
PAGE_ROUTES = frozenset({"/unnamed", "/merge", "/investigate", "/faces", "/pending", "/named", "/ignored"})


class ToolError(RuntimeError):
    pass


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


class Immich:
    def __init__(self, url: str, api_key: str, insecure: bool = False):
        base = url.strip().rstrip("/")
        if not urllib.parse.urlparse(base).scheme:
            raise ToolError("Immich URL must start with http:// or https://")
        if not api_key.strip():
            raise ToolError("API key is required")
        self.base = base if base.endswith("/api") else base + "/api"
        self.api_key = api_key.strip()
        self.context = ssl._create_unverified_context() if insecure else None

    def request(self, method: str, path: str, payload: Any = None) -> tuple[bytes, str]:
        data = json.dumps(payload).encode() if payload is not None else None
        headers = {"x-api-key": self.api_key, "Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=60, context=self.context) as response:
                body = response.read(MAX_IMAGE + 1)
                if len(body) > MAX_IMAGE:
                    raise ToolError("Immich response exceeded the safety limit")
                return body, response.headers.get_content_type()
        except urllib.error.HTTPError as exc:
            detail = exc.read(1000).decode(errors="replace")
            raise ToolError(f"Immich API {exc.code} for {path}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise ToolError(f"Cannot reach Immich: {exc.reason}") from exc

    def json(self, method: str, path: str, payload: Any = None) -> Any:
        body, _ = self.request(method, path, payload)
        try:
            return json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ToolError(f"Immich returned invalid JSON for {path}") from exc

    def version(self) -> str:
        data = self.json("GET", "/server/version")
        if isinstance(data, dict):
            if isinstance(data.get("version"), str):
                return data["version"]
            values = [data.get(field) for field in ("major", "minor", "patch")]
            if all(isinstance(value, int) for value in values):
                return ".".join(str(value) for value in values)
        return "unknown"

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
        self.json("POST", f"/people/{target_person_id}/merge", {"ids": [source_person_id]})

    def person_assets_page(self, person_id: str, page: int = 1, size: int = 50) -> tuple[list[dict[str, Any]], bool, int | None]:
        payload = {"personIds": [person_id], "page": max(1, page), "size": max(1, min(size, 100))}
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


class Store:
    def __init__(self, path: Path):
        self.path = path
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
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def skips(self) -> dict[str, int]:
        with self.connect() as db:
            return {row["person_id"]: row["skip_count"] for row in db.execute("SELECT person_id, skip_count FROM progress")}

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

    def clear_progress(self, person_id: str) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM progress WHERE person_id=?", (person_id,))

    def investigate_ids(self) -> set[str]:
        with self.connect() as db:
            return {row["person_id"] for row in db.execute("SELECT person_id FROM investigate")}

    def mark_investigate(self, person_id: str) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO investigate(person_id, created_at) VALUES(?,?) ON CONFLICT(person_id) DO NOTHING",
                (person_id, now()),
            )

    def unmark_investigate(self, person_id: str) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM investigate WHERE person_id=?", (person_id,))

    def clear_investigate(self) -> int:
        with self.connect() as db:
            cursor = db.execute("DELETE FROM investigate")
            return max(0, cursor.rowcount)

    def upsert_pending(self, item: dict[str, Any]) -> None:
        self.upsert_pending_many([item])

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

    def pending(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM pending ORDER BY created_at, person_id").fetchall()
        return [dict(row) for row in rows]

    def pending_ids(self) -> set[str]:
        return {item["person_id"] for item in self.pending()}

    def set_included(self, person_id: str, included: bool) -> None:
        with self.connect() as db:
            db.execute("UPDATE pending SET included=?, updated_at=? WHERE person_id=?", (int(included), now(), person_id))

    def remove_pending(self, person_id: str) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM pending WHERE person_id=?", (person_id,))

    def clear_pending(self) -> int:
        with self.connect() as db:
            people = db.execute("DELETE FROM pending").rowcount
            faces = db.execute("DELETE FROM face_detach_pending").rowcount
            return max(0, people) + max(0, faces)

    def set_error(self, person_id: str, error: str) -> None:
        with self.connect() as db:
            db.execute("UPDATE pending SET last_error=?, updated_at=? WHERE person_id=?", (error[:2000], now(), person_id))

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

    def set_face_detach_included(self, face_id: str, included: bool) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE face_detach_pending SET included=?, updated_at=? WHERE face_id=?",
                (int(included), now(), face_id),
            )

    def set_face_detach_target(self, face_id: str, target_person_id: str) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE face_detach_pending SET target_person_id=?, updated_at=? WHERE face_id=?",
                (target_person_id, now(), face_id),
            )

    def set_face_detach_error(self, face_id: str, error: str) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE face_detach_pending SET last_error=?, updated_at=? WHERE face_id=?",
                (error[:2000], now(), face_id),
            )

    def remove_face_detach(self, face_id: str) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM face_detach_pending WHERE face_id=?", (face_id,))

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

    def remove_face_review(self, face_id: str) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM face_reviews WHERE face_id=?", (face_id,))

    def reviewed_face_ids(self, person_id: str) -> set[str]:
        with self.connect() as db:
            rows = db.execute("SELECT face_id FROM face_reviews WHERE person_id=?", (person_id,)).fetchall()
        return {row["face_id"] for row in rows}

    def face_review_counts(self) -> dict[str, int]:
        with self.connect() as db:
            rows = db.execute("SELECT person_id, COUNT(*) AS count FROM face_reviews GROUP BY person_id").fetchall()
        return {row["person_id"]: int(row["count"]) for row in rows}

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
        self.client: Immich | None = None
        self.version = ""
        self.people: dict[str, dict[str, Any]] = {}
        self.phase = "idle"
        self.message = "Connect to Immich"
        self.progress = {"current": 0, "total": None}
        self.sample_cache: dict[str, list[dict[str, Any]]] = {}
        self.similarity_cache: dict[int, tuple[tuple[str, ...], dict[str, Any]]] = {}

    def set_status(self, phase: str, message: str, current: int = 0, total: int | None = None) -> None:
        with self.lock:
            self.phase, self.message = phase, message
            self.progress = {"current": current, "total": total}

    def require_client(self) -> Immich:
        with self.lock:
            if self.client is None:
                raise ToolError("Connect to Immich first")
            return self.client

    def connect(self, url: str, key: str, insecure: bool) -> str:
        client = Immich(url, key, insecure)
        version = client.version()
        with self.lock:
            self.client, self.version = client, version
            self.people, self.sample_cache = {}, {}
        self.set_status("connected", f"Connected to Immich {version}")
        return version

    def load(self) -> None:
        try:
            client = self.require_client()
            self.set_status("loading", "Loading people from Immich")
            people = client.all_people()
            self.set_status("statistics", "Loading person photo counts", 0, len(people))
            counts: dict[str, int | None] = {}
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                futures = {pool.submit(client.statistics, person["id"]): person["id"] for person in people}
                for index, future in enumerate(concurrent.futures.as_completed(futures), 1):
                    try:
                        counts[futures[future]] = future.result()
                    except Exception:
                        counts[futures[future]] = None
                    self.set_status("statistics", "Loading person photo counts", index, len(people))
            mapped = {}
            for person in people:
                item = dict(person)
                item["name"] = normalized_name(item.get("name"))
                item["assetCount"] = counts.get(item["id"])
                mapped[item["id"]] = item
            with self.lock:
                self.people = mapped
                self.similarity_cache = {}
            self.set_status("ready", f"Loaded {len(mapped)} people", len(mapped), len(mapped))
        except Exception as exc:
            self.set_status("error", str(exc))

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
            people = [p for p in people if not p.get("isHidden") and p["id"] not in pending_ids and p["id"] not in pending_merge_targets]
        else:
            raise ToolError("Unknown people list")
        for person in people:
            person["skipCount"] = skips.get(person["id"], 0)
        if kind == "review":
            review_counts = self.store.face_review_counts()
            for person in people:
                total = max(0, int(person.get("assetCount") or 0))
                reviewed = min(total, review_counts.get(person["id"], 0)) if total else 0
                person["reviewedCount"] = reviewed
                person["reviewTotal"] = total
                person["reviewed"] = total > 0 and reviewed >= total
        people = sort_people(people, order)
        if kind == "unnamed":
            people.sort(key=lambda p: p["skipCount"])
        return people

    def people_page(self, kind: str, order: str, page: int, size: int, query: str = "") -> dict[str, Any]:
        people = self.list_people(kind, order)
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

    def pending_public(self) -> list[dict[str, Any]]:
        rows = self.store.pending()
        with self.lock:
            people = self.people.copy()
        result = []
        for row in rows:
            person = people.get(row["person_id"], {})
            target = people.get(row.get("target_person_id"), {}) if row.get("target_person_id") else {}
            result.append({
                "kind": "person",
                "personId": row["person_id"], "operation": row["operation"], "name": row["name"],
                "targetPersonId": row.get("target_person_id"), "targetName": row["target_name"],
                "featureAssetId": row.get("feature_asset_id"), "included": bool(row["included"]),
                "lastError": row["last_error"], "assetCount": person.get("assetCount"),
                "currentName": person.get("name", ""), "targetAssetCount": target.get("assetCount"),
            })
        for row in self.store.face_detaches():
            person = people.get(row["source_person_id"], {})
            result.append({
                "kind": "face", "operation": "detach-face", "faceId": row["face_id"],
                "personId": row["source_person_id"], "assetId": row["asset_id"],
                "fileName": row["file_name"], "included": bool(row["included"]),
                "lastError": row["last_error"], "currentName": person.get("name", ""),
                "assetCount": person.get("assetCount"),
            })
        return result

    def pending_page(self, page: int, size: int) -> dict[str, Any]:
        return paginate(self.pending_public(), page, size, "pending")

    def faces_for_person(self, person_id: str, page: int = 1, size: int = 40) -> dict[str, Any]:
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
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(6, len(assets))) as pool:
                for rows in pool.map(collect, assets):
                    faces.extend(rows)
        queued = {row["face_id"] for row in self.store.face_detaches()}
        reviewed = self.store.reviewed_face_ids(person_id)
        for face in faces:
            face["queued"] = face["faceId"] in queued
            face["reviewed"] = face["faceId"] in reviewed or face["queued"]
        reviewed_count = min(total, len(reviewed | queued)) if total else 0
        pages = max(1, (total + size - 1) // size)
        return {
            "faces": faces, "page": page, "size": size, "pages": pages,
            "hasPrevious": page > 1, "hasNext": has_more, "hasMore": has_more,
            "from": (page - 1) * size + 1 if total else 0,
            "to": min(page * size, total), "total": total, "totalAssets": total,
            "reviewedCount": reviewed_count, "personReviewed": total > 0 and reviewed_count >= total,
        }

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
        total = max(0, int(person.get("assetCount") or 0))
        count = min(total, len(self.store.reviewed_face_ids(person_id))) if total else 0
        return {"reviewedCount": count, "reviewTotal": total, "personReviewed": total > 0 and count >= total}

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
        self.store.set_face_reviewed(face_id, person_id, True)

    def discard_pending(self) -> int:
        discarded = self.store.clear_pending()
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

    def sync(self) -> dict[str, Any]:
        client = self.require_client()
        all_pending = self.store.pending()
        all_face_pending = self.store.face_detaches()
        pending_by_id = {row["person_id"]: row for row in all_pending}
        rows = [row for row in all_pending if row["included"]]
        face_rows = [row for row in all_face_pending if row["included"]]
        priority = {"rename": 0, "unhide": 0, "hide": 0, "merge": 1}
        rows.sort(key=lambda row: (priority.get(row["operation"], 9), row["created_at"], row["person_id"]))
        results = []
        failed_ids: set[str] = set()
        for row in face_rows:
            face_id = row["face_id"]
            try:
                target_id = row.get("target_person_id")
                if not target_id:
                    target_id = client.create_person()["id"]
                    self.store.set_face_detach_target(face_id, target_id)
                client.reassign_face(target_id, face_id)
                self.store.remove_face_detach(face_id)
                self.store.remove_face_review(face_id)
                results.append({
                    "faceId": face_id, "personId": row["source_person_id"],
                    "operation": "detach-face", "result": "synced", "targetPersonId": target_id,
                })
            except Exception as exc:
                self.store.set_face_detach_error(face_id, str(exc))
                results.append({
                    "faceId": face_id, "personId": row["source_person_id"],
                    "operation": "detach-face", "result": "failed", "error": str(exc),
                })
        for row in rows:
            person_id = row["person_id"]
            try:
                operation = row["operation"]
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
                    client.merge(target_id, person_id)
                    if row.get("feature_asset_id"):
                        client.update_person(target_id, {"featureFaceAssetId": row["feature_asset_id"]})
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


STATE: AppState


class Handler(BaseHTTPRequestHandler):
    server_version = "ImmichPeopleWorkbench/1"

    def log_message(self, fmt: str, *args: Any) -> None:
        if args and isinstance(args[0], str):
            args = (args[0].split("?", 1)[0], *args[1:])
        super().log_message(fmt, *args)

    def allowed(self) -> bool:
        return self.headers.get("Host", "").split(":", 1)[0].strip("[]").lower() in {"127.0.0.1", "localhost", "::1"}

    def send_json(self, value: Any, status: int = 200) -> None:
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
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
        if not self.allowed():
            self.send_error(403)
            return
        parsed = urllib.parse.urlparse(self.path)
        path, query = parsed.path, urllib.parse.parse_qs(parsed.query)
        try:
            if path == "/" or path.rstrip("/") in PAGE_ROUTES:
                self.static("index.html")
            elif path in {"/app.js", "/merge.js", "/face-review.js", "/naming.js", "/investigate.js", "/pending.js", "/styles.css"}:
                self.static(path[1:])
            elif path == "/api/state":
                self.send_json(STATE.summary())
            elif path == "/api/people":
                kind = query.get("kind", ["unnamed"])[0]
                order = query.get("sort", [STATE.store.setting("sort", "most")])[0]
                STATE.store.set_setting("sort", order)
                if "page" in query or "size" in query or "q" in query:
                    try:
                        page = int(query.get("page", ["1"])[0])
                        size = int(query.get("size", ["24"])[0])
                    except ValueError as exc:
                        raise ToolError("Invalid people page") from exc
                    self.send_json(STATE.people_page(kind, order, page, size, query.get("q", [""])[0]))
                else:
                    self.send_json({"people": STATE.list_people(kind, order)})
            elif path == "/api/names":
                self.send_json({"people": STATE.named_people()})
            elif path == "/api/pending":
                if "page" in query or "size" in query:
                    try:
                        page = int(query.get("page", ["1"])[0])
                        size = int(query.get("size", ["24"])[0])
                    except ValueError as exc:
                        raise ToolError("Invalid pending page") from exc
                    self.send_json(STATE.pending_page(page, size))
                else:
                    self.send_json({"pending": STATE.pending_public()})
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
        except Exception as exc:
            self.send_json({"error": str(exc)}, 502)

    def do_POST(self) -> None:
        if not self.allowed():
            self.send_error(403)
            return
        path = urllib.parse.urlparse(self.path).path
        try:
            payload = self.body()
            if path == "/api/connect":
                version = STATE.connect(str(payload.get("url", "")), str(payload.get("apiKey", "")), bool(payload.get("insecureTls")))
                threading.Thread(target=STATE.load, daemon=True).start()
                self.send_json({"ok": True, "version": version}, 202)
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
            elif path == "/api/pending/include":
                STATE.store.set_included(str(payload.get("personId", "")), bool(payload.get("included")))
                self.send_json({"ok": True})
            elif path == "/api/pending/return":
                person_id = str(payload.get("personId", ""))
                STATE.store.remove_pending(person_id)
                STATE.store.skip(person_id)
                self.send_json({"ok": True})
            elif path == "/api/face-detach/queue":
                STATE.queue_face_detach(payload)
                self.send_json({"ok": True})
            elif path == "/api/face-review/reviewed":
                self.send_json({"ok": True, **STATE.set_face_reviewed(payload)})
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
                self.send_json(STATE.sync())
            else:
                self.send_error(404)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 400)


def main() -> int:
    global STATE
    parser = argparse.ArgumentParser(description="Keyboard-first local Immich People Workbench")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("host must be localhost")
    state_dir = args.state_dir.expanduser().resolve()
    STATE = AppState(Store(state_dir / "state.sqlite3"), state_dir / "reports")
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"Immich People Workbench: {url}")
    print(f"Local queue: {state_dir}")
    print("The API key remains in memory and is never written to disk.")
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
