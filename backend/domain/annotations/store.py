"""Annotation rows, revisions and reads on PostgreSQL (L-A-1 #635).

**Target.** The Work-addressed API names a Work; its annotations are those of
the Work's primary Manifestation's primary Asset when that Asset is a managed
file (:class:`Target`). A Work without one has no annotations and cannot get
any. Identity is ``(asset_id, id)``: one viewer id on two Assets (two copies
of a PDF) names two annotations, so an operation can only ever reach the
Asset its Work resolves to.

**Revisions.** Each annotation's conflict unit is the scope
``asset-annotation/[asset_id, id]`` (``docs/work-identity-model.md`` §14.2,
the Asset-owned form of SQLite's ``pdf-annotation/[work_id, id]``), with
SQLite's rules: a create leaves it at 0, only a change or a delete advances
it, and a deleted annotation's scope stays as its tombstone. The Asset's
``canonical_annotation_set_revision`` advances once per command that changes
the set. Scopes keyed by the Asset follow it through a Work merge untouched.

**Locks.** Every write takes the Work row ``FOR NO KEY UPDATE`` (the order
every Works command uses) and then the Asset row ``FOR UPDATE`` (§7.4), so
the writes on one Asset's annotations are serialized and each decides on
committed state: revisions are read under that lock and a scope row is
locked only to advance it. A Work delete takes the Work ``FOR UPDATE`` and
so waits for, or is waited for by, any annotation write. A materialization
(:mod:`backend.domain.annotations.materialize`) takes the Work ``FOR UPDATE``
from the start, as ``lifecycle.replace_object`` does.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, LiteralString, Optional

from backend.domain.identity.library_scope import LibraryScope
from backend.domain.works import revisions
from backend.pdf_annotations import WorkAnnotationError, reconstruct_annotation
from backend.pdf_materialization import STALE_CODE

SCOPE_TYPE = "asset-annotation"
NO_PDF = "work_pdf_not_found"
_MALFORMED = "malformed_annotation_payload"
_MAX_ID_CHARS = 200
_MAX_PAGE_INDEX = 2**31 - 1

_LOCK_WORK: LiteralString = (
    "SELECT id FROM prks.works WHERE library_id = %(library_id)s AND id = %(work_id)s FOR NO KEY UPDATE"
)
# A materialization rewrites the Asset's bytes: it takes the Work row the way
# ``lifecycle.replace_object`` does, never upgrading a weaker lock it holds.
_LOCK_WORK_FOR_REWRITE: LiteralString = (
    "SELECT id FROM prks.works WHERE library_id = %(library_id)s AND id = %(work_id)s FOR UPDATE"
)
_WORK_EXISTS: LiteralString = (
    "SELECT id FROM prks.works WHERE library_id = %(library_id)s AND id = %(work_id)s"
)
_PRIMARY_PDF: LiteralString = """
SELECT a.id FROM prks.works w
JOIN prks.manifestations m
  ON m.library_id = w.library_id AND m.id = w.primary_manifestation_id AND m.work_id = w.id
JOIN prks.assets a
  ON a.library_id = m.library_id AND a.id = m.primary_asset_id AND a.manifestation_id = m.id
WHERE w.library_id = %(library_id)s AND w.id = %(work_id)s AND a.kind = 'managed_file'
"""
_ASSET_REVISIONS: LiteralString = """
SELECT canonical_annotation_set_revision AS canonical, materialized_pdf_annotation_revision AS materialized
FROM prks.assets WHERE library_id = %(library_id)s AND id = %(asset_id)s
"""
_COLUMNS: LiteralString = """
SELECT id, type, content, page_index, color, geometry::text AS geometry_json,
       to_char(updated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') AS updated_at
FROM prks.annotations
WHERE library_id = %(library_id)s AND asset_id = %(asset_id)s
"""
# SQLite's order: page, NULL first, then id by byte.
_ROWS: LiteralString = _COLUMNS + ' ORDER BY page_index ASC NULLS FIRST, id COLLATE "C"'
_ROW: LiteralString = _COLUMNS + " AND id = %(annotation_id)s"
_INSERT: LiteralString = """
INSERT INTO prks.annotations (library_id, asset_id, id, work_id, type, content, page_index, color, geometry)
VALUES (%(library_id)s, %(asset_id)s, %(annotation_id)s, %(work_id)s, %(type)s, %(content)s,
        %(page_index)s, %(color)s, %(geometry)s::jsonb)
"""
_UPDATE: LiteralString = """
UPDATE prks.annotations
SET type = %(type)s, content = %(content)s, page_index = %(page_index)s, color = %(color)s,
    geometry = %(geometry)s::jsonb, updated_at = now()
WHERE library_id = %(library_id)s AND asset_id = %(asset_id)s AND id = %(annotation_id)s
"""
_DELETE: LiteralString = """
DELETE FROM prks.annotations
WHERE library_id = %(library_id)s AND asset_id = %(asset_id)s AND id = %(annotation_id)s
"""
_BUMP_SET: LiteralString = """
UPDATE prks.assets
SET canonical_annotation_set_revision = canonical_annotation_set_revision + 1, updated_at = now()
WHERE library_id = %(library_id)s AND id = %(asset_id)s
RETURNING canonical_annotation_set_revision AS canonical
"""
# SQLite keeps the set revision on the Work row and touches it with each change.
_TOUCH_WORK: LiteralString = (
    "UPDATE prks.works SET updated_at = now() WHERE library_id = %(library_id)s AND id = %(work_id)s"
)
_TOMBSTONES: LiteralString = """
SELECT scope_id, revision FROM prks.entity_revisions
WHERE library_id = %(library_id)s AND scope_type = %(scope_type)s
  AND left(scope_id, %(prefix_len)s) = %(prefix)s AND revision > 0
"""
_WORK_ANNOTATIONS: LiteralString = """
SELECT asset_id, id FROM prks.annotations
WHERE library_id = %(library_id)s AND work_id = %(work_id)s
"""
_DELETE_WORK_ANNOTATIONS: LiteralString = (
    "DELETE FROM prks.annotations WHERE library_id = %(library_id)s AND work_id = %(work_id)s"
)
_WORK_ASSETS: LiteralString = (
    'SELECT id FROM prks.assets WHERE library_id = %(library_id)s AND work_id = %(work_id)s ORDER BY id COLLATE "C"'
)
_ADVANCE_ASSET_SCOPES: LiteralString = """
UPDATE prks.entity_revisions SET revision = revision + 1, updated_at = now()
WHERE library_id = %(library_id)s AND scope_type = %(scope_type)s
  AND left(scope_id, %(prefix_len)s) = %(prefix)s
"""


def scope_id(asset_id: str, annotation_id: str) -> str:
    """``[asset_id, annotation_id]`` in SQLite's canonical JSON form."""
    return json.dumps([asset_id, annotation_id], ensure_ascii=True, separators=(",", ":"))


def _scope_prefix(asset_id: str) -> dict[str, Any]:
    # Asset ids never contain a quote, so the prefix cannot reach another Asset.
    prefix = '["' + asset_id + '",'
    return {"scope_type": SCOPE_TYPE, "prefix": prefix, "prefix_len": len(prefix)}


@dataclass(frozen=True)
class Target:
    """The Asset a Work's annotations live on, and its two set revisions."""

    work_id: str
    asset_id: Optional[str]  # None: the Work has no managed PDF
    canonical: int = 0
    materialized: int = 0

    def params(self, **more: Any) -> dict[str, Any]:
        return {"work_id": self.work_id, "asset_id": self.asset_id, **more}


def _resolve(scope: LibraryScope, work_id: str, *, lock: bool) -> Target:
    row = scope.execute(_PRIMARY_PDF, {"work_id": work_id}).fetchone()
    if row is None:
        return Target(work_id, None)
    asset_id = str(row["id"])
    query = _ASSET_REVISIONS + " FOR UPDATE" if lock else _ASSET_REVISIONS
    found = scope.execute(query, {"asset_id": asset_id}).fetchone()
    if found is None:  # pragma: no cover - the Work row is held and the Asset is its primary
        raise RuntimeError("the primary Asset vanished under its Work's lock")
    return Target(work_id, asset_id, int(found["canonical"]), int(found["materialized"]))


def read_target(scope: LibraryScope, work_id: str) -> Optional[Target]:
    """The Work's annotation target in this snapshot; None when there is no such Work."""
    if scope.execute(_WORK_EXISTS, {"work_id": work_id}).fetchone() is None:
        return None
    return _resolve(scope, work_id, lock=False)


def lock_target(scope: LibraryScope, work_id: str) -> Optional[Target]:
    """Lock the Work, then its Asset, for a write; None when there is no such Work."""
    if scope.execute(_LOCK_WORK, {"work_id": work_id}).fetchone() is None:
        return None
    return _resolve(scope, work_id, lock=True)


def lock_for_rewrite(scope: LibraryScope, work_id: str) -> Optional[Target]:
    """Lock the Work ``FOR UPDATE``, then its Asset, for a rewrite of the bytes; None when there is no such Work."""
    if scope.execute(_LOCK_WORK_FOR_REWRITE, {"work_id": work_id}).fetchone() is None:
        return None
    return _resolve(scope, work_id, lock=True)


def require_pdf(target: Target) -> str:
    if target.asset_id is None:
        raise WorkAnnotationError(NO_PDF, "Work has no managed PDF.", 404)
    return target.asset_id


def _holds_nul(value: Any) -> bool:
    if isinstance(value, str):
        return "\x00" in value
    if isinstance(value, dict):
        return any(_holds_nul(key) or _holds_nul(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_holds_nul(item) for item in value)
    return False


def check_storable(normalized: dict[str, Any]) -> None:
    """Refuse what PostgreSQL cannot hold (SQLite stores it).

    NUL in any text (``text`` and ``jsonb`` refuse it), an id over 200
    characters (the sync families' bound, ``pdf_annotation_sync``), and a page
    index beyond ``integer``.
    """
    page = normalized["page_index"]
    if (_holds_nul(normalized) or len(normalized["id"]) > _MAX_ID_CHARS
            or (page is not None and page > _MAX_PAGE_INDEX)):
        raise WorkAnnotationError(_MALFORMED, "Annotation cannot be stored.")


def _values(normalized: dict[str, Any]) -> dict[str, Any]:
    return {"annotation_id": normalized["id"], "type": normalized["type"], "content": normalized["content"],
            "page_index": normalized["page_index"], "color": normalized["color"],
            "geometry": json.dumps(normalized["geometry"], allow_nan=False)}


def asset_annotations(scope: LibraryScope, asset_id: str) -> list[dict[str, Any]]:
    """The Asset's annotations as the API returns them, in SQLite's order."""
    return [reconstruct_annotation(row) for row in scope.execute(_ROWS, {"asset_id": asset_id}).fetchall()]


def work_annotations(scope: LibraryScope, work_id: str) -> list[dict[str, Any]]:
    """The Work's annotations (its primary managed PDF's); none for a missing Work or one without a PDF."""
    target = read_target(scope, work_id)
    if target is None or target.asset_id is None:
        return []
    return asset_annotations(scope, target.asset_id)


def current(scope: LibraryScope, asset_id: str, annotation_id: str) -> Optional[dict[str, Any]]:
    row = scope.execute(_ROW, {"asset_id": asset_id, "annotation_id": annotation_id}).fetchone()
    return None if row is None else reconstruct_annotation(row)


def revision(scope: LibraryScope, asset_id: str, annotation_id: str) -> int:
    key = scope_id(asset_id, annotation_id)
    return revisions.read(scope, SCOPE_TYPE, [key])[key]


def advance(scope: LibraryScope, asset_id: str, annotation_id: str) -> int:
    key = scope_id(asset_id, annotation_id)
    revisions.lock(scope, SCOPE_TYPE, key)
    return revisions.advance(scope, SCOPE_TYPE, key)


def insert(scope: LibraryScope, target: Target, normalized: dict[str, Any]) -> dict[str, Any]:
    """Construction: insert one normalized annotation; its revision stays 0."""
    check_storable(normalized)
    values = _values(normalized)
    scope.execute(_INSERT, target.params(**values))
    return reconstruct_annotation({"id": normalized["id"], "type": normalized["type"],
                                   "content": normalized["content"], "page_index": normalized["page_index"],
                                   "color": normalized["color"], "geometry_json": values["geometry"],
                                   "updated_at": None})


def update(scope: LibraryScope, target: Target, normalized: dict[str, Any]) -> int:
    """Rewrite one present annotation and advance its revision."""
    check_storable(normalized)
    scope.execute(_UPDATE, target.params(**_values(normalized)))
    return advance(scope, require_pdf(target), normalized["id"])


def delete(scope: LibraryScope, target: Target, annotation_id: str) -> Optional[int]:
    """Delete one annotation and advance its revision; None when it is absent."""
    asset_id = require_pdf(target)
    if scope.execute(_DELETE, target.params(annotation_id=annotation_id)).rowcount == 0:
        return None
    return advance(scope, asset_id, annotation_id)


def bump_set(scope: LibraryScope, target: Target) -> int:
    """Advance the Asset's canonical set revision after a change; returns it."""
    row = scope.execute(_BUMP_SET, target.params()).fetchone()
    if row is None:  # pragma: no cover - the Asset row is locked by this transaction
        raise RuntimeError("bump_set() needs the Asset locked first")
    scope.execute(_TOUCH_WORK, target.params())
    return int(row["canonical"])


def snapshot(scope: LibraryScope, work_id: str) -> Optional[dict[str, Any]]:
    """``get_work_annotations_snapshot``: values, revisions and set revisions from one snapshot."""
    target = read_target(scope, work_id)
    if target is None:
        return None
    items: list[dict[str, Any]] = []
    present: list[dict[str, Any]] = []
    known_absent: dict[str, int] = {}
    if target.asset_id is not None:
        items = asset_annotations(scope, target.asset_id)
        ids = [str(item["id"]) for item in items]
        found = revisions.read(scope, SCOPE_TYPE, [scope_id(target.asset_id, i) for i in ids])
        present = [{"annotation_id": i, "revision": found[scope_id(target.asset_id, i)]} for i in ids]
        live = set(ids)
        for row in scope.execute(_TOMBSTONES, _scope_prefix(target.asset_id)).fetchall():
            annotation_id = json.loads(row["scope_id"])[1]
            if annotation_id not in live:
                known_absent[annotation_id] = int(row["revision"])
    return {"work_id": work_id, "items": items, "annotations": present, "known_absent": known_absent,
            "canonical_annotation_set_revision": target.canonical,
            "materialized_pdf_annotation_revision": target.materialized}


def materialization(scope: LibraryScope, work_id: str) -> Optional[dict[str, Any]]:
    """``get_work_pdf_materialization``: whether the stored bytes embed the current set."""
    target = read_target(scope, work_id)
    if target is None:
        return None
    stale = target.canonical > target.materialized
    return {"work_id": work_id, "canonical_annotation_set_revision": target.canonical,
            "materialized_pdf_annotation_revision": target.materialized, "stale": stale,
            "code": STALE_CODE if stale else None}


def release_work(scope: LibraryScope, work_id: str) -> None:
    """Delete a Work's annotations before its Assets, leaving tombstones (§5.2).

    Called by the Work delete with the Work row held ``FOR UPDATE``. Every
    scope of the Work's Assets ends one revision on, an annotation never
    edited included (its row is ensured first), so the deleted state is
    recorded for every annotation the Work had.
    """
    present = scope.execute(_WORK_ANNOTATIONS, {"work_id": work_id}).fetchall()
    revisions.lock_many(scope, [(SCOPE_TYPE, scope_id(str(row["asset_id"]), str(row["id"]))) for row in present])
    scope.execute(_DELETE_WORK_ANNOTATIONS, {"work_id": work_id})
    for row in scope.execute(_WORK_ASSETS, {"work_id": work_id}).fetchall():
        scope.execute(_ADVANCE_ASSET_SCOPES, _scope_prefix(str(row["id"])))
