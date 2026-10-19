"""The job queue statements (§12.1, §13.3; L-J-1 #627)."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import uuid
from datetime import timedelta
from pathlib import Path

from backend.jobs import queue
from backend.jobs.queue import JobState
from backend.jobs.runner import Outcome, run_job
from tests.pg.jobs_fixtures import LEASE, NO_BACKOFF, SET_KIND, JobsTestCase

REPO = Path(__file__).resolve().parents[2]


class EnqueueTests(JobsTestCase):
    def test_a_library_job_records_the_epoch_its_command_checked(self) -> None:
        self.restore()  # epoch 1 -> 2
        job_id = self.enqueue_library(payload={"scope_id": "a1", "value": 3})
        assert job_id is not None
        row = self.row(job_id)
        self.assertEqual((row["library_id"], row["library_epoch"]), (self.lib_a, 2))
        self.assertEqual((row["state"], row["attempts"], row["kind"]), ("queued", 0, SET_KIND))
        self.assertEqual(row["payload"], {"scope_id": "a1", "value": 3})
        self.assertEqual(row["created_by"], None)

    def test_an_instance_job_has_no_library(self) -> None:
        job_id = self.enqueue_instance(payload={"n": 1}, singleton_key="s")
        assert job_id is not None
        row = self.row(job_id)
        self.assertIsNone(row["library_id"])
        self.assertIsNone(row["library_epoch"])
        self.assertEqual(row["singleton_key"], "s")

    def test_a_queued_duplicate_coalesces(self) -> None:
        first = self.enqueue_instance(dedupe_key="d")
        self.assertIsNotNone(first)
        self.assertIsNone(self.enqueue_instance(dedupe_key="d"))
        self.assertIsNotNone(self.enqueue_instance(kind="test.other", dedupe_key="d"))
        self.assertEqual(len(self.run_sql("SELECT 1 FROM prks.jobs WHERE dedupe_key = 'd'")), 2)

    def test_the_job_exists_only_if_the_command_commits(self) -> None:
        class Boom(Exception):
            pass

        def enqueue_then_fail(tx: object) -> None:
            queue.enqueue(tx, "test.instance")  # type: ignore[arg-type]
            raise Boom()

        with self.assertRaises(Boom):
            self.db.command(enqueue_then_fail)
        self.assertEqual(self.run_sql("SELECT 1 FROM prks.jobs"), [])

    def test_workers_are_notified_at_commit_only(self) -> None:
        listener = self.connect()
        self.addCleanup(listener.close)
        listener.execute("LISTEN prks_jobs")
        inside = threading.Event()
        go = threading.Event()

        def enqueue_and_wait(tx: object) -> None:
            queue.enqueue(tx, "test.instance")  # type: ignore[arg-type]
            inside.set()
            go.wait(30)

        thread, _result, error = self.in_thread(lambda: self.db.command(enqueue_and_wait))
        self.assertTrue(inside.wait(30))
        self.assertEqual(list(listener.notifies(timeout=0.3)), [])
        go.set()
        thread.join(30)
        self.assertEqual(error, [])
        received = list(listener.notifies(timeout=10, stop_after=1))
        self.assertEqual([(n.channel, n.payload) for n in received], [("prks_jobs", "")])
        # A coalesced enqueue adds no job and wakes nobody.
        self.enqueue_instance(dedupe_key="x")
        list(listener.notifies(timeout=10, stop_after=1))
        self.enqueue_instance(dedupe_key="x")
        self.assertEqual(list(listener.notifies(timeout=0.3)), [])

    def test_bad_arguments_are_refused(self) -> None:
        for kind in ("", "nodot", "Upper.case", "a..b", "a.b c"):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.enqueue_instance(kind=kind)
        with self.assertRaises(ValueError):
            self.enqueue_instance(max_attempts=0)
        with self.assertRaises(ValueError):
            self.enqueue_instance(dedupe_key="")
        with self.assertRaises(ValueError):
            self.enqueue_instance(delay=timedelta(seconds=-1))
        self.assertEqual(self.run_sql("SELECT 1 FROM prks.jobs"), [])


class ClaimTests(JobsTestCase):
    def test_claims_by_priority_then_age_and_skips_future_jobs(self) -> None:
        later = self.enqueue_instance(delay=timedelta(hours=1), priority=9)
        low = self.enqueue_instance(kind="test.low")
        high = self.enqueue_instance(kind="test.high", priority=5)
        first, second = self.claim(), self.claim()
        assert first is not None and second is not None
        self.assertEqual((first.id, second.id), (high, low))
        self.assertEqual((first.attempts, first.max_attempts), (1, 10))
        self.assertIsNone(self.claim())
        self.assertEqual(self.state(later), "queued")
        row = self.row(first.id)
        self.assertEqual((row["state"], row["locked_by"], row["lease_id"]), ("running", "w1", first.lease_id))

    def test_jobs_of_a_library_in_maintenance_are_not_claimed(self) -> None:
        job_a = self.enqueue_library()
        self.run_sql(
            "UPDATE prks.libraries SET maintenance = 'restore', maintenance_run = gen_random_uuid() "
            "WHERE id = %s", (self.lib_a,),
        )
        job_b = self.enqueue_library(self.lib_b, payload={"scope_id": "b1", "value": 2})
        claimed = self.claim()
        assert claimed is not None
        self.assertEqual(claimed.id, job_b)
        self.assertIsNone(self.claim())
        self.assertEqual(self.state(job_a), "queued")
        self.run_sql("UPDATE prks.libraries SET maintenance = NULL, maintenance_run = NULL")
        again = self.claim()
        assert again is not None
        self.assertEqual(again.id, job_a)

    def test_jobs_of_an_older_epoch_are_not_claimed_or_reclaimed(self) -> None:
        running = self.enqueue_library(payload={"scope_id": "a2", "value": 5})
        claimed = self.claim()
        assert claimed is not None and claimed.id == running
        queued = self.enqueue_library()
        # A restore advances the epoch. (R4 also cancels the Library's jobs; here
        # they are left as they were, so only the claim's epoch check fences them.)
        self.restore()
        self.expire(claimed.id)
        self.assertIsNone(self.claim())
        self.assertEqual((self.state(running), self.state(queued)), ("running", "queued"))  # type: ignore[arg-type]
        newer = self.enqueue_library(payload={"scope_id": "a1", "value": 9})
        got = self.claim()
        assert got is not None
        self.assertEqual((got.id, got.library_epoch), (newer, 2))

    def test_a_job_with_no_attempt_left_is_not_claimed(self) -> None:
        job_id = self.enqueue_instance(max_attempts=1)
        claimed = self.claim()
        assert claimed is not None and job_id is not None
        self.expire(job_id)
        self.assertIsNone(self.claim())
        self.assertEqual(self.state(job_id), "running")

    def test_a_locked_row_is_skipped_not_waited_for(self) -> None:
        job_id = self.enqueue_instance()
        conn = self.open_tx()
        conn.execute("SELECT 1 FROM prks.jobs WHERE id = %s FOR UPDATE", (job_id,))
        self.assertIsNone(self.claim())
        conn.rollback()
        self.assertIsNotNone(self.claim())


class LeaseTests(JobsTestCase):
    def test_kill_after_claim_then_exactly_one_reclaim_and_idempotent_completion(self) -> None:
        job_id = self.enqueue_library(payload={"scope_id": "a1", "value": 7})
        env = {**os.environ, "PYTHONPATH": str(REPO)}
        child = subprocess.run(
            [sys.executable, "-m", "tests.pg.jobs_child", self.harness.conninfo(self.name), "doomed"],
            cwd=REPO, env=env, capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(child.returncode, -9, child.stderr)
        died_id, died_lease = child.stdout.split()
        self.assertEqual(uuid.UUID(died_id), job_id)
        # The killed worker's write committed; its job is still running under its lease.
        self.assertEqual(self.revisions()[(self.lib_a, "a1")], 7)
        row = self.row(job_id)
        self.assertEqual((row["state"], row["locked_by"], str(row["lease_id"])), ("running", "doomed", died_lease))
        # While the lease is live nobody reclaims it.
        self.assertIsNone(self.claim("w2"))
        self.expire(job_id)

        barrier = threading.Barrier(4)
        results: list[object] = []

        def race(name: str) -> None:
            barrier.wait(30)
            results.append(queue.claim(self.db, name, lease=LEASE))

        threads = [threading.Thread(target=race, args=(f"r{i}",)) for i in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        winners = [job for job in results if job is not None]
        self.assertEqual(len(winners), 1)
        reclaimed = winners[0]
        assert isinstance(reclaimed, queue.ClaimedJob)
        self.assertEqual((reclaimed.id, reclaimed.attempts), (job_id, 2))
        self.assertNotEqual(str(reclaimed.lease_id), died_lease)

        # The rerun repeats the same idempotent write and completes once.
        self.assertIs(run_job(self.db, reclaimed, registry=self.registry, lease=LEASE), Outcome.SUCCEEDED)
        self.assertEqual(self.revisions()[(self.lib_a, "a1")], 7)
        row = self.row(job_id)
        self.assertEqual((row["state"], row["attempts"], row["lease_id"]), ("succeeded", 2, None))
        self.assertIsNotNone(row["finished_at"])
        self.assertIsNone(self.claim("w3"))

    def test_a_stale_worker_updates_nothing(self) -> None:
        job_id = self.enqueue_instance(dedupe_key="d")
        stale = self.claim("stale")
        assert stale is not None and job_id is not None
        self.expire(job_id)
        fresh = self.claim("fresh")
        assert fresh is not None
        before = self.row(job_id)
        self.assertFalse(queue.heartbeat(self.db, stale, lease=LEASE, progress={"done": 1}))
        self.assertFalse(queue.complete(self.db, stale))
        self.assertIsNone(queue.fail(self.db, stale, "ValueError"))
        self.assertIsNone(queue.release(self.db, stale))
        self.assertEqual(self.row(job_id), before)
        # The current holder still owns it.
        self.assertTrue(queue.heartbeat(self.db, fresh, lease=LEASE, progress={"done": 2}))
        self.assertEqual(self.row(job_id)["progress"], {"done": 2})
        self.assertTrue(queue.complete(self.db, fresh))
        self.assertFalse(queue.complete(self.db, fresh))  # completing twice changes nothing

    def test_a_heartbeat_extends_the_lease(self) -> None:
        self.enqueue_instance()
        job = self.claim()
        assert job is not None
        self.run_sql("UPDATE prks.jobs SET locked_until = now() + interval '1 second' WHERE id = %s", (job.id,))
        self.assertTrue(queue.heartbeat(self.db, job, lease=timedelta(minutes=10)))
        rows = self.run_sql(
            "SELECT locked_until > now() + interval '9 minutes' AS extended FROM prks.jobs WHERE id = %s",
            (job.id,),
        )
        self.assertTrue(rows[0]["extended"])

    def test_repeated_crashes_end_failed_and_free_the_singleton_key(self) -> None:
        crashing = self.enqueue_instance(kind="test.crash", singleton_key="k", max_attempts=3)
        waiting = self.enqueue_instance(kind="test.next", singleton_key="k", delay=timedelta(0))
        assert crashing is not None and waiting is not None
        stale = None
        for attempt in (1, 2, 3):
            job = self.claim(f"w{attempt}")
            assert job is not None
            self.assertEqual((job.id, job.attempts), (crashing, attempt))
            # The queued twin of the key waits while the crashed run holds it.
            self.expire(crashing)
            stale = job
        assert stale is not None
        # No attempt is left: the expired run cannot be reclaimed, and still holds the key.
        self.assertEqual(self.reap_count(), 1)
        row = self.row(crashing)
        self.assertEqual((row["state"], row["last_error"], row["lease_id"]), ("failed", "lease_expired", None))
        self.assertIsNotNone(row["finished_at"])
        self.assertFalse(queue.complete(self.db, stale))
        self.assertFalse(queue.heartbeat(self.db, stale, lease=LEASE))
        nxt = self.claim()
        assert nxt is not None
        self.assertEqual(nxt.id, waiting)
        self.assertEqual(self.reap_count(), 0)

    def test_the_reaper_leaves_live_and_retryable_runs_alone(self) -> None:
        live = self.enqueue_instance(kind="test.live", max_attempts=1)
        retryable = self.enqueue_instance(kind="test.retry", max_attempts=2)
        first, second = self.claim(), self.claim()
        assert first is not None and second is not None and retryable is not None and live is not None
        self.expire(retryable)
        self.assertEqual(self.reap_count(), 0)
        self.assertEqual((self.state(live), self.state(retryable)), ("running", "running"))

    def reap_count(self) -> int:
        return queue.reap(self.db)


class RetryTests(JobsTestCase):
    def test_a_handled_failure_returns_to_queued_with_backoff(self) -> None:
        job_id = self.enqueue_instance(dedupe_key="d")
        job = self.claim()
        assert job is not None and job_id is not None
        backoff = queue.Backoff(base=timedelta(seconds=40), cap=timedelta(hours=1))
        self.assertIs(queue.fail(self.db, job, "ValueError", backoff=backoff), JobState.QUEUED)
        rows = self.run_sql(
            "SELECT state, attempts, last_error, lease_id, locked_by, finished_at, "
            "run_after BETWEEN now() + interval '30 seconds' AND now() + interval '50 seconds' AS backed_off "
            "FROM prks.jobs WHERE id = %s", (job_id,),
        )
        self.assertEqual(
            rows[0],
            {"state": "queued", "attempts": 1, "last_error": "ValueError", "lease_id": None,
             "locked_by": None, "finished_at": None, "backed_off": True},
        )
        self.assertIsNone(self.claim())  # not before run_after
        self.make_ready(job_id)
        again = self.claim()
        assert again is not None
        self.assertEqual((again.id, again.attempts), (job_id, 2))
        # The second retry waits twice as long, never more than the cap.
        self.assertIs(queue.fail(self.db, again, "ValueError", backoff=backoff), JobState.QUEUED)
        rows = self.run_sql(
            "SELECT run_after BETWEEN now() + interval '70 seconds' AND now() + interval '90 seconds' AS ok "
            "FROM prks.jobs WHERE id = %s", (job_id,),
        )
        self.assertTrue(rows[0]["ok"])

    def test_a_failure_while_a_twin_is_queued_cancels_and_the_twin_runs_once(self) -> None:
        a = self.enqueue_library(payload={"scope_id": "a1", "value": 4}, dedupe_key="w:a1")
        job_a = self.claim()
        assert job_a is not None and a is not None
        # Enqueued while A runs: A may have read state that changed since.
        b = self.enqueue_library(payload={"scope_id": "a1", "value": 5}, dedupe_key="w:a1")
        self.assertIsNotNone(b)
        self.assertIs(queue.fail(self.db, job_a, "ValueError"), JobState.CANCELLED)
        row = self.row(a)
        self.assertEqual((row["state"], row["lease_id"], row["last_error"]), ("cancelled", None, "ValueError"))
        self.assertIsNotNone(row["finished_at"])
        runs: list[uuid.UUID] = []
        while (job := self.claim()) is not None:
            runs.append(job.id)
            self.assertIs(run_job(self.db, job, registry=self.registry, lease=LEASE), Outcome.SUCCEEDED)
        self.assertEqual(runs, [b])
        self.assertEqual(self.revisions()[(self.lib_a, "a1")], 5)

    def test_a_twin_committed_during_the_failure_update_cancels_without_surfacing_23505(self) -> None:
        a = self.enqueue_instance(dedupe_key="d")
        job_a = self.claim()
        assert job_a is not None and a is not None
        twin = self.open_tx()
        twin_id = twin.execute(
            "INSERT INTO prks.jobs (kind, dedupe_key) VALUES ('test.instance', 'd') RETURNING id"
        ).fetchone()
        assert twin_id is not None
        # A's failure update does not see the uncommitted twin, tries to requeue A,
        # and waits on the twin's dedupe index entry.
        thread, result, error = self.in_thread(lambda: queue.fail(self.db, job_a, "ValueError"))
        self.wait_until(lambda: self.lock_waiters() >= 1, "the failure update to wait on the twin")
        twin.commit()
        thread.join(30)
        self.assertEqual(error, [])
        self.assertEqual(result, [JobState.CANCELLED])
        self.assertEqual(self.state(a), "cancelled")
        self.assertEqual(self.state(twin_id[0]), "queued")

    def test_a_failure_on_the_last_attempt_ends_failed_and_frees_the_dedupe_key(self) -> None:
        a = self.enqueue_instance(dedupe_key="d", max_attempts=2)
        first = self.claim()
        assert first is not None and a is not None
        self.assertIs(queue.fail(self.db, first, "ValueError", backoff=NO_BACKOFF), JobState.QUEUED)
        last = self.claim()
        assert last is not None
        self.assertEqual(last.attempts, 2)
        # A twin is queued as well: exhaustion wins over cancellation.
        twin = self.enqueue_instance(dedupe_key="d")
        self.assertIsNotNone(twin)
        self.assertIs(queue.fail(self.db, last, "ValueError"), JobState.FAILED)
        self.assertEqual(self.state(a), "failed")
        self.assertEqual(self.state(twin), "queued")  # type: ignore[arg-type]
        # Without a twin, the failed job holds no dedupe key either.
        b = self.enqueue_instance(kind="test.solo", dedupe_key="e", max_attempts=1)
        solo = self.claim()
        solo = solo if solo is not None and solo.id == b else self.claim()
        assert solo is not None and solo.id == b
        self.assertIs(queue.fail(self.db, solo, "ValueError"), JobState.FAILED)
        self.assertIsNotNone(self.enqueue_instance(kind="test.solo", dedupe_key="e"))

    def test_release_gives_the_attempt_back_or_yields_to_a_twin(self) -> None:
        a = self.enqueue_instance(dedupe_key="d")
        job = self.claim()
        assert job is not None and a is not None
        self.assertIs(queue.release(self.db, job), JobState.QUEUED)
        row = self.row(a)
        self.assertEqual((row["state"], row["attempts"], row["lease_id"]), ("queued", 0, None))
        again = self.claim()
        assert again is not None
        self.assertEqual(again.attempts, 1)
        twin = self.enqueue_instance(dedupe_key="d")
        self.assertIs(queue.release(self.db, again), JobState.CANCELLED)
        self.assertEqual((self.state(a), self.state(twin)), ("cancelled", "queued"))  # type: ignore[arg-type]

    def test_last_error_is_a_code_never_a_message(self) -> None:
        self.enqueue_instance()
        job = self.claim()
        assert job is not None
        for bad in ("No such file: /home/me/Secret Title.pdf", "", "x" * 65):
            with self.subTest(error=bad), self.assertRaises(ValueError):
                queue.fail(self.db, job, bad)
        self.assertEqual(queue.error_code(ValueError("Secret Title")), "ValueError")
        self.assertEqual(self.state(job.id), "running")


class SingletonTests(JobsTestCase):
    def test_a_lost_singleton_race_retries_without_surfacing_23505(self) -> None:
        first = self.enqueue_instance(kind="test.one", singleton_key="k", priority=3)
        second = self.enqueue_instance(kind="test.two", singleton_key="k", priority=2)
        other = self.enqueue_instance(kind="test.other", singleton_key="other", priority=1)
        # Worker A's claim of the first row, not yet committed.
        a = self.open_tx()
        a.execute(queue._CLAIM_SQL, {"worker": "A", "lease": 30.0})
        # Worker B sees no committed running row for "k", skips the locked first
        # row, claims the second and waits on A's jobs_singleton entry.
        thread, result, error = self.in_thread(lambda: queue.claim(self.db, "B", lease=LEASE))
        self.wait_until(lambda: self.lock_waiters() >= 1, "worker B to wait on the singleton index")
        a.commit()
        thread.join(30)
        self.assertEqual(error, [])
        claimed = result[0]
        assert isinstance(claimed, queue.ClaimedJob)
        self.assertEqual(claimed.id, other)
        self.assertEqual(self.state(second), "queued")  # type: ignore[arg-type]
        self.assertEqual(sorted(str(r["singleton_key"]) for r in self.running()), ["k", "other"])
        self.assertEqual(self.row(first)["locked_by"], "A")  # type: ignore[arg-type]

    def test_a_job_queued_during_a_running_singleton_waits_then_runs(self) -> None:
        self.enqueue_instance(kind="test.one", singleton_key="k")
        running = self.claim()
        assert running is not None
        later = self.enqueue_instance(kind="test.two", singleton_key="k")
        self.assertIsNone(self.claim())
        self.expire(running.id)  # an expired run is reclaimed, the waiting job still waits
        reclaimed = self.claim()
        assert reclaimed is not None
        self.assertEqual(reclaimed.id, running.id)
        self.assertIsNone(self.claim())
        self.assertTrue(queue.complete(self.db, reclaimed))
        nxt = self.claim()
        assert nxt is not None
        self.assertEqual(nxt.id, later)

    def test_two_concurrent_workers_never_run_two_of_one_key(self) -> None:
        for i in range(12):
            self.enqueue_instance(kind=f"test.k{i}", singleton_key="k")
        for i in range(4):
            self.enqueue_instance(kind=f"test.free{i}")
        db_b = self.open_db()
        stop = threading.Event()
        errors: list[BaseException] = []
        overlaps: list[int] = []
        done: list[uuid.UUID] = []
        completed: list[bool] = []
        lock = threading.Lock()

        def work(db: object, name: str) -> None:
            try:
                while not stop.is_set():
                    job = queue.claim(db, name, lease=LEASE)  # type: ignore[arg-type]
                    if job is None:
                        return
                    rows = self.run_sql(
                        "SELECT count(*) AS n FROM prks.jobs WHERE state = 'running' AND singleton_key = 'k'"
                    )
                    overlaps.append(int(rows[0]["n"]))
                    # Recorded, not asserted here: the thread's errors are checked below.
                    with lock:
                        completed.append(queue.complete(db, job))  # type: ignore[arg-type]
                        done.append(job.id)
            except BaseException as exc:  # noqa: BLE001 - asserted below
                errors.append(exc)
                stop.set()

        threads = [
            threading.Thread(target=work, args=(self.db, "A")),
            threading.Thread(target=work, args=(db_b, "B")),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        self.assertEqual(errors, [])
        self.assertTrue(all(completed))
        self.assertLessEqual(max(overlaps), 1)
        # A worker that finds only a held key stops; whatever is left still runs, one at a time.
        while (job := self.claim("C")) is not None:
            self.assertTrue(queue.complete(self.db, job))
            done.append(job.id)
        self.assertEqual(len(set(done)), 16)
