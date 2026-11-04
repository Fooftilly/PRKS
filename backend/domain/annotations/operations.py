"""Annotation commands on PostgreSQL with SQLite's rules (L-A-1 #635).

The decision tables, codes and result shapes are those of
``backend/pdf_annotation_sync.py`` (the ``CREATE``/``SET``/``DELETE_PDF_ANNOTATION``
families), ``PRKSDatabase.sync_work_annotations`` (the legacy full-list
replace) and ``backend/pdf_annotation_adopt.py`` (byte-only markup
adoption); payloads are validated and compared by the same functions. Each
command locks its target first (:func:`.store.lock_target`) and decides on
committed state under that lock. Differences from SQLite, each deliberate:

- An annotation belongs to an Asset, so a viewer id already used on another
  Work's PDF is that PDF's annotation, not a conflict: SQLite's
  ``ANNOTATION_ID_CONFLICT`` / ``annotation_id_conflict`` cannot arise.
- A Work without a managed PDF has nothing to annotate: an operation on it
  answers ``ENTITY_NOT_FOUND``, and a replace that would add annotations or
  an adoption answers ``work_pdf_not_found`` (404). SQLite stores
  annotations on any Work.
- What PostgreSQL cannot store (NUL, an id over 200 characters, a page
  index beyond ``integer``) is refused as malformed (SQLite stores it).
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

from backend import pdf_annotation_sync as rules
from backend.domain.annotations import store
from backend.domain.annotations.store import Target
from backend.domain.identity.library_scope import LibraryScope
from backend.pdf_annotation_adopt import _annotation_id, default_is_non_user, default_is_user_markup
from backend.pdf_annotations import (
    WorkAnnotationError,
    annotations_semantically_equal,
    normalize_annotation,
    normalize_annotation_list,
    round_trip_annotation,
)
from backend.pdf_materialization import STALE_CODE

Result = tuple[int, dict[str, Any]]


def _locked(scope: LibraryScope, work_id: str, result: dict[str, Any]) -> Target | Result:
    target = store.lock_target(scope, work_id)
    if target is None or target.asset_id is None:
        result["code"] = "ENTITY_NOT_FOUND"
        return 404, result
    return target


# -- sync families (pdf_annotation_sync) ------------------------------------------


def apply_create(scope: LibraryScope, work_id: str, payload: dict[str, Any]) -> Result:
    annotation_id = payload["annotation_id"]
    desired = rules._payload_annotation(payload, annotation_id)
    result: dict[str, Any] = {"work_id": work_id, "annotation_id": annotation_id}
    target = _locked(scope, work_id, result)
    if not isinstance(target, Target):
        return target
    asset_id = store.require_pdf(target)
    revision = store.revision(scope, asset_id, annotation_id)
    current = store.current(scope, asset_id, annotation_id)
    if current is not None:
        if annotations_semantically_equal(current, desired):
            result.update(code="ACKNOWLEDGED", changed=False, server_revision=revision, annotation=current,
                          canonical_annotation_set_revision=target.canonical)
            return 200, result
        result.update(code="ANNOTATION_EXISTS", current_revision=revision)
        return 409, rules._fit_conflict(result, current, desired)
    if revision > 0:
        # A durable delete (or an earlier change) already used this id here.
        result.update(code="ANNOTATION_ID_REUSED", current_revision=revision)
        return 409, result
    stored = store.insert(scope, target, normalize_annotation(desired))
    result.update(code="ACKNOWLEDGED", changed=True, server_revision=0, annotation=stored,
                  canonical_annotation_set_revision=store.bump_set(scope, target))
    return 200, result


def apply_set(scope: LibraryScope, work_id: str, payload: dict[str, Any], base: int) -> Result:
    annotation_id = payload["annotation_id"]
    desired = rules._payload_annotation(payload, annotation_id)
    result: dict[str, Any] = {"work_id": work_id, "annotation_id": annotation_id}
    target = _locked(scope, work_id, result)
    if not isinstance(target, Target):
        return target
    asset_id = store.require_pdf(target)
    current = store.current(scope, asset_id, annotation_id)
    if current is None:
        result["code"] = "ENTITY_NOT_FOUND"
        return 404, result
    revision = store.revision(scope, asset_id, annotation_id)
    if base > revision:
        result.update(code="FUTURE_REVISION", current_revision=revision)
        return 400, rules._fit_conflict(result, current, desired)
    if base < revision and not annotations_semantically_equal(current, desired):
        result.update(code="REVISION_CONFLICT", current_revision=revision)
        return 409, rules._fit_conflict(result, current, desired)
    if annotations_semantically_equal(current, desired):
        result.update(code="ACKNOWLEDGED", changed=False, server_revision=revision, annotation=current,
                      canonical_annotation_set_revision=target.canonical)
        return 200, result
    after = store.update(scope, target, normalize_annotation(desired))
    result.update(code="ACKNOWLEDGED", changed=True, server_revision=after,
                  annotation=store.current(scope, asset_id, annotation_id),
                  canonical_annotation_set_revision=store.bump_set(scope, target))
    return 200, result


def apply_delete(scope: LibraryScope, work_id: str, annotation_id: str, base: int) -> Result:
    result: dict[str, Any] = {"work_id": work_id, "annotation_id": annotation_id}
    target = _locked(scope, work_id, result)
    if not isinstance(target, Target):
        return target
    asset_id = store.require_pdf(target)
    current = store.current(scope, asset_id, annotation_id)
    revision = store.revision(scope, asset_id, annotation_id)
    if base > revision:
        result.update(code="FUTURE_REVISION", current_revision=revision)
        return 400, rules._fit_conflict(result, current, None)
    if current is None:
        # Already absent: a convergent acknowledgement.
        result.update(code="ACKNOWLEDGED", changed=False, server_revision=revision, present=False,
                      canonical_annotation_set_revision=target.canonical)
        return 200, result
    if base < revision:
        result.update(code="REVISION_CONFLICT", current_revision=revision)
        return 409, rules._fit_conflict(result, current, None)
    after = store.delete(scope, target, annotation_id)
    if after is None:  # pragma: no cover - the row was read under the Asset lock
        raise RuntimeError("an annotation vanished under its Asset's lock")
    result.update(code="ACKNOWLEDGED", changed=True, server_revision=after, present=False,
                  canonical_annotation_set_revision=store.bump_set(scope, target))
    return 200, result


# -- legacy full-list replace (PRKSDatabase.sync_work_annotations) ----------------


def _base_set_revision(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise WorkAnnotationError("malformed_annotation_payload",
                                  "canonical_annotation_set_revision must be an integer.", 400) from exc


def replace_all(scope: LibraryScope, work_id: str, items: list[dict[str, Any]],
                base_set_revision: Any = None) -> int:
    """Make the Work's annotations exactly ``items``; returns the set revision after."""
    target = store.lock_target(scope, work_id)
    if target is None:
        raise WorkAnnotationError("work_not_found", "Work not found.", 404)
    if base_set_revision is not None and _base_set_revision(base_set_revision) != target.canonical:
        raise WorkAnnotationError("ANNOTATION_SET_STALE", "Annotation set revision is stale.", 409)
    normalized = normalize_annotation_list(items)
    for row in normalized:
        store.check_storable(row)
    if target.asset_id is None and not normalized:
        return target.canonical
    asset_id = store.require_pdf(target)
    before = {str(item["id"]): item for item in store.asset_annotations(scope, asset_id)}
    changed = False
    for row in normalized:
        desired = round_trip_annotation({"id": row["id"], "type": row["type"], "contents": row["content"],
                                         "pageIndex": row["page_index"], "color": row["color"],
                                         **row["geometry"]})
        if row["id"] in before:
            if not annotations_semantically_equal(before[row["id"]], desired):
                changed = True
                store.update(scope, target, row)
            continue
        # As CREATE_PDF_ANNOTATION: an id a durable delete (or any change)
        # used here is never inserted again.
        if store.revision(scope, asset_id, row["id"]) > 0:
            raise WorkAnnotationError("ANNOTATION_ID_REUSED",
                                      "Annotation ID was previously used and cannot be reinserted.", 409)
        changed = True
        store.insert(scope, target, row)
    incoming = {row["id"] for row in normalized}
    for annotation_id in sorted(set(before) - incoming):
        changed = True
        store.delete(scope, target, annotation_id)
    return store.bump_set(scope, target) if changed else target.canonical


# -- byte-only markup adoption (pdf_annotation_adopt) -----------------------------


def adopt(scope: LibraryScope, work_id: str, viewer_items: Optional[Iterable[Any]]) -> dict[str, Any]:
    """Insert the viewer's user markup the Asset has no row for; never deletes.

    Refused while the stored bytes lag the set (they may still show deleted
    markup); an id with a revision is known absent and never comes back from
    bytes; a successful adoption advances only the canonical set revision.
    """
    target = store.lock_target(scope, work_id)
    if target is None:
        raise WorkAnnotationError("work_not_found", "Work not found.", 404)
    asset_id = store.require_pdf(target)
    if target.canonical > target.materialized:
        raise WorkAnnotationError(STALE_CODE, "Cannot adopt annotations from known-stale PDF bytes.", 409)
    present = {str(item["id"]) for item in store.asset_annotations(scope, asset_id)}
    adopted: list[str] = []
    skipped_existing = skipped_non_user = skipped_known_absent = 0
    seen: set[str] = set()
    for raw in viewer_items or []:
        if not isinstance(raw, dict) or default_is_non_user(raw) or not default_is_user_markup(raw):
            skipped_non_user += 1
            continue
        annotation_id = _annotation_id(raw)
        if not annotation_id or annotation_id in seen:
            continue
        seen.add(annotation_id)
        if annotation_id in present:
            skipped_existing += 1
            continue
        if store.revision(scope, asset_id, annotation_id) > 0:
            skipped_known_absent += 1
            continue
        normalized = normalize_annotation(raw)
        store.insert(scope, target, normalized)
        adopted.append(normalized["id"])
        present.add(normalized["id"])
    if adopted:
        # Metadata changed; only a materialization claim moves the bytes' revision.
        store.bump_set(scope, target)
    return {"work_id": work_id, "adopted": adopted, "adopted_count": len(adopted),
            "skipped_existing": skipped_existing, "skipped_non_user": skipped_non_user,
            "skipped_known_absent": skipped_known_absent}
