"""Adopt byte-only PDF user markup into canonical annotation metadata.

Legacy libraries may have highlights embedded in managed PDF bytes without
rows in ``annotations``. Metadata is canonical for V2: discover viewer user
markup, insert missing IDs, never silently delete byte-only markup, and never
touch Links/widgets/non-user artifacts.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, Optional

from backend.pdf_annotations import (
    WorkAnnotationError,
    normalize_annotation,
    reconstruct_annotation,
)


def _annotation_id(item: dict) -> str:
    for key in ("id", "uuid", "annotationId", "_id", "annotation_id", "ID"):
        if key not in item:
            continue
        value = item[key]
        if value is None or isinstance(value, (bool, dict, list)):
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def default_is_user_markup(item: dict) -> bool:
    """Conservative classifier mirroring the browser reconcile default."""
    if not isinstance(item, dict) or not _annotation_id(item):
        return False
    type_num = item.get("type", item.get("annotationType"))
    try:
        n = int(type_num)
    except (TypeError, ValueError):
        n = None
    if n in (9, 10):
        return True
    typ = str(item.get("type") or item.get("annotationType") or item.get("subtype") or "").lower()
    if typ in ("highlight", "underline"):
        return True
    custom = item.get("custom")
    if isinstance(custom, dict) and isinstance(custom.get("prksComment"), str):
        return True
    segs = item.get("segmentRects")
    if isinstance(segs, list) and segs:
        return True
    return False


def default_is_non_user(item: dict) -> bool:
    if not isinstance(item, dict):
        return True
    type_num = item.get("type", item.get("annotationType"))
    try:
        n = int(type_num)
    except (TypeError, ValueError):
        n = None
    if n == 2 and (item.get("uri") or item.get("url") or item.get("action") or item.get("A") or item.get("dest")):
        return True
    blob = " ".join(
        str(item.get(k) or "")
        for k in ("type", "annotationType", "subtype", "subType")
    ).lower()
    return any(tok in blob for tok in ("link", "uri", "goto", "widget", "watermark"))


def adopt_byte_only_user_markup_on_conn(
    conn,
    work_id: str,
    viewer_items: Iterable[Any],
    *,
    is_user_markup: Optional[Callable[[dict], bool]] = None,
    is_non_user: Optional[Callable[[dict], bool]] = None,
) -> dict[str, Any]:
    """Insert canonical rows for viewer user markup missing from metadata.

    Does not delete any annotation. Non-user artifacts are ignored.
    Returns ``{adopted: [...ids], skipped_existing: n, skipped_non_user: n}``.
    """
    is_user = is_user_markup or default_is_user_markup
    is_link = is_non_user or default_is_non_user
    exists = conn.execute("SELECT 1 FROM works WHERE id = ?", (work_id,)).fetchone()
    if not exists:
        raise WorkAnnotationError("work_not_found", "Work not found.", 404)

    present = {
        row[0]
        for row in conn.execute(
            "SELECT id FROM annotations WHERE work_id = ?",
            (work_id,),
        ).fetchall()
    }
    adopted: list[str] = []
    skipped_existing = 0
    skipped_non_user = 0
    seen: set[str] = set()

    for raw in viewer_items or []:
        if not isinstance(raw, dict):
            skipped_non_user += 1
            continue
        if is_link(raw) or not is_user(raw):
            skipped_non_user += 1
            continue
        ann_id = _annotation_id(raw)
        if not ann_id or ann_id in seen:
            continue
        seen.add(ann_id)
        if ann_id in present:
            skipped_existing += 1
            continue
        owner = conn.execute(
            "SELECT work_id FROM annotations WHERE id = ?",
            (ann_id,),
        ).fetchone()
        if owner is not None and owner[0] != work_id:
            raise WorkAnnotationError(
                "annotation_id_conflict",
                "Annotation ID belongs to another Work.",
                409,
            )
        normalized = normalize_annotation(raw)
        geom = __import__("json").dumps(normalized["geometry"], allow_nan=False)
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
        # Construction keeps per-annotation revision at 0.
        adopted.append(normalized["id"])
        present.add(normalized["id"])

    if adopted:
        from backend import pdf_materialization

        # Bytes already contain the adopted annotations. Bump the canonical set
        # revision for inventory coherence, but preserve any prior materialization
        # lag (do not invent staleness; do not clear real lag).
        before = pdf_materialization.get_materialization_on_conn(conn, work_id)
        pdf_materialization.bump_canonical_annotation_set_on_conn(conn, work_id)
        if before is not None:
            pdf_materialization.mark_pdf_materialized_on_conn(
                conn,
                work_id,
                at_revision=int(before["materialized_pdf_annotation_revision"]) + 1,
            )

    return {
        "work_id": work_id,
        "adopted": adopted,
        "adopted_count": len(adopted),
        "skipped_existing": skipped_existing,
        "skipped_non_user": skipped_non_user,
    }
