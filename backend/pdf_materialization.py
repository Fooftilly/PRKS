"""PDF annotation materialization lag tracking.

Canonical annotation metadata and managed PDF bytes are separate. Semantic
ACK of CREATE/SET/DELETE must never wait on a multi-MB PDF upload.

`works.canonical_annotation_set_revision` advances when annotation meaning
changes. `works.materialized_pdf_annotation_revision` advances only when the
managed PDF bytes have been rebuilt to embed that annotation generation.

Equal ⇒ PDF bytes match metadata. Canonical ahead ⇒
`ANNOTATION_MATERIALIZATION_STALE` (rebuild; never a user-facing binary conflict).
"""

from __future__ import annotations

from typing import Any, Optional


STALE_CODE = "ANNOTATION_MATERIALIZATION_STALE"


def get_materialization_on_conn(conn, work_id: str) -> Optional[dict[str, Any]]:
    row = conn.execute(
        """
        SELECT canonical_annotation_set_revision, materialized_pdf_annotation_revision
        FROM works WHERE id = ?
        """,
        (work_id,),
    ).fetchone()
    if row is None:
        return None
    canonical = int(row[0] or 0)
    materialized = int(row[1] or 0)
    return {
        "work_id": work_id,
        "canonical_annotation_set_revision": canonical,
        "materialized_pdf_annotation_revision": materialized,
        "stale": canonical > materialized,
        "code": STALE_CODE if canonical > materialized else None,
    }


def bump_canonical_annotation_set_on_conn(conn, work_id: str) -> int:
    """Advance canonical set revision after a real annotation meaning change."""
    conn.execute(
        """
        UPDATE works
        SET canonical_annotation_set_revision = canonical_annotation_set_revision + 1,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (work_id,),
    )
    row = conn.execute(
        "SELECT canonical_annotation_set_revision FROM works WHERE id = ?",
        (work_id,),
    ).fetchone()
    return int(row[0]) if row else 0


def mark_pdf_materialized_on_conn(
    conn, work_id: str, *, at_revision: Optional[int] = None
) -> int:
    """Record that managed PDF bytes now embed `at_revision` (default: current canonical)."""
    if at_revision is None:
        row = conn.execute(
            "SELECT canonical_annotation_set_revision FROM works WHERE id = ?",
            (work_id,),
        ).fetchone()
        if row is None:
            return 0
        at_revision = int(row[0] or 0)
    else:
        at_revision = int(at_revision)
        if at_revision < 0:
            raise ValueError("INVALID_MATERIALIZATION_REVISION")
    conn.execute(
        """
        UPDATE works
        SET materialized_pdf_annotation_revision = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (at_revision, work_id),
    )
    return at_revision


def require_current_materialization_on_conn(conn, work_id: str) -> dict[str, Any]:
    """Return status or raise ValueError(STALE_CODE) when PDF bytes lag metadata."""
    status = get_materialization_on_conn(conn, work_id)
    if status is None:
        raise LookupError("ENTITY_NOT_FOUND")
    if status["stale"]:
        raise ValueError(STALE_CODE)
    return status
