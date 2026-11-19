"""Time-bounded PDF text extraction in a child process (#1321).

``extract_pdf_bounded`` runs ``extract_pdf`` in ``python -m backend.pdf_text``
and kills it after a timeout. A timeout or a child that leaves no result is a
per-PDF ``PDFTextExtractionError``, never an empty document; a child without
PyMuPDF is ``PDFTextExtractorUnavailable`` so the job is retried.
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from typing import Any
from unittest import mock

from backend import pdf_text
from backend.pdf_text import (
    PDFTextExtractionError,
    PDFTextExtractorUnavailable,
    extract_pdf_bounded,
    same_pages_bounded,
)

try:
    import pymupdf  # noqa: F401

    HAVE_PYMUPDF = True
except ImportError:  # pragma: no cover - CI installs it
    HAVE_PYMUPDF = False


def _child_writing(result: Any):
    """A stand-in for ``subprocess.run`` whose child writes ``result`` (None: writes nothing)."""
    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if result is not None:
            with open(os.path.join(kwargs["cwd"], "result.json"), "w", encoding="utf-8") as handle:
                json.dump(result, handle)
        return subprocess.CompletedProcess(argv, 0 if result is not None else -11)
    return run


class BoundedExtractionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.pdf = os.path.join(self.scratch.name, "a.pdf")
        with open(self.pdf, "wb") as handle:
            handle.write(b"%PDF-1.4 placeholder")

    def reason(self, **kwargs: Any) -> str:
        with self.assertRaises(PDFTextExtractionError) as caught:
            extract_pdf_bounded(self.pdf, **kwargs)
        return caught.exception.reason

    def test_a_child_past_the_timeout_is_killed_and_failed(self) -> None:
        # A real child: starting Python alone takes longer than this bound.
        self.assertEqual(self.reason(timeout=0.001), "extraction_timeout")

    def test_the_timeout_is_always_passed_to_the_child(self) -> None:
        with mock.patch.object(pdf_text.subprocess, "run", side_effect=_child_writing(
                {"text": "x", "empty": False, "truncated": False})) as run:
            extract_pdf_bounded(self.pdf)
        self.assertEqual(run.call_args.kwargs["timeout"], pdf_text.EXTRACT_TIMEOUT_SECONDS)

    def test_a_child_that_leaves_no_result_is_failed_not_empty(self) -> None:
        with mock.patch.object(pdf_text.subprocess, "run", side_effect=_child_writing(None)):
            self.assertEqual(self.reason(), "extractor_crashed")

    def test_a_child_without_pymupdf_means_the_extractor_is_unavailable(self) -> None:
        with mock.patch.object(pdf_text.subprocess, "run", side_effect=_child_writing({"unavailable": True})):
            with self.assertRaises(PDFTextExtractorUnavailable):
                extract_pdf_bounded(self.pdf)

    def test_a_child_error_keeps_its_reason(self) -> None:
        with mock.patch.object(pdf_text.subprocess, "run", side_effect=_child_writing({"error": "FileDataError"})):
            self.assertEqual(self.reason(), "FileDataError")

    def test_the_child_argv_has_no_shell_and_names_this_module(self) -> None:
        with mock.patch.object(pdf_text.subprocess, "run", side_effect=_child_writing(
                {"text": "", "empty": True, "truncated": False})) as run:
            extract_pdf_bounded(self.pdf, max_chars=7)
        argv = run.call_args.args[0]
        self.assertEqual(argv[1:], ["-m", "backend.pdf_text", "extract", self.pdf, "7"])
        self.assertNotIn("shell", run.call_args.kwargs)


@unittest.skipUnless(HAVE_PYMUPDF, "PyMuPDF is not installed")
class BoundedExtractionRoundTripTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)

    def write(self, name: str, data: bytes) -> str:
        path = os.path.join(self.scratch.name, name)
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    def test_the_child_returns_what_extract_pdf_returns(self) -> None:
        import pymupdf

        doc = pymupdf.open()
        doc.new_page().insert_text((72, 72), "bounded words")
        path = self.write("ok.pdf", doc.tobytes())
        doc.close()
        self.assertEqual(extract_pdf_bounded(path), pdf_text.extract_pdf(path))
        self.assertEqual(extract_pdf_bounded(path, max_chars=3).text, "bou")

    def pdf(self, name: str, pages: list[str]) -> str:
        import pymupdf

        doc = pymupdf.open()
        for text in pages:
            doc.new_page().insert_text((72, 72), text)
        path = self.write(name, doc.tobytes())
        doc.close()
        return path

    def test_the_page_comparison_needs_the_same_count_and_text(self) -> None:
        source = self.pdf("source.pdf", ["one", "two"])
        self.assertTrue(same_pages_bounded(source, self.pdf("same.pdf", ["one", "two"]), 30))
        self.assertFalse(same_pages_bounded(source, self.pdf("fewer.pdf", ["one"]), 30))
        self.assertFalse(same_pages_bounded(source, self.pdf("other.pdf", ["one", "too"]), 30))
        self.assertFalse(same_pages_bounded(source, self.write("bad.pdf", b"%PDF-1.4 junk"), 30))

    def drawn(self, name: str, *, drawing: bool = False, image: bool = False,
              annot: bool = False, link: bool = False, field: bool = False,
              outline: bool = False, attachment: bool = False, shift_objects: int = 0) -> str:
        """One page with the same text; optionally a red drawing, an image, a note or a link."""
        import pymupdf

        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((72, 72), "same words")
        if drawing:
            page.draw_rect(pymupdf.Rect(100, 200, 400, 500), color=(1, 0, 0), fill=(1, 0, 0))
        if image:
            pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 8, 8), False)
            pixmap.clear_with(40)
            page.insert_image(pymupdf.Rect(100, 200, 300, 400), pixmap=pixmap)
        if annot:
            page.add_text_annot((300, 100), "a reader's note")
        for _ in range(shift_objects):
            doc.get_new_xref()  # later objects get other numbers
        if link:
            page.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(72, 60, 200, 80),
                              "uri": "https://example.invalid/"})
        if field:
            # A blank, borderless field: it renders nothing and has no text.
            widget = pymupdf.Widget()
            for attribute, value in (("field_name", "approval"),
                                     ("field_type", getattr(pymupdf, "PDF_WIDGET_TYPE_TEXT")),
                                     ("rect", pymupdf.Rect(300, 600, 500, 620)), ("border_width", 0)):
                setattr(widget, attribute, value)
            page.add_widget(widget)
        if outline:
            doc.set_toc([[1, "Chapter", 1]])
        if attachment:
            doc.embfile_add("data.csv", b"a,b\n1,2\n")
        path = self.write(name, doc.tobytes())
        doc.close()
        return path

    def test_equal_text_with_lost_visual_content_is_not_the_same_page(self) -> None:
        bare = self.drawn("bare.pdf")
        self.assertTrue(same_pages_bounded(bare, self.drawn("bare-again.pdf"), 30))
        for lost in ("drawing", "image", "annot", "link", "field", "outline", "attachment"):
            with self.subTest(lost=lost):
                full = self.drawn(f"{lost}.pdf", **{lost: True})
                self.assertEqual(pdf_text.extract_pdf(full).text.strip(), "same words")
                self.assertFalse(same_pages_bounded(full, bare, 30))

    def test_the_same_link_under_another_object_number_is_the_same_page(self) -> None:
        import pymupdf

        first = self.drawn("link-a.pdf", link=True)
        second = self.drawn("link-b.pdf", link=True, shift_objects=3)
        xrefs = []
        for path in (first, second):
            with pymupdf.open(path) as doc:
                xrefs.append(doc[0].get_links()[0]["xref"])
        self.assertNotEqual(xrefs[0], xrefs[1])
        self.assertTrue(same_pages_bounded(first, second, 30))

    @unittest.skipUnless(shutil.which("qpdf"), "qpdf is not installed")
    def test_a_real_qpdf_linearization_of_links_and_notes_is_the_same_page(self) -> None:
        source = self.drawn("rich.pdf", drawing=True, image=True, annot=True, link=True, field=True,
                            outline=True, attachment=True)
        copy = source + ".lin"
        subprocess.run(["qpdf", "--linearize", source, copy], check=True)  # nosec B603 B607 - fixture
        self.assertTrue(same_pages_bounded(source, copy, 30))

    def test_a_scan_that_lost_its_image_is_not_the_same_page(self) -> None:
        import pymupdf

        def scan(name: str, shade: int) -> str:
            doc = pymupdf.open()
            pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 8, 8), False)
            pixmap.clear_with(shade)
            doc.new_page().insert_image(pymupdf.Rect(0, 0, 595, 842), pixmap=pixmap)
            path = self.write(name, doc.tobytes())
            doc.close()
            return path

        blank = self.write("blank.pdf", self.blank_pdf())
        self.assertTrue(pdf_text.extract_pdf(scan("scan.pdf", 30)).empty)
        self.assertFalse(same_pages_bounded(scan("scan.pdf", 30), blank, 30))

    def blank_pdf(self) -> bytes:
        import pymupdf

        doc = pymupdf.open()
        doc.new_page()
        data = doc.tobytes()
        doc.close()
        return data

    def test_a_page_comparison_past_its_bound_answers_false(self) -> None:
        source = self.pdf("source.pdf", ["one"])
        self.assertFalse(same_pages_bounded(source, source, 0.001))

    def test_an_unreadable_pdf_fails_with_the_child_reason(self) -> None:
        path = self.write("bad.pdf", b"%PDF-1.4 not a pdf")
        with self.assertRaises(PDFTextExtractionError) as caught:
            extract_pdf_bounded(path)
        self.assertNotIn(caught.exception.reason, ("extraction_timeout", "extractor_crashed"))


if __name__ == "__main__":
    unittest.main()
