import logging
import os
import shutil
import subprocess

from backend.log_safety import safe_error_type, safe_log_label
from backend.performance import span as perf_span


LOGGER = logging.getLogger("prks.pdf")
_MISSING_QPDF_WARNED = False


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


def _run_qpdf(qpdf: str, src_path: str, dst_path: str, ctx: str) -> bool:
    """Run ``qpdf --linearize src dst``; False (logged) when qpdf fails."""
    proc = subprocess.run(
        [qpdf, "--linearize", src_path, dst_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        LOGGER.warning(
            "pdf_linearize_failed context=%s exit_code=%s",
            ctx,
            proc.returncode,
        )
        return False
    return True


def linearize_pdf_to(src_path: str, dst_path: str, *, context: str = "") -> tuple[bool, str]:
    """Write a linearized copy of ``src_path`` to ``dst_path``. Returns (written, reason).

    For immutable objects (postgresql-architecture §11.2): the source is
    never touched, and ``dst_path`` is scratch that the caller publishes
    under a new key through its ``StorageBackend``, which makes it durable,
    so nothing is synced here. Reasons, with (written) in front: (False)
    ``disabled`` / ``missing-qpdf`` / ``missing-file`` / ``qpdf-failed`` /
    ``error``; (True) ``ok``.
    """
    with perf_span("pdf_linearize"):
        ctx = safe_log_label(context, fallback="unknown")
        qpdf, reason = _qpdf(ctx)
        if qpdf is None:
            return False, reason
        if not src_path or not os.path.exists(src_path):
            return False, "missing-file"
        try:
            if not _run_qpdf(qpdf, src_path, dst_path, ctx):
                return False, "qpdf-failed"
        except Exception as e:
            LOGGER.warning(
                "pdf_linearize_error context=%s error_type=%s",
                ctx,
                safe_error_type(e),
            )
            return False, "error"
        return True, "ok"
