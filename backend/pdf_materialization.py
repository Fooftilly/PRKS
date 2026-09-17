"""PDF annotation materialization lag tracking.

Canonical annotation metadata and managed PDF bytes are separate. Semantic
ACK of CREATE/SET/DELETE must never wait on a multi-MB PDF upload.

`works.canonical_annotation_set_revision` advances when annotation meaning
changes. `works.materialized_pdf_annotation_revision` advances only when the
managed PDF bytes have been rebuilt to embed that annotation generation.

Equal ⇒ PDF bytes match metadata. Canonical ahead ⇒
`ANNOTATION_MATERIALIZATION_STALE` (rebuild; never a user-facing binary conflict).

Materialization may only claim an *acknowledged* generation: the client must
send `materialized_annotation_set_revision` equal to the current canonical
generation. A future or arbitrary generation must never clear stale.
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


def accept_materialization_revision_on_conn(
    conn, work_id: str, claimed_revision: Any
) -> int:
    """Validate a client materialization claim against current canonical.

    Durable PDF replace must claim the *current* acknowledged generation.
    Returns that generation when `claimed_revision` matches exactly. Raises
    LookupError when the Work is gone, ValueError(STALE_CODE) when the claim
    is missing, not an int, behind, or ahead. A future claim must never mark
    materialized and clear stale.
    """
    status = get_materialization_on_conn(conn, work_id)
    if status is None:
        raise LookupError("ENTITY_NOT_FOUND")
    canonical = int(status["canonical_annotation_set_revision"] or 0)
    if claimed_revision is None:
        raise ValueError(STALE_CODE)
    try:
        claimed = int(claimed_revision)
    except (TypeError, ValueError) as exc:
        raise ValueError(STALE_CODE) from exc
    if claimed != canonical:
        raise ValueError(STALE_CODE)
    return canonical


def mark_pdf_materialized_on_conn(
    conn, work_id: str, *, at_revision: Optional[int] = None
) -> int:
    """Record that managed PDF bytes now embed `at_revision`.

    `at_revision` may be omitted (mark at current canonical tip) or set to any
    integer in ``[0, canonical]``. Values above canonical are refused so a
    future/arbitrary generation can never clear stale. Values below canonical
    intentionally preserve materialization lag (e.g. adopt).
    """
    status = get_materialization_on_conn(conn, work_id)
    if status is None:
        return 0
    canonical = int(status["canonical_annotation_set_revision"] or 0)
    if at_revision is None:
        at_revision = canonical
    else:
        try:
            at_revision = int(at_revision)
        except (TypeError, ValueError) as exc:
            raise ValueError(STALE_CODE) from exc
        if at_revision < 0 or at_revision > canonical:
            raise ValueError(STALE_CODE)
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
