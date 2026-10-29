"""The immutable-object lifecycle on PostgreSQL (L-A-1 #635; ``backend/domain/objects``).

Object creation and adoption, cleanup claims recorded by the transaction
that stops naming a key, the three-valued reference check (Assets of any
Library, live restore runs), settle under the per-object lock and the
Library's objects lock, the orphan sweep's grace period, and the §13.3
interleavings: a write paused after storing its key while a sweep's claim
on that key is settled fails cleanly at adoption, and a settle that meets
an adoption in flight keeps the object.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any, Optional
from unittest import mock

import psycopg

from backend.domain.identity.authorization import Capability
from backend.domain.identity.library_scope import LibraryScope, library_command
from backend.domain.objects import lifecycle
from backend.domain.objects.lifecycle import ObjectMissing, Settled, StoredObject
from backend.domain.works import commands
from backend.entity_ids import generate
from backend.pg_facade import context as facade_context
from backend.storage.objects import (
    NAMESPACE_ASSET_OBJECTS,
    NAMESPACE_PORTRAITS,
    LocalFilesystemStorage,
    StorageKey,
)
from tests.pg.test_works_facade import WorksFacadeTestCase

DAY = 24 * 60 * 60


def key(name: str, namespace: str = NAMESPACE_ASSET_OBJECTS) -> StorageKey:
    return StorageKey(namespace, name)


class PausingStorage(LocalFilesystemStorage):
    """The local backend, with a hook before ``delete`` and an optional failure."""

    def __init__(self, root: str) -> None:
        super().__init__(root)
        self.before_delete: Optional[Callable[[], None]] = None
        self.fail_delete = False

    def delete(self, key: StorageKey) -> bool:
        if self.before_delete is not None:
            self.before_delete()
        if self.fail_delete:
            raise PermissionError("refused")
        return super().delete(key)


class ObjectsTestCase(WorksFacadeTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.objects = PausingStorage(self.storage.root)

    # -- objects and Works ------------------------------------------------------

    def put(self, data: bytes = b"%PDF-1.4 bytes", name: Optional[str] = None) -> StoredObject:
        stored_key = key(name or f"{uuid.uuid4().hex}.pdf")
        info = self.objects.put_new(stored_key, lambda stream: stream.write(data))
        assert info.sha256 is not None
        return StoredObject(stored_key, info.size, info.sha256)

    def stored(self, name: str) -> bool:
        return self.objects.stat(key(name)) is not None

    def work_with(self, obj: StoredObject) -> str:
        return self.library.add_work("Paper", file_path=f"/api/pdfs/{obj.key.name}")

    def command(self, fn: Callable[[LibraryScope], Any], ctx: Any = None) -> Any:
        return library_command(self.db, ctx or self.actor.context, Capability.EDIT_LIBRARY, fn)

    def create(self, obj: StoredObject, *, size: Optional[int] = None, ctx: Any = None) -> str:
        work_id = generate("W")
        new = commands.NewWork(
            work_id=work_id, manifestation_id=generate("MF"), title="Created", asset_id=generate("AS"),
            asset=commands.NewAsset(source=commands.ManagedFile(
                storage_locator=obj.key.name, sha256=obj.sha256,
                byte_size=obj.size if size is None else size)))
        self.command(lambda scope: commands.create_work(scope, new, objects=self.objects), ctx)
        return work_id

    def asset_of(self, work_id: str) -> dict[str, Any]:
        return self.sql("SELECT id, storage_locator, content_sha256, byte_size FROM prks.assets "
                        "WHERE work_id = %s", (work_id,))[0]

    # -- claims and runs --------------------------------------------------------

    def claims(self) -> dict[str, dict[str, Any]]:
        return {row["name"]: row for row in self.sql(
            "SELECT namespace, name, library_id, hold, last_attempt_at FROM prks.object_cleanup_claims")}

    def claim(self, obj_key: StorageKey, library_id: Optional[str] = None, *, hold: bool = False) -> None:
        self.sql("INSERT INTO prks.object_cleanup_claims (namespace, name, library_id, hold) "
                 "VALUES (%s, %s, %s, %s)", (obj_key.namespace, obj_key.name, library_id, hold))

    def restore_run(self, *staged: StorageKey, state: str = "running") -> uuid.UUID:
        run_id = self.sql("INSERT INTO prks.restore_runs (library_id) VALUES (%s) RETURNING id",
                          (self.actor.library_id,))[0]["id"]
        for staged_key in staged:
            self.sql("INSERT INTO prks.restore_run_objects (run_id, namespace, name) VALUES (%s, %s, %s)",
                     (run_id, staged_key.namespace, staged_key.name))
        if state != "running":
            self.finish_run(run_id, state)
        return run_id

    def finish_run(self, run_id: uuid.UUID, state: str) -> None:
        self.sql("UPDATE prks.restore_runs SET state = %s, finished_at = now() WHERE id = %s", (state, run_id))

    def settle(self, obj_key: StorageKey) -> Settled:
        return lifecycle.settle(self.db, self.objects, obj_key)

    def sweep(self, *, days: float = 2, **kwargs: Any) -> int:
        return lifecycle.sweep(self.db, self.objects, now=time.time() + days * DAY, **kwargs)

    # -- interleavings ----------------------------------------------------------

    def wait_until(self, condition: Callable[[], bool], what: str) -> None:
        deadline = time.monotonic() + 30
        while not condition():
            if time.monotonic() > deadline:
                self.fail(f"timed out waiting for {what}")
            time.sleep(0.01)

    def lock_waiters(self) -> int:
        return int(self.sql("SELECT count(*) AS n FROM pg_locks WHERE locktype = 'advisory' "
                            "AND NOT granted")[0]["n"])


class ClaimTests(ObjectsTestCase):
    def test_a_work_delete_claims_its_file_and_settle_removes_it(self) -> None:
        obj = self.put()
        work_id = self.work_with(obj)
        self.assertEqual(self.asset_of(work_id)["byte_size"], obj.size)
        self.assertEqual(self.asset_of(work_id)["content_sha256"], obj.sha256)
        self.assertEqual(self.claims(), {})
        self.library.delete_work_record(work_id)
        claim = self.claims()[obj.key.name]
        self.assertEqual((claim["namespace"], claim["library_id"], claim["hold"]),
                         (NAMESPACE_ASSET_OBJECTS, self.actor.library_id, False))
        self.assertTrue(self.stored(obj.key.name), "recording a claim never deletes")
        self.assertIs(self.settle(obj.key), Settled.REMOVED)
        self.assertFalse(self.stored(obj.key.name))
        self.assertEqual(self.claims(), {})
        self.assertIs(self.settle(obj.key), Settled.ABSENT)

    def test_a_work_delete_claims_every_key_its_assets_name(self) -> None:
        primary, attachment, pristine = self.put(), self.put(), self.put()
        work_id = self.work_with(primary)
        manifestation = self.sql("SELECT primary_manifestation_id AS id FROM prks.works WHERE id = %s",
                                 (work_id,))[0]["id"]
        self.sql("INSERT INTO prks.assets (library_id, id, manifestation_id, work_id, kind, role, "
                 "storage_locator, source_locator, content_sha256, origin) "
                 "VALUES (%s, %s, %s, %s, 'managed_file', 'attachment', %s, %s, %s, 'upload')",
                 (self.actor.library_id, generate("AS"), manifestation, work_id, attachment.key.name,
                  pristine.key.name, attachment.sha256))
        self.library.delete_work_record(work_id)
        self.assertEqual(set(self.claims()), {primary.key.name, attachment.key.name, pristine.key.name})
        self.assertEqual(lifecycle.retry_claims(self.db, self.objects), {Settled.REMOVED: 3})

    def test_undoing_a_create_that_adopted_existing_bytes_claims_nothing(self) -> None:
        obj = self.put()
        work_id = self.work_with(obj)
        self.library.delete_work_record(work_id, claim_pdf=False)
        self.assertEqual(self.count("works"), 0)
        self.assertEqual(self.claims(), {})
        self.assertTrue(self.stored(obj.key.name))

    def test_names_no_key_could_have_are_refused(self) -> None:
        for name in (" paper.pdf", "paper.pdf ", "a/b.pdf", "..", ".prks-write-1.tmp"):
            with self.subTest(name=name):
                with self.assertRaises(psycopg.errors.CheckViolation):
                    with psycopg.connect(self.harness.conninfo(self.sql(
                            "SELECT current_database() AS n")[0]["n"]), autocommit=True) as conn:
                        conn.execute("INSERT INTO prks.object_cleanup_claims (namespace, name) "
                                     "VALUES ('asset-objects', %s)", (name,))

    def test_a_key_another_asset_names_is_kept(self) -> None:
        shared = self.put()
        first, second = self.work_with(shared), self.work_with(shared)
        self.library.delete_work_record(first)
        self.assertEqual(self.claims(), {}, "another Asset still names the key")
        # A claim recorded anyway (a stale one) is retired, never acted on.
        self.claim(shared.key, self.actor.library_id)
        self.assertIs(self.settle(shared.key), Settled.REFERENCED)
        self.assertTrue(self.stored(shared.key.name))
        self.assertEqual(self.claims(), {})
        self.library.delete_work_record(second)
        self.assertIs(self.settle(shared.key), Settled.REMOVED)

    def test_identical_bytes_under_two_keys_are_two_objects(self) -> None:
        # Content is never deduplicated (§11.2): each write is its own key.
        one, two = self.put(b"same bytes"), self.put(b"same bytes")
        self.assertEqual(one.sha256, two.sha256)
        first, second = self.work_with(one), self.work_with(two)
        self.library.delete_work_record(first)
        self.assertEqual(set(self.claims()), {one.key.name})
        self.assertIs(self.settle(one.key), Settled.REMOVED)
        self.assertTrue(self.stored(two.key.name))
        self.assertEqual(self.asset_of(second)["storage_locator"], two.key.name)

    def test_a_held_claim_is_never_settled_automatically(self) -> None:
        obj = self.put()
        self.claim(obj.key, self.actor.library_id, hold=True)
        self.assertIs(self.settle(obj.key), Settled.HELD)
        self.assertEqual(lifecycle.retry_claims(self.db, self.objects), {})
        self.assertTrue(self.stored(obj.key.name))
        # A later release of the same key keeps the hold.
        self.command(lambda scope: lifecycle.release(scope, obj.key))
        self.assertTrue(self.claims()[obj.key.name]["hold"])

    def test_a_portrait_no_person_names_is_unreferenced(self) -> None:
        # Persons' portrait_locator is the catalogue (tests/pg/test_objects_portraits.py).
        portrait = key("P-1_abc.jpg", NAMESPACE_PORTRAITS)
        self.objects.put_new(portrait, lambda stream: stream.write(b"jpeg"))
        self.claim(portrait)
        self.assertIs(self.settle(portrait), Settled.REMOVED)
        self.assertIsNone(self.objects.stat(portrait))
        self.objects.put_new(portrait, lambda stream: stream.write(b"jpeg"))
        self.assertEqual(self.sweep(namespaces=(NAMESPACE_PORTRAITS,)), 1)

    def test_a_release_takes_over_an_orphan_claim(self) -> None:
        obj = self.put()
        self.assertEqual(self.sweep(), 1)
        self.assertIsNone(self.claims()[obj.key.name]["library_id"])
        work_id = self.work_with(obj)
        self.library.delete_work_record(work_id)
        # Settling it now waits for this Library's backups (§11.3).
        self.assertEqual(self.claims()[obj.key.name]["library_id"], self.actor.library_id)


class SweepTests(ObjectsTestCase):
    def test_an_interrupted_ingest_is_claimed_after_the_grace_period(self) -> None:
        orphan = self.put()
        self.assertEqual(lifecycle.sweep(self.db, self.objects), 0, "a write in progress is young")
        self.assertEqual(self.sweep(days=0.5), 0)
        self.assertEqual(self.sweep(), 1)
        self.assertIsNone(self.claims()[orphan.key.name]["library_id"])
        self.assertTrue(self.stored(orphan.key.name), "the sweep only records claims")
        self.assertEqual(self.sweep(), 0, "a second sweep records nothing new")
        self.assertEqual(lifecycle.retry_claims(self.db, self.objects), {Settled.REMOVED: 1})
        self.assertFalse(self.stored(orphan.key.name))

    def test_referenced_and_staged_keys_are_never_claimed(self) -> None:
        named, staged, abandoned, committed = self.put(), self.put(), self.put(), self.put()
        self.work_with(named)
        self.restore_run(staged.key)
        self.restore_run(abandoned.key, state="failed")
        self.restore_run(committed.key, state="committed")
        self.assertEqual(self.sweep(), 2)
        self.assertEqual(set(self.claims()), {abandoned.key.name, committed.key.name})

    def test_temporaries_beside_objects_are_not_keys(self) -> None:
        self.put()
        with open(os.path.join(self.storage.pdfs_dir, ".prks-write-x.tmp"), "wb") as handle:
            handle.write(b"partial")
        self.assertEqual(self.sweep(), 1)
        self.assertTrue(os.path.exists(os.path.join(self.storage.pdfs_dir, ".prks-write-x.tmp")))


class RestoreRunTests(ObjectsTestCase):
    def test_a_live_run_keeps_a_claimed_key_until_it_fails(self) -> None:
        obj = self.put()
        run_id = self.restore_run(obj.key)
        self.claim(obj.key, self.actor.library_id)
        self.assertIs(self.settle(obj.key), Settled.REFERENCED)
        self.assertTrue(self.stored(obj.key.name))
        self.assertEqual(self.sweep(), 0)
        self.finish_run(run_id, "failed")
        self.assertEqual(self.sweep(), 1, "a failed run's staged objects are ordinary orphans")
        self.assertIs(self.settle(obj.key), Settled.REMOVED)

    def test_a_committed_run_hands_its_keys_to_the_catalogue(self) -> None:
        obj = self.put()
        run_id = self.restore_run(obj.key)
        self.work_with(obj)  # R4 inserts the restored Asset naming the staged key
        self.finish_run(run_id, "committed")
        self.sql("DELETE FROM prks.restore_run_objects WHERE run_id = %s", (run_id,))
        self.assertEqual(self.sweep(), 0)

    def test_a_staged_portrait_is_referenced(self) -> None:
        portrait = key("P-2_def.jpg", NAMESPACE_PORTRAITS)
        self.restore_run(portrait)
        self.claim(portrait)
        self.assertIs(self.settle(portrait), Settled.REFERENCED)


class AdoptionTests(ObjectsTestCase):
    def test_create_adopts_what_it_wrote(self) -> None:
        obj = self.put()
        work_id = self.create(obj)
        asset = self.asset_of(work_id)
        self.assertEqual((asset["storage_locator"], asset["content_sha256"], asset["byte_size"]),
                         (obj.key.name, obj.sha256, obj.size))

    def test_a_key_the_sweep_settled_fails_adoption_and_commits_nothing(self) -> None:
        obj = self.put()
        self.sweep()
        self.assertIs(self.settle(obj.key), Settled.REMOVED)
        with self.assertRaises(ObjectMissing):
            self.create(obj)
        self.assertEqual((self.count("works"), self.count("assets")), (0, 0))

    def test_bytes_of_another_size_fail_adoption(self) -> None:
        obj = self.put()
        with self.assertRaises(ObjectMissing):
            self.create(obj, size=obj.size + 1)
        self.assertEqual(self.count("works"), 0)

    def test_a_managed_file_needs_the_storage_backend(self) -> None:
        obj = self.put()
        new = commands.NewWork(work_id=generate("W"), manifestation_id=generate("MF"), title="No backend",
                               asset_id=generate("AS"), asset=commands.NewAsset(source=commands.ManagedFile(
                                   storage_locator=obj.key.name, sha256=obj.sha256, byte_size=obj.size)))
        with self.assertRaisesRegex(ValueError, "storage backend"):
            self.command(lambda scope: commands.create_work(scope, new))
        self.assertEqual(self.count("works"), 0)


class RewriteTests(ObjectsTestCase):
    def test_a_rewrite_points_at_the_new_key_and_claims_the_old(self) -> None:
        old = self.put(b"old bytes")
        work_id = self.work_with(old)
        asset_id = self.asset_of(work_id)["id"]
        new = self.put(b"rewritten bytes")
        self.assertEqual(self.command(lambda scope: lifecycle.replace_object(scope, self.objects, asset_id, new)),
                         old.key.name)
        asset = self.asset_of(work_id)
        self.assertEqual((asset["storage_locator"], asset["content_sha256"], asset["byte_size"]),
                         (new.key.name, new.sha256, new.size))
        self.assertEqual(set(self.claims()), {old.key.name})
        with self.objects.open_read(old.key) as handle:
            self.assertEqual(handle.read(), b"old bytes", "no object is modified in place")
        self.assertEqual(lifecycle.retry_claims(self.db, self.objects), {Settled.REMOVED: 1})
        self.assertTrue(self.stored(new.key.name))

    def test_a_rewrite_whose_new_key_was_swept_fails_cleanly(self) -> None:
        old = self.put(b"old bytes")
        work_id = self.work_with(old)
        asset_id = self.asset_of(work_id)["id"]
        new = self.put(b"stalled rewrite")
        self.sweep()  # claims only the unadopted new key
        self.assertEqual(set(self.claims()), {new.key.name})
        self.assertIs(self.settle(new.key), Settled.REMOVED)
        with self.assertRaises(ObjectMissing):
            self.command(lambda scope: lifecycle.replace_object(scope, self.objects, asset_id, new))
        self.assertEqual(self.asset_of(work_id)["storage_locator"], old.key.name)
        self.assertEqual(self.claims(), {})
        self.assertTrue(self.stored(old.key.name))

    def test_a_rolled_back_rewrite_leaves_the_catalogue_as_it_was(self) -> None:
        old = self.put(b"old bytes")
        work_id = self.work_with(old)
        asset_id = self.asset_of(work_id)["id"]
        new = self.put(b"rolled back")

        def rewrite_then_fail(scope: LibraryScope) -> None:
            lifecycle.replace_object(scope, self.objects, asset_id, new)
            raise RuntimeError("later step failed")

        with self.assertRaises(RuntimeError):
            self.command(rewrite_then_fail)
        self.assertEqual(self.asset_of(work_id)["storage_locator"], old.key.name)
        self.assertEqual(self.claims(), {})
        # The new bytes are an ordinary orphan now.
        self.assertEqual(self.sweep(), 1)
        self.assertEqual(set(self.claims()), {new.key.name})

    def test_a_rewrite_needs_a_managed_file_of_this_library(self) -> None:
        obj = self.put()
        asset_id = self.asset_of(self.work_with(obj))["id"]
        other = self.other_actor()
        new = self.put(b"other")
        self.assertIsNone(self.command(
            lambda scope: lifecycle.replace_object(scope, self.objects, asset_id, new), other.context))
        self.assertEqual(self.claims(), {})


class IsolationTests(ObjectsTestCase):
    def test_another_library_cannot_adopt_or_free_this_librarys_key(self) -> None:
        obj = self.put()
        work_id = self.work_with(obj)
        other = self.other_actor()
        with self.assertRaises(ObjectMissing):
            self.create(obj, ctx=other.context)
        # A claim another Library records for the key is never acted on.
        self.assertTrue(self.command(lambda scope: lifecycle.release(scope, obj.key), other.context))
        self.assertEqual(self.claims()[obj.key.name]["library_id"], other.library_id)
        self.assertIs(self.settle(obj.key), Settled.REFERENCED)
        self.assertTrue(self.stored(obj.key.name))
        self.assertEqual(self.asset_of(work_id)["storage_locator"], obj.key.name)

    def test_a_key_another_library_released_cannot_be_adopted(self) -> None:
        obj = self.put()
        self.library.delete_work_record(self.work_with(obj))
        other = self.other_actor()
        with self.assertRaises(ObjectMissing):
            self.create(obj, ctx=other.context)
        self.assertEqual(self.claims()[obj.key.name]["library_id"], self.actor.library_id)
        self.create(obj)  # the Library that released it may name it again

    def test_a_reader_cannot_release_a_key(self) -> None:
        obj = self.put()
        from backend.domain.identity.authorization import AccessRefused

        with self.assertRaises(AccessRefused):
            self.command(lambda scope: lifecycle.release(scope, obj.key), self.reader_context())
        self.assertEqual(self.claims(), {})


class RetryTests(ObjectsTestCase):
    def test_a_failed_removal_keeps_the_claim_and_rotates(self) -> None:
        first, second = self.put(), self.put()
        self.claim(first.key, self.actor.library_id)
        self.claim(second.key, self.actor.library_id)
        self.objects.fail_delete = True
        self.assertEqual(lifecycle.retry_claims(self.db, self.objects, limit=1), {Settled.FAILED: 1})
        attempted = {name for name, row in self.claims().items() if row["last_attempt_at"] is not None}
        self.assertEqual(len(attempted), 1)
        # The next bounded pass takes the never-attempted claim first.
        self.objects.fail_delete = False
        self.assertEqual(lifecycle.retry_claims(self.db, self.objects, limit=1), {Settled.REMOVED: 1})
        self.assertEqual(set(self.claims()), attempted)
        self.assertEqual(lifecycle.retry_claims(self.db, self.objects), {Settled.REMOVED: 1})
        self.assertEqual(self.claims(), {})

    def test_bytes_already_gone_settle_as_removed(self) -> None:
        obj = self.put()
        self.claim(obj.key, self.actor.library_id)
        self.objects.delete(obj.key)
        self.assertIs(self.settle(obj.key), Settled.REMOVED)
        self.assertEqual(self.claims(), {})

    def test_a_commit_that_fails_after_the_unlink_is_settled_again(self) -> None:
        obj = self.put()
        self.claim(obj.key, self.actor.library_id)

        def fail_after_unlink() -> None:
            # Unlink first, then make the settle's transaction fail.
            LocalFilesystemStorage.delete(self.objects, obj.key)
            raise psycopg.errors.QueryCanceled("simulated failure after the unlink")

        self.objects.before_delete = fail_after_unlink
        with self.assertRaises(Exception):
            self.settle(obj.key)
        self.assertFalse(self.stored(obj.key.name))
        self.assertIn(obj.key.name, self.claims(), "the claim survives the rollback")
        self.objects.before_delete = None
        self.assertIs(self.settle(obj.key), Settled.REMOVED)
        self.assertEqual(self.claims(), {})

    def test_retry_can_be_limited_to_one_library(self) -> None:
        mine, theirs = self.put(), self.put()
        other = self.other_actor()
        self.claim(mine.key, self.actor.library_id)
        self.claim(theirs.key, other.library_id)
        self.assertEqual(lifecycle.retry_claims(self.db, self.objects, library_id=other.library_id),
                         {Settled.REMOVED: 1})
        self.assertEqual(set(self.claims()), {mine.key.name})

    def test_an_invalid_claim_rotates_behind_valid_ones(self) -> None:
        # The table's whitespace check is narrower than Python's strip(): a
        # name only StorageKey refuses must not hold every bounded pass.
        self.sql("INSERT INTO prks.object_cleanup_claims (namespace, name) VALUES ('asset-objects', %s)",
                 ("\x1cstray.pdf",))
        orphan = self.put()
        self.claim(orphan.key)
        self.assertEqual(lifecycle.retry_claims(self.db, self.objects, limit=1), {Settled.FAILED: 1})
        self.assertEqual(lifecycle.retry_claims(self.db, self.objects, limit=1), {Settled.REMOVED: 1})
        self.assertFalse(self.stored(orphan.key.name))
        self.assertEqual(set(self.claims()), {"\x1cstray.pdf"})


class BackupLockTests(ObjectsTestCase):
    def test_settle_waits_out_a_backup_of_the_claims_library(self) -> None:
        obj, orphan = self.put(), self.put()
        self.claim(obj.key, self.actor.library_id)
        self.claim(orphan.key)
        with psycopg.connect(self.harness.conninfo(self._db_name()), autocommit=True) as backup:
            backup.execute("SELECT pg_advisory_lock_shared(" + lifecycle.OBJECTS_LOCK_KEY_SQL + ")",
                           (self.actor.library_id,))
            # Bounded passes rotate past the busy claim to the others.
            self.assertEqual(lifecycle.retry_claims(self.db, self.objects, limit=1), {Settled.BUSY: 1})
            self.assertTrue(self.stored(obj.key.name))
            self.assertIsNotNone(self.claims()[obj.key.name]["last_attempt_at"])
            self.assertEqual(lifecycle.retry_claims(self.db, self.objects, limit=1), {Settled.REMOVED: 1},
                             "an orphan no snapshot names")
            self.assertFalse(self.stored(orphan.key.name))
            backup.execute("SELECT pg_advisory_unlock_all()")
        self.assertIs(self.settle(obj.key), Settled.REMOVED)

    def _db_name(self) -> str:
        return self.sql("SELECT current_database() AS name")[0]["name"]


class InterleavingTests(ObjectsTestCase):
    """§13.3: a write paused after storing its key, while a claim on that key is settled."""

    def run_in_thread(self, fn: Callable[[], Any]) -> tuple[threading.Thread, list[Any]]:
        outcome: list[Any] = []

        def run() -> None:
            try:
                with facade_context.acting_as(self.actor.context):
                    outcome.append(fn())
            except BaseException as exc:  # noqa: BLE001 - reported to the test thread
                outcome.append(exc)

        thread = threading.Thread(target=run)
        thread.start()
        return thread, outcome

    def join(self, thread: threading.Thread) -> None:
        thread.join(30)
        self.assertFalse(thread.is_alive())

    def test_a_settle_meeting_an_adoption_in_flight_keeps_the_object(self) -> None:
        obj = self.put()
        self.sweep()  # the write stalled past the grace period: its key is claimed
        adopted, release = threading.Event(), threading.Event()
        new = commands.NewWork(work_id=generate("W"), manifestation_id=generate("MF"), title="Stalled",
                               asset_id=generate("AS"), asset=commands.NewAsset(source=commands.ManagedFile(
                                   storage_locator=obj.key.name, sha256=obj.sha256, byte_size=obj.size)))

        def create_and_pause(scope: LibraryScope) -> Optional[str]:
            created = commands.create_work(scope, new, objects=self.objects)
            adopted.set()
            release.wait(30)
            return created

        writer, written = self.run_in_thread(lambda: self.command(create_and_pause))
        self.assertTrue(adopted.wait(30))
        settler, settled = self.run_in_thread(lambda: self.settle(obj.key))
        self.wait_until(lambda: self.lock_waiters() == 1, "the settle to wait on the object lock")
        release.set()
        self.join(writer)
        self.join(settler)
        self.assertEqual(written, [new.work_id])
        self.assertEqual(settled, [Settled.REFERENCED])
        self.assertTrue(self.stored(obj.key.name))
        self.assertEqual(self.claims(), {})

    def test_an_adoption_meeting_a_settle_in_flight_fails_cleanly(self) -> None:
        obj = self.put()
        self.sweep()
        deciding, release = threading.Event(), threading.Event()

        def pause() -> None:
            deciding.set()
            release.wait(30)

        self.objects.before_delete = pause
        settler, settled = self.run_in_thread(lambda: self.settle(obj.key))
        self.assertTrue(deciding.wait(30))
        writer, written = self.run_in_thread(lambda: self.create(obj))
        self.wait_until(lambda: self.lock_waiters() == 1, "the adoption to wait on the object lock")
        release.set()
        self.join(settler)
        self.join(writer)
        self.assertEqual(settled, [Settled.REMOVED])
        self.assertEqual(len(written), 1)
        self.assertIsInstance(written[0], ObjectMissing)
        self.assertEqual((self.count("works"), self.count("assets")), (0, 0))

    def row_lock_waiters(self) -> int:
        return int(self.sql("SELECT count(*) AS n FROM pg_stat_activity WHERE wait_event_type = 'Lock' "
                            "AND pid <> pg_backend_pid()")[0]["n"])

    def test_a_rewrite_meeting_a_work_delete_in_flight_strands_nothing(self) -> None:
        old = self.put(b"old bytes")
        work_id = self.work_with(old)
        asset_id = self.asset_of(work_id)["id"]
        new = self.put(b"rewritten bytes")
        enumerated, release = threading.Event(), threading.Event()
        release_work = commands.folders.release_work

        def pause_after_enumeration(scope: LibraryScope, paused_work_id: str) -> Any:
            # delete_work has read its Assets' keys and not yet deleted them.
            enumerated.set()
            release.wait(30)
            return release_work(scope, paused_work_id)

        with mock.patch.object(commands.folders, "release_work", pause_after_enumeration):
            deleter, deleted = self.run_in_thread(
                lambda: self.command(lambda scope: commands.delete_work(scope, work_id)))
            self.assertTrue(enumerated.wait(30))
            writer, written = self.run_in_thread(lambda: self.command(
                lambda scope: lifecycle.replace_object(scope, self.objects, asset_id, new)))
            self.wait_until(lambda: self.row_lock_waiters() == 1, "the rewrite to wait on the Work")
            release.set()
            self.join(deleter)
            self.join(writer)
        self.assertEqual(len(deleted), 1)
        self.assertNotIsInstance(deleted[0], BaseException)
        self.assertEqual(written, [None], "the rewrite finds the Work gone and commits nothing")
        self.assertEqual(set(self.claims()), {old.key.name})
        # The new bytes were never adopted: an interrupted write, which the sweep claims.
        self.assertEqual(self.sweep(), 1)
        self.assertEqual(lifecycle.retry_claims(self.db, self.objects), {Settled.REMOVED: 2})

    def test_two_deletes_dropping_a_shared_keys_last_names_leave_a_claim(self) -> None:
        shared = self.put()
        first, second = self.work_with(shared), self.work_with(shared)
        deleted_first, release = threading.Event(), threading.Event()

        def delete_and_pause(scope: LibraryScope) -> Any:
            gone = commands.delete_work(scope, first)
            deleted_first.set()
            release.wait(30)
            return gone

        one, _ = self.run_in_thread(lambda: self.command(delete_and_pause))
        self.assertTrue(deleted_first.wait(30))
        other, _ = self.run_in_thread(
            lambda: self.command(lambda scope: commands.delete_work(scope, second)))
        self.wait_until(lambda: self.lock_waiters() == 1, "the second release to wait on the object lock")
        release.set()
        self.join(one)
        self.join(other)
        self.assertEqual(self.count("assets"), 0)
        self.assertEqual(set(self.claims()), {shared.key.name}, "the last release sees the first committed")
        self.assertEqual(lifecycle.retry_claims(self.db, self.objects), {Settled.REMOVED: 1})

    def test_two_works_adopting_one_key_both_commit(self) -> None:
        obj = self.put()
        results: list[Any] = []
        threads = [self.run_in_thread(lambda: self.create(obj)) for _ in range(2)]
        for thread, outcome in threads:
            self.join(thread)
            results.extend(outcome)
        self.assertEqual([type(result) for result in results], [str, str])
        self.assertEqual(self.count("assets"), 2)
