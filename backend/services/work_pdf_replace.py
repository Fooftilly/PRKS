"""Replace managed Work PDF bytes and optionally mark materialization.

Coordinates filesystem write, linearization, text-index sync, Mentioned-role
extraction, and materialization claim/mark. HTTP adapters parse the request and
map the returned status/body; they do not own this workflow.

Shared ``/api/pdfs/<file>`` references are copy-on-write: before overwriting
bytes that another Work still points at, this Work is retargeted to an exclusive
managed filename so siblings keep their prior bytes and materialization marks.

All on-disk paths are resolved through ``safe_pdf_path_under_dir`` immediately
before use so DB/user-derived basenames cannot escape the managed pdfs dir.

Every write that publishes canonical managed-PDF bytes follows the
``backend.fs_durability`` convention through that module's primitives, never a
bare ``os.fsync``: the contents are flushed with the strongest barrier the
platform offers before anything may treat them as stored, and the containing
directory is flushed afterwards.

Where that boundary falls depends on how the bytes become canonical. A
replacement writes a sibling temporary and syncs it before ``os.replace()``
publishes the canonical name, so the barrier precedes the name. An exclusive
create already holds its final name -- reserving that name is the point of
creating it exclusively -- so nothing can be sequenced before it; the barrier
precedes the *report* instead, and the store returns that name for a Work to
reference only once the contents have passed it. Neither shape lets a caller
learn of a managed PDF whose bytes have not.

Linearization runs *after* that boundary and is optional, so it must never be
what makes the first write durable.
"""

from __future__ import annotations

from typing import Any

from backend.pdf_materialization import STALE_CODE


def stale_materialization_body(db, work_id: str) -> dict[str, Any]:
    mat = db.get_work_pdf_materialization(work_id) or {}
    return {
        "error": "PDF materialization is stale",
        "code": STALE_CODE,
        "canonical_annotation_set_revision": mat.get("canonical_annotation_set_revision"),
        "materialized_pdf_annotation_revision": mat.get(
            "materialized_pdf_annotation_revision"
        ),
    }


