import logging
import os
from dataclasses import dataclass

from backend.db_manager import (
    PRKSDatabase,
    managed_pdf_filename,
    prks_delete_pdf_thumbnails_for_work_id,
    referenced_managed_pdf_filename,
    safe_pdf_path_under_dir,
)
from backend.log_safety import safe_error_type, safe_log_id
from backend.text_index import PRKSTextIndex

LOGGER = logging.getLogger("prks.work_deletion")


@dataclass(frozen=True)
class WorkDeletionResult:
    existed: bool
    cleanup_failures: tuple[str, ...] = ()


def managed_filename_currently_referenced(db: PRKSDatabase, filename: str) -> bool:
    """True if any current Work row resolves to this managed PDF basename.

    Cleanup must ask the live DB — never trust a deletion-time snapshot.
    An exact DELETE_WORK op_id replay can arrive after another Work has begun
    referencing the same file; a stale ``managed_pdf_still_referenced=False``
    would then delete live bytes. Fail closed (treat as referenced) if the
    catalogue cannot be read.
    """
    name = str(filename or "")
    if not name:
        return True
    try:
        rows = db.execute_query(
            "SELECT file_path FROM works WHERE file_path IS NOT NULL"
        )
    except Exception:
        return True
    for row in rows or ():
        fp = row["file_path"] if isinstance(row, dict) else row[0]
        if referenced_managed_pdf_filename(fp) == name:
            return True
    return False


def _remove_managed_pdf(db: PRKSDatabase, file_path: str, pdfs_dir: str) -> None:
    filename = managed_pdf_filename(file_path)
    if filename is None:
        return
    if managed_filename_currently_referenced(db, filename):
        return
    abs_path = safe_pdf_path_under_dir(pdfs_dir, filename)
    if not abs_path:
        return
    try:
        os.remove(abs_path)
    except FileNotFoundError:
        return


def cleanup_after_work_delete(
    db: PRKSDatabase,
    text_index: PRKSTextIndex,
    work_id: str,
    *,
    file_path: str = "",
    managed_pdf_still_referenced: bool = False,
    existed: bool = True,
) -> WorkDeletionResult:
    """Best-effort derived/FS cleanup after a committed Work row delete.

    ``managed_pdf_still_referenced`` is accepted for call-site compatibility
    but ignored: PDF removal always re-checks current Work references.
    """
    del managed_pdf_still_referenced  # deletion-time snapshot is not authoritative
    failures: list[str] = []
    wid = safe_log_id(work_id)
    try:
        text_index.remove_work(work_id)
    except Exception as e:
        failures.append("text_index")
        LOGGER.warning(
            "work_delete_text_index_cleanup_failed work_id=%s error_type=%s",
            wid,
            safe_error_type(e),
        )
    try:
        from backend.research_index import get_research_index

        idx = get_research_index()
        if os.path.isfile(idx.db_path):
            idx.remove_work(work_id)
    except RuntimeError:
        pass
    except Exception as e:
        failures.append("research_index")
        LOGGER.warning(
            "research_index_cleanup_failed work_id=%s error_type=%s",
            wid,
            safe_error_type(e),
        )
    try:
        thumbnail_failures = prks_delete_pdf_thumbnails_for_work_id(
            work_id,
            db.storage.thumbs_dir,
        )
        if thumbnail_failures:
            failures.append("thumbnails")
            LOGGER.warning(
                "work_delete_thumbnails_cleanup_failed work_id=%s failed_count=%s",
                wid,
                len(thumbnail_failures),
            )
    except Exception as e:
        failures.append("thumbnails")
        LOGGER.warning(
            "work_delete_thumbnails_cleanup_failed work_id=%s error_type=%s",
            wid,
            safe_error_type(e),
        )
    if existed:
        try:
            _remove_managed_pdf(db, file_path, db.storage.pdfs_dir)
        except OSError as e:
            failures.append("pdf")
            LOGGER.warning(
                "work_delete_pdf_cleanup_failed work_id=%s error_type=%s",
                wid,
                safe_error_type(e),
            )
    return WorkDeletionResult(existed=existed, cleanup_failures=tuple(failures))


def delete_work(db: PRKSDatabase, text_index: PRKSTextIndex, work_id: str) -> WorkDeletionResult:
    record = db.delete_work_record(work_id)
    return cleanup_after_work_delete(
        db,
        text_index,
        work_id,
        file_path="" if record is None else record.file_path,
        existed=record is not None,
    )
