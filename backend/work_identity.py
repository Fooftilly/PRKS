"""Work -> Manifestation -> Asset identity layer (#60 Slice A).

See docs/work-identity-model.md. At schema 17 the `works` row is still the
ONLY authority for every field. `manifestations` and `assets` hold a
projection of it, kept current by the mirror triggers in `db_schema.sql`
(`works` -> new rows, one direction only), and the integrity triggers keep
ownership consistent. Nothing reads the new rows for product behavior yet.

This module owns the pieces that SQL cannot express and that later slices must
reuse unchanged:

* the deterministic backfill IDs (`uuid5`), which a SQL trigger cannot
  compute -- the mirror triggers therefore mint random IDs for rows created
  after the migration, and the legacy mapping is the stored, immutable
  `origin_work_id`, never a recomputed ID (§11.2);
* the Asset-creation predicate over durable revision state and legacy
  inferred-video rows (§12.2);
* `ensure_origin_asset`, the one way to create `origin_AS(W)` with its
  deterministic ID -- used by the Slice A backfill and meant for Slice D's lazy
  creation for a first queued Asset-bound edit (§14.2);
* the integrity query (§4.1 "What SQLite cannot enforce at commit").
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Iterable, List, Optional, Set, Tuple

# Fixed forever: changing it would give the same pre-#60 library different
# IDs on a second restore. Derived once as
# uuid5(NAMESPACE_URL, "https://github.com/Fooftilly/PRKS#work-identity-backfill").
NS_PRKS_BACKFILL = uuid.UUID("6d26588b-55c0-5737-95b2-bd0bdd2827e4")

MANIFESTATION_PREFIX = "MF"
ASSET_PREFIX = "AS"

# The pointer columns Slice A adds to `works`. They are not part of the legacy
# Work shape yet: the projection that exposes them is Slice C (§13.1), so the
# readers that still `SELECT *` strip them to keep responses byte-identical.
WORK_POINTER_COLUMNS = ("primary_manifestation_id", "citation_manifestation_id")

# Revision scopes that will become Asset-owned (§14.2). A Work holding any of
# them -- tombstones included -- must have an `origin_AS(W)` for those scopes
# to move to.
ASSET_BOUND_FIELD_SCOPES = frozenset({"thumb_page"})
WORK_FIELD_SCOPE_TYPE = "work-field"
WORK_SOURCE_SCOPE_TYPE = "work-source"
PDF_ANNOTATION_SCOPE_TYPE = "pdf-annotation"

# Columns the mirror copies; used by the backfill and by `mirror_drift`.
MANIFESTATION_MIRROR_COLUMNS = (
    "doc_type", "year", "published_date", "edition", "publisher", "location",
    "journal", "volume", "issue", "pages", "isbn", "doi", "url", "urldate",
)
ASSET_MIRROR_COLUMNS = (
    "kind", "storage_locator", "provider", "provider_id", "url", "media_type",
    "thumb_page", "thumb_url", "canonical_annotation_set_revision",
    "materialized_pdf_annotation_revision",
)


def _backfill_id(prefix: str, kind: str, work_id: str) -> str:
    return "%s-%s" % (prefix, uuid.uuid5(NS_PRKS_BACKFILL, "%s:%s" % (kind, work_id)).hex.upper())


def backfill_manifestation_id(work_id: str) -> str:
    """`MF-` + uuid5(NS, "manifestation:" + W), upper-case hex (§11.2)."""
    return _backfill_id(MANIFESTATION_PREFIX, "manifestation", work_id)


def backfill_asset_id(work_id: str) -> str:
    """`AS-` + uuid5(NS, "asset:" + W), upper-case hex (§11.2)."""
    return _backfill_id(ASSET_PREFIX, "asset", work_id)


def strip_pointer_columns(row: dict) -> dict:
    for column in WORK_POINTER_COLUMNS:
        row.pop(column, None)
    return row


def _scope_work(scope_id: str, arity: int) -> Optional[Tuple[str, ...]]:
    try:
        parts = json.loads(scope_id)
    except (TypeError, ValueError):
        return None
    if not isinstance(parts, list) or len(parts) != arity:
        return None
    if not all(isinstance(part, str) for part in parts):
        return None
    return tuple(parts)


def works_with_asset_bound_revisions(conn: sqlite3.Connection) -> Set[str]:
    """Work IDs named by an Asset-bound revision scope, tombstones included.

    Parsed with `json.loads`, the same decoder the sync modules use, so an ID
    that SQLite's `json_array` would spell differently cannot be missed.
    """
    found: Set[str] = set()
    rows = conn.execute(
        "SELECT scope_type, scope_id FROM sync_entity_revisions "
        "WHERE scope_type IN (?, ?, ?)",
        (WORK_FIELD_SCOPE_TYPE, WORK_SOURCE_SCOPE_TYPE, PDF_ANNOTATION_SCOPE_TYPE),
    ).fetchall()
    for scope_type, scope_id in rows:
        if scope_type == WORK_SOURCE_SCOPE_TYPE:
            parts = _scope_work(scope_id, 1)
            if parts:
                found.add(parts[0])
        elif scope_type == PDF_ANNOTATION_SCOPE_TYPE:
            parts = _scope_work(scope_id, 2)
            if parts:
                found.add(parts[0])
        else:
            parts = _scope_work(scope_id, 2)
            if parts and parts[1] in ASSET_BOUND_FIELD_SCOPES:
                found.add(parts[0])
    return found


def is_parseable_inferred_video(source_kind, source_url, file_path) -> bool:
    """A legacy inferred-video row whose URL states a video identity.

    The mirror view classifies with `effective_source_kind()` but cannot run
    the URL parser, so a row with no stored kind or provider only counts as
    having video identity when today's parser accepts its URL. An unparseable
    one keeps its URL on the Manifestation and gets no Asset for it (§12.2).
    """
    from backend import db_manager, work_source_sync

    if db_manager.effective_source_kind(source_kind, source_url, file_path) != "video":
        return False
    return work_source_sync.canonical_source({"kind": "video", "url": source_url or ""}) is not None


def origin_asset_required(conn: sqlite3.Connection, work_id: str) -> bool:
    """The full §12.2 Asset-creation predicate for one Work."""
    row = conn.execute(
        "SELECT has_asset_value FROM legacy_work_asset_mirror WHERE work_id = ?", (work_id,)
    ).fetchone()
    if row is None:
        return False
    if row[0]:
        return True
    if conn.execute("SELECT 1 FROM annotations WHERE work_id = ? LIMIT 1", (work_id,)).fetchone():
        return True
    if work_id in works_with_asset_bound_revisions(conn):
        return True
    src = conn.execute(
        "SELECT source_kind, source_url, file_path FROM works WHERE id = ?", (work_id,)
    ).fetchone()
    return bool(src) and is_parseable_inferred_video(src[0], src[1], src[2])


def origin_asset_id(conn: sqlite3.Connection, work_id: str) -> Optional[str]:
    row = conn.execute("SELECT id FROM assets WHERE origin_work_id = ?", (work_id,)).fetchone()
    return None if row is None else row[0]


def ensure_origin_asset(conn: sqlite3.Connection, work_id: str) -> Optional[str]:
    """Create `origin_AS(W)` with its deterministic ID unless it exists.

    Returns the origin Asset's ID, or None when `W` is not a live Work whose
    origin Manifestation still belongs to it. The values come from the same
    `legacy_work_asset_mirror` projection the mirror triggers use, and the
    primary-Asset pointer and the Manifestation `url` are settled here
    explicitly, so the result is identical with or without the triggers
    installed (the migration runs before them). Never deletes or moves
    anything; the caller owns the transaction.
    """
    existing = origin_asset_id(conn, work_id)
    if existing is not None:
        return existing
    asset_id = backfill_asset_id(work_id)
    cur = conn.execute(
        """
        INSERT INTO assets (
            id, manifestation_id, work_id, origin_work_id, kind, role,
            storage_locator, provider, provider_id, url, media_type,
            thumb_page, thumb_url, canonical_annotation_set_revision,
            materialized_pdf_annotation_revision, origin, created_at, updated_at
        )
        SELECT ?, m.id, m.work_id, v.work_id, v.kind, 'document',
               v.storage_locator, v.provider, v.provider_id, v.url, v.media_type,
               v.thumb_page, v.thumb_url, v.canonical_annotation_set_revision,
               v.materialized_pdf_annotation_revision, 'legacy',
               w.created_at, w.updated_at
        FROM legacy_work_asset_mirror v
        JOIN works w ON w.id = v.work_id
        JOIN manifestations m ON m.origin_work_id = v.work_id AND m.work_id = v.work_id
        WHERE v.work_id = ?
        """,
        (asset_id, work_id),
    )
    if cur.rowcount != 1:
        return None
    conn.execute(
        "UPDATE manifestations SET primary_asset_id = ? "
        "WHERE origin_work_id = ? AND work_id = ? AND primary_asset_id IS NULL",
        (asset_id, work_id, work_id),
    )
    # The whole mirrored row, never `url` alone: with the triggers installed a
    # partial refresh would be refused as drift in the columns it skipped.
    columns = MANIFESTATION_MIRROR_COLUMNS
    conn.execute(
        "UPDATE manifestations SET (%s) = (SELECT %s FROM legacy_work_manifestation_mirror v "
        "WHERE v.work_id = ?) WHERE origin_work_id = ? AND work_id = ?"
        % (", ".join(columns), ", ".join("v." + c for c in columns)),
        (work_id, work_id, work_id),
    )
    return asset_id


def integrity_violations(conn: sqlite3.Connection) -> List[Tuple[str, str]]:
    """Final-state rules SQLite cannot enforce at COMMIT (§4.1).

    Returns `(code, entity id)` pairs; empty means consistent. Deliberately
    reports rather than raises, so the migration, tests and later backup
    verification decide what a violation means for them.
    """
    checks: Iterable[Tuple[str, str]] = (
        ("WORK_WITHOUT_PRIMARY_MANIFESTATION",
         "SELECT id FROM works WHERE primary_manifestation_id IS NULL"),
        ("WORK_PRIMARY_NOT_OWNED",
         "SELECT w.id FROM works w WHERE w.primary_manifestation_id IS NOT NULL AND NOT EXISTS "
         "(SELECT 1 FROM manifestations m WHERE m.id = w.primary_manifestation_id AND m.work_id = w.id)"),
        ("WORK_CITATION_NOT_OWNED",
         "SELECT w.id FROM works w WHERE w.citation_manifestation_id IS NOT NULL AND NOT EXISTS "
         "(SELECT 1 FROM manifestations m WHERE m.id = w.citation_manifestation_id AND m.work_id = w.id)"),
        ("MANIFESTATION_ACTIVE_ASSET_WITHOUT_PRIMARY",
         "SELECT m.id FROM manifestations m WHERE m.primary_asset_id IS NULL AND EXISTS "
         "(SELECT 1 FROM assets a WHERE a.manifestation_id = m.id AND a.state = 'active')"),
        ("MANIFESTATION_PRIMARY_ASSET_INVALID",
         "SELECT m.id FROM manifestations m WHERE m.primary_asset_id IS NOT NULL AND NOT EXISTS "
         "(SELECT 1 FROM assets a WHERE a.id = m.primary_asset_id AND a.manifestation_id = m.id "
         "AND a.state = 'active')"),
    )
    found: List[Tuple[str, str]] = []
    for code, sql in checks:
        for row in conn.execute(sql).fetchall():
            found.append((code, row[0]))
    return found


def mirror_drift(conn: sqlite3.Connection) -> List[Tuple[str, str, str]]:
    """Mirror parity: `(entity, work id, column)` where a mirrored copy differs.

    At schema 17 `works` is authoritative, so any difference is a mirror bug.
    Only rows still owned by their origin Work are compared -- a row a later
    command moved away no longer mirrors that Work.
    """
    drift: List[Tuple[str, str, str]] = []
    pairs = (
        ("manifestation", "manifestations", "legacy_work_manifestation_mirror",
         MANIFESTATION_MIRROR_COLUMNS),
        ("asset", "assets", "legacy_work_asset_mirror", ASSET_MIRROR_COLUMNS),
    )
    for label, table, view, columns in pairs:
        select = ", ".join("t.%s IS NOT v.%s" % (c, c) for c in columns)
        sql = (
            "SELECT t.origin_work_id, %s FROM %s t JOIN %s v ON v.work_id = t.origin_work_id "
            "WHERE t.work_id = t.origin_work_id" % (select, table, view)
        )
        for row in conn.execute(sql).fetchall():
            for column, differs in zip(columns, row[1:]):
                if differs:
                    drift.append((label, row[0], column))
    for (work_id,) in conn.execute(
        "SELECT w.id FROM works w WHERE NOT EXISTS "
        "(SELECT 1 FROM manifestations m WHERE m.origin_work_id = w.id)"
    ).fetchall():
        drift.append(("manifestation", work_id, "missing"))
    return drift
