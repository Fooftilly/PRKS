import logging
import os
import shutil
import subprocess
import tempfile

from backend.fs_durability import fsync_directory, fsync_file_path
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


def maybe_linearize_pdf_in_place(pdf_path: str, *, context: str = "") -> tuple[bool, str]:
    """Try qpdf --linearize in-place. Returns (changed, reason).

    Linearization is an optimization that rewrites a canonical managed PDF, so it
    owns the durability boundary its own ``os.replace()`` creates and must not
    hand back a PDF less durable than the one it replaced. The rename-based
    convention in ``backend.fs_durability`` applies: fsync qpdf's finished output
    before the replace, fsync the containing directory after it. Every caller
    gets that; none needs to compensate afterwards.

    Reasons, with (changed) in front:

    - (False) ``disabled`` / ``missing-qpdf`` / ``missing-file`` -- nothing ran.
    - (False) ``qpdf-failed`` / ``error`` -- the canonical PDF is untouched.
    - (False) ``sync-failed`` -- qpdf's output could not be made durable, so the
      replace never happened and the canonical PDF is untouched. An optimization
      is not worth trading durability for.
    - (True) ``ok-unsynced-dir`` -- the replace happened and the linearized bytes
      are durable, but the directory entry could not be confirmed durable. The
      rename cannot be unwound, so this reports the weaker guarantee rather than
      claiming ``ok``.
    - (True) ``ok`` -- replaced, and durable to the extent the platform allows.
    """
    with perf_span("pdf_linearize"):
        return _maybe_linearize_pdf_in_place_inner(pdf_path, context=context)


def _maybe_linearize_pdf_in_place_inner(pdf_path: str, *, context: str = "") -> tuple[bool, str]:
    """Try qpdf --linearize in-place. Returns (changed, reason)."""
    ctx = safe_log_label(context, fallback="unknown")
    if not _linearize_enabled():
        return False, "disabled"
    qpdf = shutil.which("qpdf")
    if not qpdf:
        global _MISSING_QPDF_WARNED
        if not _MISSING_QPDF_WARNED:
            LOGGER.warning("pdf_linearize_skip_missing_qpdf context=%s", ctx)
            _MISSING_QPDF_WARNED = True
        return False, "missing-qpdf"
    if not pdf_path or not os.path.exists(pdf_path):
        return False, "missing-file"

    src_dir = os.path.dirname(pdf_path) or "."
    fd, tmp_path = tempfile.mkstemp(prefix=".linearized_", suffix=".pdf", dir=src_dir)
    os.close(fd)
    try:
        proc = subprocess.run(
            [qpdf, "--linearize", pdf_path, tmp_path],
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
            return False, "qpdf-failed"
        # qpdf wrote the temporary in another process, so nothing here has
        # flushed it. Sync it before it becomes the canonical file: a crash
        # after the rename must not find the managed name pointing at bytes
        # that were never on stable storage.
        try:
            fsync_file_path(tmp_path)
        except OSError as e:
            LOGGER.warning(
                "pdf_linearize_sync_failed context=%s error_type=%s",
                ctx,
                safe_error_type(e),
            )
            return False, "sync-failed"
        os.replace(tmp_path, pdf_path)
        # ``src_dir`` is the directory the temporary was created in and the one
        # the rename just changed -- not a fresh derivation from ``pdf_path``.
        if not fsync_directory(src_dir):
            LOGGER.warning("pdf_linearize_dir_sync_failed context=%s", ctx)
            return True, "ok-unsynced-dir"
        return True, "ok"
    except Exception as e:
        LOGGER.warning(
            "pdf_linearize_error context=%s error_type=%s",
            ctx,
            safe_error_type(e),
        )
        return False, "error"
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
