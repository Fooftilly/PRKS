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

import os
import shutil
import tempfile
import uuid
from typing import Any, Optional
from unittest import mock

from backend import sync_protocol, work_metadata_sync
from backend.db_manager import PRKSDatabase
from backend.domain.identity.authorization import AccessRefused, Refusal
from backend.domain.identity.library_scope import LibraryScope
from backend.domain.identity.request_context import RequestContext
from backend.domain.organization import playlists
from backend.domain.works import commands, notes
from backend.entity_ids import generate
from backend.pg_facade import NotPortedError
from backend.pg_facade import context as facade_context
from backend.storage.config import StorageConfig
from backend.sync import protocol
from backend.sync.families import Environment, FamilyHandler, Result
from backend.sync.handlers import work_lifecycle, work_metadata
from tests.pg.test_works_mutations import MutationTestCase
from tests.works_parity_helpers import _Side

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
        for device in (str(other.context.device_id), str(uuid.uuid4())):
            with self.subTest(device=device):
                with self.assertRaises(AccessRefused) as caught:
                    self.send(self.title_op(work_id, "Stolen", 0, device_id=device))
                self.assertIs(caught.exception.refusal, Refusal.FORBIDDEN)
        self.assertEqual((self.ledger(), self.library.get_work(work_id)["title"]), ([], "Work"))

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
        with self.assertRaises(NotPortedError):
            self.send(self.envelope("DELETE_POSITION", "P-" + uuid.uuid4().hex.upper(), {},
                                    entity_type="position"))
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

    def test_a_created_work_matches_sqlite(self) -> None:
        tmp = tempfile.mkdtemp(prefix="prks-pg-sync-ref-")
        self.addCleanup(shutil.rmtree, tmp, True)
        reference = PRKSDatabase(storage=StorageConfig.for_testing(tmp),
                                 schema_path=os.path.join(os.path.dirname(__file__), "..", "..",
                                                          "backend", "db_schema.sql"))
        for payload in (create_payload(), create_payload(title="", status="Planned", abstract="",
                                                         author_text="", private_notes="",
                                                         thumb_url="", doc_type="Online")):
            with self.subTest(payload=payload):
                work_id = "W-" + uuid.uuid4().hex.upper()
                data = self.envelope("CREATE_WORK", work_id, payload)
                mine = self.send(data)
                theirs = sync_protocol.process_operation(reference, dict(data, device_id=str(uuid.uuid4())))
                drop = ("folder_id",)
                self.assertEqual({k: v for k, v in mine[1].items() if k not in drop},
                                 {k: v for k, v in theirs[1].items() if k not in drop})
                pg_side, ref_side = _Side(self.library, self.storage.pdfs_dir, self.device), \
                    _Side(reference, tmp, "")
                for side in (pg_side, ref_side):
                    work = side.db.get_work(work_id)
                    side.labels.update({work_id: "<W>", work["primary_manifestation_id"]: "<MF>"})
                    if work["primary_asset_id"]:
                        side.labels[work["primary_asset_id"]] = "<AS>"
                filed = ("folder", "folder_id", "folder_title", "playlist", "playlist_id", "playlist_title")
                # SQLite's sync insert leaves file_path NULL where add_work writes "";
                # PostgreSQL has no file column and projects "" for both.
                self.assertEqual((self.library.get_work(work_id)["file_path"],
                                  reference.get_work(work_id)["file_path"]), ("", None))
                filed += ("file_path",)
                self.assertEqual(
                    {k: v for k, v in pg_side.normal(self.library.get_work(work_id)).items() if k not in filed},
                    {k: v for k, v in ref_side.normal(reference.get_work(work_id)).items() if k not in filed})
        self.assertEqual(len(self.filing.filed), 2)

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
        self.run_command(lambda scope: commands.create_work(scope, commands.NewWork(
            work_id=work_id, manifestation_id=generate("MF"), title="Again",
            bibliographic={"doc_type": "article"}, asset_id=generate("AS"),
            asset=commands.NewAsset(source=commands.ManagedFile(
                storage_locator="again-%s.pdf" % uuid.uuid4().hex, sha256="0" * 64, byte_size=9)))))

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
