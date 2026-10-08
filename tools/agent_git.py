#!/usr/bin/env python3
# Copyright (C) 2026 Serkan Bekdemir
# SPDX-License-Identifier: AGPL-3.0-only
"""Configure shared agent Git safeguards, or commit explicitly as the bot."""
import os
import subprocess
import sys
from pathlib import Path

BOT_NAME = "people-workbench-agent[bot]"
BOT_EMAIL = "335216742+people-workbench-agent[bot]@users.noreply.github.com"
ROOT = Path(__file__).resolve().parents[1]
parts = str(ROOT).split("\\")
WSL = os.name == "nt" and str(ROOT).startswith("\\\\wsl.localhost\\")
if WSL:
    distro = parts[3]
    GIT = ["wsl.exe", "-d", distro, "--exec", "git", "-C", "/" + "/".join(parts[4:])]
else:
    GIT = ["git", "-C", str(ROOT)]

def run(*args, env=None):
    return subprocess.run(GIT + list(args), check=True, stdout=subprocess.PIPE, env=env).stdout

def local_path(value):
    if WSL and value.startswith("/"):
        return Path("\\\\wsl.localhost\\" + distro + value.replace("/", "\\"))
    result = Path(value)
    return result if result.is_absolute() else ROOT / result

def configure():
    common = run("rev-parse", "--path-format=absolute", "--git-common-dir").decode().strip()
    config = common + "/people-workbench-agent.gitconfig"
    hook_override = subprocess.run(GIT + ["config", "--get", "core.hooksPath"], stdout=subprocess.PIPE).stdout.decode().strip()
    if hook_override:
        raise SystemExit("Existing core.hooksPath found; integrate the guard with those hooks instead of replacing them.")
    source = ROOT / ".githooks" / "pre-commit"
    content = source.read_bytes()
    hooks = local_path(common) / "hooks"
    for name in ("pre-commit", "pre-merge-commit"):
        destination = hooks / name
        if destination.exists() and destination.read_bytes() != content:
            raise SystemExit("Existing hook would be overwritten: " + str(destination))
    run("config", "--file", config, "user.name", BOT_NAME)
    run("config", "--file", config, "user.email", BOT_EMAIL)
    run("config", "--local", "includeIf.onbranch:codex/**.path", config)
    hooks.mkdir(exist_ok=True)
    for name in ("pre-commit", "pre-merge-commit"):
        destination = hooks / name
        destination.write_bytes(content)
        destination.chmod(destination.stat().st_mode | 0o111)
        if WSL:
            subprocess.run(["wsl.exe", "-d", distro, "--exec", "chmod", "+x", common + "/hooks/" + name], check=True)
    print("Installed shared Codex-branch identity defaults and commit guards for this clone.")
    print("Non-Codex branches retain their existing configured identity.")

def commit(args):
    branch = run("symbolic-ref", "--quiet", "--short", "HEAD").decode().strip()
    if not branch.startswith("codex/"):
        raise SystemExit("Agent commits require a codex/ task branch; do not commit to main.")
    if any(arg.startswith("--author") or arg == "--reset-author" for arg in args):
        raise SystemExit("Author overrides are not accepted by the agent commit helper.")
    staged = run("diff", "--cached", "--name-only", "-z").decode().split("\0")
    if any("var" in Path(name).parts for name in staged if name):
        raise SystemExit("var/ must never be committed. Unstage those paths; keep the files on disk.")
    env = os.environ.copy()
    env.update(GIT_AUTHOR_NAME=BOT_NAME, GIT_AUTHOR_EMAIL=BOT_EMAIL,
               GIT_COMMITTER_NAME=BOT_NAME, GIT_COMMITTER_EMAIL=BOT_EMAIL)
    # WSL passes only explicitly listed environment variables from Windows.
    if WSL:
        names = ["GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"]
        env["WSLENV"] = ":".join(filter(None, [env.get("WSLENV", ""), *names]))
    result = subprocess.run(GIT + ["-c", "user.name=" + BOT_NAME, "-c", "user.email=" + BOT_EMAIL, "commit", *args], env=env)
    if result.returncode:
        raise SystemExit(result.returncode)
    metadata = run("show", "-s", "--format=%an%n%ae%n%cn%n%ce", "HEAD").decode().splitlines()
    if metadata != [BOT_NAME, BOT_EMAIL, BOT_NAME, BOT_EMAIL]:
        raise SystemExit("Commit identity verification failed; stop before pushing.")
    print("Verified bot author and committer.")

if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "configure":
        configure()
    elif len(sys.argv) > 1 and sys.argv[1] == "commit":
        commit(sys.argv[2:])
    else:
        raise SystemExit("Usage: python3 tools/agent_git.py configure | commit [git commit options]")