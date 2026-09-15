"""Work lifecycle: DELETE_WORK destruction.

Work construction and the many Work field/relationship families live elsewhere.
This module is only the identity-destroying half: one operation, no base
revision, absence is convergence.

Online `DELETE /api/works/:id` has never refused a delete (unlike Person /
Folder / Concept). Offline must not invent relationship refusals the endpoint
cannot produce. Filesystem and derived-index cleanup stay post-commit
best-effort via `backend.work_deletion`, the same path the ordinary DELETE uses.
"""
from backend.db_manager import (
    DeletedWorkRecord,
    managed_pdf_filename,
    referenced_managed_pdf_filename,
)


def validate_delete(op):
    if op["payload"] != {}:
        raise ValueError("INVALID_ENVELOPE")
    if op["base_revision"] is not None:
        raise ValueError("INVALID_BASE_REVISION")


def delete_work_record_on_conn(conn, work_id):
    """Remove the Work row on the caller's transaction. Cascades handle links."""
    row = conn.execute(
        "SELECT file_path FROM works WHERE id = ?", (work_id,)).fetchone()
    if row is None:
        return None
    file_path = "" if row["file_path"] is None else str(row["file_path"])
    deleted_filename = managed_pdf_filename(file_path)
    conn.execute("DELETE FROM works WHERE id = ?", (work_id,))
    still_referenced = False
    if deleted_filename is not None:
        survivors = conn.execute(
            "SELECT file_path FROM works WHERE file_path IS NOT NULL"
        ).fetchall()
        still_referenced = any(
            referenced_managed_pdf_filename(r["file_path"]) == deleted_filename
            for r in survivors
        )
    return DeletedWorkRecord(
        work_id=work_id,
        file_path=file_path,
        managed_pdf_still_referenced=still_referenced,
    )


def apply_delete(db, conn, op, received_at):
    work_id = op["entity_id"]
    existed = conn.execute(
        "SELECT 1 FROM works WHERE id = ?", (work_id,)).fetchone() is not None
    record = delete_work_record_on_conn(conn, work_id)
    # Cleanup hints travel with the ACK so the HTTP adapter can run the same
    # post-commit best-effort path as DELETE /api/works/:id without opening a
    # second mutation. Absence is still ACKNOWLEDGED — that is the goal.
    result = {
        "code": "ACKNOWLEDGED",
        "work_id": work_id,
        "changed": bool(record is not None and existed),
    }
    if record is not None:
        result["file_path"] = record.file_path
        result["managed_pdf_still_referenced"] = record.managed_pdf_still_referenced
    return 200, result


def _handler(validate_fn, apply_fn):
    return type("_Handler", (), {"validate": staticmethod(validate_fn),
                                 "apply": staticmethod(apply_fn)})()


DELETE_HANDLER = _handler(validate_delete, apply_delete)
