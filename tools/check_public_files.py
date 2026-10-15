#!/usr/bin/env python3
"""Check tracked public paths and local links in README/docs (stdlib only)."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
MD_LINK = re.compile(r"!?\[[^\]]*\]\((?:<([^>]+)>|([^\s)]+))(?:\s+[^)]*)?\)")
HTML_LINK = re.compile(r"<(?:a|img)\b[^>]*?\b(?:href|src)=[\"']([^\"']+)[\"']", re.I)
PRIVATE_NAMES = {"state.sqlite3"}


def tracked_paths() -> list[str]:
    result = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True)
    return [item.decode("utf-8", "surrogateescape") for item in result.stdout.split(b"\0") if item]


def validate_paths(paths: list[str]) -> list[str]:
    errors = []
    for raw in paths:
        path = PurePosixPath(raw.replace("\\", "/"))
        parts = {part.lower() for part in path.parts}
        if parts & {"var", ".people-workbench", ".people-console"}:
            errors.append(f"private or ignored path is tracked: {raw}")
        name = path.name.lower()
        if (name.startswith(".env") and name != ".env.example") or name in PRIVATE_NAMES or name.endswith((".sqlite", ".sqlite3", ".db", ".pem", ".key")):
            errors.append(f"private state, environment, or key file is tracked: {raw}")
    return errors


def links(text: str):
    for match in MD_LINK.finditer(text):
        yield match.group(1) or match.group(2)
    for match in HTML_LINK.finditer(text):
        yield match.group(1)


def is_documentation(path: PurePosixPath) -> bool:
    return path.as_posix().lower() == "readme.md" or (path.parts and path.parts[0].lower() == "docs")


def validate_links(paths: list[str]) -> list[str]:
    errors = []
    for raw in paths:
        path = PurePosixPath(raw.replace("\\", "/"))
        if not is_documentation(path) or path.suffix.lower() != ".md":
            continue
        source = ROOT.joinpath(*path.parts)
        try:
            text = source.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            errors.append(f"cannot read tracked documentation: {raw}: {exc}")
            continue
        for target in links(text):
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            candidate = (source.parent / unquote(parsed.path)).resolve()
            try:
                candidate.relative_to(ROOT.resolve())
            except ValueError:
                errors.append(f"link escapes repository: {raw}: {target}")
                continue
            if not candidate.exists():
                errors.append(f"missing local link target: {raw}: {target}")
    return errors


def main() -> int:
    try:
        paths = tracked_paths()
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"Cannot read Git tracked-file list: {exc}", file=sys.stderr)
        return 2
    errors = validate_paths(paths) + validate_links(paths)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    docs_count = sum(is_documentation(PurePosixPath(path.replace("\\", "/"))) and path.lower().endswith(".md") for path in paths)
    print(f"Checked {len(paths)} tracked paths and local links in {docs_count} docs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
