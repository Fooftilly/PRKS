import logging
import os
import shutil
import subprocess
import tempfile


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
    """Try qpdf --linearize in-place. Returns (changed, reason)."""
    if not _linearize_enabled():
        return False, "disabled"
    qpdf = shutil.which("qpdf")
    if not qpdf:
        global _MISSING_QPDF_WARNED
        if not _MISSING_QPDF_WARNED:
            LOGGER.warning("pdf_linearize_skip_missing_qpdf context=%s", context or "unknown")
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
                "pdf_linearize_failed context=%s code=%s stderr=%s",
                context or "unknown",
                proc.returncode,
                (proc.stderr or "").strip(),
            )
            return False, "qpdf-failed"
        os.replace(tmp_path, pdf_path)
        LOGGER.info("pdf_linearize_ok context=%s path=%s", context or "unknown", pdf_path)
        return True, "ok"
    except Exception as e:
        LOGGER.warning("pdf_linearize_error context=%s error=%s", context or "unknown", e)
        return False, "error"
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
