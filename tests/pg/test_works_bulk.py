"""Bulk Work actions on PostgreSQL: what parity cannot show (L-W-2 #631).

``tests/test_works_bulk_parity.py`` proves the facade answers as SQLite
does. These tests cover what only PostgreSQL has: the revisions each action
leaves behind and the stale operations they refuse, atomic rollback when a
later Work fails, Library isolation and roles, and concurrent writers, each
race holding one transaction open until the other is waiting on it (§7.1).
"""

from __future__ import annotations

import random
import uuid
from typing import Any, Optional
from unittest import mock

from backend.db_manager import PRKS_BULK_WORK_MAX, BulkWorkError
from backend.domain.identity.authorization import AccessRefused, Refusal
from backend.domain.identity.request_context import RequestContext
from backend.domain.organization import folders, links, tags
from backend.domain.works import bulk, commands, metadata
from backend.entity_ids import generate
from backend.pg_facade import context as facade_context
from backend.sync import protocol
from backend.sync.families import Environment
from tests.pg.test_folders import FolderTestCase


class BulkTestCase(FolderTestCase):
    def bulk(self, action: str, work_ids: list[str], **body: Any) -> dict[str, Any]:
        return self.library.bulk_update_works({"action": action, "work_ids": work_ids, **body})

    def refused_bulk(self, http_status: int, message: str, action: str, work_ids: Any, **body: Any) -> None:
        with self.assertRaises(BulkWorkError) as caught:
            self.library.bulk_update_works({"action": action, "work_ids": work_ids, **body})
        self.assertEqual((caught.exception.http_status, str(caught.exception)), (http_status, message))

    def status_of(self, work_id: str) -> tuple[str, int]:
        state = self.library.get_work_metadata_state(work_id)
        assert state is not None
        return state["fields"]["status"]["value"], state["fields"]["status"]["revision"]

    def tag_ids(self, work_id: str) -> list[str]:
        return sorted(row["id"] for row in self.library.get_work_tags(work_id))

    def snapshot(self) -> list[Any]:
        """Everything a bulk action may change: revisions, links, filings, Statuses, Work times."""
        return [self.sql("SELECT scope_type, scope_id, revision FROM prks.entity_revisions"
                         " ORDER BY scope_type, scope_id"),
                self.sql("SELECT work_id, tag_id FROM prks.work_tags ORDER BY work_id, tag_id"),
                self.filed(),
                self.sql("SELECT id, status, updated_at FROM prks.works ORDER BY id")]

    def member_context(self, role: str) -> RequestContext:
        """A signed-in member of this test's Library with ``role``."""
        from backend.domain.identity import sessions
        from tests.pg.facade_context import provision_actor

        member = provision_actor(self.db, role=role)
        self.sql("INSERT INTO prks.library_memberships (library_id, user_id, role) VALUES (%s, %s, %s)",
                 (self.actor.library_id, member.user_id, role))
        new = self.db.command(lambda tx: sessions.create_session(
            tx, user_id=member.user_id, library_id=self.actor.library_id))
        valid = sessions.resolve_session(self.db, new.token.reveal())
        assert valid is not None
        return RequestContext.for_session(valid, epoch=1, device_id=member.context.device_id)

    def send(self, operation: str, entity_id: str, payload: dict[str, Any],
             base: Optional[int]) -> tuple[int, dict[str, Any]]:
        device = self.actor.context.device_id
        assert device is not None
        data = {"op_id": str(uuid.uuid4()), "device_id": str(device), "operation": operation,
                "entity_type": "work", "entity_id": entity_id, "payload": payload, "base_revision": base,
                "occurred_at": "2026-10-22T10:00:00Z", "created_at": "2026-10-22T10:00:00Z",
                "depends_on": []}
        return protocol.process_operation(self.db, self.actor.context, data,
                                          Environment(pdfs_dir=self.storage.pdfs_dir))

    def held_bulk(self, action: str, work_ids: list[str], **body: Any) -> Any:
        """A race holder running the bulk action in the race's open transaction."""
        return lambda scope: bulk.apply(scope, action, work_ids, **body)


class ActionTests(BulkTestCase):
    def test_set_status_advances_only_the_works_that_change(self) -> None:
        a = self.library.add_work("A", status="Paused")
        b = self.library.add_work("B", status="Paused")
        done = self.library.add_work("Done", status="Completed")
        other = self.library.add_work("Other", status="Paused")
        before = self.sql("SELECT id, updated_at FROM prks.works WHERE id IN (%s, %s)", (done, other))
        result = self.bulk("set_status", [b, a, done, a], status="Completed")
        self.assertEqual(result, {"status": "updated", "action": "set_status", "requested": 3, "updated": 3,
                                  "target_count": 1})
        self.assertEqual([self.status_of(w) for w in (a, b, done, other)],
                         [("Completed", 1), ("Completed", 1), ("Completed", 0), ("Paused", 0)])
        # A Work already there and an unselected one are not touched, and leave no scope row.
        self.assertEqual(self.sql("SELECT id, updated_at FROM prks.works WHERE id IN (%s, %s)", (done, other)),
                         before)
        # The status writer locks its scope before deciding (a single edit does too), so a
        # Work already at the value holds a revision-0 row; nothing else is touched.
        self.assertEqual(self.revisions("work-field"), {metadata.scope_id(a, "status"): 1,
                                                        metadata.scope_id(b, "status"): 1,
                                                        metadata.scope_id(done, "status"): 0})
        # Again: a no-op everywhere.
        self.bulk("set_status", [a, b, done], status="Completed")
        self.assertEqual([self.status_of(w)[1] for w in (a, b, done)], [1, 1, 0])

    def test_move_folder_files_moves_and_clears_through_the_work_folder_scope(self) -> None:
        x, y = self.folder("X"), self.folder("Y")
        a, b, c = (self.library.add_work(t) for t in "ABC")
        for work_id in (a, b, c):
            self.library.move_work_to_folder(work_id, x)
        self.library.move_work_to_folder(b, y)
        result = self.bulk("move_folder", [a, b], folder_id=f"  {y}  ")
        self.assertEqual((result["updated"], result["target_count"]), (2, 1))
        self.assertEqual(self.filed(), sorted([(a, y), (b, y), (c, x)]))
        self.assertEqual([self.work_folder(w) for w in (a, b, c)], [(y, 2), (y, 2), (x, 1)])
        self.bulk("move_folder", [a, b, c], folder_id=None)
        self.assertEqual(self.filed(), [])
        self.assertEqual([self.work_folder(w) for w in (a, b, c)], [("", 3), ("", 3), ("", 2)])
        self.bulk("move_folder", [a, b], folder_id=None)  # nothing to clear
        self.assertEqual([self.work_folder(w)[1] for w in (a, b)], [3, 3])

    def test_tag_actions_are_idempotent_and_advance_only_changed_links(self) -> None:
        a, b, c = (self.library.add_work(t) for t in "ABC")
        t1, t2 = self.tag("T1"), self.tag("T2")
        self.library.add_tag_to_work(a, t1)
        self.library.add_tag_to_work(c, t1)
        result = self.bulk("add_tags", [a, b], tag_ids=[t2, t1, t2])
        self.assertEqual((result["requested"], result["target_count"]), (2, 2))
        self.assertEqual([self.tag_ids(w) for w in (a, b, c)], [sorted([t1, t2]), sorted([t1, t2]), [t1]])
        self.assertEqual(self.revisions("work-tag"), {
            links.WORK.scope_id(a, t1): 1, links.WORK.scope_id(a, t2): 1,
            links.WORK.scope_id(b, t1): 1, links.WORK.scope_id(b, t2): 1, links.WORK.scope_id(c, t1): 1})
        self.bulk("add_tags", [a, b], tag_ids=[t1, t2])  # all present: nothing moves
        self.bulk("remove_tags", [a, c], tag_ids=[t2])  # c never had t2: no scope row for it
        self.assertEqual([self.tag_ids(w) for w in (a, b, c)], [[t1], sorted([t1, t2]), [t1]])
        revisions = self.revisions("work-tag")
        self.assertEqual((revisions[links.WORK.scope_id(a, t2)], revisions[links.WORK.scope_id(b, t2)]), (2, 1))
        self.assertNotIn(links.WORK.scope_id(c, t2), revisions)
        self.assertEqual(self.library.get_work_tag_options(a)["known_absent"], {t2: 2})


class RefusalTests(BulkTestCase):
    def test_an_invalid_body_is_refused_before_the_database_is_reached(self) -> None:
        work = self.library.add_work("W", status="Planned")
        cases: list[tuple[Any, str]] = [
            ([], "JSON object body required"),
            ({"action": "delete_everything", "work_ids": [work]}, "Unknown bulk action."),
            ({"action": "set_status", "work_ids": work, "status": "Completed"}, "work_ids must be a JSON array."),
            ({"action": "set_status", "work_ids": [], "status": "Completed"}, "No files selected."),
            ({"action": "set_status", "work_ids": ["  "], "status": "Completed"}, "work IDs must be non-empty."),
            ({"action": "set_status", "work_ids": [1], "status": "Completed"}, "work IDs must be strings."),
            ({"action": "set_status", "work_ids": [f"W-{i}" for i in range(PRKS_BULK_WORK_MAX + 1)],
              "status": "Completed"}, "Too many files selected."),
            ({"action": "set_status", "work_ids": [work], "status": "Finished"}, "Invalid status."),
            ({"action": "move_folder", "work_ids": [work]}, "folder_id is required."),
            ({"action": "move_folder", "work_ids": [work], "folder_id": "  "}, "folder_id is invalid."),
            ({"action": "move_folder", "work_ids": [work], "folder_id": 7}, "folder_id must be a string or null."),
            ({"action": "add_tags", "work_ids": [work], "tag_ids": []}, "No tags selected."),
            ({"action": "remove_tags", "work_ids": [work], "tag_ids": [f"T-{i}" for i in range(501)]},
             "Too many tags selected."),
        ]
        before = self.snapshot()
        with mock.patch.object(bulk, "apply", side_effect=AssertionError("reached the database")):
            for body, message in cases:
                with self.subTest(message=message), self.assertRaises(BulkWorkError) as caught:
                    self.library.bulk_update_works(body)
                self.assertEqual((caught.exception.http_status, str(caught.exception)), (400, message))
        self.assertEqual(self.snapshot(), before)

    def test_a_missing_work_folder_or_tag_refuses_with_404_and_writes_nothing(self) -> None:
        a, b = self.library.add_work("A", status="Paused"), self.library.add_work("B", status="Paused")
        folder, tag = self.folder("F"), self.tag("T")
        gone_work = self.library.add_work("Gone")
        self.library.delete_work_record(gone_work)
        gone_tag, merged = self.tag("Deleted"), self.tag("Merged")
        self.library.delete_tag(gone_tag)
        self.library.merge_tags_into(merged, tag)
        gone_folder = self.folder("Gone")
        self.library.delete_empty_folder(gone_folder)
        before = self.snapshot()
        works = "One or more selected files no longer exist."
        for missing in (generate("W"), gone_work):
            for action, body in (("set_status", {"status": "Completed"}), ("move_folder", {"folder_id": folder}),
                                 ("add_tags", {"tag_ids": [tag]}), ("remove_tags", {"tag_ids": [tag]})):
                with self.subTest(action=action):
                    self.refused_bulk(404, works, action, [a, missing, b], **body)
        # The Works are checked first, as on SQLite.
        self.refused_bulk(404, works, "move_folder", [generate("W")], folder_id=generate("F"))
        for missing in (generate("F"), gone_folder):
            self.refused_bulk(404, "Folder not found.", "move_folder", [a, b], folder_id=missing)
        for missing in (generate("T"), gone_tag, merged):
            for action in ("add_tags", "remove_tags"):
                with self.subTest(action=action, tag=missing):
                    self.refused_bulk(404, "One or more selected tags no longer exist.", action, [a, b],
                                      tag_ids=[tag, missing])
        self.assertEqual(self.snapshot(), before)

    def test_an_id_holding_nul_is_missing_and_never_reaches_a_query(self) -> None:
        a = self.library.add_work("A", status="Paused")
        folder, tag = self.folder("F"), self.tag("T")
        before = self.snapshot()
        works = "One or more selected files no longer exist."
        for action, body in (("set_status", {"status": "Completed"}), ("move_folder", {"folder_id": folder}),
                             ("add_tags", {"tag_ids": [tag]}), ("remove_tags", {"tag_ids": [tag]})):
            with self.subTest(action=action):
                self.refused_bulk(404, works, action, [a, a + "\x00"], **body)
                self.refused_bulk(404, works, action, ["W-\x00"], **body)
        self.refused_bulk(404, "Folder not found.", "move_folder", [a], folder_id=folder + "\x00")
        self.refused_bulk(404, "Folder not found.", "move_folder", [a], folder_id="\x00")
        for action in ("add_tags", "remove_tags"):
            with self.subTest(action=action):
                self.refused_bulk(404, "One or more selected tags no longer exist.", action, [a],
                                  tag_ids=[tag, "T-\x00"])
        self.assertEqual(self.snapshot(), before)

    def test_a_failure_on_a_later_work_rolls_back_every_earlier_one(self) -> None:
        works = sorted(self.library.add_work(t, status="Paused") for t in "ABC")
        x, y = self.folder("X"), self.folder("Y")
        tag = self.tag("T")
        for work_id in works:
            self.library.move_work_to_folder(work_id, x)
        before = self.snapshot()
        writers = {"set_status": (metadata, "set_synced_field", {"status": "Completed"}),
                   "move_folder": (folders, "set_work_folder", {"folder_id": y}),
                   "add_tags": (links, "set_state", {"tag_ids": [tag]})}
        for action, (module, name, body) in writers.items():
            real = getattr(module, name)
            calls: list[int] = []

            def failing(*args: Any, real: Any = real, calls: list[int] = calls) -> Any:
                calls.append(1)
                if len(calls) == 3:
                    raise RuntimeError("injected")
                return real(*args)

            with self.subTest(action=action), mock.patch.object(module, name, side_effect=failing):
                with self.assertRaises(RuntimeError):
                    self.bulk(action, works, **body)
                self.assertEqual(len(calls), 3)  # two Works were changed before the failure
            self.assertEqual(self.snapshot(), before)


class IsolationAndRoleTests(BulkTestCase):
    def test_another_librarys_works_folders_and_tags_are_missing(self) -> None:
        mine = self.library.add_work("Mine", status="Paused")
        my_tag = self.tag("Mine")
        other = self.other_actor()
        with facade_context.acting_as(other.context):
            theirs = self.library.add_work("Theirs", status="Paused")
            their_folder = self.library.add_folder("Theirs")
            their_tag = self.library.add_tag("Theirs")["id"]
        before = self.snapshot()
        self.refused_bulk(404, "One or more selected files no longer exist.", "set_status", [mine, theirs],
                          status="Completed")
        self.refused_bulk(404, "Folder not found.", "move_folder", [mine], folder_id=their_folder)
        self.refused_bulk(404, "One or more selected tags no longer exist.", "add_tags", [mine],
                          tag_ids=[my_tag, their_tag])
        self.assertEqual(self.snapshot(), before)
        # Their own action changes their Works only.
        with facade_context.acting_as(other.context):
            self.library.bulk_update_works({"action": "set_status", "work_ids": [theirs], "status": "Completed"})
            self.assertEqual(self.status_of(theirs), ("Completed", 1))
            self.refused_bulk(404, "One or more selected files no longer exist.", "add_tags", [mine],
                              tag_ids=[their_tag])
        self.assertEqual(self.status_of(mine), ("Paused", 0))

    def test_a_reader_is_refused_and_an_editor_may_act(self) -> None:
        work, tag = self.library.add_work("W", status="Paused"), self.tag("T")
        before = self.snapshot()
        with facade_context.acting_as(self.member_context("reader")):
            for action, body in (("set_status", {"status": "Completed"}), ("move_folder", {"folder_id": None}),
                                 ("add_tags", {"tag_ids": [tag]}), ("remove_tags", {"tag_ids": [tag]})):
                with self.subTest(action=action), self.assertRaises(AccessRefused) as caught:
                    self.bulk(action, [work], **body)
                self.assertIs(caught.exception.refusal, Refusal.FORBIDDEN)
        self.assertEqual(self.snapshot(), before)
        with facade_context.acting_as(self.member_context("editor")):
            self.bulk("add_tags", [work], tag_ids=[tag])
            self.bulk("set_status", [work], status="Completed")
        self.assertEqual((self.tag_ids(work), self.status_of(work)), ([tag], ("Completed", 1)))


class StaleOperationTests(BulkTestCase):
    def test_operations_from_before_a_bulk_action_conflict_and_converge_like_single_edits(self) -> None:
        work = self.library.add_work("W", status="Paused")
        x, y = self.folder("X"), self.folder("Y")
        tag = self.tag("T")
        self.library.move_work_to_folder(work, x)
        self.bulk("set_status", [work], status="Completed")
        self.bulk("move_folder", [work], folder_id=y)
        self.bulk("add_tags", [work], tag_ids=[tag])
        status = {"field": "status", "value": "Planned"}
        self.assertEqual(self.send("SET_WORK_METADATA_FIELD", work, status, 0)[1]["code"], "REVISION_CONFLICT")
        self.assertEqual(self.send("SET_WORK_FOLDER", work, {"folder_id": x}, 1)[1]["code"],
                         "REVISION_CONFLICT")
        self.assertEqual(self.send("REMOVE_WORK_TAG", work, {"tag_id": tag}, 0)[1]["code"], "REVISION_CONFLICT")
        # A stale base that agrees with the bulk result has converged.
        for operation, payload, base in (("SET_WORK_METADATA_FIELD", {"field": "status", "value": "Completed"}, 0),
                                         ("SET_WORK_FOLDER", {"folder_id": y}, 1),
                                         ("ADD_WORK_TAG", {"tag_id": tag}, 0)):
            with self.subTest(operation=operation):
                code, result = self.send(operation, work, payload, base)
                self.assertEqual((code, result["code"], result["changed"]), (200, "ACKNOWLEDGED", False))
        # The current base applies.
        self.assertEqual(self.send("SET_WORK_METADATA_FIELD", work, status, 1)[1]["server_revision"], 2)
        self.assertEqual(self.send("SET_WORK_FOLDER", work, {"folder_id": x}, 2)[1]["server_revision"], 3)
        self.assertEqual(self.send("REMOVE_WORK_TAG", work, {"tag_id": tag}, 1)[1]["server_revision"], 2)


class ConcurrencyTests(BulkTestCase):
    def test_overlapping_bulk_actions_in_any_order_serialize_without_deadlock(self) -> None:
        works = [self.library.add_work(f"W{i}", status="Paused") for i in range(12)]
        tag_ids = [self.tag(f"T{i}") for i in range(4)]
        rng = random.Random(631)
        calls = []
        for index, tag_id in enumerate(tag_ids):
            chosen = works[index * 2:index * 2 + 6]
            rng.shuffle(chosen)
            calls.append(lambda chosen=chosen, tag_id=tag_id: self.bulk("add_tags", chosen, tag_ids=[tag_id]))
        statuses = ("Completed", "Planned")
        for status in statuses:
            chosen = list(works)
            rng.shuffle(chosen)
            calls.append(lambda chosen=chosen, status=status: self.bulk("set_status", chosen, status=status))
        results = self.concurrently(calls)
        self.assertTrue(all(result["status"] == "updated" for result in results))
        for index, tag_id in enumerate(tag_ids):
            linked = sorted(row["owner_id"] for row in
                            self.sql("SELECT work_id AS owner_id FROM prks.work_tags WHERE tag_id = %s", (tag_id,)))
            self.assertEqual(linked, sorted(works[index * 2:index * 2 + 6]))
        self.assertEqual(set(self.revisions("work-tag").values()), {1})
        # Serializable: one bulk Status action came last for every Work, and each changed every Work once.
        final = {self.status_of(work_id) for work_id in works}
        self.assertEqual(len(final), 1, final)
        self.assertIn(final.pop(), {(status, 2) for status in statuses})

    def concurrently(self, calls: list[Any]) -> list[Any]:
        import threading

        barrier = threading.Barrier(len(calls), timeout=30)
        results: list[Any] = [None] * len(calls)
        ctx = self.actor.context

        def run(index: int) -> None:
            with facade_context.acting_as(ctx):
                barrier.wait()
                try:
                    results[index] = calls[index]()
                except BaseException as exc:  # noqa: BLE001 - re-raised below
                    results[index] = exc

        threads = [threading.Thread(target=run, args=(i,)) for i in range(len(calls))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        for result in results:
            if isinstance(result, BaseException):
                raise result
        return results

    def test_a_bulk_action_waiting_on_an_edit_reruns_against_it(self) -> None:
        a, b = self.library.add_work("A", status="Paused"), self.library.add_work("B", status="Paused")

        def edit(scope: Any) -> Any:
            return metadata.apply_field_operation(scope, a, "status", "Planned", 0)

        holder, contender = self.race(edit, lambda: self.bulk("set_status", [a, b], status="Completed"))
        self.assertEqual(holder[0], 200)
        self.assertEqual(contender["updated"], 2)
        self.assertEqual([self.status_of(w) for w in (a, b)], [("Completed", 2), ("Completed", 1)])

    def test_an_edit_waiting_on_a_bulk_action_decides_after_it(self) -> None:
        a = self.library.add_work("A", status="Paused")
        holder, contender = self.race(self.held_bulk("set_status", [a], status="Completed"),
                                      lambda: self.send("SET_WORK_METADATA_FIELD", a,
                                                        {"field": "status", "value": "Planned"}, 0),
                                      serializable=True)
        self.assertIsNone(holder)
        self.assertEqual((contender[0], contender[1]["code"]), (409, "REVISION_CONFLICT"))
        holder, contender = self.race(self.held_bulk("set_status", [a], status="Planned"),
                                      lambda: self.library.update_work_metadata(a, {"status": "In Progress"}),
                                      serializable=True)
        self.assertEqual(self.status_of(a), ("In Progress", 3))

    def test_a_bulk_action_waiting_on_a_delete_of_a_selected_work_writes_nothing(self) -> None:
        a, b = self.library.add_work("A"), self.library.add_work("B")
        tag = self.tag("T")
        holder, contender = self.race(lambda scope: commands.delete_work(scope, a) is not None,
                                      lambda: self.bulk("add_tags", [b, a], tag_ids=[tag]))
        self.assertIs(holder, True)
        self.assertIsInstance(contender, BulkWorkError)
        self.assertEqual((contender.http_status, str(contender)),
                         (404, "One or more selected files no longer exist."))
        self.assertEqual((self.tag_ids(b), self.revisions("work-tag")), ([], {}))

    def test_a_delete_waiting_on_a_bulk_action_removes_what_it_wrote(self) -> None:
        a = self.library.add_work("A")
        tag = self.tag("T")
        holder, contender = self.race(self.held_bulk("add_tags", [a], tag_ids=[tag]),
                                      lambda: self.library.delete_work_record(a) is not None,
                                      serializable=True)
        self.assertEqual((holder, contender), (None, True))
        self.assertEqual((self.count("work_tags"), self.revisions("work-tag")),
                         (0, {links.WORK.scope_id(a, tag): 2}))

    def test_a_bulk_tag_action_waiting_on_a_tag_delete_or_merge_writes_nothing(self) -> None:
        a = self.library.add_work("A")
        keep = self.tag("Keep")
        for kind in ("delete", "merge"):
            with self.subTest(kind=kind):
                doomed = self.tag("Doomed " + kind)

                def change(scope: Any, doomed: str = doomed, kind: str = kind) -> Any:
                    if kind == "delete":
                        return tags.delete_tag(scope, doomed)
                    return tags.merge(scope, doomed, keep, generate("L"))

                holder, contender = self.race(change, lambda doomed=doomed: self.bulk(
                    "add_tags", [a], tag_ids=[keep, doomed]), serializable=kind == "merge")
                self.assertNotIsInstance(holder, BaseException)
                self.assertIsInstance(contender, BulkWorkError)
                self.assertEqual(str(contender), "One or more selected tags no longer exist.")
                self.assertEqual((self.tag_ids(a), self.revisions("work-tag")), ([], {}))

    def test_a_tag_merge_waiting_on_a_bulk_add_moves_the_new_links(self) -> None:
        a, b = self.library.add_work("A"), self.library.add_work("B")
        source, target = self.tag("Source"), self.tag("Target")
        holder, contender = self.race(self.held_bulk("add_tags", [a, b], tag_ids=[source]),
                                      lambda: self.library.merge_tags_into(source, target),
                                      serializable=True)
        self.assertIsNone(holder)
        self.assertNotIsInstance(contender, BaseException)
        self.assertEqual([self.tag_ids(w) for w in (a, b)], [[target], [target]])
        self.assert_integrity()

    def test_a_bulk_move_and_a_delete_of_its_folder_queue(self) -> None:
        a = self.library.add_work("A")
        doomed, kept = self.folder("Doomed"), self.folder("Kept")
        holder, contender = self.race(lambda scope: folders.delete(scope, doomed),
                                      lambda: self.bulk("move_folder", [a], folder_id=doomed))
        self.assertEqual(holder, (True, None))
        self.assertIsInstance(contender, BulkWorkError)
        self.assertEqual(str(contender), "Folder not found.")
        holder, contender = self.race(self.held_bulk("move_folder", [a], folder_id=kept),
                                      lambda: self.library.delete_empty_folder(kept), serializable=True)
        self.assertIsNone(holder)
        self.assertIsInstance(contender, ValueError)
        self.assertEqual((self.filed(), self.work_folder(a)), ([(a, kept)], (kept, 1)))

    def test_a_bulk_move_waiting_on_a_single_move_reruns_against_it(self) -> None:
        a = self.library.add_work("A")
        x, y = self.folder("X"), self.folder("Y")

        def move(scope: Any) -> bool:
            folders.lock_work(scope, a)
            return folders.set_work_folder(scope, a, x)

        holder, contender = self.race(move, lambda: self.bulk("move_folder", [a], folder_id=y))
        self.assertIs(holder, True)
        self.assertEqual(contender["updated"], 1)
        self.assertEqual(self.work_folder(a), (y, 2))
