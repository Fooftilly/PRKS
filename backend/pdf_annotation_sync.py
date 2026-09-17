"""PDF annotation sync: CREATE / SET / DELETE as one revisioned aggregate each.

Conflict unit: one PRKS-managed annotation, scoped
``pdf-annotation / [work_id, annotation_id]``.

PDF bytes are never part of this protocol. Canonical meaning lives in the
``annotations`` table via ``pdf_annotations`` normalize/reconstruct. Materialized
PDF export is a later slice.

Construction (CREATE) leaves revision at 0. Mutation (SET/DELETE) requires a
base revision. Deletion preserves the revision row so a stale device cannot
silently resurrect or collide on a reused id.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from backend.pdf_annotations import (
    WorkAnnotationError,
    annotations_semantically_equal,
    normalize_annotation,
    reconstruct_annotation,
    round_trip_annotation,
    semantic_annotation_view,
)

SCOPE_TYPE = "pdf-annotation"
MAX_ANNOTATION_ID_CHARS = 200


def scope_key(work_id: str, annotation_id: str) -> str:
    return json.dumps([work_id, annotation_id], ensure_ascii=True, separators=(",", ":"))


def get_revision(conn, work_id: str, annotation_id: str) -> int:
    row = conn.execute(
        "SELECT revision FROM sync_entity_revisions WHERE scope_type = ? AND scope_id = ?",
        (SCOPE_TYPE, scope_key(work_id, annotation_id)),
    ).fetchone()
    return int(row[0]) if row else 0


def _advance(conn, work_id: str, annotation_id: str) -> int:
    conn.execute(
        """INSERT INTO sync_entity_revisions (scope_type, scope_id, revision)
           VALUES (?, ?, 1) ON CONFLICT (scope_type, scope_id)
           DO UPDATE SET revision = revision + 1, updated_at = CURRENT_TIMESTAMP""",
        (SCOPE_TYPE, scope_key(work_id, annotation_id)),
    )
    return get_revision(conn, work_id, annotation_id)


def _canonical_set_revision(conn, work_id: str) -> int:
    from backend import pdf_materialization

    status = pdf_materialization.get_materialization_on_conn(conn, work_id)
    if status is None:
        return 0
    return int(status["canonical_annotation_set_revision"] or 0)


def _bump_canonical_set_if_changed(conn, work_id: str, *, changed: bool) -> int:
    """Annotation meaning changed ⇒ PDF materialization may lag (Slice F).

    Returns the current canonical annotation-set generation after any bump.
    """
    from backend import pdf_materialization

    if changed:
        return pdf_materialization.bump_canonical_annotation_set_on_conn(conn, work_id)
    return _canonical_set_revision(conn, work_id)


def _work_exists(conn, work_id: str) -> bool:
    return (
        conn.execute("SELECT 1 FROM works WHERE id = ?", (work_id,)).fetchone()
        is not None
    )


def _row_for(conn, work_id: str, annotation_id: str):
    return conn.execute(
        """
        SELECT id, work_id, type, content, page_index, color, geometry_json, updated_at
        FROM annotations WHERE id = ? AND work_id = ?
        """,
        (annotation_id, work_id),
    ).fetchone()


def current_annotation(conn, work_id: str, annotation_id: str) -> Optional[dict]:
    """Reconstructed API object, or None when absent for this Work."""
    row = _row_for(conn, work_id, annotation_id)
    if row is None:
        return None
    return reconstruct_annotation(row)


def _owner_work_id(conn, annotation_id: str) -> Optional[str]:
    row = conn.execute(
        "SELECT work_id FROM annotations WHERE id = ?", (annotation_id,)
    ).fetchone()
    return row[0] if row else None


def _validate_annotation_id(annotation_id: Any) -> str:
    if not isinstance(annotation_id, str):
        raise ValueError("INVALID_ENVELOPE")
    text = annotation_id.strip()
    if not text or text != annotation_id or len(text) > MAX_ANNOTATION_ID_CHARS:
        raise ValueError("INVALID_ENVELOPE")
    return text


def _payload_annotation(payload: dict, annotation_id: str) -> dict:
    """Require `{annotation_id, annotation}`; annotation must carry the same id."""
    if set(payload) != {"annotation_id", "annotation"}:
        raise ValueError("INVALID_ENVELOPE")
    if payload["annotation_id"] != annotation_id:
        raise ValueError("INVALID_ENVELOPE")
    raw = payload["annotation"]
    if not isinstance(raw, dict):
        raise ValueError("INVALID_ENVELOPE")
    try:
        normalized = normalize_annotation(raw)
    except WorkAnnotationError as exc:
        raise ValueError("INVALID_ENVELOPE") from exc
    if normalized["id"] != annotation_id:
        raise ValueError("INVALID_ENVELOPE")
    return round_trip_annotation(raw)


def insert_annotation_on_conn(conn, work_id: str, item: dict) -> dict:
    """Construction primitive: insert one normalized annotation. Revision stays 0."""
    normalized = normalize_annotation(item)
    if normalized["id"] != item.get("id") and normalized["id"] != item.get("uuid"):
        # Prefer the already-normalized id.
        pass
    owner = _owner_work_id(conn, normalized["id"])
    if owner is not None and owner != work_id:
        raise WorkAnnotationError(
            "annotation_id_conflict",
            "Annotation ID belongs to another Work.",
            409,
        )
    geom = json.dumps(normalized["geometry"], allow_nan=False)
    conn.execute(
        """
        INSERT INTO annotations
            (id, work_id, type, content, page_index, color, geometry_json)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            normalized["id"],
            work_id,
            normalized["type"],
            normalized["content"],
            normalized["page_index"],
            normalized["color"],
            geom,
        ),
    )
    return reconstruct_annotation(
        {
            "id": normalized["id"],
            "type": normalized["type"],
            "content": normalized["content"],
            "page_index": normalized["page_index"],
            "color": normalized["color"],
            "geometry_json": geom,
            "updated_at": None,
        }
    )


def update_annotation_on_conn(conn, work_id: str, item: dict) -> tuple[bool, int, dict]:
    """Write one annotation aggregate and advance revision when meaning changes."""
    normalized = normalize_annotation(item)
    annotation_id = normalized["id"]
    current = current_annotation(conn, work_id, annotation_id)
    if current is None:
        raise WorkAnnotationError("work_not_found", "Annotation not found.", 404)
    revision = get_revision(conn, work_id, annotation_id)
    desired = round_trip_annotation(item)
    if annotations_semantically_equal(current, desired):
        return False, revision, current
    geom = json.dumps(normalized["geometry"], allow_nan=False)
    conn.execute(
        """
        UPDATE annotations SET
            type = ?, content = ?, page_index = ?, color = ?,
            geometry_json = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND work_id = ?
        """,
        (
            normalized["type"],
            normalized["content"],
            normalized["page_index"],
            normalized["color"],
            geom,
            annotation_id,
            work_id,
        ),
    )
    after = _advance(conn, work_id, annotation_id)
    return True, after, current_annotation(conn, work_id, annotation_id)


def delete_annotation_on_conn(conn, work_id: str, annotation_id: str) -> tuple[bool, int]:
    """Delete one annotation and advance its revision (tombstone bookkeeping)."""
    revision = get_revision(conn, work_id, annotation_id)
    deleted = (
        conn.execute(
            "DELETE FROM annotations WHERE id = ? AND work_id = ?",
            (annotation_id, work_id),
        ).rowcount
        > 0
    )
    if deleted:
        return True, _advance(conn, work_id, annotation_id)
    return False, revision


def advance_revision_if_changed_on_conn(
    conn, work_id: str, annotation_id: str, *, changed: bool
) -> int:
    """Shared helper for full-list replace parity: advance only on real change."""
    if changed:
        return _advance(conn, work_id, annotation_id)
    return get_revision(conn, work_id, annotation_id)


def get_annotations_state_on_conn(conn, work_id: str) -> Optional[dict]:
    """Per-annotation revisions for one Work (hydration for durable clients)."""
    snapshot = get_annotations_snapshot_on_conn(conn, work_id)
    if snapshot is None:
        return None
    return {
        "work_id": snapshot["work_id"],
        "annotations": snapshot["annotations"],
        "known_absent": snapshot["known_absent"],
    }


def get_annotations_snapshot_on_conn(conn, work_id: str) -> Optional[dict]:
    """One coherent acknowledged annotation snapshot from one DB transaction.

    Annotation values, per-annotation revisions, and materialization generation
    counters must come from the same canonical moment — never from two
    independent GETs that can race a concurrent mutation.
    """
    from backend import pdf_materialization

    if not _work_exists(conn, work_id):
        return None
    mat = pdf_materialization.get_materialization_on_conn(conn, work_id) or {}
    items = []
    present = []
    for row in conn.execute(
        """
        SELECT id, type, content, page_index, color, geometry_json, updated_at
        FROM annotations
        WHERE work_id = ?
        ORDER BY page_index ASC, id ASC
        """,
        (work_id,),
    ).fetchall():
        ann_id = row[0]
        items.append(reconstruct_annotation(row))
        present.append(
            {
                "annotation_id": ann_id,
                "revision": get_revision(conn, work_id, ann_id),
            }
        )
    # Known-absent scopes with revision > 0 (durable deletes / prior mutations).
    known_absent = {}
    present_ids = {row["annotation_id"] for row in present}
    for row in conn.execute(
        """
        SELECT scope_id, revision FROM sync_entity_revisions
        WHERE scope_type = ? AND revision > 0
        """,
        (SCOPE_TYPE,),
    ).fetchall():
        try:
            parts = json.loads(row[0])
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(parts, list) or len(parts) != 2:
            continue
        if parts[0] != work_id:
            continue
        ann_id = parts[1]
        if ann_id in present_ids:
            continue
        known_absent[ann_id] = int(row[1])
    return {
        "work_id": work_id,
        "items": items,
        "annotations": present,
        "known_absent": known_absent,
        "canonical_annotation_set_revision": int(
            mat.get("canonical_annotation_set_revision") or 0
        ),
        "materialized_pdf_annotation_revision": int(
            mat.get("materialized_pdf_annotation_revision") or 0
        ),
    }


def _conflict_body(current: Optional[dict], desired: Optional[dict]) -> dict:
    """Bounded conflict payload: semantic views, never raw megabyte geometry dumps beyond view."""
    body: dict[str, Any] = {}
    if current is not None:
        body["current_annotation"] = semantic_annotation_view(current)
    if desired is not None:
        body["requested_annotation"] = semantic_annotation_view(desired)
    return body


# ---- CREATE ---------------------------------------------------------------

def validate_create(op):
    if op["base_revision"] is not None:
        raise ValueError("INVALID_BASE_REVISION")
    payload = op["payload"]
    if "annotation_id" not in payload:
        raise ValueError("INVALID_ENVELOPE")
    annotation_id = _validate_annotation_id(payload["annotation_id"])
    _payload_annotation(payload, annotation_id)


def apply_create(db, conn, op, received_at):
    del db, received_at
    work_id = op["entity_id"]
    annotation_id = op["payload"]["annotation_id"]
    desired = _payload_annotation(op["payload"], annotation_id)
    result: dict[str, Any] = {
        "work_id": work_id,
        "annotation_id": annotation_id,
    }
    if not _work_exists(conn, work_id):
        result["code"] = "ENTITY_NOT_FOUND"
        return 404, result

    owner = _owner_work_id(conn, annotation_id)
    if owner is not None and owner != work_id:
        result["code"] = "ANNOTATION_ID_CONFLICT"
        return 409, result

    revision = get_revision(conn, work_id, annotation_id)
    current = current_annotation(conn, work_id, annotation_id)
    if current is not None:
        if annotations_semantically_equal(current, desired):
            result.update(
                code="ACKNOWLEDGED",
                changed=False,
                server_revision=revision,
                annotation=current,
                canonical_annotation_set_revision=_canonical_set_revision(conn, work_id),
            )
            return 200, result
        result.update(
            code="ANNOTATION_EXISTS",
            current_revision=revision,
            **_conflict_body(current, desired),
        )
        return 409, result

    if revision > 0:
        # Durable delete (or prior mutation) already claimed this id.
        result.update(
            code="ANNOTATION_ID_REUSED",
            current_revision=revision,
        )
        return 409, result

    stored = insert_annotation_on_conn(conn, work_id, desired)
    set_rev = _bump_canonical_set_if_changed(conn, work_id, changed=True)
    result.update(
        code="ACKNOWLEDGED",
        changed=True,
        server_revision=0,
        annotation=stored,
        canonical_annotation_set_revision=set_rev,
    )
    return 200, result


# ---- SET ------------------------------------------------------------------

def validate_set(op):
    if op["base_revision"] is None:
        raise ValueError("INVALID_BASE_REVISION")
    payload = op["payload"]
    if "annotation_id" not in payload:
        raise ValueError("INVALID_ENVELOPE")
    annotation_id = _validate_annotation_id(payload["annotation_id"])
    _payload_annotation(payload, annotation_id)


def apply_set(db, conn, op, received_at):
    del db, received_at
    from backend import work_metadata_sync as meta

    work_id = op["entity_id"]
    annotation_id = op["payload"]["annotation_id"]
    desired = _payload_annotation(op["payload"], annotation_id)
    result: dict[str, Any] = {
        "work_id": work_id,
        "annotation_id": annotation_id,
    }
    if not _work_exists(conn, work_id):
        result["code"] = "ENTITY_NOT_FOUND"
        return 404, result

    current = current_annotation(conn, work_id, annotation_id)
    if current is None:
        result["code"] = "ENTITY_NOT_FOUND"
        return 404, result

    revision = get_revision(conn, work_id, annotation_id)
    base = op["base_revision"]
    if base > revision:
        body = {
            "code": "FUTURE_REVISION",
            "current_revision": revision,
            **_conflict_body(current, desired),
        }
        result.update(body)
        return 400, meta.fit_terminal_result(
            result,
            json.dumps(semantic_annotation_view(current), ensure_ascii=False),
            json.dumps(semantic_annotation_view(desired), ensure_ascii=False),
        )
    if base < revision and not annotations_semantically_equal(current, desired):
        body = {
            "code": "REVISION_CONFLICT",
            "current_revision": revision,
            **_conflict_body(current, desired),
        }
        result.update(body)
        return 409, meta.fit_terminal_result(
            result,
            json.dumps(semantic_annotation_view(current), ensure_ascii=False),
            json.dumps(semantic_annotation_view(desired), ensure_ascii=False),
        )

    changed, after, stored = update_annotation_on_conn(conn, work_id, desired)
    set_rev = _bump_canonical_set_if_changed(conn, work_id, changed=changed)
    result.update(
        code="ACKNOWLEDGED",
        changed=changed,
        server_revision=after,
        annotation=stored,
        canonical_annotation_set_revision=set_rev,
    )
    return 200, result


# ---- DELETE ---------------------------------------------------------------

def validate_delete(op):
    if op["base_revision"] is None:
        raise ValueError("INVALID_BASE_REVISION")
    payload = op["payload"]
    if set(payload) != {"annotation_id"}:
        raise ValueError("INVALID_ENVELOPE")
    _validate_annotation_id(payload["annotation_id"])


def apply_delete(db, conn, op, received_at):
    del db, received_at
    work_id = op["entity_id"]
    annotation_id = op["payload"]["annotation_id"]
    result: dict[str, Any] = {
        "work_id": work_id,
        "annotation_id": annotation_id,
    }
    if not _work_exists(conn, work_id):
        result["code"] = "ENTITY_NOT_FOUND"
        return 404, result

    current = current_annotation(conn, work_id, annotation_id)
    revision = get_revision(conn, work_id, annotation_id)
    base = op["base_revision"]
    desired_absent = True

    if base > revision:
        result.update(
            code="FUTURE_REVISION",
            current_revision=revision,
            current_present=current is not None,
            requested_present=not desired_absent,
        )
        if current is not None:
            result["current_annotation"] = semantic_annotation_view(current)
        return 400, result

    if current is None:
        # Already absent: convergent ACK when base is not in the future.
        result.update(
            code="ACKNOWLEDGED",
            changed=False,
            server_revision=revision,
            present=False,
            canonical_annotation_set_revision=_canonical_set_revision(conn, work_id),
        )
        return 200, result

    if base < revision:
        # Stale delete against a present annotation — conflict.
        result.update(
            code="REVISION_CONFLICT",
            current_revision=revision,
            current_present=True,
            requested_present=False,
            current_annotation=semantic_annotation_view(current),
        )
        return 409, result

    changed, after = delete_annotation_on_conn(conn, work_id, annotation_id)
    set_rev = _bump_canonical_set_if_changed(conn, work_id, changed=changed)
    result.update(
        code="ACKNOWLEDGED",
        changed=changed,
        server_revision=after,
        present=False,
        canonical_annotation_set_revision=set_rev,
    )
    return 200, result


def _handler(validate_fn, apply_fn):
    return type(
        "_Handler",
        (),
        {"validate": staticmethod(validate_fn), "apply": staticmethod(apply_fn)},
    )()


CREATE_HANDLER = _handler(validate_create, apply_create)
SET_HANDLER = _handler(validate_set, apply_set)
DELETE_HANDLER = _handler(validate_delete, apply_delete)
