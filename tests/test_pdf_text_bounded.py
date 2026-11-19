"""Time-bounded PDF text extraction in a child process (#1321).

``extract_pdf_bounded`` runs ``extract_pdf`` in ``python -m backend.pdf_text``
and kills it after a timeout. A timeout or a child that leaves no result is a
per-PDF ``PDFTextExtractionError``, never an empty document; a child without
PyMuPDF is ``PDFTextExtractorUnavailable`` so the job is retried.
"""

import json
import os
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
        self.assertEqual(argv[1:], ["-m", "backend.pdf_text", self.pdf, "7"])
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

    def test_an_unreadable_pdf_fails_with_the_child_reason(self) -> None:
        path = self.write("bad.pdf", b"%PDF-1.4 not a pdf")
        with self.assertRaises(PDFTextExtractionError) as caught:
            extract_pdf_bounded(path)
        self.assertNotIn(caught.exception.reason, ("extraction_timeout", "extractor_crashed"))


if __name__ == "__main__":
    unittest.main()
