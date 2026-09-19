"""Non-browser regression tests for E2E performance helpers."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.e2e import harness


class E2ESeedCacheTests(unittest.TestCase):
    def setUp(self):
        self._old_cache = os.environ.get("PRKS_E2E_SEED_CACHE")
        os.environ["PRKS_E2E_SEED_CACHE"] = "1"
        harness.clear_seed_cache()

    def tearDown(self):
        harness.clear_seed_cache()
        if self._old_cache is None:
            os.environ.pop("PRKS_E2E_SEED_CACHE", None)
        else:
            os.environ["PRKS_E2E_SEED_CACHE"] = self._old_cache

    def test_seed_snapshot_is_built_once_and_cloned_per_test(self):
        calls = []

        def seed(root):
            calls.append(root)
            Path(root, "fixture.txt").write_text("pristine", encoding="utf-8")
            return {"nested": {"values": ["original"]}}

        with tempfile.TemporaryDirectory(prefix="prks-cache-dest-a-") as a:
            ids_a, hit_a = harness._materialize_seed(seed, a)
            self.assertFalse(hit_a)
            self.assertEqual(Path(a, "fixture.txt").read_text(encoding="utf-8"), "pristine")
            Path(a, "fixture.txt").write_text("mutated", encoding="utf-8")
            ids_a["nested"]["values"].append("mutated")

        with tempfile.TemporaryDirectory(prefix="prks-cache-dest-b-") as b:
            ids_b, hit_b = harness._materialize_seed(seed, b)
            self.assertTrue(hit_b)
            self.assertEqual(Path(b, "fixture.txt").read_text(encoding="utf-8"), "pristine")
            self.assertEqual(ids_b, {"nested": {"values": ["original"]}})

        self.assertEqual(len(calls), 1)

    def test_cache_can_be_disabled_for_clean_ab_benchmark(self):
        os.environ["PRKS_E2E_SEED_CACHE"] = "0"
        calls = []

        def seed(root):
            calls.append(root)
            Path(root, "fixture.txt").write_text(str(len(calls)), encoding="utf-8")
            return {"call": len(calls)}

        for expected in (1, 2):
            with tempfile.TemporaryDirectory(prefix="prks-cache-off-") as dest:
                ids, hit = harness._materialize_seed(seed, dest)
                self.assertFalse(hit)
                self.assertEqual(ids["call"], expected)
                self.assertEqual(
                    Path(dest, "fixture.txt").read_text(encoding="utf-8"),
                    str(expected),
                )

        self.assertEqual(len(calls), 2)

    def test_finalize_checkpoints_wal_into_main_db(self):
        import sqlite3

        with tempfile.TemporaryDirectory(prefix="prks-wal-template-") as template:
            db_path = Path(template, "prks_data.db")
            conn = sqlite3.connect(str(db_path))
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
            conn.execute("INSERT INTO t(v) VALUES ('ok')")
            conn.execute("INSERT INTO t(v) VALUES ('wal')")
            conn.commit()
            conn.close()
            # WAL companion may or may not still be present after close; finalize
            # must leave a readable main DB either way.
            harness._finalize_seed_template(template)
            self.assertFalse(Path(str(db_path) + "-wal").exists())
            self.assertFalse(Path(str(db_path) + "-shm").exists())
            conn = sqlite3.connect(str(db_path))
            rows = conn.execute("SELECT v FROM t ORDER BY id").fetchall()
            conn.close()
            self.assertEqual([r[0] for r in rows], ["ok", "wal"])

    def test_finalize_refuses_snapshot_when_checkpoint_busy(self):
        """A blocked wal_checkpoint must raise rather than cache an active template."""
        import sqlite3
        import time

        with tempfile.TemporaryDirectory(prefix="prks-wal-busy-") as template:
            db_path = Path(template, "prks_data.db")
            holder = sqlite3.connect(str(db_path))
            holder.execute("PRAGMA journal_mode=WAL")
            holder.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
            holder.execute("INSERT INTO t(v) VALUES ('base')")
            holder.commit()
            holder.execute("BEGIN IMMEDIATE")
            holder.execute("INSERT INTO t(v) VALUES ('held')")
            try:
                probe = sqlite3.connect(str(db_path), timeout=0)
                row = probe.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                probe.close()
                self.assertTrue(harness._wal_checkpoint_ok((0, 0, 0)))
                self.assertFalse(harness._wal_checkpoint_ok(row))
                self.assertTrue(Path(str(db_path) + "-wal").exists())

                started = time.perf_counter()
                with self.assertRaisesRegex(
                    RuntimeError,
                    r"active SQLite user",
                ):
                    harness._finalize_seed_template(template)
                # timeout=0 must not burn SQLite's default ~5s busy wait.
                self.assertLess(time.perf_counter() - started, 1.0)
                self.assertTrue(
                    Path(str(db_path) + "-wal").exists(),
                    "busy refuse path must not delete the WAL companion",
                )
            finally:
                holder.rollback()
                holder.close()

    def test_materialize_does_not_cache_busy_seed_template(self):
        """Busy finalize must leave _SEED_SNAPSHOTS empty for that seed_fn."""
        import sqlite3

        holders = []

        def seed(root):
            db_path = Path(root, "prks_data.db")
            holder = sqlite3.connect(str(db_path))
            holder.execute("PRAGMA journal_mode=WAL")
            holder.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
            holder.execute("INSERT INTO t(v) VALUES ('base')")
            holder.commit()
            holder.execute("BEGIN IMMEDIATE")
            holder.execute("INSERT INTO t(v) VALUES ('held')")
            holders.append(holder)
            return {"id": "busy-seed"}

        try:
            with tempfile.TemporaryDirectory(prefix="prks-busy-dest-") as dest:
                with self.assertRaisesRegex(RuntimeError, r"active SQLite user"):
                    harness._materialize_seed(seed, dest)
            self.assertNotIn(seed, harness._SEED_SNAPSHOTS)
            self.assertEqual(harness._SEED_SNAPSHOTS, {})
        finally:
            for holder in holders:
                holder.rollback()
                holder.close()

    def test_failed_seed_attempt_does_not_poison_retry(self):
        """Leftover failed templates must not FileExistsError a later build.

        Simulates Windows-style cleanup failure (rmtree no-op while a writer is
        held) then a second attempt that succeeds under a fresh unique path.
        """
        import shutil
        import sqlite3

        holders = []
        attempts = {"n": 0}
        original_rmtree = shutil.rmtree

        def sticky_rmtree(path, *args, **kwargs):
            # Leave the directory in place (open-handle / Windows delete denial).
            return None

        def seed(root):
            attempts["n"] += 1
            if attempts["n"] == 1:
                db_path = Path(root, "prks_data.db")
                holder = sqlite3.connect(str(db_path))
                holder.execute("PRAGMA journal_mode=WAL")
                holder.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
                holder.execute("INSERT INTO t(v) VALUES ('held')")
                holder.commit()
                holder.execute("BEGIN IMMEDIATE")
                holders.append(holder)
                return {"id": "first-busy"}
            Path(root, "fixture.txt").write_text("recovered", encoding="utf-8")
            return {"id": "second-ok"}

        # Also pre-poison cache-root with fixed seed-N dirs (old naming scheme).
        cache_root = harness._seed_cache_root()
        for name in ("seed-0", "seed-1", "seed-2"):
            Path(cache_root, name).mkdir(exist_ok=True)
            Path(cache_root, name, "stale.txt").write_text("poison", encoding="utf-8")

        try:
            shutil.rmtree = sticky_rmtree
            with tempfile.TemporaryDirectory(prefix="prks-retry-a-") as dest:
                with self.assertRaisesRegex(RuntimeError, r"active SQLite user"):
                    harness._materialize_seed(seed, dest)
            self.assertEqual(harness._SEED_SNAPSHOTS, {})

            for holder in holders:
                holder.rollback()
                holder.close()
            holders.clear()

            with tempfile.TemporaryDirectory(prefix="prks-retry-b-") as dest:
                ids, hit = harness._materialize_seed(seed, dest)
                self.assertFalse(hit)
                self.assertEqual(ids, {"id": "second-ok"})
                self.assertEqual(
                    Path(dest, "fixture.txt").read_text(encoding="utf-8"),
                    "recovered",
                )
            self.assertIn(seed, harness._SEED_SNAPSHOTS)
            self.assertEqual(attempts["n"], 2)
        finally:
            shutil.rmtree = original_rmtree
            for holder in holders:
                try:
                    holder.rollback()
                    holder.close()
                except Exception:
                    pass

    def test_wal_checkpoint_ok_requires_busy_zero(self):
        self.assertTrue(harness._wal_checkpoint_ok((0, 1, 1)))
        self.assertFalse(harness._wal_checkpoint_ok((1, 3, 3)))
        self.assertFalse(harness._wal_checkpoint_ok(None))
        self.assertFalse(harness._wal_checkpoint_ok(()))
        self.assertFalse(harness._wal_checkpoint_ok(("x",)))


class E2EDiagnosticAndChromiumHolderTests(unittest.TestCase):
    def setUp(self):
        self._diag = os.environ.get("PRKS_E2E_DIAGNOSTIC")
        self._recycle = os.environ.get("PRKS_E2E_CHROMIUM_RECYCLE_EVERY")
        os.environ.pop("PRKS_E2E_DIAGNOSTIC", None)
        os.environ.pop("PRKS_E2E_CHROMIUM_RECYCLE_EVERY", None)

    def tearDown(self):
        if self._diag is None:
            os.environ.pop("PRKS_E2E_DIAGNOSTIC", None)
        else:
            os.environ["PRKS_E2E_DIAGNOSTIC"] = self._diag
        if self._recycle is None:
            os.environ.pop("PRKS_E2E_CHROMIUM_RECYCLE_EVERY", None)
        else:
            os.environ["PRKS_E2E_CHROMIUM_RECYCLE_EVERY"] = self._recycle

    def test_e2e_diag_is_silent_unless_enabled(self):
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            harness.e2e_diag("START", "tests.e2e.fake.T.test_x")
        self.assertEqual(buf.getvalue(), "")

        os.environ["PRKS_E2E_DIAGNOSTIC"] = "1"
        buf = io.StringIO()
        with redirect_stdout(buf):
            harness.e2e_diag("SERVER_READY", "tests.e2e.fake.T.test_x")
        self.assertIn("[e2e-diag] SERVER_READY tests.e2e.fake.T.test_x", buf.getvalue())

    def test_chromium_recycle_every_env_and_default(self):
        self.assertEqual(harness.chromium_recycle_every(default=20), 20)
        os.environ["PRKS_E2E_CHROMIUM_RECYCLE_EVERY"] = "15"
        self.assertEqual(harness.chromium_recycle_every(default=20), 15)
        os.environ["PRKS_E2E_CHROMIUM_RECYCLE_EVERY"] = "0"
        self.assertEqual(harness.chromium_recycle_every(default=20), 0)

    def test_chromium_holder_recycles_after_n_closed_contexts(self):
        launches = []

        def fake_require():
            launches.append(1)
            return ("pw-%d" % len(launches), "browser-%d" % len(launches))

        with mock.patch.object(harness, "require_chromium", side_effect=fake_require):
            holder = harness.ChromiumHolder(recycle_every=2)
            self.assertEqual(len(launches), 1)
            holder.browser = mock.Mock()
            holder.pw = mock.Mock()
            holder.after_context_closed()
            self.assertEqual(len(launches), 1)
            holder.after_context_closed()
            self.assertEqual(len(launches), 2)
            self.assertEqual(holder._contexts_since_launch, 0)


if __name__ == "__main__":
    unittest.main()
