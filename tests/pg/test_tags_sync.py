"""The Tag sync handlers on PostgreSQL (L-O-1 #633).

``tests/test_tags_parity.py`` proves each Tag operation answers as on
SQLite. These tests cover what only PostgreSQL has: races decided by row
locks, SERIALIZABLE retries and the ``tag_keys`` index, each held open until
the other side waits (§7.1); one ``op_id`` replayed, reused or sent
concurrently; a stale operation after a delete or a merge, and after its
Work was deleted and recreated; dependencies; Library isolation and a
reader; and NUL, which PostgreSQL cannot store.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from backend import sync_protocol
from backend.domain.identity.authorization import AccessRefused, Refusal
from backend.domain.organization import links, tags
from backend.entity_ids import generate
from backend.pg_facade import context as facade_context
from backend.sync.families import FamilyHandler
from backend.sync.handlers import tag as tag_handlers
from backend.sync.handlers import tag_link
from tests.pg.test_tags import OrganizationTestCase
from tests.pg.test_works_sync import RecreatedWorkTests, SyncTestCase

_ENTITY_TYPES = {"CREATE_TAG": "tag", "DELETE_TAG": "tag", "MERGE_TAG": "tag",
                 "ADD_FOLDER_TAG": "folder", "REMOVE_FOLDER_TAG": "folder"}


class TagSyncTestCase(SyncTestCase, OrganizationTestCase):
    def envelope(self, operation: str, entity_id: str, payload: dict[str, Any],
                 base: Optional[int] = None, **changes: Any) -> dict[str, Any]:
        return super().envelope(operation, entity_id, payload, base,
                                entity_type=_ENTITY_TYPES.get(operation, "work"), **changes)

    def create(self, name: str, tag_id: Optional[str] = None, color: str = "#6d6cf7") -> dict[str, Any]:
        return self.envelope("CREATE_TAG", tag_id or generate("T"), {"name": name, "color": color})

    def create_tag(self, name: str) -> str:
        data = self.create(name)
        status, result = self.send(data)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"), result)
        return data["entity_id"]

    def link(self, operation: str, owner_id: str, tag_id: str, base: int, **changes: Any) -> dict[str, Any]:
        return self.envelope(operation, owner_id, {"tag_id": tag_id}, base, **changes)

    def merge(self, source: str, target: str) -> dict[str, Any]:
        return self.envelope("MERGE_TAG", source, {"target_tag_id": target})

    def applying(self, handler: FamilyHandler, data: dict[str, Any]) -> Any:
        """A holder that applies ``data`` with ``handler`` in the race's open transaction."""
        op = sync_protocol.normalize_envelope(data)
        return lambda scope: handler.apply(scope, op, datetime.now(timezone.utc), self.env)


class CreateTests(TagSyncTestCase):
    def test_creation_acknowledges_converges_and_refuses_a_taken_name(self) -> None:
        data = self.create("  Λόγος  ")
        tag_id = data["entity_id"]
        first = self.send(data)
        self.assertEqual(first, (200, {"code": "ACKNOWLEDGED", "tag_id": tag_id, "changed": True,
                                       "tag": {"id": tag_id, "name": "Λόγος", "color": "#6d6cf7"}}))
        self.assertEqual(self.send(data), first)  # a replay
        again = self.send(self.create("Other", tag_id, "#000000"))
        self.assertEqual(again[1]["changed"], False)
        self.assertEqual(again[1]["tag"]["name"], "Λόγος")
        self.library.add_tag_alias(tag_id, "Logos")
        for name in ("ΛΌΓΟΣ", "LOGOS"):
            other = self.create(name)
            with self.subTest(name=name):
                self.assertEqual(self.send(other), (409, {"code": "NAME_TAKEN", "tag_id": other["entity_id"],
                                                          "target_tag_id": tag_id}))
        self.assertEqual(self.count("tags"), 1)
        self.assert_integrity()

    def test_a_deleted_or_merged_id_is_never_constructed_again(self) -> None:
        deleted, source, target = self.create_tag("A"), self.create_tag("B"), self.create_tag("C")
        self.library.delete_tag(deleted)
        self.library.merge_tags_into(source, target)
        self.assertEqual(self.send(self.create("A", deleted)), (409, {"code": "TAG_DELETED", "tag_id": deleted}))
        self.assertEqual(self.send(self.create("New", source)),
                         (409, {"code": "TAG_MERGED", "tag_id": source, "target_tag_id": target}))
        self.assertEqual(self.count("tags"), 1)

    def test_another_librarys_id_is_refused_unledgered(self) -> None:
        other = self.other_actor()
        theirs, gone = generate("T"), generate("T")
        device = {"device_id": str(other.context.device_id)}
        for data in (self.create("Theirs", theirs), self.create("Gone", gone),
                     self.envelope("DELETE_TAG", gone, {})):
            self.assertEqual(self.send(data | device, other.context)[0], 200)
        before = len(self.ledger())
        for tag_id in (theirs, gone):
            with self.subTest(tag_id=tag_id), self.assertRaises(AccessRefused) as caught:
                self.send(self.create("Mine", tag_id))
            self.assertEqual(caught.exception.refusal, Refusal.FORBIDDEN)
        self.assertEqual(len(self.ledger()), before)
        self.assertEqual(self.library.get_all_tags(), [])

    def test_nul_is_an_invalid_envelope(self) -> None:
        self.assertEqual(self.send(self.create("a\x00b")), (400, {"code": "INVALID_ENVELOPE"}))
        self.assertEqual(self.ledger(), [])

    def test_two_creates_of_one_name_make_one_tag(self) -> None:
        first, second = self.create("Λόγος"), self.create("ΛΌΓΟΣ")
        holder, contender = self.race(self.applying(tag_handlers.CREATE_HANDLER, first),
                                      lambda: self.send(second), serializable=True)
        self.assertEqual(holder[1]["changed"], True)
        self.assertEqual(contender, (409, {"code": "NAME_TAKEN", "tag_id": second["entity_id"],
                                           "target_tag_id": first["entity_id"]}))
        self.assertEqual(self.count("tags"), 1)

    def test_two_creates_of_one_id_make_one_tag(self) -> None:
        first = self.create("Ethics")
        second = self.create("Ethics", first["entity_id"])
        holder, contender = self.race(self.applying(tag_handlers.CREATE_HANDLER, first),
                                      lambda: self.send(second), serializable=True)
        self.assertEqual(holder[1]["changed"], True)
        self.assertEqual((contender[0], contender[1]["changed"]), (200, False))

    def test_a_create_racing_an_alias_of_its_name(self) -> None:
        ethics = self.create_tag("Ethics")
        data = self.create("morals")
        holder, contender = self.race(lambda scope: tags.add_alias(scope, generate("L"), ethics, "Morals"),
                                      lambda: self.send(data), serializable=True)
        self.assertIsNone(holder)
        self.assertEqual(contender, (409, {"code": "NAME_TAKEN", "tag_id": data["entity_id"],
                                           "target_tag_id": ethics}))


class LinkTests(TagSyncTestCase):
    def test_the_decision_table(self) -> None:
        work, folder, tag = self.library.add_work("W"), self.folder(), self.create_tag("Ethics")
        for operation, owner, link in (("WORK", work, links.WORK), ("FOLDER", folder, links.FOLDER)):
            add, remove = "ADD_%s_TAG" % operation, "REMOVE_%s_TAG" % operation
            with self.subTest(operation=operation):
                status, result = self.send(self.link(add, owner, tag, 0))
                self.assertEqual((status, result["code"], result["server_revision"], result["changed"]),
                                 (200, "ACKNOWLEDGED", 1, True))
                self.assertEqual(result["tag"], {"id": tag, "name": "Ethics", "color": "#6d6cf7"})
                # A stale base that agrees converges; one that disagrees conflicts.
                self.assertEqual(self.send(self.link(add, owner, tag, 0))[1]["changed"], False)
                self.assertEqual(self.send(self.link(remove, owner, tag, 0)), (409, {
                    "code": "REVISION_CONFLICT", link.owner_key: owner, "tag_id": tag, "current_revision": 1,
                    "current_state": True, "requested_state": False}))
                self.assertEqual(self.send(self.link(remove, owner, tag, 2))[0], 400)
                self.assertEqual(self.send(self.link(remove, owner, tag, 1))[1]["server_revision"], 2)
                missing = generate(owner[0])
                self.assertEqual(self.send(self.link(add, missing, tag, 0)),
                                 (404, {"code": "ENTITY_NOT_FOUND", link.owner_key: missing, "tag_id": tag}))

    def test_a_deleted_or_merged_tag_is_never_linked_or_redirected(self) -> None:
        work = self.library.add_work("W")
        deleted, source, target = self.create_tag("A"), self.create_tag("B"), self.create_tag("C")
        self.assertEqual(self.send(self.link("ADD_WORK_TAG", work, source, 0))[0], 200)
        self.send(self.envelope("DELETE_TAG", deleted, {}))
        self.send(self.merge(source, target))
        self.assertEqual(self.send(self.link("ADD_WORK_TAG", work, deleted, 0)),
                         (409, {"code": "TAG_DELETED", "work_id": work, "tag_id": deleted}))
        # A stale remove of the source never reaches the target's link.
        self.assertEqual(self.send(self.link("REMOVE_WORK_TAG", work, source, 1)),
                         (409, {"code": "TAG_MERGED", "work_id": work, "tag_id": source, "target_tag_id": target}))
        self.assertEqual([row["id"] for row in self.library.get_work_tags(work)], [target])
        self.assertEqual(self.send(self.link("ADD_WORK_TAG", work, generate("T"), 0))[1]["code"],
                         "ENTITY_NOT_FOUND")

    def test_a_link_to_the_source_waits_for_the_merge(self) -> None:
        work, source, target = self.library.add_work("W"), self.create_tag("A"), self.create_tag("B")
        holder, contender = self.race(self.applying(tag_handlers.MERGE_HANDLER, self.merge(source, target)),
                                      lambda: self.send(self.link("ADD_WORK_TAG", work, source, 0)),
                                      serializable=True)
        self.assertEqual(holder[0], 200)
        self.assertEqual(contender, (409, {"code": "TAG_MERGED", "work_id": work, "tag_id": source,
                                           "target_tag_id": target}))
        self.assertEqual(self.count("work_tags"), 0)

    def test_a_merge_waits_for_a_link_to_the_source(self) -> None:
        work, source, target = self.library.add_work("W"), self.create_tag("A"), self.create_tag("B")
        holder, contender = self.race(
            self.applying(tag_link.ADD_WORK_HANDLER, self.link("ADD_WORK_TAG", work, source, 0)),
            lambda: self.send(self.merge(source, target)), serializable=True)
        self.assertEqual(holder[0], 200)
        self.assertEqual(contender[1]["affected_work_ids"], [work])
        self.assertEqual([row["id"] for row in self.library.get_work_tags(work)], [target])

    def test_a_merge_reruns_on_the_key_after_a_read_committed_link(self) -> None:
        work, source, target = self.library.add_work("W"), self.create_tag("A"), self.create_tag("B")
        # The add applied READ COMMITTED (no PRKS command adds a link so): the
        # merge's snapshot misses it, the link's key refuses the source's
        # delete, and the operation runs once more.
        holder, contender = self.race(
            self.applying(tag_link.REMOVE_WORK_HANDLER, self.link("ADD_WORK_TAG", work, source, 0)),
            lambda: self.send(self.merge(source, target)))
        self.assertEqual(holder[0], 200)
        self.assertEqual((contender[0], contender[1]["affected_work_ids"]), (200, [work]))
        self.assertEqual(self.ledger()[-1]["result"]["affected_work_ids"], [work])

    def test_a_link_waits_for_the_delete(self) -> None:
        work, tag = self.library.add_work("W"), self.create_tag("A")
        holder, contender = self.race(self.applying(tag_handlers.DELETE_HANDLER,
                                                    self.envelope("DELETE_TAG", tag, {})),
                                      lambda: self.send(self.link("ADD_WORK_TAG", work, tag, 0)))
        self.assertEqual(holder[1]["changed"], True)
        self.assertEqual(contender, (409, {"code": "TAG_DELETED", "work_id": work, "tag_id": tag}))

    def test_two_adds_of_one_link_advance_it_once(self) -> None:
        work, tag = self.library.add_work("W"), self.create_tag("A")
        holder, contender = self.race(
            self.applying(tag_link.ADD_WORK_HANDLER, self.link("ADD_WORK_TAG", work, tag, 0)),
            lambda: self.send(self.link("ADD_WORK_TAG", work, tag, 0)), serializable=True)
        self.assertEqual(holder[1]["changed"], True)
        self.assertEqual((contender[1]["code"], contender[1]["changed"]), ("ACKNOWLEDGED", False))
        self.assertEqual(self.revisions("work-tag"), {links.WORK.scope_id(work, tag): 1})


class LifecycleTests(TagSyncTestCase):
    def test_delete_and_merge_answers(self) -> None:
        source, target, gone = self.create_tag("A"), self.create_tag("B"), self.create_tag("C")
        self.assertEqual(self.send(self.envelope("DELETE_TAG", gone, {}))[1]["changed"], True)
        self.assertEqual(self.send(self.envelope("DELETE_TAG", gone, {}))[1]["changed"], False)
        self.assertEqual(self.send(self.merge(source, gone)), (409, {"code": "TAG_DELETED", "tag_id": gone}))
        self.assertEqual(self.send(self.merge(gone, source)), (409, {"code": "TAG_DELETED", "tag_id": gone}))
        unknown = generate("T")
        self.assertEqual(self.send(self.merge(unknown, source)),
                         (404, {"code": "ENTITY_NOT_FOUND", "tag_id": unknown}))
        status, merged = self.send(self.merge(source, target))
        self.assertEqual((status, merged["canonical_tag_id"], merged["canonical_name"]), (200, target, "B"))
        third = self.create_tag("D")
        self.assertEqual(self.send(self.merge(third, source)),
                         (404, {"code": "ENTITY_NOT_FOUND", "tag_id": source}))
        self.assertEqual(self.send(self.merge(source, third)),
                         (409, {"code": "TAG_MERGED", "tag_id": source, "target_tag_id": target}))
        self.assertEqual(self.send(self.envelope("DELETE_TAG", source, {})),
                         (409, {"code": "TAG_MERGED", "tag_id": source, "target_tag_id": target}))
        self.assert_integrity()

    def test_crossed_merges_leave_one_tag(self) -> None:
        first, second = self.create_tag("A"), self.create_tag("B")
        holder, contender = self.race(self.applying(tag_handlers.MERGE_HANDLER, self.merge(first, second)),
                                      lambda: self.send(self.merge(second, first)), serializable=True)
        self.assertEqual(holder[0], 200)
        # It waited for the first merge and found its target merged away.
        self.assertEqual(contender, (404, {"code": "ENTITY_NOT_FOUND", "tag_id": first}))
        self.assertEqual([row["id"] for row in self.library.get_all_tags()], [second])
        self.assert_integrity()

    def test_dependencies_name_acknowledged_operations_of_the_device(self) -> None:
        work = self.library.add_work("W")
        create = self.create("Ethics")
        self.send(create)
        add = self.link("ADD_WORK_TAG", work, create["entity_id"], 0, depends_on=[create["op_id"]])
        self.assertEqual(self.send(add)[0], 200)
        orphan = self.link("REMOVE_WORK_TAG", work, create["entity_id"], 1, depends_on=[str(uuid.uuid4())])
        self.assertEqual(self.send(orphan), (400, {"code": "UNSATISFIED_DEPENDENCY"}))
        self.assertEqual(len(self.ledger()), 2)


class IsolationTests(TagSyncTestCase):
    def test_another_library_cannot_see_or_change_these_tags(self) -> None:
        work, folder, tag = self.library.add_work("W"), self.folder(), self.create_tag("Ethics")
        other = self.other_actor()
        for data, answer in (
                (self.link("ADD_WORK_TAG", work, tag, 0), (404, {"code": "ENTITY_NOT_FOUND", "work_id": work,
                                                                 "tag_id": tag})),
                (self.link("ADD_FOLDER_TAG", folder, tag, 0), (404, {"code": "ENTITY_NOT_FOUND",
                                                                     "folder_id": folder, "tag_id": tag})),
                (self.envelope("DELETE_TAG", tag, {}), (200, {"code": "ACKNOWLEDGED", "tag_id": tag,
                                                              "changed": False, **tags.EMPTY_AFFECTED})),
                (self.merge(tag, generate("T")), (404, {"code": "ENTITY_NOT_FOUND", "tag_id": tag}))):
            with self.subTest(operation=data["operation"]):
                data["device_id"] = str(other.context.device_id)
                self.assertEqual(self.send(data, other.context), answer)
        # Their own Work cannot take this Library's Tag either.
        with facade_context.acting_as(other.context):
            theirs = self.library.add_work("Theirs")
        self.assertEqual(self.send(self.link("ADD_WORK_TAG", theirs, tag, 0) | {
            "device_id": str(other.context.device_id)}, other.context)[1]["code"], "ENTITY_NOT_FOUND")
        self.assertEqual(len(self.library.get_all_tags()), 1)
        self.assertEqual(self.count("work_tags"), 0)

    def test_a_reader_cannot_send_tag_operations(self) -> None:
        reader = self.reader_context()
        data = self.create("Ethics") | {"device_id": str(reader.device_id)}
        with self.assertRaises(AccessRefused):
            self.send(data, reader)
        self.assertEqual(self.count("tags"), 0)


class RecreatedWorkTagTests(TagSyncTestCase):
    recreate = RecreatedWorkTests.recreate

    def test_a_link_base_from_before_a_work_delete_never_lands_on_the_recreated_work(self) -> None:
        tag = self.create_tag("Ethics")
        work_id = "W-" + uuid.uuid4().hex.upper()
        self.recreate(work_id)
        self.assertEqual(self.send(self.link("ADD_WORK_TAG", work_id, tag, 0))[1]["server_revision"], 1)
        self.assertIsNotNone(self.library.delete_work_record(work_id))
        self.recreate(work_id)
        self.assertEqual(self.send(self.link("REMOVE_WORK_TAG", work_id, tag, 1))[1]["changed"], False)
        self.assertEqual(self.send(self.link("ADD_WORK_TAG", work_id, tag, 1)), (409, {
            "code": "REVISION_CONFLICT", "work_id": work_id, "tag_id": tag, "current_revision": 2,
            "current_state": False, "requested_state": True}))
