"""PDF text extraction shared by every Library backend (PG-C2 #641).

PyMuPDF extraction with its size cap and privacy-safe failure labels, the
per-Work sync result and the empty rebuild summary. No store. Moved from
``backend/text_index.py``.

:func:`extract_pdf_bounded` runs :func:`extract_pdf` in a child process
that is killed after ``EXTRACT_TIMEOUT_SECONDS`` (#1321): a page loop can
check a deadline only between pages, and one slow page would still hold
the job worker. A child that times out or dies without a result is a
per-PDF failure (``extraction_timeout`` / ``extractor_crashed``), never an
empty document.
"""

from __future__ import annotations

import json
import os
import subprocess  # nosec B404 - runs this module's own extractor, no shell
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from backend.log_safety import safe_error_type

_MAX_EXTRACTED_CHARS = 2_000_000

# Wall-time bound for one PDF. PyMuPDF reads ordinary books in seconds; only
# a pathological file gets near this.
EXTRACT_TIMEOUT_SECONDS = 300
_PACKAGE_ROOT = str(Path(__file__).resolve().parents[1])


class PDFTextExtractorUnavailable(Exception):
    """PyMuPDF cannot be imported; extraction is globally unavailable."""


class PDFTextExtractionError(Exception):
    """Per-PDF extraction failed. ``reason`` is a privacy-safe error_type label."""

    def __init__(self, reason: str = "extraction_failed"):
        self.reason = reason or "extraction_failed"


@dataclass(frozen=True)
class PDFTextExtraction:
    text: str
    empty: bool
    truncated: bool


@dataclass(frozen=True)
class TextIndexSyncResult:
    action: str
    status: str | None = None
    truncated: bool = False
    extracted: bool = False


def extractor_available() -> bool:
    try:
        import pymupdf as fitz  # noqa: F401
    except Exception:
        return False
    return True


def extract_pdf(
    pdf_path: str, max_chars: int = _MAX_EXTRACTED_CHARS
) -> PDFTextExtraction:
    try:
        import pymupdf as fitz
    except Exception:
        raise PDFTextExtractorUnavailable() from None
    parts: List[str] = []
    total_chars = 0
    try:
        with fitz.open(pdf_path) as doc:
            for page in doc:
                raw = page.get_text("text") or ""
                # PyMuPDF types every get_text() mode together; "text" is a str.
                page_text = raw if isinstance(raw, str) else ""
                parts.append(page_text)
                # Running total of page text only; the "\n" separators added
                # by the join below are deliberately not counted here.
                total_chars += len(page_text)
                if total_chars >= max_chars:
                    break
    except PDFTextExtractorUnavailable:
        raise
    except Exception as exc:
        raise PDFTextExtractionError(safe_error_type(exc)) from None
    txt = "\n".join(parts)
    truncated = len(txt) > max_chars
    if truncated:
        txt = txt[:max_chars]
    if not txt.strip():
        return PDFTextExtraction(text="", empty=True, truncated=False)
    return PDFTextExtraction(text=txt, empty=False, truncated=truncated)


def extract_pdf_bounded(
    pdf_path: str,
    max_chars: int = _MAX_EXTRACTED_CHARS,
    timeout: float = EXTRACT_TIMEOUT_SECONDS,
) -> PDFTextExtraction:
    """:func:`extract_pdf` in a child process killed after ``timeout`` seconds.

    Raises :class:`PDFTextExtractorUnavailable` when the child cannot import
    PyMuPDF and :class:`PDFTextExtractionError` for an unreadable PDF, a
    timeout (``extraction_timeout``) or a child that ends without a result
    (``extractor_crashed``). The result goes through a file, not stdout,
    because MuPDF writes its own messages to stdout.
    """
    with tempfile.TemporaryDirectory(prefix="prks-pdf-text-") as scratch:
        out = os.path.join(scratch, "result.json")
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(p for p in (_PACKAGE_ROOT, env.get("PYTHONPATH")) if p)
        try:
            subprocess.run(  # nosec B603 - fixed argv, no shell
                [sys.executable, "-m", "backend.pdf_text", pdf_path, str(int(max_chars)), out],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=_PACKAGE_ROOT,
                env=env,
                check=False,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            # subprocess.run has already killed and reaped the child.
            raise PDFTextExtractionError("extraction_timeout") from None
        try:
            with open(out, encoding="utf-8") as handle:
                result = json.load(handle)
        except (OSError, ValueError):
            raise PDFTextExtractionError("extractor_crashed") from None
    if result.get("unavailable"):
        raise PDFTextExtractorUnavailable()
    if "error" in result:
        raise PDFTextExtractionError(str(result["error"]))
    return PDFTextExtraction(
        text=str(result["text"]), empty=bool(result["empty"]), truncated=bool(result["truncated"])
    )


def _child_main(argv: List[str]) -> int:
    """``python -m backend.pdf_text PDF MAX_CHARS OUT``: one extraction, written to OUT as JSON."""
    pdf_path, max_chars, out = argv[0], int(argv[1]), argv[2]
    result: Dict[str, Any]
    try:
        found = extract_pdf(pdf_path, max_chars)
        result = {"text": found.text, "empty": found.empty, "truncated": found.truncated}
    except PDFTextExtractorUnavailable:
        result = {"unavailable": True}
    except PDFTextExtractionError as exc:
        result = {"error": exc.reason}
    # Scratch read once by the parent after this process exits; a write cut
    # short is not valid JSON, which the parent reads as ``extractor_crashed``.
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(result, handle)
    return 0


def _empty_summary(
    *, extractor_unavailable: bool = False, fts_rebuilt: bool = False
) -> Dict[str, Any]:
    return {
        "processed": 0,
        "indexed": 0,
        "updated": 0,
        "unchanged": 0,
        "empty": 0,
        "truncated": 0,
        "missing": 0,
        "failed": 0,
        "removed_orphans": 0,
        "fts_rebuilt": fts_rebuilt,
        "extractor_unavailable": extractor_unavailable,
        "skipped": 0,
    }


if __name__ == "__main__":
    sys.exit(_child_main(sys.argv[1:]))
