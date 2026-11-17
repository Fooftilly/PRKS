"""The Library access gate and how each request is classified (no database, no server).

Reads overlap, mutations serialize, a backup waits out a mutation and holds
off later ones, a restore waits for everything and starves new reads, and
every scope is released when its body raises. The live HTTP behaviour on the
PostgreSQL Library is tests/pg/test_http_concurrency.py.
"""
import os
import re
import sys
import threading
import time
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_DIR)

from backend.concurrency import LibraryAccessGate, request_access_mode

_FORBIDDEN_SAME_THREAD = re.compile(r"check_same_thread\s*=\s*False")


def _py_files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in filenames:
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


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
        # The path-based inbox's rescan wrote while it read; it is gone
        # (PG-C2 #641), so no query makes a GET a mutation any more.
        self.assertEqual(request_access_mode("GET", "/api/processing-files", "rescan=1"), "read")
        self.assertEqual(request_access_mode("GET", "/api/processing-items", "rescan=true"), "read")
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


if __name__ == "__main__":
    unittest.main()
