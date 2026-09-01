"""SQLite connection lifetime: close-on-exit scopes, not transaction-only with-conn."""
import os
import re
import shutil
import sys
import tempfile
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_DIR)

from run_tests import apply_isolated_test_env

apply_isolated_test_env(_PROJECT_DIR)

from backend.db_manager import PRKSDatabase
from backend.research_index import PRKSResearchIndex
from backend.storage.config import StorageConfig
from backend.text_index import PRKSTextIndex

_SCHEMA_PATH = os.path.join(_PROJECT_DIR, "backend", "db_schema.sql")

_FORBIDDEN_GET_CONNECTION = re.compile(r"with\s+.+\.get_connection\(\)\s+as\s+")
_FORBIDDEN_RAW_CONN = re.compile(r"with\s+.+\._conn\(\)\s+as\s+")


class _TrackingConnection:
    def __init__(self, conn):
        object.__setattr__(self, "_conn", conn)
        object.__setattr__(self, "closed", False)

    def close(self):
        if not self.closed:
            object.__setattr__(self, "closed", True)
            self._conn.close()

    def __enter__(self):
        self._conn.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):
        return self._conn.__exit__(exc_type, exc, tb)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def _wrap_opener(opener):
    trackers = []

    def wrapped():
        tracked = _TrackingConnection(opener())
        trackers.append(tracked)
        return tracked

    return wrapped, trackers


def _py_files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in filenames:
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


class SqliteConnectionLifetimeTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="prks-sqlite-life-")
        self.storage = StorageConfig.for_testing(self._tmpdir)
        os.makedirs(self.storage.pdfs_dir, exist_ok=True)
        self.db = PRKSDatabase(storage=self.storage, schema_path=_SCHEMA_PATH)
        self.index = PRKSResearchIndex(storage=self.storage)
        self.text_index = PRKSTextIndex(storage=self.storage)

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_main_db_scope_closes_on_success(self):
        orig = self.db.get_connection
        wrapped, trackers = _wrap_opener(orig)
        self.db.get_connection = wrapped
        try:
            with self.db.connection() as conn:
                conn.execute(
                    "INSERT INTO tags (id, name) VALUES (?, ?)",
                    ("T-LIFE", "lifetime-ok"),
                )
            self.assertEqual(len(trackers), 1)
            self.assertTrue(trackers[0].closed)
        finally:
            self.db.get_connection = orig
        rows = self.db.execute_query("SELECT name FROM tags WHERE id = ?", ("T-LIFE",))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "lifetime-ok")

    def test_main_db_scope_rolls_back_and_closes_on_exception(self):
        orig = self.db.get_connection
        wrapped, trackers = _wrap_opener(orig)
        self.db.get_connection = wrapped
        try:
            with self.assertRaises(RuntimeError):
                with self.db.connection() as conn:
                    conn.execute(
                        "INSERT INTO tags (id, name) VALUES (?, ?)",
                        ("T-ROLL", "lifetime-roll"),
                    )
                    raise RuntimeError("boom")
            self.assertEqual(len(trackers), 1)
            self.assertTrue(trackers[0].closed)
        finally:
            self.db.get_connection = orig
        rows = self.db.execute_query("SELECT id FROM tags WHERE id = ?", ("T-ROLL",))
        self.assertEqual(rows, [])

    def test_research_index_scope_closes_on_success_and_exception(self):
        orig = self.index._conn
        wrapped, trackers = _wrap_opener(orig)
        self.index._conn = wrapped
        try:
            with self.index._connection() as conn:
                conn.execute("SELECT 1")
            self.assertEqual(len(trackers), 1)
            self.assertTrue(trackers[0].closed)

            with self.assertRaises(RuntimeError):
                with self.index._connection() as conn:
                    conn.execute(
                        "INSERT INTO research_index_meta (key, value) VALUES (?, ?)",
                        ("lifetime_probe", "x"),
                    )
                    raise RuntimeError("boom")
            self.assertEqual(len(trackers), 2)
            self.assertTrue(trackers[1].closed)
        finally:
            self.index._conn = orig
        with self.index._connection() as conn:
            row = conn.execute(
                "SELECT value FROM research_index_meta WHERE key = ?",
                ("lifetime_probe",),
            ).fetchone()
        self.assertIsNone(row)

    def test_text_index_scope_closes_on_success_and_exception(self):
        orig = self.text_index._conn
        wrapped, trackers = _wrap_opener(orig)
        self.text_index._conn = wrapped
        try:
            with self.text_index._connection() as conn:
                conn.execute("SELECT 1")
            self.assertEqual(len(trackers), 1)
            self.assertTrue(trackers[0].closed)

            with self.assertRaises(RuntimeError):
                with self.text_index._connection() as conn:
                    conn.execute(
                        "INSERT INTO text_index_meta (key, value) VALUES (?, ?)",
                        ("lifetime_probe", "x"),
                    )
                    raise RuntimeError("boom")
            self.assertEqual(len(trackers), 2)
            self.assertTrue(trackers[1].closed)
        finally:
            self.text_index._conn = orig
        with self.text_index._connection() as conn:
            row = conn.execute(
                "SELECT value FROM text_index_meta WHERE key = ?",
                ("lifetime_probe",),
            ).fetchone()
        self.assertIsNone(row)

    def test_production_has_no_transaction_only_connection_contexts(self):
        hits = []
        for path in _py_files(os.path.join(_PROJECT_DIR, "backend")):
            rel = os.path.relpath(path, _PROJECT_DIR)
            with open(path, encoding="utf-8") as handle:
                for lineno, line in enumerate(handle, 1):
                    if _FORBIDDEN_GET_CONNECTION.search(line) or _FORBIDDEN_RAW_CONN.search(
                        line
                    ):
                        hits.append("%s:%s" % (rel, lineno))
        self.assertEqual(hits, [])

    def test_tests_do_not_use_transaction_only_get_connection_or_raw_conn(self):
        hits = []
        tests_dir = os.path.join(_PROJECT_DIR, "tests")
        for path in _py_files(tests_dir):
            rel = os.path.relpath(path, _PROJECT_DIR)
            with open(path, encoding="utf-8") as handle:
                for lineno, line in enumerate(handle, 1):
                    if _FORBIDDEN_GET_CONNECTION.search(line) or _FORBIDDEN_RAW_CONN.search(
                        line
                    ):
                        hits.append("%s:%s" % (rel, lineno))
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
