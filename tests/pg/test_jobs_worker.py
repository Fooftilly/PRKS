"""The job worker: wakeups, idle tick, graceful stop and the §11.1 start (L-J-1 #627, PR 2/3)."""

from __future__ import annotations

import io
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from datetime import timedelta
from typing import Any, Optional
from unittest.mock import patch

from backend.db.config import DatabaseConfig
from backend.db.database import Database
from backend.db.errors import DatabaseUnavailableError
from backend.db.migrate import SCHEMA_DIR, migrate
from backend.domain.identity import root_pairing
from backend.jobs import diagnostics, prune, queue
from backend.jobs import worker as worker_module
from backend.jobs.handlers import HandlerRegistry
from backend.jobs.runner import JobRun, run_job
from backend.jobs.worker import EXIT_OK, EXIT_REFUSED, Worker, WorkerSettings, main
from backend.storage import root_binding, root_marker
from backend.storage.config import StorageConfig
from tests.pg.jobs_fixtures import LEASE, JobsTestCase
from tests.pg.test_root_pairing import Crash, RootPairingTestCase, crash_at

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
INSERT_EFFECT = "INSERT INTO prks.instance_effects (note) VALUES (%s)"
WAIT = 30.0  # an upper bound for something that happens promptly; never slept


def wait_until(predicate: Any, what: str, timeout: float = WAIT) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        time.sleep(0.01)


class WorkerTestCase(JobsTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.run_sql("CREATE TABLE prks.instance_effects (note text NOT NULL)")

    def effects(self) -> list[str]:
        return [row["note"] for row in self.run_sql("SELECT note FROM prks.instance_effects ORDER BY note")]

    def start_worker(self, registry: Optional[HandlerRegistry] = None, **settings: Any) -> Worker:
        settings.setdefault("lease", LEASE)
        settings.setdefault("poll", timedelta(milliseconds=50))
        worker = Worker(
            self.db, name="worker", registry=registry or self.registry, settings=WorkerSettings(**settings)
        )
        thread, _result, error = self.in_thread(worker.run)

        def stop() -> None:
            worker.stop()
            thread.join(WAIT)
            self.assertEqual(error, [])

        self.addCleanup(stop)
        self.assertTrue(worker.started.wait(WAIT))
        self.worker_thread = thread
        return worker

    def stop_worker(self, worker: Worker) -> None:
        worker.stop()
        self.worker_thread.join(WAIT)
        self.assertFalse(self.worker_thread.is_alive())

    def blocking_handler(self, kind: str = "test.instance") -> tuple[threading.Event, threading.Event]:
        """A handler that waits for ``go`` and then writes one fenced effect."""
        inside, go = threading.Event(), threading.Event()

        def handler(run: JobRun) -> None:
            inside.set()
            go.wait(WAIT)
            run.command(lambda tx: tx.execute(INSERT_EFFECT, ("done",)))

        self.registry.register(kind, handler)
        return inside, go


class WakeupTests(WorkerTestCase):
    def test_a_notify_wakes_an_idle_worker_without_waiting_for_the_poll(self) -> None:
        ran = threading.Event()
        self.registry.register("test.instance", lambda _run: ran.set())
        worker = self.start_worker(poll=timedelta(hours=1))
        self.assertTrue(worker.idle.wait(WAIT))
        self.enqueue_instance()  # NOTIFY at commit
        self.assertTrue(ran.wait(WAIT))  # an hour-long poll never fired
        wait_until(lambda: worker.stats.snapshot()["succeeded"] == 1, "the success to be counted")

    def test_the_poll_claims_a_job_whose_notify_was_missed(self) -> None:
        ran = threading.Event()
        self.registry.register("test.instance", lambda _run: ran.set())
        worker = self.start_worker()
        self.assertTrue(worker.idle.wait(WAIT))
        # Inserted without NOTIFY, as if the notification had been lost.
        self.run_sql("INSERT INTO prks.jobs (kind) VALUES ('test.instance')")
        self.assertTrue(ran.wait(WAIT))

    def test_jobs_run_one_after_another_and_are_counted(self) -> None:
        def fail(_run: JobRun) -> None:
            raise ValueError("boom")

        self.registry.register("test.ok", lambda _run: None)
        self.registry.register("test.fail", fail)
        for _ in range(3):
            self.enqueue_instance(kind="test.ok")
        failing = self.enqueue_instance(kind="test.fail", max_attempts=1)
        worker = self.start_worker()
        wait_until(lambda: self.state(failing) == "failed", "the failing job")  # type: ignore[arg-type]
        wait_until(lambda: worker.stats.snapshot()["succeeded"] == 3, "three successes")
        stats = worker.stats.snapshot()
        self.assertEqual((stats["claimed"], stats["failed"], stats["lost"]), (4, 1, 0))

    def test_an_unreachable_database_is_counted_and_retried_not_fatal(self) -> None:
        ran = threading.Event()
        self.registry.register("test.instance", lambda _run: ran.set())
        real = queue.claim
        calls = {"n": 0}

        def flaky(*args: Any, **kwargs: Any) -> Any:
            calls["n"] += 1
            if calls["n"] <= 2:
                raise DatabaseUnavailableError()
            return real(*args, **kwargs)

        self.enqueue_instance()
        with patch.object(queue, "claim", flaky):
            worker = self.start_worker()
            self.assertTrue(ran.wait(WAIT))
        self.assertEqual(worker.stats.snapshot()["errors"], 2)


class IdleTickTests(WorkerTestCase):
    def test_the_idle_tick_reaps_exhausted_runs(self) -> None:
        crashed = self.enqueue_instance(kind="test.crash", singleton_key="k", max_attempts=1)
        job = self.claim("dead")
        assert job is not None and crashed is not None
        self.expire(crashed)
        worker = self.start_worker()
        wait_until(lambda: self.state(crashed) == "failed", "the reaper")
        self.assertEqual(self.row(crashed)["last_error"], "lease_expired")
        self.assertEqual(worker.stats.snapshot()["reaped"], 1)

    def test_prune_is_enqueued_once_per_interval_by_any_number_of_idle_ticks(self) -> None:
        self.registry.register(prune.PRUNE_KIND, prune.prune_jobs)
        worker = self.start_worker()
        wait_until(
            lambda: self.run_sql(
                "SELECT count(*) AS n FROM prks.jobs WHERE kind = %s AND state = 'succeeded'",
                (prune.PRUNE_KIND,),
            )[0]["n"] == 1,
            "one prune run",
        )
        ticks = 0
        while ticks < 5:  # several more idle ticks
            worker.idle.clear()
            self.assertTrue(worker.idle.wait(WAIT))
            ticks += 1
        rows = self.run_sql("SELECT state FROM prks.jobs WHERE kind = %s", (prune.PRUNE_KIND,))
        self.assertEqual([row["state"] for row in rows], ["succeeded"])

    def test_a_worker_without_the_prune_handler_enqueues_no_prune(self) -> None:
        worker = self.start_worker()
        self.assertTrue(worker.idle.wait(WAIT))
        self.assertEqual(self.run_sql("SELECT count(*) AS n FROM prks.jobs")[0]["n"], 0)


class PruneTests(WorkerTestCase):
    def finished(self, state: str, days: float) -> Any:
        rows = self.run_sql(
            "INSERT INTO prks.jobs (kind, state, finished_at) "
            "VALUES ('test.old', %s, CASE WHEN %s THEN now() - make_interval(secs => %s) END) RETURNING id",
            (state, state in ("succeeded", "failed", "cancelled"), days * 86400),
        )
        return rows[0]["id"]

    def test_prune_deletes_old_succeeded_and_cancelled_jobs_only_in_batches(self) -> None:
        old = [self.finished("succeeded", 8) for _ in range(5)] + [self.finished("cancelled", 30)]
        kept = [
            self.finished("succeeded", 6),
            self.finished("cancelled", 1),
            self.finished("failed", 30),
            self.finished("queued", 30),
        ]
        registry = HandlerRegistry()
        registry.register(prune.PRUNE_KIND, prune.prune_jobs)
        job_id = self.enqueue_instance(kind=prune.PRUNE_KIND, priority=10)
        job = self.claim()
        assert job is not None and job.id == job_id
        with patch.object(prune, "BATCH", 2):
            self.assertIs(run_job(self.db, job, registry=registry, lease=LEASE).value, "succeeded")
        remaining = {row["id"] for row in self.run_sql("SELECT id FROM prks.jobs")}
        self.assertEqual(remaining & set(old), set())
        self.assertEqual(remaining, set(kept) | {job_id})
        self.assertEqual(self.row(job_id)["progress"], {"deleted": 6})  # type: ignore[arg-type]


class GracefulStopTests(WorkerTestCase):
    def test_a_running_job_finishes_and_nothing_more_is_claimed(self) -> None:
        inside, go = self.blocking_handler()
        first = self.enqueue_instance()
        worker = self.start_worker()
        self.assertTrue(inside.wait(WAIT))
        second = self.enqueue_instance()
        stopper = threading.Thread(target=worker.stop)
        stopper.start()
        stopper.join(WAIT)
        go.set()
        self.worker_thread.join(WAIT)
        self.assertFalse(self.worker_thread.is_alive())
        self.assertEqual(self.effects(), ["done"])
        self.assertEqual((self.state(first), self.state(second)), ("succeeded", "queued"))  # type: ignore[arg-type]

    def test_a_job_past_the_deadline_is_released_and_reclaimed_once(self) -> None:
        inside, go = self.blocking_handler()
        job_id = self.enqueue_instance()
        worker = self.start_worker(stop_grace=timedelta(milliseconds=100))
        self.assertTrue(inside.wait(WAIT))
        self.stop_worker(worker)  # returns after the grace period, without the job
        row = self.row(job_id)  # type: ignore[arg-type]
        self.assertEqual((row["state"], row["attempts"], row["lease_id"]), ("queued", 0, None))
        self.assertEqual(worker.stats.snapshot()["released"], 1)
        again = self.claim("next")
        assert again is not None
        self.assertEqual((again.id, again.attempts), (job_id, 1))
        self.assertIsNone(self.claim("third"))
        # The abandoned handler resumes: its fenced write is refused.
        go.set()
        wait_until(lambda: worker.stats.snapshot()["lost"] == 1, "the abandoned handler to finish")
        self.assertEqual(self.effects(), [])

    def test_a_release_that_fails_still_abandons_the_run(self) -> None:
        # The handler's own transaction or an outage can make the release fail:
        # the run is marked lost anyway, so its heartbeat stops and it writes nothing.
        inside, go = self.blocking_handler()
        job_id = self.enqueue_instance()
        worker = self.start_worker(stop_grace=timedelta(0))
        self.assertTrue(inside.wait(WAIT))
        with patch.object(queue, "release", side_effect=DatabaseUnavailableError()):
            self.stop_worker(worker)
        self.assertEqual(worker.stats.snapshot()["errors"], 1)
        go.set()
        wait_until(lambda: worker.stats.snapshot()["lost"] == 1, "the abandoned handler to finish")
        self.assertEqual(self.effects(), [])
        row = self.row(job_id)  # type: ignore[arg-type]
        self.assertEqual(row["state"], "running")  # left for its lease to expire

    def test_an_abandoned_run_that_stops_on_lost_is_neither_completed_nor_failed(self) -> None:
        # Its lease still matches until it expires: stopping as ``lost`` asks
        # must not record the unfinished run succeeded, or spend an attempt.
        for ending in ("return", "raise"):
            with self.subTest(ending=ending):
                inside, go = threading.Event(), threading.Event()

                def handler(
                    run: JobRun, ending: str = ending,
                    inside: threading.Event = inside, go: threading.Event = go,
                ) -> None:
                    inside.set()
                    go.wait(WAIT)
                    if run.lost:
                        if ending == "raise":
                            raise RuntimeError("stopped")
                        return
                    run.command(lambda tx: tx.execute(INSERT_EFFECT, ("done",)))

                self.registry.register(f"test.{ending}", handler)
                job_id = self.enqueue_instance(kind=f"test.{ending}")
                worker = self.start_worker(stop_grace=timedelta(0))
                self.assertTrue(inside.wait(WAIT))
                with patch.object(queue, "release", side_effect=DatabaseUnavailableError()):
                    self.stop_worker(worker)
                go.set()
                wait_until(lambda w=worker: w.stats.snapshot()["lost"] == 1, "the abandoned handler to finish")
                row = self.row(job_id)  # type: ignore[arg-type]
                self.assertEqual((row["state"], row["attempts"], row["last_error"]), ("running", 1, None))
                self.assertEqual(self.effects(), [])
                self.run_sql("UPDATE prks.jobs SET state = 'cancelled', finished_at = now() WHERE state = 'running'")

    def test_sigterm_lets_the_running_job_finish_then_the_process_exits(self) -> None:
        go_file = os.path.join(self.tmp_dir(), "go")
        job_id = self.enqueue_instance(kind="test.child", payload={"go": go_file})
        child = subprocess.Popen(
            [sys.executable, "-m", "tests.pg.worker_child", self.harness.conninfo(self.name)],
            cwd=_PROJECT_DIR, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self.addCleanup(lambda: child.poll() is None and child.kill())
        assert child.stdout is not None
        self.assertEqual(child.stdout.readline().strip(), "running")
        child.send_signal(signal.SIGTERM)
        with open(go_file, "w", encoding="utf-8"):
            pass
        _out, err = child.communicate(timeout=WAIT)
        self.assertEqual(child.returncode, 0, err)
        self.assertEqual(self.state(job_id), "succeeded")  # type: ignore[arg-type]
        self.assertEqual(self.effects(), ["child"])

    def tmp_dir(self) -> str:
        path = tempfile.mkdtemp(prefix="prks-worker-")
        self.addCleanup(shutil.rmtree, path, True)
        return path


class DiagnosticsTests(WorkerTestCase):
    def test_queue_depth_counts_and_ages_by_kind_and_state_only(self) -> None:
        self.enqueue_instance(kind="test.a")
        self.enqueue_instance(kind="test.a", payload={"title": "Secret Title"})
        self.enqueue_library(kind="test.b")
        self.run_sql("UPDATE prks.jobs SET created_at = now() - interval '90 seconds' WHERE kind = 'test.b'")
        depth = diagnostics.queue_depth(self.db)
        self.assertEqual([(d.kind, d.state, d.count) for d in depth], [("test.a", "queued", 2), ("test.b", "queued", 1)])
        self.assertGreaterEqual(depth[1].oldest_age_seconds, 90)
        self.assertNotIn("Secret", repr(depth))
        self.assertNotIn(self.lib_a, repr(depth))

    def test_unknown_counters_are_refused(self) -> None:
        stats = diagnostics.WorkerStats()
        with self.assertRaises(ValueError):
            stats.add("payload")
        self.assertEqual(set(stats.snapshot()), set(diagnostics.COUNTERS))


class SettingsTests(WorkerTestCase):
    def test_non_positive_intervals_are_refused(self) -> None:
        for kwargs in ({"lease": timedelta(0)}, {"poll": timedelta(0)}, {"prune_every": timedelta(-1)},
                       {"stop_grace": timedelta(seconds=-1)}):
            with self.subTest(**{k: str(v) for k, v in kwargs.items()}), self.assertRaises(ValueError):
                WorkerSettings(**kwargs)


# --- prks worker and the §11.1 start -------------------------------------------------


class WorkerStartTests(RootPairingTestCase):
    def worker_main(
        self, name: str, root: str, registry: HandlerRegistry, *extra: str, schema_dir: str = SCHEMA_DIR
    ) -> dict[str, Any]:
        """Run ``prks worker`` on a thread; returns its worker, thread, exit code and stderr."""
        state: dict[str, Any] = {"stderr": io.StringIO(), "exit": []}
        made = threading.Event()

        def on_worker(worker: Worker) -> None:
            state["worker"] = worker
            made.set()

        def open_database(_env: Any) -> Database:
            return Database(DatabaseConfig(conninfo=self.harness.conninfo(name), role="worker", pool_min=0))

        def body() -> None:
            state["exit"].append(
                main(
                    ["--root", root, "--testing", *extra],
                    environ={},
                    stderr=state["stderr"],
                    open_database=open_database,
                    settings=WorkerSettings(lease=LEASE, poll=timedelta(milliseconds=50)),
                    registry=registry,
                    on_worker=on_worker,
                    schema_dir=schema_dir,
                )
            )
            made.set()

        thread = threading.Thread(target=body)
        thread.start()
        state["thread"] = thread
        self.addCleanup(self.stop_main, state)
        self.assertTrue(made.wait(WAIT))
        return state

    def stop_main(self, state: dict[str, Any]) -> None:
        worker = state.get("worker")
        if worker is not None:
            worker.stop()
        state["thread"].join(WAIT)

    def finish(self, state: dict[str, Any]) -> int:
        self.stop_main(state)
        self.assertFalse(state["thread"].is_alive())
        return int(state["exit"][0])

    def counting_registry(self) -> tuple[HandlerRegistry, threading.Event]:
        ran = threading.Event()
        registry = HandlerRegistry()
        registry.register("test.instance", lambda _run: ran.set())
        return registry, ran

    def enqueue(self, name: str) -> Any:
        return self.db(name).command(lambda tx: queue.enqueue(tx, "test.instance"))

    def job_state(self, name: str, job_id: Any) -> str:
        return str(
            self.db(name).query(
                lambda tx: tx.execute("SELECT state FROM prks.jobs WHERE id = %s", (job_id,)).fetchone()
            )["state"]
        )

    def bootstrapped(self) -> tuple[str, str]:
        name, root = self.database(), self.new_root()
        lease = root_binding.acquire_shared_root_lease(root)
        try:
            self.bootstrap(name, root, lease=lease)
        finally:
            lease.release()
        return name, root

    def test_a_worker_started_before_bootstrap_claims_nothing_then_checks_and_bumps(self) -> None:
        name, root = self.database(), self.new_root()
        job_id = self.enqueue(name)
        registry, ran = self.counting_registry()
        with patch.object(root_pairing, "start_process", wraps=root_pairing.start_process) as checks:
            state = self.worker_main(name, root, registry)
            worker = state["worker"]
            # Unconfigured, then bootstrapping (a claim that stopped half-way): no claim.
            wait_until(lambda: checks.call_count >= 3, "several start checks")
            self.assertFalse(worker.started.is_set())
            with self.assertRaises(Crash):
                self.bootstrap(name, root, faults=crash_at(root_pairing.FAULT_AFTER_CLAIM))
            self.assertEqual(self.instance(name)["setup_state"], "bootstrapping")  # type: ignore[index]
            seen = checks.call_count
            wait_until(lambda: checks.call_count >= seen + 3, "more start checks")
            self.assertFalse(worker.started.is_set())
        self.assertEqual(self.job_state(name, job_id), "queued")
        self.bootstrap(name, root)  # setup is ready at generation 1
        self.assertTrue(worker.started.wait(WAIT))
        self.assertTrue(ran.wait(WAIT))
        self.assert_paired(name, root, 2)  # the worker's own bump
        self.assertEqual(self.finish(state), EXIT_OK)

    def test_a_worker_for_a_foreign_root_is_refused_and_claims_nothing(self) -> None:
        name, _root = self.bootstrapped()
        other = self.new_root("other")
        job_id = self.enqueue(name)
        registry, ran = self.counting_registry()
        state = self.worker_main(name, other, registry)
        state["thread"].join(WAIT)
        self.assertEqual(state["exit"], [EXIT_REFUSED])
        self.assertIn("belongs to storage root", state["stderr"].getvalue())
        self.assertNotIn(self.harness.conninfo(name), state["stderr"].getvalue())
        self.assertFalse(ran.is_set())
        self.assertEqual(self.job_state(name, job_id), "queued")
        # Its shared lease is gone: an exclusive holder can take the root.
        root_binding.open_storage_root(StorageConfig.for_testing(other), register=False).release()

    def test_a_worker_after_a_failed_web_reconcile_gets_past_the_start_key_and_reconciles(self) -> None:
        name, root = self.bootstrapped()
        with self.assertRaises(Crash):
            self.start(name, root, faults=crash_at(root_pairing.FAULT_AFTER_DATABASE_BUMP))
        with patch.object(root_marker, "replace_file_atomically", side_effect=OSError("read-only")):
            self.refused("marker_write_failed", self.start, name, root)
        registry, ran = self.counting_registry()
        self.enqueue(name)
        state = self.worker_main(name, root, registry)
        self.assertTrue(state["worker"].started.wait(WAIT))
        self.assertTrue(ran.wait(WAIT))
        self.assert_paired(name, root, 3)  # reconciled to 2, then bumped
        self.assertEqual(self.finish(state), EXIT_OK)

    def test_a_retryable_refusal_waits_and_retries(self) -> None:
        name, root = self.bootstrapped()
        registry, ran = self.counting_registry()
        self.enqueue(name)
        failing = patch.object(root_marker, "replace_file_atomically", side_effect=OSError("full"))
        failing.start()
        try:
            with patch.object(root_pairing, "start_process", wraps=root_pairing.start_process) as checks:
                state = self.worker_main(name, root, registry)
                wait_until(lambda: checks.call_count >= 3, "several start checks")
                self.assertFalse(state["worker"].started.is_set())
        finally:
            failing.stop()
        self.assertTrue(state["worker"].started.wait(WAIT))
        self.assertTrue(ran.wait(WAIT))
        self.assertEqual(self.finish(state), EXIT_OK)

    def schema_with_pending_file(self) -> str:
        directory = tempfile.mkdtemp(prefix="prks-schema-")
        self.addCleanup(shutil.rmtree, directory, True)
        for entry in os.listdir(SCHEMA_DIR):
            if entry.endswith(".sql"):
                shutil.copy(os.path.join(SCHEMA_DIR, entry), directory)
        with open(os.path.join(directory, "990_later.sql"), "w", encoding="utf-8") as handle:
            handle.write("CREATE TABLE prks.later (id int);\n")
        return directory

    def test_a_worker_newer_than_the_schema_waits_for_migrate_without_bumping(self) -> None:
        name, root = self.bootstrapped()
        before = self.db_state(name)
        job_id = self.enqueue(name)
        registry, ran = self.counting_registry()
        directory = self.schema_with_pending_file()
        with patch.object(worker_module, "verify_schema", wraps=worker_module.verify_schema) as checks:
            state = self.worker_main(name, root, registry, schema_dir=directory)
            wait_until(lambda: checks.call_count >= 3, "several schema checks")
            self.assertFalse(state["worker"].started.is_set())
            self.assertEqual(self.job_state(name, job_id), "queued")
            self.assertEqual(self.db_state(name), before)  # no bump against the old schema
            migrate(self.harness.conninfo(name), directory=directory)
            self.assertTrue(state["worker"].started.wait(WAIT))
        self.assertTrue(ran.wait(WAIT))
        self.assert_paired(name, root, before.generation + 1)
        self.assertEqual(self.finish(state), EXIT_OK)

    def test_a_worker_older_than_the_schema_is_refused_and_claims_nothing(self) -> None:
        name, root = self.bootstrapped()
        before = self.db_state(name)
        job_id = self.enqueue(name)
        self.db(name).command(
            lambda tx: tx.execute(
                "INSERT INTO prks.schema_migrations (version, name, sha256) VALUES (999, 'future', %s)",
                ("0" * 64,),
            )
        )
        registry, ran = self.counting_registry()
        state = self.worker_main(name, root, registry)
        state["thread"].join(WAIT)
        self.assertEqual(state["exit"], [EXIT_REFUSED])
        self.assertIn("newer than this PRKS", state["stderr"].getvalue())
        self.assertFalse(ran.is_set())
        self.assertEqual(self.job_state(name, job_id), "queued")
        self.assertEqual(self.db_state(name), before)

    def test_a_root_held_exclusively_is_refused(self) -> None:
        name, root = self.bootstrapped()
        held = root_binding.open_storage_root(StorageConfig.for_testing(root), register=False)
        self.addCleanup(held.release)
        registry, _ran = self.counting_registry()
        state = self.worker_main(name, root, registry)
        state["thread"].join(WAIT)
        self.assertEqual(state["exit"], [EXIT_REFUSED])
        self.assertIn("root_in_use", state["stderr"].getvalue())
