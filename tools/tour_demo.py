#!/usr/bin/env python3
# Copyright (C) 2026 Serkan Bekdemir
# SPDX-License-Identifier: AGPL-3.0-only
"""Run an isolated, foreground app/mock pair for documentation screenshots."""
import argparse
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-port", type=int, default=18776)
    parser.add_argument("--mock-port", type=int, default=18777)
    parser.add_argument("--runtime-dir", type=Path)
    args = parser.parse_args()
    if args.app_port == args.mock_port:
        parser.error("App and mock ports must differ.")
    for port in (args.app_port, args.mock_port):
        if not 1024 <= port <= 65535:
            parser.error("Use ports between 1024 and 65535.")
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))  # Refuse collisions before starting anything.
    runtime = args.runtime_dir.resolve() if args.runtime_dir else Path(tempfile.mkdtemp(prefix="people-workbench-tour-"))
    if str(runtime).startswith("\\\\"):
        parser.error("Use a Windows-local runtime for SQLite, not a UNC share.")
    runtime.mkdir(parents=True, exist_ok=True)
    state_dir = runtime / "synthetic-state"
    if state_dir.exists():
        parser.error("Runtime already contains state. Choose a fresh directory; existing contents are retained.")
    app_dir = runtime / "app"
    app_dir.mkdir(exist_ok=True)
    shutil.copy2(ROOT / "people_workbench.py", app_dir / "people_workbench.py")
    shutil.copytree(ROOT / "web", app_dir / "web", dirs_exist_ok=True)
    commands = {
        "mock": [sys.executable, "-u", str(ROOT / "tools/tour_mock.py"), "--port", str(args.mock_port)],
        "app": [sys.executable, "-u", str(app_dir / "people_workbench.py"), "--port", str(args.app_port),
                "--state-dir", str(state_dir), "--no-browser"],
    }
    children, logs = [], []
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        for name, command in commands.items():
            log = (runtime / (name + ".log")).open("w")
            logs.append(log)
            child = subprocess.Popen(command, cwd=runtime, stdout=log, stderr=log,
                                     creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
            children.append(child)
        for port, path in ((args.mock_port, "/__review__/audit"), (args.app_port, "/login")):
            deadline = time.monotonic() + 20
            while True:
                if any(child.poll() is not None for child in children):
                    raise RuntimeError("A demo process exited; inspect logs in " + str(runtime))
                try:
                    with opener.open(f"http://127.0.0.1:{port}{path}", timeout=1):
                        break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Demo did not become ready; inspect logs in " + str(runtime))
                    time.sleep(0.2)
        (runtime / "tour-run.json").write_text(json.dumps({
            "appPort": args.app_port, "mockPort": args.mock_port,
            "source": str(ROOT), "stateDir": str(state_dir),
            "snapshot": "people_workbench.py and web copied unchanged at launch"
        }, indent=2) + "\n")
        print(f"App: http://127.0.0.1:{args.app_port}/", flush=True)
        print(f"Mock Immich URL: http://127.0.0.1:{args.mock_port}", flush=True)
        print("Synthetic sign-in: review@example.test / synthetic-review", flush=True)
        print(f"Runtime and logs: {runtime}", flush=True)
        print("Keep this launcher open. Ctrl+C stops only these two child processes.", flush=True)
        while all(child.poll() is None for child in children):
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        for log in logs:
            log.close()

if __name__ == "__main__":
    main()