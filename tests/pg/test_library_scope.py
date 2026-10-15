"""Library scope and epoch fencing (§5.2, §9.4, §11.4; L-ID-1 #626, PR 4/5)."""

from __future__ import annotations

from typing import Any

import psycopg

from backend.db.config import DatabaseConfig
from backend.db.database import Database, Isolation
from backend.domain.identity.authorization import Capability, Refusal
from backend.domain.identity.library_scope import (
    LIBRARY_LOCK_KEY_SQL,
    LibraryScope,
    library_command,
    library_query,
)
from backend.domain.identity.request_context import RequestContext
from tests.pg.scope_fixtures import Paused, ScopeTestCase, bump, read_all

EDIT = Capability.EDIT_LIBRARY


class EpochTests(ScopeTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.editor = self.member("Editor", "editor")

    def edit(self, epoch: object, scope_id: str = "a1") -> Any:
        return library_command(self.db, self.ctx(self.editor, epoch=epoch), EDIT, bump(scope_id))

    def test_the_current_epoch_commits(self) -> None:
        self.assertEqual(self.edit(1), 2)
        self.assertEqual(self.edit("1"), 3)

    def test_missing_or_unusable_epochs_are_refused_before_any_work(self) -> None:
        ctx = self.ctx(self.editor, epoch=None)

        class NoDatabase(Database):
            def command(self, *_args: Any, **_kwargs: Any) -> Any:
                raise AssertionError("no database work for a mutation without an epoch")

        no_db = NoDatabase(self.open_db().config, open_pool=False)
        self.assert_refused(Refusal.LIBRARY_EPOCH_REQUIRED, lambda: library_command(no_db, ctx, EDIT, bump("a1")))
        before = self.revisions()
        for value in (None, "", "0", 0, -1, "abc", True, "1.5"):
            with self.subTest(epoch=value):
                self.assert_refused(Refusal.LIBRARY_EPOCH_REQUIRED, lambda value=value: self.edit(value))
        self.assertEqual(self.revisions(), before)

    def test_stale_and_future_epochs_are_refused_with_nothing_written(self) -> None:
        self.restore()  # epoch 1 -> 2
        before = self.revisions()
        for value in (1, 3, 2**40):
            with self.subTest(epoch=value):
                self.assert_refused(Refusal.LIBRARY_EPOCH_CHANGED, lambda value=value: self.edit(value))
        self.assertEqual(self.revisions(), before)
        self.assertEqual(self.edit(2), 2)

    def test_a_restore_before_the_command_starts_fences_it(self) -> None:
        ctx = self.ctx(self.editor, epoch=1)  # the tab loaded epoch 1
        self.restore()
        before = self.revisions()
        self.assert_refused(Refusal.LIBRARY_EPOCH_CHANGED, lambda: library_command(self.db, ctx, EDIT, bump("a1")))
        self.assertEqual(self.revisions(), before)

    def test_another_librarys_restore_does_not_fence_this_one(self) -> None:
        self.restore(self.lib_b)
        self.assertEqual(self.edit(1), 2)

    def test_every_refusal_happens_before_the_protected_write(self) -> None:
        reached: list[str] = []

        def spy(scope: LibraryScope) -> None:
            reached.append(scope.library_id)
            scope.execute(
                "UPDATE prks.entity_revisions SET revision = 99 WHERE library_id = %(library_id)s"
            )

        reader = self.ctx(self.member("Reader", "reader"))
        self.restore()
        before = self.revisions()
        for ctx, refusal in ((self.ctx(self.editor, epoch=1), Refusal.LIBRARY_EPOCH_CHANGED),
                             (self.ctx(self.editor, epoch=None), Refusal.LIBRARY_EPOCH_REQUIRED),
                             (reader, Refusal.FORBIDDEN)):
            self.assert_refused(refusal, lambda ctx=ctx: library_command(self.db, ctx, EDIT, spy))
        self.assertEqual((reached, self.revisions()), ([], before))

    def test_a_failure_inside_the_command_rolls_everything_back(self) -> None:
        def half(scope: LibraryScope) -> None:
            bump("a1")(scope)
            raise RuntimeError("after the first write")

        before = self.revisions()
        with self.assertRaises(RuntimeError):
            library_command(self.db, self.ctx(self.editor), EDIT, half)
        self.assertEqual(self.revisions(), before)

    def test_serializable_commands_are_fenced_too(self) -> None:
        ctx = self.ctx(self.editor, epoch=1)
        self.assertEqual(library_command(self.db, ctx, EDIT, bump("a1"), isolation=Isolation.SERIALIZABLE), 2)
        self.restore()
        self.assert_refused(
            Refusal.LIBRARY_EPOCH_CHANGED,
            lambda: library_command(self.db, ctx, EDIT, bump("a1"), isolation=Isolation.SERIALIZABLE),
        )


class MaintenanceTests(ScopeTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.ctx_a = self.ctx(self.member("Editor", "editor"))

    def test_a_committed_maintenance_flag_refuses_writes_but_not_reads(self) -> None:
        self.run_sql("UPDATE prks.libraries SET maintenance = 'restore' WHERE id = %s", (self.lib_a,))
        before = self.revisions()
        self.assert_refused(Refusal.MAINTENANCE, lambda: library_command(self.db, self.ctx_a, EDIT, bump("a1")))
        self.assertEqual(self.revisions(), before)
        self.assertEqual(len(library_query(self.db, self.ctx_a, read_all)), 2)

    def test_a_held_exclusive_maintenance_lock_refuses_at_once(self) -> None:
        with self.connect() as restore:
            restore.execute("SELECT pg_advisory_lock(" + LIBRARY_LOCK_KEY_SQL + ")", (self.lib_a,))
            before = self.revisions()
            self.assert_refused(Refusal.MAINTENANCE, lambda: library_command(self.db, self.ctx_a, EDIT, bump("a1")))
            self.assertEqual(self.revisions(), before)
            # Library B is not affected by A's maintenance.
            ctx_b = self.ctx(self.member("B Editor", "editor", self.lib_b), self.lib_b)
            self.assertEqual(library_command(self.db, ctx_b, EDIT, bump("b1")), 2)
            restore.execute("UPDATE prks.libraries SET epoch = epoch + 1 WHERE id = %s", (self.lib_a,))
            restore.execute("SELECT pg_advisory_unlock_all()")
        self.assert_refused(Refusal.LIBRARY_EPOCH_CHANGED,
                            lambda: library_command(self.db, self.ctx_a, EDIT, bump("a1")))

    def test_maintenance_is_only_revealed_to_authorized_actors(self) -> None:
        reader = self.ctx(self.member("Reader", "reader"))
        removed_id = self.member("Removed", "editor")
        removed = self.ctx(removed_id)
        self.run_sql("DELETE FROM prks.library_memberships WHERE user_id = %s", (removed_id,))
        with self.connect() as restore:
            restore.execute("SELECT pg_advisory_lock(" + LIBRARY_LOCK_KEY_SQL + ")", (self.lib_a,))
            self.assert_refused(Refusal.FORBIDDEN, lambda: library_command(self.db, reader, EDIT, bump("a1")))
            self.assert_refused(Refusal.NOT_AUTHENTICATED,
                                lambda: library_command(self.db, removed, EDIT, bump("a1")))
            self.assert_refused(Refusal.MAINTENANCE, lambda: library_command(self.db, self.ctx_a, EDIT, bump("a1")))

    def test_a_restore_waits_for_a_command_in_flight_then_fences_the_next(self) -> None:
        paused = Paused()
        writer = paused.start(lambda: library_command(self.db, self.ctx_a, EDIT, paused.hold(bump("a1"))))
        self.assertTrue(paused.inside.wait(30))
        with self.connect() as restore:
            drain = Paused()
            drainer = drain.start(lambda: (
                restore.execute("SELECT pg_advisory_lock(" + LIBRARY_LOCK_KEY_SQL + ")", (self.lib_a,)),
                restore.execute("UPDATE prks.libraries SET epoch = epoch + 1 WHERE id = %s", (self.lib_a,)),
                restore.execute("SELECT pg_advisory_unlock_all()"),
            ))
            self.wait_until(self.waiting_on_lock(restore), "the restore drain to wait for the command")
            self.assertEqual(self.epoch(), 1)  # the command still runs under epoch 1
            paused.release.set()
            writer.join(30)
            drainer.join(30)
        self.assertEqual((paused.error, drain.error), ([], []))
        self.assertEqual(paused.result, [2])
        self.assertEqual((self.epoch(), self.revisions()[(self.lib_a, "a1")]), (2, 2))
        self.assert_refused(Refusal.LIBRARY_EPOCH_CHANGED,
                            lambda: library_command(self.db, self.ctx_a, EDIT, bump("a1")))
        self.assertEqual(self.revisions()[(self.lib_a, "a1")], 2)

    def test_an_epoch_change_that_skips_the_lock_still_waits_for_the_command(self) -> None:
        # Defense in depth: the Library row is locked FOR SHARE by the epoch check.
        paused = Paused()
        writer = paused.start(lambda: library_command(self.db, self.ctx_a, EDIT, paused.hold(bump("a1"))))
        self.assertTrue(paused.inside.wait(30))
        with self.connect() as other:
            change = Paused()
            changer = change.start(lambda: other.execute(
                "UPDATE prks.libraries SET epoch = epoch + 1 WHERE id = %s", (self.lib_a,)
            ))
            self.wait_until(self.waiting_on_lock(other), "the epoch change to wait for the command")
            paused.release.set()
            writer.join(30)
            changer.join(30)
        self.assertEqual((paused.error, change.error, paused.result), ([], [], [2]))
        self.assertEqual(self.epoch(), 2)

    def test_concurrent_commands_share_the_maintenance_lock(self) -> None:
        first, second = Paused(), Paused()
        ctx_2 = self.ctx(self.member("Second", "editor"))
        threads = [
            first.start(lambda: library_command(self.db, self.ctx_a, EDIT, first.hold(bump("a1")))),
            second.start(lambda: library_command(self.db, ctx_2, EDIT, second.hold(bump("a2")))),
        ]
        self.assertTrue(first.inside.wait(30) and second.inside.wait(30))  # both inside at once
        first.release.set()
        second.release.set()
        for thread in threads:
            thread.join(30)
        self.assertEqual((first.result, second.result), ([2], [2]))


class WorkerFencingTests(ScopeTestCase):
    def test_a_job_from_before_a_restore_is_refused(self) -> None:
        job_id, lease_id = self.job(self.lib_a, 1)
        worker = RequestContext.for_job(self.db, job_id, lease_id)
        assert worker is not None
        self.assertEqual(library_command(self.db, worker, EDIT, bump("a1")), 2)
        self.restore()
        before = self.revisions()
        # Rebuilt from the job row, the context still carries the job's epoch.
        again = RequestContext.for_job(self.db, job_id, lease_id)
        assert again is not None
        self.assertEqual(again.epoch, 1)
        for ctx in (worker, again):
            self.assert_refused(Refusal.LIBRARY_EPOCH_CHANGED, lambda ctx=ctx: library_command(self.db, ctx, EDIT, bump("a1")))
        self.assertEqual(self.revisions(), before)

    def test_a_cancelled_or_reclaimed_job_has_no_authority(self) -> None:
        job_id, lease_id = self.job(self.lib_a, 1)
        worker = RequestContext.for_job(self.db, job_id, lease_id)
        assert worker is not None
        self.run_sql("UPDATE prks.jobs SET state = 'cancelled', lease_id = NULL WHERE id = %s", (job_id,))
        self.assertIsNone(RequestContext.for_job(self.db, job_id, lease_id))
        before = self.revisions()
        self.assert_refused(Refusal.NOT_AUTHENTICATED, lambda: library_command(self.db, worker, EDIT, bump("a1")))
        # A finished job keeps its lease id but no longer acts.
        done_id, done_lease = self.job(self.lib_a, 1)
        done = RequestContext.for_job(self.db, done_id, done_lease)
        assert done is not None
        self.run_sql("UPDATE prks.jobs SET state = 'succeeded' WHERE id = %s", (done_id,))
        self.assert_refused(Refusal.NOT_AUTHENTICATED, lambda: library_command(self.db, done, EDIT, bump("a1")))
        self.assertEqual(self.revisions(), before)

    def test_an_expired_lease_has_no_authority(self) -> None:
        job_id, lease_id = self.job(self.lib_a, 1)
        worker = RequestContext.for_job(self.db, job_id, lease_id)
        assert worker is not None
        self.run_sql("UPDATE prks.jobs SET locked_until = now() - interval '1 second' WHERE id = %s", (job_id,))
        self.assertIsNone(RequestContext.for_job(self.db, job_id, lease_id))
        before = self.revisions()
        self.assert_refused(Refusal.NOT_AUTHENTICATED, lambda: library_command(self.db, worker, EDIT, bump("a1")))
        self.assertEqual(self.revisions(), before)

    def test_a_worker_cannot_reach_another_library(self) -> None:
        job_id, lease_id = self.job(self.lib_b, 1)
        worker = RequestContext.for_job(self.db, job_id, lease_id)
        assert worker is not None
        self.assertIsNone(library_command(self.db, worker, EDIT, bump("a1")))
        self.assertEqual(self.revisions()[(self.lib_a, "a1")], 1)
        self.assert_refused(Refusal.FORBIDDEN,
                            lambda: library_command(self.db, worker, Capability.ADMIN_LIBRARY, bump("b1")))


class ScopeHelperTests(ScopeTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.ctx_a = self.ctx(self.member("Editor", "editor"))

    def test_statements_must_be_scoped_to_the_context_library(self) -> None:
        def unscoped(scope: LibraryScope) -> None:
            scope.execute("UPDATE prks.entity_revisions SET revision = 0")

        def other_library(scope: LibraryScope) -> None:
            scope.execute("UPDATE prks.entity_revisions SET revision = 0 WHERE library_id = %(library_id)s",
                          {"library_id": self.lib_b})

        before = self.revisions()
        for fn in (unscoped, other_library):
            with self.subTest(fn=fn.__name__), self.assertRaises(ValueError):
                library_command(self.db, self.ctx_a, EDIT, fn)
        self.assertEqual(self.revisions(), before)

    def test_the_library_is_set_for_the_transaction_only(self) -> None:
        db = Database(DatabaseConfig(conninfo=self.harness.conninfo(self.name), role="test", pool_min=0, pool_max=1))
        self.addCleanup(db.close)

        def setting(scope: LibraryScope) -> Any:
            return scope.execute_unscoped("SELECT current_setting('prks.library_id') AS v").fetchone()

        self.assertEqual(library_command(db, self.ctx_a, EDIT, setting), {"v": self.lib_a})
        self.assertEqual(library_query(db, self.ctx_a, setting), {"v": self.lib_a})
        after = db.query(lambda tx: tx.execute("SELECT current_setting('prks.library_id', true) AS v").fetchone())
        self.assertIn(after, ({"v": None}, {"v": ""}))  # same pooled connection, nothing left over

    def test_no_advisory_lock_outlives_the_command(self) -> None:
        library_command(self.db, self.ctx_a, EDIT, bump("a1"))
        with self.assertRaises(Exception) as refused:
            library_command(self.db, self.ctx(self.member("Reader", "reader")), EDIT, bump("a1"))
        self.assertIsNotNone(refused.exception)
        with psycopg.connect(self.harness.conninfo(self.name), autocommit=True) as conn:
            held = conn.execute(
                "SELECT count(*) FROM pg_locks l JOIN pg_database d ON d.oid = l.database "
                "WHERE l.locktype = 'advisory' AND d.datname = current_database()"
            ).fetchone()
            got = conn.execute("SELECT pg_try_advisory_lock(" + LIBRARY_LOCK_KEY_SQL + ")", (self.lib_a,)).fetchone()
        self.assertEqual((held, got), ((0,), (True,)))

    def test_commands_take_the_published_maintenance_key(self) -> None:
        from backend.domain.identity import library_scope

        self.assertIn(LIBRARY_LOCK_KEY_SQL, library_scope._TRY_LOCK_SHARED_SQL)

    def test_queries_report_the_current_epoch(self) -> None:
        self.restore()
        self.assertEqual(library_query(self.db, self.ctx_a, lambda scope: scope.epoch), 2)

    def test_library_helpers_refuse_instance_capabilities_and_library_less_contexts(self) -> None:
        reader = self.ctx(self.member("Reader", "reader"))
        before = self.revisions()
        for capability in (Capability.ADMIN_INSTANCE, Capability.READ_LIBRARY):
            with self.subTest(capability=capability), self.assertRaises(ValueError):
                library_command(self.db, reader, capability, bump("a1"))  # a read never authorizes a write
        self.assertEqual(self.revisions(), before)
        self.assert_refused(Refusal.FORBIDDEN,
                            lambda: library_command(self.db, RequestContext.for_admin(epoch=1), EDIT, bump("a1")))
        admin = RequestContext.for_admin(library_id=self.lib_a, epoch=1)
        self.assertEqual(library_command(self.db, admin, Capability.ADMIN_LIBRARY, bump("a1")), 2)
