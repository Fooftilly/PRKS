"""Restore R2 to R5 and recovery (L-B-1 #639; §11.4, §13.3).

§13.3 "Restore commit protocol": a committed restore replaces the Library's
rows atomically, advances the epoch, cancels the Library's jobs, holds the
objects only the old catalogue named (and keeps the holds of an earlier
restore), and queues the rebuild, cleanup and sweep in the same transaction.
Every abort after R2 (a fault in R4, ``53100``, a lost lease, a drain
timeout) leaves the Library as it was, clears the flag the run set, and fails
the run with its reason. Recovery fails runs whose lease expired, never
takes over a restore that reached R3, and drops staging schemas of runs that
are no longer live.
"""

from __future__ import annotations

import shutil
import tempfile
import uuid
from collections.abc import Callable
from typing import Any, Optional
from unittest import mock

import psycopg
from psycopg import errors

from backend.db.config import DatabaseConfig
from backend.db.database import Database
from backend.domain.maintenance import restore
from backend.domain.maintenance.connection import maintenance_connection
from backend.domain.maintenance.restore import RestoreFailed, RestoreResult
from backend.domain.maintenance.staging import staging_schema
from backend.domain.objects import lifecycle
from backend.domain.objects.lifecycle import Settled
from backend.storage.objects import LocalFilesystemStorage, StorageKey
from tests.pg.facade_context import provision_actor
from tests.pg.test_backup_export import BackupTestCase


class RestoreTestCase(BackupTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.staging_dir = tempfile.mkdtemp(prefix="prks-pg-restore-")
        self.addCleanup(shutil.rmtree, self.staging_dir, True)

    def restore(self, path: str, *, library_id: Optional[str] = None, owner: Optional[str] = None,
                hooks: Optional[dict[str, Callable[[], None]]] = None, **kwargs: Any) -> RestoreResult:
        return restore.restore_library(
            self.conninfo, self.objects, path, library_id or self.actor.library_id,
            owner_user_id=owner or self.actor.user_id, staging_dir=self.staging_dir, hooks=hooks, **kwargs)

    def refused(self, path: str, reason: str, **kwargs: Any) -> RestoreFailed:
        with self.assertRaises(RestoreFailed) as caught:
            self.restore(path, **kwargs)
        self.assertEqual(caught.exception.reason, reason, caught.exception.message)
        return caught.exception

    def library_row(self, library_id: Optional[str] = None) -> dict[str, Any]:
        return self.sql("SELECT maintenance, maintenance_run, epoch, search_folding FROM prks.libraries "
                        "WHERE id = %s", (library_id or self.actor.library_id,))[0]

    def restore_runs(self) -> list[dict[str, Any]]:
        return self.sql("SELECT id, state, phase, error_code, error_message FROM prks.restore_runs "
                        "ORDER BY started_at")

    def work_ids(self) -> list[str]:
        return [row["id"] for row in self.sql("SELECT id FROM prks.works WHERE library_id = %s ORDER BY id",
                                              (self.actor.library_id,))]

    def staging_schemas(self) -> list[str]:
        return [row["nspname"] for row in self.sql(
            "SELECT nspname FROM pg_namespace WHERE nspname LIKE 'restore\\_%%' ORDER BY nspname")]

    def jobs(self) -> list[dict[str, Any]]:
        return self.sql("SELECT kind, state, library_epoch, last_error FROM prks.jobs ORDER BY created_at, kind")

    def assert_unchanged_after_abort(self, works: list[str], epoch: int, reason: str) -> None:
        self.assertEqual(self.work_ids(), works)
        library = self.library_row()
        self.assertEqual((library["maintenance"], library["maintenance_run"], library["epoch"]),
                         (None, None, epoch))
        (run,) = self.restore_runs()
        self.assertEqual((run["state"], run["error_code"]), ("failed", reason))
        self.assertEqual(self.staging_schemas(), [])
        self.assertEqual(self.advisory_locks(), 0)

    def advisory_locks(self) -> int:
        return int(self.sql("SELECT count(*) AS n FROM pg_locks WHERE locktype = 'advisory'")[0]["n"])

    def recover(self) -> restore.Recovered:
        with maintenance_connection(self.conninfo) as conn:
            return restore.recover(conn)

    def expire_lease(self) -> None:
        self.sql("UPDATE prks.restore_runs SET locked_until = now() - interval '1 second' "
                 "WHERE state = 'running'")


class CommitTests(RestoreTestCase):
    def test_a_restore_replaces_the_library_and_queues_its_follow_up_work(self) -> None:
        kept = self.put(b"%PDF kept")
        kept_work = self.create(kept)
        tag = self.library.add_tag("Philosophy")
        self.library.add_tag_to_work(kept_work, tag["id"])
        archive = self.backup().path
        tags_then = self.sql("SELECT id, name, name_key FROM prks.tags ORDER BY id")
        # After the backup: a new Work with a new object, an edit, and a delete.
        later = self.put(b"%PDF later")
        later_work = self.create(later)
        self.sql("UPDATE prks.works SET title = 'Edited' WHERE id = %s", (kept_work,))
        self.sql("DELETE FROM prks.work_tags")
        queued = self.sql("INSERT INTO prks.jobs (library_id, library_epoch, kind) VALUES (%s, 1, 'test.kind') "
                          "RETURNING id", (self.actor.library_id,))[0]["id"]
        result = self.restore(archive)
        self.assertEqual(self.work_ids(), [kept_work])
        self.assertEqual(self.sql("SELECT title FROM prks.works WHERE id = %s", (kept_work,))[0]["title"],
                         "Created")
        self.assertEqual(self.sql("SELECT id, name, name_key FROM prks.tags ORDER BY id"), tags_then)
        self.assertEqual(len(self.sql("SELECT 1 FROM prks.work_tags")), 1)
        library = self.library_row()
        self.assertEqual((library["maintenance"], library["maintenance_run"], library["epoch"],
                          library["search_folding"]), (None, None, 2, None))
        self.assertEqual(result.epoch, 2)
        # The object only the old catalogue named is held, never cleaned up.
        self.assertEqual(result.objects_held, 1)
        claim = self.claims()[later.key.name]
        self.assertEqual((claim["hold"], claim["library_id"]), (True, self.actor.library_id))
        self.assertNotIn(kept.key.name, self.claims())
        self.assertNotIn(later_work, self.work_ids())
        # The old job is cancelled; the follow-ups are queued at the new epoch.
        self.assertEqual(self.sql("SELECT state, last_error FROM prks.jobs WHERE id = %s", (queued,)),
                         [{"state": "cancelled", "last_error": "restore_superseded"}])
        queued_now = {(row["kind"], row["library_epoch"]) for row in self.jobs() if row["state"] == "queued"}
        self.assertEqual(queued_now, {("derived.rebuild", 2), ("objects.cleanup", None), ("objects.sweep", None)})
        (run,) = self.restore_runs()
        self.assertEqual((run["id"], run["state"], run["phase"], run["error_code"]),
                         (result.run_id, "committed", "commit", None))
        self.assertEqual(self.sql("SELECT 1 FROM prks.restore_run_objects WHERE run_id = %s", (result.run_id,)), [])
        self.assertEqual(self.staging_schemas(), [])
        self.assertEqual(self.advisory_locks(), 0)

    def test_derived_rows_are_deleted_for_the_rebuild(self) -> None:
        work_id = self.create(self.put())
        archive = self.backup().path
        self.sql("INSERT INTO derived.work_search (library_id, work_id, doc, title_norm, people_norm, text_norm) "
                 "VALUES (%s, %s, ''::tsvector, 'x', '', '') ON CONFLICT DO NOTHING", (self.actor.library_id, work_id))
        self.restore(archive)
        self.assertEqual(self.sql("SELECT 1 FROM derived.work_search WHERE library_id = %s",
                                  (self.actor.library_id,)), [])

    def test_holds_survive_a_second_restore_of_the_same_archive(self) -> None:
        self.create(self.put(b"%PDF archived"))
        archive = self.backup().path
        replaced = self.put(b"%PDF replaced")
        self.create(replaced)
        self.restore(archive)
        self.assertTrue(self.claims()[replaced.key.name]["hold"])
        # The archive predates the hold, and its catalogue never names the key.
        second = self.restore(archive)
        self.assertEqual(second.epoch, 3)
        self.assertTrue(self.claims()[replaced.key.name]["hold"])
        self.sweep()
        self.assertEqual(lifecycle.retry_claims(self.db, self.objects), {})
        self.assertEqual(self.settle(replaced.key), Settled.HELD)
        self.assertTrue(self.stored(replaced.key.name))

    def test_a_staged_object_unlinked_before_the_drain_is_written_again(self) -> None:
        obj = self.put(b"%PDF reused")
        self.create(obj)
        archive = self.backup().path

        def unlink() -> None:
            self.objects.delete(obj.key)

        self.restore(archive, hooks={restore.HOOK_ENTERED: unlink})
        self.assertTrue(self.objects.verify(obj.key, obj.sha256))

    def test_a_serialization_failure_is_retried(self) -> None:
        self.create(self.put())
        archive = self.backup().path
        attempts: list[int] = []

        def conflict_once() -> None:
            attempts.append(1)
            if len(attempts) == 1:
                raise errors.SerializationFailure("simulated")

        result = self.restore(archive, hooks={restore.HOOK_COMMITTING: conflict_once})
        self.assertEqual(len(attempts), 2)
        self.assertEqual(result.epoch, 2)
        self.assertEqual(self.library_row()["epoch"], 2)

    def test_a_restore_into_a_new_instance_inserts_its_devices(self) -> None:
        obj = self.put(b"%PDF travelling")
        work_id = self.create(obj)
        self.ledger_row(self.actor, work_id)
        archive = self.backup().path
        name = self.harness.create_database("target")
        self.addCleanup(self.harness.drop_database, name)
        conninfo = self.harness.conninfo(name)
        target_db = Database(DatabaseConfig(conninfo=conninfo, role="test", pool_min=0, pool_max=4))
        self.addCleanup(target_db.close)
        target = provision_actor(target_db, name="New")
        root = tempfile.mkdtemp(prefix="prks-pg-target-")
        self.addCleanup(shutil.rmtree, root, True)
        target_objects = LocalFilesystemStorage(root)
        result = restore.restore_library(conninfo, target_objects, archive, target.library_id,
                                         owner_user_id=target.user_id, staging_dir=self.staging_dir)
        self.assertEqual(result.new_devices, 1)

        def read(statement: str) -> list[dict[str, Any]]:
            return target_db.command(lambda tx: tx.execute(statement.encode()).fetchall())

        self.assertEqual(read("SELECT id, library_id FROM prks.works"),
                         [{"id": work_id, "library_id": target.library_id}])
        (op,) = read("SELECT device_id, user_id FROM prks.sync_operations")
        self.assertEqual((op["device_id"], op["user_id"]), (self.actor.device_id, target.user_id))
        (device,) = read(f"SELECT user_id FROM prks.devices WHERE id = '{self.actor.device_id}'")
        self.assertEqual(device["user_id"], target.user_id)
        self.assertIsNotNone(target_objects.stat(StorageKey("asset-objects", obj.key.name)))


class RefusalTests(RestoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.work = self.create(self.put())
        self.archive = self.backup().path

    def test_only_an_owner_can_restore(self) -> None:
        reader = self.other_actor()
        self.refused(self.archive, "forbidden", owner=reader.user_id)
        self.assertEqual(self.restore_runs(), [])

    def test_a_restore_is_refused_while_the_library_is_in_maintenance(self) -> None:
        self.sql("UPDATE prks.libraries SET maintenance = 'rebuild', maintenance_run = %s WHERE id = %s",
                 (uuid.uuid4(), self.actor.library_id))
        self.refused(self.archive, "MAINTENANCE")
        self.assertEqual(self.restore_runs(), [])

    def test_a_running_backup_refuses_r2(self) -> None:
        self.sql("INSERT INTO prks.backup_runs (library_id) VALUES (%s)", (self.actor.library_id,))
        self.refused(self.archive, "backup_running")
        self.assert_unchanged_after_abort([self.work], 1, "backup_running")

    def test_a_backup_whose_lease_expired_does_not_block(self) -> None:
        self.sql("INSERT INTO prks.backup_runs (library_id, locked_until) VALUES (%s, now() - interval '1 s')",
                 (self.actor.library_id,))
        self.assertEqual(self.restore(self.archive).epoch, 2)

    def test_unknown_libraries_are_refused(self) -> None:
        self.refused(self.archive, "library_missing", library_id="LIB-" + "0" * 32)


class AbortTests(RestoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.work = self.create(self.put())
        self.archive = self.backup().path
        self.later = self.create(self.put(b"%PDF later"))
        self.before = self.work_ids()

    def test_a_fault_inside_r4_leaves_the_library_as_it_was(self) -> None:
        def fail() -> None:
            raise RuntimeError("injected")

        self.refused(self.archive, "internal", hooks={restore.HOOK_COMMITTING: fail})
        self.assert_unchanged_after_abort(self.before, 1, "internal")
        # The Library's jobs stay queued, and nothing R4 enqueued survived.
        self.assertEqual({(row["kind"], row["state"], row["library_epoch"]) for row in self.jobs()},
                         {("text.extract", "queued", 1)})

    def test_a_full_disk_in_r4_fails_the_run_with_its_sqlstate(self) -> None:
        def disk_full() -> None:
            raise errors.DiskFull("could not extend file")

        error = self.refused(self.archive, "53100", hooks={restore.HOOK_COMMITTING: disk_full})
        self.assertIn("53100", error.message)
        self.assert_unchanged_after_abort(self.before, 1, "53100")

    def test_a_full_disk_in_r1_fails_the_run_and_drops_the_staging_schema(self) -> None:
        real_stage = restore.stage

        def stage_until_full(conn: Any, *args: Any, **kwargs: Any) -> Any:
            # Leave a schema behind, as a crash between R1 statements could, to prove R5 drops it.
            run_id = kwargs["run_id"]
            conn.execute(f"CREATE SCHEMA {staging_schema(run_id)}".encode())
            raise errors.DiskFull("could not extend file")

        with mock.patch.object(restore, "stage", stage_until_full):
            self.refused(self.archive, "53100")
        self.assertIs(restore.stage, real_stage)
        self.assert_unchanged_after_abort(self.before, 1, "53100")

    def test_a_refused_archive_fails_the_run_before_r2(self) -> None:
        with open(self.archive, "r+b") as handle:
            handle.seek(0)
            handle.write(b"XXXX")
        self.refused(self.archive, "not_a_backup")
        self.assert_unchanged_after_abort(self.before, 1, "not_a_backup")

    def test_a_drain_that_cannot_get_the_library_lock_times_out(self) -> None:
        with psycopg.connect(self.conninfo, autocommit=True) as holder:
            holder.execute("SELECT pg_advisory_lock_shared(hashtextextended('prks.library:' || %s, 0))",
                           (self.actor.library_id,))
            self.refused(self.archive, "drain_timeout", drain_timeout_seconds=0.2)
            holder.execute("SELECT pg_advisory_unlock_all()")
        self.assert_unchanged_after_abort(self.before, 1, "drain_timeout")

    def test_commands_are_refused_while_the_flag_is_set(self) -> None:
        seen: dict[str, Any] = {}

        def try_command() -> None:
            try:
                self.library.add_tag("During")
            except Exception as exc:  # noqa: BLE001 - the admission error type is the Works lane's
                seen["error"] = exc

        self.restore(self.archive, hooks={restore.HOOK_ENTERED: try_command})
        self.assertIn("error", seen)
        self.assertEqual(self.sql("SELECT 1 FROM prks.tags WHERE name = 'During'"), [])


class LeaseTests(RestoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.work = self.create(self.put())
        self.archive = self.backup().path
        self.later = self.create(self.put(b"%PDF later"))
        self.before = self.work_ids()

    def test_recovery_takes_over_a_run_that_stalled_before_the_drain(self) -> None:
        recovered: dict[str, restore.Recovered] = {}

        def stall() -> None:
            self.expire_lease()
            recovered["r"] = self.recover()

        self.refused(self.archive, "lease_lost", hooks={restore.HOOK_ENTERED: stall})
        self.assertEqual((recovered["r"].restores_failed, recovered["r"].flags_cleared), (1, 1))
        self.assertEqual(self.work_ids(), self.before)
        (run,) = self.restore_runs()
        self.assertEqual((run["state"], run["error_code"]), ("failed", "lease_expired"))
        library = self.library_row()
        self.assertEqual((library["maintenance"], library["epoch"]), (None, 1))

    def test_recovery_never_takes_over_a_run_that_reached_the_drain(self) -> None:
        seen: dict[str, Any] = {}

        def stall() -> None:
            self.expire_lease()
            seen["recovered"] = self.recover()
            seen["flag"] = self.library_row()["maintenance"]

        # The stalled run cannot commit either: its lease is gone.
        self.refused(self.archive, "lease_lost", hooks={restore.HOOK_DRAINED: stall})
        self.assertEqual(seen["recovered"].restores_failed, 0)
        self.assertEqual(seen["flag"], "restore")
        self.assert_unchanged_after_abort(self.before, 1, "lease_lost")

    def test_recovery_after_a_crash_clears_the_flag_and_drops_the_schema(self) -> None:
        run_id = self.sql("INSERT INTO prks.restore_runs (library_id, locked_until) "
                          "VALUES (%s, now() - interval '1 s') RETURNING id", (self.actor.library_id,))[0]["id"]
        self.sql("UPDATE prks.libraries SET maintenance = 'restore', maintenance_run = %s WHERE id = %s",
                 (run_id, self.actor.library_id))
        self.sql(f"CREATE SCHEMA {staging_schema(run_id)}")
        live = self.sql("INSERT INTO prks.restore_runs (library_id) VALUES (%s) RETURNING id",
                        (self.other_actor().library_id,))[0]["id"]
        self.sql(f"CREATE SCHEMA {staging_schema(live)}")
        orphan = uuid.uuid4()
        self.sql(f"CREATE SCHEMA {staging_schema(orphan)}")
        self.sql("INSERT INTO prks.backup_runs (library_id, locked_until) VALUES (%s, now() - interval '1 s')",
                 (self.actor.library_id,))
        result = self.recover()
        self.assertEqual(result, restore.Recovered(restores_failed=1, flags_cleared=1, backups_failed=1,
                                                   schemas_dropped=2))
        self.assertEqual(self.staging_schemas(), [staging_schema(live)])
        self.assertIsNone(self.library_row()["maintenance"])
        self.assertEqual(self.recover(), restore.Recovered(0, 0, 0, 0))
