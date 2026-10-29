"""Lease-fenced PostgreSQL object maintenance primitives (L-A-1 #635).

These tests run against the real jobs table and immutable storage in the PG
harness; scheduling/worker bootstrap is completed in the next PR.
"""

from __future__ import annotations

from datetime import timedelta

from backend.domain.objects import jobs, lifecycle
from backend.domain.objects.lifecycle import Settled
from backend.jobs import queue
from backend.jobs.handlers import HandlerRegistry
from backend.jobs.runner import JobRun
from backend.storage.objects import NAMESPACE_PORTRAITS, StorageKey
from tests.pg.test_objects_lifecycle import ObjectsTestCase


class ObjectJobTests(ObjectsTestCase):
    def claimed(self, kind: str) -> JobRun:
        job_id = self.db.command(lambda tx: queue.enqueue(tx, kind))
        assert job_id is not None
        job = queue.claim(self.db, "objects-test")
        assert job is not None and job.id == job_id
        return JobRun(self.db, job, lease=queue.DEFAULT_LEASE)

    def test_a_claim_is_settled_under_the_job_lease(self) -> None:
        orphan = self.put()
        self.claim(orphan.key, self.actor.library_id)
        run = self.claimed(jobs.CLEANUP_KIND)
        self.assertEqual(jobs.cleanup_pass(run, self.objects), {Settled.REMOVED: 1})
        self.assertFalse(self.stored(orphan.key.name))
        self.assertEqual(self.claims(), {})
        self.assertTrue(queue.complete(self.db, run.job))

    def test_a_lost_lease_cannot_delete_an_object(self) -> None:
        orphan = self.put()
        self.claim(orphan.key, self.actor.library_id)
        run = self.claimed(jobs.CLEANUP_KIND)
        self.sql("UPDATE prks.jobs SET state = 'cancelled', lease_id = NULL WHERE id = %s", (run.job.id,))
        with self.assertRaises(queue.LeaseLost):
            jobs.cleanup_pass(run, self.objects)
        self.assertTrue(self.stored(orphan.key.name))
        self.assertIn(orphan.key.name, self.claims())

    def test_a_held_claim_and_a_live_reference_are_never_deleted(self) -> None:
        held, live = self.put(), self.put()
        self.claim(held.key, self.actor.library_id, hold=True)
        self.claim(live.key, self.actor.library_id)
        self.work_with(live)
        run = self.claimed(jobs.CLEANUP_KIND)
        self.assertEqual(jobs.cleanup_pass(run, self.objects), {Settled.REFERENCED: 1})
        self.assertTrue(self.stored(held.key.name))
        self.assertTrue(self.stored(live.key.name))
        self.assertIn(held.key.name, self.claims())

    def test_sweep_records_orphans_but_never_deletes_bytes(self) -> None:
        orphan, live = self.put(), self.put()
        self.work_with(live)
        run = self.claimed(jobs.SWEEP_KIND)
        self.assertEqual(jobs.sweep_pass(run, self.objects, grace=timedelta(0)), 1)
        self.assertEqual(set(self.claims()), {orphan.key.name})
        self.assertTrue(self.stored(orphan.key.name))
        self.assertTrue(self.stored(live.key.name))
        self.assertEqual(jobs.sweep_pass(run, self.objects, grace=timedelta(0)), 0)

    def test_sweep_respects_running_restore_staging(self) -> None:
        staged = StorageKey(NAMESPACE_PORTRAITS, "staged.webp")
        self.objects.put_new(staged, lambda stream: stream.write(b"image"))
        self.restore_run(staged)
        run = self.claimed(jobs.SWEEP_KIND)
        self.assertEqual(jobs.sweep_pass(run, self.objects, grace=timedelta(0)), 0)
        self.assertNotIn(staged.name, self.claims())

    def test_registers_both_maintenance_kinds_for_a_validated_storage_backend(self) -> None:
        registry = HandlerRegistry()
        jobs.register_handlers(registry, self.objects)
        self.assertEqual(registry.kinds(), {jobs.CLEANUP_KIND, jobs.SWEEP_KIND})

    def test_enqueue_helpers_are_deduplicated(self) -> None:
        first = self.db.command(lambda tx: jobs.enqueue_cleanup(tx))
        second = self.db.command(lambda tx: jobs.enqueue_cleanup(tx))
        self.assertIsNotNone(first)
        self.assertIsNone(second)
        one = self.db.command(lambda tx: jobs.enqueue_sweep(tx))
        two = self.db.command(lambda tx: jobs.enqueue_sweep(tx))
        self.assertIsNotNone(one)
        self.assertIsNone(two)
