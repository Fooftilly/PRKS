import logging
import os
from dataclasses import dataclass

from backend.db_manager import (
    PRKSDatabase,
    prks_delete_pdf_thumbnails_for_work_id,
    safe_pdf_path_under_dir,
)
from backend.text_index import PRKSTextIndex

LOGGER = logging.getLogger("prks.work_deletion")


@dataclass(frozen=True)
class WorkDeletionResult:
    existed: bool
    cleanup_failures: tuple[str, ...] = ()


def _remove_managed_pdf(file_path: str, pdfs_dir: str) -> None:
    fp = (file_path or "").strip()
    if not fp.startswith("/api/pdfs/"):
        return
    filename = fp.split("/")[-1]
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
    try:
        text_index.remove_work(work_id)
    except Exception as e:
        failures.append("text_index")
        LOGGER.warning(
            "work_delete_text_index_cleanup_failed work_id=%s error=%s",
            work_id,
            e,
        )
    try:
        thumbnail_failures = prks_delete_pdf_thumbnails_for_work_id(
            work_id,
            db.storage.thumbs_dir,
        )
        if thumbnail_failures:
            failures.append("thumbnails")
            LOGGER.warning(
                "work_delete_thumbnails_cleanup_failed work_id=%s failed=%s",
                work_id,
                thumbnail_failures,
            )
    except Exception as e:
        failures.append("thumbnails")
        LOGGER.warning(
            "work_delete_thumbnails_cleanup_failed work_id=%s error=%s",
            work_id,
            e,
        )
    if record is not None:
        try:
            _remove_managed_pdf(record.file_path, db.storage.pdfs_dir)
        except OSError as e:
            failures.append("pdf")
            LOGGER.warning(
                "work_delete_pdf_cleanup_failed work_id=%s error=%s",
                work_id,
                e,
            )
    return WorkDeletionResult(existed=record is not None, cleanup_failures=tuple(failures))
