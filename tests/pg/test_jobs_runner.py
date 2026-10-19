"""Running one claimed job: outcomes, heartbeat and lease/epoch fencing (§12.1, §11.4; L-J-1 #627)."""

from __future__ import annotations

import threading
import time
from datetime import timedelta
from typing import Any

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
        # Even if a job of the old epoch was not cancelled, its commands are refused.
        inside, go = self.pause_in("test.lib", set_revision)
        self.enqueue_library(kind="test.lib", payload={"scope_id": "a1", "value": 42})
        job = self.claim()
        assert job is not None
        thread, result, _error = self.in_thread(
            lambda: self.run_one(job, heartbeat_every=timedelta(hours=1), backoff=NO_BACKOFF)
        )
        self.assertTrue(inside.wait(30))
        self.restore()
        go.set()
        thread.join(30)
        self.assertEqual(result, [Outcome.RETRYING])
        self.assertEqual(self.row(job.id)["last_error"], "LIBRARY_EPOCH_CHANGED")
        self.assertEqual(self.revisions()[(self.lib_a, "a1")], 1)
        self.assertIsNone(self.claim())  # and it is never claimed again


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

