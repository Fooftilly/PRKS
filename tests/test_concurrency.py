"""Library access gate and threaded HTTP concurrency."""
import http.client
import json
import os
import re
import shutil
import socket
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_DIR)

from run_tests import apply_isolated_test_env

apply_isolated_test_env(_PROJECT_DIR)

from backend.backup_restore import CONFIRM_RESTORE, create_backup, stage_restore
from backend.concurrency import LibraryAccessGate, request_access_mode
from backend.storage.config import StorageConfig
from backend.text_index import get_text_index, reset_text_index
import backend.server as server_module

_FORBIDDEN_SAME_THREAD = re.compile(r"check_same_thread\s*=\s*False")


def _find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return sock.getsockname()[1]


def _capture_bind():
    try:
        previous_index = get_text_index()
    except RuntimeError:
        previous_index = None
    return (
        server_module._bound_storage,
        server_module.pdfs_dir,
        server_module.thumbs_dir,
        server_module.processing_dir,
        server_module.db,
        server_module.text_index,
        previous_index,
    )


def _restore_bind(snapshot):
    (
        server_module._bound_storage,
        server_module.pdfs_dir,
        server_module.thumbs_dir,
        server_module.processing_dir,
        server_module.db,
        server_module.text_index,
        previous_index,
    ) = snapshot
    if previous_index is None:
        reset_text_index()
    else:
        from backend.text_index import replace_text_index

        replace_text_index(previous_index)


def _py_files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in filenames:
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


class TrackingGate(LibraryAccessGate):
    instances = []

    def __init__(self):
        super().__init__()
        TrackingGate.instances.append(self)


class RecordingServer(server_module.PRKSThreadingTCPServer):
    instances = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        RecordingServer.instances.append(self)


def _start_worker(fn, *args, **kwargs):
    errors = []

    def run():
        try:
            fn(*args, **kwargs)
        except Exception:
            errors.append(sys.exc_info())

    thread = threading.Thread(target=run)
    thread.start()
    return thread, errors


def _join_workers(*pairs, timeout=5):
    first = None
    for thread, errors in pairs:
        thread.join(timeout)
        if first is None:
            if errors:
                first = errors[0]
            elif thread.is_alive():
                first = (None, AssertionError("worker thread still alive"), None)
    if first is not None:
        _typ, exc, tb = first
        if tb is not None:
            raise exc.with_traceback(tb)
        raise exc


class RequestAccessModeTests(unittest.TestCase):
    def test_static_and_defaults(self):
        self.assertIsNone(request_access_mode("GET", "/index.html"))
        self.assertIsNone(request_access_mode("GET", "/vendor/app.js"))
        self.assertEqual(request_access_mode("GET", "/api/works"), "read")
        self.assertEqual(request_access_mode("HEAD", "/api/pdfs/a.pdf"), "read")
        self.assertEqual(request_access_mode("POST", "/api/works"), "mutation")
        self.assertEqual(request_access_mode("PATCH", "/api/works/W-1"), "mutation")
        self.assertEqual(request_access_mode("PUT", "/api/concepts/C-1/parents"), "mutation")
        self.assertEqual(request_access_mode("DELETE", "/api/works/W-1"), "mutation")
        self.assertEqual(request_access_mode("POST", "/api/new-endpoint"), "mutation")

    def test_exceptions(self):
        self.assertEqual(request_access_mode("GET", "/api/processing-files"), "read")
        self.assertEqual(
            request_access_mode("GET", "/api/processing-files", "rescan=1"),
            "mutation",
        )
        self.assertEqual(
            request_access_mode("GET", "/api/processing-files", "rescan=true"),
            "mutation",
        )
        self.assertEqual(
            request_access_mode("GET", "/api/persons/P-1/profile-image"),
            "mutation",
        )
        self.assertEqual(
            request_access_mode("GET", "/api/works/W-1/thumbnail"),
            "mutation",
        )
        self.assertEqual(request_access_mode("GET", "/api/backups/download"), "read")
        self.assertEqual(request_access_mode("POST", "/api/backups/progress"), "backup")
        self.assertEqual(request_access_mode("POST", "/api/backups/restore"), "restore")
        self.assertEqual(request_access_mode("POST", "/api/backups/stage"), "read")


class LibraryAccessGateTests(unittest.TestCase):
    def test_concurrent_reads(self):
        gate = LibraryAccessGate()
        a_inside = threading.Event()
        release_a = threading.Event()
        b_inside = threading.Event()

        def read_a():
            with gate.read():
                a_inside.set()
                self.assertTrue(release_a.wait(5))

        def read_b():
            with gate.read():
                b_inside.set()

        t_a, err_a = _start_worker(read_a)
        self.assertTrue(a_inside.wait(5))
        t_b, err_b = _start_worker(read_b)
        self.assertTrue(b_inside.wait(5))
        release_a.set()
        _join_workers((t_a, err_a), (t_b, err_b))

    def test_mutations_serialize(self):
        gate = LibraryAccessGate()
        a_inside = threading.Event()
        release_a = threading.Event()
        b_inside = threading.Event()
        lock = threading.Lock()
        active = 0
        max_active = 0

        def mutation(hold):
            nonlocal active, max_active
            with gate.mutation():
                with lock:
                    active += 1
                    max_active = max(max_active, active)
                if hold:
                    a_inside.set()
                    self.assertTrue(release_a.wait(5))
                else:
                    b_inside.set()
                with lock:
                    active -= 1

        t_a, err_a = _start_worker(mutation, True)
        self.assertTrue(a_inside.wait(5))
        t_b, err_b = _start_worker(mutation, False)
        self.assertFalse(b_inside.wait(0.05))
        self.assertEqual(gate.snapshot()["mutation_active"], True)
        release_a.set()
        self.assertTrue(b_inside.wait(5))
        _join_workers((t_a, err_a), (t_b, err_b))
        self.assertEqual(max_active, 1)

    def test_backup_waits_for_mutation_and_blocks_later_mutation(self):
        gate = LibraryAccessGate()
        mut_a_inside = threading.Event()
        release_a = threading.Event()
        backup_inside = threading.Event()
        release_backup = threading.Event()
        mut_b_inside = threading.Event()

        def mut_a():
            with gate.mutation():
                mut_a_inside.set()
                self.assertTrue(release_a.wait(5))

        def do_backup():
            with gate.backup():
                backup_inside.set()
                self.assertTrue(release_backup.wait(5))

        def mut_b():
            with gate.mutation():
                mut_b_inside.set()

        t_a, err_a = _start_worker(mut_a)
        self.assertTrue(mut_a_inside.wait(5))
        t_backup, err_backup = _start_worker(do_backup)
        self.assertTrue(gate.wait_until(lambda s: s["backup_waiting"] >= 1))
        self.assertFalse(backup_inside.is_set())
        t_b, err_b = _start_worker(mut_b)
        self.assertFalse(mut_b_inside.wait(0.05))
        release_a.set()
        self.assertTrue(backup_inside.wait(5))
        self.assertFalse(mut_b_inside.is_set())
        release_backup.set()
        self.assertTrue(mut_b_inside.wait(5))
        _join_workers((t_a, err_a), (t_backup, err_backup), (t_b, err_b))

    def test_backup_allows_reads_blocks_mutations(self):
        gate = LibraryAccessGate()
        backup_inside = threading.Event()
        release_backup = threading.Event()
        read_done = threading.Event()
        mut_inside = threading.Event()

        def do_backup():
            with gate.backup():
                backup_inside.set()
                self.assertTrue(release_backup.wait(5))

        def do_read():
            with gate.read():
                read_done.set()

        def do_mut():
            with gate.mutation():
                mut_inside.set()

        t_backup, err_backup = _start_worker(do_backup)
        self.assertTrue(backup_inside.wait(5))
        t_read, err_read = _start_worker(do_read)
        self.assertTrue(read_done.wait(5))
        t_mut, err_mut = _start_worker(do_mut)
        self.assertFalse(mut_inside.wait(0.05))
        release_backup.set()
        self.assertTrue(mut_inside.wait(5))
        _join_workers((t_backup, err_backup), (t_read, err_read), (t_mut, err_mut))

    def test_restore_waits_for_read_and_starves_new_reads(self):
        gate = LibraryAccessGate()
        read_a_inside = threading.Event()
        release_a = threading.Event()
        restore_inside = threading.Event()
        release_restore = threading.Event()
        read_b_inside = threading.Event()

        def read_a():
            with gate.read():
                read_a_inside.set()
                self.assertTrue(release_a.wait(5))

        def do_restore():
            with gate.restore():
                restore_inside.set()
                self.assertTrue(release_restore.wait(5))

        def read_b():
            with gate.read():
                read_b_inside.set()

        t_a, err_a = _start_worker(read_a)
        self.assertTrue(read_a_inside.wait(5))
        t_restore, err_restore = _start_worker(do_restore)
        self.assertTrue(gate.wait_until(lambda s: s["restore_waiting"] >= 1))
        self.assertFalse(restore_inside.is_set())
        t_b, err_b = _start_worker(read_b)
        self.assertFalse(read_b_inside.wait(0.05))
        release_a.set()
        self.assertTrue(restore_inside.wait(5))
        self.assertFalse(read_b_inside.is_set())
        release_restore.set()
        self.assertTrue(read_b_inside.wait(5))
        _join_workers((t_a, err_a), (t_restore, err_restore), (t_b, err_b))

    def test_restore_waits_for_mutation_and_backup(self):
        gate = LibraryAccessGate()
        for holder in ("mutation", "backup"):
            with self.subTest(holder=holder):
                held = threading.Event()
                release_held = threading.Event()
                restore_inside = threading.Event()

                def hold(kind=holder):
                    ctx = gate.mutation() if kind == "mutation" else gate.backup()
                    with ctx:
                        held.set()
                        self.assertTrue(release_held.wait(5))

                def do_restore():
                    with gate.restore():
                        restore_inside.set()

                t_hold, err_hold = _start_worker(hold)
                self.assertTrue(held.wait(5))
                t_restore, err_restore = _start_worker(do_restore)
                self.assertTrue(gate.wait_until(lambda s: s["restore_waiting"] >= 1))
                self.assertFalse(restore_inside.is_set())
                release_held.set()
                self.assertTrue(restore_inside.wait(5))
                _join_workers((t_hold, err_hold), (t_restore, err_restore))

    def test_exception_releases_every_scope(self):
        gate = LibraryAccessGate()
        for name in ("read", "mutation", "backup", "restore"):
            with self.assertRaises(RuntimeError):
                with getattr(gate, name)():
                    raise RuntimeError("boom")
            snap = gate.snapshot()
            self.assertEqual(snap["active_reads"], 0)
            self.assertFalse(snap["mutation_active"])
            self.assertFalse(snap["backup_active"])
            self.assertFalse(snap["restore_active"])
            self.assertEqual(snap["backup_waiting"], 0)
            self.assertEqual(snap["restore_waiting"], 0)
            with gate.read():
                pass
            with gate.mutation():
                pass


class StructuralConcurrencyGuards(unittest.TestCase):
    def test_production_has_no_check_same_thread_false(self):
        hits = []
        for path in _py_files(os.path.join(_PROJECT_DIR, "backend")):
            rel = os.path.relpath(path, _PROJECT_DIR)
            with open(path, encoding="utf-8") as handle:
                for lineno, line in enumerate(handle, 1):
                    if _FORBIDDEN_SAME_THREAD.search(line):
                        hits.append("%s:%s" % (rel, lineno))
        self.assertEqual(hits, [])


class LiveHttpConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self._prev = _capture_bind()
        self._tmpdir = tempfile.mkdtemp(prefix="prks-conc-")
        self.httpd = None
        self.thread = None
        self._gate_patch = None
        self._server_patch = None
        self.addCleanup(self._shutdown_and_restore)
        TrackingGate.instances = []
        RecordingServer.instances = []
        cfg = StorageConfig.for_testing(self._tmpdir)
        os.makedirs(cfg.pdfs_dir, exist_ok=True)
        os.makedirs(cfg.thumbs_dir, exist_ok=True)
        server_module.bind_storage(cfg)
        self.port = _find_free_port()
        self._server_patch = patch.object(
            server_module, "PRKSThreadingTCPServer", RecordingServer
        )
        self._gate_patch = patch.object(server_module, "LibraryAccessGate", TrackingGate)
        self._server_patch.start()
        self._gate_patch.start()
        self.thread = threading.Thread(
            target=server_module.run_server,
            args=(self.port, "127.0.0.1"),
            daemon=True,
        )
        self.thread.start()
        self._wait_ready()
        self.httpd = RecordingServer.instances[-1]
        self.gate = TrackingGate.instances[-1]

    def _shutdown_and_restore(self):
        try:
            if self.httpd is None:
                stop = time.time() + 2
                while self.httpd is None and time.time() < stop:
                    if RecordingServer.instances:
                        self.httpd = RecordingServer.instances[-1]
                        break
                    if self.thread is not None and not self.thread.is_alive():
                        break
                    time.sleep(0.02)
            if self.httpd is not None:
                try:
                    self.httpd.shutdown()
                except Exception:
                    pass
                try:
                    self.httpd.server_close()
                except OSError:
                    pass
            if self.thread is not None:
                self.thread.join(5)
                self.assertFalse(self.thread.is_alive())
        finally:
            if self._gate_patch is not None:
                self._gate_patch.stop()
            if self._server_patch is not None:
                self._server_patch.stop()
            _restore_bind(self._prev)
            shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _wait_ready(self, timeout=8.0):
        deadline_err = None
        stop = time.time() + timeout
        while time.time() < stop:
            try:
                status, _body = self._request("GET", "/api/works")
                if status == 200:
                    return
            except OSError as exc:
                deadline_err = exc
            time.sleep(0.05)
        raise RuntimeError("server did not start: %s" % deadline_err)

    def _request(self, method, path, body=None, timeout=10):
        payload = None
        headers = {"Host": "127.0.0.1"}
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(payload))
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        try:
            conn.request(method, path, body=payload, headers=headers)
            res = conn.getresponse()
            data = res.read()
            return res.status, data
        finally:
            conn.close()

    def _send_partial_json(self, path, payload, prefix_len=8):
        body = json.dumps(payload).encode("utf-8")
        if prefix_len >= len(body):
            prefix_len = max(1, len(body) // 2)
        started = threading.Event()
        orig = server_module.PRKSHandler._read_json_body

        def wrapped(handler):
            orig_read = handler.rfile.read

            def wait_read(n=-1):
                started.set()
                return orig_read(n)

            handler.rfile.read = wait_read
            return orig(handler)

        header = (
            "POST %s HTTP/1.1\r\n"
            "Host: 127.0.0.1\r\n"
            "Content-Type: application/json\r\n"
            "Content-Length: %s\r\n"
            "Connection: close\r\n"
            "\r\n" % (path, len(body))
        ).encode("utf-8")
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        patcher = patch.object(server_module.PRKSHandler, "_read_json_body", wrapped)
        patcher.start()
        try:
            sock.sendall(header + body[:prefix_len])
            self.assertTrue(started.wait(5))
            return sock, body[prefix_len:], patcher
        except Exception:
            patcher.stop()
            sock.close()
            raise

    def _finish_partial(self, sock, rest, timeout=10):
        sock.sendall(rest)
        sock.settimeout(timeout)
        chunks = []
        while True:
            try:
                chunk = sock.recv(4096)
            except OSError:
                break
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)

    def test_http_reads_overlap_on_separate_threads(self):
        entered = threading.Event()
        release = threading.Event()
        b_done = threading.Event()
        orig = server_module.db.get_all_works

        self.addCleanup(release.set)

        def wrapped():
            entered.set()
            self.assertTrue(release.wait(5))
            return orig()

        def req_a():
            self._request("GET", "/api/works", timeout=15)

        def req_b():
            status, body = self._request("GET", "/api/folders")
            self.assertEqual(status, 200)
            json.loads(body)
            b_done.set()

        with patch.object(server_module.db, "get_all_works", wrapped):
            t_a, err_a = _start_worker(req_a)
            self.assertTrue(entered.wait(5))
            t_b, err_b = _start_worker(req_b)
            self.assertTrue(b_done.wait(5))
            release.set()
            _join_workers((t_a, err_a), (t_b, err_b))

    def test_http_mutations_serialize(self):
        a_inside = threading.Event()
        release_a = threading.Event()
        b_inside = threading.Event()
        orig = server_module.db.add_work
        order = []
        self.addCleanup(release_a.set)

        def wrapped(*args, **kwargs):
            if not a_inside.is_set():
                a_inside.set()
                order.append("a")
                self.assertTrue(release_a.wait(5))
                return orig(*args, **kwargs)
            b_inside.set()
            order.append("b")
            return orig(*args, **kwargs)

        def post(title):
            status, _body = self._request(
                "POST",
                "/api/works",
                {"title": title},
                timeout=15,
            )
            self.assertEqual(status, 200)

        with patch.object(server_module.db, "add_work", wrapped):
            t_a, err_a = _start_worker(post, "A")
            self.assertTrue(a_inside.wait(5))
            t_b, err_b = _start_worker(post, "B")
            self.assertTrue(self.gate.wait_until(lambda s: s["mutation_active"]))
            self.assertFalse(b_inside.is_set())
            release_a.set()
            self.assertTrue(b_inside.wait(5))
            _join_workers((t_a, err_a), (t_b, err_b), timeout=15)
        self.assertEqual(order, ["a", "b"])

    def test_http_backup_blocks_mutations_allows_reads(self):
        backup_inside = threading.Event()
        release_backup = threading.Event()
        mut_inside = threading.Event()
        rescan_inside = threading.Event()
        second_backup = threading.Event()
        orig_backup = server_module.run_backup_with_progress
        orig_add = server_module.db.add_work
        orig_scan = server_module.db.scan_processing_files
        backup_calls = []
        self.addCleanup(release_backup.set)

        def wrapped_backup(*args, **kwargs):
            backup_calls.append(1)
            if not backup_inside.is_set():
                backup_inside.set()
                self.assertTrue(release_backup.wait(15))
                return orig_backup(*args, **kwargs)
            second_backup.set()
            return orig_backup(*args, **kwargs)

        def wrapped_add(*args, **kwargs):
            mut_inside.set()
            return orig_add(*args, **kwargs)

        def wrapped_scan(*args, **kwargs):
            rescan_inside.set()
            return orig_scan(*args, **kwargs)

        def run_backup():
            status, body = self._request("POST", "/api/backups/progress", {}, timeout=30)
            self.assertEqual(status, 200)
            events = [
                json.loads(line)
                for line in body.decode("utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(events[-1]["phase"], "ready")
            self.assertTrue(events[-1].get("token"))

        with (
            patch.object(server_module, "run_backup_with_progress", wrapped_backup),
            patch.object(server_module.db, "add_work", wrapped_add),
            patch.object(server_module.db, "scan_processing_files", wrapped_scan),
        ):
            t_backup, err_backup = _start_worker(run_backup)
            self.assertTrue(backup_inside.wait(10))
            status, body = self._request("GET", "/api/works")
            self.assertEqual(status, 200)
            json.loads(body)

            t_mut, err_mut = _start_worker(
                lambda: self._request("POST", "/api/works", {"title": "X"}, timeout=20)
            )
            t_rescan, err_rescan = _start_worker(
                lambda: self._request("GET", "/api/processing-files?rescan=1", timeout=20)
            )
            t_backup2, err_backup2 = _start_worker(run_backup)
            self.assertTrue(self.gate.wait_until(lambda s: s["backup_active"]))
            self.assertFalse(mut_inside.is_set())
            self.assertFalse(rescan_inside.is_set())
            self.assertFalse(second_backup.is_set())
            self.assertEqual(len(backup_calls), 1)
            release_backup.set()
            _join_workers(
                (t_backup, err_backup),
                (t_mut, err_mut),
                (t_rescan, err_rescan),
                (t_backup2, err_backup2),
                timeout=20,
            )
        self.assertTrue(mut_inside.is_set())
        self.assertTrue(rescan_inside.is_set())

    def test_http_restore_prevents_stale_binding(self):
        cfg = server_module._bound_storage
        db = server_module.db
        db.add_work(title="BEFORE")
        backup = create_backup(cfg)
        copied = os.path.join(self._tmpdir, "upload.prks-backup")
        shutil.copy2(backup.archive_path, copied)
        staged = stage_restore(cfg, copied)
        db.add_work(title="AFTER")
        titles_live = {
            row["title"] for row in db.execute_query("SELECT title FROM works")
        }
        self.assertEqual(titles_live, {"BEFORE", "AFTER"})

        apply_entered = threading.Event()
        release_apply = threading.Event()
        orig_apply = server_module.apply_restore
        seen_titles = []
        self.addCleanup(release_apply.set)

        def wrapped_apply(*args, **kwargs):
            apply_entered.set()
            self.assertTrue(release_apply.wait(15))
            return orig_apply(*args, **kwargs)

        bound_db = server_module.db
        real_get = bound_db.get_all_works

        def recording_get():
            rows = real_get()
            seen_titles.append(tuple(sorted(r["title"] for r in rows if r.get("title"))))
            return rows

        get_result = {}

        def delayed_get():
            status, body = self._request("GET", "/api/works", timeout=30)
            get_result["status"] = status
            get_result["titles"] = [
                row["title"] for row in json.loads(body.decode("utf-8"))
            ]

        def run_restore():
            status, body = self._request(
                "POST",
                "/api/backups/restore",
                {"token": staged.token, "confirm": CONFIRM_RESTORE},
                timeout=30,
            )
            get_result["restore_status"] = status
            get_result["restore_body"] = body

        with (
            patch.object(server_module, "apply_restore", wrapped_apply),
            patch.object(bound_db, "get_all_works", recording_get),
        ):
            t_restore, err_restore = _start_worker(run_restore)
            self.assertTrue(apply_entered.wait(10))
            t_get, err_get = _start_worker(delayed_get)
            self.assertTrue(self.gate.wait_until(lambda s: s["restore_active"]))
            self.assertEqual(seen_titles, [])
            release_apply.set()
            _join_workers((t_restore, err_restore), (t_get, err_get), timeout=20)
        self.assertEqual(get_result.get("restore_status"), 200)
        self.assertEqual(get_result.get("status"), 200)
        self.assertEqual(set(get_result.get("titles") or []), {"BEFORE"})
        self.assertEqual(seen_titles, [])
        self.assertNotIn("AFTER", get_result.get("titles") or [])

    def test_sqlite_thread_ownership_two_reads(self):
        barrier = threading.Barrier(2, timeout=5)
        orig = server_module.db.get_all_works
        errors = []

        def wrapped():
            barrier.wait()
            try:
                return orig()
            except sqlite3.ProgrammingError as exc:
                errors.append(exc)
                raise

        results = []

        def worker():
            status, body = self._request("GET", "/api/works", timeout=15)
            results.append(status)
            json.loads(body)

        with patch.object(server_module.db, "get_all_works", wrapped):
            t1, err1 = _start_worker(worker)
            t2, err2 = _start_worker(worker)
            _join_workers((t1, err1), (t2, err2), timeout=10)
        self.assertEqual(errors, [])
        self.assertEqual(results, [200, 200])

    def test_partial_restore_json_body_does_not_hold_gate(self):
        sock = None
        patcher = None
        restore_entered = threading.Event()
        orig_restore = server_module.PRKSHandler._handle_backup_restore

        def wrapped_restore(handler, data):
            restore_entered.set()
            return orig_restore(handler, data)

        try:
            with patch.object(
                server_module.PRKSHandler, "_handle_backup_restore", wrapped_restore
            ):
                sock, rest, patcher = self._send_partial_json(
                    "/api/backups/restore",
                    {"token": "partial-token", "confirm": CONFIRM_RESTORE},
                )
                snap = self.gate.snapshot()
                self.assertFalse(snap["restore_active"])
                self.assertEqual(snap["restore_waiting"], 0)
                self.assertFalse(restore_entered.is_set())
                status, body = self._request("GET", "/api/works")
                self.assertEqual(status, 200)
                json.loads(body)
                snap = self.gate.snapshot()
                self.assertFalse(snap["restore_active"])
                self.assertEqual(snap["restore_waiting"], 0)
                raw = self._finish_partial(sock, rest)
                self.assertTrue(restore_entered.wait(5))
                self.assertTrue(raw.startswith(b"HTTP/"))
        finally:
            if patcher is not None:
                patcher.stop()
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass

    def test_partial_mutation_json_body_does_not_set_mutation_active(self):
        sock = None
        patcher = None
        add_entered = threading.Event()
        orig_add = server_module.db.add_work

        def wrapped_add(*args, **kwargs):
            add_entered.set()
            return orig_add(*args, **kwargs)

        try:
            with patch.object(server_module.db, "add_work", wrapped_add):
                sock, rest, patcher = self._send_partial_json(
                    "/api/works",
                    {"title": "Slow Body Work"},
                )
                snap = self.gate.snapshot()
                self.assertFalse(snap["mutation_active"])
                self.assertFalse(add_entered.is_set())
                status, body = self._request("GET", "/api/works")
                self.assertEqual(status, 200)
                json.loads(body)
                snap = self.gate.snapshot()
                self.assertFalse(snap["mutation_active"])
                raw = self._finish_partial(sock, rest)
                self.assertTrue(add_entered.wait(5))
                status_line = raw.split(b"\r\n", 1)[0]
                self.assertIn(b" 200", status_line)
        finally:
            if patcher is not None:
                patcher.stop()
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass

    def test_stage_unexpected_oserror_is_internal_error_and_releases_gate(self):
        seen = {}

        def boom(_reader, dest_path, **_kwargs):
            seen["path"] = dest_path
            seen["snap"] = self.gate.snapshot()
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            with open(dest_path, "wb") as handle:
                handle.write(b"partial")
            raise OSError("forced")

        payload = b"not-a-real-backup"
        with patch.object(server_module, "stream_upload_to_file", boom):
            conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
            try:
                conn.request(
                    "POST",
                    "/api/backups/stage",
                    body=payload,
                    headers={
                        "Host": "127.0.0.1",
                        "Content-Type": "application/octet-stream",
                        "Content-Length": str(len(payload)),
                    },
                )
                res = conn.getresponse()
                status = res.status
                req_id = (res.getheader("X-Request-ID") or "").strip()
                body = res.read()
            finally:
                conn.close()

        data = json.loads(body.decode("utf-8"))
        self.assertEqual(status, 500)
        self.assertEqual(data.get("error"), "internal_error")
        self.assertTrue(req_id)
        self.assertEqual(data.get("request_id"), req_id)
        self.assertIn("path", seen)
        self.assertGreater(seen["snap"]["active_reads"], 0)
        self.assertTrue(
            self.gate.wait_until(
                lambda s: s["active_reads"] == 0
                and not s["mutation_active"]
                and not s["backup_active"]
                and not s["restore_active"]
            )
        )
        self.assertFalse(os.path.exists(seen["path"]))


if __name__ == "__main__":
    unittest.main()
