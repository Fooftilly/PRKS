"""Work create and reads on PostgreSQL: what parity cannot show (L-W-1 #628).

``tests/test_works_core_parity.py`` proves the facade returns SQLite's JSON.
These tests cover what only PostgreSQL has: the bound actor and its checks,
Library isolation, the rows create writes, timestamp precision and order,
and concurrent creates.
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
from backend.domain.works import queries
from backend.pg_facade import PostgresLibrary
from backend.pg_facade import context as facade_context
from backend.storage.config import StorageConfig
from tests.pg.facade_context import Actor, provision_actor
from tests.pg.harness import PostgresHarness, get_harness

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
        for call in (self.library.get_all_works, lambda: self.library.add_work("Unbound"),
                     self.library.etag_recent_works):
            with self.assertRaises(facade_context.NoRequestContext):
                unbound.run(call)
        self.assertEqual(self.count("works"), 0)
        with facade_context.acting_as(self.actor.context):
            self.library.add_work("Bound")
        self.assertEqual([w["title"] for w in self.library.get_all_works()], ["Bound"])

    def test_the_binding_is_per_thread(self) -> None:
        seen: list[BaseException] = []

        def run() -> None:
            try:
                self.library.get_all_works()
            except BaseException as exc:  # noqa: BLE001 - recorded for the assertion
                seen.append(exc)

        thread = threading.Thread(target=run)
        thread.start()
        thread.join()
        self.assertEqual(len(seen), 1)
        self.assertIsInstance(seen[0], facade_context.NoRequestContext)

    def test_a_reader_reads_but_cannot_create(self) -> None:
        work_id = self.library.add_work("Shared")
        with facade_context.acting_as(self.reader_context()):
            self.assertEqual(self.library.get_work(work_id)["title"], "Shared")
            with self.assertRaises(AccessRefused) as caught:
                self.library.add_work("Refused")
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

        with mock.patch.object(PostgresLibrary, "_managed_file",
                               side_effect=AssertionError("file touched")) as touched:
            for path in (existing, missing):
                with self.assertRaises(facade_context.NoRequestContext):
                    unbound.run(lambda p=path: self.library.add_work("Unbound", file_path=p))
                with self.assertRaises(AccessRefused) as caught:
                    as_reader(lambda p=path: self.library.add_work("Reader", file_path=p))
                self.assertIs(caught.exception.refusal, Refusal.FORBIDDEN)
        touched.assert_not_called()
        self.assertEqual(self.count("works"), 0)
        self.assertEqual(self.count("assets"), 0)

    def test_a_create_prepared_before_a_restore_is_refused(self) -> None:
        self.sql("UPDATE prks.libraries SET epoch = epoch + 1 WHERE id = %s", (self.actor.library_id,))
        with self.assertRaises(AccessRefused) as caught:
            self.library.add_work("Stale")
        self.assertIs(caught.exception.refusal, Refusal.LIBRARY_EPOCH_CHANGED)
        self.assertEqual(self.count("works"), 0)
        self.assertEqual(self.library.get_all_works(), [])  # reads are not fenced by epoch


class LibraryIsolationTests(WorksFacadeTestCase):
    def test_another_librarys_works_are_invisible(self) -> None:
        mine = self.library.add_work("Mine")
        other = self.other_actor()
        with facade_context.acting_as(other.context):
            theirs = self.library.add_work("Theirs", source_url=_VIDEO)
            self.library.add_work("Theirs too")
        self.assertEqual([w["id"] for w in self.library.get_all_works()], [mine])
        self.assertEqual([w["id"] for w in self.library.get_works_browse_catalog()], [mine])
        self.assertEqual([w["id"] for w in self.library.get_recently_added_browse()], [mine])
        self.assertIsNone(self.library.get_work(theirs))
        self.assertIsNone(self.library.get_primary_thumbnail_fields(theirs))
        self.assertEqual([w["id"] for w in self.library.get_work_summaries_by_ids_ordered([theirs, mine])],
                         [mine])
        self.assertRegex(self.library.etag_recently_added_works(), r'^W/"prks-recently-added-1-')
        self.sql("UPDATE prks.works SET last_opened_at = now()")
        self.assertEqual([w["id"] for w in self.library.get_recent_browse()], [mine])
        self.assertRegex(self.library.etag_recent_works(), r'^W/"prks-recent-1-')
        with facade_context.acting_as(other.context):
            self.assertEqual(len(self.library.get_all_works()), 2)

    def test_a_managed_pdf_of_another_library_cannot_be_adopted(self) -> None:
        path = self.store_pdf("p.pdf", b"%PDF-1.4\nmine\n%%EOF\n")
        mine = self.library.add_work("Mine", file_path=path)
        other = self.other_actor()
        works_before = self.count("works")
        with facade_context.acting_as(other.context):
            with self.assertRaisesRegex(ValueError, "does not exist"):
                self.library.add_work("Theirs", file_path=path)
            self.assertEqual(self.library.get_all_works(), [])
        self.assertEqual(self.count("works"), works_before)
        self.assertEqual(self.sql("SELECT work_id FROM prks.assets WHERE storage_locator = 'p.pdf'"),
                         [{"work_id": mine}])
        # Within one Library two Works may still share a file, as on SQLite.
        self.library.add_work("Mine again", file_path=path)

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
                    self.library.add_work("Race", file_path=path)
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
        work_id = self.library.add_work("Paper", file_path=self.store_pdf("p.pdf", data),
                                        source_url="https://example.org/p", thumb_page="2")
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
        work_id = self.library.add_work("Video", source_url=_VIDEO + "&t=3")
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
                    self.library.add_work("Refused", **fields)
        for table in ("works", "manifestations", "assets", "work_notes"):
            self.assertEqual(self.count(table), 0, table)

    def test_notes_are_rows_of_their_own(self) -> None:
        with_notes = self.library.add_work("Notes", text_content="# Heading", private_notes="  keep  ")
        without = self.library.add_work("None", text_content="", private_notes="   ")
        self.assertEqual(
            self.sql("SELECT work_id, kind, body FROM prks.work_notes ORDER BY kind"),
            [{"work_id": with_notes, "kind": "private", "body": "keep"},
             {"work_id": with_notes, "kind": "research", "body": "# Heading"}])
        self.assertEqual((self.core(with_notes)["text_content"], self.core(with_notes)["private_notes"]),
                         ("# Heading", "keep"))
        self.assertEqual((self.core(without)["text_content"], self.core(without)["private_notes"]),
                         ("", None))

    def test_a_work_with_a_research_note_needs_the_wiki_link_resolver(self) -> None:
        from backend.pg_facade import NotPortedError

        work_id = self.library.add_work("Linked", text_content="See [[Other]]")
        with self.assertRaises(NotPortedError) as caught:
            self.library.get_work(work_id)
        self.assertEqual(caught.exception.method, "resolve_wiki_links")

    def test_concurrent_creates_all_commit(self) -> None:
        errors: list[BaseException] = []
        created: list[str] = []
        lock = threading.Lock()
        start = threading.Barrier(6)

        def run(index: int) -> None:
            try:
                with facade_context.acting_as(self.actor.context):
                    start.wait()
                    work_id = self.library.add_work(f"Work {index}", source_url=_VIDEO)
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
        self.assertEqual(sorted(w["id"] for w in self.library.get_all_works()), sorted(created))
        self.assertEqual(self.count("assets"), 6)


class OrderAndPrecisionTests(WorksFacadeTestCase):
    def three(self) -> list[str]:
        ids = [self.library.add_work(title) for title in ("a", "b", "c")]
        return sorted(ids)

    def test_recent_orders_at_millisecond_precision_then_by_id(self) -> None:
        first, second, third = self.three()
        self.sql("UPDATE prks.works SET last_opened_at = '2026-01-02 03:04:05.123456+00' WHERE id = %s", (third,))
        self.sql("UPDATE prks.works SET last_opened_at = '2026-01-02 03:04:05.123999+00' WHERE id = %s", (first,))
        self.sql("UPDATE prks.works SET last_opened_at = '2026-01-02 03:04:05.124+00' WHERE id = %s", (second,))
        rows = self.library.get_recent_browse()
        self.assertEqual([(r["id"], r["last_opened_at"]) for r in rows], [
            (second, "2026-01-02 03:04:05.124"),
            (first, "2026-01-02 03:04:05.123"),   # same rendered millisecond: by id
            (third, "2026-01-02 03:04:05.123"),
        ])
        self.assertEqual([r["id"] for r in self.library.get_recent_works(2)], [second, first])
        self.assertEqual(self.library.etag_recent_works(), 'W/"prks-recent-3-2026-01-02 03:04:05.124"')

    def test_added_orders_at_second_precision_then_by_id(self) -> None:
        first, second, third = self.three()
        self.sql("UPDATE prks.works SET created_at = '2026-01-02 03:04:05.9+00' WHERE id = %s", (third,))
        self.sql("UPDATE prks.works SET created_at = '2026-01-02 03:04:05.1+00' WHERE id = %s", (first,))
        self.sql("UPDATE prks.works SET created_at = '2026-01-02 03:04:06+00' WHERE id = %s", (second,))
        for rows in (self.library.get_all_works(), self.library.get_recently_added_browse()):
            self.assertEqual([(r["id"], r["created_at"]) for r in rows], [
                (second, "2026-01-02 03:04:06"),
                (first, "2026-01-02 03:04:05"),
                (third, "2026-01-02 03:04:05"),
            ])
        self.assertEqual(self.library.etag_recently_added_works(),
                         'W/"prks-recently-added-3-2026-01-02 03:04:06"')

    def test_timestamps_render_in_utc_whatever_the_session_zone(self) -> None:
        work_id = self.library.add_work("Zoned")
        self.sql("UPDATE prks.works SET created_at = '2026-06-01 12:00:00+02' WHERE id = %s", (work_id,))
        self.sql("ALTER DATABASE " + self.harness_db_name() + " SET timezone = 'America/New_York'")
        self.db.close()
        self.db = Database(DatabaseConfig(conninfo=self.db.config.conninfo, role="test", pool_min=0,
                                          pool_max=2))
        self.addCleanup(self.db.close)
        self.library = PostgresLibrary(self.db, storage=self.storage)
        self.assertEqual(self.library.get_all_works()[0]["created_at"], "2026-06-01 10:00:00")

    def harness_db_name(self) -> str:
        return str(self.sql("SELECT current_database() AS n")[0]["n"])


if __name__ == "__main__":
    unittest.main()
