"""PDF text for search: ``text.extract`` and ``derived.asset_text`` (L-S-1 #637; §8.3, §8.7).

The job reads the stored bytes and writes one row per Asset and content
hash; the commands that give an Asset new bytes, or delete it, keep the
rows to the current bytes. The orderings of §8.3 are run with paused
transactions: an extraction against a Work delete and against a content
replace, in both orders. A failed extraction is an outcome, a missing
extractor is not, and a text too long for a ``tsvector`` is cut, not lost.
"""

from __future__ import annotations

import threading
from typing import Any, BinaryIO

from backend.domain.identity.library_scope import LibraryScope
from backend.domain.objects import ingest, replace
from backend.domain.search import text as search_text
from backend.domain.works import commands
from backend.jobs import queue
from backend.jobs.handlers import REGISTRY, HandlerRegistry
from backend.jobs.runner import Outcome, run_job
from backend.storage.objects import LocalFilesystemStorage
from backend.text_index import PDFTextExtractorUnavailable
from tests.pg.jobs_fixtures import LEASE
from tests.pg.test_search_refresh import SearchTestCase


def pdf_bytes(text: str) -> bytes:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    if text:
        page.insert_text((72, 72), text)
    out = doc.tobytes()
    doc.close()
    return out


class TextTestCase(SearchTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.objects = LocalFilesystemStorage.for_config(self.storage)
        self.extracted: list[str] = []
        self.fake: Any = None

    def registry(self) -> HandlerRegistry:
        registry = REGISTRY.copy()
        search_text.attach(registry, self.objects).extractor = self.extract
        return registry

    def extract(self, path: str) -> tuple[str, bool]:
        self.extracted.append(path)
        if self.fake is None:
            return search_text.extract_pdf(path)
        if isinstance(self.fake, BaseException):
            raise self.fake
        return self.fake, False

    def run_jobs(self) -> list[Outcome]:
        registry = self.registry()
        outcomes = []
        while True:
            job = queue.claim(self.db, "test-worker", lease=LEASE)
            if job is None:
                return outcomes
            outcomes.append(run_job(self.db, job, registry=registry, lease=LEASE))

    def pdf_work(self, title: str, text: str, name: str = "p.pdf") -> str:
        return self.library.add_work(title, file_path=self.store_pdf(name, pdf_bytes(text)))

    def asset(self, work_id: str) -> dict[str, Any]:
        return self.sql("SELECT a.id, a.content_sha256 FROM prks.assets a JOIN prks.manifestations m"
                        " ON m.id = a.manifestation_id JOIN prks.works w ON w.primary_manifestation_id = m.id"
                        " WHERE w.id = %s", (work_id,))[0]

    def rows(self, work_id: str) -> list[dict[str, Any]]:
        return self.sql("SELECT t.content_sha256, t.status, t.truncated, t.text FROM derived.asset_text t"
                        " JOIN prks.assets a ON a.id = t.asset_id WHERE a.work_id = %s", (work_id,))

    def all_rows(self) -> int:
        return int(self.scalar("SELECT count(*) FROM derived.asset_text"))

    def found(self, query: str) -> list[str]:
        return self.command(lambda scope: search_text.pdf_text_work_ids(scope, query))

    def queued(self) -> list[str]:
        rows = self.sql("SELECT payload->>'asset_id' AS asset_id FROM prks.jobs"
                        " WHERE kind = %s AND state = 'queued' ORDER BY created_at, id", (search_text.KIND,))
        return [row["asset_id"] for row in rows]

    def written(self, data: bytes) -> ingest.IngestedPdf:
        def write(handle: BinaryIO) -> None:
            handle.write(data)

        return ingest.store_pdf(self.objects, "replaced.pdf", write)


class ExtractionTests(TextTestCase):
    def test_a_created_pdf_is_queued_extracted_and_found(self) -> None:
        work = self.pdf_work("Paper", "Hello searchable world")
        self.assertEqual(self.queued(), [self.asset(work)["id"]])
        self.assertEqual(self.run_jobs(), [Outcome.SUCCEEDED])
        [row] = self.rows(work)
        self.assertEqual((row["content_sha256"], row["status"], row["truncated"]),
                         (self.asset(work)["content_sha256"], "indexed", False))
        self.assertIn("Hello searchable world", row["text"])
        self.assertEqual(self.found("SEARCHA wor"), [work])
        self.assertEqual(self.found("absent"), [])

    def test_a_work_without_a_pdf_queues_nothing(self) -> None:
        self.library.add_work("Plain")
        self.library.add_work("Video", source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        self.assertEqual(self.queued(), [])

    def test_greek_pdf_text_folds_case_sigma_and_accents(self) -> None:
        work = self.pdf_work("Greek", "")
        self.fake = "Ο Σωκράτης και η Πολιτεία"
        self.run_jobs()
        for query in ("ΣΩΚΡΑΤΗΣ", "σωκρατησ", "σωκρ", "πολιτεια"):
            with self.subTest(query=query):
                self.assertEqual(self.found(query), [work])

    def test_an_empty_pdf_is_empty_and_an_unreadable_one_failed(self) -> None:
        empty = self.pdf_work("Empty", "", "e.pdf")
        broken = self.library.add_work("Broken", file_path=self.store_pdf("b.pdf", b"%PDF-1.4 not a pdf"))
        self.assertEqual(self.run_jobs(), [Outcome.SUCCEEDED, Outcome.SUCCEEDED])
        self.assertEqual([(r["status"], r["text"]) for r in self.rows(empty)], [("empty", "")])
        self.assertEqual([(r["status"], r["text"]) for r in self.rows(broken)], [("failed", "")])

    def test_a_missing_extractor_records_no_outcome(self) -> None:
        work = self.pdf_work("Paper", "words")
        self.fake = PDFTextExtractorUnavailable()
        self.assertEqual(self.run_jobs(), [Outcome.RETRYING])
        self.assertEqual(self.rows(work), [])

    def test_a_repeated_job_reads_nothing_again(self) -> None:
        work = self.pdf_work("Paper", "words")
        self.run_jobs()
        self.command(lambda scope: search_text.queue.enqueue_library_job(
            scope, search_text.KIND, {"asset_id": self.asset(work)["id"]}))
        self.extracted.clear()
        self.assertEqual(self.run_jobs(), [Outcome.SUCCEEDED])
        self.assertEqual(self.extracted, [])
        self.assertEqual(len(self.rows(work)), 1)

    def test_a_text_too_long_for_a_tsvector_is_cut_and_marked(self) -> None:
        work = self.pdf_work("Long", "")
        self.fake = " ".join(f"w{i:07d}" for i in range(200_000))
        self.assertEqual(self.run_jobs(), [Outcome.SUCCEEDED])
        [row] = self.rows(work)
        self.assertEqual((row["status"], row["truncated"]), ("indexed", True))
        self.assertTrue(self.fake.startswith(row["text"]))
        self.assertEqual(self.found("w0000001"), [work])

    def test_another_librarys_pdf_is_never_found(self) -> None:
        mine = self.pdf_work("Mine", "shared words", "m.pdf")
        self.bind(self.other_actor().context)
        self.pdf_work("Theirs", "shared words", "t.pdf")
        self.bind(self.actor.context)
        self.run_jobs()
        self.assertEqual(self.all_rows(), 2)
        self.assertEqual(self.found("shared"), [mine])


class CommandTests(TextTestCase):
    def test_a_replace_drops_the_old_text_and_queues_the_new(self) -> None:
        work = self.pdf_work("Paper", "first version")
        self.run_jobs()
        self.library._replace_work_pdf(work, lambda handle: handle.write(pdf_bytes("second edition")))
        self.assertEqual(self.rows(work), [])
        self.assertEqual(self.queued(), [self.asset(work)["id"]])
        self.run_jobs()
        self.assertEqual(self.found("second"), [work])
        self.assertEqual(self.found("first"), [])

    def test_a_deleted_work_leaves_no_text(self) -> None:
        work = self.pdf_work("Paper", "doomed words")
        self.run_jobs()
        self.library.delete_work_record(work)
        self.assertEqual(self.all_rows(), 0)

    def test_a_job_for_a_deleted_asset_writes_nothing(self) -> None:
        work = self.pdf_work("Paper", "words")
        [job] = self.queued()
        self.library.delete_work_record(work)
        self.run_jobs()
        self.assertEqual(self.scalar("SELECT state FROM prks.jobs WHERE payload->>'asset_id' = %s", job),
                         "succeeded")
        self.assertEqual(self.all_rows(), 0)


class OrderingTests(TextTestCase):
    """§8.3: the Asset row orders an extraction against a delete or a content switch."""

    def setUp(self) -> None:
        super().setUp()
        self.work = self.pdf_work("Paper", "old words")
        found = self.asset(self.work)
        self.asset_id, self.sha256 = str(found["id"]), str(found["content_sha256"])
        self.sql("DELETE FROM prks.jobs")

    def write_old(self, scope: LibraryScope) -> bool:
        return search_text.write_text(scope, self.asset_id, self.sha256, "indexed", "old words", False)

    def test_a_delete_waits_for_an_extraction_then_removes_its_row(self) -> None:
        with self.paused(self.write_old):
            thread, out = self.started(lambda scope: commands.delete_work(scope, self.work))
            self.wait_for_lock_waiter()
        thread.join(10)
        self.assertIsNotNone(out[0])
        self.assertEqual(self.all_rows(), 0)

    def test_an_extraction_after_a_delete_that_locked_first_writes_nothing(self) -> None:
        with self.paused(lambda scope: commands.delete_work(scope, self.work)):
            thread, out = self.started(self.write_old)
            self.wait_for_lock_waiter()
        thread.join(10)
        self.assertEqual(out, [False])
        self.assertEqual(self.all_rows(), 0)

    def test_a_replace_waits_for_an_extraction_then_removes_its_row(self) -> None:
        written = self.written(pdf_bytes("new words"))
        with self.paused(self.write_old):
            thread, out = self.started(lambda scope: replace.commit(scope, self.objects, self.work, written))
            self.wait_for_lock_waiter()
        thread.join(10)
        self.assertIsInstance(out[0], replace.Replaced)
        self.assertEqual(self.rows(self.work), [])

    def test_an_extraction_of_replaced_bytes_writes_nothing(self) -> None:
        written = self.written(pdf_bytes("new words"))
        with self.paused(lambda scope: replace.commit(scope, self.objects, self.work, written)):
            thread, out = self.started(self.write_old)
            self.wait_for_lock_waiter()
        thread.join(10)
        self.assertEqual(out, [False])
        self.assertEqual(self.rows(self.work), [])

    def test_a_job_that_read_old_bytes_writes_nothing_after_a_replace(self) -> None:
        # The job reads the Asset, then extracts outside any transaction;
        # the bytes change in between.
        replaced = threading.Event()

        def extract_then_replace(path: str) -> tuple[str, bool]:
            if not replaced.is_set():
                replaced.set()
                self.library._replace_work_pdf(self.work, lambda handle: handle.write(pdf_bytes("new words")))
            return search_text.extract_pdf(path)

        self.command(lambda scope: search_text.queue.enqueue_library_job(
            scope, search_text.KIND, {"asset_id": self.asset_id}))
        registry = REGISTRY.copy()
        search_text.attach(registry, self.objects).extractor = extract_then_replace
        job = queue.claim(self.db, "test-worker", lease=LEASE)
        assert job is not None
        self.assertIs(run_job(self.db, job, registry=registry, lease=LEASE), Outcome.SUCCEEDED)
        self.assertEqual(self.rows(self.work), [])
        self.run_jobs()
        [row] = self.rows(self.work)
        self.assertEqual(row["content_sha256"], self.asset(self.work)["content_sha256"])
        self.assertIn("new words", row["text"])

