"""The PostgreSQL operation ledger and the Works handlers (L-W-1 #628).

``tests/test_works_sync_parity.py`` proves each Works operation answers as
on SQLite. These tests cover what only PostgreSQL has (§7.3, §7.6): one
``op_id`` sent concurrently, the ledger looked up before the epoch, a
ledger row of another account or Library, the device check, a claim rolled
back with its exception, concurrent writers through the protocol, and
``CREATE_WORK`` with a stand-in for the folder filing the Organization lane
will provide.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from typing import Any, Optional

from backend import sync_protocol
from backend.db_manager import PRKSDatabase
from backend.domain.identity.authorization import AccessRefused, Refusal
from backend.domain.identity.library_scope import LibraryScope
from backend.domain.identity.request_context import RequestContext
from backend.pg_facade import NotPortedError
from backend.pg_facade import context as facade_context
from backend.storage.config import StorageConfig
from backend.sync import protocol
from backend.sync.families import Environment, Result
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
        unported = Environment(pdfs_dir=self.storage.pdfs_dir)  # the default filing refuses
        data = self.envelope("CREATE_WORK", "W-" + uuid.uuid4().hex.upper(), create_payload())
        with self.assertRaises(NotPortedError):
            protocol.process_operation(self.db, self.actor.context, data, unported)
        self.assertEqual((self.ledger(), self.count("works")), ([], 0))
        # The op_id was not burnt.
        self.assertEqual(self.send(data)[1]["changed"], True)

    def test_families_without_a_handler_are_not_ported(self) -> None:
        work_id = self.library.add_work("Work")
        with self.assertRaises(NotPortedError):
            self.send(self.envelope("ADD_WORK_TAG", work_id, {"tag": "x"}, 0))
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

    def reader_context(self) -> RequestContext:
        from backend.domain.identity import sessions

        reader = self.other_actor(role="reader")
        self.sql("INSERT INTO prks.library_memberships (library_id, user_id, role) VALUES (%s, %s, 'reader')",
                 (self.actor.library_id, reader.user_id))
        new = self.db.command(lambda tx: sessions.create_session(
            tx, user_id=reader.user_id, library_id=self.actor.library_id))
        valid = sessions.resolve_session(self.db, new.token.reveal())
        assert valid is not None
        return RequestContext.for_session(valid, epoch=1, device_id=reader.context.device_id)


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
            # Whichever came first, the field scope ends tombstoned or absent, never live.
            revisions = self.revisions_of(work_id)
            self.assertEqual(revisions, {} if edit == "ENTITY_NOT_FOUND" else
                             {("work-field", '["%s","title"]' % work_id): 2})


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
