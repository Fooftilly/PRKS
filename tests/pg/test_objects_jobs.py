"""Object maintenance jobs on PostgreSQL (L-A-1 #635; ``backend/domain/objects/jobs.py``).

The ``objects.cleanup`` and ``objects.sweep`` handlers under the job lease,
their bounded passes and continuations, the scheduling (the enqueue in the
releasing command and the worker's idle task), their registration on a
worker bound to its data root, and the safety rules they keep: held,
referenced and staged keys are never deleted, a backup's objects lock defers
a claim, a failed delete keeps a retryable claim, and a run that lost its
lease deletes nothing. Interleavings are fixed with locks held on raw
connections or with hooks in the storage backend, never with sleeps.
"""

from __future__ import annotations

import os
import tempfile
import threading
import time
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import psycopg

from backend.domain.identity.library_scope import LibraryScope
from backend.domain.objects import jobs, lifecycle
from backend.domain.objects.lifecycle import Settled
from backend.jobs import queue
from backend.jobs.handlers import REGISTRY, HandlerRegistry
from backend.jobs.runner import JobRun, Outcome, run_job
from backend.jobs.worker import Worker, WorkerSettings
from backend.pg_facade import context as facade_context
from backend.storage.objects import NAMESPACE_ASSET_OBJECTS, NAMESPACE_PORTRAITS, LocalFilesystemStorage, StorageKey
from tests.pg.test_objects_lifecycle import DAY, ObjectsTestCase
from tests.pg.test_objects_portraits import URL, image

LEASE = timedelta(seconds=30)
WAIT = 30.0
NO_GRACE = timedelta(0)


class ObjectJobsTestCase(ObjectsTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.maintenance = jobs.ObjectMaintenance(self.objects, self.storage.root)
        self.registry = HandlerRegistry()
        self.registry.register(jobs.CLEANUP_KIND, self.maintenance.cleanup)
        self.registry.register(jobs.SWEEP_KIND, lambda run: jobs.sweep_pass(run, self.objects, grace=NO_GRACE))

    # -- the jobs table ---------------------------------------------------------

    def jobs_of(self, kind: str, *states: str) -> list[dict[str, Any]]:
        rows = self.sql("SELECT id, state, payload, run_after FROM prks.jobs WHERE kind = %s "
                        "ORDER BY created_at, id", (kind,))
        return [row for row in rows if not states or row["state"] in states]

    def drop_other_jobs(self) -> None:
        """Jobs of other lanes (a Work delete's reindex) are not this test's."""
        self.sql("DELETE FROM prks.jobs WHERE kind NOT LIKE 'objects.%%'")

    def claimed(self, kind: str, worker: str = "objects-test") -> JobRun:
        self.drop_other_jobs()
        job = queue.claim(self.db, worker, lease=LEASE)
        assert job is not None and job.kind == kind, job
        return JobRun(self.db, job, lease=LEASE)

    def enqueue(self, fn: Callable[[Any], Any]) -> Any:
        return self.db.command(fn)

    def run_until_done(self, kind: str, handler: Callable[[JobRun], Any]) -> list[Any]:
        """Claim and run every queued job of ``kind`` (continuations included); each pass's result."""
        results: list[Any] = []
        while self.jobs_of(kind, "queued"):
            run = self.claimed(kind)
            results.append(handler(run))
            self.assertTrue(queue.complete(self.db, run.job))
            self.assertLess(len(results), 100, "a pass that never ends")
        return results

    def orphans(self, count: int, namespace: str = NAMESPACE_ASSET_OBJECTS) -> list[StorageKey]:
        made = []
        for index in range(count):
            suffix = ".pdf" if namespace == NAMESPACE_ASSET_OBJECTS else ".webp"
            stored = StorageKey(namespace, f"{index:04d}-{os.urandom(4).hex()}{suffix}")
            self.objects.put_new(stored, lambda stream: stream.write(b"bytes"))
            made.append(stored)
        return made

    def exists(self, stored: StorageKey) -> bool:
        return self.objects.stat(stored) is not None

    def age(self, *keys: StorageKey, days: float = 2) -> None:
        """Make objects older than the sweep's grace period."""
        for stored in keys:
            with self.objects.local_path(stored) as path:
                past = time.time() - days * DAY
                os.utime(path, (past, past))

    def stamp(self, seconds_ago: float) -> None:
        self.sql("UPDATE prks.object_cleanup_claims SET last_attempt_at = clock_timestamp() "
                 "- make_interval(secs => %s)", (seconds_ago,))

    def raw(self) -> psycopg.Connection[Any]:
        name = self.sql("SELECT current_database() AS name")[0]["name"]
        conn = psycopg.connect(self.harness.conninfo(name), autocommit=True)
        self.addCleanup(conn.close)
        return conn

    # -- a worker ---------------------------------------------------------------

    def start_worker(self, registry: HandlerRegistry, **settings: Any) -> Worker:
        settings.setdefault("lease", LEASE)
        settings.setdefault("poll", timedelta(milliseconds=50))
        worker = Worker(self.db, name="objects-worker", registry=registry, settings=WorkerSettings(**settings))
        errors: list[BaseException] = []

        def body() -> None:
            try:
                worker.run()
            except BaseException as exc:  # noqa: BLE001 - asserted below
                errors.append(exc)

        thread = threading.Thread(target=body)
        thread.start()

        def stop() -> None:
            worker.stop()
            thread.join(WAIT)
            self.assertEqual(errors, [])

        self.addCleanup(stop)
        self.assertTrue(worker.started.wait(WAIT))
        return worker


class CleanupPassTests(ObjectJobsTestCase):
    def test_a_claim_is_settled_under_the_job_lease(self) -> None:
        orphan = self.put()
        self.claim(orphan.key, self.actor.library_id)
        self.enqueue(jobs.enqueue_cleanup)
        run = self.claimed(jobs.CLEANUP_KIND)
        self.assertEqual(jobs.cleanup_pass(run, self.objects), {Settled.REMOVED: 1})
        self.assertFalse(self.stored(orphan.key.name))
        self.assertEqual(self.claims(), {})
        self.assertTrue(queue.complete(self.db, run.job))

    def test_the_registered_handlers_return_nothing_and_succeed(self) -> None:
        orphan = self.put()
        self.claim(orphan.key)
        job_id = self.enqueue(jobs.enqueue_cleanup)
        self.drop_other_jobs()
        job = queue.claim(self.db, "objects-test", lease=LEASE)
        assert job is not None
        self.assertIs(run_job(self.db, job, registry=self.registry, lease=LEASE), Outcome.SUCCEEDED)
        self.assertEqual(self.jobs_of(jobs.CLEANUP_KIND)[0]["id"], job_id)
        self.assertEqual(self.jobs_of(jobs.CLEANUP_KIND)[0]["state"], "succeeded")
        self.assertFalse(self.stored(orphan.key.name))

    def test_held_referenced_and_staged_keys_are_never_deleted(self) -> None:
        held, live, staged = self.put(), self.put(), self.put()
        self.claim(held.key, self.actor.library_id, hold=True)
        self.claim(live.key, self.actor.library_id)
        self.work_with(live)
        self.claim(staged.key)
        self.restore_run(staged.key)
        self.enqueue(jobs.enqueue_cleanup)
        run = self.claimed(jobs.CLEANUP_KIND)
        self.assertEqual(jobs.cleanup_pass(run, self.objects), {Settled.REFERENCED: 2})
        for obj in (held, live, staged):
            self.assertTrue(self.stored(obj.key.name))
        self.assertEqual(set(self.claims()), {held.key.name})

    def test_a_key_another_library_names_is_kept(self) -> None:
        theirs = self.put()
        with facade_context.acting_as(self.other_actor().context):
            self.work_with(theirs)
        self.claim(theirs.key, self.actor.library_id)  # a stale claim of this Library
        self.enqueue(jobs.enqueue_cleanup)
        run = self.claimed(jobs.CLEANUP_KIND)
        self.assertEqual(jobs.cleanup_pass(run, self.objects), {Settled.REFERENCED: 1})
        self.assertTrue(self.stored(theirs.key.name))

    def test_a_backup_defers_its_librarys_claims_and_they_are_retried_later(self) -> None:
        obj, orphan = self.put(), self.put()
        self.claim(obj.key, self.actor.library_id)
        self.claim(orphan.key)
        backup = self.raw()
        backup.execute("SELECT pg_advisory_lock_shared(" + lifecycle.OBJECTS_LOCK_KEY_SQL + ")",
                       (self.actor.library_id,))
        self.enqueue(jobs.enqueue_cleanup)
        self.assertEqual(self.run_until_done(jobs.CLEANUP_KIND, lambda run: jobs.cleanup_pass(run, self.objects)),
                         [{Settled.BUSY: 1, Settled.REMOVED: 1}])
        self.assertTrue(self.stored(obj.key.name))
        self.assertEqual(self.enqueue(jobs.schedule), [jobs.SWEEP_KIND], "not due again yet")
        backup.execute("SELECT pg_advisory_unlock_all()")
        self.stamp(jobs.RETRY_AFTER.total_seconds())
        self.assertEqual(self.enqueue(jobs.schedule), [jobs.CLEANUP_KIND])
        self.assertEqual(self.run_until_done(jobs.CLEANUP_KIND, lambda run: jobs.cleanup_pass(run, self.objects)),
                         [{Settled.REMOVED: 1}])
        self.assertFalse(self.stored(obj.key.name))

    def test_a_failed_delete_keeps_a_claim_that_a_later_pass_settles(self) -> None:
        orphans = [self.put() for _ in range(3)]
        for obj in orphans:
            self.claim(obj.key)
        self.objects.fail_delete = True
        self.enqueue(jobs.enqueue_cleanup)
        with self.assertLogs("prks.objects", "WARNING"):
            results = self.run_until_done(jobs.CLEANUP_KIND,
                                          lambda run: jobs.cleanup_pass(run, self.objects, limit=3))
        # A full batch whose claims all failed enqueues no continuation: none is due.
        self.assertEqual(results, [{Settled.FAILED: 3}])
        self.assertEqual(len(self.claims()), 3)
        self.assertTrue(all(row["last_attempt_at"] is not None for row in self.claims().values()))
        self.assertNotIn(jobs.CLEANUP_KIND, self.enqueue(jobs.schedule))
        self.objects.fail_delete = False
        self.stamp(jobs.RETRY_AFTER.total_seconds())
        self.assertIn(jobs.CLEANUP_KIND, self.enqueue(jobs.schedule))
        self.assertEqual(self.run_until_done(jobs.CLEANUP_KIND, lambda run: jobs.cleanup_pass(run, self.objects)),
                         [{Settled.REMOVED: 3}])
        self.assertEqual(self.claims(), {})

    def test_an_invalid_claim_is_stamped_not_retried_at_once(self) -> None:
        self.sql("INSERT INTO prks.object_cleanup_claims (namespace, name) VALUES ('asset-objects', %s)",
                 ("\x1cstray.pdf",))
        self.enqueue(jobs.enqueue_cleanup)
        with self.assertLogs("prks.objects", "WARNING"):
            self.assertEqual(self.run_until_done(jobs.CLEANUP_KIND,
                                                 lambda run: jobs.cleanup_pass(run, self.objects, limit=1)),
                             [{Settled.FAILED: 1}])
        self.assertIsNotNone(self.claims()["\x1cstray.pdf"]["last_attempt_at"])
        self.assertNotIn(jobs.CLEANUP_KIND, self.enqueue(jobs.schedule))

    def test_many_claims_are_settled_in_bounded_passes(self) -> None:
        orphans = self.orphans(25)
        for stored in orphans:
            self.claim(stored)
        self.enqueue(jobs.enqueue_cleanup)
        results = self.run_until_done(jobs.CLEANUP_KIND,
                                      lambda run: jobs.cleanup_pass(run, self.objects, limit=10))
        self.assertEqual(results, [{Settled.REMOVED: 10}, {Settled.REMOVED: 10}, {Settled.REMOVED: 5}])
        self.assertEqual(self.claims(), {})
        self.assertFalse(any(self.exists(stored) for stored in orphans))
        self.assertEqual({row["state"] for row in self.jobs_of(jobs.CLEANUP_KIND)}, {"succeeded"})

    def test_a_full_batch_with_nothing_left_enqueues_nothing(self) -> None:
        for stored in self.orphans(2):
            self.claim(stored)
        self.enqueue(jobs.enqueue_cleanup)
        results = self.run_until_done(jobs.CLEANUP_KIND,
                                      lambda run: jobs.cleanup_pass(run, self.objects, limit=2))
        self.assertEqual(results, [{Settled.REMOVED: 2}])
        self.assertEqual(len(self.jobs_of(jobs.CLEANUP_KIND)), 1)


class FencingTests(ObjectJobsTestCase):
    def test_a_cancelled_run_deletes_nothing(self) -> None:
        orphan = self.put()
        self.claim(orphan.key, self.actor.library_id)
        self.enqueue(jobs.enqueue_cleanup)
        run = self.claimed(jobs.CLEANUP_KIND)
        self.sql("UPDATE prks.jobs SET state = 'cancelled', lease_id = NULL WHERE id = %s", (run.job.id,))
        with self.assertRaises(queue.LeaseLost):
            jobs.cleanup_pass(run, self.objects)
        self.assertTrue(self.stored(orphan.key.name))
        self.assertIn(orphan.key.name, self.claims())

    def test_an_expired_and_reclaimed_run_deletes_nothing_and_the_new_run_does(self) -> None:
        first, second = self.put(), self.put()
        self.claim(first.key)
        self.claim(second.key)
        self.enqueue(jobs.enqueue_cleanup)
        stale = self.claimed(jobs.CLEANUP_KIND, "worker-a")
        # Worker A stalls; its lease runs out and worker B reclaims the job.
        self.sql("UPDATE prks.jobs SET locked_until = now() - interval '1 second' WHERE id = %s", (stale.job.id,))
        fresh = queue.claim(self.db, "worker-b", lease=LEASE)
        assert fresh is not None and fresh.id == stale.job.id and fresh.lease_id != stale.job.lease_id
        with self.assertRaises(queue.LeaseLost):
            jobs.cleanup_pass(stale, self.objects)
        self.assertTrue(self.stored(first.key.name) and self.stored(second.key.name))
        self.assertEqual(jobs.cleanup_pass(JobRun(self.db, fresh, lease=LEASE), self.objects),
                         {Settled.REMOVED: 2})
        self.assertFalse(queue.complete(self.db, stale.job), "the stale run records nothing")
        self.assertTrue(queue.complete(self.db, fresh))

    def test_a_run_stopped_between_claims_settles_no_more(self) -> None:
        # A worker stop marks its run lost (worker.py); the pass ends at the next key.
        for stored in self.orphans(3):
            self.claim(stored)
        self.enqueue(jobs.enqueue_cleanup)
        run = self.claimed(jobs.CLEANUP_KIND)
        self.objects.before_delete = run.mark_lost
        with self.assertRaises(queue.LeaseLost):
            jobs.cleanup_pass(run, self.objects)
        self.assertEqual(len(self.claims()), 2, "the settle in progress commits; the rest wait")
        self.assertIs(queue.release(self.db, run.job), queue.JobState.QUEUED)
        self.objects.before_delete = None
        self.assertEqual(self.run_until_done(jobs.CLEANUP_KIND, lambda r: jobs.cleanup_pass(r, self.objects)),
                         [{Settled.REMOVED: 2}])

    def test_a_sweep_that_lost_its_lease_records_no_claim(self) -> None:
        orphan = self.orphans(1)[0]
        self.age(orphan)
        self.enqueue(jobs.enqueue_sweep)
        run = self.claimed(jobs.SWEEP_KIND)
        self.sql("UPDATE prks.jobs SET locked_until = now() - interval '1 second' WHERE id = %s", (run.job.id,))
        with self.assertRaises(queue.LeaseLost):
            jobs.sweep_pass(run, self.objects)
        self.assertEqual(self.claims(), {})


class SweepPassTests(ObjectJobsTestCase):
    def test_the_sweep_records_old_orphans_and_never_deletes_bytes(self) -> None:
        orphan, live = self.put(), self.put()
        self.work_with(live)
        self.age(orphan.key, live.key)
        young = self.orphans(1)[0]  # an upload still in progress
        self.enqueue(jobs.enqueue_sweep)
        run = self.claimed(jobs.SWEEP_KIND)
        self.assertEqual(jobs.sweep_pass(run, self.objects), 1)
        self.assertEqual(set(self.claims()), {orphan.key.name})
        self.assertTrue(self.stored(orphan.key.name) and self.stored(live.key.name) and self.exists(young))
        self.assertEqual(jobs.sweep_pass(run, self.objects), 0, "a repeated pass records nothing new")

    def test_a_running_restores_staged_key_is_not_claimed(self) -> None:
        staged = StorageKey(NAMESPACE_PORTRAITS, "staged.webp")
        self.objects.put_new(staged, lambda stream: stream.write(b"image"))
        self.restore_run(staged)
        self.enqueue(jobs.enqueue_sweep)
        run = self.claimed(jobs.SWEEP_KIND)
        self.assertEqual(jobs.sweep_pass(run, self.objects, grace=NO_GRACE), 0)
        self.assertNotIn(staged.name, self.claims())

    def test_a_large_root_is_swept_in_bounded_passes_that_resume_after_their_cursor(self) -> None:
        pdfs, pictures = self.orphans(5), self.orphans(2, NAMESPACE_PORTRAITS)
        examined: list[list[str]] = []
        real_stat = self.objects.stat

        def counting_stat(stored: StorageKey) -> Any:
            examined[-1].append(stored.name)
            return real_stat(stored)

        self.objects.stat = counting_stat  # type: ignore[method-assign]

        def one_pass(run: JobRun) -> int:
            examined.append([])
            return jobs.sweep_pass(run, self.objects, grace=NO_GRACE, batch=3)

        self.enqueue(jobs.enqueue_sweep)
        self.assertEqual(self.run_until_done(jobs.SWEEP_KIND, one_pass), [3, 3, 1])
        self.assertEqual([len(names) for names in examined], [3, 3, 1])
        self.assertEqual(set(self.claims()), {stored.name for stored in pdfs + pictures})
        self.assertEqual(sum(examined, []), sorted(s.name for s in pdfs) + sorted(s.name for s in pictures),
                         "each key once, in order")
        payloads = [row["payload"] for row in self.jobs_of(jobs.SWEEP_KIND)]
        self.assertEqual(payloads[0], {})
        self.assertEqual(payloads[1], {"namespace": NAMESPACE_ASSET_OBJECTS, "after": sorted(s.name for s in pdfs)[2]})
        self.assertEqual(payloads[2]["namespace"], NAMESPACE_PORTRAITS)

    def test_a_malformed_cursor_fails_the_pass_before_any_claim(self) -> None:
        self.orphans(1)
        for payload in ({"namespace": "elsewhere", "after": "a.pdf"}, {"namespace": NAMESPACE_PORTRAITS},
                        {"namespace": NAMESPACE_PORTRAITS, "after": "../x"}):
            with self.subTest(payload=payload):
                self.enqueue(lambda tx, payload=payload: queue.enqueue(tx, jobs.SWEEP_KIND, payload))
                run = self.claimed(jobs.SWEEP_KIND)
                with self.assertRaises(ValueError):
                    jobs.sweep_pass(run, self.objects, grace=NO_GRACE)
                queue.complete(self.db, run.job)
        self.assertEqual(self.claims(), {})


class SchedulingTests(ObjectJobsTestCase):
    def test_a_release_enqueues_cleanup_in_its_command_once(self) -> None:
        first, second = self.put(), self.put()
        works = [self.work_with(first), self.work_with(second)]
        self.assertEqual(self.jobs_of(jobs.CLEANUP_KIND), [])
        for work_id in works:
            self.library.delete_work_record(work_id)
        self.assertEqual(len(self.jobs_of(jobs.CLEANUP_KIND, "queued")), 1, "deduplicated while queued")

    def test_a_rolled_back_release_enqueues_nothing(self) -> None:
        obj = self.put()
        self.work_with(obj)

        def release_then_fail(scope: LibraryScope) -> None:
            lifecycle.release(scope, obj.key)
            raise RuntimeError("rolled back")

        self.sql("UPDATE prks.assets SET storage_locator = 'elsewhere.pdf'")
        with self.assertRaises(RuntimeError):
            self.command(release_then_fail)
        self.assertEqual(self.jobs_of(jobs.CLEANUP_KIND), [])
        self.assertEqual(self.claims(), {})

    def test_a_hold_claim_enqueues_nothing(self) -> None:
        obj = self.put()
        self.command(lambda scope: lifecycle.release(scope, obj.key, hold=True))
        self.assertEqual(self.jobs_of(jobs.CLEANUP_KIND), [])
        self.assertNotIn(jobs.CLEANUP_KIND, self.enqueue(jobs.schedule))

    def test_a_running_cleanup_does_not_swallow_a_later_release(self) -> None:
        early, late = self.put(), self.put()
        self.claim(early.key)
        self.enqueue(jobs.enqueue_cleanup)
        run = self.claimed(jobs.CLEANUP_KIND)

        def release_late() -> None:
            # The running pass has selected its claims; a command releases another key.
            self.objects.before_delete = None
            self.command(lambda scope: lifecycle.release(scope, late.key))

        self.objects.before_delete = release_late
        self.assertEqual(jobs.cleanup_pass(run, self.objects), {Settled.REMOVED: 1})
        self.assertEqual(len(self.jobs_of(jobs.CLEANUP_KIND, "queued")), 1, "queued behind the running one")
        self.assertTrue(queue.complete(self.db, run.job))
        self.assertTrue(self.stored(late.key.name))
        self.assertEqual(self.run_until_done(jobs.CLEANUP_KIND, lambda r: jobs.cleanup_pass(r, self.objects)),
                         [{Settled.REMOVED: 1}])
        self.assertFalse(self.stored(late.key.name))

    def test_claims_left_by_a_restart_are_scheduled_and_an_idle_instance_schedules_nothing(self) -> None:
        self.assertEqual(self.enqueue(jobs.schedule), [jobs.SWEEP_KIND], "the first sweep")
        self.assertEqual(self.enqueue(jobs.schedule), [], "nothing due: no claim, a sweep queued")
        self.run_until_done(jobs.SWEEP_KIND, lambda run: jobs.sweep_pass(run, self.objects))
        self.assertEqual(self.enqueue(jobs.schedule), [], "the sweep ran this week")
        orphan = self.put()
        self.claim(orphan.key)  # recorded before a crash; its job was lost
        self.assertEqual(self.enqueue(jobs.schedule), [jobs.CLEANUP_KIND])
        self.assertEqual(self.enqueue(jobs.schedule), [], "already queued")
        self.run_until_done(jobs.CLEANUP_KIND, lambda run: jobs.cleanup_pass(run, self.objects))
        self.assertEqual(self.enqueue(jobs.schedule), [])
        self.sql("UPDATE prks.jobs SET created_at = now() - interval '8 days' WHERE kind = %s", (jobs.SWEEP_KIND,))
        self.assertEqual(self.enqueue(jobs.schedule), [jobs.SWEEP_KIND], "a week later")

    def test_a_running_sweep_or_cleanup_suppresses_only_a_duplicate(self) -> None:
        self.claim(self.put().key)
        self.assertEqual(self.enqueue(jobs.schedule), [jobs.CLEANUP_KIND, jobs.SWEEP_KIND])
        run = self.claimed(jobs.CLEANUP_KIND)
        self.assertEqual(self.enqueue(jobs.schedule), [], "running and queued")
        self.sql("UPDATE prks.jobs SET state = 'failed', lease_id = NULL, finished_at = now() WHERE id = %s",
                 (run.job.id,))
        self.assertEqual(self.enqueue(jobs.schedule), [jobs.CLEANUP_KIND], "a failed run is not active")

    def test_concurrent_schedulers_enqueue_each_kind_once(self) -> None:
        self.claim(self.put().key)
        holder = self.raw()
        holder.execute("BEGIN")
        holder.execute(jobs._SCHEDULE_LOCK)
        results: list[list[str]] = []
        threads = [threading.Thread(target=lambda: results.append(self.enqueue(jobs.schedule))) for _ in range(3)]
        for thread in threads:
            thread.start()
        self.wait_until(lambda: self.lock_waiters() >= 3, "three schedulers waiting on the schedule lock")
        holder.execute("COMMIT")
        for thread in threads:
            thread.join(WAIT)
        self.assertEqual(sorted(sum(results, [])), [jobs.CLEANUP_KIND, jobs.SWEEP_KIND])
        self.assertEqual(len(self.jobs_of(jobs.CLEANUP_KIND)), 1)
        self.assertEqual(len(self.jobs_of(jobs.SWEEP_KIND)), 1)

    def test_the_idle_task_schedules_at_most_once_per_interval(self) -> None:
        self.maintenance.tick(self.db)
        self.assertEqual(len(self.jobs_of(jobs.SWEEP_KIND)), 1)
        self.sql("DELETE FROM prks.jobs")
        self.maintenance.tick(self.db)
        self.assertEqual(self.jobs_of(jobs.SWEEP_KIND), [], "within the interval")
        eager = jobs.ObjectMaintenance(self.objects, self.storage.root, schedule_every=timedelta(0))
        eager.tick(self.db)
        self.assertEqual(len(self.jobs_of(jobs.SWEEP_KIND)), 1)


class RootTests(ObjectJobsTestCase):
    def root(self, *files: str) -> str:
        root = tempfile.mkdtemp(prefix="prks-objects-root-")
        self.addCleanup(lambda: __import__("shutil").rmtree(root, True))
        for name in files:
            open(os.path.join(root, name), "wb").close()
        return root

    def test_attach_binds_both_kinds_and_the_idle_task_to_one_workers_registry(self) -> None:
        registry = REGISTRY.copy()
        maintenance = jobs.attach(registry, self.storage.root)
        assert maintenance is not None
        self.assertTrue({jobs.CLEANUP_KIND, jobs.SWEEP_KIND} <= registry.kinds())
        self.assertEqual(registry.idle_tasks(), (maintenance.tick,))
        self.assertFalse({jobs.CLEANUP_KIND, jobs.SWEEP_KIND} & REGISTRY.kinds(), "the process registry")
        self.assertEqual(os.path.realpath(self.storage.root), maintenance.storage._root_real)  # type: ignore[attr-defined]

    def test_a_root_holding_a_sqlite_catalogue_gets_no_object_maintenance(self) -> None:
        for name in jobs.SQLITE_CATALOGUES:
            with self.subTest(name=name):
                registry = HandlerRegistry()
                with self.assertLogs("prks.objects", "WARNING") as logs:
                    self.assertIsNone(jobs.attach(registry, self.root(name)))
                self.assertIn("object_maintenance_disabled reason=sqlite_catalogue", logs.output[0])
                self.assertEqual(registry.kinds(), frozenset())
                self.assertEqual(registry.idle_tasks(), ())

    def test_a_sqlite_catalogue_appearing_later_stops_every_pass(self) -> None:
        root = self.root()
        maintenance = jobs.ObjectMaintenance(LocalFilesystemStorage(root), root, schedule_every=timedelta(0))
        stored = StorageKey(NAMESPACE_ASSET_OBJECTS, "orphan.pdf")
        maintenance.storage.put_new(stored, lambda stream: stream.write(b"bytes"))
        self.claim(stored)
        open(os.path.join(root, "prks_data.db"), "wb").close()
        maintenance.tick(self.db)
        self.assertEqual(self.sql("SELECT kind FROM prks.jobs WHERE kind LIKE 'objects.%%'"), [])
        passes = ((maintenance.cleanup, jobs.enqueue_cleanup, jobs.CLEANUP_KIND),
                  (maintenance.sweep, jobs.enqueue_sweep, jobs.SWEEP_KIND))
        for handler, enqueue, kind in passes:
            self.enqueue(enqueue)
            run = self.claimed(kind)
            with self.assertRaises(jobs.ObjectsRootRefused):
                handler(run)
            queue.complete(self.db, run.job)
        self.assertIsNotNone(maintenance.storage.stat(stored))
        self.assertEqual(set(self.claims()), {"orphan.pdf"})


class WorkerTests(ObjectJobsTestCase):
    def attached(self) -> HandlerRegistry:
        registry = HandlerRegistry()
        self.assertIsNotNone(jobs.attach(registry, self.storage.root))
        return registry

    def test_a_deleted_works_file_is_removed_by_the_running_worker(self) -> None:
        self.start_worker(self.attached())
        obj = self.put()
        work_id = self.work_with(obj)
        self.library.delete_work_record(work_id)
        self.wait_until(lambda: not self.stored(obj.key.name), "the worker to settle the claim")
        self.wait_until(lambda: self.claims() == {}, "the claim to be gone")

    def test_a_replaced_portrait_is_removed_by_the_running_worker(self) -> None:
        self.start_worker(self.attached())
        person_id = self.library.add_person("Ada", "Lovelace", image_url=URL)
        first = self.library._cache_person_portrait(person_id, lambda _url: image((1, 2, 3)))
        old = self.sql("SELECT portrait_locator FROM prks.persons WHERE id = %s", (person_id,))[0]["portrait_locator"]
        second = self.library._cache_person_portrait(person_id, lambda _url: image((4, 5, 6)))
        self.assertNotEqual(first, second)
        portrait = StorageKey(NAMESPACE_PORTRAITS, old)
        self.wait_until(lambda: self.objects.stat(portrait) is None, "the old portrait to be removed")
        new = self.sql("SELECT portrait_locator FROM prks.persons WHERE id = %s", (person_id,))[0]["portrait_locator"]
        self.assertIsNotNone(self.objects.stat(StorageKey(NAMESPACE_PORTRAITS, new)))

    def test_claims_left_by_a_restart_are_settled_without_any_enqueue(self) -> None:
        orphan = self.put()
        self.claim(orphan.key)  # no job: the worker's idle task finds it
        self.start_worker(self.attached())
        self.wait_until(lambda: not self.stored(orphan.key.name), "the idle task's cleanup to run")
        self.wait_until(lambda: bool(self.jobs_of(jobs.SWEEP_KIND, "succeeded")), "the first sweep")

    def test_a_job_whose_notify_was_missed_is_run_on_the_poll(self) -> None:
        orphan = self.put()
        self.claim(orphan.key)
        worker = self.start_worker(self.attached())
        self.wait_until(lambda: bool(self.jobs_of(jobs.SWEEP_KIND, "succeeded")), "the first sweep")
        self.wait_until(lambda: not self.stored(orphan.key.name), "the first cleanup")
        self.assertTrue(worker.idle.wait(WAIT))
        late = self.put()
        self.claim(late.key)
        # Inserted directly: no NOTIFY reaches the worker.
        self.sql("INSERT INTO prks.jobs (kind, dedupe_key, singleton_key) VALUES (%s, %s, %s)",
                 (jobs.CLEANUP_KIND, jobs.CLEANUP_KIND, jobs.CLEANUP_KIND))
        self.wait_until(lambda: not self.stored(late.key.name), "the poll to claim the job")

    def test_two_workers_never_run_two_cleanups_at_once(self) -> None:
        for stored in self.orphans(20):
            self.claim(stored)
        overlap: list[int] = []
        active = [0]
        guard = threading.Lock()
        real_delete = self.objects.delete

        def delete(stored: StorageKey) -> bool:
            with guard:
                active[0] += 1
                overlap.append(active[0])
            try:
                return real_delete(stored)
            finally:
                with guard:
                    active[0] -= 1

        self.objects.delete = delete  # type: ignore[method-assign]
        for _ in range(2):
            maintenance = jobs.ObjectMaintenance(self.objects, self.storage.root)

            def cleanup(run: JobRun, storage: Any = maintenance.storage) -> None:
                jobs.cleanup_pass(run, storage, limit=3)

            registry = HandlerRegistry()
            registry.register(jobs.CLEANUP_KIND, cleanup)
            registry.register(jobs.SWEEP_KIND, maintenance.sweep)
            registry.add_idle_task(maintenance.tick)
            self.start_worker(registry)
        self.wait_until(lambda: self.claims() == {}, "every claim to be settled")
        self.assertEqual(max(overlap), 1)
        self.wait_until(lambda: not self.jobs_of(jobs.CLEANUP_KIND, "queued", "running"), "the passes to end")
        self.assertEqual({row["state"] for row in self.jobs_of(jobs.CLEANUP_KIND)}, {"succeeded"})

