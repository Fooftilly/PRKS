"""Replace managed Work PDF bytes and optionally mark materialization.

Coordinates filesystem write, linearization, text-index sync, Mentioned-role
extraction, and materialization claim/mark. HTTP adapters parse the request and
map the returned status/body; they do not own this workflow.

Shared ``/api/pdfs/<file>`` references are copy-on-write: before overwriting
bytes that another Work still points at, this Work is retargeted to an exclusive
managed filename so siblings keep their prior bytes and materialization marks.

All on-disk paths are resolved through ``safe_pdf_path_under_dir`` immediately
before use so DB/user-derived basenames cannot escape the managed pdfs dir.
"""

from __future__ import annotations

import logging
import os
import tempfile
import threading
import time
import uuid
from typing import Any, Optional

from backend.db_manager import (
    managed_pdf_filename,
    mint_managed_pdf_filename,
    prks_thumb_cache_safe_wid,
    referenced_managed_pdf_filename,
    safe_pdf_path_under_dir,
)
from backend.log_safety import safe_error_type, safe_log_id, safe_log_label
from backend.pdf_linearize import maybe_linearize_pdf_in_place
from backend.pdf_materialization import STALE_CODE

LOGGER = logging.getLogger("prks")

_PDF_PATH_LOCKS_GUARD = threading.Lock()
_PDF_PATH_LOCKS: dict[str, threading.Lock] = {}


def managed_pdf_path_lock(pdfs_dir: str, filename: str) -> Optional[threading.Lock]:
    """Lock for a managed PDF basename under ``pdfs_dir``, or None if unsafe.

    Keyed by the ``safe_pdf_path_under_dir`` return value directly (no re-join of
    a DB/user basename onto the root).
    """
    path = safe_pdf_path_under_dir(pdfs_dir, filename)
    if not path:
        return None
    key = path
    with _PDF_PATH_LOCKS_GUARD:
        lock = _PDF_PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _PDF_PATH_LOCKS[key] = lock
        return lock


def fsync_managed_pdf_parent(pdfs_dir: str, filename: str) -> None:
    """Best-effort directory fsync after rename (POSIX/Linux only).

    Confirms ``filename`` is a managed path under ``pdfs_dir``, then fsyncs the
    trusted storage root (``realpath(pdfs_dir)``) — never ``dirname`` of a
    DB-derived path.
    """
    if not safe_pdf_path_under_dir(pdfs_dir, filename):
        return
    if os.name != "posix":
        return
    parent = os.path.realpath(pdfs_dir)
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


def atomic_replace_managed_pdf_bytes(
    pdfs_dir: str, filename: str, body: bytes
) -> str:
    """Write ``body`` to managed ``filename`` under ``pdfs_dir`` without truncating first.

    Runtime containment uses ``safe_pdf_path_under_dir``. The destination local
    passed to ``os.replace`` is then rebuilt with the CodeQL-documented
    ``normpath(join(base, basename))`` + ``startswith(base)`` pattern so the
    sink does not carry a helper return CodeQL still treats as tainted. Temps
    live under ``realpath(pdfs_dir)`` only.
    Returns the absolute managed path written.
    """
    if not safe_pdf_path_under_dir(pdfs_dir, filename):
        raise ValueError("Invalid or unsafe PDF storage path")
    base_path = os.path.realpath(pdfs_dir)
    # CodeQL py/path-injection documented sanitizer (query help user_picture3):
    # build with join+normpath, then startswith the root before any FS sink.
    name = os.path.basename(str(filename))
    fullpath = os.path.normpath(os.path.join(base_path, name))
    if not fullpath.startswith(base_path):
        raise ValueError("Invalid or unsafe PDF storage path")
    if fullpath == base_path or not fullpath.startswith(base_path + os.sep):
        raise ValueError("Invalid or unsafe PDF storage path")

    fd, tmp = tempfile.mkstemp(prefix=".prks-write-", suffix=".tmp", dir=base_path)
    try:
        with os.fdopen(fd, "wb") as fp:
            fp.write(body)
            fp.flush()
            os.fsync(fp.fileno())
    except Exception:
        if tmp.startswith(base_path + os.sep):
            try:
                os.remove(tmp)
            except OSError:
                pass
        raise
    try:
        if not tmp.startswith(base_path):
            raise ValueError("Invalid or unsafe PDF storage path")
        if not fullpath.startswith(base_path):
            raise ValueError("Invalid or unsafe PDF storage path")
        os.replace(tmp, fullpath)
        fsync_managed_pdf_parent(pdfs_dir, filename)
    except Exception:
        if tmp.startswith(base_path + os.sep):
            try:
                os.remove(tmp)
            except OSError:
                pass
        raise
    return fullpath


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


def other_works_share_managed_filename(
    db, filename: str, *, exclude_work_id: str
) -> bool:
    """True when another Work's ``file_path`` resolves to the same managed basename."""
    name = str(filename or "")
    if not name:
        return False
    try:
        rows = db.execute_query(
            "SELECT id, file_path FROM works WHERE file_path IS NOT NULL"
        )
    except Exception:
        # Fail closed: treat as shared so we COW rather than overwrite unknowns.
        return True
    for row in rows or ():
        wid = row["id"] if isinstance(row, dict) else row[0]
        if str(wid) == str(exclude_work_id):
            continue
        fp = row["file_path"] if isinstance(row, dict) else row[1]
        if referenced_managed_pdf_filename(fp) == name:
            return True
    return False


def allocate_exclusive_managed_filename(work_id: str, shared_filename: str) -> str:
    """Mint a Work-specific managed PDF basename under ``pdfs/``."""
    # shared_filename must already be a managed basename (from managed_pdf_filename).
    base = managed_pdf_filename(f"/api/pdfs/{os.path.basename(str(shared_filename or ''))}")
    if not base:
        base = "work.pdf"
    safe_base = "".join(c for c in base if c.isalnum() or c in ".-_") or "work.pdf"
    if not safe_base.lower().endswith(".pdf"):
        safe_base = f"{safe_base}.pdf"
    safe_wid = prks_thumb_cache_safe_wid(work_id)
    return f"{int(time.time())}_{safe_wid}_{uuid.uuid4().hex[:8]}_{safe_base}"


class ManagedPdfStoreError(Exception):
    """A new managed PDF could not be stored. Carries the HTTP status to map."""

    def __init__(self, reason: str, message: str, *, http_status: int = 500):
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.http_status = http_status


def store_new_managed_pdf_bytes(pdfs_dir: str, original_name: str, body: bytes) -> str:
    """Store ``body`` as a brand-new managed PDF; return its basename.

    Owns the whole filesystem side of an upload so the HTTP adapter does not:
    mints the name, contains it, creates it exclusively, and linearizes.

    Exclusive create is the point. The create path used to name files
    ``<unix-seconds>_<sanitized>``, so two uploads of one filename inside a
    second resolved to the same path and the second write replaced the first
    Work's bytes while both rows still referenced it. A managed PDF is never
    written over here.

    Containment follows this module's existing pattern: ``safe_pdf_path_under_dir``
    at runtime, then the sink path rebuilt with
    ``normpath(join(base, basename))`` + ``startswith(base)`` so the sink does
    not carry a helper return CodeQL still treats as tainted.
    """
    os.makedirs(pdfs_dir, exist_ok=True)
    created = False
    filename = mint_managed_pdf_filename(original_name)
    if not safe_pdf_path_under_dir(pdfs_dir, filename):
        raise ManagedPdfStoreError("invalid_file_name", "Invalid file_name", http_status=400)
    base_path = os.path.realpath(pdfs_dir)
    # CodeQL py/path-injection documented sanitizer: build with join+normpath,
    # then startswith the root before any FS sink.
    name = os.path.basename(str(filename))
    fullpath = os.path.normpath(os.path.join(base_path, name))
    if fullpath == base_path or not fullpath.startswith(base_path + os.sep):
        raise ManagedPdfStoreError("invalid_file_name", "Invalid file_name", http_status=400)

    try:
        with open(fullpath, "xb") as fp:
            created = True
            fp.write(body)
            fp.flush()
            os.fsync(fp.fileno())
    except FileExistsError as exc:
        # The name was already taken, so the file on disk is not ours to remove.
        raise ManagedPdfStoreError(
            "name_taken", "Could not allocate a managed PDF path", http_status=409
        ) from exc
    except OSError as exc:
        # Create can succeed and write or fsync still fail — a full disk is the
        # likeliest cause and the likeliest to be retried. No Work row will
        # reference this path, so leaving the partial file behind would
        # accumulate orphans exactly when space is short.
        if created:
            unlink_managed_pdf_best_effort(pdfs_dir, name)
        LOGGER.error("pdf_upload_write_failed error_type=%s", safe_error_type(exc))
        raise ManagedPdfStoreError(
            "write_failed", "Could not store the uploaded PDF"
        ) from exc
    fsync_managed_pdf_parent(pdfs_dir, name)

    changed, reason = maybe_linearize_pdf_in_place(fullpath, context="work-create-upload")
    LOGGER.info(
        "pdf_linearize_result context=work-create-upload changed=%s reason=%s",
        "true" if changed else "false",
        safe_log_label(reason),
    )
    return name


def discard_unowned_managed_pdf(pdfs_dir: str, stored_name: Optional[str]) -> None:
    """Roll back a `store_new_managed_pdf_bytes()` that never gained an owner.

    Work creation stores the bytes before the row exists, so a create rejected
    afterwards — a contradictory source identity, say — leaves a managed PDF
    nothing references, and a client retrying an invalid request accumulates
    them.

    Only ever pass a name this request just minted and that no Work row was
    given. A name taken from `works.file_path` is a sibling's bytes, and
    removing that is the data-loss this module exists to prevent; `None` (the
    request referenced an existing `file_path` rather than uploading) is a
    no-op for the same reason.
    """
    if not stored_name:
        return
    unlink_managed_pdf_best_effort(pdfs_dir, stored_name)


def unlink_managed_pdf_best_effort(pdfs_dir: str, filename: str) -> bool:
    """Best-effort delete of a managed PDF basename under ``pdfs_dir``.

    Used to roll back a COW exclusive write when ``works.file_path`` was never
    retargeted — never unlink the shared path a sibling still references.

    Runtime containment uses ``safe_pdf_path_under_dir``. The path passed to
    ``os.remove`` is then rebuilt with the CodeQL-documented
    ``normpath(join(base, basename))`` + ``startswith(base)`` pattern so the
    sink does not carry a helper return CodeQL still treats as tainted. Do not
    ``dirname`` a tainted path into the sink.
    """
    if not safe_pdf_path_under_dir(pdfs_dir, filename):
        return False
    base_path = os.path.realpath(pdfs_dir)
    # CodeQL py/path-injection documented sanitizer (query help user_picture3):
    # build with join+normpath, then startswith the root before any FS sink.
    name = os.path.basename(str(filename))
    fullpath = os.path.normpath(os.path.join(base_path, name))
    if not fullpath.startswith(base_path):
        return False
    if fullpath == base_path or not fullpath.startswith(base_path + os.sep):
        return False
    try:
        os.remove(fullpath)
        return True
    except OSError as e:
        LOGGER.warning(
            "pdf_cow_orphan_unlink_failed error_type=%s",
            safe_error_type(e),
        )
        return False


def retarget_work_managed_file_path(db, work_id: str, file_path: str) -> int:
    """UPDATE ``works.file_path`` for ``work_id``; return affected row count.

    A concurrent Work delete can leave this UPDATE with zero rows even though
    ``execute_query`` returns normally. Callers must require exactly one row
    before treating a COW exclusive write as referenced.
    """
    with db.connection() as conn:
        cur = conn.execute(
            """
            UPDATE works
            SET file_path = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (file_path, work_id),
        )
        n = int(cur.rowcount or 0)
        conn.commit()
        return n


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

    When another Work still references the same managed basename, bytes are
    written to a new exclusive file and this Work's ``file_path`` is retargeted
    (copy-on-write) so sibling materialization marks stay honest.

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

    shared_lock = managed_pdf_path_lock(pdfs_dir, filename)
    if shared_lock is None:
        return {
            "status": 400,
            "body": {"error": "Invalid or unsafe PDF storage path"},
            "wrote_pdf": False,
        }

    with shared_lock:
        # Re-read under the shared-path lock: a concurrent COW may have moved us.
        res_path = db.execute_query(
            "SELECT file_path FROM works WHERE id=?", (work_id,)
        )
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
        if not safe_pdf_path_under_dir(pdfs_dir, filename):
            return {
                "status": 400,
                "body": {"error": "Invalid or unsafe PDF storage path"},
                "wrote_pdf": False,
            }

        target_filename = filename
        target_fp = stored_fp
        cow_retarget = False
        exclusive_lock = None

        if other_works_share_managed_filename(
            db, filename, exclude_work_id=work_id
        ):
            exclusive_name = allocate_exclusive_managed_filename(work_id, filename)
            if not safe_pdf_path_under_dir(pdfs_dir, exclusive_name):
                return {
                    "status": 400,
                    "body": {"error": "Invalid or unsafe PDF storage path"},
                    "wrote_pdf": False,
                }
            exclusive_lock = managed_pdf_path_lock(pdfs_dir, exclusive_name)
            if exclusive_lock is None:
                return {
                    "status": 400,
                    "body": {"error": "Invalid or unsafe PDF storage path"},
                    "wrote_pdf": False,
                }
            exclusive_lock.acquire()
            target_filename = exclusive_name
            target_fp = f"/api/pdfs/{exclusive_name}"
            cow_retarget = True

        # True after an exclusive COW write until works.file_path is retargeted.
        # If retarget never commits, the exclusive bytes are unreferenced and
        # must be deleted so failures do not accumulate orphan managed files.
        cow_exclusive_unreferenced = False
        try:
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

            target_path = atomic_replace_managed_pdf_bytes(
                pdfs_dir, target_filename, pdf_bytes
            )
            if cow_retarget:
                cow_exclusive_unreferenced = True
                try:
                    updated = retarget_work_managed_file_path(
                        db, work_id, target_fp
                    )
                except Exception as e:
                    unlink_managed_pdf_best_effort(pdfs_dir, target_filename)
                    cow_exclusive_unreferenced = False
                    LOGGER.warning(
                        "pdf_cow_retarget_failed work_id=%s error_type=%s",
                        safe_log_id(work_id),
                        safe_error_type(e),
                    )
                    return {
                        "status": 500,
                        "body": {"error": "Failed to retarget managed PDF"},
                        "wrote_pdf": False,
                    }
                if updated != 1:
                    # Concurrent delete (or other miss): UPDATE ran with 0 rows.
                    # Exclusive bytes are still unreferenced — remove them.
                    unlink_managed_pdf_best_effort(pdfs_dir, target_filename)
                    cow_exclusive_unreferenced = False
                    LOGGER.info(
                        "pdf_cow_retarget_work_gone work_id=%s rows=%s",
                        safe_log_id(work_id),
                        updated,
                    )
                    return {
                        "status": 404,
                        "body": {"error": "Work not found"},
                        "wrote_pdf": False,
                    }
                # Retarget committed — exclusive file is now referenced.
                cow_exclusive_unreferenced = False
                LOGGER.info(
                    "pdf_cow_retarget work_id=%s",
                    safe_log_id(work_id),
                )
            changed, reason = maybe_linearize_pdf_in_place(
                target_path, context="work-pdf-overwrite"
            )
            # Linearize may os.replace again — re-resolve via sanitizer then fsync.
            fsync_managed_pdf_parent(pdfs_dir, target_filename)
            LOGGER.info(
                "pdf_linearize_result context=work-pdf-overwrite changed=%s reason=%s",
                "true" if changed else "false",
                safe_log_label(reason),
            )
            try:
                text_index.sync_work(work_id, target_fp)
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
                body_404: dict[str, Any] = {"error": "Work not found"}
                if cow_retarget:
                    body_404["file_path"] = target_fp
                return {
                    "status": 404,
                    "body": body_404,
                    "wrote_pdf": True,
                }
            except ValueError as e:
                if str(e) == STALE_CODE:
                    # COW may already have committed works.file_path to the
                    # exclusive file. Surface that path so the client can leave
                    # the shared URL even though materialization is stale.
                    stale = _stale_body(db, work_id)
                    if cow_retarget:
                        stale["file_path"] = target_fp
                    return {
                        "status": 409,
                        "body": stale,
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
            if cow_retarget:
                body["file_path"] = target_fp
            if materialized_rev is not None:
                body["materialized_pdf_annotation_revision"] = materialized_rev
                mat = db.get_work_pdf_materialization(work_id)
                if mat:
                    body["canonical_annotation_set_revision"] = mat[
                        "canonical_annotation_set_revision"
                    ]
                    body["stale"] = mat["stale"]
            return {"status": 200, "body": body, "wrote_pdf": True}
        except Exception:
            if cow_exclusive_unreferenced:
                unlink_managed_pdf_best_effort(pdfs_dir, target_filename)
                cow_exclusive_unreferenced = False
            raise
        finally:
            if exclusive_lock is not None:
                exclusive_lock.release()
