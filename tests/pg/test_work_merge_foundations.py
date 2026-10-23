"""Work merge foundations on PostgreSQL (L-W-2 #631).

``MERGE_WORKS`` is not available: these tests cover what it will be built
on. The lifecycle of a merged-away id and the ``WORK_MERGED`` refusal of the
sync gate; the merge plan (validation, locks, isolation and the rows it
reads); the relationship registry against the live foreign keys; the
ownership invariants a merge relies on; the peer-table writers' isolation;
and that nothing can invoke a merge yet.
"""

from __future__ import annotations

import contextlib
import os
import threading
import uuid
from typing import Any, Callable, Iterator, Optional, TypeVar
from unittest import mock

from backend import sync_protocol
from backend.db.database import Isolation
from backend.db.errors import DatabaseError
from backend.domain.identity import library_scope
from backend.domain.identity.authorization import AccessRefused, Capability, Refusal
from backend.domain.identity.library_scope import LibraryScope, library_command
from backend.domain.identity.request_context import RequestContext
from backend.domain.works import commands, lifecycle, merge
from backend.entity_ids import generate
from backend.pg_facade import PostgresLibrary
from backend.pg_facade import context as facade_context
from backend.sync import protocol
from tests.pg.test_works_sync import SyncTestCase, create_payload

T = TypeVar("T")
_PROJECT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def new_work_id() -> str:
    return "W-" + uuid.uuid4().hex.upper()


class MergeTestCase(SyncTestCase):
    def merged(self, source: str, target: str) -> None:
        """Record ``source`` as merged into ``target`` and delete it, as the merge will."""
        def run(scope: LibraryScope) -> None:
            lifecycle.mark_merged(scope, source, target)
            commands.delete_work(scope, source)

        self.command(run)

    def merged_as(self, ctx: RequestContext, source: str, target: str) -> None:
        def run(scope: LibraryScope) -> None:
            lifecycle.mark_merged(scope, source, target)
            commands.delete_work(scope, source)

        self.command(run, ctx)

    def command(self, fn: Callable[[LibraryScope], T], ctx: Optional[RequestContext] = None,
                isolation: Isolation = Isolation.SERIALIZABLE) -> T:
        return library_command(self.db, ctx or self.actor.context, Capability.EDIT_LIBRARY, fn,
                               isolation=isolation)

    def prepare(self, source: str, target: str, ctx: Optional[RequestContext] = None) -> merge.MergePlan:
        return self.command(lambda scope: merge.prepare(scope, source, target), ctx)

    def refused(self, source: str, target: str, ctx: Optional[RequestContext] = None) -> tuple[int, dict]:
        with self.assertRaises(merge.MergeRefused) as caught:
            self.prepare(source, target, ctx)
        return caught.exception.status, caught.exception.result

    def add_manifestation(self, work_id: str, title: Optional[str] = None) -> str:
        manifestation_id = "MF-" + uuid.uuid4().hex.upper()
        self.sql("INSERT INTO prks.manifestations (library_id, id, work_id, title) VALUES (%s, %s, %s, %s)",
                 (self.actor.library_id, manifestation_id, work_id, title))
        return manifestation_id

    @contextlib.contextmanager
    def refused_by(self, sqlstate: str) -> Iterator[None]:
        with self.assertRaises(DatabaseError) as caught:
            yield
        self.assertEqual(caught.exception.sqlstate, sqlstate)

    def snapshot(self) -> dict[str, list[dict[str, Any]]]:
        tables = ("works", "manifestations", "assets", "work_notes", "work_tags", "roles", "folder_files",
                  "playlist_items", "entity_revisions", "sync_work_lifecycle")
        return {table: self.sql(f"SELECT * FROM prks.{table} ORDER BY 1, 2, 3") for table in tables}


class LifecycleTests(MergeTestCase):
    def resolve(self, work_id: str) -> dict[str, Any]:
        return self.command(lambda scope: lifecycle.resolve(scope, work_id), isolation=Isolation.READ_COMMITTED)

    def test_live_unknown_and_merged_ids(self) -> None:
        source, target = self.library.add_work("Source"), self.library.add_work("Target")
        self.assertEqual(self.resolve(source), {"state": lifecycle.ACTIVE})
        self.assertEqual(self.resolve(new_work_id()), {"state": lifecycle.UNKNOWN})
        self.merged(source, target)
        self.assertEqual(self.resolve(source), {"state": lifecycle.MERGED, "target_work_id": target})
        self.assertEqual(self.resolve(target), {"state": lifecycle.ACTIVE})

    def test_a_merge_flattens_the_works_merged_into_its_source(self) -> None:
        first, second, third = (self.library.add_work(title) for title in ("A", "B", "C"))
        self.merged(first, second)
        self.merged(second, third)
        rows = self.sql("SELECT work_id, target_work_id FROM prks.sync_work_lifecycle ORDER BY work_id")
        self.assertEqual({row["work_id"]: row["target_work_id"] for row in rows}, {first: third, second: third})
        self.assertEqual(self.resolve(first), {"state": lifecycle.MERGED, "target_work_id": third})

    def test_an_unflattened_chain_resolves_to_its_end_and_a_cycle_fails(self) -> None:
        a, b, c = new_work_id(), new_work_id(), new_work_id()
        library = self.actor.library_id
        self.sql("INSERT INTO prks.sync_work_lifecycle (library_id, work_id, state, target_work_id)"
                 " VALUES (%s, %s, 'merged', %s), (%s, %s, 'merged', %s)", (library, a, b, library, b, c))
        self.assertEqual(self.resolve(a), {"state": lifecycle.MERGED, "target_work_id": c})
        self.sql("INSERT INTO prks.sync_work_lifecycle (library_id, work_id, state, target_work_id)"
                 " VALUES (%s, %s, 'merged', %s)", (library, c, a))
        with self.assertRaisesRegex(RuntimeError, "cycle"):
            self.resolve(a)

    def test_another_librarys_merged_id_is_unknown(self) -> None:
        other = self.other_actor()
        with facade_context.acting_as(other.context):
            source, target = self.library.add_work("Theirs"), self.library.add_work("Their target")
        self.merged_as(other.context, source, target)
        self.assertEqual(self.resolve(source), {"state": lifecycle.UNKNOWN})

    def test_a_work_is_merged_away_once_and_never_into_itself(self) -> None:
        source, target, other = (self.library.add_work(title) for title in ("S", "T", "O"))
        self.merged(source, target)
        with self.refused_by("23505"):
            self.command(lambda scope: lifecycle.mark_merged(scope, source, other))
        with self.assertRaises(ValueError):
            self.command(lambda scope: lifecycle.mark_merged(scope, target, target))


class SyncGateTests(MergeTestCase):
    def test_every_operation_naming_a_merged_work_is_refused_and_ledgered(self) -> None:
        source, target = self.library.add_work("Source"), self.library.add_work("Target")
        tag = self.library.add_tag("Tag")["id"]
        self.merged(source, target)
        before = self.snapshot()
        expected = (409, {"code": "WORK_MERGED", "work_id": source, "target_work_id": target})
        operations = [
            ("SET_WORK_METADATA_FIELD", {"field": "title", "value": "Stale"}, 0),
            ("SET_WORK_RESEARCH_NOTE", {"text": "stale note"}, 0),
            ("SET_WORK_PRIVATE_NOTE", {"text": "stale"}, 0),
            ("MARK_WORK_OPENED", {}, None),
            ("ADD_WORK_TAG", {"tag_id": tag}, 0),
            ("DELETE_WORK", {}, None),
            ("CREATE_WORK", create_payload(), None),  # the id is never reused
        ]
        for operation, payload, base in operations:
            with self.subTest(operation=operation):
                data = self.envelope(operation, source, payload, base)
                self.assertEqual(self.send(data), expected)
                self.assertEqual(self.send(data), expected)  # replayed from the ledger
        self.assertEqual(self.snapshot(), before)  # nothing reached the target
        ledger = self.ledger()
        self.assertEqual(len(ledger), len(operations))
        self.assertTrue(all(row["status"] == "WORK_MERGED" and row["http_status"] == 409 for row in ledger))

    def test_a_merged_works_operation_is_refused_even_while_its_dependency_is_pending(self) -> None:
        source, target, live = (self.library.add_work(t) for t in ("Source", "Target", "Live"))
        self.merged(source, target)
        pending = str(uuid.uuid4())  # a valid op_id the server has not acknowledged
        data = self.envelope("SET_WORK_METADATA_FIELD", source, {"field": "title", "value": "Stale"}, 0,
                             depends_on=[pending])
        expected = (409, {"code": "WORK_MERGED", "work_id": source, "target_work_id": target})
        self.assertEqual(self.send(data), expected)
        self.assertEqual(self.send(data), expected)  # replayed from the ledger
        (row,) = self.ledger()
        self.assertEqual((row["op_id"], row["status"], row["http_status"]), (data["op_id"], "WORK_MERGED", 409))
        # A live Work's operation still waits for its dependency, and is not ledgered.
        waiting = self.envelope("SET_WORK_METADATA_FIELD", live, {"field": "title", "value": "Later"}, 0,
                                depends_on=[pending])
        self.assertEqual(self.send(waiting), (400, {"code": "UNSATISFIED_DEPENDENCY"}))
        self.assertEqual(len(self.ledger()), 1)
        self.assertEqual(self.library.get_work(live)["title"], "Live")

    def test_playlist_reorder_with_a_merged_payload_work_is_terminal_and_ledgered(self) -> None:
        # REORDER_PLAYLIST_ITEMS has entity_type=playlist and a Playlist id
        # in the envelope; the Work ids only appear inside work_ids.
        source, target, live = (self.library.add_work(t) for t in ("Source", "Target", "Live"))
        self.merged(source, target)
        playlist_id = generate("PL")
        create = self.envelope("CREATE_PLAYLIST", playlist_id, {
            "title": "Reorder", "description": "", "original_url": "",
        }, entity_type="playlist")
        self.assertEqual(self.send(create)[1]["code"], "ACKNOWLEDGED")
        before = self.snapshot()
        pending = str(uuid.uuid4())
        expected = (409, {"code": "WORK_MERGED", "work_id": source, "target_work_id": target})
        # A duplicate payload Work is guarded once and the merged refusal
        # takes precedence over a pending dependency, before any reorder.
        stale = self.envelope("REORDER_PLAYLIST_ITEMS", playlist_id,
                              {"work_ids": [live, source, source]}, 0,
                              entity_type="playlist", depends_on=[pending])
        self.assertEqual(self.send(stale), expected)
        self.assertEqual(self.send(stale), expected)  # exact terminal replay
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.sql("SELECT * FROM prks.playlist_items WHERE playlist_id = %s",
                                  (playlist_id,)), [])
        ledger = self.ledger()
        matches = [row for row in ledger if row["op_id"] == stale["op_id"]]
        self.assertEqual(len(matches), 1)
        self.assertEqual((matches[0]["status"], matches[0]["http_status"]), ("WORK_MERGED", 409))
        # A normal Playlist operation does not acquire a terminal refusal
        # merely because its entity_type is Playlist.
        normal = self.envelope("REORDER_PLAYLIST_ITEMS", playlist_id,
                               {"work_ids": []}, 0, entity_type="playlist")
        status, result = self.send(normal)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))

    def test_a_live_work_is_held_against_a_merge_while_an_operation_applies(self) -> None:
        work_id = self.library.add_work("Live")
        held, release = threading.Event(), threading.Event()

        def hold(scope: LibraryScope) -> None:
            self.assertIsNone(lifecycle.guard(scope, work_id))
            held.set()
            release.wait(30)

        thread = threading.Thread(target=lambda: self.command(hold, isolation=Isolation.READ_COMMITTED))
        thread.start()
        try:
            self.assertTrue(held.wait(30))
            with self.refused_by("55P03"):
                self.sql("SELECT id FROM prks.works WHERE id = %s FOR UPDATE NOWAIT", (work_id,))
        finally:
            release.set()
            thread.join(30)
        self.assertEqual(len(self.sql("SELECT id FROM prks.works WHERE id = %s FOR UPDATE NOWAIT", (work_id,))), 1)

    def test_a_create_of_an_id_another_library_merged_away_is_refused(self) -> None:
        other = self.other_actor()
        with facade_context.acting_as(other.context):
            source, target = self.library.add_work("Theirs"), self.library.add_work("Their target")
        self.merged_as(other.context, source, target)
        with self.assertRaises(AccessRefused) as caught:
            self.send(self.envelope("CREATE_WORK", source, create_payload()))
        self.assertIs(caught.exception.refusal, Refusal.FORBIDDEN)
        self.assertEqual(self.ledger(), [])
        self.assertEqual(self.sql("SELECT id FROM prks.works WHERE id = %s", (source,)), [])
        # Any other operation reads it as an id that exists nowhere.
        self.assertEqual(self.send(self.envelope("MARK_WORK_OPENED", source, {}))[1]["code"],
                         self.send(self.envelope("MARK_WORK_OPENED", new_work_id(), {}))[1]["code"])

    def test_two_deletes_of_one_work_queue_at_the_gate_instead_of_deadlocking(self) -> None:
        # Both holding FOR KEY SHARE and upgrading to delete_work's FOR UPDATE
        # would deadlock (40P01), and a READ COMMITTED delete answers 503.
        work_id = self.library.add_work("Deleted twice")
        entered, release = threading.Event(), threading.Event()
        delete_work = commands.delete_work

        def first_waits(scope: LibraryScope, target: str) -> Any:
            if not entered.is_set():
                entered.set()
                release.wait(30)
            return delete_work(scope, target)

        results: dict[str, tuple[int, dict[str, Any]]] = {}

        def send(name: str) -> None:
            results[name] = self.send(self.envelope("DELETE_WORK", work_id, {}))

        with mock.patch.object(commands, "delete_work", first_waits):
            first = threading.Thread(target=send, args=("first",))
            first.start()
            self.assertTrue(entered.wait(30))
            second = threading.Thread(target=send, args=("second",))
            second.start()
            try:
                self.wait_for_lock_waiter()  # the second waits for the first's lock
            finally:
                release.set()
                first.join(30)
                second.join(30)
        self.assertEqual(results["first"][0], 200, results)
        self.assertNotEqual(results["second"][0], 503, results)
        self.assertEqual(self.sql("SELECT id FROM prks.works WHERE id = %s", (work_id,)), [])

    def test_only_the_delete_takes_the_work_for_update_at_the_gate(self) -> None:
        self.assertEqual(lifecycle.LOCKS_FOR_UPDATE, frozenset({"DELETE_WORK"}))
        work_id = self.library.add_work("Live")
        held, release = threading.Event(), threading.Event()

        def hold(scope: LibraryScope) -> None:
            self.assertIsNone(lifecycle.guard(scope, work_id, "DELETE_WORK"))
            held.set()
            release.wait(30)

        thread = threading.Thread(target=lambda: self.command(hold, isolation=Isolation.READ_COMMITTED))
        thread.start()
        try:
            self.assertTrue(held.wait(30))
            with self.refused_by("55P03"):
                self.sql("SELECT id FROM prks.works WHERE id = %s FOR KEY SHARE NOWAIT", (work_id,))
        finally:
            release.set()
            thread.join(30)


class PrepareRefusalTests(MergeTestCase):
    def test_a_self_merge_a_missing_work_and_another_librarys_work_are_refused(self) -> None:
        work_id = self.library.add_work("Work")
        self.assertEqual(self.refused(work_id, work_id),
                         (400, {"code": "MERGE_SAME_WORK", "work_id": work_id}))
        missing = new_work_id()
        self.assertEqual(self.refused(missing, work_id), (404, {"code": "ENTITY_NOT_FOUND", "work_id": missing}))
        self.assertEqual(self.refused(work_id, missing), (404, {"code": "ENTITY_NOT_FOUND", "work_id": missing}))
        with facade_context.acting_as(self.other_actor().context):
            theirs = self.library.add_work("Theirs")
        # Indistinguishable from an id that exists nowhere.
        self.assertEqual(self.refused(theirs, work_id), (404, {"code": "ENTITY_NOT_FOUND", "work_id": theirs}))
        self.assertEqual(self.refused(work_id, theirs), (404, {"code": "ENTITY_NOT_FOUND", "work_id": theirs}))

    def test_a_merged_source_or_target_is_refused_with_its_target(self) -> None:
        gone, survivor, other = (self.library.add_work(title) for title in ("Gone", "Survivor", "Other"))
        self.merged(gone, survivor)
        merged = (409, {"code": "WORK_MERGED", "work_id": gone, "target_work_id": survivor})
        self.assertEqual(self.refused(gone, other), merged)
        self.assertEqual(self.refused(other, gone), merged)

    def test_a_deleted_work_reads_as_missing(self) -> None:
        deleted, target = self.library.add_work("Deleted"), self.library.add_work("Target")
        self.library.delete_work_record(deleted, claim_pdf=False)
        self.assertEqual(self.refused(deleted, target), (404, {"code": "ENTITY_NOT_FOUND", "work_id": deleted}))

    def test_it_runs_only_serializable(self) -> None:
        source, target = self.library.add_work("S"), self.library.add_work("T")
        with self.assertRaisesRegex(RuntimeError, "SERIALIZABLE"):
            self.command(lambda scope: merge.prepare(scope, source, target), isolation=Isolation.READ_COMMITTED)

    def test_authorization_and_the_epoch_are_checked_first(self) -> None:
        source, target = self.library.add_work("S"), self.library.add_work("T")
        with self.assertRaises(AccessRefused) as caught:
            self.prepare(source, target, self.reader_context())
        self.assertIs(caught.exception.refusal, Refusal.FORBIDDEN)
        self.sql("UPDATE prks.libraries SET epoch = epoch + 1 WHERE id = %s", (self.actor.library_id,))
        with self.assertRaises(AccessRefused) as caught:
            self.prepare(source, target)
        self.assertIs(caught.exception.refusal, Refusal.LIBRARY_EPOCH_CHANGED)


class PlanTests(MergeTestCase):
    def primary(self, work_id: str) -> str:
        return str(self.sql("SELECT primary_manifestation_id AS m FROM prks.works WHERE id = %s", (work_id,))[0]["m"])

    def test_the_plan_reads_every_affected_row_and_writes_nothing(self) -> None:
        source = self.library.add_work("Source title", author_text="Source, A.")
        target = self.library.add_work("Target title", abstract="Target abstract", author_text="Target, B.")
        own_title = self.add_manifestation(source, title="Its own title")
        shared, only_source = self.library.add_tag("Shared")["id"], self.library.add_tag("Only")["id"]
        for work_id, tag in ((source, shared), (target, shared), (source, only_source)):
            self.library.add_tag_to_work(work_id, tag)
        same, moved, colliding = (self.library.add_person(name, "Person") for name in ("Same", "Moved", "Two"))
        self.library.add_role(same, source, "Author", credit_name="S. Person")
        self.library.add_role(same, target, "Author", credit_name="S. Person")
        self.library.add_role(moved, source, "Editor")
        self.library.add_role(colliding, source, "Author", credit_name="One spelling")
        self.library.add_role(colliding, target, "Author", credit_name="Another spelling")
        here, there = self.library.add_folder("Here"), self.library.add_folder("There")
        self.library.move_work_to_folder(source, here)
        self.library.move_work_to_folder(target, there)
        self.library.update_work_metadata(source, {"text_content": "Source research", "private_notes": "mine",
                                                   "status": "Completed"})
        self.library.update_work_metadata(target, {"text_content": "Target research"})
        self.library.mark_work_opened(target)
        before = self.snapshot()

        plan = self.prepare(source, target)

        self.assertEqual(self.snapshot(), before)
        self.assertEqual((plan.source_id, plan.target_id), (source, target))
        self.assertEqual(set(plan.manifestation_ids), {self.primary(source), own_title})
        # The inherited title is frozen; the empty inherited abstract cannot be.
        self.assertEqual(plan.frozen, ((self.primary(source), "title", "Source title"),))
        self.assertEqual(set(plan.shows_target), {(self.primary(source), "abstract"), (own_title, "abstract")})
        self.assertEqual((plan.tags_added, plan.tags_collapsed), ((only_source,), (shared,)))
        self.assertEqual(plan.roles_moved, ((moved, "Editor"),))
        self.assertEqual(plan.roles_collapsed, ((same, "Author"),))
        self.assertEqual(plan.role_collisions, (merge.RoleCollision(colliding, "Author", "One spelling",
                                                                    "Another spelling"),))
        self.assertEqual(len(plan.blocked), 1)
        self.assertIn("manifestation_credit_overrides", plan.blocked[0])
        self.assertEqual((plan.folder.source, plan.folder.target), (here, there))
        self.assertEqual(plan.notes["research"], merge.Side("Source research", "Target research"))
        self.assertEqual(plan.notes["private"], merge.Side("mine", ""))
        self.assertEqual(plan.status, merge.Side("Completed", "Not Started"))
        self.assertEqual(plan.author_text, merge.Side("Source, A.", "Target, B."))
        self.assertIsNotNone(plan.last_opened_at)
        self.assertEqual(plan.choices, ("folder", "note:research", "status", "author_text"))
        # Every fixed scope of the source, and the ones it advanced.
        self.assertTrue(set(commands.work_scopes(source)) <= set(plan.source_scopes))
        edited = {(row["scope_type"], row["scope_id"]) for row in self.sql(
            "SELECT scope_type, scope_id FROM prks.entity_revisions WHERE scope_id LIKE %s OR scope_id = %s",
            (f'["{source}"%', source))}
        self.assertTrue(edited and edited <= set(plan.source_scopes))
        self.assertFalse(any(target in scope_id for _, scope_id in plan.source_scopes))

    def test_a_plain_pair_has_nothing_to_choose_and_nothing_blocked(self) -> None:
        source, target = self.library.add_work("Same"), self.library.add_work("Same")
        plan = self.prepare(source, target)
        self.assertEqual((plan.frozen, plan.shows_target, plan.choices, plan.blocked), ((), (), (), ()))
        self.assertEqual(plan.manifestation_ids, (self.primary(source),))

    def test_a_free_text_author_on_one_side_is_kept_without_a_choice(self) -> None:
        for source_text, target_text in (("Only, S.", ""), ("", "Only, T.")):
            with self.subTest(source=source_text, target=target_text):
                source = self.library.add_work("S", author_text=source_text)
                target = self.library.add_work("T", author_text=target_text)
                plan = self.prepare(source, target)
                self.assertEqual(plan.author_text, merge.Side(source_text, target_text))
                self.assertNotIn("author_text", plan.choices)

    def test_the_plan_lists_every_declined_work_pair_naming_the_source(self) -> None:
        source, target, other, unrelated = (self.library.add_work(t) for t in ("S", "T", "O", "U"))
        manifestations = sorted((self.primary(source), self.primary(target)))
        rows = [("work", *sorted((source, target))), ("work", *sorted((source, other))),
                ("work", *sorted((other, unrelated))), ("manifestation", *manifestations)]
        for entity_type, low, high in rows:
            self.sql("INSERT INTO prks.duplicate_decisions (library_id, entity_type, low_id, high_id, decision)"
                     " VALUES (%s, %s, %s, %s, 'not_duplicate')", (self.actor.library_id, entity_type, low, high))
        plan = self.prepare(source, target)
        self.assertEqual(set(plan.duplicate_decisions),
                         {tuple(sorted((source, target))), tuple(sorted((source, other)))})
        self.assertEqual(self.prepare(unrelated, target).duplicate_decisions, (tuple(sorted((other, unrelated))),))

    def test_two_merges_of_one_pair_queue_on_the_works(self) -> None:
        source, target = self.library.add_work("S"), self.library.add_work("T")
        held, release = threading.Event(), threading.Event()

        def hold(scope: LibraryScope) -> None:
            merge.prepare(scope, source, target)
            held.set()
            release.wait(30)

        thread = threading.Thread(target=lambda: self.command(hold))
        thread.start()
        try:
            self.assertTrue(held.wait(30))
            for work_id in (source, target):
                with self.refused_by("55P03"):
                    self.sql("SELECT id FROM prks.works WHERE id = %s FOR KEY SHARE NOWAIT", (work_id,))
        finally:
            release.set()
            thread.join(30)

    def test_a_tag_added_after_the_snapshot_fails_the_merge_and_its_retry_sees_it(self) -> None:
        # §7.1: a row that joins the source after the merge read the set is
        # caught by SSI, because the writer is SERIALIZABLE (PEER_WRITERS).
        source, target = self.library.add_work("S"), self.library.add_work("T")
        tag = self.library.add_tag("Late")["id"]
        snapshot_taken, added = threading.Event(), threading.Event()
        plans: list[merge.MergePlan] = []

        def run(scope: LibraryScope) -> None:
            if not plans:
                snapshot_taken.set()
                added.wait(30)
            plan = merge.prepare(scope, source, target)
            plans.append(plan)
            # The merge's first write to the source (§10.4 step 2 releases its pointers).
            scope.execute("UPDATE prks.works SET updated_at = now() WHERE library_id = %(library_id)s"
                          " AND id = %(work_id)s", {"work_id": source})

        thread = threading.Thread(target=lambda: self.command(run))
        thread.start()
        try:
            self.assertTrue(snapshot_taken.wait(30))
            self.library.add_tag_to_work(source, tag)
        finally:
            added.set()
            thread.join(60)
        self.assertEqual(len(plans), 2, "the first attempt must fail with 40001 and be retried")
        self.assertEqual(plans[0].tags_added, ())
        self.assertEqual(plans[1].tags_added, (tag,))


class RegistryTests(MergeTestCase):
    _FOREIGN_KEYS = """
    SELECT c.conname, src.relname AS table_name, ref.relname AS referenced,
           c.confupdtype::text AS on_update, c.confdeltype::text AS on_delete,
           ARRAY(SELECT a.attname::text FROM unnest(c.conkey) AS k(n)
                 JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.n) AS columns
    FROM pg_constraint c
    JOIN pg_class src ON src.oid = c.conrelid
    JOIN pg_class ref ON ref.oid = c.confrelid
    JOIN pg_namespace n ON n.oid = ref.relnamespace
    WHERE c.contype = 'f' AND n.nspname = 'prks' AND ref.relname IN ('works', 'manifestations', 'assets')
    """

    def keys(self) -> dict[str, dict[str, Any]]:
        return {row["conname"]: row for row in self.sql(self._FOREIGN_KEYS)}

    def test_every_foreign_key_to_a_work_manifestation_or_asset_is_classified(self) -> None:
        live = {(row["table_name"], name, row["referenced"]) for name, row in self.keys().items()}
        registered = {(r.table, r.constraint, r.references) for r in merge.RELATIONSHIPS}
        self.assertEqual(live, registered,
                         "a table holding Work, Manifestation or Asset rows needs a merge disposition")

    def test_the_declared_cascades_are_the_schemas(self) -> None:
        keys = self.keys()
        for relationship in merge.RELATIONSHIPS:
            key = keys[relationship.constraint]
            with self.subTest(constraint=relationship.constraint):
                if relationship.on_source_delete is merge.OnSourceDelete.CASCADE:
                    self.assertEqual(key["on_delete"], "c")
                elif relationship.on_source_delete is merge.OnSourceDelete.REFUSED:
                    self.assertIn(key["on_delete"], ("a", "r"))
                if relationship.disposition is merge.Disposition.FOLLOWS and "work_id" in key["columns"]:
                    self.assertEqual(key["on_update"], "c")  # the Work moves with the parent

    def test_deferred_tables_do_not_exist_yet(self) -> None:
        # When a lane adds one, this fails until the merge says what it does with it.
        for deferred in merge.DEFERRED:
            with self.subTest(table=deferred.table):
                found = self.sql("SELECT to_regclass(%s) AS oid", ("prks." + deferred.table,))[0]["oid"]
                self.assertIsNone(found)

    def test_unkeyed_tables_exist_and_name_works_without_a_foreign_key(self) -> None:
        # The foreign-key test cannot see these, so they are listed by hand.
        for unkeyed in merge.UNKEYED:
            with self.subTest(table=unkeyed.table):
                self.assertIsNotNone(self.sql("SELECT to_regclass(%s) AS oid",
                                              ("prks." + unkeyed.table,))[0]["oid"])
                self.assertEqual(self.sql(
                    "SELECT count(*) AS n FROM pg_constraint c JOIN pg_class src ON src.oid = c.conrelid"
                    " JOIN pg_class ref ON ref.oid = c.confrelid WHERE c.contype = 'f' AND src.relname = %s"
                    " AND ref.relname IN ('works', 'manifestations', 'assets')", (unkeyed.table,))[0]["n"], 0)

    def test_the_peer_tables_are_the_architectures(self) -> None:
        # docs/postgresql-architecture.md §7.1, less the tables still deferred.
        self.assertEqual({r.table for r in merge.RELATIONSHIPS if r.peer},
                         {"manifestations", "work_notes", "work_tags", "folder_files", "playlist_items", "roles"})


class IsolationTests(MergeTestCase):
    def test_every_peer_table_writer_runs_serializable(self) -> None:
        for operation in sorted(merge.PEER_WRITERS):
            with self.subTest(operation=operation):
                self.assertIs(protocol._HANDLERS[operation].isolation, Isolation.SERIALIZABLE)

    def test_a_note_written_through_the_facade_runs_serializable(self) -> None:
        work_id = self.library.add_work("Noted")
        seen: list[Isolation] = []
        real = library_scope.library_command

        def traced(*args: Any, **kwargs: Any) -> Any:
            seen.append(kwargs.get("isolation", Isolation.READ_COMMITTED))
            return real(*args, **kwargs)

        with mock.patch.object(library_scope, "library_command", traced):
            self.library.update_work_metadata(work_id, {"text_content": "A note"})
            self.library.update_work_metadata(work_id, {"title": "Renamed"})
        self.assertEqual(seen, [Isolation.SERIALIZABLE, Isolation.READ_COMMITTED])


class OwnershipTests(MergeTestCase):
    """The database facts a merge's writes rely on (§4.1): no partial state commits."""

    def test_a_work_cannot_lose_its_primary_manifestation(self) -> None:
        source, target = self.library.add_work("S"), self.library.add_work("T")

        def move_primary(scope: LibraryScope) -> None:
            scope.execute("UPDATE prks.manifestations SET work_id = %(target)s WHERE library_id = %(library_id)s"
                          " AND work_id = %(source)s", {"source": source, "target": target})

        with self.refused_by("23503"):
            self.command(move_primary)  # checked at COMMIT: the source's pointer names a moved row
        self.assertEqual(self.library.get_work(source)["id"], source)

    def test_a_work_with_manifestations_cannot_be_deleted(self) -> None:
        work_id = self.library.add_work("W")
        with self.refused_by("23503"):
            self.command(lambda scope: scope.execute(
                "DELETE FROM prks.works WHERE library_id = %(library_id)s AND id = %(work_id)s",
                {"work_id": work_id}))
        self.assertIsNotNone(self.library.get_work(work_id))

    def test_a_manifestation_cannot_move_to_another_librarys_work(self) -> None:
        work_id = self.library.add_work("Mine")
        with facade_context.acting_as(self.other_actor().context):
            theirs = self.library.add_work("Theirs")
        extra = self.add_manifestation(work_id)
        with self.refused_by("23503"):
            self.command(lambda scope: scope.execute(
                "UPDATE prks.manifestations SET work_id = %(theirs)s"
                " WHERE library_id = %(library_id)s AND id = %(extra)s",
                {"theirs": theirs, "extra": extra}))

    def test_a_moved_manifestation_carries_its_assets_and_scoped_roles(self) -> None:
        source = self.library.add_work("S")
        target = self.library.add_work("T")
        extra = self.add_manifestation(source)
        asset = "AS-" + uuid.uuid4().hex.upper()
        self.sql("INSERT INTO prks.assets (library_id, id, manifestation_id, work_id, kind, provider, provider_id,"
                 " url) VALUES (%s, %s, %s, %s, 'external_stream', 'youtube', 'abcdefghijk',"
                 " 'https://www.youtube.com/watch?v=abcdefghijk')", (self.actor.library_id, asset, extra, source))
        person = self.library.add_person("A", "Person")
        self.sql("INSERT INTO prks.roles (library_id, work_id, person_id, role_type, order_index, manifestation_id)"
                 " VALUES (%s, %s, %s, 'Translator', 0, %s)", (self.actor.library_id, source, person, extra))
        self.command(lambda scope: scope.execute(
            "UPDATE prks.manifestations SET work_id = %(target)s"
            " WHERE library_id = %(library_id)s AND id = %(extra)s",
            {"target": target, "extra": extra}))
        self.assertEqual(self.sql("SELECT work_id FROM prks.assets WHERE id = %s", (asset,))[0]["work_id"], target)
        self.assertEqual(self.sql("SELECT work_id FROM prks.roles WHERE manifestation_id = %s", (extra,))[0]["work_id"],
                         target)

    def test_a_failure_after_the_first_merge_write_leaves_both_works(self) -> None:
        source, target = self.library.add_work("S"), self.library.add_work("T")
        before = self.snapshot()

        def partial(scope: LibraryScope) -> None:
            merge.prepare(scope, source, target)
            lifecycle.mark_merged(scope, source, target)
            scope.execute("UPDATE prks.works SET primary_manifestation_id = primary_manifestation_id"
                          " WHERE library_id = %(library_id)s AND id = %(work_id)s", {"work_id": source})
            raise RuntimeError("injected")

        with self.assertRaisesRegex(RuntimeError, "injected"):
            self.command(partial)
        self.assertEqual(self.snapshot(), before)


class NotAvailableTests(MergeTestCase):
    def test_nothing_can_invoke_a_merge_yet(self) -> None:
        self.assertNotIn("MERGE_WORKS", sync_protocol.supported_operations())
        self.assertNotIn("MERGE_WORKS", protocol.supported_operations())
        self.assertFalse(hasattr(PostgresLibrary, "merge_works"))
        callers = []
        for root in ("backend", "server.py", "prks_app.py"):
            path = os.path.join(_PROJECT, root)
            files = [path] if os.path.isfile(path) else [
                os.path.join(folder, name) for folder, _, names in os.walk(path) for name in names
                if name.endswith(".py")]
            for file in files:
                if file.endswith(os.path.join("works", "merge.py")):
                    continue
                with open(file, encoding="utf-8") as handle:
                    text = handle.read()
                if "merge.prepare" in text or "works import merge" in text or "MERGE_WORKS" in text:
                    callers.append(os.path.relpath(file, _PROJECT))
        self.assertEqual(callers, [])
