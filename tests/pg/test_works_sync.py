"""The PostgreSQL operation ledger and the Works handlers (L-W-1 #628).

``tests/test_works_sync_parity.py`` proves each Works operation answers as
on SQLite. These tests cover what only PostgreSQL has (§7.3, §7.6): one
``op_id`` sent concurrently, the ledger looked up before the epoch, a
ledger row of another account or Library, the device check, a claim rolled
back with its exception, concurrent writers through the protocol, and
``CREATE_WORK`` with a stand-in for the folder filing the Organization lane
will provide; then operations queued before a delete, another Library, a
worker without a person, a changed epoch, a failure after the write, and
concurrent note writers.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from unittest import mock

from backend import sync_protocol, work_metadata_sync, work_note_sync, work_open_sync, work_source_sync
from backend.domain.identity.authorization import AccessRefused, Refusal
from backend.domain.identity.library_scope import LibraryScope
from backend.domain.identity.request_context import RequestContext
from backend.domain.organization import playlists
from backend.domain.research.rules import ResearchError
from backend.domain.works import commands, notes
from backend.entity_ids import generate
from backend.pg_facade import NotPortedError
from backend.pg_facade import context as facade_context
from backend.sync import protocol
from backend.sync.families import Environment, FamilyHandler, Result
from backend.sync.handlers import work_lifecycle, work_metadata
from tests.pg.test_works_mutations import MutationTestCase

_VIDEO = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
_OTHER_VIDEO = "https://www.youtube.com/watch?v=9bZkp7q19f0"


class StandInFiling:
    """Files nothing; reports what SQLite reports for an existing root folder."""

    def __init__(self) -> None:
        self.filed: list[str] = []

    def prepare(self, scope: LibraryScope, work_id: str, fields: dict[str, Any]) -> dict[str, Any] | Result:
        if fields["folder_id"] == "F-MISSING":
            return 404, {"code": "FOLDER_NOT_FOUND", "work_id": work_id}
        return {"folder_id": fields["folder_id"] or "F-UNCATEGORIZED",
                "playlist_id": fields["playlist_id"], "role_count": len(fields["roles"])}

    def file(self, scope: LibraryScope, work_id: str, fields: dict[str, Any],
             prepared: dict[str, Any]) -> dict[str, Any]:
        self.filed.append(work_id)
        return {}


def create_payload(**changes: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "title": "  A talk  ", "status": "", "doc_type": "", "abstract": "Abstract",
        "author_text": "Speaker", "year": " 2024 ", "published_date": "2024-05", "urldate": "",
        "private_notes": "  mine  ", "thumb_url": "https://img/t.jpg",
        "source": {"kind": "video", "url": _VIDEO}, "folder_id": "", "playlist_id": "", "roles": [],
    }
    payload.update(changes)
    return payload


class SyncTestCase(MutationTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.filing = StandInFiling()
        self.env = Environment(pdfs_dir=self.storage.pdfs_dir, filing=self.filing)
        device = self.actor.context.device_id
        assert device is not None
        self.device = str(device)

    def envelope(self, operation: str, entity_id: str, payload: dict[str, Any],
                 base: Optional[int] = None, **changes: Any) -> dict[str, Any]:
        data: dict[str, Any] = {
            "op_id": str(uuid.uuid4()), "device_id": self.device, "operation": operation,
            "entity_type": "work", "entity_id": entity_id, "payload": payload, "base_revision": base,
            "occurred_at": "2026-09-11T10:00:00Z", "created_at": "2026-09-11T10:00:00Z",
            "depends_on": [],
        }
        data.update(changes)
        return data

    def send(self, data: dict[str, Any], ctx: Optional[RequestContext] = None) -> Result:
        return protocol.process_operation(self.db, ctx or self.actor.context, data, self.env)

    def ledger(self) -> list[dict[str, Any]]:
        return self.sql("SELECT op_id::text AS op_id, library_id, user_id, status, http_status, result"
                        " FROM prks.sync_operations ORDER BY applied_at, op_id")

    def title_op(self, work_id: str, value: str, base: int, **changes: Any) -> dict[str, Any]:
        return self.envelope("SET_WORK_METADATA_FIELD", work_id, {"field": "title", "value": value}, base,
                             **changes)


class LedgerTests(SyncTestCase):
    def test_one_op_id_sent_concurrently_applies_once(self) -> None:
        work_id = self.library.add_work("Work")
        data = self.title_op(work_id, "Once", 0)
        results = self.concurrently([lambda: self.send(data) for _ in range(6)])
        self.assertTrue(all(result == results[0] for result in results), results)
        self.assertEqual(results[0][1]["server_revision"], 1)
        self.assertEqual(len(self.ledger()), 1)
        self.assertEqual(self.library.get_work_metadata_state(work_id)["fields"]["title"]["revision"], 1)

    def test_a_create_sent_concurrently_applies_once(self) -> None:
        data = self.envelope("CREATE_WORK", "W-" + uuid.uuid4().hex.upper(), create_payload())
        results = self.concurrently([lambda: self.send(data) for _ in range(4)])
        self.assertTrue(all(result == results[0] for result in results), results)
        self.assertEqual(results[0][1]["changed"], True)
        self.assertEqual((self.count("works"), len(self.ledger()), len(self.filing.filed)), (1, 1, 1))

    def test_the_ledger_is_read_before_the_epoch(self) -> None:
        work_id = self.library.add_work("Work")
        data = self.title_op(work_id, "Before", 0)
        first = self.send(data)
        self.sql("UPDATE prks.libraries SET epoch = epoch + 1 WHERE id = %s", (self.actor.library_id,))
        # The restored state already holds the effect: the retry replays it.
        self.assertEqual(self.send(data), first)
        # A new operation prepared against the old epoch is refused, unledgered.
        with self.assertRaises(AccessRefused) as caught:
            self.send(self.title_op(work_id, "After", 1))
        self.assertIs(caught.exception.refusal, Refusal.LIBRARY_EPOCH_CHANGED)
        self.assertEqual(len(self.ledger()), 1)

    def test_an_op_id_of_another_account_or_library_is_never_disclosed(self) -> None:
        work_id = self.library.add_work("Work")
        data = self.title_op(work_id, "Mine", 0)
        self.send(data)
        other = self.other_actor()
        self.assertEqual(self.send(dict(data, device_id=str(other.context.device_id)), other.context),
                         (409, {"code": "OP_ID_REUSE"}))
        self.assertEqual(len(self.ledger()), 1)
        self.assertEqual(self.library.get_work(work_id)["title"], "Mine")

    def test_the_device_must_be_the_users(self) -> None:
        work_id = self.library.add_work("Work")
        other = self.other_actor()
        with self.assertRaises(AccessRefused) as caught:
            self.send(self.title_op(work_id, "Stolen", 0, device_id=str(other.context.device_id)))
        self.assertIs(caught.exception.refusal, Refusal.FORBIDDEN)
        self.assertEqual((self.ledger(), self.library.get_work(work_id)["title"]), ([], "Work"))

    def test_a_new_device_is_bound_to_the_user_that_first_presents_it(self) -> None:
        work_id = self.library.add_work("Work")
        device = str(uuid.uuid4())
        status, _result = self.send(self.title_op(work_id, "From a new browser", 0, device_id=device))
        self.assertEqual(status, 200)
        self.assertEqual(self.sql("SELECT user_id FROM prks.devices WHERE id = %s", (device,)),
                         [{"user_id": self.actor.context.user_id}])
        other = self.other_actor()
        with facade_context.acting_as(other.context):
            other_work = self.library.add_work("Theirs")
        with self.assertRaises(AccessRefused) as caught:
            self.send(self.title_op(other_work, "Stolen", 0, device_id=device), other.context)
        self.assertIs(caught.exception.refusal, Refusal.FORBIDDEN)

    def test_a_reader_cannot_send_operations(self) -> None:
        work_id = self.library.add_work("Work")
        reader = self.reader_context()
        with self.assertRaises(AccessRefused) as caught:
            self.send(self.title_op(work_id, "Read only", 0), reader)
        self.assertIs(caught.exception.refusal, Refusal.FORBIDDEN)
        self.assertEqual(self.ledger(), [])

    def test_an_exception_rolls_the_claim_back(self) -> None:
        # The default filing fails at the playlist, after it created the root
        # "Uncategorized" folder in the same transaction.
        default = Environment(pdfs_dir=self.storage.pdfs_dir)
        data = self.envelope("CREATE_WORK", "W-" + uuid.uuid4().hex.upper(),
                             create_payload(playlist_id="PL-" + "0" * 32))
        with mock.patch.object(playlists, "lock", side_effect=RuntimeError("boom")), \
                self.assertRaises(RuntimeError):
            protocol.process_operation(self.db, self.actor.context, data, default)
        self.assertEqual((self.ledger(), self.count("works"), self.count("folders")), ([], 0, 0))
        # The op_id was not burnt.
        self.assertEqual(self.send(data)[1]["changed"], True)

    def test_families_without_a_handler_are_not_ported(self) -> None:
        work_id = self.library.add_work("Work")
        # Every SQLite family has a handler now; one without is refused, typed.
        with mock.patch.dict(protocol._HANDLERS), self.assertRaises(NotPortedError):
            del protocol._HANDLERS["MARK_WORK_OPENED"]
            self.send(self.envelope("MARK_WORK_OPENED", work_id, {}))
        self.assertEqual(self.send(self.envelope("NO_SUCH_OPERATION", work_id, {})),
                         (400, {"code": "INVALID_ENVELOPE"}))
        self.assertEqual(self.ledger(), [])

    def test_the_ledger_row(self) -> None:
        work_id = self.library.add_work("Paper", file_path=self.store_pdf("p.pdf", b"%PDF-1.4\n"))
        data = self.envelope("DELETE_WORK", work_id, {})
        status, result = self.send(data)
        self.assertEqual((status, result["file_path"]), (200, "/api/pdfs/p.pdf"))
        (row,) = self.ledger()
        self.assertEqual(row, {"op_id": data["op_id"], "library_id": self.actor.library_id,
                               "user_id": self.actor.user_id, "status": "ACKNOWLEDGED", "http_status": 200,
                               "result": {"code": "ACKNOWLEDGED", "work_id": work_id, "changed": True}})
        stored = self.sql("SELECT device_id::text AS device, operation_type, entity_type, entity_id,"
                          " encode(request_sha256, 'hex') AS digest FROM prks.sync_operations")
        normalized = sync_protocol.normalize_envelope(data)
        self.assertEqual(stored, [{"device": self.device, "operation_type": "DELETE_WORK",
                                   "entity_type": "work", "entity_id": work_id,
                                   "digest": sync_protocol.request_hash(normalized)}])


class ConcurrentOperationTests(SyncTestCase):
    def test_writers_of_one_field_on_one_base(self) -> None:
        work_id = self.library.add_work("Work")
        results = self.concurrently([
            (lambda value=value: self.send(self.title_op(work_id, value, 0))) for value in "ABCDE"
        ])
        self.assertEqual(sorted(result[1]["code"] for result in results),
                         ["ACKNOWLEDGED"] + ["REVISION_CONFLICT"] * 4)
        self.assertEqual(len(self.ledger()), 5)

    def test_opens_from_many_devices_keep_the_latest(self) -> None:
        work_id = self.library.add_work("Work")
        moments = ["2026-09-11T10:00:00.%03dZ" % ms for ms in (5, 900, 250, 640, 1)]
        results = self.concurrently([
            (lambda moment=moment: self.send(self.envelope("MARK_WORK_OPENED", work_id, {},
                                                           occurred_at=moment, created_at=moment)))
            for moment in moments
        ])
        self.assertTrue(all(result[0] == 200 for result in results))
        self.assertEqual(self.library.get_work(work_id)["last_opened_at"], "2026-09-11 10:00:00.900")

    def test_a_delete_and_an_edit_of_one_work(self) -> None:
        for _ in range(5):
            work_id = self.library.add_work("Work")
            results = self.concurrently([
                lambda work_id=work_id: self.send(self.envelope("DELETE_WORK", work_id, {})),
                lambda work_id=work_id: self.send(self.title_op(work_id, "Edited", 0)),
            ])
            self.assertEqual(results[0][1]["changed"], True)
            edit = results[1][1]["code"]
            self.assertIn(edit, ("ACKNOWLEDGED", "ENTITY_NOT_FOUND"))
            # Whichever came first, the field scope ends tombstoned, never live.
            revisions = self.revisions_of(work_id)
            self.assertEqual(revisions, self.tombstoned(work_id) if edit == "ENTITY_NOT_FOUND" else
                             self.tombstoned(work_id, {("work-field", '["%s","title"]' % work_id): 2}))


class CreateWorkTests(SyncTestCase):
    maxDiff = None

    def test_a_second_create_of_one_id_converges(self) -> None:
        work_id = "W-" + uuid.uuid4().hex.upper()
        self.assertEqual(self.send(self.envelope("CREATE_WORK", work_id, create_payload()))[1]["changed"], True)
        again = self.send(self.envelope("CREATE_WORK", work_id, create_payload(title="Other")))
        self.assertEqual((again[0], again[1]["changed"]), (200, False))
        self.assertEqual(self.library.get_work(work_id)["title"], "A talk")
        self.assertEqual(self.filing.filed, [work_id])

    def test_concurrent_creates_of_one_id_make_one_work(self) -> None:
        work_id = "W-" + uuid.uuid4().hex.upper()
        results = self.concurrently([
            (lambda title=title: self.send(self.envelope("CREATE_WORK", work_id, create_payload(title=title))))
            for title in ("One", "Two", "Three", "Four")
        ])
        self.assertEqual(sorted(result[1]["changed"] for result in results), [False, False, False, True])
        self.assertEqual((self.count("works"), self.count("manifestations"), self.count("assets")), (1, 1, 1))

    def test_refusals(self) -> None:
        work_id = "W-" + uuid.uuid4().hex.upper()
        refused = self.send(self.envelope("CREATE_WORK", work_id, create_payload(folder_id="F-MISSING")))
        self.assertEqual(refused, (404, {"code": "FOLDER_NOT_FOUND", "work_id": work_id}))
        self.assertEqual(self.count("works"), 0)
        self.assertEqual(len(self.ledger()), 1)  # a terminal refusal is an outcome, so it is ledgered
        for data in (
            self.envelope("CREATE_WORK", work_id, create_payload(source={"kind": "video", "url": "x"})),
            # A PDF Work is uploaded, never created by sync.
            self.envelope("CREATE_WORK", work_id, create_payload(source={"kind": "pdf",
                                                                          "url": "https://example.com/a.pdf"})),
            self.envelope("CREATE_WORK", work_id, create_payload(status="Someday")),
            self.envelope("CREATE_WORK", work_id, create_payload(doc_type="book")),
            self.envelope("CREATE_WORK", work_id, create_payload(), 0),
            self.envelope("CREATE_WORK", "W-not-distributed", create_payload()),
        ):
            with self.subTest(data=data["payload"]):
                self.assertEqual(self.send(data)[0], 400)
        self.assertEqual((self.count("works"), len(self.ledger())), (0, 1))

    def test_another_librarys_id_is_not_this_librarys_work(self) -> None:
        work_id = "W-" + uuid.uuid4().hex.upper()
        self.send(self.envelope("CREATE_WORK", work_id, create_payload()))
        other = self.other_actor()
        with facade_context.acting_as(other.context):
            self.assertIsNone(self.library.get_work(work_id))
        data = dict(self.envelope("CREATE_WORK", work_id, create_payload()),
                    device_id=str(other.context.device_id))
        # Ids are unique across Libraries: the other Library is refused, unledgered.
        with self.assertRaises(AccessRefused) as caught:
            self.send(data, other.context)
        self.assertIs(caught.exception.refusal, Refusal.FORBIDDEN)
        self.assertEqual(len(self.ledger()), 1)
        self.assertEqual(self.count("works"), 1)


def every_edit(case: SyncTestCase, work_id: str, base: int) -> list[dict[str, Any]]:
    """One operation of each Works family that edits an existing Work."""
    return [
        case.title_op(work_id, "Late", base),
        case.envelope("SET_WORK_RESEARCH_NOTE", work_id, {"text": "late"}, base),
        case.envelope("SET_WORK_PRIVATE_NOTE", work_id, {"text": "late"}, base),
        case.envelope("SET_WORK_SOURCE", work_id, {"source": {"kind": "video", "url": _OTHER_VIDEO}}, base),
        case.envelope("MARK_WORK_OPENED", work_id, {}),
    ]


class DeleteAgainstPendingTests(SyncTestCase):
    def test_operations_queued_before_a_delete_find_nothing(self) -> None:
        work_id = self.library.add_work("Work", source_url=_VIDEO)
        self.assertEqual(self.send(self.title_op(work_id, "Seen", 0))[0], 200)
        self.assertEqual(self.send(self.envelope("DELETE_WORK", work_id, {}))[1]["changed"], True)
        tombstoned = self.revisions_of(work_id)
        for data in every_edit(self, work_id, 1):
            with self.subTest(operation=data["operation"]):
                status, result = self.send(data)
                self.assertEqual((status, result["code"]), (404, "ENTITY_NOT_FOUND"))
        # Nothing came back: no Work, no note, no live revision scope.
        self.assertEqual((self.count("works"), self.count("manifestations"), self.count("assets"),
                          self.count("work_notes")), (0, 0, 0, 0))
        self.assertEqual(self.revisions_of(work_id), tombstoned)

    def test_a_delete_racing_each_kind_of_edit(self) -> None:
        for index in range(5):
            work_id = self.library.add_work("Work", source_url=_VIDEO)
            edit = every_edit(self, work_id, 0)[index]
            results = self.concurrently([
                lambda work_id=work_id: self.send(self.envelope("DELETE_WORK", work_id, {})),
                lambda edit=edit: self.send(edit),
            ])
            with self.subTest(operation=edit["operation"]):
                self.assertEqual(results[0][1]["changed"], True)
                self.assertIn(results[1][1]["code"], ("ACKNOWLEDGED", "ENTITY_NOT_FOUND"))
                self.assertEqual(self.count("works"), 0)
                self.assertEqual(self.count("work_notes"), 0)

    def test_a_base_from_before_a_delete_never_lands_on_a_recreated_work(self) -> None:
        # Client-minted ids let CREATE_WORK bring a deleted id back. The
        # tombstones outlive the Work (section 5.2), so an edit queued against
        # the first Work conflicts with the second instead of overwriting it.
        # SQLite keeps no tombstone and applies it: a deliberate difference.
        work_id = "W-" + uuid.uuid4().hex.upper()
        self.send(self.envelope("CREATE_WORK", work_id, create_payload()))
        self.assertEqual(self.send(self.title_op(work_id, "First", 0))[1]["server_revision"], 1)
        self.send(self.envelope("DELETE_WORK", work_id, {}))
        self.assertEqual(self.send(self.envelope("CREATE_WORK", work_id, create_payload(title="Again")))[1]
                         ["changed"], True)
        status, result = self.send(self.title_op(work_id, "Queued before the delete", 1))
        self.assertEqual((status, result["code"], result["current_revision"]), (409, "REVISION_CONFLICT", 2))
        self.assertEqual(self.library.get_work(work_id)["title"], "Again")
        self.assertEqual(self.send(self.title_op(work_id, "Seen after", 2))[1]["server_revision"], 3)


# A value for each synchronized field that differs from what
# `RecreatedWorkTests.recreate` stores, so a stale write is a real conflict.
_STALE_VALUES = {
    "status": "Paused", "doc_type": "book", "thumb_page": "7", "year": "1999",
    "published_date": "1999-01", "source_url": "https://example.org/stale",
}


class RecreatedWorkTests(SyncTestCase):
    """#790: a delete tombstones every Work scope, edited or not (section 5.2).

    A never-edited scope had no row and read as revision 0, the base a Work
    recreated under the same client-minted id starts from, so a base-0
    operation queued against the deleted Work used to land on the new one.
    """

    maxDiff = None

    def recreate(self, work_id: str) -> None:
        """A PDF Work under ``work_id``: every field, both notes and an Asset to hold thumb_page."""
        name = "again-%s.pdf" % uuid.uuid4().hex
        self.store_pdf(name, b"%PDF-1.4\n")
        objects = self.library._objects()
        self.run_command(lambda scope: commands.create_work(scope, commands.NewWork(
            work_id=work_id, manifestation_id=generate("MF"), title="Again",
            bibliographic={"doc_type": "article"}, asset_id=generate("AS"),
            asset=commands.NewAsset(source=commands.ManagedFile(
                storage_locator=name, sha256="0" * 64, byte_size=9))), objects=objects))

    def deleted_and_recreated(self) -> str:
        work_id = "W-" + uuid.uuid4().hex.upper()
        self.recreate(work_id)
        self.assertIsNotNone(self.library.delete_work_record(work_id))
        self.recreate(work_id)
        return work_id

    def test_a_stale_base_0_edit_of_any_field_conflicts_with_the_recreated_work(self) -> None:
        work_id = self.deleted_and_recreated()
        before = self.library.get_work(work_id)
        for field in sorted(work_metadata_sync.SYNCED_FIELDS):
            with self.subTest(field=field):
                status, result = self.field(work_id, field, _STALE_VALUES.get(field, "Stale"), 0)
                self.assertEqual((status, result["code"], result["current_revision"]),
                                 (409, "REVISION_CONFLICT", 1))
        self.assertEqual(self.library.get_work(work_id), before)
        # A base observed after the recreation is current and applies.
        for field in sorted(work_metadata_sync.SYNCED_FIELDS):
            with self.subTest(field=field):
                status, result = self.field(work_id, field, _STALE_VALUES.get(field, "Stale"), 1)
                self.assertEqual((status, result["code"], result["server_revision"]), (200, "ACKNOWLEDGED", 2))

    def test_a_stale_base_0_note_conflicts_with_the_recreated_work(self) -> None:
        work_id = self.deleted_and_recreated()
        for kind in ("research", "private"):
            with self.subTest(note=kind):
                status, result = self.note(work_id, kind, "Queued before the delete", 0)
                self.assertEqual((status, result["code"], result["current_revision"]),
                                 (409, "REVISION_CONFLICT", 1))
                status, result = self.note(work_id, kind, "Seen after", 1)
                self.assertEqual((status, result["code"], result["server_revision"]), (200, "ACKNOWLEDGED", 2))
        self.assertEqual(self.count("work_notes"), 2)

    def test_stale_base_0_operations_by_sync_never_land_on_a_recreated_work(self) -> None:
        work_id = "W-" + uuid.uuid4().hex.upper()
        self.send(self.envelope("CREATE_WORK", work_id, create_payload()))
        self.send(self.envelope("DELETE_WORK", work_id, {}))
        self.assertEqual(self.send(self.envelope("CREATE_WORK", work_id, create_payload(title="Again")))[1]
                         ["changed"], True)
        before = self.library.get_work(work_id)
        expected = {"SET_WORK_METADATA_FIELD": "REVISION_CONFLICT", "SET_WORK_RESEARCH_NOTE": "REVISION_CONFLICT",
                    "SET_WORK_PRIVATE_NOTE": "REVISION_CONFLICT", "SET_WORK_SOURCE": "SOURCE_REVISION_CONFLICT"}
        stale = [data for data in every_edit(self, work_id, 0) if data["operation"] in expected]
        for data in stale:
            with self.subTest(operation=data["operation"]):
                status, result = self.send(data)
                self.assertEqual((status, result["code"], result["current_revision"]),
                                 (409, expected[data["operation"]], 1))
        self.assertEqual(self.library.get_work(work_id), before)
        for data in every_edit(self, work_id, 1):
            if data["operation"] in expected:
                with self.subTest(operation=data["operation"]):
                    status, result = self.send(data)
                    self.assertEqual((status, result["code"], result["server_revision"]),
                                     (200, "ACKNOWLEDGED", 2))

    def test_edited_scopes_keep_advancing_across_deletes(self) -> None:
        work_id = "W-" + uuid.uuid4().hex.upper()
        title = ("work-field", '["%s","title"]' % work_id)
        self.recreate(work_id)
        self.assertEqual(self.field(work_id, "title", "One", 0)[1]["server_revision"], 1)
        self.assertEqual(self.field(work_id, "title", "Two", 1)[1]["server_revision"], 2)
        self.library.delete_work_record(work_id)
        self.assertEqual(self.revisions_of(work_id), self.tombstoned(work_id, {title: 3}))
        self.recreate(work_id)
        self.assertEqual(self.field(work_id, "title", "Three", 2)[1]["code"], "REVISION_CONFLICT")
        self.assertEqual(self.field(work_id, "title", "Three", 3)[1]["server_revision"], 4)
        self.library.delete_work_record(work_id)
        # Each delete moves every scope on by exactly one: never back, never twice.
        self.assertEqual(self.revisions_of(work_id),
                         {**{key: 2 for key in commands.work_scopes(work_id)}, title: 5})

    def test_deleting_an_absent_work_changes_no_revision(self) -> None:
        work_id = self.deleted_and_recreated()
        self.library.delete_work_record(work_id)
        tombstoned = self.revisions_of(work_id)
        total = self.count("entity_revisions")
        self.assertIsNone(self.library.delete_work_record(work_id))
        self.assertEqual(self.send(self.envelope("DELETE_WORK", work_id, {}))[1]["changed"], False)
        self.assertEqual(self.revisions_of(work_id), tombstoned)
        never = "W-" + uuid.uuid4().hex.upper()
        self.assertIsNone(self.library.delete_work_record(never))
        self.assertEqual(self.revisions_of(never), {})
        self.assertEqual(self.count("entity_revisions"), total)

    def test_a_delete_tombstones_only_its_own_work_in_its_own_library(self) -> None:
        work_id = self.library.add_work("Deleted")
        neighbour = self.library.add_work("Neighbour")
        self.field(neighbour, "title", "Kept", 0)
        other = self.other_actor()
        with facade_context.acting_as(other.context):
            theirs = self.library.add_work("Theirs")
        self.run_command(lambda scope: commands.delete_work(scope, theirs) and None, other.context)
        their_rows = self.sql("SELECT scope_type, scope_id, revision FROM prks.entity_revisions"
                              " WHERE library_id = %s ORDER BY 1, 2", (other.library_id,))
        self.library.delete_work_record(work_id)
        self.assertEqual(self.revisions_of(work_id), self.tombstoned(work_id))
        self.assertEqual(self.revisions_of(neighbour), {("work-field", '["%s","title"]' % neighbour): 1})
        self.assertEqual(self.sql("SELECT scope_type, scope_id, revision FROM prks.entity_revisions"
                                  " WHERE library_id = %s ORDER BY 1, 2", (other.library_id,)), their_rows)
        self.assertEqual({row["library_id"] for row in self.sql(
            "SELECT DISTINCT library_id FROM prks.entity_revisions WHERE scope_id = %s OR scope_id LIKE %s",
            (work_id, '["' + work_id + '"%'))}, {self.actor.library_id})

    def test_concurrent_deletes_tombstone_each_scope_once(self) -> None:
        work_id = self.library.add_work("Work", source_url=_VIDEO)
        self.field(work_id, "title", "Edited", 0)
        results = self.concurrently([
            (lambda: self.send(self.envelope("DELETE_WORK", work_id, {}))) for _ in range(4)
        ])
        self.assertEqual(sorted(result[1]["changed"] for result in results), [False, False, False, True])
        self.assertEqual(self.revisions_of(work_id),
                         self.tombstoned(work_id, {("work-field", '["%s","title"]' % work_id): 2}))

    def test_an_edit_of_a_never_edited_scope_waits_for_the_delete(self) -> None:
        work_id = self.library.add_work("Work", source_url=_VIDEO)
        deleted, edit = self.hold_work_then(
            lambda scope: commands.delete_work(scope, work_id),
            lambda scope: notes.apply_note_operation(scope, notes.rules.RESEARCH_OPERATION, work_id, "late", 0))
        self.assertIsNotNone(deleted)
        self.assertEqual(edit[1]["code"], "ENTITY_NOT_FOUND")
        self.assertEqual(self.revisions_of(work_id), self.tombstoned(work_id))


class IsolationTests(SyncTestCase):
    def test_another_library_cannot_change_a_work_by_sync(self) -> None:
        work_id = self.library.add_work("Mine", source_url=_VIDEO, private_notes="kept")
        other = self.other_actor()
        device = str(other.context.device_id)
        for data in every_edit(self, work_id, 0):
            with self.subTest(operation=data["operation"]):
                status, result = self.send(dict(data, device_id=device), other.context)
                self.assertEqual((status, result["code"]), (404, "ENTITY_NOT_FOUND"))
        deleted = self.send(self.envelope("DELETE_WORK", work_id, {}, device_id=device), other.context)
        self.assertEqual(deleted, (200, {"code": "ACKNOWLEDGED", "work_id": work_id, "changed": False}))
        work = self.library.get_work(work_id)
        self.assertEqual((work["title"], work["private_notes"], work["last_opened_at"]), ("Mine", "kept", None))
        self.assertEqual(self.revisions_of(work_id), {})
        # The other Library's answers are its own ledger rows, never this one's.
        self.assertEqual({row["library_id"] for row in self.ledger()}, {other.library_id})

    def test_a_worker_without_a_person_cannot_send_operations(self) -> None:
        work_id = self.library.add_work("Work")
        lease_id = uuid.uuid4()
        (job,) = self.sql(
            "INSERT INTO prks.jobs (library_id, library_epoch, kind, state, lease_id, locked_until)"
            " VALUES (%s, 1, 'test.edit', 'running', %s, now() + interval '5 minutes') RETURNING id",
            (self.actor.library_id, lease_id))
        worker = RequestContext.for_job(self.db, job["id"], lease_id)
        assert worker is not None
        with self.assertRaises(AccessRefused) as caught:
            self.send(self.title_op(work_id, "Job", 0), worker)
        self.assertIs(caught.exception.refusal, Refusal.FORBIDDEN)
        self.assertEqual((self.ledger(), self.library.get_work(work_id)["title"]), ([], "Work"))


class EpochTests(SyncTestCase):
    def context_at(self, epoch: int) -> RequestContext:
        from backend.domain.identity import sessions

        new = self.db.command(lambda tx: sessions.create_session(
            tx, user_id=self.actor.user_id, library_id=self.actor.library_id))
        valid = sessions.resolve_session(self.db, new.token.reveal())
        assert valid is not None
        return RequestContext.for_session(valid, epoch=epoch, device_id=self.actor.device_id)

    def test_an_operation_of_the_old_epoch_applies_once_resent_for_the_new_one(self) -> None:
        work_id = self.library.add_work("Work")
        self.sql("UPDATE prks.libraries SET epoch = epoch + 1 WHERE id = %s", (self.actor.library_id,))
        for data in every_edit(self, work_id, 0) + [self.envelope("DELETE_WORK", work_id, {})]:
            with self.subTest(operation=data["operation"]):
                with self.assertRaises(AccessRefused) as caught:
                    self.send(data)
                self.assertIs(caught.exception.refusal, Refusal.LIBRARY_EPOCH_CHANGED)
        self.assertEqual((self.ledger(), self.library.get_work(work_id)["title"]), ([], "Work"))
        self.assertEqual(self.revisions_of(work_id), {})
        # The op_id was not burnt: the client resends it for the new epoch.
        data = self.title_op(work_id, "Resent", 0)
        with self.assertRaises(AccessRefused):
            self.send(data)
        self.assertEqual(self.send(data, self.context_at(2))[1]["server_revision"], 1)

    def test_a_newer_epoch_than_the_librarys_is_refused(self) -> None:
        work_id = self.library.add_work("Work")
        with self.assertRaises(AccessRefused) as caught:
            self.send(self.title_op(work_id, "Ahead", 0), self.context_at(2))
        self.assertIs(caught.exception.refusal, Refusal.LIBRARY_EPOCH_CHANGED)
        self.assertEqual(self.ledger(), [])


class FailureTests(SyncTestCase):
    def failing_after(self, operation: str, handler: FamilyHandler) -> Any:
        def apply(scope: LibraryScope, op: dict[str, Any], received_at: Any, env: Environment) -> Result:
            handler.apply(scope, op, received_at, env)
            raise RuntimeError("fails after the write")

        broken = FamilyHandler(handler.validate_fn, apply, handler.isolation, handler.ledger_fn)
        return mock.patch.dict(protocol._HANDLERS, {operation: broken})

    def test_an_edit_that_fails_after_writing_leaves_nothing_and_retries(self) -> None:
        work_id = self.library.add_work("Work")
        data = self.title_op(work_id, "Once", 0)
        with self.failing_after("SET_WORK_METADATA_FIELD", work_metadata.HANDLER):
            with self.assertRaises(RuntimeError):
                self.send(data)
        self.assertEqual((self.ledger(), self.library.get_work(work_id)["title"]), ([], "Work"))
        self.assertEqual(self.revisions_of(work_id), {})
        self.assertEqual(self.send(data)[1]["server_revision"], 1)
        self.assertEqual(self.send(data)[1]["server_revision"], 1)  # and then replays

    def test_a_delete_that_fails_after_deleting_leaves_the_work(self) -> None:
        work_id = self.library.add_work("Work", source_url=_VIDEO, private_notes="kept")
        data = self.envelope("DELETE_WORK", work_id, {})
        with self.failing_after("DELETE_WORK", work_lifecycle.DELETE_HANDLER):
            with self.assertRaises(RuntimeError):
                self.send(data)
        self.assertEqual(self.library.get_work(work_id)["private_notes"], "kept")
        self.assertEqual((self.ledger(), self.revisions_of(work_id)), ([], {}))
        self.assertEqual(self.send(data)[1]["changed"], True)
        self.assertIsNone(self.library.get_work(work_id))


class NoteConflictTests(SyncTestCase):
    def test_writers_of_one_note_on_one_base(self) -> None:
        for operation in ("SET_WORK_RESEARCH_NOTE", "SET_WORK_PRIVATE_NOTE"):
            work_id = self.library.add_work("Work")
            results = self.concurrently([
                (lambda text=text, operation=operation, work_id=work_id:
                 self.send(self.envelope(operation, work_id, {"text": text}, 0)))
                for text in ("a", "b", "c", "d")
            ])
            with self.subTest(operation=operation):
                self.assertEqual(sorted(result[1]["code"] for result in results),
                                 ["ACKNOWLEDGED"] + ["REVISION_CONFLICT"] * 3)
                winner = next(result[1] for result in results if result[1]["code"] == "ACKNOWLEDGED")
                losers = [result[1] for result in results if result[1]["code"] != "ACKNOWLEDGED"]
                # Every loser is told the winner's revision, and nothing overwrote the winner.
                self.assertTrue(all(loser["current_revision"] == winner["server_revision"] for loser in losers))
                state = self.library.get_work_notes_state(work_id)
                key = "research_note_revision" if operation == "SET_WORK_RESEARCH_NOTE" else "private_note_revision"
                self.assertEqual(state[key], 1)


class OpenSyncTests(SyncTestCase):
    """``MARK_WORK_OPENED`` as a max-register over event time (the retired ``tests/test_work_open_sync.py``)."""

    def setUp(self) -> None:
        super().setUp()
        self.work = self.library.add_work("Opened work")

    def opened(self, occurred_at: str, work_id: Optional[str] = None, **changes: Any) -> dict[str, Any]:
        return self.envelope("MARK_WORK_OPENED", work_id or self.work, {}, occurred_at=occurred_at,
                             created_at=occurred_at, **changes)

    def opened_at(self, work_id: Optional[str] = None) -> Optional[str]:
        return self.library.get_work(work_id or self.work)["last_opened_at"]

    def test_the_latest_event_wins_whatever_the_arrival_order_or_zone(self) -> None:
        self.assertIsNone(self.opened_at())
        status, result = self.send(self.opened("2026-09-11T10:00:00Z"))
        self.assertEqual((status, result["code"], result["changed"]), (200, "ACKNOWLEDGED", True))
        self.assertEqual(self.opened_at(), "2026-09-11 10:00:00.000")
        self.assertTrue(self.send(self.opened("2026-09-11T11:00:00Z"))[1]["changed"])
        for older in ("2026-09-11T10:00:00Z", "2026-09-11T11:00:00Z", "2026-09-11T07:00:00-03:00"):
            with self.subTest(older=older):
                status, result = self.send(self.opened(older))
                self.assertEqual((status, result["changed"], result["effective_opened_at"]),
                                 (200, False, "2026-09-11 11:00:00.000"))
        mirror = self.library.add_work("Mirror")
        for occurred in ("2026-09-11T15:00:00+02:00", "2026-09-11T12:00:00Z"):
            self.send(self.opened(occurred, mirror))
        self.assertEqual(self.opened_at(mirror), "2026-09-11 13:00:00.000")
        # Offline for years is no reason to refuse the event.
        old = self.library.add_work("Old")
        self.assertEqual(self.send(self.opened("2019-01-02T03:04:05Z", old))[1]["changed"], True)
        self.assertEqual(self.opened_at(old), "2019-01-02 03:04:05.000")

    def test_a_far_future_event_is_clamped_and_sub_second_events_do_not_tie(self) -> None:
        far = (datetime.now(timezone.utc) + timedelta(days=365)).isoformat()
        result = self.send(self.opened(far))[1]
        self.assertTrue(result["changed"])
        self.assertLess(result["effective_opened_at"],
                        work_open_sync.format_moment(datetime.now(timezone.utc) + timedelta(minutes=1)))
        other = self.library.add_work("Precise")
        for millis in (0, 250, 900):
            moment = datetime(2026, 9, 11, 12, 0, 0, millis * 1000, tzinfo=timezone.utc)
            self.send(self.opened(moment.isoformat(), other))
        self.assertEqual(self.opened_at(other), "2026-09-11 12:00:00.900")

    def test_malformed_envelopes_are_refused_unledgered_and_a_missing_work_is_terminal(self) -> None:
        for field, value in (("payload", {"tag_id": "T-1"}), ("base_revision", 0), ("base_revision", 7),
                             ("entity_type", "tag"), ("occurred_at", "yesterday"),
                             ("depends_on", [str(uuid.uuid4())])):
            with self.subTest(field=field, value=value):
                self.assertEqual(self.send(self.opened("2026-09-11T10:00:00Z") | {field: value})[0], 400)
        self.assertEqual((self.opened_at(), self.ledger()), (None, []))
        missing = self.opened("2026-09-11T10:00:00Z", generate("W"))
        first = self.send(missing)
        self.assertEqual((first[0], first[1]["code"]), (404, "ENTITY_NOT_FOUND"))
        self.assertEqual(self.send(missing), first)

    def test_the_ack_carries_the_recent_row_and_a_replay_answers_from_the_ledger(self) -> None:
        data = self.opened("2026-09-11T10:00:00Z")
        original = self.send(data)
        row = original[1]["recent_item"]
        self.assertEqual((row["id"], row["last_opened_at"]), (self.work, "2026-09-11 10:00:00.000"))
        self.assertEqual([row], [item for item in self.library.get_recent_browse() if item["id"] == self.work])
        self.send(self.opened("2026-09-11T18:00:00Z"))
        self.assertEqual(self.send(data), original)
        self.assertEqual(self.opened_at(), "2026-09-11 18:00:00.000")
        self.assertEqual(self.send(data | {"occurred_at": "2026-09-11T20:00:00Z"}), (409, {"code": "OP_ID_REUSE"}))
        self.assertEqual((self.opened_at(), len(self.ledger())), ("2026-09-11 18:00:00.000", 2))

    def test_the_direct_open_shares_the_register_and_a_read_stays_pure(self) -> None:
        self.assertTrue(self.library.mark_work_opened(self.work))
        self.assertRegex(self.opened_at() or "", r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}$")
        tomorrow = work_open_sync.format_moment(datetime.now(timezone.utc) + timedelta(days=1))
        self.sql("UPDATE prks.works SET last_opened_at = %s::timestamptz WHERE id = %s", (tomorrow + "+00", self.work))
        self.assertTrue(self.library.mark_work_opened(self.work))
        self.assertEqual(self.opened_at(), tomorrow)
        self.assertFalse(self.library.mark_work_opened(generate("W")))
        self.library.get_work(self.work)
        self.library.get_work(self.work)
        self.assertEqual(self.opened_at(), tomorrow)


class NoteSyncTests(SyncTestCase):
    """The two Work notes as independent aggregates (the retired ``tests/test_work_note_sync.py``)."""

    def setUp(self) -> None:
        super().setUp()
        self.work = self.library.add_work("Noted work")

    def note(self, kind: str, text: Any, base: Optional[int] = 0, work_id: Optional[str] = None,
             **changes: Any) -> dict[str, Any]:
        operation = "SET_WORK_RESEARCH_NOTE" if kind == "research" else "SET_WORK_PRIVATE_NOTE"
        return self.envelope(operation, work_id or self.work, {"text": text}, base, **changes)

    def state(self) -> tuple[int, int]:
        state = self.library.get_work_notes_state(self.work)
        return state["research_note_revision"], state["private_note_revision"]

    def test_the_two_notes_advance_their_own_revisions_on_either_path(self) -> None:
        fresh = self.library.add_work("Untouched")
        self.assertEqual(self.library.get_work_notes_state(fresh),
                         {"work_id": fresh, "research_note_revision": 0, "private_note_revision": 0})
        self.assertIsNone(self.library.get_work_notes_state(generate("W")))
        self.send(self.note("research", "Research A"))
        self.send(self.note("private", "Private A"))
        self.assertEqual(self.state(), (1, 1))
        self.send(self.note("research", "Research B", 1))
        self.assertEqual(self.state(), (2, 1))
        self.send(self.note("private", "Private B", 1))
        self.assertEqual(self.state(), (2, 2))
        work = self.library.get_work(self.work)
        self.assertEqual((work["text_content"], work["private_notes"]), ("Research B", "Private B"))
        self.library.update_work_metadata(self.work, {"private_notes": "Reminder via PATCH"})
        self.assertEqual(self.state(), (2, 3))
        self.library.save_work_notes(self.work, "Research via PATCH")
        self.assertEqual(self.state(), (3, 3))

    def test_acknowledgements_and_conflicts_carry_sizes_never_prose(self) -> None:
        body, other = "Secret prose that must never appear in a durable result.", "A different secret."
        for kind in ("research", "private"):
            with self.subTest(kind=kind):
                status, result = self.send(self.note(kind, body))
                self.assertEqual((status, result["value_omitted"]), (200, True))
                self.assertNotIn(body, json.dumps(result))
                status, result = self.send(self.note(kind, other))
                dumped = json.dumps(result)
                self.assertEqual((status, result["code"], result["current_revision"], result["current_bytes"],
                                  result["requested_bytes"]),
                                 (409, "REVISION_CONFLICT", 1, len(body.encode()), len(other.encode())))
                for secret in (body, other):
                    self.assertNotIn(secret, dumped)
                for key in ("current_value", "current_preview", "text"):
                    self.assertNotIn(key, result)
                self.assertLessEqual(len(dumped.encode()), 2048)
                # The same text from a stale base converges at the newer revision.
                status, result = self.send(self.note(kind, body))
                self.assertEqual((status, result["changed"], result["server_revision"]), (200, False, 1))
        self.assertEqual(self.state(), (1, 1))

    def test_refusals_match_the_ordinary_path(self) -> None:
        control = "See [[concept:Bell\x07 Curve]]."
        with self.assertRaises(ResearchError) as caught:
            self.library.save_work_notes(self.work, control)
        self.assertEqual(caught.exception.code, "invalid_text")
        self.assertEqual(self.send(self.note("research", control)), (400, {"code": "INVALID_ENVELOPE"}))
        over_private = "p" * (work_note_sync.MAX_PRIVATE_NOTE_UTF8_BYTES + 1)
        self.assertEqual(self.send(self.note("private", over_private))[0], 400)
        with self.assertRaises(ValueError):
            self.library.update_work_metadata(self.work, {"private_notes": over_private})
        self.assertEqual(self.send(self.note("research", "x", None))[1]["code"], "INVALID_BASE_REVISION")
        extra = self.note("research", "x")
        extra["payload"]["extra"] = 1
        self.assertEqual(self.send(extra), (400, {"code": "INVALID_ENVELOPE"}))
        self.assertEqual(self.send(self.note("private", "x") | {"payload": {}}), (400, {"code": "INVALID_ENVELOPE"}))
        self.assertEqual((self.ledger(), self.library.list_concepts(), self.state()), ([], [], (0, 0)))
        # The Research Note is the large aggregate: past the generic 64 KiB envelope cap it still lands.
        large = "r" * (64 * 1024 + 1)
        self.assertEqual(self.send(self.note("research", large))[0], 200)
        self.assertEqual(len(self.library.get_work(self.work)["text_content"]), 64 * 1024 + 1)
        at_limit = "q" * work_note_sync.MAX_PRIVATE_NOTE_UTF8_BYTES
        self.assertEqual(self.send(self.note("private", at_limit))[0], 200)
        missing = generate("W")
        for kind in ("research", "private"):
            with self.subTest(kind=kind):
                data = self.note(kind, "gone", 0, missing)
                first = self.send(data)
                self.assertEqual((first[0], first[1]["code"], first[1]["work_id"]),
                                 (404, "ENTITY_NOT_FOUND", missing))
                self.assertEqual(self.send(data), first)


_WATCH = "https://www.youtube.com/watch?v=%s"


class SourceSyncTests(SyncTestCase):
    """``SET_WORK_SOURCE`` and source creation (the retired ``tests/test_work_source_sync.py``)."""

    def setUp(self) -> None:
        super().setUp()
        self.work = self.library.add_work("Clip", source_kind="video", source_url=_WATCH % "AAA",
                                          provider="youtube", provider_id="AAA", thumb_url="https://img/AAA.jpg")

    def source_op(self, url: str, base: Optional[int] = 0, work_id: Optional[str] = None) -> dict[str, Any]:
        return self.envelope("SET_WORK_SOURCE", work_id or self.work, {"source": {"kind": "video", "url": url}}, base)

    def columns(self, work_id: Optional[str] = None) -> dict[str, Any]:
        row = self.library.get_work(work_id or self.work)
        return {key: row[key] for key in ("source_kind", "provider", "provider_id", "source_url", "thumb_url")}

    def revision(self, work_id: Optional[str] = None) -> int:
        return self.library.get_work_source_state(work_id or self.work)["revision"]

    def test_refused_envelopes_write_nothing_and_are_never_ledgered(self) -> None:
        extra = self.source_op(_WATCH % "BBB")
        extra["payload"]["extra"] = 1
        self.assertEqual(self.send(extra), (400, {"code": "INVALID_ENVELOPE"}))
        self.assertEqual(self.send(self.source_op(_WATCH % "BBB", None)), (400, {"code": "INVALID_BASE_REVISION"}))
        for bad in ("B" * (work_source_sync.MAX_PROVIDER_ID_CHARS + 1), "ab%22cd", "a%20b", "a.b"):
            with self.subTest(bad=bad[:20]):
                self.assertEqual(self.send(self.source_op(_WATCH % bad))[1]["code"], "INVALID_ENVELOPE")
        self.assertEqual((self.ledger(), self.columns()["provider_id"], self.revision()), ([], "AAA", 0))

    def test_a_change_is_one_revision_and_the_ack_states_the_stored_row(self) -> None:
        for spelling in ("https://youtu.be/AAA", "https://www.youtube.com/embed/AAA", _WATCH % "AAA"):
            with self.subTest(url=spelling):
                self.assertEqual(self.send(self.source_op(spelling))[1]["changed"], False)
        self.assertEqual((self.revision(), self.columns()["source_url"], self.columns()["thumb_url"]),
                         (0, _WATCH % "AAA", "https://img/AAA.jpg"))
        status, result = self.send(self.source_op(_WATCH % "BBB"))
        row = self.library.get_work(self.work)
        self.assertEqual((status, result["changed"], result["source_url"], result["provider_id"], result["provider"],
                          result["source_kind"], result["thumb_url"], result["urldate"]),
                         (200, True, row["source_url"], "BBB", "youtube", "video", None, row["urldate"]))
        self.assertNotIn("value_omitted", result)
        self.assertEqual(self.columns(), {"source_kind": "video", "provider": "youtube", "provider_id": "BBB",
                                          "source_url": _WATCH % "BBB", "thumb_url": None})
        # A stale base naming the same video in another spelling converges on the stored spelling.
        status, result = self.send(self.source_op("https://youtu.be/BBB"))
        self.assertEqual((status, result["changed"], result["source_url"], result["server_revision"]),
                         (200, False, _WATCH % "BBB", 1))
        # The source is its own scope: no metadata field moved.
        fields = self.library.get_work_metadata_state(self.work)["fields"]
        self.assertEqual({entry["revision"] for entry in fields.values()}, {0})
        self.assertEqual(self.send(self.source_op(_WATCH % ("B" * work_source_sync.MAX_PROVIDER_ID_CHARS), 1))[0],
                         200)

    def test_a_conflict_reports_the_identity_exactly_within_the_durable_bound(self) -> None:
        self.send(self.source_op(_WATCH % "BBB"))
        status, result = self.send(self.source_op(_WATCH % "CCC"))
        self.assertEqual((status, result["code"], result["current_revision"], result["current_provider"],
                          result["current_provider_id"]), (409, "SOURCE_REVISION_CONFLICT", 1, "youtube", "BBB"))
        self.assertIn("current_preview", result)
        self.assertNotIn("current_value", result)
        self.assertEqual(self.columns()["provider_id"], "BBB")
        long_id = "B" * 300
        self.sql("UPDATE prks.assets SET url = %s, provider_id = %s WHERE work_id = %s",
                 (_WATCH % long_id + "&pad=" + "x" * 3000, long_id, self.work))
        status, result = self.send(self.source_op(_WATCH % "CCC"))
        self.assertEqual((status, result["current_provider_id"]), (409, long_id))
        self.assertLessEqual(len(json.dumps(result).encode()), 2048)
        self.assertLess(len(result["current_preview"]), 2048)

    def test_a_replay_answers_from_the_ledger_and_a_reuse_is_refused(self) -> None:
        data = self.source_op(_WATCH % "BBB")
        first = self.send(data)
        self.assertEqual((self.send(dict(data)), self.revision()), (first, 1))
        reused = data | {"payload": {"source": {"kind": "video", "url": _WATCH % "CCC"}}}
        self.assertEqual(self.send(reused), (409, {"code": "OP_ID_REUSE"}))

    def test_creation_derives_or_refuses_and_advances_nothing(self) -> None:
        made = self.library.add_work("Derived", source_kind="video", source_url="https://youtu.be/NNN")
        self.assertEqual((self.columns(made)["provider"], self.columns(made)["provider_id"],
                          self.columns(made)["source_url"]), ("youtube", "NNN", "https://youtu.be/NNN"))
        self.assertEqual((self.revision(made), self.revisions_of(made)), (0, {}))
        kindless = self.library.add_work("Kindless", source_url=_WATCH % "KKK")
        self.assertEqual(self.columns(kindless)["source_kind"], "video")
        self.assertEqual(self.send(self.source_op(_WATCH % "LLL", 0, kindless))[1]["code"], "ACKNOWLEDGED")
        spaced = self.library.add_work("Ok", source_kind=" VIDEO ", source_url=_WATCH % "AAA")
        self.assertEqual(self.columns(spaced)["source_kind"], "video")
        self.assertIsNone(self.columns(self.library.add_work("Bare"))["source_kind"])
        for fields in ({"source_kind": "video", "source_url": _WATCH % "BBB", "provider": "youtube",
                        "provider_id": "AAA"},
                       {"source_kind": "video", "source_url": _WATCH % "BBB", "provider": "vimeo"},
                       {"source_kind": "video", "source_url": _WATCH % "CCC",
                        "provider_id": "B" * (work_source_sync.MAX_PROVIDER_ID_CHARS + 1)},
                       {"source_kind": "video", "source_url": _WATCH % "a.b"},
                       {"source_kind": "video", "source_url": _WATCH % "CCC", "provider_id": "a b"},
                       {"source_kind": "video"},
                       {"source_kind": "video", "source_url": "https://example.com/clip.mp4"},
                       {"source_url": "https://example.com/clip"},
                       *({"source_kind": kind, "source_url": _WATCH % "AAA"} for kind in ("web", "audio", "html"))):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.library.add_work("Refused", **fields)

    def test_a_file_makes_a_pdf_and_its_url_stays_provenance(self) -> None:
        for label, url, kind in (("inferred", "https://example.org/paper.pdf", ""),
                                 ("inferred, YouTube-shaped", _WATCH % "AAA", ""),
                                 ("explicit, YouTube-shaped", _WATCH % "AAA", "pdf")):
            with self.subTest(case=label):
                path = self.store_pdf("%s.pdf" % uuid.uuid4().hex, b"%PDF-1.4\n")
                columns = self.columns(self.library.add_work("Paper", file_path=path, source_url=url,
                                                             source_kind=kind))
                self.assertEqual((columns["source_kind"], columns["provider"] or None,
                                  columns["provider_id"] or None, columns["source_url"]), ("pdf", None, None, url))
        path = self.store_pdf("refused.pdf", b"%PDF-1.4\n")
        for kind in ("pdf", ""):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.library.add_work("Filed", file_path=path, source_kind=kind,
                                      source_url="https://example.org/p.pdf", provider_id="AAA")
        pdf = self.library.add_work("Paper", file_path=path)
        self.assertEqual(self.send(self.source_op(_WATCH % "BBB", 0, pdf))[1]["code"],
                         "UNSUPPORTED_SOURCE_TRANSITION")
        self.assertEqual((self.columns(pdf)["source_kind"], self.library.get_work(pdf)["file_path"]),
                         ("pdf", "/api/pdfs/refused.pdf"))

    def test_a_patch_never_writes_the_identity(self) -> None:
        for column, value in (("provider_id", "CCC"), ("provider", "vimeo"), ("source_kind", "pdf"),
                              ("thumb_url", "https://img/CCC.jpg")):
            with self.subTest(column=column):
                with self.assertRaises(ValueError) as caught:
                    self.library.update_work_metadata(self.work, {column: value})
                self.assertIn(column, str(caught.exception))
                self.assertIn("SET_WORK_SOURCE", str(caught.exception))
        with self.assertRaises(ValueError) as caught:
            self.library.update_work_metadata(self.work, {"source_kind": "video", "provider": "youtube",
                                                          "provider_id": "BBB", "title": "Renamed too"})
        for column in ("provider", "provider_id", "source_kind"):
            self.assertIn(column, str(caught.exception))
        self.assertEqual((self.columns()["provider_id"], self.library.get_work(self.work)["title"], self.revision()),
                         ("AAA", "Clip", 0))
        self.library.update_work_metadata(self.work, {"title": "Renamed", "year": "2021"})
        self.assertEqual(self.library.get_work(self.work)["title"], "Renamed")
