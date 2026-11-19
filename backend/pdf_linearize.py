import logging
import os
import shutil
import subprocess
import time

from backend.log_safety import safe_error_type, safe_log_label
from backend.performance import span as perf_span


LOGGER = logging.getLogger("prks.pdf")
_MISSING_QPDF_WARNED = False

# Linearizing is an optimization: a qpdf that runs longer than this is killed
# and the upload keeps the source as its working file. qpdf linearizes large
# ordinary PDFs in seconds; only a pathological input gets near this bound.
QPDF_TIMEOUT_SECONDS = 120
# qpdf exits 3 when it finished with warnings, usually after repairing a
# damaged file (#1322). Its copy is used only when PyMuPDF reads the same
# page count and page text from it as from the source; the comparison stops
# after this many seconds and then keeps the source.
QPDF_WARNINGS_EXIT = 3
REPAIRED_CHECK_SECONDS = 30.0


def _env_truthy(name: str) -> bool | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    v = str(raw).strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    return None


def _linearize_enabled() -> bool:
    explicit = _env_truthy("PRKS_PDF_LINEARIZE")
    if explicit is not None:
        return explicit
    return shutil.which("qpdf") is not None


def is_pdf_linearized(pdf_path: str) -> bool:
    """Best-effort linearization check using PDF header marker."""
    if not pdf_path or not os.path.exists(pdf_path):
        return False
    try:
        with open(pdf_path, "rb") as f:
            head = f.read(4096)
    except OSError:
        return False
    return b"/Linearized" in head


def _qpdf(ctx: str) -> tuple[str | None, str]:
    """The qpdf binary to run, or ``(None, reason)`` when linearization is off or impossible."""
    if not _linearize_enabled():
        return None, "disabled"
    qpdf = shutil.which("qpdf")
    if not qpdf:
        global _MISSING_QPDF_WARNED
        if not _MISSING_QPDF_WARNED:
            LOGGER.warning("pdf_linearize_skip_missing_qpdf context=%s", ctx)
            _MISSING_QPDF_WARNED = True
        return None, "missing-qpdf"
    return qpdf, ""


def _run_qpdf(qpdf: str, src_path: str, dst_path: str, ctx: str) -> str:
    """Run ``qpdf --linearize src dst``: ``ok``, ``qpdf-warnings``, ``qpdf-failed`` or ``qpdf-timeout`` (logged)."""
    try:
        proc = subprocess.run(
            [qpdf, "--linearize", src_path, dst_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=QPDF_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        # subprocess.run has already killed and reaped qpdf.
        LOGGER.warning(
            "pdf_linearize_timeout context=%s timeout_seconds=%s",
            ctx,
            QPDF_TIMEOUT_SECONDS,
        )
        return "qpdf-timeout"
    if proc.returncode == QPDF_WARNINGS_EXIT:
        LOGGER.info("pdf_linearize_warnings context=%s", ctx)
        return "qpdf-warnings"
    if proc.returncode != 0:
        LOGGER.warning(
            "pdf_linearize_failed context=%s exit_code=%s",
            ctx,
            proc.returncode,
        )
        return "qpdf-failed"
    return "ok"


def _page_texts(path: str, deadline: float) -> list[str] | None:
    """Every page's text as PyMuPDF reads it, or None when it cannot, or not before ``deadline``."""
    try:
        import pymupdf
    except ImportError:
        return None
    texts: list[str] = []
    with pymupdf.open(path) as doc:
        for page in doc:
            if time.monotonic() > deadline:
                return None
            raw = page.get_text("text")
            texts.append(raw if isinstance(raw, str) else "")
    return texts


def _repaired_copy_matches(src_path: str, dst_path: str, ctx: str) -> bool:
    """Whether qpdf's repaired copy is linearized and keeps the source's pages and text.

    qpdf repairs can drop objects, so a copy that PyMuPDF reads differently
    (or cannot read, or reads too slowly) is refused and the source kept.
    """
    if not is_pdf_linearized(dst_path):
        return False
    deadline = time.monotonic() + REPAIRED_CHECK_SECONDS
    try:
        source = _page_texts(src_path, deadline)
        copy = _page_texts(dst_path, deadline) if source is not None else None
    except Exception as e:
        LOGGER.info("pdf_linearize_repair_check_error context=%s error_type=%s", ctx, safe_error_type(e))
        return False
    return source is not None and copy == source


def linearize_pdf_to(src_path: str, dst_path: str, *, context: str = "") -> tuple[bool, str]:
    """Write a linearized copy of ``src_path`` to ``dst_path``. Returns (written, reason).

    For immutable objects (postgresql-architecture §11.2): the source is
    never touched, and ``dst_path`` is scratch that the caller publishes
    under a new key through its ``StorageBackend``, which makes it durable,
    so nothing is synced here. Reasons, with (written) in front: (False)
    ``disabled`` / ``missing-qpdf`` / ``missing-file`` / ``qpdf-failed`` /
    ``qpdf-warnings`` / ``qpdf-timeout`` / ``error``; (True) ``ok`` /
    ``ok-repaired``. A timed-out qpdf is killed after
    ``QPDF_TIMEOUT_SECONDS``; its partial output is scratch the caller
    discards. A qpdf that exits 3 (warnings) gives ``ok-repaired`` when its
    copy keeps the source's page count and page text, else ``qpdf-warnings``.
    """
    with perf_span("pdf_linearize"):
        ctx = safe_log_label(context, fallback="unknown")
        qpdf, reason = _qpdf(ctx)
        if qpdf is None:
            return False, reason
        if not src_path or not os.path.exists(src_path):
            return False, "missing-file"
        try:
            outcome = _run_qpdf(qpdf, src_path, dst_path, ctx)
            if outcome == "qpdf-warnings" and os.path.exists(dst_path):
                if _repaired_copy_matches(src_path, dst_path, ctx):
                    return True, "ok-repaired"
                LOGGER.info("pdf_linearize_repair_refused context=%s", ctx)
            if outcome != "ok":
                return False, outcome
        except Exception as e:
            LOGGER.warning(
                "pdf_linearize_error context=%s error_type=%s",
                ctx,
                safe_error_type(e),
            )
            return False, "error"
        return True, "ok"
