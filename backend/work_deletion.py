"""Work deletion and its post-commit side effects.

The canonical Work row commits on its own; filesystem and derived-index
cleanup happen afterwards and may fail. Only ONE of those side effects has no
other owner: a managed PDF. Once the row is gone so is `works.file_path`, the
only thing that said which bytes belonged to that Work -- so the identity is
recorded durably, in the same transaction as the delete, and the claim is
settled by a retry that always re-asks the live catalogue first.

The other categories are deliberately NOT given retry records, because
rebuilding them is both safer and already implemented:

- text index: `text_index.reconcile_all()` removes rows with no canonical Work
  (`removed_orphans`) and runs at startup.
- research index: `PRKSResearchIndex.reconcile_all()` does the same.
- thumbnails: `prune_orphan_pdf_thumbnails()` deletes cached thumbnails no PDF
  Work's `thumb_page` claims, and runs at startup.

All three are derived and disposable; a stale row there is a wrong search hit
or a wasted cache file, not private bytes left on disk forever.
"""
import logging
import os
from dataclasses import dataclass
from typing import Collection, Optional

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

# One bounded pass. Startup must not turn a long-broken pdfs directory into an
# unbounded amount of work before the server binds, and an ordinary DELETE must
# not pay for a large backlog. Whatever is left is retried by the next pass.
PENDING_PDF_CLEANUP_RETRY_LIMIT = 64


@dataclass(frozen=True)
class WorkDeletionResult:
    existed: bool
    cleanup_failures: tuple[str, ...] = ()
    # True when this deletion left a managed PDF PRKS still owes a delete. The
    # canonical deletion is committed either way; this says the degradation is
    # durable rather than lost with the log line.
    pending_pdf_cleanup: bool = False


def canonical_managed_basename(filename: str) -> Optional[str]:
    """The exact managed basename ``filename`` names, or None.

    Reuses `managed_pdf_filename()` rather than repeating its rules, so a name
    accepted here is exactly a name PRKS would accept as a Work's own managed
    PDF ownership path.
    """
    name = str(filename or "")
    if not name:
        return None
    return managed_pdf_filename(f"/api/pdfs/{name}")


def record_pending_pdf_cleanup_on_conn(conn, filename: str) -> bool:
    """Claim, on the caller's transaction, that ``filename`` is owed a delete.

    Written with the Work-row delete so the identity outlives both the row and
    the process: a crash between the commit and `os.remove()` still leaves a
    retryable record. Only a managed basename is stored -- never an absolute
    path, which would bind the claim to one storage root and survive a restore
    into a different one.

    The basename is the primary key, so deleting two Works that shared one
    managed PDF, or replaying a delete, can never accumulate a second permanent
    row for the same bytes.
    """
    name = canonical_managed_basename(filename)
    if name is None:
        return False
    conn.execute(
        "INSERT INTO pending_pdf_cleanup (filename) VALUES (?) "
        "ON CONFLICT(filename) DO NOTHING",
        (name,),
    )
    return True


def forget_pending_pdf_cleanup(db: PRKSDatabase, filename: str) -> None:
    """Settle the claim on ``filename``. Best-effort and idempotent.

    Called only where the cleanup this row owns is genuinely finished: the
    bytes are gone, or another Work now references that name so nothing is
    owed. A failed delete here leaves the row, and the next pass resolves it
    from the filesystem (a missing file is success) -- never a second removal.
    """
    name = canonical_managed_basename(filename)
    if name is None:
        return
    try:
        db.execute_query("DELETE FROM pending_pdf_cleanup WHERE filename = ?", (name,))
    except Exception as e:
        LOGGER.warning(
            "pdf_cleanup_claim_release_failed error_type=%s", safe_error_type(e)
        )


def pending_pdf_cleanup_count(db: PRKSDatabase) -> int:
    """How many managed PDFs PRKS still owes a delete. 0 when healthy."""
    try:
        rows = db.execute_query("SELECT COUNT(*) AS c FROM pending_pdf_cleanup")
    except Exception:
        return 0
    if not rows:
        return 0
    row = rows[0]
    return int(row["c"] if isinstance(row, dict) else row[0])


def managed_filename_reference_state(
    db: PRKSDatabase, filename: str
) -> Optional[bool]:
    """Does a current Work row resolve to this managed basename? None = unknown.

    Cleanup must ask the live DB -- never trust a deletion-time snapshot. An
    exact DELETE_WORK op_id replay can arrive after another Work has begun
    referencing the same file; a stale
    ``managed_pdf_still_referenced=False`` would then delete live bytes.

    The third answer is what makes the durable claim safe. "Referenced" and
    "could not read the catalogue" demand opposite responses -- retire the
    claim, or keep it and try again later -- so a boolean that fails closed
    would either strand orphans forever or discard a claim over a database
    hiccup. Every caller must handle None before deleting anything.
    """
    name = str(filename or "")
    if not name:
        return True
    try:
        rows = db.execute_query(
            "SELECT file_path FROM works WHERE file_path IS NOT NULL"
        )
    except Exception:
        return None
    for row in rows or ():
        fp = row["file_path"] if isinstance(row, dict) else row[0]
        if referenced_managed_pdf_filename(fp) == name:
            return True
    return False


def _remove_managed_pdf(db: PRKSDatabase, filename: Optional[str], pdfs_dir: str) -> bool:
    """Remove one orphaned managed PDF. True when nothing is owed afterwards.

    Raises OSError for a transient filesystem failure; the durable claim is
    already recorded, so the caller reports the degradation rather than
    inventing retry state here. An unreadable catalogue or an uncontainable
    name returns False with the claim untouched -- declining to act is not
    completion, and the caller must not report the cleanup as done.
    """
    if filename is None:
        return True
    referenced = managed_filename_reference_state(db, filename)
    if referenced is None:
        return False
    if referenced:
        # A live Work owns these bytes. Nothing is owed, so any claim on the
        # name is retired here rather than left dormant over a file PRKS is
        # now serving -- the same decision a retry pass makes.
        forget_pending_pdf_cleanup(db, filename)
        return True
    abs_path = safe_pdf_path_under_dir(pdfs_dir, filename)
    if not abs_path:
        return False
    try:
        os.remove(abs_path)
    except FileNotFoundError:
        # Already gone is the successful terminal state, not a failure: the
        # only thing this claim owed is that the bytes not be there.
        forget_pending_pdf_cleanup(db, filename)
        return True
    forget_pending_pdf_cleanup(db, filename)
    return True


def retry_pending_pdf_cleanup(
    db: PRKSDatabase,
    *,
    limit: int = PENDING_PDF_CLEANUP_RETRY_LIMIT,
    skip: Collection[str] = (),
) -> dict:
    """One bounded pass over the managed PDFs PRKS still owes a delete.

    Idempotent by construction: every outcome is decided from the live
    catalogue and the filesystem, never from what the record remembers about
    the Work that once owned the file. A record is removed only when the bytes
    are gone (`removed`/`missing`) or another Work has claimed the name
    (`superseded`); everything else keeps the record for a later attempt.

    ``skip`` excludes names the caller has just acted on itself, so a deletion
    that just failed does not fail again in the same breath.
    """
    summary = {
        "claimed": 0,
        "removed": 0,
        "missing": 0,
        "superseded": 0,
        "failed": 0,
        "unsafe": 0,
        "deferred": 0,
    }
    try:
        rows = db.execute_query(
            "SELECT filename FROM pending_pdf_cleanup "
            "ORDER BY recorded_at ASC, filename ASC LIMIT ?",
            (int(limit),),
        )
    except Exception as e:
        LOGGER.warning("pdf_cleanup_retry_unavailable error_type=%s", safe_error_type(e))
        return summary
    if not rows:
        return summary
    excluded = {str(name) for name in (skip or ())}
    pdfs_dir = db.storage.pdfs_dir
    for row in rows:
        raw = row["filename"] if isinstance(row, dict) else row[0]
        name = canonical_managed_basename(raw)
        if name is None:
            # A name this PRKS would not accept as a managed PDF is never
            # resolved to a path and never deleted. Keeping the row is the
            # conservative answer: refusing to act is not proof of completion.
            summary["claimed"] += 1
            summary["unsafe"] += 1
            continue
        if name in excluded:
            continue
        summary["claimed"] += 1
        # Re-asked per record, immediately before the removal it authorizes.
        referenced = managed_filename_reference_state(db, name)
        if referenced is None:
            summary["deferred"] += 1
            continue
        if referenced:
            # A live Work owns these bytes now. The old deletion's claim is
            # retired rather than left dormant: while that Work exists the
            # bytes are its, and when it is deleted that deletion records its
            # own claim.
            forget_pending_pdf_cleanup(db, name)
            summary["superseded"] += 1
            continue
        abs_path = safe_pdf_path_under_dir(pdfs_dir, name)
        if not abs_path:
            summary["unsafe"] += 1
            continue
        try:
            os.remove(abs_path)
        except FileNotFoundError:
            forget_pending_pdf_cleanup(db, name)
            summary["missing"] += 1
            continue
        except OSError as e:
            summary["failed"] += 1
            LOGGER.warning(
                "pdf_cleanup_retry_failed error_type=%s", safe_error_type(e)
            )
            continue
        forget_pending_pdf_cleanup(db, name)
        summary["removed"] += 1
    if summary["claimed"]:
        LOGGER.info(
            "pdf_cleanup_retry claimed=%s removed=%s missing=%s superseded=%s "
            "failed=%s unsafe=%s deferred=%s",
            summary["claimed"],
            summary["removed"],
            summary["missing"],
            summary["superseded"],
            summary["failed"],
            summary["unsafe"],
            summary["deferred"],
        )
    return summary


def retry_pending_pdf_cleanup_at_startup(db: PRKSDatabase) -> dict:
    """Bounded recovery pass at process start. Never fails startup.

    What survives the pass is stated as a count, so a library that keeps
    failing to remove bytes says so on every start instead of degrading
    silently. Counts only -- a managed filename is library content.
    """
    try:
        summary = retry_pending_pdf_cleanup(db)
    except Exception as e:
        LOGGER.warning("pdf_cleanup_retry_skipped error_type=%s", safe_error_type(e))
        return {}
    remaining = pending_pdf_cleanup_count(db)
    if remaining:
        LOGGER.warning("pdf_cleanup_pending count=%s", remaining)
    return summary


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

    A failure here is reported, not swallowed, and for the managed PDF it is
    also already durable -- `delete_work_record_on_conn()` recorded the claim
    inside the delete transaction. Derived state is left to the startup
    reconcilers named in this module's docstring.
    """
    del managed_pdf_still_referenced  # deletion-time snapshot is not authoritative
    failures: list[str] = []
    pending_pdf = False
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
    filename = managed_pdf_filename(file_path) if existed else None
    if filename is not None:
        try:
            # Reported from what actually happened, not from the absence of an
            # exception: a check this pass could not complete leaves the claim
            # standing, and saying otherwise would hide the degradation the
            # durable record exists to preserve.
            pending_pdf = not _remove_managed_pdf(db, filename, db.storage.pdfs_dir)
        except OSError as e:
            failures.append("pdf")
            pending_pdf = True
            LOGGER.warning(
                "work_delete_pdf_cleanup_failed work_id=%s error_type=%s",
                wid,
                safe_error_type(e),
            )
    # Draining the backlog here is what makes recovery independent of a
    # restart: the process that could not remove bytes a minute ago is usually
    # the one that can now. Bounded, and never re-attempts the name this call
    # just handled.
    retry_pending_pdf_cleanup(
        db, skip=() if filename is None else (filename,)
    )
    return WorkDeletionResult(
        existed=existed,
        cleanup_failures=tuple(failures),
        pending_pdf_cleanup=pending_pdf,
    )


def delete_work(db: PRKSDatabase, text_index: PRKSTextIndex, work_id: str) -> WorkDeletionResult:
    record = db.delete_work_record(work_id)
    return cleanup_after_work_delete(
        db,
        text_index,
        work_id,
        file_path="" if record is None else record.file_path,
        existed=record is not None,
    )
