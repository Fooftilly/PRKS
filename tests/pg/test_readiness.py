"""``GET /readyz`` of a PostgreSQL web process (§12.3; L-D-1 #630).

Ready only with a reachable pool, the code's schema, setup ``ready`` and the
opened root still paired with equal start generations: a ``migrate`` killed
between its database bump and its marker write keeps ``web`` not ready until
the next start reconciles the marker.
"""

from __future__ import annotations

import http.client
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from typing import Any

import psycopg

import prks_app

from backend.db.config import DatabaseConfig
from backend.db.database import Database
from backend.domain.identity import root_pairing
from backend.readiness import PostgresReadiness
from backend.storage import root_binding, root_marker
from backend.storage.config import StorageConfig
from tests.pg.test_root_pairing import Crash, RootPairingTestCase, crash_at

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class ReadinessTests(RootPairingTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.name = self.database()
        self.root = self.new_root()
        lease = root_binding.acquire_shared_root_lease(self.root)
        try:
            self.bootstrap(self.name, self.root, lease=lease)
            started = self.start(self.name, self.root, lease=lease)  # this web's own start
        finally:
            lease.release()
        assert started.instance_id is not None
        self.instance_id = started.instance_id

    def readiness(self, root: str | None = None) -> PostgresReadiness:
        return PostgresReadiness(self.db(self.name), instance_id=self.instance_id, root=root or self.root)

    def execute(self, sql: str) -> None:
        with psycopg.connect(self.harness.conninfo(self.name), autocommit=True) as conn:
            conn.execute(sql)  # type: ignore[arg-type]  # fixed test statements

    def test_ready_after_this_process_started(self) -> None:
        readiness = self.readiness()
        self.assertIsNone(readiness.not_ready_reason())
        self.assertTrue(readiness.ready())

    def test_a_crashed_bump_elsewhere_is_not_ready_until_a_start_reconciles_it(self) -> None:
        readiness = self.readiness()
        lease = root_binding.acquire_shared_root_lease(self.root)
        self.addCleanup(lease.release)
        with self.assertRaises(Crash):
            self.start(self.name, self.root, lease=lease, faults=crash_at(root_pairing.FAULT_AFTER_DATABASE_BUMP))
        self.assertEqual(readiness.not_ready_reason(), "start_generation_unreconciled")
        self.assertFalse(readiness.ready())
        reconciled = self.start(self.name, self.root, lease=lease)
        self.assertTrue(reconciled.reconciled)
        self.assertTrue(readiness.ready())

    def test_another_processs_completed_start_keeps_it_ready(self) -> None:
        readiness = self.readiness()
        self.start(self.name, self.root)  # a worker or migrate starting beside it
        self.assertTrue(readiness.ready())

    def test_a_pending_or_changed_schema_is_not_ready(self) -> None:
        readiness = self.readiness()
        self.execute(
            "DELETE FROM prks.schema_migrations WHERE version = "
            "(SELECT max(version) FROM prks.schema_migrations)"
        )
        self.assertEqual(readiness.not_ready_reason(), "schema_pending")
        self.execute("INSERT INTO prks.schema_migrations (version, name, sha256) VALUES (999, 'future', repeat('a', 64))")
        self.assertEqual(readiness.not_ready_reason(), "schema_mismatch")

    def test_setup_not_ready_is_not_ready(self) -> None:
        readiness = self.readiness()
        self.execute("ALTER TABLE prks.instance DISABLE TRIGGER ALL")
        self.execute("UPDATE prks.instance SET setup_state = 'bootstrapping'")
        self.assertEqual(readiness.not_ready_reason(), "setup_pending")

    def test_a_root_that_is_no_longer_this_ones_is_not_ready(self) -> None:
        other = self.new_root("other")
        self.assertEqual(self.readiness(other).not_ready_reason(), "root_pairing_mismatch")
        os.replace(root_marker.marker_path(self.root), os.path.join(self.tmp, "kept-marker"))
        self.assertEqual(self.readiness().not_ready_reason(), "root_unmarked")

    def test_a_busy_pool_answers_not_ready_within_the_probe_bound(self) -> None:
        busy = Database(
            DatabaseConfig(conninfo=self.harness.conninfo(self.name), role="test", pool_min=0, pool_max=1)
        )
        self.addCleanup(busy.close)
        readiness = PostgresReadiness(busy, instance_id=self.instance_id, root=self.root)
        self.assertIsNone(readiness.not_ready_reason())
        holding, release = threading.Event(), threading.Event()

        def hold(_tx: Any) -> None:
            holding.set()
            release.wait(30)

        holder = threading.Thread(target=busy.query, args=(hold,))
        holder.start()
        self.addCleanup(holder.join, 30)
        self.addCleanup(release.set)
        self.assertTrue(holding.wait(10))
        started = time.monotonic()
        self.assertEqual(readiness.not_ready_reason(), "database_unavailable")
        self.assertLess(time.monotonic() - started, 3.0)

    def test_an_unreachable_database_is_not_ready(self) -> None:
        unreachable = Database(
            DatabaseConfig(
                conninfo="postgresql://prks@127.0.0.1:1/prks?connect_timeout=1",
                role="test",
                pool_min=0,
                pool_timeout_seconds=1.0,
            )
        )
        self.addCleanup(unreachable.close)
        readiness = PostgresReadiness(unreachable, instance_id=self.instance_id, root=self.root)
        self.assertEqual(readiness.not_ready_reason(), "database_unavailable")
        self.assertFalse(readiness.ready())


class WebProcessTests(RootPairingTestCase):
    """``python prks_app.py --testing`` with ``PRKS_AUTH``, as a real process."""

    def migrated(self) -> str:
        name = self.harness.create_database("web", empty=True)
        self.addCleanup(self.harness.drop_database, name)
        from backend.db.migrate import migrate

        migrate(self.harness.conninfo(name))
        return name

    def environment(self, name: str, auth: str = "loopback-owner") -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if not k.startswith("PRKS_") or k == "PRKS_TEST_DATABASE_URL"}
        env.update({
            "PRKS_AUTH": auth,
            "PRKS_DATABASE_URL": self.harness.conninfo(name),
            "PRKS_EMBEDDED_WORKER": "0",
        })
        return env

    def spawn(self, name: str, *args: str, auth: str = "loopback-owner") -> subprocess.Popen[str]:
        return subprocess.Popen(
            [sys.executable, "prks_app.py", *args],
            cwd=_PROJECT_DIR, env=self.environment(name, auth),
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
        )

    def web(self, name: str, root: str, port: int, auth: str = "loopback-owner") -> subprocess.Popen[str]:
        return self.spawn(name, "--testing", "--storage-root", root, "--port", str(port), auth=auth)

    def stop(self, proc: subprocess.Popen[str]) -> str:
        proc.send_signal(signal.SIGTERM)
        _out, err = proc.communicate(timeout=60)
        self.assertEqual(proc.returncode, 0, err)
        return err

    def bootstrapped(self, name: str, root: str) -> None:
        lease = root_binding.acquire_shared_root_lease(root)
        try:
            self.bootstrap(name, root, lease=lease)
        finally:
            lease.release()
        self.close(name)

    def test_web_answers_readyz_once_its_start_check_passed(self) -> None:
        name = self.migrated()
        root = self.new_root()
        self.bootstrapped(name, root)
        port = _free_port()
        proc = self.web(name, root, port)
        try:
            status = _wait_for(port, "/readyz", proc)
            self.assertEqual(status, 200)
            self.assertEqual(self.db_state(name), self.marker(root).start_state)
            self.assertEqual(self.db_state(name).generation, 2)  # bootstrap, then this start
        finally:
            self.stop(proc)

    def test_a_worker_and_migrate_run_beside_web_on_the_shared_lease(self) -> None:
        name = self.migrated()
        root = self.new_root()
        self.bootstrapped(name, root)
        port = _free_port()
        web = self.web(name, root, port)
        worker: subprocess.Popen[str] | None = None
        try:
            self.assertEqual(_wait_for(port, "/readyz", web), 200)
            worker = self.spawn(name, "worker", "--testing", "--storage-root", root)
            deadline = time.monotonic() + 60
            while self.db_state(name).generation < 3:
                self.assertIsNone(worker.poll(), "the worker exited beside web")
                self.assertLess(time.monotonic(), deadline, "the worker never bumped")
                time.sleep(0.2)
            migrate = subprocess.run(
                [sys.executable, "prks_app.py", "migrate", "--testing", "--storage-root", root],
                cwd=_PROJECT_DIR, env=self.environment(name), capture_output=True, text=True, timeout=120,
            )
            self.assertEqual(migrate.returncode, 0, migrate.stderr)
            self.assertIn("start generation 4", migrate.stdout)
            # Every start elsewhere completed, so web stays ready.
            self.assertEqual(_wait_for(port, "/readyz", web), 200)
            # A second web is refused: one web process serves the root's content.
            second = self.web(name, root, _free_port())
            _out, err = second.communicate(timeout=120)
            self.assertEqual(second.returncode, 2, err)
            self.assertIn("already served by another PRKS web process", err)
        finally:
            if worker is not None:
                self.stop(worker)
            self.stop(web)

    def test_a_first_run_web_binds_the_root_and_waits_for_setup(self) -> None:
        # Password sign-in waits for setup; a loopback-owner testing web sets
        # itself up instead (test_a_first_loopback_owner_testing_web_sets_itself_up).
        name = self.migrated()
        root = os.path.join(self.tmp, "first-run")
        port = _free_port()
        proc = self.web(name, root, port, auth="session")
        try:
            deadline = time.monotonic() + 60
            while not os.path.exists(root_marker.marker_path(root)):
                self.assertIsNone(proc.poll(), "web exited instead of binding the root")
                self.assertLess(time.monotonic(), deadline, "web never bound the root")
                time.sleep(0.2)
            time.sleep(1.0)
            self.assertIsNone(proc.poll(), "web exited instead of waiting for setup")
            self.assertIsNone(self.marker(root).instance_id)  # nothing claimed, nothing served
            self.bootstrapped(name, root)
            self.assertEqual(_wait_for(port, "/readyz", proc), 200)
            # The claim is generation 1; web's start after setup is the one bump.
            self.assertEqual(self.db_state(name).generation, 2)
            self.assertEqual(self.db_state(name), self.marker(root).start_state)
        finally:
            err = self.stop(proc)
        self.assertIn("waiting for setup", err)

    def test_a_first_loopback_owner_testing_web_sets_itself_up(self) -> None:
        name = self.migrated()
        root = os.path.join(self.tmp, "first-testing-run")
        port = _free_port()
        proc = self.web(name, root, port)
        try:
            self.assertEqual(_wait_for(port, "/readyz", proc), 200)
            with psycopg.connect(self.harness.conninfo(name)) as conn:
                instance_id, testing = conn.execute("SELECT id, testing FROM prks.instance").fetchone()
            self.assertTrue(testing)
            self.assertEqual(self.marker(root).instance_id, instance_id)
            # Bootstrap's root claim is this start's one bump.
            self.assertEqual(self.db_state(name).generation, 1)
            self.assertEqual(self.db_state(name), self.marker(root).start_state)
        finally:
            err = self.stop(proc)
        self.assertNotIn("waiting for setup", err)

    def test_a_stop_while_waiting_for_setup_exits_0(self) -> None:
        name = self.migrated()
        root = self.new_root()
        proc = self.web(name, root, _free_port(), auth="session")
        assert proc.stderr is not None
        line = proc.stderr.readline()
        self.assertIn("waiting for setup", line)
        err = self.stop(proc)
        self.assertIn("stopped while waiting for setup", err)
        # Both of its locks are gone.
        root_binding.open_storage_root(StorageConfig.for_testing(root), register=False).release()


class MigrateOnStartTests(RootPairingTestCase):
    def empty(self) -> Database:
        name = self.harness.create_database("mos", empty=True)
        self.addCleanup(self.harness.drop_database, name)
        db = Database(DatabaseConfig(conninfo=self.harness.conninfo(name), role="test", pool_min=0))
        self.addCleanup(db.close)
        return db

    def applied(self, db: Database) -> bool:
        row = db.query(lambda tx: tx.execute("SELECT to_regclass('prks.schema_migrations') IS NOT NULL AS t").fetchone())
        return bool(row and row["t"])

    def test_default_and_1_apply_the_schema(self) -> None:
        for env in ({}, {"PRKS_MIGRATE_ON_START": "1"}):
            with self.subTest(env=env):
                db = self.empty()
                prks_app.migrate_on_start(db, env)
                self.assertTrue(self.applied(db))

    def test_0_leaves_it_to_the_migrate_role_but_refuses_an_unmigrated_schema(self) -> None:
        db = self.empty()
        with self.assertRaises(SystemExit) as ctx:
            prks_app.migrate_on_start(db, {"PRKS_MIGRATE_ON_START": "0"})
        self.assertEqual(ctx.exception.code, 2)
        with psycopg.connect(db.config.conninfo) as conn:
            row = conn.execute("SELECT to_regclass('prks.schema_migrations')").fetchone()
        self.assertEqual(row, (None,))

    def test_0_starts_on_exactly_this_codes_schema_only(self) -> None:
        name = self.database("mos")
        db = self.db(name)
        prks_app.migrate_on_start(db, {"PRKS_MIGRATE_ON_START": "0"})  # current: passes
        with psycopg.connect(self.harness.conninfo(name), autocommit=True) as conn:
            conn.execute(
                "INSERT INTO prks.schema_migrations (version, name, sha256) "
                "VALUES (999, 'future', repeat('a', 64))"
            )
        with self.assertRaises(SystemExit) as ctx:
            prks_app.migrate_on_start(db, {"PRKS_MIGRATE_ON_START": "0"})
        self.assertEqual(ctx.exception.code, 2)

    def test_anything_else_is_refused(self) -> None:
        for value in ("true", "yes", "2"):
            with self.subTest(value=value):
                db = self.empty()
                with self.assertRaises(SystemExit) as ctx:
                    prks_app.migrate_on_start(db, {"PRKS_MIGRATE_ON_START": value})
                self.assertEqual(ctx.exception.code, 2)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for(port: int, path: str, proc: subprocess.Popen[str], timeout: float = 90.0) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise AssertionError(f"web exited with {proc.returncode}: {proc.stderr.read() if proc.stderr else ''}")
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            conn.request("GET", path)
            status = conn.getresponse().status
            conn.close()
            if status == 200:
                return status
        except OSError:
            pass
        time.sleep(0.3)
    raise AssertionError(f"{path} never answered 200")
