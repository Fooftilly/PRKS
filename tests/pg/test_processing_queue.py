"""The Processing queue on PostgreSQL (L-PR-1 #636; ``docs/postgresql-architecture.md`` §11.5).

``backend/domain/processing``: uploads streamed into fresh managed keys and
staged as drafts; idempotent upload keys whose markers are kept; revisioned
draft edits; import in one SERIALIZABLE transaction that moves the key from
the item to the Asset; delete into a marker that releases the key; and the
object lifecycle seeing the item's key as a reference throughout.
Interleavings are fixed with :meth:`QueueTestCase.hold_then` (no timing).
"""

from __future__ import annotations

import hashlib
import io
import threading
import time
import uuid
from datetime import timedelta
from typing import Any, Callable, Optional
from unittest import mock

from backend.db.database import Isolation
from backend.domain.identity.authorization import Capability
from backend.domain.identity.library_scope import LibraryScope, library_command
from backend.domain.identity.request_context import RequestContext
from backend.domain.objects import lifecycle
from backend.domain.objects.lifecycle import ObjectMissing, Settled
from backend.domain.organization import folders
from backend.domain.people import persons
from backend.domain.processing import commands, imports, items, uploads
from backend.domain.works import merge
from backend.pg_facade import context as facade_context
from backend.storage.objects import NAMESPACE_ASSET_OBJECTS, LocalFilesystemStorage, StorageKey
from tests.pg.test_works_mutations import MutationTestCase

CAP = 10 * 1024 * 1024


def pdf(label: str = "a") -> bytes:
    return b"%PDF-1.4\n% " + label.encode() + b"\n" + b"0" * 64 + b"\n%%EOF\n"


class Unreadable(io.RawIOBase):
    """A body that must never be read: a replay is answered before the body."""

    def read(self, size: int = -1) -> bytes:  # noqa: ARG002 - the signature of a stream
        raise AssertionError("the body was read")


class QueueTestCase(MutationTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.objects = LocalFilesystemStorage.for_config(self.storage)

    # -- operations ---------------------------------------------------------------

    def upload(self, data: bytes, key: Optional[str] = None, filename: str = "paper.pdf",
               ctx: Optional[RequestContext] = None, body: Any = None) -> commands.Uploaded:
        return commands.upload(self.db, ctx or self.actor.context, self.objects,
                               upload_key=uuid.uuid4().hex if key is None else key, filename=filename,
                               body=body if body is not None else io.BytesIO(data),
                               content_length=len(data), max_bytes=CAP)

    def ready(self, data: Optional[bytes] = None, **kwargs: Any) -> dict[str, Any]:
        return self.upload(data or pdf(uuid.uuid4().hex), **kwargs).item

    def edit(self, item_id: str, revision: int, fields: dict[str, Any],
             ctx: Optional[RequestContext] = None) -> dict[str, Any]:
        return commands.edit(self.db, ctx or self.actor.context, item_id, revision, fields)

    def delete(self, item_id: str, ctx: Optional[RequestContext] = None) -> dict[str, Any]:
        return commands.delete(self.db, ctx or self.actor.context, item_id)

    def import_(self, item_id: str, ctx: Optional[RequestContext] = None) -> imports.Imported:
        return commands.import_one(self.db, ctx or self.actor.context, self.objects, item_id)

    def get(self, item_id: str, ctx: Optional[RequestContext] = None) -> Optional[dict[str, Any]]:
        return commands.item(self.db, ctx or self.actor.context, item_id)

    def serializable(self, fn: Callable[[LibraryScope], Any], ctx: Optional[RequestContext] = None) -> Any:
        return library_command(self.db, ctx or self.actor.context, Capability.EDIT_LIBRARY, fn,
                               isolation=Isolation.SERIALIZABLE)

    # -- state --------------------------------------------------------------------

    def row(self, item_id: str) -> dict[str, Any]:
        (row,) = self.sql("SELECT * FROM prks.processing_items WHERE id = %s", (item_id,))
        return row

    def names(self) -> set[str]:
        return {key.name for key in self.objects.iter_keys(NAMESPACE_ASSET_OBJECTS)}

    def claims(self) -> set[str]:
        return {row["name"] for row in self.sql(
            "SELECT name FROM prks.object_cleanup_claims WHERE namespace = 'asset-objects'")}

    def bytes_of(self, name: str) -> bytes:
        with self.objects.open_read(StorageKey(NAMESPACE_ASSET_OBJECTS, name)) as handle:
            return handle.read()

    def asset_of(self, work_id: str) -> dict[str, Any]:
        (row,) = self.sql("SELECT storage_locator, source_locator, content_sha256, ingest_sha256, byte_size, origin,"
                          " thumb_page FROM prks.assets WHERE work_id = %s", (work_id,))
        return row

    def settle_all(self) -> Any:
        return lifecycle.retry_claims(self.db, self.objects)

    def sweep(self) -> int:
        return lifecycle.sweep(self.db, self.objects, grace=timedelta(0), now=time.time() + 60,
                               namespaces=(NAMESPACE_ASSET_OBJECTS,))

    def assert_refused(self, code: str, fn: Callable[[], Any]) -> items.Refused:
        with self.assertRaises(items.Refused) as caught:
            fn()
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def hold_then(self, holder: Callable[[LibraryScope], Any], waiter: Callable[[], Any], *,
                  serializable: bool = False) -> tuple[Any, Any]:
        """Run ``holder`` in a command and keep it open until ``waiter`` (any call) blocks on it."""
        locked, release = threading.Event(), threading.Event()
        outcome: dict[str, Any] = {}
        ctx = self.actor.context
        isolation = Isolation.SERIALIZABLE if serializable else Isolation.READ_COMMITTED

        def hold(scope: LibraryScope) -> Any:
            result = holder(scope)
            locked.set()
            if not release.wait(30):
                raise RuntimeError("never released")
            return result

        def run(name: str, fn: Callable[[], Any]) -> None:
            try:
                with facade_context.acting_as(ctx):
                    outcome[name] = fn()
            except BaseException as exc:  # noqa: BLE001 - returned to the test
                outcome[name] = exc

        first = threading.Thread(target=run, args=("holder", lambda: library_command(
            self.db, ctx, Capability.EDIT_LIBRARY, hold, isolation=isolation)))
        first.start()
        self.assertTrue(locked.wait(30))
        second = threading.Thread(target=run, args=("waiter", waiter))
        second.start()
        try:
            self.wait_for_lock_waiter()
        finally:
            release.set()
            first.join(30)
            second.join(30)
        if isinstance(outcome.get("holder"), BaseException):
            raise outcome["holder"]
        return outcome["holder"], outcome["waiter"]


class UploadTests(QueueTestCase):
    def test_an_upload_is_a_ready_item_with_an_empty_draft_and_the_bytes_as_sent(self) -> None:
        data = pdf()
        with mock.patch("backend.pdf_linearize.linearize_pdf_to", side_effect=AssertionError("linearized")):
            done = self.upload(data, key="k-1", filename="C:\\Users\\me\\Paper One.pdf")
        item = done.item
        row = self.row(item["id"])
        self.assertTrue(done.created and done.body_read)
        self.assertRegex(item["id"], r"^PI-[0-9A-F]{32}$")
        self.assertEqual((item["status"], item["revision"], item["original_filename"]),
                         ("ready", 0, "Paper One.pdf"))
        self.assertEqual((item["sha256"], item["byte_size"]), (hashlib.sha256(data).hexdigest(), len(data)))
        self.assertEqual(row["uploaded_by"], self.actor.user_id)
        self.assertEqual(row["upload_key"], "k-1")
        # One key: the original bytes, never linearized.
        self.assertEqual(self.names(), {row["object_locator"]})
        self.assertEqual(self.bytes_of(row["object_locator"]), data)
        draft = item["draft"]
        self.assertEqual((draft["title"], draft["status_draft"], draft["doc_type"], draft["roles"], draft["tags"],
                          draft["target_folder_id"]), ("", "Not Started", "article", [], [], ""))
        self.assertEqual(done.duplicates, {"items": [], "works": []})
        self.assertEqual([found["id"] for found in commands.queue(self.db, self.actor.context)[0]], [item["id"]])
        self.assertEqual(commands.preview(self.db, self.actor.context, item["id"]),
                         (StorageKey(NAMESPACE_ASSET_OBJECTS, row["object_locator"]), len(data), item["sha256"]))
        self.assertEqual(self.claims(), set())

    def test_a_replay_answers_from_the_item_without_reading_the_body(self) -> None:
        first = self.upload(pdf(), key="same")
        again = self.upload(pdf(), key="same", body=Unreadable())
        self.assertEqual(again.item["id"], first.item["id"])
        self.assertFalse(again.created or again.body_read)
        self.assertEqual(self.count("processing_items"), 1)
        self.assertEqual(len(self.names()), 1)

    def test_a_refused_body_leaves_no_item_and_no_object(self) -> None:
        for data, length, code in ((b"<html>not a pdf</html>", None, uploads.NOT_A_PDF),
                                   (pdf(), CAP + 1, uploads.TOO_LARGE),
                                   (pdf()[:20], len(pdf()), uploads.INCOMPLETE),
                                   (b"%PD", None, uploads.NOT_A_PDF)):
            with self.subTest(code=code), self.assertRaises(uploads.UploadRefused) as caught:
                commands.upload(self.db, self.actor.context, self.objects, upload_key=uuid.uuid4().hex,
                                filename="x.pdf", body=io.BytesIO(data),
                                content_length=len(data) if length is None else length, max_bytes=CAP)
            self.assertEqual(caught.exception.code, code)
        self.assertEqual((self.count("processing_items"), self.names()), (0, set()))

    def test_no_byte_past_the_declared_length_is_read(self) -> None:
        data = pdf()
        body = io.BytesIO(data + b"GET /next HTTP/1.1\r\n")
        self.upload(data, body=body)
        self.assertEqual(body.tell(), len(data))

    def test_invalid_keys_and_filenames_are_refused_before_the_body(self) -> None:
        for key, filename in (("", "a.pdf"), ("has space", "a.pdf"), ("k" * 129, "a.pdf"), ("ok", "a\x00.pdf")):
            with self.subTest(key=key, filename=filename), self.assertRaises(ValueError):
                self.upload(pdf(), key=key, filename=filename, body=Unreadable())
        self.assertEqual(self.ready(filename="")["original_filename"], uploads.FALLBACK_FILENAME)

    def test_an_abandoned_upload_is_an_orphan_the_sweep_collects(self) -> None:
        # Stored, then the process stopped before the item was inserted.
        stored = uploads.store(self.objects, "paper.pdf", io.BytesIO(pdf()), len(pdf()), CAP)
        kept = self.ready()
        self.assertEqual(self.sweep(), 1)
        self.assertEqual(self.claims(), {stored.key.name})
        self.settle_all()
        self.assertEqual(self.names(), {self.row(kept["id"])["object_locator"]})
        self.assertEqual(self.count("processing_items"), 1)

    def test_filenames_repeat_and_duplicate_bytes_only_warn(self) -> None:
        data = pdf("same")
        first = self.upload(data, filename="paper.pdf")
        second = self.upload(data, filename="paper.pdf")
        self.assertTrue(second.created)
        self.assertEqual(second.duplicates, {"items": [{"id": first.item["id"], "original_filename": "paper.pdf"}],
                                             "works": []})
        imported = self.import_(first.item["id"])
        third = self.upload(data)
        self.assertEqual(third.duplicates["items"], [{"id": second.item["id"], "original_filename": "paper.pdf"}])
        self.assertEqual([work["id"] for work in third.duplicates["works"]], [imported.work_id])
        self.assertEqual(self.count("processing_items"), 3)


class QueuePageTests(QueueTestCase):
    def page(self, limit: int, after: Optional[str] = None) -> tuple[list[str], Optional[str]]:
        found, cursor = commands.queue(self.db, self.actor.context, limit=limit, after=after)
        return [entry["id"] for entry in found], cursor

    def test_pages_follow_upload_order_and_end_without_a_cursor(self) -> None:
        uploaded = [self.ready()["id"] for _ in range(5)]
        first, cursor = self.page(2)
        assert cursor is not None
        second, cursor = self.page(2, cursor)
        assert cursor is not None
        third, cursor = self.page(2, cursor)
        self.assertEqual((first + second + third, cursor), (uploaded, None))
        self.assertEqual(self.page(5), (uploaded, None))

    def test_ties_on_the_upload_time_break_on_the_id(self) -> None:
        ids = sorted(self.ready()["id"] for _ in range(4))
        self.sql("UPDATE prks.processing_items SET created_at = '2026-01-01T00:00:00Z'")
        first, cursor = self.page(3)
        rest, end = self.page(3, cursor)
        self.assertEqual((first + rest, end), (ids, None))

    def test_changes_between_pages_neither_repeat_nor_skip_an_item_that_stays(self) -> None:
        a, b, c, d = (self.ready()["id"] for _ in range(4))
        first, cursor = self.page(2)
        self.assertEqual(first, [a, b])
        self.delete(b)  # the page's last item: the cursor still holds its place
        self.delete(c)
        self.import_(a)
        e = self.ready()["id"]
        rest, end = self.page(10, cursor)
        self.assertEqual((rest, end), ([d, e], None))

    def test_a_page_is_bounded(self) -> None:
        for limit in (0, items.MAX_PAGE_SIZE + 1, True):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                self.page(limit)  # type: ignore[arg-type]
        for cursor in ("", "x", "1~PI-1", "-1~PI-" + "0" * 32, "9" * 20 + "~PI-" + "0" * 32):
            with self.subTest(cursor=cursor), self.assertRaises(ValueError):
                self.page(1, cursor)


class UploadRaceTests(QueueTestCase):
    def stored(self, data: Optional[bytes] = None) -> lifecycle.StoredObject:
        data = data or pdf(uuid.uuid4().hex)
        return uploads.store(self.objects, "paper.pdf", io.BytesIO(data), len(data), CAP)

    def register(self, obj: lifecycle.StoredObject, key: str) -> Callable[[LibraryScope], uploads.Registered]:
        return lambda scope: uploads.register(scope, self.objects, obj, item_id="PI-" + uuid.uuid4().hex.upper(),
                                              upload_key=key, filename="paper.pdf",
                                              uploaded_by=self.actor.user_id)

    def test_two_uploads_with_one_key_leave_one_item_and_release_the_losers_bytes(self) -> None:
        winner, loser = self.stored(), self.stored()
        held, waited = self.hold_then(self.register(winner, "k"),
                                      lambda: self.run_command(self.register(loser, "k")))
        self.assertTrue(held.created)
        self.assertFalse(waited.created)
        self.assertEqual(waited.item["id"], held.item["id"])
        self.assertEqual(self.count("processing_items"), 1)
        self.assertEqual(self.claims(), {loser.key.name})
        self.settle_all()
        self.assertEqual(self.names(), {winner.key.name})

    def test_a_winner_deleted_before_the_loser_resolves_answers_deleted(self) -> None:
        winner, loser = self.stored(), self.stored()

        def register_then_delete(scope: LibraryScope) -> Any:
            created = self.register(winner, "k")(scope)
            return items.delete(scope, created.item["id"])

        def upload_again() -> Any:
            found = self.run_command(self.register(loser, "k"))
            return commands._answer(found, True)

        marker, waited = self.hold_then(register_then_delete, upload_again)
        self.assertEqual(marker["status"], "deleted")
        self.assertIsInstance(waited, items.Refused)
        self.assertEqual(waited.code, items.ITEM_DELETED)
        self.assertEqual(self.count("processing_items"), 1)
        self.settle_all()
        self.assertEqual(self.names(), set())

    def test_a_replay_racing_a_delete_sees_the_item_or_the_marker_never_a_new_item(self) -> None:
        item = self.ready(key="k")
        marker, waited = self.hold_then(lambda scope: items.delete(scope, item["id"]),
                                        lambda: self.upload(pdf(), key="k"))
        self.assertEqual(marker["status"], "deleted")
        self.assertIsInstance(waited, items.Refused)
        self.assertEqual((waited.code, self.count("processing_items")), (items.ITEM_DELETED, 1))
        self.assert_refused(items.ITEM_DELETED, lambda: self.upload(pdf(), key="k"))

    def test_settle_first_unlinks_the_bytes_and_the_insert_is_refused(self) -> None:
        obj = self.stored()
        self.assertTrue(self.db.command(lambda tx: lifecycle.claim_orphan(tx, obj.key)))
        self.assertIs(lifecycle.settle(self.db, self.objects, obj.key), Settled.REMOVED)
        with self.assertRaises(ObjectMissing):
            self.run_command(self.register(obj, "k"))
        self.assertEqual(self.count("processing_items"), 0)

    def test_an_insert_first_keeps_the_bytes_from_a_waiting_settle(self) -> None:
        obj = self.stored()
        self.assertTrue(self.db.command(lambda tx: lifecycle.claim_orphan(tx, obj.key)))
        held, settled = self.hold_then(self.register(obj, "k"),
                                       lambda: lifecycle.settle(self.db, self.objects, obj.key))
        self.assertTrue(held.created)
        self.assertIs(settled, Settled.REFERENCED)
        self.assertEqual((self.names(), self.claims()), ({obj.key.name}, set()))


class EditTests(QueueTestCase):
    def test_an_edit_writes_the_draft_roles_and_tags_and_advances_the_revision(self) -> None:
        item = self.ready()
        ada = self.library.add_person("Ada", "Lovelace")
        alan = self.library.add_person("Alan", "Turing")
        tag = self.library.add_tag("logic")["id"]
        folder = self.library.add_folder("Reading")
        after = self.edit(item["id"], 0, {
            "title": "  On Computable Numbers ", "status": "In Progress", "doc_type": "book", "year": 1936,
            "thumb_page": "2", "target_folder_id": folder, "unknown": "ignored",
            "roles": [{"person_id": alan, "role_type": "Author"}, {"person_id": ada, "role_type": "Editor"},
                      {"person_id": alan, "role_type": "Author"}],
            "tags": [{"id": tag}, {"tag_id": tag}],
        })
        draft = after["draft"]
        self.assertEqual(after["revision"], 1)
        self.assertEqual((draft["title"], draft["status_draft"], draft["doc_type"], draft["year"],
                          draft["thumb_page"], draft["target_folder_id"]),
                         ("On Computable Numbers", "In Progress", "book", "1936", 2, folder))
        self.assertEqual([(role["person_id"], role["role_type"], role["order_index"]) for role in draft["roles"]],
                         [(alan, "Author", 0), (ada, "Editor", 1)])
        self.assertEqual([found["id"] for found in draft["tags"]], [tag])
        self.assertEqual(self.get(item["id"]), after)

    def test_a_stale_revision_is_refused_with_the_current_item(self) -> None:
        item = self.ready()
        current = self.edit(item["id"], 0, {"title": "First"})
        refused = self.assert_refused(items.REVISION_CONFLICT, lambda: self.edit(item["id"], 0, {"title": "Late"}))
        self.assertEqual(refused.item, current)

    def test_an_invalid_edit_changes_nothing(self) -> None:
        item = self.ready()
        tag = self.library.add_tag("logic")["id"]
        for fields in ({"status_draft": "Done"}, {"title": "a\x00b"}, {"title": "x", "target_folder_id": "F-NONE"},
                       {"title": "x", "roles": [{"person_id": "P-NONE", "role_type": "Author"}]},
                       {"title": "x", "roles": [{"person_id": "P-NONE", "role_type": "Mentioned"}]},
                       {"title": "x", "tags": [{"id": tag}, {"id": "T-NONE"}]}, {"roles": {}}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.edit(item["id"], 0, fields)
        self.assertEqual(self.get(item["id"]), item)
        with self.assertRaises(ValueError):
            self.edit(item["id"], -1, {"title": "x"})

    def test_markers_refuse_an_edit_whatever_the_revision(self) -> None:
        deleted, imported = self.ready(), self.ready()
        self.delete(deleted["id"])
        self.import_(imported["id"])
        self.assert_refused(items.ITEM_DELETED, lambda: self.edit(deleted["id"], 1, {"title": "x"}))
        self.assert_refused(items.ITEM_IMPORTED, lambda: self.edit(imported["id"], 0, {"title": "x"}))
        self.assert_refused(items.NOT_FOUND, lambda: self.edit("PI-" + "0" * 32, 0, {"title": "x"}))

    def test_deleting_a_folder_person_or_tag_updates_the_draft(self) -> None:
        item = self.ready()
        ada = self.library.add_person("Ada", "Lovelace")
        tag = self.library.add_tag("logic")["id"]
        folder = self.library.add_folder("Reading")
        self.edit(item["id"], 0, {"target_folder_id": folder, "roles": [{"person_id": ada, "role_type": "Author"}],
                                  "tags": [{"id": tag}]})
        self.run_command(lambda scope: folders.delete(scope, folder))
        self.serializable(lambda scope: persons.delete_person(scope, ada))
        self.library.delete_tag(tag)
        draft = self.get(item["id"])["draft"]
        self.assertEqual((draft["target_folder_id"], draft["roles"], draft["tags"]), ("", [], []))

    def test_a_tag_merge_moves_draft_tags(self) -> None:
        item = self.ready()
        source = self.library.add_tag("logic")["id"]
        target = self.library.add_tag("maths")["id"]
        self.edit(item["id"], 0, {"tags": [{"id": source}]})
        self.library.merge_tags_into(source, target)
        self.assertEqual([tag["id"] for tag in self.get(item["id"])["draft"]["tags"]], [target])


class ImportTests(QueueTestCase):
    def test_an_import_creates_the_work_from_the_draft_and_moves_the_key_to_its_asset(self) -> None:
        item = self.ready(filename="turing.pdf")
        alan = self.library.add_person("Alan", "Turing")
        tag = self.library.add_tag("logic")["id"]
        folder = self.library.add_folder("Reading")
        self.edit(item["id"], 0, {
            "title": "On Computable Numbers", "status_draft": "Planned", "abstract": "A", "year": "1936",
            "journal": "PLMS", "doc_type": "article", "private_notes": "note", "thumb_page": 3,
            "source_url": "https://example.org/turing", "target_folder_id": folder,
            "roles": [{"person_id": alan, "role_type": "Author"}], "tags": [{"id": tag}]})
        key = self.row(item["id"])["object_locator"]
        done = self.import_(item["id"])
        self.assertTrue(done.created)
        work = self.library.get_work(done.work_id)
        assert work is not None
        self.assertEqual((work["title"], work["status"], work["abstract"], work["year"], work["journal"]),
                         ("On Computable Numbers", "Planned", "A", "1936", "PLMS"))
        self.assertEqual([found["id"] for found in self.library.get_work_tags(done.work_id)], [tag])
        self.assertEqual(self.sql("SELECT folder_id FROM prks.folder_files WHERE work_id = %s",
                                  (done.work_id,)), [{"folder_id": folder}])
        self.assertEqual([(row["person_id"], row["role_type"]) for row in self.sql(
            "SELECT person_id, role_type FROM prks.roles WHERE work_id = %s", (done.work_id,))],
                         [(alan, "Author")])
        asset = self.asset_of(done.work_id)
        self.assertEqual((asset["storage_locator"], asset["source_locator"], asset["content_sha256"],
                          asset["ingest_sha256"], asset["byte_size"], asset["origin"], asset["thumb_page"]),
                         (key, None, item["sha256"], item["sha256"], item["byte_size"], "processing_import", 3))
        marker = self.get(item["id"])
        self.assertEqual((marker["status"], marker["imported_work_id"], marker["draft"]),
                         ("imported", done.work_id, None))
        row = self.row(item["id"])
        self.assertIsNone(row["object_locator"])
        self.assertIsNotNone(row["imported_at"])
        self.assertEqual(self.count("processing_item_drafts") + self.count("processing_item_roles")
                         + self.count("processing_item_tags"), 0)
        self.assertEqual(commands.queue(self.db, self.actor.context), ([], None))
        self.assertIsNone(commands.preview(self.db, self.actor.context, item["id"]))
        # The Asset holds the key now: nothing to clean, nothing for the sweep.
        self.assertEqual((self.claims(), self.sweep()), (set(), 0))
        self.assertEqual(self.names(), {key})

    def test_no_target_folder_files_the_work_in_uncategorized_and_names_it_after_the_file(self) -> None:
        done = self.import_(self.ready(filename="Some Paper.pdf")["id"])
        work = self.library.get_work(done.work_id)
        assert work is not None
        self.assertEqual(work["title"], "Some Paper")
        (filed,) = self.sql("SELECT f.title FROM prks.folder_files ff JOIN prks.folders f ON f.id = ff.folder_id"
                            " WHERE ff.work_id = %s", (done.work_id,))
        self.assertEqual(filed["title"], "Uncategorized")

    def test_a_replay_answers_the_same_work_and_a_deleted_work_is_reported(self) -> None:
        item = self.ready()
        done = self.import_(item["id"])
        again = self.import_(item["id"])
        self.assertEqual((again.work_id, again.created), (done.work_id, False))
        self.assertEqual(self.count("works"), 1)
        self.library.delete_work_record(done.work_id)
        refused = self.assert_refused(items.WORK_DELETED, lambda: self.import_(item["id"]))
        assert refused.item is not None
        self.assertEqual((refused.item["status"], refused.item["imported_work_id"]), ("imported", None))
        self.assertEqual(self.count("works"), 0)
        self.assert_refused(items.ITEM_IMPORTED, lambda: self.delete(item["id"]))

    def test_a_deleted_item_is_never_imported(self) -> None:
        item = self.ready()
        self.delete(item["id"])
        self.assert_refused(items.ITEM_DELETED, lambda: self.import_(item["id"]))
        self.assert_refused(items.NOT_FOUND, lambda: self.import_("PI-" + "0" * 32))
        self.assertEqual(self.count("works"), 0)

    def test_a_failed_import_changes_nothing(self) -> None:
        item = self.ready()
        self.edit(item["id"], 0, {"title": "Kept"})
        before = self.get(item["id"])
        self.objects.delete(StorageKey(NAMESPACE_ASSET_OBJECTS, self.row(item["id"])["object_locator"]))
        with self.assertRaises(ObjectMissing):
            self.import_(item["id"])
        self.assertEqual(self.get(item["id"]), before)
        self.assertEqual(self.count("works"), 0)

    def test_a_batch_imports_each_item_on_its_own(self) -> None:
        good, gone, other = self.ready(), self.ready(), self.ready()
        self.delete(gone["id"])
        self.objects.delete(StorageKey(NAMESPACE_ASSET_OBJECTS, self.row(other["id"])["object_locator"]))
        last = self.ready()
        results = commands.import_many(self.db, self.actor.context, self.objects,
                                       [good["id"], gone["id"], other["id"], "PI-" + "0" * 32, last["id"],
                                        good["id"]])
        self.assertEqual([(result.item_id, result.code) for result in results],
                         [(good["id"], None), (gone["id"], items.ITEM_DELETED),
                          (other["id"], "PROCESSING_OBJECT_MISSING"), ("PI-" + "0" * 32, items.NOT_FOUND),
                          (last["id"], None), (good["id"], None)])
        self.assertEqual([result.created for result in results], [True, False, False, False, True, False])
        self.assertEqual(results[0].work_id, results[5].work_id)
        self.assertEqual(self.count("works"), 2)
        self.assertEqual(self.get(other["id"])["status"], "ready")

    def test_a_database_error_fails_only_its_item(self) -> None:
        from backend.db.errors import RetryTransactionError

        first, failing, last = self.ready(), self.ready(), self.ready()
        real = imports.import_item

        def flaky(scope: LibraryScope, storage: Any, item_id: str, ids: imports.NewIds) -> imports.Imported:
            if item_id == failing["id"]:
                raise RetryTransactionError(sqlstate="40001")
            return real(scope, storage, item_id, ids)

        with mock.patch.object(imports, "import_item", flaky):
            results = commands.import_many(self.db, self.actor.context, self.objects,
                                           [first["id"], failing["id"], last["id"]])
        self.assertEqual([result.code for result in results], [None, "RETRY", None])
        self.assertEqual(self.get(failing["id"])["status"], "ready")
        self.assertEqual(self.count("works"), 2)

    def test_a_work_merge_plan_lists_the_items_its_source_was_imported_from(self) -> None:
        source = self.import_(self.ready()["id"]).work_id
        target = self.import_(self.ready()["id"]).work_id
        item_id = self.sql("SELECT id FROM prks.processing_items WHERE imported_work_id = %s", (source,))[0]["id"]
        plan = self.serializable(lambda scope: merge.prepare(scope, source, target))
        self.assertEqual(plan.imported_items, (item_id,))


class ImportRaceTests(QueueTestCase):
    def test_two_imports_of_one_item_create_one_work(self) -> None:
        item = self.ready()
        ids = imports.NewIds.mint()
        held, waited = self.hold_then(lambda scope: imports.import_item(scope, self.objects, item["id"], ids),
                                      lambda: self.import_(item["id"]), serializable=True)
        self.assertEqual((held.created, waited.created, waited.work_id), (True, False, held.work_id))
        self.assertEqual(self.count("works"), 1)

    def test_an_import_waiting_on_a_delete_is_refused(self) -> None:
        item = self.ready()
        _marker, waited = self.hold_then(lambda scope: items.delete(scope, item["id"]),
                                         lambda: self.import_(item["id"]))
        self.assertIsInstance(waited, items.Refused)
        self.assertEqual(waited.code, items.ITEM_DELETED)
        self.assertEqual(self.count("works"), 0)

    def test_a_delete_waiting_on_an_import_is_refused(self) -> None:
        item = self.ready()
        ids = imports.NewIds.mint()
        held, waited = self.hold_then(lambda scope: imports.import_item(scope, self.objects, item["id"], ids),
                                      lambda: self.delete(item["id"]), serializable=True)
        self.assertIsInstance(waited, items.Refused)
        self.assertEqual(waited.code, items.ITEM_IMPORTED)
        self.assertEqual(self.claims(), set())
        self.assertEqual(self.names(), {self.asset_of(held.work_id)["storage_locator"]})

    def test_an_import_waiting_on_an_edit_imports_the_edited_draft(self) -> None:
        item = self.ready()
        request = items.parse_update({"title": "Edited"})
        _after, waited = self.hold_then(lambda scope: items.update(scope, item["id"], 0, request),
                                        lambda: self.import_(item["id"]))
        work = self.library.get_work(waited.work_id)
        assert work is not None
        self.assertEqual(work["title"], "Edited")

    def test_an_import_racing_a_tag_merge_files_the_surviving_tag(self) -> None:
        from backend.domain.organization import tags

        item = self.ready()
        source = self.library.add_tag("logic")["id"]
        target = self.library.add_tag("maths")["id"]
        self.edit(item["id"], 0, {"tags": [{"id": source}]})
        _merged, waited = self.hold_then(lambda scope: tags.merge(scope, source, target, "L-" + uuid.uuid4().hex.upper()),
                                         lambda: self.import_(item["id"]), serializable=True)
        self.assertEqual([tag["id"] for tag in self.library.get_work_tags(waited.work_id)], [target])

    def test_a_settle_waiting_on_an_import_finds_the_asset(self) -> None:
        item = self.ready()
        key = StorageKey(NAMESPACE_ASSET_OBJECTS, self.row(item["id"])["object_locator"])
        # A stale claim on a referenced key, as a crashed release could leave.
        self.sql("INSERT INTO prks.object_cleanup_claims (namespace, name) VALUES (%s, %s)", (key.namespace, key.name))
        ids = imports.NewIds.mint()
        held, settled = self.hold_then(lambda scope: imports.import_item(scope, self.objects, item["id"], ids),
                                       lambda: lifecycle.settle(self.db, self.objects, key), serializable=True)
        self.assertIs(settled, Settled.REFERENCED)
        self.assertEqual(self.asset_of(held.work_id)["storage_locator"], key.name)
        self.assertEqual((self.names(), self.claims()), ({key.name}, set()))


class DeleteTests(QueueTestCase):
    def test_a_delete_keeps_a_marker_and_releases_the_bytes(self) -> None:
        item = self.ready(key="k")
        key = self.row(item["id"])["object_locator"]
        marker = self.delete(item["id"])
        self.assertEqual((marker["status"], marker["draft"], marker["revision"]), ("deleted", None, 1))
        self.assertIsNotNone(marker["deleted_at"])
        row = self.row(item["id"])
        self.assertEqual((row["object_locator"], row["upload_key"], row["sha256"]), (None, "k", item["sha256"]))
        self.assertEqual(self.claims(), {key})
        self.assertEqual(self.count("processing_item_drafts"), 0)
        self.assertEqual(self.sql("SELECT count(*) AS n FROM prks.jobs WHERE kind = %s", (lifecycle.CLEANUP_KIND,)),
                         [{"n": 1}])
        self.settle_all()
        self.assertEqual(self.names(), set())
        # Again: the same marker, nothing changed.
        self.assertEqual(self.delete(item["id"]), marker)
        self.assert_refused(items.ITEM_DELETED, lambda: self.upload(pdf(), key="k", body=Unreadable()))
        self.assertEqual(commands.queue(self.db, self.actor.context), ([], None))
        self.assertEqual(self.get(item["id"]), marker)

    def test_a_ready_key_is_referenced_for_settle_and_sweep(self) -> None:
        item = self.ready()
        key = StorageKey(NAMESPACE_ASSET_OBJECTS, self.row(item["id"])["object_locator"])
        self.assertEqual(self.sweep(), 0)
        self.assertIs(self.db.query(lambda tx: lifecycle.references(tx, key)), True)
        self.sql("INSERT INTO prks.object_cleanup_claims (namespace, name) VALUES (%s, %s)", (key.namespace, key.name))
        self.assertIs(lifecycle.settle(self.db, self.objects, key), Settled.REFERENCED)
        self.assertEqual(self.names(), {key.name})

    def test_the_schema_refuses_a_marker_that_keeps_its_draft_or_its_key(self) -> None:
        from backend.db.errors import DatabaseError

        item = self.ready()
        with self.assertRaises(DatabaseError):
            self.sql("UPDATE prks.processing_items SET status = 'deleted', deleted_at = now(), object_locator = NULL"
                     " WHERE id = %s", (item["id"],))
        self.sql("DELETE FROM prks.processing_item_drafts WHERE item_id = %s", (item["id"],))
        with self.assertRaises(DatabaseError):
            self.sql("UPDATE prks.processing_items SET status = 'deleted', deleted_at = now() WHERE id = %s",
                     (item["id"],))


class IsolationTests(QueueTestCase):
    def test_another_library_neither_sees_nor_changes_an_item(self) -> None:
        item = self.ready(key="k")
        other = self.other_actor().context
        self.assertIsNone(self.get(item["id"], ctx=other))
        self.assertEqual(commands.queue(self.db, other), ([], None))
        self.assertIsNone(commands.preview(self.db, other, item["id"]))
        self.assert_refused(items.NOT_FOUND, lambda: self.edit(item["id"], 0, {"title": "x"}, ctx=other))
        self.assert_refused(items.NOT_FOUND, lambda: self.delete(item["id"], ctx=other))
        self.assert_refused(items.NOT_FOUND, lambda: self.import_(item["id"], ctx=other))
        # The upload key is the Library's own.
        theirs = self.upload(pdf(), key="k", ctx=other)
        self.assertTrue(theirs.created)
        self.assertNotEqual(theirs.item["id"], item["id"])
        self.assertEqual(self.get(item["id"])["revision"], 0)

    def test_a_key_another_library_names_cannot_be_adopted(self) -> None:
        item = self.ready()
        row = self.row(item["id"])
        obj = lifecycle.StoredObject(StorageKey(NAMESPACE_ASSET_OBJECTS, row["object_locator"]),
                                     int(row["byte_size"]), row["sha256"])
        with self.assertRaises(ObjectMissing):
            self.run_command(lambda scope: uploads.register(scope, self.objects, obj, item_id="PI-" + "1" * 32,
                                                            upload_key="theirs", filename="x.pdf",
                                                            uploaded_by=None), self.other_actor().context)

    def test_a_reader_cannot_change_the_queue(self) -> None:
        from backend.domain.identity.authorization import AccessRefused

        item = self.ready()
        reader = self.reader_context()
        self.assertEqual(self.get(item["id"], ctx=reader), item)
        for attempt in (lambda: self.upload(pdf(), ctx=reader, body=Unreadable()),
                        lambda: self.edit(item["id"], 0, {"title": "x"}, ctx=reader),
                        lambda: self.delete(item["id"], ctx=reader),
                        lambda: self.import_(item["id"], ctx=reader)):
            with self.assertRaises(AccessRefused):
                attempt()
        self.assertEqual(self.get(item["id"]), item)
