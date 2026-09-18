"""Adopt byte-only PDF user markup into canonical annotation metadata.

Legacy libraries may have highlights embedded in managed PDF bytes without
rows in ``annotations``. Metadata is canonical for V2: discover viewer user
markup, insert missing IDs, never silently delete byte-only markup, and never
touch Links/widgets/non-user artifacts.

``default_is_user_markup`` mirrors the browser
``prksIsUserMarkupAnnotation`` classifier so every managed user type the
viewer would persist (ink, free text, stamps, shapes, strikeout/squiggly,
types 3–15, comments, segmentRects/inkList) can enter canonical metadata on
adoption. Link exclusion mirrors ``prksIsPdfLinkAnnotation`` (including
legacy numeric type ``1`` and flattened type ``2`` without URI).
"""

from __future__ import annotations

import re
from typing import Any, Callable, Iterable, Optional

from backend.pdf_annotations import (
    WorkAnnotationError,
    normalize_annotation,
)

# pdf.js / EmbedPDF user markup numbers. Type 1 is NEVER user markup here —
# browser ``prksIsPdfLinkAnnotation`` treats rawType 1 as a link before any
# user-type allowlist. Type 2 is link unless EmbedPDF text-markup heuristics.
_USER_TYPE_NUMS = frozenset({3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15})
_DENY_TYPE_NUMS = frozenset(
    {1, 2, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27}
)
_ALLOW_TOKENS = frozenset(
    {
        "highlight",
        "underline",
        "strikeout",
        "strikethrough",
        "strike",
        "squiggly",
        "ink",
        "freetext",
        "caret",
        "stamp",
        "square",
        "circle",
        "line",
        "polygon",
        "polyline",
        "text",
        "note",
        "comment",
    }
)
_DENY_TOKENS = frozenset(
    {
        "watermark",
        "widget",
        "popup",
        "movie",
        "sound",
        "screen",
        "trapnet",
        "redact",
        "attachment",
    }
)
_DENY_SUBSTR = (
    "watermark",
    "widget",
    "popup",
    "fileattachment",
    "movie",
    "sound",
    "screen",
    "printermark",
    "trapnet",
    "redact",
)
_ALLOW_NEEDLES = (
    "highlight",
    "underline",
    "strikeout",
    "strikethrough",
    "squiggly",
    "freetext",
    "textmarkup",
)
_RECORD_TYPE_RE = (
    "highlight|underline|strike|squiggly|ink|freetext|textmarkup|"
    "caret|line|polygon|polyline|square|circle|stamp"
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


def _primary_type_number(item: dict) -> Optional[int]:
    raw = item.get("type", item.get("annotationType", item.get("subtype", item.get("Subtype"))))
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float) and raw == int(raw):
        return int(raw)
    if isinstance(raw, str) and raw.strip().isdigit():
        return int(raw.strip())
    return None


def _type_blob(item: dict) -> str:
    parts = []
    for key in ("type", "annotationType", "subtype", "subType", "Subtype"):
        value = item.get(key)
        if value is None or value == "":
            continue
        parts.append(str(value).lower())
    return " ".join(parts)


def _has_geometry(item: dict) -> bool:
    if item.get("rect") or item.get("rects") or item.get("quadPoints"):
        return True
    if item.get("points") or item.get("position") or item.get("location"):
        return True
    if item.get("box") or item.get("Rect") or item.get("QuadPoints"):
        return True
    segs = item.get("segmentRects")
    if isinstance(segs, list) and segs:
        return True
    ink = item.get("inkList")
    if isinstance(ink, list) and ink:
        return True
    verts = item.get("vertices")
    if isinstance(verts, list) and verts:
        return True
    return False


def _is_likely_annotation_object(item: dict) -> bool:
    if item.get("deleted") is True:
        return False
    if not _annotation_id(item):
        return False
    if not _has_geometry(item):
        return False
    type_raw = (
        item.get("type")
        or item.get("annotationType")
        or item.get("subtype")
        or item.get("subType")
        or item.get("Subtype")
        or ""
    )
    type_lo = (type_raw if isinstance(type_raw, str) else str(type_raw)).lower()
    type_hints = (
        "high",
        "mark",
        "text",
        "comment",
        "strike",
        "under",
        "stamp",
        "note",
        "ink",
        "shape",
        "freetext",
        "square",
        "circle",
        "line",
        "poly",
        "squiggly",
    )
    has_type = any(t in type_lo for t in type_hints)
    has_content = bool(
        item.get("contents")
        or item.get("content")
        or item.get("comment")
        or item.get("text")
        or item.get("body")
    )
    return (
        has_type
        or has_content
        or bool(item.get("rect") or item.get("rects") or item.get("quadPoints"))
        or (isinstance(item.get("segmentRects"), list) and bool(item.get("segmentRects")))
    )


def _embed_type2_is_user_text_markup(item: dict) -> bool:
    ink = item.get("inkList")
    if isinstance(ink, list) and ink:
        return True
    segs = item.get("segmentRects")
    if isinstance(segs, list) and segs:
        return True
    blob = " ".join(
        str(item.get(k) or "").lower()
        for k in ("subtype", "subType", "annotationType", "type", "name")
        if item.get(k) not in (None, "")
    )
    if any(
        tok in blob
        for tok in (
            "highlight",
            "underline",
            "strike",
            "squiggly",
            "ink",
            "freetext",
            "textmarkup",
        )
    ):
        return True
    custom = item.get("custom")
    if isinstance(custom, dict) and custom:
        return True
    return False


def _uri_like(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text:
        return False
    return bool(re.match(r"^https?:\/\/", text, re.I) or "://" in text)


def is_pdf_link_annotation(item: dict) -> bool:
    """Mirror browser ``prksIsPdfLinkAnnotation`` — exclude before user-type rules."""
    if not isinstance(item, dict):
        return False
    raw_type = item.get(
        "type",
        item.get("annotationType", item.get("subtype", item.get("Subtype"))),
    )
    # Legacy engine: numeric 1 is a link. String "1" after JSON round-trip too.
    if raw_type == 1 or raw_type == "1":
        return True
    typ_blob = _type_blob(item)
    if "link" in typ_blob:
        return True
    sub = str(item.get("subtype") or item.get("Subtype") or "").lower()
    if "link" in sub:
        return True
    # Viewer often sets subject/title/contents to the literal "Link".
    label_fields = [
        item.get("contents"),
        item.get("content"),
        item.get("comment"),
        item.get("text"),
        item.get("subject"),
        item.get("title"),
        item.get("body"),
    ]
    label_joined = " ".join(str(v) for v in label_fields if v).strip().lower()
    if label_joined == "link":
        return True
    if (
        _uri_like(item.get("uri"))
        or _uri_like(item.get("url"))
        or _uri_like(item.get("URL"))
    ):
        return True
    action = item.get("action")
    if isinstance(action, dict):
        at = str(action.get("type") or action.get("S") or action.get("s") or "").lower()
        if any(x in at for x in ("uri", "goto", "gotor", "launch", "named")):
            return True
        dest = action.get("uri") or action.get("URL") or action.get("url")
        if _uri_like(dest):
            return True
    if item.get("dest") is not None or item.get("destination") is not None:
        return True
    # pdf.js Link = 2; flattened clones may omit URI — link unless text markup.
    if raw_type == 2 or raw_type == "2":
        return not _embed_type2_is_user_text_markup(item)
    return False


def default_is_non_user(item: dict) -> bool:
    if not isinstance(item, dict):
        return True
    if is_pdf_link_annotation(item):
        return True
    blob = _type_blob(item)
    return any(tok in blob for tok in ("widget", "watermark"))


def default_is_user_markup(item: dict) -> bool:
    """Shared equivalent of browser ``prksIsUserMarkupAnnotation``."""
    if not isinstance(item, dict) or not _annotation_id(item):
        return False
    # Browser returns early for links before any user-type allowlist.
    if is_pdf_link_annotation(item):
        return False
    if default_is_non_user(item):
        return False

    geometry_backed = _is_likely_annotation_object(item)
    typ_lo = str(
        item.get("type") or item.get("annotationType") or item.get("subtype") or ""
    ).lower()
    page_ok = any(
        item.get(k) is not None
        for k in ("pageIndex", "page", "pageNumber", "page_index")
    )
    persisted_text_note = (
        not geometry_backed
        and page_ok
        and typ_lo in ("note", "comment", "freetext", "text")
        and bool(
            item.get("contents")
            or item.get("content")
            or item.get("comment")
            or item.get("text")
        )
    )
    if not geometry_backed and not persisted_text_note:
        return False

    if geometry_backed:
        segs = item.get("segmentRects")
        if isinstance(segs, list) and segs:
            return True
        ink = item.get("inkList")
        if isinstance(ink, list) and ink:
            return True
        for key in ("recordType", "schemaType", "annotationKind", "variant", "name"):
            rec = item.get(key)
            if isinstance(rec, str) and re.search(_RECORD_TYPE_RE, rec, re.I):
                return True

    type_num = _primary_type_number(item)
    if type_num is not None:
        if type_num == 2 and _embed_type2_is_user_text_markup(item):
            return True
        if type_num in _DENY_TYPE_NUMS:
            return False
        if type_num in _USER_TYPE_NUMS:
            return True

    blob = _type_blob(item)
    if any(d in blob for d in _DENY_SUBSTR):
        return False
    tokens = [t for t in re.split(r"[^a-z0-9]+", blob) if t]
    if any(t in _DENY_TOKENS for t in tokens):
        return False
    if any(t in _ALLOW_TOKENS for t in tokens):
        return True
    if any(n in blob for n in _ALLOW_NEEDLES):
        return True

    custom = item.get("custom")
    if (
        isinstance(custom, dict)
        and isinstance(custom.get("prksComment"), str)
        and custom.get("prksComment", "").strip()
    ):
        return True
    return False


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
    Returns ``{adopted: [...ids], skipped_existing: n, skipped_non_user: n,
    skipped_known_absent: n}``.

    Known-stale PDF bytes are never authority: when
    ``canonical_annotation_set_revision > materialized_pdf_annotation_revision``,
    adoption is refused (do not resurrect deleted markup still embedded in an
    older PDF). Adoption is only for legacy/current when the two revisions
    match.

    IDs with ``sync_entity_revisions.revision > 0`` are known-absent (or
    previously mutated) and must never be re-inserted from a viewer list —
    that path would clear a durable DELETE tombstone. A client viewer list is
    also not proof the *server* managed PDF embeds the new tip: successful
    adopt bumps only the canonical generation (leaving materialization lag),
    never jointly advances ``materialized_pdf_annotation_revision``.
    """
    from backend import pdf_annotation_sync, pdf_materialization

    is_user = is_user_markup or default_is_user_markup
    is_link = is_non_user or default_is_non_user
    mat = pdf_materialization.get_materialization_on_conn(conn, work_id)
    if mat is None:
        raise WorkAnnotationError("work_not_found", "Work not found.", 404)
    if int(mat["canonical_annotation_set_revision"]) > int(
        mat["materialized_pdf_annotation_revision"]
    ):
        raise WorkAnnotationError(
            pdf_materialization.STALE_CODE,
            "Cannot adopt annotations from known-stale PDF bytes.",
            409,
        )

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
    skipped_known_absent = 0
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
        # Tombstone / prior mutation: never resurrect from viewer bytes.
        if pdf_annotation_sync.get_revision(conn, work_id, ann_id) > 0:
            skipped_known_absent += 1
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
        # Metadata meaning changed. Do not mark materialization from a client
        # viewer list — only a POST /pdf claim may advance that generation.
        pdf_materialization.bump_canonical_annotation_set_on_conn(conn, work_id)

    return {
        "work_id": work_id,
        "adopted": adopted,
        "adopted_count": len(adopted),
        "skipped_existing": skipped_existing,
        "skipped_non_user": skipped_non_user,
        "skipped_known_absent": skipped_known_absent,
    }
