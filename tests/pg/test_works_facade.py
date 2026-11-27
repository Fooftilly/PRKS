"""Work create and reads on PostgreSQL (L-W-1 #628).

:class:`AnswerTests` spells out what the reads answer. The other tests cover
the bound actor and its checks, Library isolation, the rows create writes,
timestamp precision and order, and concurrent creates.
"""

from __future__ import annotations

import contextvars
import hashlib
import os
import shutil
import tempfile
import threading
import unittest
from typing import Any
from unittest import mock

from backend.db.config import DatabaseConfig
from backend.db.database import Database
from backend.domain.identity.authorization import AccessRefused, Refusal
from backend.domain.identity.library_scope import library_query
from backend.domain.identity.request_context import RequestContext
from backend.domain.works import queries, work_commands
from backend.library import PostgresLibrary
from tests import acting_context as facade_context
from backend.storage.config import StorageConfig
from tests.pg.facade_context import Actor, provision_actor
from tests.pg.harness import PostgresHarness, get_harness
from tests.narrowing import require
from tests.pg.answers import AnswerAssertions
from backend.web import etags
from tests import works_fixtures

_VIDEO = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


class WorksFacadeTestCase(unittest.TestCase):
    harness: PostgresHarness

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.harness = get_harness()

    def setUp(self) -> None:
        super().setUp()
        name = self.harness.create_database("works")
        self.addCleanup(self.harness.drop_database, name)
        self.db = Database(DatabaseConfig(conninfo=self.harness.conninfo(name), role="test",
                                          pool_min=0, pool_max=8))
        self.addCleanup(self.db.close)
        tmp = tempfile.mkdtemp(prefix="prks-pg-works-")
        self.addCleanup(shutil.rmtree, tmp, True)
        self.storage = StorageConfig.for_testing(tmp)
        os.makedirs(self.storage.pdfs_dir, exist_ok=True)
        self.library = PostgresLibrary(self.db, storage=self.storage)
        self.actor = provision_actor(self.db)
        self.bind(self.actor.context)

    @property
    def ctx(self) -> RequestContext:
        """The request context the test is bound to (:meth:`bind`): the actor a domain call runs as."""
        return facade_context.current_context()

    def bind(self, ctx: RequestContext) -> None:
        self.addCleanup(facade_context.unbind, facade_context.bind(ctx))

    def sql(self, query: Any, params: Any = None) -> list[dict[str, Any]]:
        def run(tx: Any) -> list[dict[str, Any]]:
            cursor = tx.execute(query, params)
            return cursor.fetchall() if cursor.description is not None else []

        return self.db.command(run)

    def count(self, table: str) -> int:
        return int(self.sql(f"SELECT count(*) AS n FROM prks.{table}")[0]["n"])

    def store_pdf(self, name: str, data: bytes) -> str:
        with open(os.path.join(self.storage.pdfs_dir, name), "wb") as handle:
            handle.write(data)
        return f"/api/pdfs/{name}"

    def other_actor(self, role: str = "owner") -> Actor:
        return provision_actor(self.db, name="Other", role=role)

    def reader_context(self) -> RequestContext:
        """A reader of this test's Library, signed in."""
        from backend.domain.identity import sessions

        reader = provision_actor(self.db, role="reader")
        self.sql("INSERT INTO prks.library_memberships (library_id, user_id, role) VALUES (%s, %s, 'reader')",
                 (self.actor.library_id, reader.user_id))
        new = self.db.command(lambda tx: sessions.create_session(
            tx, user_id=reader.user_id, library_id=self.actor.library_id))
        valid = sessions.resolve_session(self.db, new.token.reveal())
        assert valid is not None
        return RequestContext.for_session(valid, epoch=1, device_id=reader.context.device_id)

    def core(self, work_id: str) -> dict[str, Any]:
        found = library_query(self.db, self.actor.context, lambda scope: queries.work(scope, work_id))
        assert found is not None
        return found


class ActorTests(WorksFacadeTestCase):
    def test_a_call_without_a_bound_actor_is_refused(self) -> None:
        # A fresh context has no binding, like a request nothing admitted.
        unbound = contextvars.Context()
        for call in (lambda: work_commands.catalog(self.db, self.ctx),
                     lambda: works_fixtures.add_work(self.db, self.ctx, self.storage, "Unbound"),
                     lambda: works_fixtures.etag_recent(self.db, self.ctx)):
            with self.assertRaises(facade_context.NoRequestContext):
                unbound.run(call)
        self.assertEqual(self.count("works"), 0)
        with facade_context.acting_as(self.actor.context):
            works_fixtures.add_work(self.db, self.ctx, self.storage, "Bound")
        self.assertEqual([w["title"] for w in work_commands.catalog(self.db, self.ctx)], ["Bound"])

    def test_the_binding_is_per_thread(self) -> None:
        seen: list[BaseException] = []

        def run() -> None:
            try:
                work_commands.catalog(self.db, self.ctx)
            except BaseException as exc:  # noqa: BLE001 - recorded for the assertion
                seen.append(exc)

        thread = threading.Thread(target=run)
        thread.start()
        thread.join()
        self.assertEqual(len(seen), 1)
        self.assertIsInstance(seen[0], facade_context.NoRequestContext)

    def test_a_reader_reads_but_cannot_create(self) -> None:
        work_id = works_fixtures.add_work(self.db, self.ctx, self.storage, "Shared")
        with facade_context.acting_as(self.reader_context()):
            self.assertEqual(require(work_commands.detail(self.db, self.ctx, work_id))["title"], "Shared")
            with self.assertRaises(AccessRefused) as caught:
                works_fixtures.add_work(self.db, self.ctx, self.storage, "Refused")
        self.assertIs(caught.exception.refusal, Refusal.FORBIDDEN)
        self.assertEqual(self.count("works"), 1)

    def test_a_refused_create_never_touches_a_managed_pdf(self) -> None:
        # Existing and missing keys are refused alike, before the file is looked at.
        existing = self.store_pdf("kept.pdf", b"%PDF-1.4 kept")
        missing = "/api/pdfs/never-stored.pdf"
        reader = self.reader_context()
        unbound = contextvars.Context()

        def as_reader(call: Any) -> Any:
            with facade_context.acting_as(reader):
                return call()

        with mock.patch.object(work_commands, "_managed_file",
                               side_effect=AssertionError("file touched")) as touched:
            for path in (existing, missing):
                with self.assertRaises(facade_context.NoRequestContext):
                    unbound.run(lambda p=path: works_fixtures.add_work(self.db, self.ctx, self.storage, "Unbound", file_path=p))
                with self.assertRaises(AccessRefused) as caught:
                    as_reader(lambda p=path: works_fixtures.add_work(self.db, self.ctx, self.storage, "Reader", file_path=p))
                self.assertIs(caught.exception.refusal, Refusal.FORBIDDEN)
        touched.assert_not_called()
        self.assertEqual(self.count("works"), 0)
        self.assertEqual(self.count("assets"), 0)

    def test_a_create_prepared_before_a_restore_is_refused(self) -> None:
        self.sql("UPDATE prks.libraries SET epoch = epoch + 1 WHERE id = %s", (self.actor.library_id,))
        with self.assertRaises(AccessRefused) as caught:
            works_fixtures.add_work(self.db, self.ctx, self.storage, "Stale")
        self.assertIs(caught.exception.refusal, Refusal.LIBRARY_EPOCH_CHANGED)
        self.assertEqual(self.count("works"), 0)
        self.assertEqual(work_commands.catalog(self.db, self.ctx), [])  # reads are not fenced by epoch


class LibraryIsolationTests(WorksFacadeTestCase):
    def test_another_librarys_works_are_invisible(self) -> None:
        mine = works_fixtures.add_work(self.db, self.ctx, self.storage, "Mine")
        other = self.other_actor()
        with facade_context.acting_as(other.context):
            theirs = works_fixtures.add_work(self.db, self.ctx, self.storage, "Theirs", source_url=_VIDEO)
            works_fixtures.add_work(self.db, self.ctx, self.storage, "Theirs too")
        self.assertEqual([w["id"] for w in work_commands.catalog(self.db, self.ctx)], [mine])
        self.assertEqual([w["id"] for w in work_commands.browse_catalog(self.db, self.ctx)], [mine])
        self.assertEqual([w["id"] for w in work_commands.recently_added_browse(self.db, self.ctx)], [mine])
        self.assertIsNone(work_commands.detail(self.db, self.ctx, theirs))
        self.assertIsNone(work_commands.thumbnail_fields(self.db, self.ctx, theirs))
        self.assertEqual([w["id"] for w in works_fixtures.summaries_by_ids(self.db, self.ctx, self.storage, [theirs, mine])],
                         [mine])
        self.assertRegex(works_fixtures.etag_recently_added(self.db, self.ctx), r'^W/"prks-recently-added-1-')
        self.sql("UPDATE prks.works SET last_opened_at = now()")
        self.assertEqual([w["id"] for w in work_commands.recent_browse(self.db, self.ctx)], [mine])
        self.assertRegex(works_fixtures.etag_recent(self.db, self.ctx), r'^W/"prks-recent-1-')
        with facade_context.acting_as(other.context):
            self.assertEqual(len(work_commands.catalog(self.db, self.ctx)), 2)

    def test_a_managed_pdf_of_another_library_cannot_be_adopted(self) -> None:
        path = self.store_pdf("p.pdf", b"%PDF-1.4\nmine\n%%EOF\n")
        mine = works_fixtures.add_work(self.db, self.ctx, self.storage, "Mine", file_path=path)
        other = self.other_actor()
        works_before = self.count("works")
        with facade_context.acting_as(other.context):
            with self.assertRaisesRegex(ValueError, "does not exist"):
                works_fixtures.add_work(self.db, self.ctx, self.storage, "Theirs", file_path=path)
            self.assertEqual(work_commands.catalog(self.db, self.ctx), [])
        self.assertEqual(self.count("works"), works_before)
        self.assertEqual(self.sql("SELECT work_id FROM prks.assets WHERE storage_locator = 'p.pdf'"),
                         [{"work_id": mine}])
        # Within one Library two Works may still share a file, as on SQLite.
        works_fixtures.add_work(self.db, self.ctx, self.storage, "Mine again", file_path=path)

    def test_a_managed_pdf_is_served_only_to_its_library(self) -> None:
        # The content route asks this before reading bytes: the data root is shared.
        path = self.store_pdf("served.pdf", b"%PDF-1.4\nserved\n%%EOF\n")
        mine = works_fixtures.add_work(self.db, self.ctx, self.storage, "Mine", file_path=path)
        stream = works_fixtures.add_work(self.db, self.ctx, self.storage, "Stream", source_url=_VIDEO)
        asset = require(work_commands.detail(self.db, self.ctx, mine))["primary_asset_id"]
        streamed = require(work_commands.detail(self.db, self.ctx, stream))["primary_asset_id"]
        self.assertEqual(work_commands.asset_object(self.db, self.ctx, asset), "served.pdf")
        self.assertIsNone(work_commands.asset_object(self.db, self.ctx, streamed))
        self.assertIsNone(work_commands.asset_object(self.db, self.ctx, "AS-" + "0" * 32))
        with facade_context.acting_as(self.other_actor().context):
            self.assertIsNone(work_commands.asset_object(self.db, self.ctx, asset))

    def test_two_libraries_racing_for_one_pdf_leave_it_with_one(self) -> None:
        # Whatever the interleaving (one after the other, or both checks before
        # either insert, where SERIALIZABLE retries the loser), one Library wins.
        path = self.store_pdf("race.pdf", b"%PDF-1.4\nrace\n%%EOF\n")
        contexts = [self.actor.context, self.other_actor().context]
        start = threading.Barrier(2)
        outcomes: list[str] = []
        lock = threading.Lock()

        def run(context: Any) -> None:
            with facade_context.acting_as(context):
                start.wait()
                try:
                    works_fixtures.add_work(self.db, self.ctx, self.storage, "Race", file_path=path)
                    outcome = "created"
                except ValueError:
                    outcome = "refused"
            with lock:
                outcomes.append(outcome)

        threads = [threading.Thread(target=run, args=(context,)) for context in contexts]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(outcomes), ["created", "refused"])
        rows = self.sql("SELECT DISTINCT library_id FROM prks.assets WHERE storage_locator = 'race.pdf'")
        self.assertEqual(len(rows), 1)


class CreateTests(WorksFacadeTestCase):
    def test_a_managed_pdf_is_catalogued_with_its_hash(self) -> None:
        data = b"%PDF-1.4\nbytes\n%%EOF\n"
        work_id = works_fixtures.add_work(self.db, self.ctx, self.storage, "Paper", file_path=self.store_pdf("p.pdf", data), source_url="https://example.org/p", thumb_page="2")
        rows = self.sql(
            "SELECT a.kind, a.storage_locator, a.content_sha256, a.ingest_sha256, a.byte_size, a.origin,"
            " a.thumb_page, a.media_type, m.url, m.primary_asset_id = a.id AS is_primary"
            " FROM prks.assets a JOIN prks.manifestations m ON m.id = a.manifestation_id"
            " WHERE a.work_id = %s", (work_id,))
        digest = hashlib.sha256(data).hexdigest()
        self.assertEqual(rows, [{
            "kind": "managed_file", "storage_locator": "p.pdf", "content_sha256": digest,
            "ingest_sha256": digest, "byte_size": len(data), "origin": "upload", "thumb_page": 2,
            "media_type": None, "url": "https://example.org/p", "is_primary": True,
        }])

    def test_a_video_is_a_stream_without_bytes(self) -> None:
        work_id = works_fixtures.add_work(self.db, self.ctx, self.storage, "Video", source_url=_VIDEO + "&t=3")
        rows = self.sql("SELECT kind, provider, provider_id, url, origin, storage_locator FROM prks.assets"
                        " WHERE work_id = %s", (work_id,))
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["kind"], rows[0]["provider"], rows[0]["provider_id"], rows[0]["origin"],
                          rows[0]["storage_locator"]),
                         ("external_stream", "youtube", "dQw4w9WgXcQ", None, None))
        self.assertIsNone(self.sql("SELECT url FROM prks.manifestations WHERE work_id = %s",
                                   (work_id,))[0]["url"])

    def test_refused_sources_write_nothing(self) -> None:
        self.store_pdf("present.pdf", b"x")
        for fields in (
            {"file_path": "/api/pdfs/missing.pdf"},          # no such file
            {"file_path": "/elsewhere/present.pdf"},         # not a managed path
            {"file_path": "/api/pdfs/../present.pdf"},
            {"file_path": "/api/pdfs/present.pdf", "source_kind": "video", "source_url": _VIDEO},
            {"status": "Someday"},                           # not a status
        ):
            with self.subTest(fields=fields):
                with self.assertRaises(Exception):
                    works_fixtures.add_work(self.db, self.ctx, self.storage, "Refused", **fields)
        for table in ("works", "manifestations", "assets", "work_notes"):
            self.assertEqual(self.count(table), 0, table)

    def test_notes_are_rows_of_their_own(self) -> None:
        with_notes = works_fixtures.add_work(self.db, self.ctx, self.storage, "Notes", text_content="# Heading", private_notes="  keep  ")
        without = works_fixtures.add_work(self.db, self.ctx, self.storage, "None", text_content="", private_notes="   ")
        self.assertEqual(
            self.sql("SELECT work_id, kind, body FROM prks.work_notes ORDER BY kind"),
            [{"work_id": with_notes, "kind": "private", "body": "keep"},
             {"work_id": with_notes, "kind": "research", "body": "# Heading"}])
        self.assertEqual((self.core(with_notes)["text_content"], self.core(with_notes)["private_notes"]),
                         ("# Heading", "keep"))
        self.assertEqual((self.core(without)["text_content"], self.core(without)["private_notes"]),
                         ("", None))

    def test_the_catalog_leaves_out_the_notes_the_detail_has(self) -> None:
        text = "x" * 5000
        work_id = works_fixtures.add_work(self.db, self.ctx, self.storage, "Notes", text_content=text, private_notes="secret")
        summary = next(row for row in work_commands.catalog(self.db, self.ctx) if row["id"] == work_id)
        self.assertNotIn("text_content", summary)
        self.assertNotIn("private_notes", summary)
        self.assertNotIn("secret", repr(summary))
        detail = work_commands.detail(self.db, self.ctx, work_id)
        assert detail is not None
        self.assertEqual((detail["text_content"], detail["private_notes"]), (text, "secret"))

    def test_an_invalid_thumbnail_page_is_created_as_none(self) -> None:
        # A video Work has an Asset, so the page has somewhere to land.
        def create(page: Any) -> Any:
            work_id = works_fixtures.add_work(self.db, self.ctx, self.storage, "Clip", source_kind="video", source_url=_VIDEO, provider="youtube", provider_id="dQw4w9WgXcQ", thumb_page=page, private_notes="")
            work = work_commands.detail(self.db, self.ctx, work_id)
            assert work is not None
            return work["thumb_page"]

        for page in ("x", "0", "-3", 0, "1.5", ""):
            with self.subTest(page=page):
                self.assertIsNone(create(page))
        self.assertEqual(create("4"), 4)

    def test_a_research_note_renders_its_wiki_links(self) -> None:
        work_id = works_fixtures.add_work(self.db, self.ctx, self.storage, "Linked", text_content="See [[Other]]")
        self.assertEqual(require(work_commands.detail(self.db, self.ctx, work_id))["html_content"],
                         'See <span class="wiki-link-unresolved" style="color:#ef4444;">[[Other]]</span>')
        self.assertNotIn("html_content", require(work_commands.detail(self.db, self.ctx, works_fixtures.add_work(self.db, self.ctx, self.storage, "Plain"))))

    def test_concurrent_creates_all_commit(self) -> None:
        errors: list[BaseException] = []
        created: list[str] = []
        lock = threading.Lock()
        start = threading.Barrier(6)

        def run(index: int) -> None:
            try:
                with facade_context.acting_as(self.actor.context):
                    start.wait()
                    work_id = works_fixtures.add_work(self.db, self.ctx, self.storage, f"Work {index}", source_url=_VIDEO)
                with lock:
                    created.append(work_id)
            except BaseException as exc:  # noqa: BLE001 - recorded for the assertion
                errors.append(exc)

        threads = [threading.Thread(target=run, args=(i,)) for i in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(sorted(w["id"] for w in work_commands.catalog(self.db, self.ctx)), sorted(created))
        self.assertEqual(self.count("assets"), 6)


class OrderAndPrecisionTests(WorksFacadeTestCase):
    def three(self) -> list[str]:
        ids = [works_fixtures.add_work(self.db, self.ctx, self.storage, title) for title in ("a", "b", "c")]
        return sorted(ids)

    def test_recent_orders_at_millisecond_precision_then_by_id(self) -> None:
        first, second, third = self.three()
        self.sql("UPDATE prks.works SET last_opened_at = '2026-01-02 03:04:05.123456+00' WHERE id = %s", (third,))
        self.sql("UPDATE prks.works SET last_opened_at = '2026-01-02 03:04:05.123999+00' WHERE id = %s", (first,))
        self.sql("UPDATE prks.works SET last_opened_at = '2026-01-02 03:04:05.124+00' WHERE id = %s", (second,))
        rows = work_commands.recent_browse(self.db, self.ctx)
        self.assertEqual([(r["id"], r["last_opened_at"]) for r in rows], [
            (second, "2026-01-02 03:04:05.124"),
            (first, "2026-01-02 03:04:05.123"),   # same rendered millisecond: by id
            (third, "2026-01-02 03:04:05.123"),
        ])
        self.assertEqual([r["id"] for r in works_fixtures.recent_works(self.db, self.ctx, self.storage, 2)], [second, first])
        self.assertEqual(works_fixtures.etag_recent(self.db, self.ctx), 'W/"prks-recent-3-2026-01-02 03:04:05.124"')

    def test_added_orders_at_second_precision_then_by_id(self) -> None:
        first, second, third = self.three()
        self.sql("UPDATE prks.works SET created_at = '2026-01-02 03:04:05.9+00' WHERE id = %s", (third,))
        self.sql("UPDATE prks.works SET created_at = '2026-01-02 03:04:05.1+00' WHERE id = %s", (first,))
        self.sql("UPDATE prks.works SET created_at = '2026-01-02 03:04:06+00' WHERE id = %s", (second,))
        for rows in (work_commands.catalog(self.db, self.ctx), work_commands.recently_added_browse(self.db, self.ctx)):
            self.assertEqual([(r["id"], r["created_at"]) for r in rows], [
                (second, "2026-01-02 03:04:06"),
                (first, "2026-01-02 03:04:05"),
                (third, "2026-01-02 03:04:05"),
            ])
        self.assertEqual(works_fixtures.etag_recently_added(self.db, self.ctx),
                         'W/"prks-recently-added-3-2026-01-02 03:04:06"')

    def test_timestamps_render_in_utc_whatever_the_session_zone(self) -> None:
        work_id = works_fixtures.add_work(self.db, self.ctx, self.storage, "Zoned")
        self.sql("UPDATE prks.works SET created_at = '2026-06-01 12:00:00+02' WHERE id = %s", (work_id,))
        self.sql("ALTER DATABASE " + self.harness_db_name() + " SET timezone = 'America/New_York'")
        self.db.close()
        self.db = Database(DatabaseConfig(conninfo=self.db.config.conninfo, role="test", pool_min=0,
                                          pool_max=2))
        self.addCleanup(self.db.close)
        self.library = PostgresLibrary(self.db, storage=self.storage)
        self.assertEqual(work_commands.catalog(self.db, self.ctx)[0]["created_at"], "2026-06-01 10:00:00")

    def harness_db_name(self) -> str:
        return str(self.sql("SELECT current_database() AS n")[0]["n"])


_PDF_BYTES = b"%PDF-1.4\n% answer fixture\n%%EOF\n"
_MISSING = "W-" + "0" * 32


class WorksAnswerTestCase(AnswerAssertions, WorksFacadeTestCase):
    """Works tests that spell out answers: Works are created under labels."""

    def add(self, label: str, **fields: Any) -> str:
        work_id = self.labels.name(works_fixtures.add_work(self.db, self.ctx, self.storage, **fields), f"<W {label}>")
        work = require(work_commands.detail(self.db, self.ctx, work_id))
        self.labels.name(work["primary_manifestation_id"], f"<MF {label}>")
        if work["primary_asset_id"]:
            self.labels.name(work["primary_asset_id"], f"<AS {label}>")
        return work_id

    def add_pdf(self, label: str, name: str, **fields: Any) -> str:
        return self.add(label, file_path=self.store_pdf(name, _PDF_BYTES), source_kind="pdf", **fields)


class AnswerTests(WorksAnswerTestCase):
    """The answers of the Work reads, spelled out."""

    def test_a_work_without_a_source(self) -> None:
        work_id = self.add("plain", title="Plain", status="In Progress", abstract="An abstract",
                           author_text="Ann Lee", year="2020", publisher="Press", journal="J", volume="3",
                           issue="2", pages="1-9", isbn="978", doi="10.1/X", doc_type="book",
                           published_date="2020-01-02", location="Paris", edition="2",
                           urldate="2024-05-06", private_notes="  remember this  ")
        self.answer(work_commands.detail(self.db, self.ctx, work_id), {
            "abstract": "An abstract", "annotations": "[]", "arguments": [], "asset_count": 0,
            "author_text": "Ann Lee", "canonical_annotation_set_revision": 0, "citation_style": "",
            "created_at": "<s>", "doc_type": "book", "doi": "10.1/X", "edition": "2", "file_path": "",
            "file_size_bytes": None, "folder_id": None, "folder_title": None, "hide_pdf_link_annotations": 0,
            "id": "<W plain>", "isbn": "978", "issue": "2", "journal": "J", "last_opened_at": None,
            "location": "Paris", "manifestation_count": 1, "materialized_pdf_annotation_revision": 0,
            "pages": "1-9", "playlist_id": None, "playlist_title": None, "primary_asset_id": None,
            "primary_manifestation_id": "<MF plain>", "private_notes": "remember this", "provider": None,
            "provider_id": None, "published_date": "2020-01-02", "publisher": "Press",
            "research_refs": {"arguments": [], "concepts": []}, "roles": [], "source_kind": None,
            "source_mime": None, "source_url": None, "status": "In Progress", "tags": [], "text_content": "",
            "thumb_page": None, "thumb_url": None, "title": "Plain", "updated_at": "<s>",
            "urldate": "2024-05-06", "volume": "3", "year": "2020"})

    def test_a_managed_pdf_records_its_file(self) -> None:
        work_id = self.add_pdf("pdf", "paper.pdf", title="Paper", source_url="https://example.org/p",
                               thumb_page=3, source_mime="application/pdf", thumb_url="https://t")
        self.answer(work_commands.detail(self.db, self.ctx, work_id), {
            "file_path": works_fixtures.content_url("<AS pdf>", _PDF_BYTES), "file_size_bytes": len(_PDF_BYTES), "asset_count": 1,
            "source_kind": "pdf", "primary_asset_id": "<AS pdf>", "thumb_page": 3})
        self.answer(work_commands.thumbnail_fields(self.db, self.ctx, work_id), {
            "file_path": works_fixtures.content_url("<AS pdf>", _PDF_BYTES), "primary_asset_id": "<AS pdf>",
            "storage_locator": "paper.pdf", "thumb_page": 3})

    def test_a_video_is_a_stream(self) -> None:
        video = self.add("video", title="Video", source_kind="video", source_url=_VIDEO,
                         thumb_url="https://i.ytimg.com/x.jpg", urldate="2024-01-01")
        inferred = self.add("inferred", title="Inferred", source_url=_VIDEO)
        for work_id in (video, inferred):
            self.answer(work_commands.detail(self.db, self.ctx, work_id), {
                "source_kind": "video", "provider": "youtube", "provider_id": "dQw4w9WgXcQ",
                "asset_count": 1, "file_size_bytes": None, "file_path": ""})

    def test_a_contradictory_source_is_refused(self) -> None:
        for fields in (
            {"source_kind": "web"},
            {"source_kind": "video", "source_url": "https://example.org/not-a-video"},
            {"source_kind": "video", "source_url": _VIDEO, "provider_id": "other"},
            {"provider": "youtube"},
        ):
            with self.subTest(fields=fields):
                with self.assertRaises(ValueError):
                    works_fixtures.add_work(self.db, self.ctx, self.storage, "Refused", **fields)
        self.assertEqual(work_commands.catalog(self.db, self.ctx), [])

    def test_an_unknown_work_reads_as_none(self) -> None:
        self.assertIsNone(work_commands.detail(self.db, self.ctx, _MISSING))
        self.assertIsNone(work_commands.thumbnail_fields(self.db, self.ctx, _MISSING))

    def catalog(self) -> None:
        for title in ("beta", "Alpha", "_under", "Zed", "Ä umlaut", "ä umlaut"):
            self.add(title, title=title, abstract="x" * 150 if title == "Zed" else "")
        self.add_pdf("file", "f.pdf", title="file")
        self.add("stream", title="stream", source_url=_VIDEO)

    def test_the_catalog_rows(self) -> None:
        self.catalog()
        common = {"asset_count": 1, "linked_authors": None, "linked_people": [], "manifestation_count": 1,
                  "primary_author": None, "primary_editor": None, "status": "Not Started",
                  "doc_type": "article", "author_text": "", "year": "", "published_date": "",
                  "thumb_page": None, "thumb_url": None}
        file_row = {**common, "id": "<W file>", "title": "file", "file_path": works_fixtures.content_url("<AS file>", _PDF_BYTES),
                    "file_size_bytes": 32, "primary_asset_id": "<AS file>", "primary_manifestation_id": "<MF file>",
                    "provider": None, "provider_id": None, "source_kind": "pdf", "source_url": None}
        stream_row = {**common, "id": "<W stream>", "title": "stream", "file_path": "",
                      "file_size_bytes": None, "primary_asset_id": "<AS stream>",
                      "primary_manifestation_id": "<MF stream>", "provider": "youtube",
                      "provider_id": "dQw4w9WgXcQ", "source_kind": "video", "source_url": _VIDEO}
        detail = {"abstract": "", "created_at": "<s>", "updated_at": "<s>", "folder_id": None,
                  "last_opened_at": None, "source_mime": None, "urldate": None}
        calls = {
            "catalog": lambda: work_commands.catalog(self.db, self.ctx),
            "recently_added": lambda: works_fixtures.recently_added_works(self.db, self.ctx, self.storage),
            "recently_added_browse": lambda: work_commands.recently_added_browse(self.db, self.ctx, 50),
        }
        for call, read in calls.items():
            with self.subTest(call=call):
                rows = {row["title"]: row for row in self.labels.show(read())}
                self.assertEqual(len(rows), 8)
                extra = detail if call != "recently_added_browse" else {"created_at": "<s>"}
                self.answer([rows["file"], rows["stream"]], [{**file_row, **extra}, {**stream_row, **extra}])
        browse = work_commands.browse_catalog(self.db, self.ctx)
        self.assertEqual([row["title"] for row in browse],
                         ["_under", "Alpha", "beta", "file", "stream", "Zed", "Ä umlaut", "ä umlaut"])
        self.answer(browse[3:5], [{**file_row, "abstract_excerpt": ""}, {**stream_row, "abstract_excerpt": ""}])
        self.assertEqual(browse[5]["abstract_excerpt"], "x" * 100)

    def test_the_catalogue_reads_no_file(self) -> None:
        # A file size is the Asset's byte_size (#1427): no catalogue read touches the data root,
        # and a file gone from the disk still reports the size the database holds.
        self.catalog()
        opened_id = next(row["id"] for row in work_commands.catalog(self.db, self.ctx) if row["title"] == "file")
        self.assertTrue(work_commands.mark_opened(self.db, self.ctx, opened_id))
        os.remove(os.path.join(self.storage.pdfs_dir, "f.pdf"))
        root = os.path.realpath(self.storage.root)
        touched: list[str] = []
        real_stat, real_open = os.stat, open

        def under_root(path: Any) -> bool:
            return isinstance(path, (str, bytes, os.PathLike)) and \
                os.path.realpath(os.fsdecode(path)).startswith(root)

        def stat(path: Any, *args: Any, **kwargs: Any) -> Any:
            if under_root(path):
                touched.append(os.fsdecode(path))
            return real_stat(path, *args, **kwargs)

        def opened(path: Any, *args: Any, **kwargs: Any) -> Any:
            if under_root(path):
                touched.append(os.fsdecode(path))
            return real_open(path, *args, **kwargs)

        reads = {
            "catalog": lambda: work_commands.catalog(self.db, self.ctx),
            "browse_catalog": lambda: work_commands.browse_catalog(self.db, self.ctx),
            "recently_added_browse": lambda: work_commands.recently_added_browse(self.db, self.ctx, 50),
            "recent_browse": lambda: work_commands.recent_browse(self.db, self.ctx),
        }
        with mock.patch("os.stat", stat), mock.patch("builtins.open", opened):
            answers = {name: read() for name, read in reads.items()}
        self.assertEqual(touched, [])
        for name in reads:
            with self.subTest(read=name):
                self.assertEqual([row["file_size_bytes"] for row in answers[name] if row["title"] == "file"], [32])

    def test_limits(self) -> None:
        self.catalog()
        self.assertEqual(len(work_commands.recently_added_browse(self.db, self.ctx, 3)), 3)
        self.assertEqual(len(works_fixtures.recently_added_works(self.db, self.ctx, self.storage, 2)), 2)
        self.assertEqual(len(works_fixtures.recently_added_works(self.db, self.ctx, self.storage, -1)), 8)

    def test_nothing_opened_yet(self) -> None:
        self.catalog()
        self.assertEqual(work_commands.recent_browse(self.db, self.ctx), [])
        self.assertEqual(works_fixtures.recent_works(self.db, self.ctx, self.storage, 5), [])
        self.assertEqual(works_fixtures.etag_recent(self.db, self.ctx), 'W/"prks-recent-0-"')

    def test_summaries_follow_the_requested_order(self) -> None:
        a = self.add("a", title="A")
        b = self.add("b", title="B")
        rows = works_fixtures.summaries_by_ids(self.db, self.ctx, self.storage, [b, " ", "W-" + "F" * 32, a, b])
        self.answer(rows, [{"id": "<W b>", "title": "B", "primary_manifestation_id": "<MF b>"},
                           {"id": "<W a>", "title": "A"}, {"id": "<W b>", "title": "B"}])
        self.assertEqual(works_fixtures.summaries_by_ids(self.db, self.ctx, self.storage, []), [])

    def test_etags_follow_the_representation(self) -> None:
        self.catalog()
        rows = work_commands.catalog(self.db, self.ctx)
        tag = etags.etag_for_representation("works", rows)
        self.assertRegex(tag, r'^W/"prks-works-8-[0-9a-f]{32}"$')
        self.answer(works_fixtures.etag_recently_added(self.db, self.ctx), 'W/"prks-recently-added-8-<s>"')
        self.add("later", title="Later")
        self.assertNotEqual(etags.etag_for_representation("works", work_commands.catalog(self.db, self.ctx)), tag)


if __name__ == "__main__":
    unittest.main()
