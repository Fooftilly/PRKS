import logging
import os
from dataclasses import dataclass

from backend.db_manager import (
    PRKSDatabase,
    managed_pdf_filename,
    prks_delete_pdf_thumbnails_for_work_id,
    safe_pdf_path_under_dir,
)
from backend.log_safety import safe_error_type, safe_log_id
from backend.text_index import PRKSTextIndex

LOGGER = logging.getLogger("prks.work_deletion")


@dataclass(frozen=True)
class WorkDeletionResult:
    existed: bool
    cleanup_failures: tuple[str, ...] = ()


def _remove_managed_pdf(file_path: str, pdfs_dir: str, still_referenced: bool) -> None:
    filename = managed_pdf_filename(file_path)
    if filename is None or still_referenced:
        return
    abs_path = safe_pdf_path_under_dir(pdfs_dir, filename)
    if not abs_path:
        return
    try:
        os.remove(abs_path)
    except FileNotFoundError:
        return


def delete_work(db: PRKSDatabase, text_index: PRKSTextIndex, work_id: str) -> WorkDeletionResult:
    record = db.delete_work_record(work_id)
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
    if record is not None:
        try:
            _remove_managed_pdf(
                record.file_path,
                db.storage.pdfs_dir,
                record.managed_pdf_still_referenced,
            )
        except OSError as e:
            failures.append("pdf")
            LOGGER.warning(
                "work_delete_pdf_cleanup_failed work_id=%s error_type=%s",
                wid,
                safe_error_type(e),
            )
    return WorkDeletionResult(existed=record is not None, cleanup_failures=tuple(failures))
