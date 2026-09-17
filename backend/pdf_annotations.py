"""Canonical PDF annotation metadata for the `annotations` table.

First-class columns: id, type, content, page_index, color.
`geometry_json` holds remaining JSON-compatible EmbedPDF fields needed to
reconstruct the submitted annotation. Alias keys already represented by
first-class columns are stripped so GET output does not accumulate
contradictory duplicates (page / pageNumber / pageIndex, etc.).
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Optional


class WorkAnnotationError(ValueError):
    """Controlled annotation-sync failure. http_status is 400, 404, or 409."""

    def __init__(self, code: str, message: str, http_status: int = 400):
        super().__init__(message)
        self.code = code
        self.http_status = http_status


_ID_KEYS = ("id", "uuid", "annotationId", "_id", "annotation_id", "ID")
_TYPE_KEYS = ("type", "annotationType", "subtype", "subType", "Subtype")
_CONTENT_KEYS = ("contents", "content", "comment", "text", "body")
_PAGE_KEYS = ("pageIndex", "page", "pageNumber", "page_index")
_COLOR_KEYS = ("color",)
_STRIP_FROM_GEOMETRY = frozenset(
    _ID_KEYS + _TYPE_KEYS + _CONTENT_KEYS + _PAGE_KEYS + _COLOR_KEYS + ("work_id",)
)

_MALFORMED = "malformed_annotation_payload"


def parse_annotations_json(raw: Any) -> list:
    """Parse the HTTP `annotations_json` string into a JSON list.

    Does not normalize entries. Invalid JSON, non-strings, and non-lists fail
    the entire payload so callers cannot treat garbage as an empty list.
    """
    if not isinstance(raw, str):
        raise WorkAnnotationError(
            _MALFORMED,
            "annotations_json must be a JSON list string.",
        )
    try:
        items = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise WorkAnnotationError(
            _MALFORMED,
            "annotations_json is not valid JSON.",
        ) from exc
    if not isinstance(items, list):
        raise WorkAnnotationError(
            _MALFORMED,
            "annotations_json must be a JSON list.",
        )
    return items


def normalize_annotation_list(items: Any) -> list[dict]:
    """Normalize and validate a complete annotation list before mutation."""
    if not isinstance(items, list):
        raise WorkAnnotationError(_MALFORMED, "Annotations must be a JSON list.")
    out: list[dict] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise WorkAnnotationError(
                _MALFORMED,
                "Each annotation must be an object.",
            )
        normalized = normalize_annotation(item)
        ann_id = normalized["id"]
        if ann_id in seen:
            raise WorkAnnotationError(_MALFORMED, "Duplicate annotation ID.")
        seen.add(ann_id)
        out.append(normalized)
    return out


def normalize_annotation(item: dict) -> dict:
    """Return first-class columns plus leftover geometry for one annotation."""
    ann_id = _canonical_id(item)
    a_type = _canonical_type(item)
    content = _canonical_content(item)
    page_index = _canonical_page_index(item)
    color = _canonical_color(item)
    geometry = _geometry_payload(item)
    return {
        "id": ann_id,
        "type": a_type,
        "content": content,
        "page_index": page_index,
        "color": color,
        "geometry": geometry,
    }


def reconstruct_annotation(row: Any) -> dict:
    """Rebuild API annotation JSON from one `annotations` row.

    Canonical columns win over any conflicting keys left in `geometry_json`.
    """
    geom = _load_geometry(row["geometry_json"] if "geometry_json" in row.keys() else None)
    item: dict[str, Any] = {
        "id": row["id"],
        "type": _restore_type(row["type"]),
        "contents": "" if row["content"] is None else str(row["content"]),
        "pageIndex": row["page_index"],
        "color": "" if row["color"] is None else str(row["color"]),
    }
    for key, value in geom.items():
        if key in _STRIP_FROM_GEOMETRY or key in item:
            continue
        item[key] = value
    updated = row["updated_at"] if "updated_at" in row.keys() else None
    if updated is not None:
        item["updated_at"] = updated
    return item


def round_trip_annotation(item: dict) -> dict:
    """Normalize then reconstruct as if the annotation were stored and loaded.

    Used by fidelity proofs: viewer → this → fresh viewer createAnnotation.
    Drops row bookkeeping (`updated_at`) so the result is create-ready.
    """
    normalized = normalize_annotation(item)
    reconstructed = reconstruct_annotation(
        {
            "id": normalized["id"],
            "type": normalized["type"],
            "content": normalized["content"],
            "page_index": normalized["page_index"],
            "color": normalized["color"],
            "geometry_json": json.dumps(normalized["geometry"], allow_nan=False),
            "updated_at": None,
        }
    )
    reconstructed.pop("updated_at", None)
    return reconstructed


# Geometry keys that must survive recreate for PRKS-managed markup fidelity.
_FIDELITY_GEOMETRY_KEYS = (
    "rect",
    "segmentRects",
    "strokeColor",
    "opacity",
    "blendMode",
    "custom",
)


def semantic_annotation_view(item: dict) -> dict:
    """Stable, comparable view of one annotation for fidelity equality.

    Compares canonical identity + PRKS-relevant geometry. Ignores viewer-only
    bookkeeping (created timestamps, authors, engine-private keys).
    """
    normalized = normalize_annotation(item)
    geom = normalized["geometry"]
    view: dict[str, Any] = {
        "id": normalized["id"],
        "type": normalized["type"],
        "content": normalized["content"],
        "page_index": normalized["page_index"],
        "color": normalized["color"],
    }
    for key in _FIDELITY_GEOMETRY_KEYS:
        if key in geom:
            view[key] = geom[key]
    return view


def annotations_semantically_equal(left: dict, right: dict) -> bool:
    """True when two annotations match for PRKS-managed recreate fidelity."""
    return semantic_annotation_view(left) == semantic_annotation_view(right)


def _load_geometry(raw: Any) -> dict:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return parsed


def _canonical_id(item: dict) -> str:
    for key in _ID_KEYS:
        if key not in item:
            continue
        value = item[key]
        if value is None:
            continue
        if isinstance(value, bool) or isinstance(value, (dict, list)):
            raise WorkAnnotationError(_MALFORMED, "Annotation ID is invalid.")
        text = str(value).strip()
        if text:
            return text
    raise WorkAnnotationError(_MALFORMED, "Annotation ID is required.")


def _canonical_type(item: dict) -> str:
    value = _first_scalar(item, _TYPE_KEYS)
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, int):
        return str(value)
    return str(value)


def _canonical_content(item: dict) -> str:
    value = _first_text(item, _CONTENT_KEYS)
    if value is None:
        return ""
    return value


def _canonical_page_index(item: dict) -> Optional[int]:
    for key in _PAGE_KEYS:
        if key not in item:
            continue
        return _parse_page_index(item[key])
    return None


def _canonical_color(item: dict) -> str:
    if "color" not in item or item["color"] is None:
        return ""
    value = item["color"]
    if isinstance(value, bool) or isinstance(value, (dict, list)):
        raise WorkAnnotationError(_MALFORMED, "Annotation color is invalid.")
    return str(value)


def _geometry_payload(item: dict) -> dict:
    geometry = {}
    for key, value in item.items():
        if key in _STRIP_FROM_GEOMETRY:
            continue
        geometry[key] = value
    try:
        json.dumps(geometry, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise WorkAnnotationError(
            _MALFORMED,
            "Annotation contains non-JSON fields.",
        ) from exc
    return geometry


def _parse_page_index(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool):
        raise WorkAnnotationError(_MALFORMED, "Annotation page index is invalid.")
    if isinstance(value, int):
        if value < 0:
            raise WorkAnnotationError(_MALFORMED, "Annotation page index is invalid.")
        return value
    if isinstance(value, float):
        if not value.is_integer() or value < 0:
            raise WorkAnnotationError(_MALFORMED, "Annotation page index is invalid.")
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.startswith("-"):
            raise WorkAnnotationError(_MALFORMED, "Annotation page index is invalid.")
        if not text.isdigit():
            raise WorkAnnotationError(_MALFORMED, "Annotation page index is invalid.")
        return int(text)
    raise WorkAnnotationError(_MALFORMED, "Annotation page index is invalid.")


def _first_scalar(item: dict, keys: Iterable[str]) -> Any:
    for key in keys:
        if key not in item:
            continue
        value = item[key]
        if value is None:
            continue
        if isinstance(value, bool) or isinstance(value, (dict, list)):
            raise WorkAnnotationError(_MALFORMED, "Annotation type is invalid.")
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _first_text(item: dict, keys: Iterable[str]) -> Optional[str]:
    for key in keys:
        if key not in item:
            continue
        value = item[key]
        if value is None:
            continue
        if isinstance(value, bool) or isinstance(value, (dict, list)):
            continue
        if isinstance(value, str):
            if not value:
                continue
            return value
        return str(value)
    return None


def _restore_type(stored: Any) -> Any:
    if stored is None:
        return ""
    text = str(stored)
    if not text:
        return ""
    if text.isdigit() or (text.startswith("-") and text[1:].isdigit()):
        return int(text)
    return text
