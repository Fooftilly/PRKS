"""Replace managed Work PDF bytes and optionally mark materialization.

Coordinates filesystem write, linearization, text-index sync, Mentioned-role
extraction, and materialization claim/mark. HTTP adapters parse the request and
map the returned status/body; they do not own this workflow.
"""

from __future__ import annotations

import logging
import os
import tempfile
import threading
from typing import Any, Optional

from backend.db_manager import managed_pdf_filename, safe_pdf_path_under_dir
from backend.log_safety import safe_error_type, safe_log_id, safe_log_label
from backend.pdf_linearize import maybe_linearize_pdf_in_place
from backend.pdf_materialization import STALE_CODE

LOGGER = logging.getLogger("prks")

_PDF_PATH_LOCKS_GUARD = threading.Lock()
_PDF_PATH_LOCKS: dict[str, threading.Lock] = {}


def pdf_path_lock_for(pdf_path: str) -> threading.Lock:
    """Serialize claim/replace/mark for a canonical on-disk managed PDF path."""
    key = os.path.normpath(pdf_path)
    with _PDF_PATH_LOCKS_GUARD:
        lock = _PDF_PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _PDF_PATH_LOCKS[key] = lock
        return lock


def fsync_parent_dir(path: str) -> None:
    """Best-effort directory fsync after rename (POSIX/Linux only)."""
    if os.name != "posix":
        return
    parent = os.path.dirname(path) or "."
    flags = getattr(os, "O_RDONLY", None)
    if flags is None:
        return
    try:
        dir_fd = os.open(parent, flags)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        try:
            os.close(dir_fd)
        except OSError:
            pass


def atomic_replace_file_bytes(path: str, body: bytes) -> None:
    """Write ``body`` to ``path`` without truncating the live file first."""
    parent = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(prefix=".prks-write-", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "wb") as fp:
            fp.write(body)
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(tmp, path)
        fsync_parent_dir(path)
    except Exception:
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise


def _stale_body(db, work_id: str) -> dict[str, Any]:
    mat = db.get_work_pdf_materialization(work_id) or {}
    return {
        "error": "PDF materialization is stale",
        "code": STALE_CODE,
        "canonical_annotation_set_revision": mat.get("canonical_annotation_set_revision"),
        "materialized_pdf_annotation_revision": mat.get(
            "materialized_pdf_annotation_revision"
        ),
    }


def _sync_mentioned_roles(db, work_id: str, pdf_bytes: bytes) -> None:
    from backend.pdf_byte_mentions import iter_pdf_mentioned_labels

    for clean in iter_pdf_mentioned_labels(pdf_bytes):
        try:
            db_res = db.execute_query(
                "SELECT id FROM persons WHERE (first_name || ' ' || last_name) = ? OR last_name = ?",
                (clean, clean),
            )
            if db_res:
                p_id = db_res[0]["id"]
                exist = db.execute_query(
                    "SELECT 1 FROM roles WHERE person_id=? AND work_id=? AND role_type='Mentioned'",
                    (p_id, work_id),
                )
                if not exist:
                    db.add_role(p_id, work_id, "Mentioned")
        except Exception:
            continue


def replace_managed_work_pdf(
    db,
    *,
    work_id: str,
    pdf_bytes: bytes,
    pdfs_dir: str,
    text_index,
    claimed_set_rev: Any = None,
) -> dict[str, Any]:
    """Replace managed PDF bytes for ``work_id``; optionally mark materialization.

    Caller must already hold the per-Work materialization lock and have verified
    the Work exists. ``claimed_set_rev is not None`` enables durable claim+mark.

    Returns ``{status, body, wrote_pdf}`` for the HTTP adapter.
    """
    durable = claimed_set_rev is not None
    res_path = db.execute_query("SELECT file_path FROM works WHERE id=?", (work_id,))
    if not res_path:
        return {
            "status": 404,
            "body": {"error": "Work not found"},
            "wrote_pdf": False,
        }
    stored_fp = (res_path[0].get("file_path") or "").strip()
    filename = managed_pdf_filename(stored_fp)
    if not filename:
        return {
            "status": 404,
            "body": {"error": "Work has no managed PDF"},
            "wrote_pdf": False,
        }
    pdf_path = safe_pdf_path_under_dir(pdfs_dir, filename)
    if not pdf_path:
        return {
            "status": 400,
            "body": {"error": "Invalid or unsafe PDF storage path"},
            "wrote_pdf": False,
        }

    with pdf_path_lock_for(pdf_path):
        if durable:
            try:
                db.accept_work_pdf_materialization_claim(work_id, claimed_set_rev)
            except LookupError:
                return {
                    "status": 404,
                    "body": {"error": "Work not found"},
                    "wrote_pdf": False,
                }
            except ValueError as e:
                if str(e) == STALE_CODE:
                    return {
                        "status": 409,
                        "body": _stale_body(db, work_id),
                        "wrote_pdf": False,
                    }
                return {
                    "status": 400,
                    "body": {"error": "Invalid materialization revision"},
                    "wrote_pdf": False,
                }

        atomic_replace_file_bytes(pdf_path, pdf_bytes)
        changed, reason = maybe_linearize_pdf_in_place(
            pdf_path, context="work-pdf-overwrite"
        )
        fsync_parent_dir(pdf_path)
        LOGGER.info(
            "pdf_linearize_result context=work-pdf-overwrite changed=%s reason=%s",
            "true" if changed else "false",
            safe_log_label(reason),
        )
        try:
            text_index.sync_work(work_id, stored_fp)
        except Exception as e:
            LOGGER.warning(
                "work_pdf_replace_text_index_failed work_id=%s error_type=%s",
                safe_log_id(work_id),
                safe_error_type(e),
            )

        _sync_mentioned_roles(db, work_id, pdf_bytes)

        materialized_rev: Optional[int] = None
        try:
            if durable:
                materialized_rev = db.mark_work_pdf_materialized_if_claim_current(
                    work_id, claimed_set_rev
                )
        except LookupError:
            return {
                "status": 404,
                "body": {"error": "Work not found"},
                "wrote_pdf": True,
            }
        except ValueError as e:
            if str(e) == STALE_CODE:
                return {
                    "status": 409,
                    "body": _stale_body(db, work_id),
                    "wrote_pdf": True,
                }
            LOGGER.warning(
                "pdf_materialization_mark_failed work_id=%s error_type=%s",
                safe_log_id(work_id),
                safe_error_type(e),
            )
        except Exception as e:
            LOGGER.warning(
                "pdf_materialization_mark_failed work_id=%s error_type=%s",
                safe_log_id(work_id),
                safe_error_type(e),
            )

        body: dict[str, Any] = {"status": "success"}
        if materialized_rev is not None:
            body["materialized_pdf_annotation_revision"] = materialized_rev
            mat = db.get_work_pdf_materialization(work_id)
            if mat:
                body["canonical_annotation_set_revision"] = mat[
                    "canonical_annotation_set_revision"
                ]
                body["stale"] = mat["stale"]
        return {"status": 200, "body": body, "wrote_pdf": True}
