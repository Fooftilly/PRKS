"""The Folder sync handlers and ``CREATE_WORK``'s Folder filing on PostgreSQL (L-O-1 #633).

``tests/test_folders_parity.py`` proves each Folder operation answers as on
SQLite. These tests cover what only PostgreSQL has: races decided by row
locks, the hierarchy lock, SERIALIZABLE retries and the title index, each
held open until the other side waits (§7.1); one ``op_id`` replayed, reused
or sent concurrently; a stale operation after a Folder or a Work was deleted
and created again under its id; dependencies and the epoch; Library
isolation and a reader; and NUL, which PostgreSQL cannot store.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from backend import sync_protocol
from backend.domain.identity.authorization import AccessRefused, Refusal
from backend.domain.organization import folders
from backend.entity_ids import generate
from backend.folder_sync import scope_key
from backend.pg_facade import NotPortedError
from backend.pg_facade import context as facade_context
from backend.sync.families import Environment, FamilyHandler
from backend.sync.handlers import folder as folder_handlers
from backend.sync.handlers import work_lifecycle
from tests.pg.test_folders import FolderTestCase
from tests.pg.test_works_sync import RecreatedWorkTests, SyncTestCase, create_payload

_FOLDER_OPERATIONS = frozenset({"CREATE_FOLDER", "SET_FOLDER_FIELD", "DELETE_FOLDER"})


def folder_payload(title: str = "Folder", **changes: str) -> dict[str, str]:
    return {"title": title, "description": "", "parent_id": "", "private_notes": "", **changes}


class FolderSyncTestCase(SyncTestCase, FolderTestCase):
    def setUp(self) -> None:
        super().setUp()
        # The real filing: this lane's Folder, then the People lane's roles.
        self.env = Environment(pdfs_dir=self.storage.pdfs_dir)

    def envelope(self, operation: str, entity_id: str, payload: dict[str, Any],
                 base: Optional[int] = None, **changes: Any) -> dict[str, Any]:
        changes.setdefault("entity_type", "folder" if operation in _FOLDER_OPERATIONS else "work")
        return super().envelope(operation, entity_id, payload, base, **changes)

    def create(self, title: str = "Folder", folder_id: Optional[str] = None, **fields: str) -> dict[str, Any]:
        return self.envelope("CREATE_FOLDER", folder_id or generate("F"), folder_payload(title, **fields))

    def create_folder(self, title: str, **fields: str) -> str:
        data = self.create(title, **fields)
        status, result = self.send(data)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"), result)
        return data["entity_id"]

    def field(self, folder_id: str, field: str, value: str, base: int, **changes: Any) -> dict[str, Any]:
        return self.envelope("SET_FOLDER_FIELD", folder_id, {"field": field, "value": value}, base, **changes)

    def file(self, work_id: str, folder_id: str, base: int, **changes: Any) -> dict[str, Any]:
        return self.envelope("SET_WORK_FOLDER", work_id, {"folder_id": folder_id}, base, **changes)

    def create_work(self, work_id: Optional[str] = None, **payload: Any) -> dict[str, Any]:
        return self.envelope("CREATE_WORK", work_id or generate("W"), create_payload(**payload))

    def applying(self, handler: FamilyHandler, data: dict[str, Any]) -> Any:
        """A holder that applies ``data`` with ``handler`` in the race's open transaction."""
        op = sync_protocol.normalize_envelope(data)
        return lambda scope: handler.apply(scope, op, datetime.now(timezone.utc), self.env)


class CreateTests(FolderSyncTestCase):
    def test_creation_acknowledges_converges_and_refuses_a_taken_title(self) -> None:
        parent = self.create_folder("Parent")
        data = self.create("  Λόγος  ", description="About", parent_id=parent)
        folder_id = data["entity_id"]
        first = self.send(data)
        self.assertEqual(first, (200, {"code": "ACKNOWLEDGED", "folder_id": folder_id, "changed": True,
                                       "folder": {"id": folder_id, "title": "Λόγος", "description": "About",
                                                  "parent_id": parent, "private_notes": "",
                                                  "work_count": 0, "child_count": 0}}))
        self.assertEqual(self.send(data), first)  # a replay
        self.library.add_work_to_folder(folder_id, self.library.add_work("W"))
        again = self.send(self.create("Other", folder_id))
        self.assertEqual((again[1]["changed"], again[1]["folder"]["title"], again[1]["folder"]["work_count"]),
                         (False, "Λόγος", 1))
        taken = self.create("ΛΌΓΟΣ", parent_id=parent)
        self.assertEqual(self.send(taken), (409, {"code": "TITLE_TAKEN", "folder_id": taken["entity_id"]}))
        self.assertEqual(self.send(self.create("ΛΌΓΟΣ"))[1]["changed"], True)  # at the root it is free
        missing = self.create("Child", parent_id=generate("F"))
        self.assertEqual(self.send(missing), (404, {"code": "PARENT_NOT_FOUND",
                                                    "folder_id": missing["entity_id"]}))
        self.assertEqual(self.count("folders"), 3)
        # Construction: every field at revision 0, no revision rows.
        self.assertEqual(self.revisions("folder-field"), {})

    def test_another_librarys_id_is_refused_unledgered(self) -> None:
        other = self.other_actor()
        theirs = generate("F")
        self.assertEqual(self.send(self.create("Theirs", theirs) | {"device_id": str(other.context.device_id)},
                                   other.context)[0], 200)
        before = len(self.ledger())
        with self.assertRaises(AccessRefused) as caught:
            self.send(self.create("Mine", theirs))
        self.assertIs(caught.exception.refusal, Refusal.FORBIDDEN)
        self.assertEqual((len(self.ledger()), self.library.get_all_folders()), (before, []))

    def test_nul_is_an_invalid_envelope(self) -> None:
        self.assertEqual(self.send(self.create("a\x00b")), (400, {"code": "INVALID_ENVELOPE"}))
        self.assertEqual(self.ledger(), [])

    def test_two_creates_of_one_title_make_one_folder(self) -> None:
        first, second = self.create("Drafts"), self.create("DRAFTS")
        holder, contender = self.race(self.applying(folder_handlers.CREATE_HANDLER, first),
                                      lambda: self.send(second))
        self.assertEqual(holder[1]["changed"], True)
        self.assertEqual(contender, (409, {"code": "TITLE_TAKEN", "folder_id": second["entity_id"]}))
        self.assertEqual(self.count("folders"), 1)

    def test_two_creates_of_one_id_make_one_folder(self) -> None:
        folder_id = generate("F")
        holder, contender = self.race(self.applying(folder_handlers.CREATE_HANDLER, self.create("A", folder_id)),
                                      lambda: self.send(self.create("B", folder_id)))
        self.assertEqual(holder[1]["changed"], True)
        self.assertEqual((contender[1]["changed"], contender[1]["folder"]["title"]), (False, "A"))
        self.assertEqual(self.count("folders"), 1)


class FieldTests(FolderSyncTestCase):
    def test_the_decision_table(self) -> None:
        folder_id, work_id = self.create_folder("Folder"), self.library.add_work("W")
        self.library.add_work_to_folder(folder_id, work_id)
        renamed = self.send(self.field(folder_id, "title", "Renamed", 0))
        self.assertEqual(renamed, (200, {"code": "ACKNOWLEDGED", "folder_id": folder_id, "field": "title",
                                         "server_revision": 1, "changed": True, "value_omitted": True,
                                         "member_work_ids": [work_id]}))
        # A stale base with the same value converges; with another value it conflicts.
        self.assertEqual(self.send(self.field(folder_id, "title", " Renamed ", 0))[1]["changed"], False)
        self.assertEqual(self.send(self.field(folder_id, "title", "Stale", 0)), (409, {
            "code": "REVISION_CONFLICT", "folder_id": folder_id, "field": "title", "current_revision": 1,
            "current_value": "Renamed", "requested_value": "Stale"}))
        self.assertEqual(self.send(self.field(folder_id, "title", "Future", 5))[1]["code"], "FUTURE_REVISION")
        self.assertEqual(self.send(self.field(folder_id, "private_notes", "Mine", 0))[1]["server_revision"], 1)
        missing = generate("F")
        self.assertEqual(self.send(self.field(missing, "title", "X", 0)),
                         (404, {"code": "ENTITY_NOT_FOUND", "folder_id": missing, "field": "title"}))
        self.assertEqual(self.field_revisions(folder_id),
                         {"description": 0, "parent_id": 0, "private_notes": 1, "title": 1})

    def test_moves_refuse_cycles_and_taken_titles(self) -> None:
        a = self.create_folder("A")
        b = self.create_folder("B", parent_id=a)
        twin = self.create_folder("b")
        for value, answer in ((b, (409, "PARENT_CYCLE")), (a, (409, "PARENT_CYCLE")),
                              (generate("F"), (404, "PARENT_NOT_FOUND"))):
            with self.subTest(value=value):
                status, result = self.send(self.field(a, "parent_id", value, 0))
                self.assertEqual((status, result["code"], result["current_revision"]), (*answer, 0))
        status, result = self.send(self.field(b, "parent_id", "", 0))
        self.assertEqual((status, result["code"]), (409, "TITLE_TAKEN"))
        self.assertEqual(self.send(self.field(twin, "parent_id", a, 0))[1]["code"], "TITLE_TAKEN")
        self.assertEqual(self.send(self.field(b, "parent_id", twin, 0))[1]["server_revision"], 1)
        self.assertEqual(self.revisions("folder-field"), {scope_key(b, "parent_id"): 1})

    def test_a_rename_that_loses_the_title_index_runs_again_and_ledgers_the_refusal(self) -> None:
        loser = self.create_folder("Loser")
        holder, contender = self.race(self.applying(folder_handlers.CREATE_HANDLER, self.create("Same")),
                                      lambda: self.send(self.field(loser, "title", "SAME", 0)))
        self.assertEqual(holder[1]["changed"], True)
        self.assertEqual(contender, (409, {"code": "TITLE_TAKEN", "folder_id": loser, "field": "title",
                                           "current_revision": 0}))
        self.assertEqual(self.ledger()[-1]["result"]["code"], "TITLE_TAKEN")

    def test_two_crossed_moves_cannot_close_a_cycle(self) -> None:
        a, b = self.create_folder("A"), self.create_folder("B")
        holder, contender = self.race(self.applying(folder_handlers.FIELD_HANDLER, self.field(a, "parent_id", b, 0)),
                                      lambda: self.send(self.field(b, "parent_id", a, 0)))
        self.assertEqual(holder[1]["changed"], True)
        self.assertEqual((contender[0], contender[1]["code"]), (409, "PARENT_CYCLE"))


class WorkFolderTests(FolderSyncTestCase):
    def test_the_decision_table(self) -> None:
        a, b = self.create_folder("A"), self.create_folder("B")
        work_id = self.library.add_work("W")
        self.assertEqual(self.send(self.file(work_id, a, 0)), (200, {
            "code": "ACKNOWLEDGED", "work_id": work_id, "changed": True, "folder_id": a, "server_revision": 1,
            "folder_title": "A"}))
        self.assertEqual(self.send(self.file(work_id, a, 0))[1]["changed"], False)  # converged
        self.assertEqual(self.send(self.file(work_id, b, 0)), (409, {
            "code": "REVISION_CONFLICT", "work_id": work_id, "current_revision": 1, "current_value": a,
            "requested_value": b}))
        self.assertEqual(self.send(self.file(work_id, b, 9))[0], 400)
        missing = generate("F")
        self.assertEqual(self.send(self.file(work_id, missing, 1)), (404, {
            "code": "FOLDER_NOT_FOUND", "work_id": work_id, "current_revision": 1}))
        self.assertEqual(self.send(self.file(work_id, "", 1)), (200, {
            "code": "ACKNOWLEDGED", "work_id": work_id, "changed": True, "folder_id": "", "server_revision": 2,
            "folder_title": ""}))
        unknown = generate("W")
        self.assertEqual(self.send(self.file(unknown, a, 0)), (404, {"code": "ENTITY_NOT_FOUND",
                                                                     "work_id": unknown}))
        self.assertEqual(self.revisions("work-folder"), {work_id: 2})

    def test_two_moves_of_one_work_advance_it_in_turn(self) -> None:
        a, b = self.create_folder("A"), self.create_folder("B")
        work_id = self.library.add_work("W")
        holder, contender = self.race(self.applying(folder_handlers.WORK_FOLDER_HANDLER, self.file(work_id, a, 0)),
                                      lambda: self.send(self.file(work_id, b, 0)), serializable=True)
        self.assertEqual(holder[1]["server_revision"], 1)
        # It waited, retried and saw the first move: its base is stale.
        self.assertEqual((contender[0], contender[1]["code"]), (409, "REVISION_CONFLICT"))
        self.assertEqual(self.work_folder(work_id), (a, 1))

    def test_a_filing_into_a_folder_being_deleted_is_refused(self) -> None:
        doomed, work_id = self.create_folder("Doomed"), self.library.add_work("W")
        holder, contender = self.race(
            self.applying(folder_handlers.DELETE_HANDLER, self.envelope("DELETE_FOLDER", doomed, {})),
            lambda: self.send(self.file(work_id, doomed, 0)))
        self.assertEqual(holder[1]["changed"], True)
        self.assertEqual(contender, (404, {"code": "FOLDER_NOT_FOUND", "work_id": work_id, "current_revision": 0}))
        self.assertEqual(self.filed(), [])


class DeleteTests(FolderSyncTestCase):
    def test_refusals_and_convergence(self) -> None:
        parent = self.create_folder("Parent")
        self.create_folder("Child", parent_id=parent)
        busy = self.create_folder("Busy")
        self.send(self.file(self.library.add_work("W"), busy, 0))
        self.assertEqual(self.send(self.envelope("DELETE_FOLDER", parent, {})),
                         (409, {"code": "FOLDER_HAS_SUBFOLDERS", "folder_id": parent}))
        self.assertEqual(self.send(self.envelope("DELETE_FOLDER", busy, {})),
                         (409, {"code": "FOLDER_NOT_EMPTY", "folder_id": busy}))
        gone = generate("F")
        for _ in range(2):
            self.assertEqual(self.send(self.envelope("DELETE_FOLDER", gone, {})),
                             (200, {"code": "ACKNOWLEDGED", "folder_id": gone, "changed": False}))

    def test_a_base_from_before_the_delete_never_lands_on_the_folder_created_again(self) -> None:
        folder_id = generate("F")
        self.send(self.create("Old", folder_id))
        tag = self.library.add_tag("Ethics")["id"]
        self.assertEqual(self.send(self.envelope("ADD_FOLDER_TAG", folder_id, {"tag_id": tag}, 0,
                                                 entity_type="folder"))[1]["server_revision"], 1)
        self.assertEqual(self.send(self.envelope("DELETE_FOLDER", folder_id, {}))[1]["changed"], True)
        self.assertEqual(self.send(self.envelope("ADD_FOLDER_TAG", folder_id, {"tag_id": tag}, 1,
                                                 entity_type="folder"))[1]["code"], "ENTITY_NOT_FOUND")
        self.assertEqual(self.send(self.create("New", folder_id))[1]["changed"], True)
        # Every field was at revision 0 and is now at 1: a base-0 edit is stale.
        for field, value in (("title", "Stale"), ("description", "Stale"), ("private_notes", "Stale")):
            with self.subTest(field=field):
                status, result = self.send(self.field(folder_id, field, value, 0))
                self.assertEqual((status, result["code"], result["current_revision"]),
                                 (409, "REVISION_CONFLICT", 1))
        self.assertEqual(self.send(self.envelope("ADD_FOLDER_TAG", folder_id, {"tag_id": tag}, 1,
                                                 entity_type="folder"))[0], 409)
        # A base observed after the recreation is current and applies.
        self.assertEqual(self.send(self.field(folder_id, "title", "Fresh", 1))[1]["server_revision"], 2)


class CreateWorkFilingTests(FolderSyncTestCase):
    def test_a_work_with_no_folder_goes_to_one_uncategorized_root(self) -> None:
        first, second = self.create_work(), self.create_work()
        status, result = self.send(first)
        self.assertEqual(status, 200)
        uncategorized = result["folder_id"]
        self.assertEqual((result["changed"], result["playlist_id"], result["role_count"]), (True, "", 0))
        self.assertEqual(self.send(second)[1]["folder_id"], uncategorized)
        self.assertEqual(self.library.get_folder(uncategorized)["title"], "Uncategorized")
        self.assertEqual(self.library.get_all_folders()[0]["work_count"], 2)
        self.assertEqual(self.library.get_work(first["entity_id"])["folder_title"], "Uncategorized")
        # Filing is construction: no revision moved.
        self.assertEqual(self.work_folder(first["entity_id"]), (uncategorized, 0))
        self.assertEqual(self.revisions("work-folder"), {})
        # So a base of 0 is current for the first move.
        other = self.create_folder("Other")
        self.assertEqual(self.send(self.file(first["entity_id"], other, 0))[1]["server_revision"], 1)

    def test_an_explicit_folder_or_its_absence(self) -> None:
        folder_id = self.create_folder("Talks")
        status, result = self.send(self.create_work(folder_id=folder_id))
        self.assertEqual((status, result["folder_id"]), (200, folder_id))
        missing = self.create_work(folder_id=generate("F"))
        self.assertEqual(self.send(missing), (404, {"code": "FOLDER_NOT_FOUND", "work_id": missing["entity_id"]}))
        # A Person is checked after the Folder, as on SQLite.
        ghost = self.create_work(roles=[{"person_id": generate("P"), "role_type": "Author"}])
        self.assertEqual(self.send(ghost)[1]["code"], "PERSON_NOT_FOUND")
        both = self.create_work(folder_id=generate("F"), roles=[{"person_id": generate("P"), "role_type": "Author"}])
        self.assertEqual(self.send(both)[1]["code"], "FOLDER_NOT_FOUND")
        self.assertEqual((self.count("works"), self.filed()), (1, [(result["work_id"], folder_id)]))

    def test_roles_are_filed_with_the_folder(self) -> None:
        person = self.library.add_person("Ann", "Lee")
        folder_id = self.create_folder("Talks")
        data = self.create_work(folder_id=folder_id, roles=[{"person_id": person, "role_type": "Author",
                                                             "credit_name": "A. Lee"}])
        status, result = self.send(data)
        self.assertEqual((status, result["role_count"], result["aliases_revisions"]), (200, 1, {person: 1}))
        self.assertEqual([row["id"] for row in self.library.get_work_roles(data["entity_id"])], [person])
        self.assertEqual(self.library.get_related_folders_for_work(data["entity_id"]), [])

    def test_a_playlist_is_not_ported_and_nothing_is_written(self) -> None:
        with self.assertRaises(NotPortedError):
            self.send(self.create_work(playlist_id="PL-" + "0" * 32))
        self.assertEqual((self.ledger(), self.count("works"), self.count("folders")), ([], 0, 0))

    def test_a_create_into_a_folder_being_deleted_is_refused(self) -> None:
        doomed = self.create_folder("Doomed")
        data = self.create_work(folder_id=doomed)
        holder, contender = self.race(
            self.applying(folder_handlers.DELETE_HANDLER, self.envelope("DELETE_FOLDER", doomed, {})),
            lambda: self.send(data))
        self.assertEqual(holder[1]["changed"], True)
        self.assertEqual(contender, (404, {"code": "FOLDER_NOT_FOUND", "work_id": data["entity_id"]}))
        self.assertEqual(self.count("works"), 0)

    def test_two_creates_with_no_folder_make_one_uncategorized(self) -> None:
        first, second = self.create_work(), self.create_work()
        holder, contender = self.race(self.applying(work_lifecycle.CREATE_HANDLER, first),
                                      lambda: self.send(second), serializable=True)
        self.assertEqual(holder[1]["changed"], True)
        self.assertEqual((contender[0], contender[1]["folder_id"]), (200, holder[1]["folder_id"]))
        self.assertEqual(self.count("folders"), 1)
        self.assertEqual(len(self.filed()), 2)

    def test_a_folder_base_from_before_a_work_delete_never_lands_on_the_recreated_work(self) -> None:
        a, b = self.create_folder("A"), self.create_folder("B")
        work_id = generate("W")
        self.send(self.create_work(work_id, folder_id=a))
        self.assertEqual(self.send(self.envelope("DELETE_WORK", work_id, {}))[1]["changed"], True)
        self.assertEqual(self.revisions("work-folder"), {work_id: 1})  # tombstoned from revision 0
        self.assertEqual(self.send(self.create_work(work_id, folder_id=a))[1]["changed"], True)
        self.assertEqual(self.send(self.file(work_id, b, 0)), (409, {
            "code": "REVISION_CONFLICT", "work_id": work_id, "current_revision": 1, "current_value": a,
            "requested_value": b}))
        self.assertEqual(self.send(self.file(work_id, b, 1))[1]["server_revision"], 2)


class RecreatedFiledWorkTests(FolderSyncTestCase):
    recreate = RecreatedWorkTests.recreate

    def test_a_filed_work_delete_tombstones_the_scope_with_the_rest(self) -> None:
        folder_id, work_id = self.create_folder("A"), generate("W")
        self.recreate(work_id)
        self.assertEqual(self.send(self.file(work_id, folder_id, 0))[1]["server_revision"], 1)
        self.assertIsNotNone(self.library.delete_work_record(work_id))
        self.recreate(work_id)
        self.assertEqual(self.work_folder(work_id), ("", 2))
        self.assertEqual(self.send(self.file(work_id, folder_id, 1))[0], 409)


class LedgerAndIsolationTests(FolderSyncTestCase):
    def test_dependencies_name_acknowledged_operations_of_the_device(self) -> None:
        work_id = self.library.add_work("W")
        create = self.create("Talks")
        self.send(create)
        filed = self.file(work_id, create["entity_id"], 0, depends_on=[create["op_id"]])
        self.assertEqual(self.send(filed)[0], 200)
        orphan = self.field(create["entity_id"], "title", "X", 0, depends_on=[str(uuid.uuid4())])
        self.assertEqual(self.send(orphan), (400, {"code": "UNSATISFIED_DEPENDENCY"}))
        self.assertEqual(len(self.ledger()), 2)

    def test_an_operation_prepared_against_an_old_epoch_is_refused(self) -> None:
        create = self.create("Talks")
        first = self.send(create)
        self.sql("UPDATE prks.libraries SET epoch = epoch + 1 WHERE id = %s", (self.actor.library_id,))
        self.assertEqual(self.send(create), first)  # a replay of what the restored state holds
        with self.assertRaises(AccessRefused) as caught:
            self.send(self.field(create["entity_id"], "title", "After", 0))
        self.assertIs(caught.exception.refusal, Refusal.LIBRARY_EPOCH_CHANGED)
        self.assertEqual(len(self.ledger()), 1)

    def test_another_library_cannot_see_or_change_these_folders(self) -> None:
        folder_id, work_id = self.create_folder("Mine"), self.library.add_work("W")
        other = self.other_actor()
        device = {"device_id": str(other.context.device_id)}
        with facade_context.acting_as(other.context):
            their_work = self.library.add_work("Theirs")
        for data, answer in (
                (self.field(folder_id, "title", "Theirs", 0),
                 (404, {"code": "ENTITY_NOT_FOUND", "folder_id": folder_id, "field": "title"})),
                (self.file(work_id, folder_id, 0), (404, {"code": "ENTITY_NOT_FOUND", "work_id": work_id})),
                (self.file(their_work, folder_id, 0),
                 (404, {"code": "FOLDER_NOT_FOUND", "work_id": their_work, "current_revision": 0})),
                (self.envelope("DELETE_FOLDER", folder_id, {}),
                 (200, {"code": "ACKNOWLEDGED", "folder_id": folder_id, "changed": False}))):
            with self.subTest(operation=data["operation"]):
                self.assertEqual(self.send(data | device, other.context), answer)
        self.assertEqual((self.library.get_folder(folder_id)["title"], self.filed()), ("Mine", []))

    def test_a_reader_cannot_send_folder_operations(self) -> None:
        reader = self.reader_context()
        with self.assertRaises(AccessRefused):
            self.send(self.create("Talks") | {"device_id": str(reader.device_id)}, reader)
        self.assertEqual(self.count("folders"), 0)

    def test_a_folder_operation_and_a_tag_delete_share_the_folder_in_turn(self) -> None:
        folder_id = self.create_folder("Tagged")
        tag = self.library.add_tag("Ethics")["id"]
        self.library.add_tag_to_folder(folder_id, tag)
        holder, contender = self.race(lambda scope: folders.delete(scope, folder_id),
                                      lambda: self.send(self.envelope("DELETE_TAG", tag, {}, entity_type="tag")))
        self.assertEqual(holder, (True, None))
        self.assertEqual((contender[0], contender[1]["affected_folder_ids"]), (200, []))
