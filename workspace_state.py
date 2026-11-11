# Copyright (C) 2026 Serkan Bekdemir
# SPDX-License-Identifier: AGPL-3.0-only
"""Local offline-transfer state. No network, application import, or default paths.

Functions taking a connection participate in the caller's transaction. In
particular, reserving an export and accepting an import must be called inside
Store.workflow_transaction(). See docs/development/offline-formats.md.
"""
from __future__ import annotations

from dataclasses import dataclass
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Mapping, Sequence, TypedDict
import uuid

FORMAT_VERSION = 1
MAX_INTEGER = 2 ** 53 - 1
RESULT_BYTES = 16 * 1024 ** 2 - 122
PREFERENCES = ("sort", "group_min_faces", "group_archived", "group_order", "group_pass", "group_current")
GROUP_SETTINGS = ("group_min_faces", "group_archived", "group_pass")
NAMESPACE = uuid.UUID("f5a15f80-2cbd-4e83-b038-ad66867eafc0")


class WorkspaceError(RuntimeError):
    def __init__(self, message: str, code: str = "invalid_results"):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Limits:
    logical_bytes: int = 64 * 1024 ** 2
    snapshot_count: int = 128
    snapshot_bytes: int = 1024 ** 3
    export_count: int = 256
    ready_archive_count: int = 32
    archive_bytes: int = 2 * 1024 ** 4
    registry_bytes: int = 1024 ** 3
    registry_records: int = 1_000_000
    operations: int = 50_000
    references: int = 200_000


DEFAULT_LIMITS = Limits()


class Owner(TypedDict):
    url: str
    userId: str


class LogicalRow(TypedDict):
    entity: str
    key: list[str]
    revision: int
    deleted: bool
    value: dict[str, Any] | None


class StateCapture(TypedDict):
    format: str
    formatVersion: int
    instanceId: str
    owner: Owner | None
    stateRevision: int
    groupRevision: int
    rows: list[LogicalRow]


class Operation(TypedDict):
    id: str
    type: str
    key: list[str]
    value: dict[str, Any] | None


class ChangeGroup(TypedDict):
    id: str
    kind: str
    operations: list[Operation]


class Results(TypedDict):
    format: str
    formatVersion: int
    sourceInstanceId: str
    exportId: str
    portableInstanceId: str
    baselineSha256: str
    mediaSnapshotId: str
    events: list[ChangeGroup]


class Snapshot(TypedDict):
    id: str
    name: str
    createdAt: str
    reason: str


class ExportSummary(TypedDict):
    id: str
    name: str
    createdAt: str
    sourceInstanceId: str
    owner: Owner
    baselineSha256: str
    media: dict[str, Any]
    status: str
    archiveRef: str | None
    archiveBytes: int | None
    error: str | None
    importSummary: dict[str, Any]


class ExportRecord(ExportSummary):
    baseline: dict[str, Any]


# Fixed trusted SQL mapping. Returned files never select a table or column.
# (primary keys, selected value fields database -> wire, domain operation stem)
SPECS: dict[str, tuple[tuple[str, ...], dict[str, str], str]] = {
    "progress": (("person_id",), {"skip_count": "skipCount"}, "person.skip"),
    "pending": (("person_id",), {"operation": "operation", "name": "name", "target_person_id": "targetPersonId",
        "target_name": "targetName", "feature_asset_id": "featureAssetId", "included": "included", "merge_step": "mergeStep"}, "person.pending"),
    "investigate": (("person_id",), {}, "person.investigate"),
    "face_detach_pending": (("face_id",), {"source_person_id": "sourcePersonId", "asset_id": "assetId", "file_name": "fileName",
        "target_person_id": "targetPersonId", "included": "included"}, "face.pending"),
    "face_reviews": (("face_id",), {"person_id": "personId"}, "review.accepted"),
    "face_review_drafts": (("face_id",), {"person_id": "personId", "asset_id": "assetId", "file_name": "fileName", "verdict": "verdict"}, "review.draft"),
    "face_review_reopened": (("person_id",), {}, "review.reopened"),
    "group_photo_progress": (("asset_id",), {"status": "status"}, "group.progress"),
    "group_photo_drafts": (("asset_id", "person_id"), {"name": "name", "target_person_id": "targetPersonId", "operation": "operation"}, "group.draft"),
    "settings": (("key",), {"value": "value"}, "preference"),
}
OP_ENTITIES = {stem: entity for entity, (_, _, stem) in SPECS.items()}
REGISTRY_FIELDS = {
    "workspace_row_versions": ("entity", "row_key"),
    "workspace_events": ("keys_json", "kind", "origin"),
    "workspace_exports": ("baseline_json", "media_json", "owner_json", "name", "archive_ref", "error_code", "last_result_json"),
    "workspace_imports": ("export_id", "snapshot_id", "result_sha"),
    "workspace_import_groups": ("export_id", "group_id", "payload_sha", "import_id"),
    "workspace_accepted": ("export_id", "operation_id", "payload_sha", "event_id"),
    "workspace_emitted": ("entity", "row_key"),
    "workspace_actions": ("entity", "row_key", "parent_id", "intent_sha", "intent_json", "guard_json"),
    "workspace_action_heads": ("entity", "row_key", "action_id"),
    "workspace_effects": ("action_id", "kind", "payload_json"),
}


def timestamp() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def _key(key: Sequence[str]) -> str:
    return "|".join(value.encode("utf-8").hex().upper() for value in key)


def _decode_key(key: str) -> list[str]:
    return [bytes.fromhex(value).decode("utf-8") for value in key.split("|")]


def _sql_key(prefix: str, keys: Sequence[str]) -> str:
    # SQLite built-ins; no JSON extension or per-connection UDF required.
    return " || '|' || ".join(f"hex(CAST({prefix}.{column} AS BLOB))" for column in keys)


def _label(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 256 or len(value.encode("utf-8")) > 1024:
        raise WorkspaceError("A name of at most 256 characters is required")
    return value.strip()


def _require_transaction(db: sqlite3.Connection) -> None:
    if not db.in_transaction:
        raise WorkspaceError("A protected write transaction is required", "mutation_busy")


def state_revision(db: sqlite3.Connection) -> int:
    return int(db.execute("SELECT revision FROM workspace_clock WHERE singleton=1").fetchone()[0])


def group_revision(db: sqlite3.Connection) -> int:
    return int(db.execute("SELECT group_revision FROM workspace_clock WHERE singleton=1").fetchone()[0])


def instance_identity(db: sqlite3.Connection) -> dict[str, Any]:
    row = db.execute("SELECT instance_id, mode, origin_json FROM workspace_instance WHERE singleton=1").fetchone()
    return {"instanceId": row[0], "mode": row[1], "origin": json.loads(row[2]) if row[2] else None}


def _owner(db: sqlite3.Connection) -> Owner | None:
    row = db.execute("SELECT value FROM settings WHERE key='owner'").fetchone()
    if not row:
        return None
    try:
        value = json.loads(row[0])
    except (TypeError, ValueError):
        raise WorkspaceError("Saved source identity is unavailable", "source_mismatch") from None
    if not isinstance(value, dict) or set(value) != {"url", "userId"} or not all(isinstance(v, str) and v for v in value.values()):
        raise WorkspaceError("Saved source identity is unavailable", "source_mismatch")
    return value


def install_schema(db: sqlite3.Connection) -> None:
    """Add metadata/triggers without modifying existing workflow values.

    Requires the application's existing workflow schema (including old additive
    columns). Uses execute, not executescript, so callers retain transaction
    ownership. Initial row revisions are seeded once; deletion versions persist.
    """
    if not db.in_transaction:
        db.execute("BEGIN IMMEDIATE")
    statements = (
        "CREATE TABLE IF NOT EXISTS workspace_instance(singleton INTEGER PRIMARY KEY CHECK(singleton=1), version INTEGER NOT NULL, instance_id TEXT NOT NULL, mode TEXT NOT NULL, origin_json TEXT)",
        "CREATE TABLE IF NOT EXISTS workspace_clock(singleton INTEGER PRIMARY KEY CHECK(singleton=1), revision INTEGER NOT NULL, group_revision INTEGER NOT NULL)",
        "CREATE TABLE IF NOT EXISTS workspace_row_versions(entity TEXT NOT NULL, row_key TEXT NOT NULL, revision INTEGER NOT NULL, deleted INTEGER NOT NULL, PRIMARY KEY(entity,row_key))",
        "CREATE INDEX IF NOT EXISTS workspace_changed_revision ON workspace_row_versions(revision)",
        "CREATE TABLE IF NOT EXISTS workspace_usage(singleton INTEGER PRIMARY KEY CHECK(singleton=1), byte_count INTEGER NOT NULL, record_count INTEGER NOT NULL)",
        "CREATE TABLE IF NOT EXISTS workspace_events(id TEXT PRIMARY KEY, kind TEXT NOT NULL, origin TEXT NOT NULL, start_revision INTEGER NOT NULL, end_revision INTEGER NOT NULL, keys_json TEXT NOT NULL, created_at TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS workspace_exports(id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL, owner_json TEXT NOT NULL, baseline_sha TEXT NOT NULL, baseline_json TEXT NOT NULL, media_json TEXT NOT NULL, status TEXT NOT NULL, archive_ref TEXT, archive_bytes INTEGER, error_code TEXT, imported_groups INTEGER NOT NULL DEFAULT 0, last_imported_at TEXT, last_result_json TEXT)",
        "CREATE TABLE IF NOT EXISTS workspace_origin(singleton INTEGER PRIMARY KEY CHECK(singleton=1), source_baseline_json TEXT NOT NULL, local_baseline_json TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS workspace_snapshots(id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL, reason TEXT NOT NULL, owner_json TEXT, payload BLOB NOT NULL, payload_sha TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS workspace_imports(id TEXT PRIMARY KEY, export_id TEXT NOT NULL, created_at TEXT NOT NULL, snapshot_id TEXT NOT NULL UNIQUE, imported INTEGER NOT NULL, skipped INTEGER NOT NULL, result_sha TEXT)",
        "CREATE INDEX IF NOT EXISTS workspace_import_receipt ON workspace_imports(export_id,result_sha,created_at,id)",
        "CREATE TABLE IF NOT EXISTS workspace_import_groups(export_id TEXT NOT NULL, group_id TEXT NOT NULL, payload_sha TEXT NOT NULL, import_id TEXT NOT NULL, PRIMARY KEY(export_id,group_id))",
        "CREATE TABLE IF NOT EXISTS workspace_accepted(export_id TEXT NOT NULL, operation_id TEXT NOT NULL, payload_sha TEXT NOT NULL, event_id TEXT NOT NULL, PRIMARY KEY(export_id,operation_id))",
        "CREATE TABLE IF NOT EXISTS workspace_emitted(entity TEXT NOT NULL, row_key TEXT NOT NULL, PRIMARY KEY(entity,row_key))",
        "CREATE TABLE IF NOT EXISTS workspace_actions(id TEXT PRIMARY KEY, entity TEXT NOT NULL, row_key TEXT NOT NULL, parent_id TEXT, origin TEXT NOT NULL, intent_sha TEXT NOT NULL, intent_json TEXT NOT NULL, disposition TEXT NOT NULL, guard_json TEXT NOT NULL, guard_revision INTEGER NOT NULL)",
        "CREATE TABLE IF NOT EXISTS workspace_action_heads(entity TEXT NOT NULL, row_key TEXT NOT NULL, action_id TEXT NOT NULL, PRIMARY KEY(entity,row_key))",
        "CREATE TABLE IF NOT EXISTS workspace_effects(id TEXT PRIMARY KEY, action_id TEXT NOT NULL, kind TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL)",
    )
    for sql in statements:
        db.execute(sql)
    initial = db.execute("INSERT OR IGNORE INTO workspace_instance VALUES(1,?,?,?,NULL)", (FORMAT_VERSION, str(uuid.uuid4()), "source")).rowcount == 1
    if db.execute("SELECT version FROM workspace_instance WHERE singleton=1").fetchone()[0] != FORMAT_VERSION:
        raise WorkspaceError("Unsupported saved workspace version", "unsupported_format")
    db.execute("INSERT OR IGNORE INTO workspace_clock VALUES(1,0,0)")
    db.execute("INSERT OR IGNORE INTO workspace_usage VALUES(1,0,0)")
    for table, fields in REGISTRY_FIELDS.items():
        def charge(prefix: str) -> str:
            return "128+" + "+".join(f"COALESCE(length(CAST({prefix}.{field} AS BLOB)),0)" for field in fields)
        for action, delta, count in (("INSERT", charge("NEW"), 1), ("DELETE", f"-({charge('OLD')})", -1),
                                     ("UPDATE", f"({charge('NEW')})-({charge('OLD')})", 0)):
            db.execute(f"""CREATE TRIGGER IF NOT EXISTS workspace_charge_{table}_{action.lower()} AFTER {action} ON {table} BEGIN
                UPDATE workspace_usage SET byte_count=byte_count+({delta}),record_count=record_count+({count}) WHERE singleton=1;
                SELECT CASE WHEN EXISTS(SELECT 1 FROM workspace_usage WHERE byte_count>{DEFAULT_LIMITS.registry_bytes} OR record_count>{DEFAULT_LIMITS.registry_records})
                    THEN RAISE(ABORT,'workspace_storage_quota') END;
                END""")
    for entity, (keys, _, _) in SPECS.items():
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (entity,)).fetchone():
            raise WorkspaceError("Required workflow schema is unavailable", "unsupported_format")
        where = " WHERE key IN (" + ",".join("?" for _ in PREFERENCES) + ")" if entity == "settings" else ""
        if initial:
            for row in db.execute("SELECT " + ",".join(keys) + " FROM " + entity + where, PREFERENCES if where else ()):
                db.execute("UPDATE workspace_clock SET revision=revision+1 WHERE singleton=1")
                db.execute("INSERT INTO workspace_row_versions VALUES(?,?,?,0)", (entity, _key(tuple(row)), state_revision(db)))
        for trigger, action, prefix, deleted in (("insert", "INSERT", "NEW", 0), ("update", "UPDATE", "NEW", 0),
                                                  ("update_key", "UPDATE", "OLD", 1), ("delete", "DELETE", "OLD", 1)):
            key_expr = _sql_key(prefix, keys)
            conditions = []
            if trigger == "update_key":
                conditions.append(f"({key_expr}) != ({_sql_key('NEW', keys)})")
            if entity == "settings":
                allowed = ",".join("'" + key + "'" for key in PREFERENCES)
                conditions.append(f"{prefix}.key IN ({allowed})")
                if trigger == "update":
                    conditions.append("(OLD.value IS NOT NEW.value OR OLD.key IS NOT NEW.key)")
            condition = " WHEN " + " AND ".join(conditions) if conditions else ""
            is_group = "1" if entity.startswith("group_photo_") else (prefix + ".key IN ('group_min_faces','group_archived','group_pass')" if entity == "settings" else "0")
            sql = f"""CREATE TRIGGER IF NOT EXISTS workspace_{entity}_{trigger}
                AFTER {action} ON {entity}{condition} BEGIN
                SELECT CASE WHEN (SELECT revision FROM workspace_clock WHERE singleton=1)>={MAX_INTEGER}
                    THEN RAISE(ABORT,'workspace_revision_limit') END;
                UPDATE workspace_clock SET revision=revision+1, group_revision=group_revision+CASE WHEN {is_group} THEN 1 ELSE 0 END WHERE singleton=1;
                INSERT INTO workspace_row_versions(entity,row_key,revision,deleted)
                  VALUES('{entity}',{key_expr},(SELECT revision FROM workspace_clock WHERE singleton=1),{deleted})
                  ON CONFLICT(entity,row_key) DO UPDATE SET revision=excluded.revision,deleted=excluded.deleted;
                END"""
            db.execute(sql)


def row_revision(db: sqlite3.Connection, entity: str, key: Sequence[str]) -> tuple[int, bool]:
    if entity not in SPECS:
        raise WorkspaceError("Unknown logical entity")
    row = db.execute("SELECT revision,deleted FROM workspace_row_versions WHERE entity=? AND row_key=?", (entity, _key(key))).fetchone()
    return (int(row[0]), bool(row[1])) if row else (0, True)


def _logical_value(entity: str, row: sqlite3.Row | tuple, columns: list[str]) -> dict[str, Any]:
    values = dict(zip(columns, row))
    result = {wire: values[column] for column, wire in SPECS[entity][1].items()}
    if "included" in result:
        result["included"] = bool(result["included"])
    return result


def capture_state(db: sqlite3.Connection, *, limits: Limits = DEFAULT_LIMITS) -> StateCapture:
    """Capture one caller-owned committed read/write view; no caches or secrets."""
    if not db.in_transaction:
        db.execute("BEGIN")
    rows: list[LogicalRow] = []
    live: dict[tuple[str, str], dict[str, Any]] = {}
    charged = 0
    for entity, (keys, fields, _) in SPECS.items():
        columns = list(keys) + list(fields)
        where = " WHERE key IN (" + ",".join("?" for _ in PREFERENCES) + ")" if entity == "settings" else ""
        for row in db.execute("SELECT " + ",".join(columns) + " FROM " + entity + where, PREFERENCES if where else ()):
            key = _key(row[:len(keys)])
            value = _logical_value(entity, row, columns)
            charged += len(key) + len(canonical(value)) + 96
            if len(live) >= limits.registry_records or charged > limits.logical_bytes:
                raise WorkspaceError("Saved state exceeds the logical size limit", "resource_limit")
            live[entity, key] = value
    charged = 0
    for entity, key, revision, deleted in db.execute("SELECT entity,row_key,revision,deleted FROM workspace_row_versions ORDER BY entity,row_key"):
        if entity not in SPECS:
            raise WorkspaceError("Saved logical versions are unavailable")
        item: LogicalRow = {"entity": entity, "key": _decode_key(key), "revision": revision, "deleted": bool(deleted),
            "value": None if deleted else live.pop((entity, key), None)}
        if not deleted and item["value"] is None:
            raise WorkspaceError("Saved logical versions are inconsistent")
        charged += len(canonical(item)) + 1
        if len(rows) >= limits.registry_records or charged > limits.logical_bytes:
            raise WorkspaceError("Saved state exceeds the logical size limit", "resource_limit")
        rows.append(item)
    if live:
        raise WorkspaceError("Saved logical versions are incomplete")
    result: StateCapture = {"format": "people-workbench.state", "formatVersion": FORMAT_VERSION,
        "instanceId": instance_identity(db)["instanceId"], "owner": _owner(db), "stateRevision": state_revision(db),
        "groupRevision": group_revision(db), "rows": rows}
    if len(canonical(result)) > limits.logical_bytes:
        raise WorkspaceError("Saved state exceeds the logical size limit", "resource_limit")
    return result


def _registry_usage(db: sqlite3.Connection) -> tuple[int, int]:
    size, count = db.execute("SELECT byte_count,record_count FROM workspace_usage WHERE singleton=1").fetchone()
    return int(size), int(count)


def _capacity(db: sqlite3.Connection, add_bytes: int = 0, add_records: int = 0, *, limits: Limits = DEFAULT_LIMITS) -> None:
    size, count = _registry_usage(db)
    if size + add_bytes > limits.registry_bytes or count + add_records > limits.registry_records:
        raise WorkspaceError("Local workspace history capacity is full", "storage_quota")


def _intent(entity: str, value: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in value.items() if k not in {"included", "mergeStep"} and not (entity == "face_detach_pending" and k == "targetPersonId")}


def action_head(db: sqlite3.Connection, entity: str, key: Sequence[str]) -> dict[str, Any] | None:
    row = db.execute("""SELECT a.id,a.parent_id,a.origin,a.intent_sha,a.disposition,a.guard_json,a.guard_revision,a.intent_json
        FROM workspace_action_heads h JOIN workspace_actions a ON a.id=h.action_id WHERE h.entity=? AND h.row_key=?""", (entity, _key(key))).fetchone()
    if not row:
        return None
    return dict(zip(("id", "parentId", "origin", "intentSha256", "disposition", "guard", "guardRevision", "intent"),
                    (*row[:5], json.loads(row[5]), row[6], json.loads(row[7]))))


def _put_action(db: sqlite3.Connection, entity: str, key: Sequence[str], intent: dict[str, Any], origin: str,
                guard: Mapping[str, Any], previous: Mapping[str, Any] | None, revision: int) -> str:
    if previous and previous["disposition"] == "queued":
        db.execute("UPDATE workspace_actions SET disposition='superseded' WHERE id=?", (previous["id"],))
    action_id = str(uuid.uuid4())
    db.execute("INSERT INTO workspace_actions VALUES(?,?,?,?,?,?,?,?,?,?)", (action_id, entity, _key(key), previous["id"] if previous else None,
        origin, digest(intent), canonical(intent).decode(), "queued", canonical(dict(guard)).decode(), revision))
    db.execute("INSERT INTO workspace_action_heads VALUES(?,?,?) ON CONFLICT(entity,row_key) DO UPDATE SET action_id=excluded.action_id", (entity, _key(key), action_id))
    return action_id


def _track_actions(db: sqlite3.Connection, rows: list[LogicalRow], origin: str) -> None:
    for row in rows:
        entity, key = row["entity"], row["key"]
        if entity not in {"pending", "face_detach_pending"}:
            continue
        previous = action_head(db, entity, key)
        if row["deleted"]:
            if previous and previous["disposition"] == "queued":
                disposition = "synced" if origin == "sync_checkpoint" else "removed"
                db.execute("UPDATE workspace_actions SET disposition=? WHERE id=?", (disposition, previous["id"]))
            continue
        intent = _intent(entity, row["value"] or {})
        if previous and previous["disposition"] == "queued" and previous["intentSha256"] == digest(intent):
            continue
        guard: dict[str, Any] = {}
        action_origin = "import" if origin == "import" else "source-native"
        if previous and previous["disposition"] == "queued":
            if previous["origin"] in {"import", "source-derived"} and origin != "import":
                action_origin = "source-derived"
            guard = previous["guard"].copy()
            if guard and any(intent.get(field) and intent[field] != previous["intent"].get(field)
                             for field in ("targetPersonId", "featureAssetId", "assetId", "sourcePersonId")):
                guard["status"] = "baseline_unverified"
        elif previous and (previous["origin"] in {"import", "source-derived"} or previous["guard"]):
            guard = {"status": "baseline_unverified", "removedAncestor": previous["id"]}
        _put_action(db, entity, key, intent, action_origin, guard, previous, row["revision"])


def _read_value(db: sqlite3.Connection, entity: str, key: Sequence[str]) -> dict[str, Any] | None:
    keys, fields, _ = SPECS[entity]
    columns = list(keys) + list(fields)
    where = " AND ".join(column + "=?" for column in keys)
    row = db.execute("SELECT " + ",".join(columns) + " FROM " + entity + " WHERE " + where, tuple(key)).fetchone()
    return _logical_value(entity, row, columns) if row else None


def record_event(db: sqlite3.Connection, kind: str, origin: str, start_revision: int) -> dict[str, Any] | None:
    _require_transaction(db)
    end = state_revision(db)
    if end == start_revision:
        return None
    if not isinstance(kind, str) or not kind or len(kind) > 128 or origin not in {"source_user", "import", "sync_checkpoint", "local_maintenance"}:
        raise WorkspaceError("Invalid local action context")
    keys: list[list[Any]] = []
    pending: list[LogicalRow] = []
    charged = 0
    for entity, row_key, revision, deleted in db.execute("SELECT entity,row_key,revision,deleted FROM workspace_row_versions WHERE revision>? ORDER BY revision", (start_revision,)):
        key = _decode_key(row_key)
        item = [entity, key]
        charged += len(canonical(item)) + 1
        if len(keys) >= DEFAULT_LIMITS.registry_records or charged > DEFAULT_LIMITS.logical_bytes:
            raise WorkspaceError("Local action exceeds the history size limit", "resource_limit")
        keys.append(item)
        if entity in {"pending", "face_detach_pending"}:
            pending.append({"entity": entity, "key": key, "revision": revision, "deleted": bool(deleted),
                            "value": None if deleted else _read_value(db, entity, key)})
    _capacity(db, charged + len(pending) * 1024 + 256, 1 + len(pending) * 2)
    _track_actions(db, pending, origin)
    event = {"id": str(uuid.uuid4()), "kind": kind, "origin": origin, "startRevision": start_revision, "endRevision": end, "keys": keys}
    db.execute("INSERT INTO workspace_events VALUES(?,?,?,?,?,?,?)", (event["id"], kind, origin, start_revision, end, canonical(keys).decode(), timestamp()))
    return event


def bind_import_guard(db: sqlite3.Connection, entity: str, key: Sequence[str], guard: Mapping[str, Any]) -> str:
    """Bind an imported pending intent before the enclosing workflow commits."""
    _require_transaction(db)
    if entity not in {"pending", "face_detach_pending"}:
        raise WorkspaceError("Only pending actions can have import guards")
    value = _read_value(db, entity, key)
    if value is None:
        raise WorkspaceError("Pending action changed", "preview_stale")
    previous = action_head(db, entity, key)
    binding = dict(guard)
    if previous and previous["disposition"] == "queued" and previous["guard"]:
        binding["inherited"] = previous["guard"]
    _capacity(db, len(canonical(binding)) + len(canonical(value)) + 512, 2)
    return _put_action(db, entity, key, _intent(entity, value), "import", binding, previous, row_revision(db, entity, key)[0])


def set_action_guard(db: sqlite3.Connection, entity: str, key: Sequence[str], guard: Mapping[str, Any]) -> None:
    """C/D attach verified source facts; incoming objects never call this directly."""
    _require_transaction(db)
    action = action_head(db, entity, key)
    if not action or action["disposition"] != "queued":
        raise WorkspaceError("Pending action changed", "preview_stale")
    encoded = canonical(dict(guard))
    _capacity(db, len(encoded))
    db.execute("UPDATE workspace_actions SET guard_json=?,guard_revision=guard_revision+1 WHERE id=?", (encoded.decode(), action["id"]))


def guard_token_rows(db: sqlite3.Connection) -> list[list[Any]]:
    return [[row[0], _decode_key(row[1]), row[2], row[3], row[4]] for row in db.execute("""SELECT h.entity,h.row_key,a.id,a.guard_revision,a.guard_json
        FROM workspace_action_heads h JOIN workspace_actions a ON a.id=h.action_id
        WHERE a.disposition='queued' AND (a.origin!='source-native' OR a.guard_json!='{}') ORDER BY h.entity,h.row_key""")]


def record_effect(db: sqlite3.Connection, action_id: str, kind: str, payload: Mapping[str, Any], *, limits: Limits = DEFAULT_LIMITS) -> str:
    _require_transaction(db)
    if not db.execute("SELECT 1 FROM workspace_actions WHERE id=?", (action_id,)).fetchone():
        raise WorkspaceError("Pending action changed", "preview_stale")
    data = canonical(dict(payload))
    _capacity(db, len(data) + 256, 1, limits=limits)
    effect_id = str(uuid.uuid4())
    db.execute("INSERT INTO workspace_effects VALUES(?,?,?,?,?)", (effect_id, action_id, kind, data.decode(), timestamp()))
    return effect_id


def _inventory(inventory: Mapping[str, Any], budget: int, references: int) -> dict[str, Any]:
    """Project only trusted source fields used by identity/conflict checks."""
    result: dict[str, Any] = {}
    charged = 2
    count = 0
    fields = {
        "people": ("id", "name", "isHidden", "updatedAt", "featureFaceAssetId", "faceAssetId", "assetCount"),
        "assets": ("id", "type", "isTrashed", "deletedAt", "isOffline", "isExternal", "isArchived", "visibility", "originalFileName"),
        "faces": ("id", "assetId", "personId", "boundingBoxX1", "boundingBoxX2", "boundingBoxY1", "boundingBoxY2", "imageWidth", "imageHeight"),
    }
    for kind, allowed in fields.items():
        table = inventory.get(kind, {})
        if not isinstance(table, Mapping):
            raise WorkspaceError("Prepared media inventory is unavailable", "not_ready")
        projected: dict[str, Any] = {}
        for key, row in table.items():
            if not isinstance(key, str):
                raise WorkspaceError("Prepared media inventory is unavailable", "not_ready")
            if kind == "faces" and not isinstance(row, list):
                raise WorkspaceError("Prepared face inventory is unavailable", "not_ready")
            rows = row if kind == "faces" else (row,)
            charged += len(canonical(key)) + 4
            count += 1
            if charged > budget or count > references:
                raise WorkspaceError("Export inventory exceeds the size limit", "resource_limit")
            entries = []
            for item in rows:
                if not isinstance(item, Mapping):
                    raise WorkspaceError("Prepared media inventory is unavailable", "not_ready")
                value = {field: item[field] for field in allowed if field in item and type(item[field]) in {type(None), str, int, float, bool}}
                if kind == "faces" and isinstance(item.get("person"), Mapping) and isinstance(item["person"].get("id"), str):
                    value["person"] = {"id": item["person"]["id"]}
                charged += len(canonical(value)) + 1
                count += 1
                if charged > budget or count > references:
                    raise WorkspaceError("Export inventory exceeds the size limit", "resource_limit")
                entries.append(value)
            projected[key] = entries if kind == "faces" else entries[0]
        result[kind] = projected
    return result


def reserve_export(db: sqlite3.Connection, name: str, capture: StateCapture, media: Mapping[str, Any], *, inventory: Mapping[str, Any] | None = None, limits: Limits = DEFAULT_LIMITS) -> ExportRecord:
    _require_transaction(db)
    identity = instance_identity(db)
    if identity["mode"] != "source" or capture["instanceId"] != identity["instanceId"] or capture["owner"] != _owner(db) or not capture["owner"]:
        raise WorkspaceError("Workspace does not belong to this source", "source_mismatch")
    if capture["stateRevision"] != state_revision(db):
        raise WorkspaceError("Saved state changed before export", "preview_stale")
    for row in capture["rows"]:
        if row["deleted"]:
            continue
        value = row["value"] or {}
        if row["entity"] == "pending" and value.get("mergeStep", "ready") != "ready":
            raise WorkspaceError("Reconcile started merges before exporting", "not_ready")
        if row["entity"] == "face_detach_pending" and value.get("targetPersonId"):
            raise WorkspaceError("Reconcile started face corrections before exporting", "not_ready")
    if (not isinstance(media.get("snapshotId"), str) or not re.fullmatch(r"[0-9a-fA-F-]{20,50}", media["snapshotId"])
            or not re.fullmatch(r"[0-9a-f]{64}", str(media.get("sha256", "")))
            or type(media.get("assetCount")) is not int or not 0 <= media["assetCount"] <= 100_000
            or type(media.get("mediaBytes")) is not int or not 0 <= media["mediaBytes"] <= 512 * 1024 ** 3):
        raise WorkspaceError("Prepared media identity is unavailable", "not_ready")
    if db.execute("SELECT 1 FROM workspace_exports WHERE status='preparing'").fetchone():
        raise WorkspaceError("Another workspace export is preparing", "resource_busy")
    if db.execute("SELECT COUNT(*) FROM workspace_exports").fetchone()[0] >= limits.export_count:
        raise WorkspaceError("Export history capacity is full", "storage_quota")
    media = {key: media[key] for key in ("snapshotId", "sha256", "assetCount", "mediaBytes")}
    inventory_budget = limits.logical_bytes - len(canonical(capture)) - len(canonical(media))
    baseline = {"state": capture, "media": media, "inventory": _inventory(inventory or {}, inventory_budget, limits.registry_records)}
    data = canonical(baseline)
    if len(data) > limits.logical_bytes:
        raise WorkspaceError("Export baseline exceeds the size limit", "resource_limit")
    _capacity(db, len(data) + 512, 1, limits=limits)
    export_id = str(uuid.uuid4())
    db.execute("INSERT INTO workspace_exports(id,name,created_at,owner_json,baseline_sha,baseline_json,media_json,status) VALUES(?,?,?,?,?,?,?,'preparing')",
        (export_id, _label(name), timestamp(), canonical(capture["owner"]).decode(), hashlib.sha256(data).hexdigest(), data.decode(), canonical(media).decode()))
    return read_known_export(db, export_id)


def read_export_summary(db: sqlite3.Connection, export_id: str, owner: Owner | None = None) -> ExportSummary:
    metadata = db.execute("SELECT " + EXPORT_COLUMNS + " FROM workspace_exports WHERE id=?", (export_id,)).fetchone()
    identity = instance_identity(db)
    if not metadata or identity["mode"] != "source":
        raise WorkspaceError("Unknown source export", "source_mismatch")
    saved_owner = json.loads(metadata[3])
    if saved_owner != _owner(db) or (owner is not None and owner != saved_owner):
        raise WorkspaceError("Export belongs to another source account", "source_mismatch")
    return _export_summary(metadata, identity["instanceId"])


def read_known_export(db: sqlite3.Connection, export_id: str, owner: Owner | None = None) -> ExportRecord:
    metadata = read_export_summary(db, export_id, owner)
    row = db.execute("SELECT baseline_json FROM workspace_exports WHERE id=? AND length(CAST(baseline_json AS BLOB))<=?", (export_id, DEFAULT_LIMITS.logical_bytes)).fetchone()
    if not row or hashlib.sha256(row[0].encode()).hexdigest() != metadata["baselineSha256"]:
        raise WorkspaceError("Saved export baseline is unavailable", "source_mismatch")
    baseline = json.loads(row[0])
    if baseline["state"]["instanceId"] != metadata["sourceInstanceId"] or baseline["state"]["owner"] != metadata["owner"] or baseline["media"] != metadata["media"]:
        raise WorkspaceError("Saved export baseline identity changed", "source_mismatch")
    return {**metadata, "baseline": baseline}


EXPORT_COLUMNS = "id,name,created_at,owner_json,baseline_sha,media_json,status,archive_ref,archive_bytes,error_code,imported_groups,last_imported_at,last_result_json"


def _export_summary(row: Sequence[Any], source_id: str) -> ExportSummary:
    imported = {"importedGroups": row[10], "lastImportedAt": row[11]}
    if row[12]:
        imported["lastResult"] = json.loads(row[12])
    return {"id": row[0], "name": row[1], "createdAt": row[2], "sourceInstanceId": source_id, "owner": json.loads(row[3]),
        "baselineSha256": row[4], "media": json.loads(row[5]), "status": row[6], "archiveRef": row[7], "archiveBytes": row[8], "error": row[9],
        "importSummary": imported}


def list_exports(db: sqlite3.Connection, owner: Owner) -> list[ExportSummary]:
    identity = instance_identity(db)
    if identity["mode"] != "source" or owner != _owner(db):
        raise WorkspaceError("Workspace account changed", "source_mismatch")
    return [_export_summary(row, identity["instanceId"]) for row in db.execute("SELECT " + EXPORT_COLUMNS +
        " FROM workspace_exports WHERE owner_json=? ORDER BY created_at,id LIMIT ?", (canonical(owner).decode(), DEFAULT_LIMITS.export_count))]


def publish_export(db: sqlite3.Connection, export_id: str, archive_ref: str, archive_bytes: int, *, baseline_sha256: str, limits: Limits = DEFAULT_LIMITS) -> ExportSummary:
    _require_transaction(db)
    export = read_export_summary(db, export_id)
    if export["status"] != "preparing" or export["baselineSha256"] != baseline_sha256:
        raise WorkspaceError("Export is no longer preparing", "not_ready")
    if archive_ref != export_id + ".zip" or type(archive_bytes) is not int or not 0 <= archive_bytes <= 512 * 1024 ** 3:
        raise WorkspaceError("Invalid archive publication")
    count, size = db.execute("SELECT COUNT(*),COALESCE(SUM(archive_bytes),0) FROM workspace_exports WHERE status='ready'").fetchone()
    if count >= limits.ready_archive_count or size + archive_bytes > limits.archive_bytes:
        raise WorkspaceError("Ready archive capacity is full", "storage_quota")
    db.execute("UPDATE workspace_exports SET status='ready',archive_ref=?,archive_bytes=? WHERE id=?", (archive_ref, archive_bytes, export_id))
    row = db.execute("SELECT " + EXPORT_COLUMNS + " FROM workspace_exports WHERE id=?", (export_id,)).fetchone()
    return _export_summary(row, export["sourceInstanceId"])


def fail_export(db: sqlite3.Connection, export_id: str, code: str) -> None:
    _require_transaction(db)
    if not re.fullmatch(r"[a-z_]{1,64}", code):
        raise WorkspaceError("Invalid export error code")
    db.execute("UPDATE workspace_exports SET status='error',error_code=? WHERE id=? AND status='preparing'", (code, export_id))


def create_snapshot(db: sqlite3.Connection, name: str, reason: str, *, limits: Limits = DEFAULT_LIMITS) -> Snapshot:
    _require_transaction(db)
    if reason not in {"manual", "pre-import"}:
        raise WorkspaceError("Unsupported snapshot reason")
    data = canonical(capture_state(db, limits=limits))
    size, count = db.execute("SELECT COALESCE(SUM(length(payload)),0),COUNT(*) FROM workspace_snapshots").fetchone()
    if count >= limits.snapshot_count or size + len(data) > limits.snapshot_bytes:
        raise WorkspaceError("State snapshot capacity is full", "snapshot_quota")
    snapshot: Snapshot = {"id": str(uuid.uuid4()), "name": _label(name), "createdAt": timestamp(), "reason": reason}
    db.execute("INSERT INTO workspace_snapshots VALUES(?,?,?,?,?,?,?)", (snapshot["id"], snapshot["name"], snapshot["createdAt"], reason, canonical(_owner(db)).decode(), data, hashlib.sha256(data).hexdigest()))
    return snapshot


def list_snapshots(db: sqlite3.Connection, owner: Owner | None = None) -> list[Snapshot]:
    actual = _owner(db)
    if owner is not None and owner != actual:
        raise WorkspaceError("Workspace account changed", "source_mismatch")
    return [dict(zip(("id", "name", "createdAt", "reason"), row)) for row in db.execute("SELECT id,name,created_at,reason FROM workspace_snapshots WHERE owner_json=? ORDER BY created_at,id", (canonical(actual).decode(),))]


def read_snapshot(db: sqlite3.Connection, snapshot_id: str, owner: Owner | None = None) -> tuple[Snapshot, bytes]:
    allowed = {item["id"]: item for item in list_snapshots(db, owner)}
    if snapshot_id not in allowed:
        raise WorkspaceError("Unknown state snapshot", "source_mismatch")
    row = db.execute("SELECT payload,payload_sha FROM workspace_snapshots WHERE id=? AND length(payload)<=?", (snapshot_id, DEFAULT_LIMITS.logical_bytes)).fetchone()
    if not row or hashlib.sha256(row[0]).hexdigest() != row[1]:
        raise WorkspaceError("Saved snapshot is unavailable", "snapshot_failed")
    return allowed[snapshot_id], bytes(row[0])


def _workflow_schema(db: sqlite3.Connection) -> None:
    # New outbound database only, never an incoming schema or a live DB rewrite.
    definitions = {
        "progress": "person_id TEXT PRIMARY KEY,skip_count INTEGER NOT NULL DEFAULT 0,updated_at TEXT NOT NULL",
        "pending": "person_id TEXT PRIMARY KEY,operation TEXT NOT NULL,name TEXT NOT NULL DEFAULT '',target_person_id TEXT,target_name TEXT NOT NULL DEFAULT '',feature_asset_id TEXT,included INTEGER NOT NULL DEFAULT 1,last_error TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,updated_at TEXT NOT NULL,merge_step TEXT NOT NULL DEFAULT 'ready'",
        "investigate": "person_id TEXT PRIMARY KEY,created_at TEXT NOT NULL",
        "face_detach_pending": "face_id TEXT PRIMARY KEY,source_person_id TEXT NOT NULL,asset_id TEXT NOT NULL,file_name TEXT NOT NULL DEFAULT '',target_person_id TEXT,included INTEGER NOT NULL DEFAULT 1,last_error TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,updated_at TEXT NOT NULL",
        "face_reviews": "face_id TEXT PRIMARY KEY,person_id TEXT NOT NULL,reviewed_at TEXT NOT NULL",
        "face_review_drafts": "face_id TEXT PRIMARY KEY,person_id TEXT NOT NULL,asset_id TEXT NOT NULL,file_name TEXT NOT NULL DEFAULT '',verdict TEXT NOT NULL CHECK(verdict IN ('correct','wrong','unreviewed')),updated_at TEXT NOT NULL",
        "face_review_reopened": "person_id TEXT PRIMARY KEY,reopened_at TEXT NOT NULL",
        "group_photo_progress": "asset_id TEXT PRIMARY KEY,status TEXT NOT NULL CHECK(status IN ('deferred','ready','done')),updated_at TEXT NOT NULL",
        "group_photo_drafts": "asset_id TEXT NOT NULL,person_id TEXT NOT NULL,name TEXT NOT NULL,target_person_id TEXT,operation TEXT NOT NULL DEFAULT 'rename',PRIMARY KEY(asset_id,person_id)",
        "settings": "key TEXT PRIMARY KEY,value TEXT NOT NULL",
    }
    for table, columns in definitions.items():
        db.execute(f"CREATE TABLE {table}({columns})")


def materialize_portable(capture: StateCapture, export: ExportRecord, destination: Path) -> dict[str, Any]:
    """Build a new clean DB, not a backup with deleted/private pages remaining.

    Destination must not exist. Caller owns staging and writes the returned
    root descriptor only when its archive publication fence succeeds.
    """
    if export["baseline"]["state"] != capture or digest(export["baseline"]) != export["baselineSha256"]:
        raise WorkspaceError("Export baseline changed", "source_mismatch")
    destination = Path(destination)
    with destination.open("xb"):
        pass
    db = sqlite3.connect(destination)
    try:
        with db:
            _workflow_schema(db)
            install_schema(db)
            local_id = instance_identity(db)["instanceId"]
            origin = {"format": "people-workbench.portable", "formatVersion": FORMAT_VERSION,
                "portableInstanceId": local_id, "sourceInstanceId": export["sourceInstanceId"], "exportId": export["id"],
                "owner": export["owner"], "baselineSha256": export["baselineSha256"], "media": export["baseline"]["media"]}
            db.execute("UPDATE workspace_instance SET mode='portable',origin_json=? WHERE singleton=1", (canonical(origin).decode(),))
            stamp = timestamp()
            for row in capture["rows"]:
                if row["deleted"]:
                    continue
                entity, value = row["entity"], row["value"] or {}
                if entity not in SPECS:
                    raise WorkspaceError("Unsupported captured entity")
                if (entity == "pending" and value.get("mergeStep", "ready") != "ready") or (entity == "face_detach_pending" and value.get("targetPersonId")):
                    raise WorkspaceError("Started operations cannot travel", "not_ready")
                keys, fields, _ = SPECS[entity]
                values = dict(zip(keys, row["key"]))
                values.update({column: value[wire] for column, wire in fields.items()})
                columns = {column[1] for column in db.execute(f"PRAGMA table_info({entity})")}
                for column in ("created_at", "updated_at", "reviewed_at", "reopened_at"):
                    if column in columns:
                        values[column] = stamp
                names = list(values)
                db.execute(f"INSERT INTO {entity}(" + ",".join(names) + ") VALUES(" + ",".join("?" for _ in names) + ")", tuple(values.values()))
            db.execute("INSERT INTO settings VALUES('owner',?)", (canonical(export["owner"]).decode(),))
            db.execute("INSERT INTO workspace_origin VALUES(1,?,?)", (canonical(export["baseline"]).decode(), canonical(capture_state(db)).decode()))
        db.execute("PRAGMA journal_mode=DELETE")
        db.execute("VACUUM")
        return origin
    finally:
        db.close()


def portable_descriptor(db: sqlite3.Connection) -> dict[str, Any] | None:
    identity = instance_identity(db)
    if identity["mode"] == "source":
        return None
    origin = identity["origin"]
    if (identity["mode"] != "portable" or not isinstance(origin, dict) or origin.get("format") != "people-workbench.portable"
            or origin.get("formatVersion") != FORMAT_VERSION or origin.get("portableInstanceId") != identity["instanceId"]):
        raise WorkspaceError("Portable workspace identity is unavailable", "portable_invalid")
    return origin


def _dependencies(op: Operation, prior: Mapping[str, Any] | None = None) -> set[str]:
    stem = op["type"].rsplit(".", 1)[0]
    entity = OP_ENTITIES[stem]
    key = op["key"]
    value = op["value"] or {}
    result = {entity + ":" + _key(key)}
    if entity in {"progress", "pending", "investigate", "face_review_reopened"}:
        result.add("person:" + key[0])
    if entity in {"face_detach_pending", "face_reviews", "face_review_drafts"}:
        result.add("face:" + key[0])
    if entity in {"group_photo_progress", "group_photo_drafts"}:
        result.add("asset:" + key[0])
    if entity == "group_photo_drafts":
        result.add("person:" + key[1])
    for values in (value, prior or {}):
        for field, prefix in (("personId", "person:"), ("sourcePersonId", "person:"), ("targetPersonId", "person:"), ("assetId", "asset:"), ("featureAssetId", "asset:")):
            if values.get(field):
                result.add(prefix + values[field])
    return result


def _kind(members: Sequence[Operation]) -> str:
    if any(op["type"].startswith("group.") for op in members):
        return "group-photo"
    if any(op["type"].startswith(("face.", "review.")) for op in members):
        return "face-review"
    return "local-changes"


def group_changes(operations: Sequence[Operation], *, event_keys: Sequence[Sequence[tuple[str, Sequence[str]]]] = (),
                  baseline: StateCapture | None = None, export_id: str = "", limits: Limits = DEFAULT_LIMITS) -> list[ChangeGroup]:
    """Linear union-find closure, fixed domain dependencies plus saved event keys."""
    if len(operations) > limits.operations:
        raise WorkspaceError("Too many returned operations", "resource_limit")
    parents = list(range(len(operations)))
    def root(i: int) -> int:
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i
    def join(a: int, b: int) -> None:
        parents[root(a)] = root(b)
    by_dependency: dict[str, int] = {}
    by_row: dict[tuple[str, str], int] = {}
    for index, op in enumerate(operations):
        row = OP_ENTITIES[op["type"].rsplit(".", 1)[0]], _key(op["key"])
        if row in by_row:
            raise WorkspaceError("More than one returned change targets the same item")
        by_row[row] = index
    prior = {(row["entity"], _key(row["key"])): row["value"] for row in baseline["rows"]
             if (row["entity"], _key(row["key"])) in by_row} if baseline else {}
    references = 0
    global_pass = any(op["type"].startswith("preference.") and op["key"][0] in GROUP_SETTINGS for op in operations)
    for index, op in enumerate(operations):
        entity = OP_ENTITIES[op["type"].rsplit(".", 1)[0]]
        dependencies = _dependencies(op, prior.get((entity, _key(op["key"]))))
        if global_pass and (entity.startswith("group_photo_") or (entity == "settings" and op["key"][0] in GROUP_SETTINGS)):
            dependencies.add("global-group-pass")
        references += len(dependencies)
        if references > limits.references:
            raise WorkspaceError("Change dependencies exceed the limit", "resource_limit")
        for dependency in dependencies:
            if dependency in by_dependency:
                join(index, by_dependency[dependency])
            else:
                by_dependency[dependency] = index
    for keys in event_keys:
        members = [by_row[(entity, _key(key))] for entity, key in keys if (entity, _key(key)) in by_row]
        references += len(keys)
        if references > limits.references:
            raise WorkspaceError("Change dependencies exceed the limit", "resource_limit")
        for member in members[1:]:
            join(members[0], member)
    components: dict[int, list[Operation]] = {}
    for i, op in enumerate(operations):
        components.setdefault(root(i), []).append(op)
    groups: list[ChangeGroup] = []
    for members in components.values():
        members.sort(key=lambda op: op["id"])
        groups.append({"id": digest({"exportId": export_id, "operations": members}), "kind": _kind(members), "operations": members})
    return sorted(groups, key=lambda group: group["id"])


def group_results(result: Results, baseline: StateCapture, *, limits: Limits = DEFAULT_LIMITS) -> tuple[list[ChangeGroup], dict[str, str]]:
    """Build source selection groups from validated result and trusted baseline."""
    operations: list[Operation] = []
    event_ids: dict[str, str] = {}
    event_keys = []
    for event in result["events"]:
        keys = []
        for op in event["operations"]:
            if op["id"] in event_ids:
                raise WorkspaceError("Duplicate returned operation ID")
            if len(operations) >= limits.operations:
                raise WorkspaceError("Too many returned operations", "resource_limit")
            event_ids[op["id"]] = event["id"]
            operations.append(op)
            keys.append((OP_ENTITIES[op["type"].rsplit(".", 1)[0]], op["key"]))
        event_keys.append(keys)
    return group_changes(operations, event_keys=event_keys, baseline=baseline, export_id=result["exportId"], limits=limits), event_ids


def compute_changes(db: sqlite3.Connection, *, limits: Limits = DEFAULT_LIMITS) -> Results:
    origin = portable_descriptor(db)
    if not origin:
        raise WorkspaceError("Results are available only in portable workspaces", "portable_restricted")
    baseline = db.execute("SELECT source_baseline_json,local_baseline_json FROM workspace_origin WHERE singleton=1").fetchone()
    if not baseline or digest(json.loads(baseline[0])) != origin["baselineSha256"]:
        raise WorkspaceError("Portable baseline is unavailable", "portable_invalid")
    local_base = json.loads(baseline[1])
    current = capture_state(db, limits=limits)
    if current["owner"] != origin["owner"] or local_base["instanceId"] != origin["portableInstanceId"]:
        raise WorkspaceError("Portable baseline identity changed", "portable_invalid")
    old = {(row["entity"], _key(row["key"])): row for row in local_base["rows"]}
    by_event: dict[str, list[Operation]] = {}
    emitted = set(map(tuple, db.execute("SELECT entity,row_key FROM workspace_emitted")))
    touched: dict[tuple[str, str], str] = {}
    references = 0
    for event_id, event_keys in db.execute("SELECT id,keys_json FROM workspace_events ORDER BY end_revision,id"):
        keys = json.loads(event_keys)
        references += len(keys)
        if references > limits.references:
            raise WorkspaceError("Change dependencies exceed the limit", "resource_limit")
        for entity, key in keys:
            touched[entity, _key(key)] = event_id
    count = 0
    for row in current["rows"]:
        entity, key = row["entity"], row["key"]
        if entity == "settings" and key == ["group_current"]:
            continue
        row_key = entity, _key(key)
        before = old.get(row_key)
        value = None if row["deleted"] else dict(row["value"] or {})
        before_value = None if not before or before["deleted"] else dict(before["value"] or {})
        if value == before_value:
            if row_key in emitted:
                raise WorkspaceError("Previously returned work was reverted. Prepare a fresh workspace to return further changes.", "not_ready")
            continue
        if value is not None and entity == "pending":
            if value.pop("mergeStep", "ready") != "ready":
                raise WorkspaceError("Portable work contains a started merge", "portable_invalid")
        if value is not None and entity == "face_detach_pending":
            if value.pop("targetPersonId", None):
                raise WorkspaceError("Portable work contains a started correction", "portable_invalid")
        op_type = SPECS[entity][2] + (".remove" if value is None else ".set")
        operation = {"type": op_type, "key": key, "value": value}
        operation_id = str(uuid.uuid5(NAMESPACE, origin["exportId"] + ":" + origin["portableInstanceId"] + ":" + str(row["revision"]) + ":" + digest(operation)))
        count += 1
        if count > limits.operations:
            raise WorkspaceError("Too many returned operations", "resource_limit")
        event_id = touched.get(row_key)
        if not event_id:
            raise WorkspaceError("Portable change history is unavailable", "portable_invalid")
        by_event.setdefault(event_id, []).append({"id": operation_id, **operation})
    events: list[ChangeGroup] = [{"id": event_id, "kind": _kind(members), "operations": sorted(members, key=lambda op: op["id"])}
        for event_id, members in sorted(by_event.items())]
    result: Results = {"format": "people-workbench.results", "formatVersion": FORMAT_VERSION,
        "sourceInstanceId": origin["sourceInstanceId"], "exportId": origin["exportId"], "portableInstanceId": origin["portableInstanceId"],
        "baselineSha256": origin["baselineSha256"], "mediaSnapshotId": origin["media"]["snapshotId"], "events": events}
    if len(canonical(result)) > RESULT_BYTES:
        raise WorkspaceError("Results exceed the return-file size limit", "resource_limit")
    return result


def note_results_emitted(db: sqlite3.Connection, result: Results, *, limits: Limits = DEFAULT_LIMITS) -> None:
    """Fence conservative continuation before releasing any result bytes.

    Caller computes the result and validates the writer's size in this same
    protected transaction, commits this note, then begins the download.
    """
    _require_transaction(db)
    origin = portable_descriptor(db)
    if not origin or any(result[key] != origin[key] for key in ("exportId", "portableInstanceId", "baselineSha256")):
        raise WorkspaceError("Portable workspace changed", "portable_invalid")
    keys = {(OP_ENTITIES[op["type"].rsplit(".", 1)[0]], _key(op["key"])) for event in result["events"] for op in event["operations"]}
    existing = set(map(tuple, db.execute("SELECT entity,row_key FROM workspace_emitted")))
    added = keys - existing
    _capacity(db, sum(len(entity.encode()) + len(key.encode()) + 128 for entity, key in added), len(added), limits=limits)
    db.executemany("INSERT INTO workspace_emitted VALUES(?,?)", sorted(added))


def accepted_operation(db: sqlite3.Connection, export_id: str, operation: Operation, event_id: str) -> bool:
    row = db.execute("SELECT payload_sha,event_id FROM workspace_accepted WHERE export_id=? AND operation_id=?", (export_id, operation["id"])).fetchone()
    if row and tuple(row) != (digest(operation), event_id):
        raise WorkspaceError("A returned operation ID was reused with different contents")
    return row is not None


def acceptance_status(db: sqlite3.Connection, export_id: str, group: ChangeGroup, event_ids: Mapping[str, str]) -> str:
    if not group["operations"]:
        raise WorkspaceError("Empty change group")
    flags = [accepted_operation(db, export_id, op, event_ids[op["id"]]) for op in group["operations"]]
    return "applied" if all(flags) else ("conflict" if any(flags) else "ready")


def record_import(db: sqlite3.Connection, export_id: str, groups: Sequence[ChangeGroup], snapshot_id: str, *, event_ids: Mapping[str, str],
                  skipped: int = 0, result_sha256: str | None = None, import_id: str | None = None, observed_counts: Mapping[str, int] | None = None,
                  limits: Limits = DEFAULT_LIMITS) -> dict[str, Any]:
    """C calls after protected operations; rollback covers this audit and snapshot.

    No workflow writes happen here. Existing accepted IDs are immutable. The
    caller validates group closure/current guards before applying any operation.
    """
    _require_transaction(db)
    if read_export_summary(db, export_id)["status"] != "ready":
        raise WorkspaceError("Export is not ready", "not_ready")
    if not groups:
        raise WorkspaceError("Select at least one ready change")
    seen: set[str] = set()
    seen_groups: set[str] = set()
    for group in groups:
        if group["id"] in seen_groups:
            raise WorkspaceError("Duplicate change group")
        seen_groups.add(group["id"])
        if acceptance_status(db, export_id, group, event_ids) != "ready":
            raise WorkspaceError("Selected changes are no longer ready", "preview_stale")
        for operation in group["operations"]:
            if operation["id"] in seen:
                raise WorkspaceError("Duplicate returned operation ID")
            seen.add(operation["id"])
    if not db.execute("SELECT 1 FROM workspace_snapshots WHERE id=? AND reason='pre-import' AND owner_json=?", (snapshot_id, canonical(_owner(db)).decode())).fetchone():
        raise WorkspaceError("Pre-import snapshot is required", "snapshot_failed")
    _capacity(db, len(seen) * 384 + len(groups) * 256 + 512, len(seen) + len(groups) + 1, limits=limits)
    import_id = import_id or str(uuid.uuid4())
    created = timestamp()
    db.execute("INSERT INTO workspace_imports VALUES(?,?,?,?,?,?,?)", (import_id, export_id, created, snapshot_id, len(groups), skipped, result_sha256))
    for group in groups:
        db.execute("INSERT INTO workspace_import_groups VALUES(?,?,?,?)", (export_id, group["id"], digest(group), import_id))
        for operation in group["operations"]:
            db.execute("INSERT INTO workspace_accepted VALUES(?,?,?,?)", (export_id, operation["id"], digest(operation), event_ids[operation["id"]]))
    observed = {"imported": len(groups), "skipped": skipped}
    if observed_counts is not None:
        for status in ("ready", "conflict", "applied"):
            count = observed_counts.get(status)
            if type(count) is not int or not 0 <= count <= limits.operations:
                raise WorkspaceError("Invalid observed import counts")
            observed[status] = count
    db.execute("UPDATE workspace_exports SET imported_groups=imported_groups+?,last_imported_at=?,last_result_json=? WHERE id=?",
               (len(groups), created, canonical(observed).decode(), export_id))
    return {"id": import_id, "imported": len(groups), "skipped": skipped}


def last_import(db: sqlite3.Connection, export_id: str, result_sha256: str) -> dict[str, Any] | None:
    read_export_summary(db, export_id)
    row = db.execute("SELECT imported,skipped,snapshot_id,created_at FROM workspace_imports WHERE export_id=? AND result_sha=? ORDER BY created_at DESC,id DESC LIMIT 1",
                     (export_id, result_sha256)).fetchone()
    if not row:
        return None
    snapshot = next((item for item in list_snapshots(db) if item["id"] == row[2]), None)
    if not snapshot:
        raise WorkspaceError("Import receipt snapshot is unavailable", "snapshot_failed")
    return {"imported": row[0], "skipped": row[1], "snapshot": snapshot, "createdAt": row[3]}
