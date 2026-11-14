#!/usr/bin/env python3
# Copyright (C) 2026 Serkan Bekdemir
# SPDX-License-Identifier: AGPL-3.0-only
"""Copy a prepared offline workspace and app into a new portable folder.

Stop Workbench before exporting. No Immich connection or credential is used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ("people_workbench.py", "offline_workspace.py", "workspace_state.py",
           "workspace_packages.py", "workspace_import.py", "LICENSE")
WEB_FILES = (
    "index.html", "login.html", "styles.css", "login-key.css", "app.js",
    "login.js", "navigation.js", "asset-preview.js", "browser-storage.js",
    "face-review.js", "group-photos.js", "investigate.js", "merge.js",
    "naming.js", "pending.js", "offline.js",
)
ITEM_ID = re.compile(r"^[0-9a-fA-F-]{20,50}$")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def contained_file(root, relative):
    relative = Path(relative)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Workspace contains an unsafe media path")
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root.resolve()) or not candidate.is_file():
        raise ValueError("Workspace media is missing or outside its snapshot")
    return candidate


def export_workspace(destination, state_dir, source_root=ROOT):
    """Publish a fresh portable folder, preserving the source and existing targets."""
    source_root, state_dir = Path(source_root).resolve(), Path(state_dir).resolve()
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise ValueError("Destination already exists; choose a new folder")
    if destination.resolve().is_relative_to(state_dir) or state_dir.is_relative_to(destination.resolve()):
        raise ValueError("Destination must be separate from the source state directory")
    if not destination.parent.is_dir():
        raise ValueError("Create the destination's parent directory first")
    active_path = state_dir / "offline" / "active.json"
    active = read_json(active_path)
    if active.get("stale"):
        raise ValueError("Saved snapshot needs refreshing after Sync; prepare it again before exporting")
    snapshot_id = active.get("id")
    if not isinstance(snapshot_id, str) or not ITEM_ID.fullmatch(snapshot_id):
        raise ValueError("No valid prepared offline workspace")
    snapshot_root = state_dir / "offline" / "snapshots" / snapshot_id
    if snapshot_root.is_symlink() or not snapshot_root.resolve().is_relative_to((state_dir / "offline").resolve()):
        raise ValueError("Workspace snapshot is outside its data directory")
    snapshot = read_json(snapshot_root / "snapshot.json")
    if snapshot.get("formatVersion") != 1 or snapshot.get("id") != snapshot_id:
        raise ValueError("Unsupported offline workspace format")
    originals = snapshot.get("media", {}).get("originals", {})
    if set(originals) != set(snapshot.get("assets", {})):
        raise ValueError("Workspace originals are incomplete; finish preparation first")
    media = []
    for kind in ("originals", "previews", "people"):
        for item in snapshot.get("media", {}).get(kind, {}).values():
            file = contained_file(snapshot_root, item["path"])
            if file.stat().st_size != item["size"]:
                raise ValueError("Workspace media size changed; prepare again")
            media.append((file, item))
    required = [*(source_root / name for name in RUNTIME),
                *(source_root / "web" / name for name in WEB_FILES)]
    if any(not file.is_file() or file.is_symlink() for file in required):
        raise ValueError("Application runtime files are missing or unsafe")
    required_bytes = sum(file.stat().st_size for file, _ in media) + (state_dir / "state.sqlite3").stat().st_size
    if shutil.disk_usage(destination.parent).free < required_bytes + 64 * 1024 * 1024:
        raise ValueError("Not enough free space for the portable workspace")
    staging = Path(tempfile.mkdtemp(prefix=".people-workbench-export-", dir=destination.parent))
    try:
        for file in required:
            target = staging / file.relative_to(source_root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(file, target)
        target_state = staging / ".people-workbench"
        target_state.mkdir()
        # SQLite backup includes committed WAL pages without copying a live WAL.
        source = sqlite3.connect((state_dir / "state.sqlite3").as_uri() + "?mode=ro", uri=True)
        target = sqlite3.connect(target_state / "state.sqlite3")
        try:
            source.backup(target)
            owner = target.execute("SELECT value FROM settings WHERE key='owner'").fetchone()
            if not owner or json.loads(owner[0]) != snapshot.get("owner"):
                raise ValueError("Workspace and saved progress belong to different sources")
            baseline_id = target.execute("SELECT value FROM settings WHERE key='offlineBaseline'").fetchone()
            if baseline_id and baseline_id[0] and baseline_id[0] != snapshot_id:
                raise ValueError("Pending work references a different offline snapshot")
        finally:
            target.close()
            source.close()
        target_snapshot = target_state / "offline" / "snapshots" / snapshot_id
        for file, item in media:
            target_file = target_snapshot / item["path"]
            target_file.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            with file.open("rb") as reader, target_file.open("xb") as writer:
                for chunk in iter(lambda: reader.read(1024 * 1024), b""):
                    digest.update(chunk)
                    writer.write(chunk)
            if item.get("sha256") and digest.hexdigest() != item["sha256"]:
                raise ValueError("Workspace media checksum changed; prepare again")
        target_snapshot.mkdir(parents=True, exist_ok=True)
        (target_snapshot / "snapshot.json").write_text(json.dumps(snapshot), encoding="utf-8")
        baseline_path = snapshot_root / "baseline.json"
        if baseline_path.exists():
            baseline = read_json(contained_file(snapshot_root, "baseline.json"))
            if not isinstance(baseline, dict) or set(baseline) != {"people", "faces"}:
                raise ValueError("Offline reconciliation data is invalid")
            (target_snapshot / "baseline.json").write_text(json.dumps(baseline), encoding="utf-8")
        (target_state / "offline" / "active.json").write_text(json.dumps(active), encoding="utf-8")
        (staging / "START_HERE.txt").write_text(
            "Start with Python 3.10 or newer:\npython people_workbench.py\n\n"
            "Choose Open saved offline workspace on the connection page.\n"
            "Close Workbench before moving this folder. Keep one active copy.\n",
            encoding="utf-8",
        )
        if read_json(active_path) != active:
            raise ValueError("Workspace changed during export; stop Workbench and retry")
        # Never overwrite an existing directory, including one created meanwhile.
        if destination.exists() or destination.is_symlink():
            raise ValueError("Destination appeared during export; choose a new folder")
        staging.rename(destination)
        return destination
    except Exception as exc:
        # Retain partial data for inspection; never recursively delete a computed destination.
        raise RuntimeError(f"Export not completed. Temporary copy retained at {staging}: {exc}") from exc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path, help="New folder on the target drive")
    parser.add_argument("--state-dir", type=Path, default=ROOT / ".people-workbench")
    args = parser.parse_args()
    try:
        folder = export_workspace(args.destination, args.state_dir)
    except (ValueError, OSError, RuntimeError, sqlite3.Error) as exc:
        parser.exit(1, f"{exc}\n")
    print(f"Portable workspace ready: {folder}\nStart people_workbench.py there with Python 3.10 or newer.")


if __name__ == "__main__":
    main()
