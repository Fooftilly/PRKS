"""Running one claimed job: outcomes, heartbeat and lease/epoch fencing (§12.1, §11.4; L-J-1 #627)."""

from __future__ import annotations

import threading
import time
from datetime import timedelta
from typing import Any

from backend.domain.identity.authorization import AccessRefused, Capability
from backend.jobs import queue
from backend.jobs.queue import LeaseLost
from backend.jobs.runner import JobRun, Outcome, run_job
from tests.pg.jobs_fixtures import LEASE, NO_BACKOFF, JobsTestCase, set_revision

INSERT_EFFECT = "INSERT INTO prks.instance_effects (note) VALUES (%s)"


class RunnerTestCase(JobsTestCase):
    def setUp(self) -> None:
        super().setUp()
        # An instance-level table for instance-job side effects (test database only).
        self.run_sql("CREATE TABLE prks.instance_effects (note text NOT NULL)")

    def effects(self) -> list[str]:
        return [row["note"] for row in self.run_sql("SELECT note FROM prks.instance_effects ORDER BY note")]

    def run_one(self, job: Any, **kwargs: Any) -> Outcome:
        kwargs.setdefault("lease", LEASE)
        return run_job(self.db, job, registry=self.registry, **kwargs)

    def steal(self, job_id: Any) -> Any:
        """Another worker reclaims the job after its lease ran out."""
        self.expire(job_id)
        other = self.claim("thief")
        self.assertIsNotNone(other)
        return other


class OutcomeTests(RunnerTestCase):
    def test_an_instance_job_commits_its_fenced_command_and_succeeds(self) -> None:
        def handler(run: JobRun) -> None:
            run.command(lambda tx: tx.execute(INSERT_EFFECT, (run.payload["note"],)))

        self.registry.register("test.instance", handler)
        job_id = self.enqueue_instance(payload={"note": "done"})
        job = self.claim()
        assert job is not None
        self.assertIs(self.run_one(job), Outcome.SUCCEEDED)
        self.assertEqual(self.effects(), ["done"])
        self.assertEqual(self.state(job_id), "succeeded")  # type: ignore[arg-type]

    def test_a_library_job_writes_through_its_job_context(self) -> None:
        self.enqueue_library(payload={"scope_id": "a2", "value": 11})
        job = self.claim()
        assert job is not None
        self.assertIs(self.run_one(job), Outcome.SUCCEEDED)
        self.assertEqual(self.revisions()[(self.lib_a, "a2")], 11)
        self.assertEqual(self.revisions()[(self.lib_b, "b1")], 1)

    def test_a_handler_error_is_a_handled_failure_recorded_as_a_code(self) -> None:
        secret = "Secret Title of /home/someone/private.pdf"

        def handler(_run: JobRun) -> None:
            raise ValueError(secret)

        self.registry.register("test.instance", handler)
        job_id = self.enqueue_instance(max_attempts=2)
        job = self.claim()
        assert job is not None
        self.assertIs(self.run_one(job, backoff=NO_BACKOFF), Outcome.RETRYING)
        self.assertEqual(self.row(job_id)["last_error"], "ValueError")  # type: ignore[arg-type]
        again = self.claim()
        assert again is not None
        self.assertIs(self.run_one(again), Outcome.FAILED)
        self.assertEqual(self.state(job_id), "failed")  # type: ignore[arg-type]
        logged = self.logs.getvalue()
        self.assertIn("job_retrying", logged)
        self.assertIn("job_failed", logged)
        self.assertNotIn("Secret", logged)
        self.assertNotIn("private", logged)

    def test_an_unknown_kind_is_a_handled_failure(self) -> None:
        job_id = self.enqueue_instance(kind="test.nobody", max_attempts=1)
        job = self.claim()
        assert job is not None
        self.assertIs(self.run_one(job), Outcome.FAILED)
        self.assertEqual(self.row(job_id)["last_error"], "unknown_kind")  # type: ignore[arg-type]

    def test_an_instance_job_cannot_take_a_library_context_and_vice_versa(self) -> None:
        seen: list[str] = []

        def instance_handler(run: JobRun) -> None:
            try:
                run.context()
            except ValueError:
                seen.append("no context")

        def library_handler(run: JobRun) -> None:
            try:
                run.command(lambda tx: None)
            except ValueError:
                seen.append("no instance command")

        self.registry.register("test.instance", instance_handler)
        self.registry.register("test.lib", library_handler)
        self.enqueue_instance()
        self.enqueue_library(kind="test.lib")
        while (job := self.claim()) is not None:
            self.assertIs(self.run_one(job), Outcome.SUCCEEDED)
        self.assertEqual(sorted(seen), ["no context", "no instance command"])


    def test_a_non_positive_lease_or_heartbeat_interval_is_refused_before_running(self) -> None:
        ran: list[bool] = []
        self.registry.register("test.instance", lambda _run: ran.append(True))
        self.enqueue_instance()
        job = self.claim()
        assert job is not None
        for kwargs in (
            {"heartbeat_every": timedelta(0)},
            {"heartbeat_every": timedelta(seconds=-1)},
            {"lease": timedelta(0)},
            {"lease": timedelta(microseconds=1)},  # a third of it rounds to zero
        ):
            with self.subTest(**{k: str(v) for k, v in kwargs.items()}), self.assertRaises(ValueError):
                self.run_one(job, **kwargs)
        self.assertEqual(ran, [])
        self.assertEqual(self.state(job.id), "running")


class FencingTests(RunnerTestCase):
    def pause_in(self, kind: str, body: Any) -> tuple[threading.Event, threading.Event]:
        inside, go = threading.Event(), threading.Event()

        def handler(run: JobRun) -> None:
            inside.set()
            if not go.wait(30):
                raise TimeoutError()
            body(run)

        self.registry.register(kind, handler)
        return inside, go

    def test_a_worker_whose_job_was_reclaimed_writes_nothing_and_records_nothing(self) -> None:
        inside, go = self.pause_in("test.lib", set_revision)
        job_id = self.enqueue_library(kind="test.lib", payload={"scope_id": "a1", "value": 99})
        job = self.claim("slow")
        assert job is not None
        thread, result, error = self.in_thread(lambda: self.run_one(job, heartbeat_every=timedelta(hours=1)))
        self.assertTrue(inside.wait(30))
        thief = self.steal(job_id)
        before = self.row(job_id)  # type: ignore[arg-type]
        go.set()
        thread.join(30)
        self.assertEqual((error, result), ([], [Outcome.LOST]))
        self.assertEqual(self.revisions()[(self.lib_a, "a1")], 1)
        self.assertEqual(self.row(job_id), before)  # type: ignore[arg-type]
        self.assertEqual(before["lease_id"], thief.lease_id)

    def test_an_instance_command_after_the_lease_was_lost_writes_nothing(self) -> None:
        lost: list[bool] = []

        def write(run: JobRun) -> None:
            try:
                run.command(lambda tx: tx.execute(INSERT_EFFECT, ("stale",)))
            except LeaseLost:
                lost.append(run.lost)
                raise

        inside, go = self.pause_in("test.instance", write)
        job_id = self.enqueue_instance()
        job = self.claim("slow")
        assert job is not None
        thread, result, _error = self.in_thread(lambda: self.run_one(job, heartbeat_every=timedelta(hours=1)))
        self.assertTrue(inside.wait(30))
        self.steal(job_id)
        go.set()
        thread.join(30)
        self.assertEqual((result, lost), ([Outcome.LOST], [True]))
        self.assertEqual(self.effects(), [])

    def blocked_command(self, job: Any) -> tuple[threading.Thread, list[object], list[BaseException]]:
        """Start the job's fenced write; it waits on a row lock someone else holds."""
        run = JobRun(self.db, job, lease=LEASE)
        if job.library_id is None:
            write: Any = lambda: run.command(lambda tx: tx.execute(INSERT_EFFECT, ("stale",)))
        else:
            write = lambda: set_revision(run)
        thread, result, error = self.in_thread(write)
        deadline = time.monotonic() + 30
        while self.lock_waiters() == 0:
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.01)
        return thread, result, error

    def test_a_lease_that_expires_while_the_command_waits_for_the_row_lock_writes_nothing(self) -> None:
        # The lease is checked only once the row lock is granted, by the clock
        # at that moment. The holder does not modify the row, so PostgreSQL
        # does not re-evaluate the statement on a newer row version.
        for library in (False, True):
            with self.subTest(library_job=library):
                self.expire_while_blocked(library)
            # Settle it either way, so the next case does not reclaim it.
            self.run_sql(
                "UPDATE prks.jobs SET state = 'cancelled', lease_id = NULL, finished_at = now() "
                "WHERE state = 'running'"
            )

    def expire_while_blocked(self, library: bool) -> None:
        if library:
            self.enqueue_library(payload={"scope_id": "a1", "value": 99})
        else:
            self.enqueue_instance()
        job = queue.claim(self.db, "w1", lease=timedelta(seconds=2))
        assert job is not None
        holder = self.open_tx()
        holder.execute("SELECT 1 FROM prks.jobs WHERE id = %s FOR UPDATE", (job.id,))
        thread, result, error = self.blocked_command(job)
        # The command started while the lease was valid; let it run out during the wait.
        live = self.run_sql("SELECT locked_until > clock_timestamp() AS live FROM prks.jobs WHERE id = %s", (job.id,))
        self.assertTrue(live[0]["live"])
        deadline = time.monotonic() + 30
        while self.run_sql(
            "SELECT locked_until > clock_timestamp() AS live FROM prks.jobs WHERE id = %s", (job.id,)
        )[0]["live"]:
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.05)
        holder.rollback()
        thread.join(30)
        self.assertEqual(result, [])
        self.assertEqual(len(error), 1)
        self.assertIsInstance(error[0], AccessRefused if library else LeaseLost)
        if library:
            self.assertEqual(self.revisions()[(self.lib_a, "a1")], 1)
        else:
            self.assertEqual(self.effects(), [])

    def test_a_lease_expired_by_the_lock_holder_writes_nothing(self) -> None:
        # The holder moves locked_until into the past and commits while the command waits.
        self.enqueue_instance()
        job = self.claim()
        assert job is not None
        holder = self.open_tx()
        holder.execute(
            "UPDATE prks.jobs SET locked_until = now() - interval '1 second' WHERE id = %s", (job.id,)
        )
        thread, result, error = self.blocked_command(job)
        holder.commit()
        thread.join(30)
        self.assertEqual((result, [type(e) for e in error]), ([], [LeaseLost]))
        self.assertEqual(self.effects(), [])

    def test_a_restore_during_a_job_fences_its_later_commands_and_completion(self) -> None:
        inside, go = self.pause_in("test.lib", set_revision)
        job_id = self.enqueue_library(kind="test.lib", payload={"scope_id": "a1", "value": 42})
        job = self.claim()
        assert job is not None
        thread, result, _error = self.in_thread(lambda: self.run_one(job, heartbeat_every=timedelta(hours=1)))
        self.assertTrue(inside.wait(30))
        # R4: the epoch moves on and the Library's jobs are cancelled.
        self.restore()
        self.run_sql(
            "UPDATE prks.jobs SET state = 'cancelled', lease_id = NULL, last_error = 'restore_superseded', "
            "finished_at = now() WHERE library_id = %s AND state IN ('queued', 'running')", (self.lib_a,),
        )
        go.set()
        thread.join(30)
        self.assertEqual(result, [Outcome.LOST])
        self.assertEqual(self.revisions()[(self.lib_a, "a1")], 1)
        row = self.row(job_id)  # type: ignore[arg-type]
        self.assertEqual((row["state"], row["last_error"]), ("cancelled", "restore_superseded"))

    def test_a_restore_alone_fences_a_job_of_the_old_epoch(self) -> None:
        # Even if a job of the old epoch was not cancelled, its commands are
        # refused, and it ends cancelled rather than queued forever.
        inside, go = self.pause_in("test.lib", set_revision)
        self.enqueue_library(kind="test.lib", dedupe_key="d", payload={"scope_id": "a1", "value": 42})
        job = self.claim()
        assert job is not None
        thread, result, _error = self.in_thread(
            lambda: self.run_one(job, heartbeat_every=timedelta(hours=1), backoff=NO_BACKOFF)
        )
        self.assertTrue(inside.wait(30))
        self.restore()
        go.set()
        thread.join(30)
        self.assertEqual(result, [Outcome.CANCELLED])
        row = self.row(job.id)
        self.assertEqual((row["state"], row["last_error"]), ("cancelled", "LIBRARY_EPOCH_CHANGED"))
        self.assertEqual(self.revisions()[(self.lib_a, "a1")], 1)
        # The same work enqueued in the new epoch is not coalesced into the old row.
        fresh = self.enqueue_library(kind="test.lib", dedupe_key="d", payload={"scope_id": "a1", "value": 43})
        self.assertIsNotNone(fresh)
        again = self.claim()
        assert again is not None
        self.assertEqual(again.id, fresh)


class HeartbeatTests(RunnerTestCase):
    def locked_until(self, job_id: Any) -> Any:
        return self.row(job_id)["locked_until"]

    def test_the_heartbeat_keeps_a_long_job_leased(self) -> None:
        extended = threading.Event()

        def long_job(run: JobRun) -> None:
            first = self.locked_until(run.job.id)
            deadline = time.monotonic() + 30
            while self.locked_until(run.job.id) <= first:
                if time.monotonic() > deadline:
                    raise TimeoutError()
                time.sleep(0.02)
            extended.set()

        self.registry.register("test.instance", long_job)
        self.enqueue_instance()
        job = self.claim()
        assert job is not None
        self.assertIs(self.run_one(job, heartbeat_every=timedelta(milliseconds=20)), Outcome.SUCCEEDED)
        self.assertTrue(extended.is_set())

    def test_progress_is_reported_between_transactions_and_refused_inside_one(self) -> None:
        refused: list[str] = []

        def batched(run: JobRun) -> None:
            def inside(_tx_or_scope: object) -> None:
                try:
                    run.heartbeat({"done": 0})
                except RuntimeError:
                    refused.append("refused")

            if run.job.library_id is None:
                run.command(inside)
            else:
                run.library_command(Capability.EDIT_LIBRARY, inside)
            run.heartbeat({"done": 1})  # between transactions: no wait on its own lock

        self.registry.register("test.instance", batched)
        self.registry.register("test.lib", batched)
        instance_id = self.enqueue_instance()
        library_id = self.enqueue_library(kind="test.lib")
        started = time.monotonic()
        while (job := self.claim()) is not None:
            self.assertIs(self.run_one(job, heartbeat_every=timedelta(hours=1)), Outcome.SUCCEEDED)
        self.assertLess(time.monotonic() - started, 5)  # never waited out lock_timeout
        self.assertEqual(refused, ["refused", "refused"])
        for job_id in (instance_id, library_id):
            self.assertEqual(self.row(job_id)["progress"], {"done": 1})  # type: ignore[arg-type]

    def test_the_heartbeat_tells_the_handler_its_lease_is_gone(self) -> None:
        stolen = threading.Event()

        def cooperative(run: JobRun) -> None:
            self.assertTrue(stolen.wait(30))
            deadline = time.monotonic() + 30
            while not run.lost:
                if time.monotonic() > deadline:
                    raise TimeoutError()
                time.sleep(0.02)
            run.check()  # raises LeaseLost

        self.registry.register("test.instance", cooperative)
        job_id = self.enqueue_instance()
        job = self.claim()
        assert job is not None
        thread, result, _error = self.in_thread(
            lambda: self.run_one(job, heartbeat_every=timedelta(milliseconds=20))
        )
        self.steal(job_id)
        stolen.set()
        thread.join(30)
        self.assertEqual(result, [Outcome.LOST])
        self.assertEqual(self.row(job_id)["locked_by"], "thief")  # type: ignore[arg-type]

