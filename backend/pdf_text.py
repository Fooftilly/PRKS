"""PDF text extraction shared by every Library backend (PG-C2 #641).

PyMuPDF extraction with its size cap and privacy-safe failure labels, the
per-Work sync result and the empty rebuild summary. No store. Moved from
``backend/text_index.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from backend.log_safety import safe_error_type

_MAX_EXTRACTED_CHARS = 2_000_000


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
