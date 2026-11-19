"""PRKS's own PDF pipeline over a synthetic adversarial corpus (#1287).

Each case of :mod:`tests.pdf_corpus` (generated at test time, never committed
as a document) goes through the real HTTP routes the way a browser sends it:
upload into the Processing queue, import as a Work, the ``text.extract`` job,
search, the first-page thumbnail and the reader's byte stream, then
linearization through ``ingest.store_pdf``. Every case has one asserted
outcome per stage, from :data:`EXPECTED`:

- an upload is kept (201) or refused with a documented code, and a refusal
  leaves no item;
- an import always creates exactly one Work and one Asset: PRKS does not parse
  the PDF to import it;
- extraction ends ``indexed``, ``empty`` or ``failed`` and the job itself
  succeeds, within :data:`EXTRACT_SECONDS`;
- the thumbnail is an image or the documented 404 ``Thumbnail unavailable``;
- linearization writes a copy or keeps the source, never fails the store;
- markup that shares one annotation id is adopted once;
- the whole run stays under :data:`PEAK_MEMORY_GROWTH_KIB` of extra peak
  memory, and the server keeps answering afterwards.

The PyMuPDF and qpdf parsers themselves are fuzzed upstream (OSS-Fuzz); this
checks how PRKS behaves around them.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
from typing import Any, BinaryIO, Callable, Optional, TypeVar
from unittest import mock

from backend.domain.objects import ingest
from backend.domain.search import text as search_text
from backend.jobs import queue
from backend.jobs.handlers import REGISTRY
from backend.jobs.runner import Outcome, run_job
from backend.storage.objects import LocalFilesystemStorage
from tests.pdf_corpus import CORPUS, DUPLICATE_ANNOTATION_ID, MARKER, PDF_ENCRYPT_AES_256, Case
from tests.pg.jobs_fixtures import LEASE
from tests.pg.test_http_processing import ProcessingHttpTestCase

# Per case: (text status, thumbnail answers, linearized when qpdf is present).
# ``None`` for a case the upload refuses, so nothing after it runs.
EXPECTED: dict[str, Optional[tuple[str, int, bool]]] = {
    "valid": ("indexed", 200, True),
    "truncated": ("empty", 200, False),  # MuPDF repairs it to one blank page; qpdf refuses it
    "damaged_xref": ("indexed", 200, True),  # qpdf exits 3 (warnings); its repaired copy keeps pages and text
    "encrypted": ("failed", 404, False),
    "zero_pages": ("empty", 404, False),
    "many_pages": ("indexed", 200, True),
    "rotated": ("indexed", 200, True),
    "no_text": ("empty", 200, True),
    "odd_font_encoding": ("indexed", 200, True),
    "duplicate_annotation_ids": ("indexed", 200, True),
    "header_then_garbage": ("failed", 404, False),
    "not_a_pdf": None,
}
# Bounds far above what each case takes locally (all well under a second).
EXTRACT_SECONDS = 10.0
LINEARIZE_SECONDS = 10.0
# The process's peak resident size may grow by at most this over the corpus.
PEAK_MEMORY_GROWTH_KIB = 256 * 1024

T = TypeVar("T")


def bounded(seconds: float, call: Callable[[], T]) -> T:
    """``call()``'s answer, or a failure once it has run ``seconds``: a hang fails, it never blocks the suite.

    A call still running then is left on a daemon thread; the test has failed.
    """
    done: list[T] = []
    errors: list[BaseException] = []

    def target() -> None:
        try:
            done.append(call())
        except BaseException as exc:  # noqa: BLE001 - re-raised on the test's thread
            errors.append(exc)

    worker = threading.Thread(target=target, name="corpus-bounded", daemon=True)
    worker.start()
    worker.join(seconds)
    if worker.is_alive():
        raise AssertionError(f"still running after {seconds} s")
    if errors:
        raise errors[0]
    return done[0]


def reset_peak_rss() -> bool:
    """Reset this process's peak resident size (Linux ``clear_refs``), so earlier tests' peaks cannot mask growth."""
    try:
        with open("/proc/self/clear_refs", "w", encoding="ascii") as handle:
            handle.write("5")
        return True
    except OSError:
        return False


def peak_rss_kib() -> int:
    """The peak resident size since :func:`reset_peak_rss`, in KiB (``VmHWM``)."""
    with open("/proc/self/status", encoding="ascii") as handle:
        for line in handle:
            if line.startswith("VmHWM:"):
                return int(line.split()[1])
    raise AssertionError("no VmHWM in /proc/self/status")


class AdversarialPdfCorpusTests(ProcessingHttpTestCase):
    def setUp(self) -> None:
        super().setUp()
        storage = self.server_module._bound_storage
        assert storage is not None
        self.objects = LocalFilesystemStorage.for_config(storage)

    def run_jobs(self) -> list[Outcome]:
        registry = REGISTRY.copy()
        search_text.attach(registry, self.objects)
        outcomes = []
        while (job := queue.claim(self.db, "corpus-worker", lease=LEASE)) is not None:
            outcomes.append(run_job(self.db, job, registry=registry, lease=LEASE))
        return outcomes

    def counts(self) -> dict[str, int]:
        return {table: int(self.run_sql(f"SELECT count(*) AS n FROM prks.{table} WHERE library_id = %s",  # noqa: S608
                                        (self.lib,))[0]["n"])
                for table in ("works", "assets", "processing_items")}

    def text_status(self, work_id: str) -> dict[str, Any]:
        rows = self.run_sql("SELECT t.status, t.text FROM derived.asset_text t JOIN prks.assets a ON a.id = t.asset_id"
                            " WHERE a.work_id = %s", (work_id,))
        self.assertEqual(len(rows), 1, rows)
        return rows[0]

    def test_the_corpus_is_generated_small_and_covers_the_issue(self) -> None:
        import pymupdf

        self.assertEqual(PDF_ENCRYPT_AES_256, getattr(pymupdf, "PDF_ENCRYPT_AES_256"))
        names = {case.name for case in CORPUS}
        self.assertEqual(names, set(EXPECTED))
        for case in CORPUS:
            with self.subTest(case=case.name):
                data = case.build()
                self.assertLess(len(data), 1_000_000)
                self.assertEqual(data.startswith(b"%PDF-"), case.pdf_magic)

    def test_every_case_has_its_documented_outcome_at_each_stage(self) -> None:
        # Linux only: the peak is reset here and read back as VmHWM, both in KiB.
        measured = reset_peak_rss()
        start = peak_rss_kib() if measured else 0
        for case in CORPUS:
            with self.subTest(case=case.name):
                self.check(case, EXPECTED[case.name])
        # The server still serves the whole library afterwards.
        works = self.ok(self.get("/api/works"))
        self.assertEqual(len(works), sum(1 for value in EXPECTED.values() if value is not None))
        if measured:
            self.assertLess(peak_rss_kib() - start, PEAK_MEMORY_GROWTH_KIB)

    def check(self, case: Case, expected: Optional[tuple[str, int, bool]]) -> None:
        data = case.build()
        before = self.counts()
        res = self.raw_upload(data, key=f"corpus-{case.name}", filename=f"{case.name}.pdf")
        if expected is None:
            self.refused(res, 415, "PROCESSING_NOT_A_PDF")
            self.assertEqual(self.counts(), before)
            return
        self.assertEqual(res.status, 201, res.raw)
        item = res.body["item"]
        done = self.ok(self.send("POST", f"/api/processing-items/{item['id']}/import", {}), 201)
        work_id = done["work_id"]
        after = self.counts()
        self.assertEqual((after["works"] - before["works"], after["assets"] - before["assets"]), (1, 1))

        status, thumbnail, linearized = expected
        outcomes = bounded(EXTRACT_SECONDS, self.run_jobs)
        self.assertTrue(outcomes, "the import queued no extraction")
        self.assertEqual(set(outcomes), {Outcome.SUCCEEDED})
        row = self.text_status(work_id)
        self.assertEqual(row["status"], status)
        self.assertNotIn("\x00", row["text"] or "")
        found = [hit["id"] for hit in self.ok(self.get(f"/api/search?q={MARKER}&any=1"))]
        self.assertEqual(work_id in found, status == "indexed")

        thumb = self.get(f"/api/works/{work_id}/thumbnail")
        self.assertEqual(thumb.status, thumbnail, thumb.raw[:200])
        if thumbnail == 200:
            self.assertTrue(thumb.header("Content-Type")[0].startswith("image/"))
        # The reader gets the stored bytes back unchanged, whatever they are.
        file_path = self.ok(self.get(f"/api/works/{work_id}"))["file_path"]
        stream = self.get(file_path)
        self.assertEqual(stream.status, 200)
        self.assertEqual(stream.raw, data)

        if case.name == "duplicate_annotation_ids":
            self.check_adopt_once(work_id, data)
        self.check_linearize(case, data, linearized)

    def check_adopt_once(self, work_id: str, data: bytes) -> None:
        """The viewer reports each markup it finds in the bytes; one shared id is adopted once."""
        import pymupdf

        viewer = []
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            for annot in doc[0].annots():
                rect = annot.rect
                viewer.append({"id": annot.info["id"], "type": 9 if annot.type[0] == 8 else 1, "pageIndex": 0,
                               "contents": annot.info.get("content", ""),
                               "rect": {"origin": {"x": rect.x0, "y": rect.y0},
                                        "size": {"width": rect.width, "height": rect.height}}})
        self.assertEqual(len(viewer), 3)
        self.assertEqual({item["id"] for item in viewer}, {DUPLICATE_ANNOTATION_ID})
        adopted = self.ok(self.send("POST", f"/api/works/{work_id}/annotations/adopt", {"viewer_annotations": viewer}))
        self.assertEqual(adopted["adopted"], [DUPLICATE_ANNOTATION_ID])
        stored = json.loads(self.ok(self.get(f"/api/works/{work_id}/annotations"))["annotations_json"])
        self.assertEqual([item["id"] for item in stored], [DUPLICATE_ANNOTATION_ID])

    def check_linearize(self, case: Case, data: bytes, linearized: bool) -> None:
        """Pin PRKS_PDF_LINEARIZE for each store, so the host's own setting cannot decide the answer."""
        def write(handle: BinaryIO) -> None:
            handle.write(data)

        def store(setting: str) -> ingest.IngestedPdf:
            with mock.patch.dict(os.environ, {"PRKS_PDF_LINEARIZE": setting}):
                return bounded(LINEARIZE_SECONDS,
                               lambda: ingest.store_pdf(self.objects, f"{case.name}.pdf", write, context="corpus"))

        # Switched off, the stored bytes are kept as they are, even with qpdf installed.
        stored = store("0")
        self.assertEqual(stored.linearize, "disabled")
        self.assertEqual(stored.working, stored.source)
        stored = store("1")
        if shutil.which("qpdf") is None:
            self.assertEqual(stored.linearize, "missing-qpdf")
            self.assertEqual(stored.working, stored.source)
            return
        self.assertEqual(stored.linearize in ("ok", "ok-repaired"), linearized, stored.linearize)
        self.assertEqual(stored.working != stored.source, linearized)
        if not linearized:
            self.assertIn(stored.linearize, ("qpdf-failed", "qpdf-warnings", "already"))
        with self.objects.local_path(stored.working.key) as path:
            self.assertGreater(os.path.getsize(path), 0)
