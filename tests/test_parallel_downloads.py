# Copyright (C) 2026 Serkan Bekdemir
# SPDX-License-Identifier: AGPL-3.0-only
"""Synthetic transfer, quota, drain and real server shutdown boundaries."""
import copy
import errno
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import selectors
import subprocess
import sys
import threading
import time
import unittest
from unittest import mock

from test_offline_workspaces import OfflineFixture, SyntheticImmich, A1, A2, OWNER
import people_workbench as app
import offline_workspace as offline


class ParallelTests(OfflineFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.manager.start_plan(self.client)
        self.assertEqual(self.wait_job(), "planned")

    def expand(self, count=9):
        template = copy.deepcopy(self.manager.plan["assets"][A1])
        self.manager.plan["assets"] = {
            f"00000000-0000-4000-8000-{i:012d}": dict(template, id=f"00000000-0000-4000-8000-{i:012d}")
            for i in range(count)}

    def start(self):
        self.manager.start_download(self.client, self.manager.job["planId"])

    def no_media_threads(self):
        self.assertFalse(any(t.name.startswith("offline-media") for t in threading.enumerate()))

    def test_three_workers_and_stage_progress_are_bounded_and_durable(self):
        self.expand()
        gate = threading.Event()
        three = threading.Event()
        lock = threading.Lock()
        active, peak = 0, 0
        original = self.client.download_original
        def download(*args, **kwargs):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
                if active == 3:
                    three.set()
            try:
                self.assertTrue(gate.wait(3))
                return original(*args, **kwargs)
            finally:
                with lock:
                    active -= 1
        self.client.download_original = download
        self.start()
        try:
            self.assertTrue(three.wait(3))
            self.assertEqual(self.manager.job["step"], "original")
            self.assertEqual(self.manager.job["stepTotal"], 9)
            self.assertEqual(self.manager.job["stepDone"], 0)
            self.assertEqual(len(self.client.download_calls), 0)
        finally:
            gate.set()
        self.assertEqual(self.wait_job(), "ready")
        self.assertEqual(peak, 3)
        self.assertEqual(self.manager.job["downloadedFiles"], 9)
        self.assertEqual(self.manager.job["stepTotal"], 0)
        saved = json.loads((self.manager.root / "plan.json").read_text())
        self.assertEqual(len(saved["media"]["originals"]), 9)
        for item in saved["media"]["originals"].values():
            self.assertEqual(item["sha256"], hashlib.sha256(b"original").hexdigest())
        self.no_media_threads()
        self.assertEqual(self.client.writes, [])

    def test_preview_failure_preserves_all_originals_and_current_stage_counts(self):
        self.expand()
        entered = threading.Event()
        release = threading.Event()
        def preview(*_args):
            entered.set()
            self.assertTrue(release.wait(3))
            raise app.UpstreamError(403)
        self.client.request = preview
        self.start()
        try:
            self.assertTrue(entered.wait(3))
            self.assertEqual(self.manager.job["downloadedFiles"], 9)
            self.assertEqual(self.manager.job["step"], "preview")
            self.assertEqual(self.manager.job["stepDone"], 0)
            self.assertEqual(self.manager.job["stepTotal"], 9)
            saved = json.loads((self.manager.root / "plan.json").read_text())
            self.assertEqual(len(saved["media"]["originals"]), 9)
        finally:
            release.set()
        self.assertEqual(self.wait_job(), "error")
        self.assertEqual(self.manager.job["errorCode"], "upstream_http_403")
        self.no_media_threads()
        self.client.request = lambda *_: (b"preview", "image/jpeg")
        self.start()
        self.assertEqual(self.wait_job(), "ready")
        self.assertEqual(len(self.client.download_calls), 9)

    def test_error_stops_new_submissions_and_drains_successful_originals(self):
        self.expand()
        started = threading.Barrier(3)
        completed = threading.Barrier(3)
        held = threading.Event()
        failed = threading.Event()
        lock = threading.Lock()
        calls = []
        original = self.client.download_original
        def download(aid, *args, **kwargs):
            with lock:
                calls.append(aid)
                index = len(calls)
            started.wait(3)
            if index == 1:
                completed.wait(3)
                failed.set()
                raise app.UpstreamError(429)
            # Model an already completed response whose return is delayed.
            result = original(aid, *args, **kwargs)
            completed.wait(3)
            self.assertTrue(held.wait(3))
            return result
        self.client.download_original = download
        self.start()
        try:
            self.assertTrue(failed.wait(3))
            time.sleep(.03)
            self.assertTrue(self.manager.worker.is_alive())
            self.assertEqual(self.manager.job["phase"], "downloading")
            self.assertEqual(len(calls), 3)
        finally:
            held.set()
        self.assertEqual(self.wait_job(), "error")
        self.assertEqual(self.manager.job["errorCode"], "upstream_http_429")
        self.assertEqual(len(calls), 3)
        self.assertEqual(self.manager.job["downloadedFiles"], 2)
        self.assertIsNone(self.manager.active())
        self.no_media_threads()

    def test_cancel_waits_for_buffered_preview_and_does_not_publish_or_write_late(self):
        entered, release = threading.Event(), threading.Event()
        def preview(*args):
            entered.set()
            self.assertTrue(release.wait(3))
            return b"preview", "image/jpeg"
        self.client.request = preview
        self.start()
        try:
            self.assertTrue(entered.wait(3))
            self.manager.cancel()
            self.manager.worker.join(.03)
            self.assertTrue(self.manager.worker.is_alive())
            self.assertEqual(self.manager.job["phase"], "cancelling")
        finally:
            release.set()
        self.assertEqual(self.wait_job(), "cancelled")
        self.assertEqual(self.manager.plan["media"]["previews"], {})
        self.assertIsNone(self.manager.active())
        self.assertFalse(list(self.manager.root.rglob("*.part")))
        self.no_media_threads()

    def test_connection_rotation_midstream_stops_without_activation(self):
        entered, release = threading.Event(), threading.Event()
        original = self.client.download_original
        def download(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(3))
            return original(*args, **kwargs)
        self.client.download_original = download
        self.start()
        try:
            self.assertTrue(entered.wait(3))
            self.state.client = SyntheticImmich()
        finally:
            release.set()
        self.assertEqual(self.wait_job(), "error")
        self.assertEqual(self.manager.job["errorCode"], "connection_changed")
        self.assertEqual(self.manager.job["downloadedFiles"], 0)
        self.assertIsNone(self.manager.active())
        self.no_media_threads()

    def test_progress_write_failure_drains_and_preserves_original_diagnostic(self):
        self.expand()
        with mock.patch.object(self.manager, "_persist_plan", side_effect=PermissionError(errno.EACCES, "private")):
            self.start()
            self.assertEqual(self.wait_job(), "error")
        self.assertEqual(self.manager.job["step"], "progress")
        self.assertEqual(self.manager.job["errorCode"], "storage_not_writable")
        self.assertNotIn("private", self.manager.job["error"])
        self.no_media_threads()

    def test_close_fences_admission_and_drains_to_deadline(self):
        entered, release = threading.Event(), threading.Event()
        original = self.client.download_original
        def download(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(3))
            return original(*args, **kwargs)
        self.client.download_original = download
        self.start()
        try:
            self.assertTrue(entered.wait(3))
            self.assertFalse(self.manager.close(time.monotonic() + .02))
            self.assertEqual(self.manager.job["phase"], "cancelling")
            with self.assertRaisesRegex(offline.OfflineError, "shutting down"):
                self.manager.start_plan(self.client)
        finally:
            release.set()
        self.assertTrue(self.manager.close(time.monotonic() + 3))
        self.assertEqual(self.manager.job["phase"], "cancelled")
        self.assertIsNone(self.manager.active())
        self.no_media_threads()

    def test_person_404_is_optional_but_429_is_fatal_without_retry(self):
        original = self.client.request
        calls = []
        def request(method, path):
            calls.append(path)
            if path.startswith("/people/"):
                raise app.UpstreamError(404)
            return original(method, path)
        self.client.request = request
        self.start()
        self.assertEqual(self.wait_job(), "ready")
        self.manager.start_plan(self.client)
        self.assertEqual(self.wait_job(), "planned")
        def throttled(method, path):
            if path.startswith("/people/"):
                calls.append("throttled")
                raise app.UpstreamError(429)
            return original(method, path)
        self.client.request = throttled
        previous = self.manager.active()["id"]
        self.start()
        self.assertEqual(self.wait_job(), "error")
        self.assertEqual(calls.count("throttled"), 1)
        self.assertEqual(self.manager.job["errorCode"], "upstream_http_429")
        self.assertEqual(self.manager.active()["id"], previous)


class BudgetTests(unittest.TestCase):
    def test_partial_cancel_releases_aggregate_charge_and_keeps_existing_original(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / A1
            path.write_bytes(b"previous")
            event = threading.Event()
            budget = offline.DownloadBudget(4, lambda: None)
            write = budget.write
            def cancel_after_write(*args, **kwargs):
                write(*args, **kwargs)
                event.set()
            budget.write = cancel_after_write
            transport = app.Immich("https://synthetic.example")
            response = io.BytesIO(b"synthetic-body")
            response.headers = mock.Mock()
            response.headers.get.return_value = None
            response.headers.get_content_type.return_value = "image/jpeg"
            transport.opener.open = lambda *_args, **_kwargs: response
            with self.assertRaises(offline.Cancelled):
                transport.download_original(A1, path, event, 1000, 0, budget=budget)
            self.assertEqual(budget.total, 4)
            self.assertEqual(path.read_bytes(), b"previous")
            self.assertFalse(path.with_suffix(".part").exists())

    def test_shared_workspace_limit_known_and_unknown_lengths(self):
        import tempfile
        for length in ("8", None):
            with self.subTest(length=length), tempfile.TemporaryDirectory() as directory:
                transport = app.Immich("https://synthetic.example")
                barrier = threading.Barrier(2)
                def response(*_, **__):
                    body = io.BytesIO(b"original")
                    body.headers = mock.Mock()
                    body.headers.get.return_value = length
                    body.headers.get_content_type.return_value = "image/jpeg"
                    barrier.wait(3)
                    return body
                transport.opener.open = response
                budget = offline.DownloadBudget(0, lambda: None)
                results = []
                def run(aid):
                    try:
                        transport.download_original(aid, Path(directory) / aid, threading.Event(), 10, 0, budget=budget)
                        results.append("complete")
                    except offline.OfflineError as exc:
                        results.append(exc.code)
                with mock.patch.object(offline, "MAX_WORKSPACE_BYTES", 12):
                    threads = [threading.Thread(target=run, args=(aid,)) for aid in (A1, A2)]
                    for thread in threads:
                        thread.start()
                    for thread in threads:
                        thread.join(3)
                        self.assertFalse(thread.is_alive())
                self.assertCountEqual(results, ["complete", "workspace_limit"])
                self.assertEqual(budget.total, 8)
                self.assertFalse(list(Path(directory).glob("*.part")))

    def test_disk_reserve_admission_cannot_be_spent_twice(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            budget = offline.DownloadBudget(0, lambda: None)
            written = 0
            class Output:
                def write(self, chunk):
                    nonlocal written
                    time.sleep(.01)
                    written += len(chunk)
                    return len(chunk)
            def disk(_):
                return mock.Mock(free=offline.RESERVE_BYTES + 10 - written)
            results = []
            def write():
                try:
                    budget.write(Output(), b"12345678", Path(directory) / "synthetic")
                    results.append("complete")
                except offline.OfflineError as exc:
                    results.append(exc.code)
            with mock.patch.object(offline.shutil, "disk_usage", side_effect=disk):
                threads = [threading.Thread(target=write) for _ in range(2)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(3)
            self.assertCountEqual(results, ["complete", "insufficient_storage"])
            self.assertEqual(written, 8)

    def test_worker_transports_keep_tls_auth_and_redirect_policy_without_sharing_openers(self):
        source = app.Immich("https://synthetic.example", "synthetic-token", True, session=True)
        workers = [source.download_transport() for _ in range(3)]
        self.assertEqual(len({id(c.opener) for c in workers + [source]}), 4)
        for worker in workers:
            self.assertEqual((worker.base, worker.credential, worker.session),
                             (source.base, source.credential, True))
            self.assertIsNotNone(worker.context)
            self.assertTrue(any(isinstance(h, app.NoCredentialRedirect) for h in worker.opener.handlers))


@unittest.skipUnless(os.name == "posix", "POSIX server signals")
class ShutdownTests(unittest.TestCase):
    def test_real_server_exits_at_deadline_with_blocked_offline_executor(self):
        self.server_shutdown(cooperative=False)

    def test_real_server_cancels_and_drains_offline_before_clean_exit(self):
        self.server_shutdown(cooperative=True)

    def server_shutdown(self, cooperative):
        import tempfile
        script = '''
import sys, threading
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / 'tests'))
from test_offline_workspaces import SyntheticImmich, OWNER
import people_workbench as app
import json
original = app.AppState
cooperative = sys.argv[2] == 'cooperative'
def state(*args):
    state = original(*args)
    state.store.set_setting('owner', json.dumps(OWNER))
    state.client = client = SyntheticImmich()
    state.offline.start_plan(client)
    state.offline.worker.join()
    entered = threading.Event()
    def blocked(aid, path, cancel, *args, **kwargs):
        entered.set()
        if cooperative:
            import time
            while not cancel.is_set():
                time.sleep(.01)
        else:
            threading.Event().wait(60)
    client.download_original = blocked
    state.offline.start_download(client, state.offline.job['planId'])
    assert entered.wait(3)
    return state
app.AppState = state
sys.argv = ['people_workbench.py', '--no-browser', '--port', '0', '--shutdown-timeout', '1.5' if cooperative else '.8', '--state-dir', sys.argv[1]]
raise SystemExit(app.main())
'''
        with tempfile.TemporaryDirectory() as directory:
            process = subprocess.Popen([sys.executable, "-u", "-c", script, directory,
                                        "cooperative" if cooperative else "blocked"],
                                       cwd=Path(__file__).resolve().parents[1],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                with selectors.DefaultSelector() as ready:
                    ready.register(process.stdout, selectors.EVENT_READ)
                    self.assertTrue(ready.select(3), "server startup timed out")
                    first = process.stdout.readline()
                self.assertIn("Immich People Workbench:", first)
                start = time.monotonic()
                process.send_signal(signal.SIGTERM)
                stdout, stderr = process.communicate(timeout=4)
                self.assertEqual(process.returncode, 0 if cooperative else 1, (stdout, stderr))
                if not cooperative:
                    self.assertIn("Shutdown deadline expired", stderr)
                else:
                    self.assertFalse((Path(directory) / "offline" / "active.json").exists())
                    saved = json.loads((Path(directory) / "offline" / "plan.json").read_text())
                    self.assertEqual(saved["job"]["phase"], "cancelled")
                self.assertLess(time.monotonic() - start, 3)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()


if __name__ == "__main__":
    unittest.main()
