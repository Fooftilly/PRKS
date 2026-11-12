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
    normalize_annotation,
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


def _view_json(item: Optional[dict]) -> str:
    if item is None:
        return ""
    return json.dumps(
        semantic_annotation_view(item),
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _fit_conflict(
    result: dict[str, Any],
    current: Optional[dict],
    desired: Optional[dict],
) -> dict[str, Any]:
    """Project disagreement into STRUCTURED_RESULT_KEYS-compatible scalars.

    Nested ``current_annotation`` / ``requested_annotation`` objects are not
    storable in ``prks-local-v1`` — ``settle()`` rejects them as
    ``invalid_result`` and the op retries forever. Mirror the metadata model:
    JSON string values when they fit, else bounded previews + byte sizes.
    Presence uses ``current_state`` / ``requested_state`` booleans.
    """
    from backend import work_metadata_sync as meta

    cur_s = _view_json(current)
    des_s = _view_json(desired)
    result["current_state"] = current is not None
    result["requested_state"] = desired is not None
    if current is not None:
        result["current_value"] = cur_s
    if desired is not None:
        result["requested_value"] = des_s
    return meta.fit_terminal_result(result, cur_s, des_s)


# ---- CREATE ---------------------------------------------------------------

def validate_create(op):
    if op["base_revision"] is not None:
        raise ValueError("INVALID_BASE_REVISION")
    payload = op["payload"]
    if "annotation_id" not in payload:
        raise ValueError("INVALID_ENVELOPE")
    annotation_id = _validate_annotation_id(payload["annotation_id"])
    _payload_annotation(payload, annotation_id)


# ---- SET ------------------------------------------------------------------

def validate_set(op):
    if op["base_revision"] is None:
        raise ValueError("INVALID_BASE_REVISION")
    payload = op["payload"]
    if "annotation_id" not in payload:
        raise ValueError("INVALID_ENVELOPE")
    annotation_id = _validate_annotation_id(payload["annotation_id"])
    _payload_annotation(payload, annotation_id)


# ---- DELETE ---------------------------------------------------------------

def validate_delete(op):
    if op["base_revision"] is None:
        raise ValueError("INVALID_BASE_REVISION")
    payload = op["payload"]
    if set(payload) != {"annotation_id"}:
        raise ValueError("INVALID_ENVELOPE")
    _validate_annotation_id(payload["annotation_id"])


