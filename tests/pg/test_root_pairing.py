"""Root claim, start check and start-generation bump (§9.2, §11.1, §13.3; L-ID-1 #626).

Fault injection between each pair of writes: a ``faults`` callback raises at a
named point (the process "dies" there and every ``finally`` runs), and the
kill tests run the same steps in a child process that ``os._exit``s at the
point, so only the kernel and the server release what it held.

A "copy" of a database is ``CREATE DATABASE … TEMPLATE``, which is what a
cluster copy, a reloaded dump or a moved volume looks like to PRKS.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from typing import Any, Callable
from unittest.mock import patch

import psycopg

from backend.db.config import DatabaseConfig
from backend.db.database import Database
from backend.domain.identity import root_pairing
from backend.domain.identity.root_pairing import (
    BootstrapResult,
    OwnerSpec,
    RootPairingRefused,
    StartStatus,
)
from backend.storage import root_binding, root_marker
from backend.storage.config import StorageConfig
from backend.storage.file_lock import SharedFileLock
from backend.storage.objects import LocalFilesystemStorage, StorageKey
from backend.storage.root_marker import StartState
from tests.pg.harness import PostgresHarness, fetch_one, get_harness
from tests.pg.root_pairing_child import KILLED_EXIT

OWNER = OwnerSpec("owner", "Owner", "Library")
_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class Crash(Exception):
    """A process dying at a fault point."""


def crash_at(point: str) -> Callable[[str], None]:
    def faults(current: str) -> None:
        if current == point:
            raise Crash(point)

    return faults


class RootPairingTestCase(unittest.TestCase):
    harness: PostgresHarness

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.harness = get_harness()

    def setUp(self) -> None:
        tmp = tempfile.mkdtemp(prefix="prks-pairing-")
        self.addCleanup(shutil.rmtree, tmp, True)
        self.tmp = os.path.realpath(tmp)
        self._dbs: dict[str, Database] = {}

    # -- fixtures ----------------------------------------------------------

    def database(self, label: str = "pair") -> str:
        name = self.harness.create_database(label)
        self.addCleanup(self._drop, name)
        return name

    def _drop(self, name: str) -> None:
        self.close(name)
        self.harness.drop_database(name)

    def db(self, name: str) -> Database:
        if name not in self._dbs:
            self._dbs[name] = Database(
                DatabaseConfig(conninfo=self.harness.conninfo(name), role="test", pool_min=0)
            )
        return self._dbs[name]

    def close(self, name: str) -> None:
        db = self._dbs.pop(name, None)
        if db is not None:
            db.close()

    def copy_of(self, name: str) -> str:
        """A copy of the database as it is now (closes its pool first)."""
        self.close(name)
        copy = self.harness.clone_database(name)
        self.addCleanup(self._drop, copy)
        return copy

    def new_root(self, name: str = "lib") -> str:
        root = os.path.join(self.tmp, name)
        bound = root_binding.open_storage_root(StorageConfig.for_testing(root), register=False)
        bound.release()
        return root

    def lease(self, root: str) -> SharedFileLock:
        lease = root_binding.acquire_shared_root_lease(root)
        self.addCleanup(lease.release)
        return lease

    def bootstrap(self, name: str, root: str, **kwargs: Any) -> BootstrapResult:
        lease = kwargs.pop("lease", None) or self.lease(root)
        kwargs.setdefault("testing", True)
        return root_pairing.bootstrap(self.db(name), OWNER, root=root, lease=lease, **kwargs)

    def start(self, name: str, root: str, **kwargs: Any) -> root_pairing.StartResult:
        lease = kwargs.pop("lease", None) or self.lease(root)
        kwargs.setdefault("testing", True)
        kwargs.setdefault("bump", True)
        return root_pairing.start_process(self.db(name), root=root, lease=lease, **kwargs)

    def refused(self, reason: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> RootPairingRefused:
        with self.assertRaises(RootPairingRefused) as ctx:
            fn(*args, **kwargs)
        self.assertEqual(ctx.exception.reason, reason, ctx.exception.message)
        return ctx.exception

    def instance(self, name: str) -> dict[str, Any] | None:
        with psycopg.connect(self.harness.conninfo(name)) as conn:
            with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                cur.execute(
                    "SELECT id, setup_state, start_generation, start_token, prev_start_token "
                    "FROM prks.instance"
                )
                return cur.fetchone()

    def db_state(self, name: str) -> StartState:
        row = self.instance(name)
        assert row is not None
        return StartState(row["start_generation"], row["start_token"])

    def marker(self, root: str) -> root_marker.RootMarker:
        marker = root_marker.read_marker(root)
        assert marker is not None
        return marker

    def marker_bytes(self, root: str) -> bytes:
        with open(root_marker.marker_path(root), "rb") as handle:
            return handle.read()

    def store_object(self, root: str, name: str = "a.pdf") -> None:
        LocalFilesystemStorage(root).put_new(
            StorageKey("asset-objects", name), lambda handle: handle.write(b"%PDF-1.7\n")
        )

    def assert_paired(self, name: str, root: str, generation: int) -> None:
        row = self.instance(name)
        assert row is not None
        marker = self.marker(root)
        self.assertEqual(row["setup_state"], "ready")
        self.assertEqual(marker.instance_id, row["id"])
        self.assertEqual(marker.start_state, StartState(generation, row["start_token"]))
        self.assertEqual(row["start_generation"], generation)

    def kill_child(self, action: str, name: str, root: str, kill_at: str) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "tests.pg.root_pairing_child", action,
             self.harness.conninfo(name), root, kill_at],
            cwd=_PROJECT_DIR, capture_output=True, timeout=120,
        )
        self.assertEqual(result.returncode, KILLED_EXIT, result.stderr.decode(errors="replace"))


class BootstrapTests(RootPairingTestCase):
    def test_bootstrap_claims_root_and_creates_owner_library_membership(self) -> None:
        name, root = self.database(), self.new_root()
        result = self.bootstrap(name, root)
        self.assertTrue(result.created)
        self.assert_paired(name, root, 1)
        with psycopg.connect(self.harness.conninfo(name)) as conn:
            row = fetch_one(conn.execute(
                "SELECT u.login_key, m.role, l.epoch FROM prks.library_memberships m "
                "JOIN prks.users u ON u.id = m.user_id JOIN prks.libraries l ON l.id = m.library_id"
            ))
        self.assertEqual(row, ("owner", "owner", 1))
        # The claimant counts the claim as its bump.
        started = self.start(name, root, bump=False)
        self.assertEqual((started.status, started.start_generation, started.bumped), (StartStatus.READY, 1, False))
        self.assert_paired(name, root, 1)

    def test_takeover_after_ready_is_refused(self) -> None:
        name, root = self.database(), self.new_root()
        self.bootstrap(name, root)
        self.refused("already_ready", self.bootstrap, name, root)
        with psycopg.connect(self.harness.conninfo(name)) as conn:
            self.assertEqual(fetch_one(conn.execute("SELECT count(*) FROM prks.users"))[0], 1)

    def test_simultaneous_bootstraps_create_one_owner_and_library(self) -> None:
        name, root = self.database(), self.new_root()
        barrier = threading.Barrier(5)
        outcomes: list[Any] = []

        def run() -> None:
            lease = root_binding.acquire_shared_root_lease(root)
            try:
                barrier.wait(30)
                outcomes.append(self.bootstrap(name, root, lease=lease))
            except RootPairingRefused as exc:
                outcomes.append(exc.reason)
            finally:
                lease.release()

        threads = [threading.Thread(target=run) for _ in range(5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        results = [o for o in outcomes if isinstance(o, BootstrapResult)]
        self.assertTrue(results, outcomes)
        self.assertEqual(len(outcomes), 5)
        self.assertTrue(all(o == "already_ready" for o in outcomes if not isinstance(o, BootstrapResult)), outcomes)
        self.assertEqual({(r.instance_id, r.user_id, r.library_id) for r in results}, {(results[0].instance_id, results[0].user_id, results[0].library_id)})
        with psycopg.connect(self.harness.conninfo(name)) as conn:
            counts = fetch_one(conn.execute(
                "SELECT (SELECT count(*) FROM prks.users), (SELECT count(*) FROM prks.libraries), "
                "(SELECT count(*) FROM prks.library_memberships)"
            ))
        self.assertEqual(counts, (1, 1, 1))
        self.assert_paired(name, root, 1)

    def test_repeated_final_step_returns_existing_state(self) -> None:
        name, root = self.database(), self.new_root()
        lease = self.lease(root)
        claim = root_pairing.begin_bootstrap(self.db(name), root=root, lease=lease, testing=True)
        root_pairing.claim_bootstrap_root(root, claim, lease=lease)
        first = root_pairing.finish_bootstrap(self.db(name), claim, OWNER, root=root, lease=lease)
        again = root_pairing.finish_bootstrap(self.db(name), claim, OWNER, root=root, lease=lease)
        self.assertEqual((again.user_id, again.library_id, again.created), (first.user_id, first.library_id, False))

    def test_crash_after_each_bootstrap_step_resumes_and_no_process_serves_meanwhile(self) -> None:
        for point in (root_pairing.FAULT_AFTER_INSTANCE_COMMIT, root_pairing.FAULT_AFTER_CLAIM):
            with self.subTest(point=point):
                name, root = self.database(), self.new_root(point.replace(".", "-"))
                with self.assertRaises(Crash):
                    self.bootstrap(name, root, faults=crash_at(point))
                self.assertEqual(self.instance(name)["setup_state"], "bootstrapping")  # type: ignore[index]
                # web and worker started meanwhile do nothing and bump nothing.
                before = self.marker_bytes(root)
                self.assertEqual(self.start(name, root).status, StartStatus.SETUP_PENDING)
                self.assertEqual(self.marker_bytes(root), before)
                self.bootstrap(name, root)
                self.assert_paired(name, root, 1)

    def test_killed_bootstrap_resumes_after_each_step(self) -> None:
        for point in (root_pairing.FAULT_AFTER_INSTANCE_COMMIT, root_pairing.FAULT_AFTER_CLAIM):
            with self.subTest(point=point):
                name, root = self.database(), self.new_root("k-" + point.replace(".", "-"))
                self.kill_child("bootstrap", name, root, point)
                self.assertEqual(self.instance(name)["setup_state"], "bootstrapping")  # type: ignore[index]
                claimed = point == root_pairing.FAULT_AFTER_CLAIM
                self.assertEqual(self.marker(root).instance_id is not None, claimed)
                self.bootstrap(name, root)
                self.assert_paired(name, root, 1)

    def test_second_empty_database_after_the_claim_is_refused(self) -> None:
        first, root = self.database(), self.new_root()
        with self.assertRaises(Crash):
            self.bootstrap(first, root, faults=crash_at(root_pairing.FAULT_AFTER_CLAIM))
        before = self.marker_bytes(root)
        second = self.database()
        self.refused("root_used", self.bootstrap, second, root)
        self.assertIsNone(self.instance(second))
        self.assertEqual(self.marker_bytes(root), before)

    def test_recreated_database_volume_is_refused_and_objects_stay(self) -> None:
        first, root = self.database(), self.new_root()
        self.bootstrap(first, root)
        self.start(first, root)
        self.store_object(root)
        before = self.marker_bytes(root)
        recreated = self.database()
        self.refused("root_used", self.bootstrap, recreated, root)
        self.assertEqual(self.marker_bytes(root), before)
        self.assertEqual(
            [str(k) for k in LocalFilesystemStorage(root).iter_keys("asset-objects")],
            ["asset-objects/a.pdf"],
        )

    def test_unclaimed_root_holding_objects_is_refused(self) -> None:
        name, root = self.database(), self.new_root()
        self.store_object(root)
        self.refused("root_used", self.bootstrap, name, root)
        self.assertIsNone(self.instance(name))
        self.assertIsNone(self.marker(root).instance_id)

    def test_two_empty_databases_claiming_one_root_leave_exactly_one_ready(self) -> None:
        first, second, root = self.database(), self.database(), self.new_root()
        paused, resume, second_done = threading.Event(), threading.Event(), threading.Event()
        outcome: dict[str, Any] = {}

        def hold(point: str) -> None:
            if point == root_pairing.FAULT_CLAIM_BEFORE_WRITE:
                paused.set()
                if not resume.wait(30):
                    raise AssertionError("first claim was never resumed")

        def run(label: str, name: str, faults: Callable[[str], None] | None, done: threading.Event | None) -> None:
            try:
                kwargs = {"faults": faults} if faults else {}
                outcome[label] = self.bootstrap(name, root, **kwargs)
            except BaseException as exc:  # noqa: BLE001 - reported below
                outcome[label] = exc
            finally:
                if done is not None:
                    done.set()

        t1 = threading.Thread(target=run, args=("first", first, hold, None))
        t1.start()
        self.assertTrue(paused.wait(30))
        t2 = threading.Thread(target=run, args=("second", second, None, second_done))
        t2.start()
        # The second claim waits on root.claim while the first holds it
        # between its marker re-read and its write.
        self.assertFalse(second_done.wait(1.0))
        resume.set()
        t1.join(60)
        t2.join(60)
        self.assertIsInstance(outcome["first"], BootstrapResult)
        self.assertIsInstance(outcome["second"], RootPairingRefused)
        self.assertEqual(outcome["second"].reason, "root_claimed_by_other")
        states = [self.instance(first)["setup_state"], self.instance(second)["setup_state"]]  # type: ignore[index]
        self.assertEqual(states, ["ready", "bootstrapping"])
        self.assert_paired(first, root, 1)

    def test_bootstrapping_database_retried_with_another_root_is_refused_before_claiming(self) -> None:
        name, root, other = self.database(), self.new_root(), self.new_root("other")
        with self.assertRaises(Crash):
            self.bootstrap(name, root, faults=crash_at(root_pairing.FAULT_AFTER_INSTANCE_COMMIT))
        before = self.marker_bytes(other)
        error = self.refused("bootstrap_root_mismatch", self.bootstrap, name, other)
        self.assertIn(self.marker(root).storage_root_id, error.message)
        self.assertEqual(self.marker_bytes(other), before)
        self.assertIsNone(self.marker(other).instance_id)
        # The original root still completes.
        self.bootstrap(name, root)
        self.assert_paired(name, root, 1)

    def test_concurrent_bootstrap_with_another_root_never_claims_it(self) -> None:
        name, root, other = self.database(), self.new_root(), self.new_root("other")
        paused, resume = threading.Event(), threading.Event()
        outcome: dict[str, Any] = {}

        def hold(point: str) -> None:
            if point == root_pairing.FAULT_AFTER_INSTANCE_COMMIT:
                paused.set()
                resume.wait(30)

        def first() -> None:
            outcome["first"] = self.bootstrap(name, root, faults=hold)

        t1 = threading.Thread(target=first)
        t1.start()
        self.assertTrue(paused.wait(30))
        before = self.marker_bytes(other)
        self.refused("bootstrap_root_mismatch", self.bootstrap, name, other)
        resume.set()
        t1.join(60)
        self.assertIsInstance(outcome["first"], BootstrapResult)
        self.assertEqual(self.marker_bytes(other), before)

    def test_copy_taken_while_bootstrapping_is_refused_after_the_root_moved_on(self) -> None:
        for moved_by in ("bump", "object"):
            with self.subTest(moved_by=moved_by):
                original, root = self.database(), self.new_root("copy-" + moved_by)
                with self.assertRaises(Crash):
                    self.bootstrap(original, root, faults=crash_at(root_pairing.FAULT_AFTER_INSTANCE_COMMIT))
                copy = self.copy_of(original)
                self.bootstrap(original, root)
                if moved_by == "bump":
                    self.start(original, root)
                else:
                    self.store_object(root)
                before = self.marker_bytes(root)
                self.refused("root_moved_on", self.bootstrap, copy, root)
                self.assertEqual(self.marker_bytes(root), before)
                self.assertEqual(self.instance(copy)["setup_state"], "bootstrapping")  # type: ignore[index]


class StartTests(RootPairingTestCase):
    def ready(self, label: str = "lib") -> tuple[str, str]:
        name, root = self.database(), self.new_root(label)
        self.bootstrap(name, root)
        return name, root

    def test_every_start_bumps_database_then_marker(self) -> None:
        name, root = self.ready()
        tokens = {self.marker(root).start_state.token}
        for generation in (2, 3, 4):
            result = self.start(name, root)
            self.assertEqual((result.status, result.start_generation, result.bumped), (StartStatus.READY, generation, True))
            self.assert_paired(name, root, generation)
            tokens.add(self.marker(root).start_state.token)
        self.assertEqual(len(tokens), 4)
        row = self.instance(name)
        assert row is not None
        self.assertNotEqual(row["prev_start_token"], row["start_token"])

    def test_read_only_check_does_not_bump(self) -> None:
        name, root = self.ready()
        self.start(name, root)
        before = self.marker_bytes(root)
        result = self.start(name, root, bump=False)
        self.assertEqual((result.start_generation, result.bumped), (2, False))
        self.assertEqual(self.marker_bytes(root), before)

    def test_migrate_killed_between_database_bump_and_marker_write_is_reconciled(self) -> None:
        name, root = self.ready()
        stale_copy = self.copy_of(name)  # taken before the interrupted bump
        self.kill_child("start", name, root, root_pairing.FAULT_AFTER_DATABASE_BUMP)
        self.assertEqual(self.db_state(name).generation, 2)
        self.assertEqual(self.marker(root).start_state.generation, 1)
        result = self.start(name, root)  # the next web or worker
        self.assertTrue(result.reconciled)
        self.assert_paired(name, root, 3)
        self.refused("database_behind", self.start, stale_copy, root)
        self.assert_paired(name, root, 3)

    def test_crash_between_bumps_then_read_only_check_reconciles(self) -> None:
        name, root = self.ready()
        with self.assertRaises(Crash):
            self.start(name, root, faults=crash_at(root_pairing.FAULT_AFTER_DATABASE_BUMP))
        result = self.start(name, root, bump=False)
        self.assertEqual((result.reconciled, result.start_generation), (True, 2))
        self.assert_paired(name, root, 2)

    def test_generations_reached_separately_are_refused(self) -> None:
        for original_restart in ("migrate", "worker_with_object"):
            with self.subTest(original_restart=original_restart):
                original, root = self.ready("forked-" + original_restart)
                self.start(original, root)
                copy = self.copy_of(original)
                # The copy's migrate dies after its database bump commits.
                with self.assertRaises(Crash):
                    self.start(copy, root, faults=crash_at(root_pairing.FAULT_AFTER_DATABASE_BUMP))
                # The original comes back (a plain worker restart is enough).
                self.start(original, root)
                if original_restart == "worker_with_object":
                    self.store_object(root, original_restart + ".pdf")
                before = self.marker_bytes(root)
                self.assertEqual(self.db_state(copy).generation, self.marker(root).start_state.generation)
                self.refused("generation_forked", self.start, copy, root)
                self.assertEqual(self.marker_bytes(root), before)

    def test_database_more_than_one_ahead_is_refused(self) -> None:
        name, root = self.ready()
        with psycopg.connect(self.harness.conninfo(name)) as conn:
            conn.execute("UPDATE prks.instance SET start_generation = 3")
        before = self.marker_bytes(root)
        self.refused("database_ahead", self.start, name, root)
        self.assertEqual(self.marker_bytes(root), before)

    def test_one_ahead_with_a_foreign_previous_token_is_refused(self) -> None:
        name, root = self.ready()
        with psycopg.connect(self.harness.conninfo(name)) as conn:
            conn.execute(
                "UPDATE prks.instance SET start_generation = 2, prev_start_token = %s, start_token = %s",
                ("e" * 32, "f" * 32),
            )
        before = self.marker_bytes(root)
        self.refused("database_ahead", self.start, name, root)
        self.assertEqual(self.marker_bytes(root), before)

    def test_marker_write_failure_in_reconcile_keeps_web_not_ready_and_releases_the_start_key(self) -> None:
        name, root = self.ready()
        with self.assertRaises(Crash):
            self.start(name, root, faults=crash_at(root_pairing.FAULT_AFTER_DATABASE_BUMP))
        before = self.marker_bytes(root)
        with patch.object(root_marker, "replace_file_atomically", side_effect=OSError("read-only")):
            error = self.refused("marker_write_failed", self.start, name, root)
        self.assertTrue(error.retryable)
        self.assertEqual(self.marker_bytes(root), before)
        self.assertEqual(self.db_state(name).generation, 2)  # web did not bump past it
        self.assertFalse(self._start_key_held(name))
        # A worker started next gets past the lock (a short wait would fail).
        result = self.start(name, root, lock_timeout_ms=2_000)
        self.assertTrue(result.reconciled)
        self.assert_paired(name, root, 3)

    def test_marker_write_failure_after_database_bump_is_reconciled_by_the_next_start(self) -> None:
        name, root = self.ready()
        with patch.object(root_marker, "replace_file_atomically", side_effect=OSError("full")):
            self.refused("marker_write_failed", self.start, name, root)
        self.assertEqual((self.db_state(name).generation, self.marker(root).start_state.generation), (2, 1))
        self.assertTrue(self.start(name, root).reconciled)
        self.assert_paired(name, root, 3)

    def _without_durability(self) -> Any:
        real_replace = root_marker.replace_file_atomically

        def replace_without_directory_sync(path: str, data: bytes) -> bool:
            real_replace(path, data)
            return False

        replace = patch.object(
            root_marker, "replace_file_atomically", side_effect=replace_without_directory_sync
        )
        sync = patch.object(root_marker, "fsync_directory", return_value=False)
        return replace, sync

    def test_non_durable_marker_bump_is_never_ready(self) -> None:
        name, root = self.ready()
        replace, sync = self._without_durability()
        with replace, sync:
            error = self.refused("marker_not_durable", self.start, name, root)
            self.assertTrue(error.retryable)
            self.assertFalse(self._start_key_held(name))
            # A retry finding the marker already written proves durability too.
            self.refused("marker_not_durable", self.start, name, root)
            self.assertFalse(self._start_key_held(name))
        result = self.start(name, root, lock_timeout_ms=2_000)
        self.assertEqual(result.status, StartStatus.READY)
        self.assert_paired(name, root, result.start_generation or 0)

    def test_non_durable_reconcile_is_never_ready(self) -> None:
        name, root = self.ready()
        with self.assertRaises(Crash):
            self.start(name, root, faults=crash_at(root_pairing.FAULT_AFTER_DATABASE_BUMP))
        replace, sync = self._without_durability()
        with replace, sync:
            for bump in (True, False):
                with self.subTest(bump=bump):
                    error = self.refused("marker_not_durable", self.start, name, root, bump=bump)
                    self.assertTrue(error.retryable)
                    self.assertFalse(self._start_key_held(name))
        result = self.start(name, root, lock_timeout_ms=2_000)
        self.assertEqual(result.status, StartStatus.READY)
        self.assert_paired(name, root, result.start_generation or 0)

    def _start_key_held(self, name: str) -> bool:
        with psycopg.connect(self.harness.conninfo(name)) as conn:
            row = fetch_one(conn.execute(
                "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND database = "
                "(SELECT oid FROM pg_database WHERE datname = current_database()) "
                "AND ((classid::bigint << 32) | objid::bigint) = %s",
                (root_pairing.START_LOCK_KEY,),
            ))
        return bool(row[0])

    def test_start_key_serializes_starts_and_its_wait_is_bounded(self) -> None:
        name, root = self.ready()
        holder = psycopg.connect(self.harness.conninfo(name), autocommit=True)
        self.addCleanup(holder.close)
        holder.execute("SELECT pg_advisory_lock(%s)", (root_pairing.START_LOCK_KEY,))
        self.assertTrue(self._start_key_held(name))
        before = self.marker_bytes(root)
        error = self.refused("start_lock_timeout", self.start, name, root, lock_timeout_ms=200)
        self.assertTrue(error.retryable)
        self.assertEqual(self.marker_bytes(root), before)
        holder.execute("SELECT pg_advisory_unlock(%s)", (root_pairing.START_LOCK_KEY,))
        self.assertEqual(self.start(name, root).start_generation, 2)

    def test_concurrent_starts_bump_one_after_the_other(self) -> None:
        name, root = self.ready()
        results: list[Any] = []

        def run() -> None:
            try:
                results.append(self.start(name, root))
            except BaseException as exc:  # noqa: BLE001 - asserted below
                results.append(exc)

        threads = [threading.Thread(target=run) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        self.assertEqual(sorted(r.start_generation for r in results), [2, 3, 4, 5])
        self.assert_paired(name, root, 5)

    def test_start_key_released_after_every_refusal(self) -> None:
        name, root = self.ready()
        with psycopg.connect(self.harness.conninfo(name)) as conn:
            conn.execute("UPDATE prks.instance SET start_generation = 7")
        self.refused("database_ahead", self.start, name, root)
        self.assertFalse(self._start_key_held(name))

    def test_worker_started_before_bootstrap_waits_then_checks_and_bumps(self) -> None:
        name, root = self.database(), self.new_root()
        self.assertEqual(self.start(name, root).status, StartStatus.SETUP_PENDING)
        with self.assertRaises(Crash):
            self.bootstrap(name, root, faults=crash_at(root_pairing.FAULT_AFTER_CLAIM))
        self.assertEqual(self.start(name, root).status, StartStatus.SETUP_PENDING)
        self.bootstrap(name, root)
        other = self.new_root("other")
        self.refused("root_pairing_mismatch", self.start, name, other)
        result = self.start(name, root)
        self.assertEqual((result.status, result.start_generation), (StartStatus.READY, 2))
        self.assert_paired(name, root, 2)

    def test_pairing_refusals_name_both_sides_and_change_nothing(self) -> None:
        name, root = self.ready()
        other_db, other_root = self.ready("second")
        before = self.marker_bytes(root)
        # Another bootstrapped database pointed at this root: its storage ID differs.
        self.refused("root_pairing_mismatch", self.start, other_db, root)
        # Same storage ID, different instance (another database claimed this root).
        doc = dict(self.marker(other_root).document)
        doc["storage_root_id"] = self.marker(root).storage_root_id
        with psycopg.connect(self.harness.conninfo(other_db)) as conn:
            conn.execute("UPDATE prks.instance SET storage_root_id = %s", (doc["storage_root_id"],))
        error = self.refused("root_claimed_by_other", self.start, other_db, root)
        self.assertIn(self.marker(root).instance_id or "", error.message)
        self.assertEqual(self.marker_bytes(root), before)
        self.refused("testing_mismatch", self.start, name, root, testing=False)

    def test_ready_database_with_unclaimed_or_unmarked_root_is_refused(self) -> None:
        name, root = self.ready()
        lease = self.lease(root)
        path = root_marker.marker_path(root)
        with open(path, "rb") as handle:
            saved = handle.read()
        doc = dict(self.marker(root).document)
        for field in root_marker.PAIRING_FIELDS:
            doc.pop(field)
        with open(path, "wb") as handle:
            handle.write(root_marker._encode(doc))
        self.refused("root_unclaimed", self.start, name, root, lease=lease)
        os.remove(path)
        self.refused("root_unmarked", self.start, name, root, lease=lease)
        with open(path, "wb") as handle:
            handle.write(saved)
        self.assertEqual(self.start(name, root, lease=lease).start_generation, 2)

    def test_relocated_root_keeps_its_pairing_and_starts(self) -> None:
        name, root = self.ready()
        self.start(name, root)
        source = self.marker(root)
        destination = os.path.join(self.tmp, "moved")
        os.makedirs(os.path.join(destination, root_marker.MAINTENANCE_DIRNAME))
        lock = root_marker.ExclusiveFileLock.acquire(root_marker.lease_path(destination))
        try:
            built = root_marker.carry_pairing_fields(source, dict(source.document))
            root_marker.write_marker(destination, built, lease=lock)
        finally:
            lock.release()
        result = self.start(name, destination)
        self.assertEqual(result.start_generation, 3)
        self.assert_paired(name, destination, 3)


if __name__ == "__main__":
    unittest.main()
