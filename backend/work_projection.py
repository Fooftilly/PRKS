"""Work compatibility projection for #60 Slice C.

Schema 17 still writes through ``works``. Readers go through this module so
later authority slices can change one boundary instead of every endpoint.
"""
from __future__ import annotations

import json
import sqlite3
from typing import List, Optional

ABSTRACT_EXCERPT_LEN = 100
M_FIELDS = (
    "doc_type", "year", "published_date", "edition", "publisher", "location",
    "journal", "volume", "issue", "pages", "isbn", "doi", "urldate",
)


def _payload(values) -> str:
    return json.dumps(list(dict.fromkeys(str(v) for v in values if v)),
                      ensure_ascii=True, separators=(",", ":"))


def _rows_by_id(conn, table: str, ids) -> dict:
    payload = _payload(ids)
    if payload == "[]":
        return {}
    return {
        str(row["id"]): dict(row)
        for row in conn.execute(
            f"SELECT * FROM {table} WHERE id IN "
            "(SELECT value FROM json_each(?))", (payload,)
        ).fetchall()
    }


def _counts(conn, table: str, work_ids, *, active=False) -> dict:
    payload = _payload(work_ids)
    if payload == "[]":
        return {}
    where = " AND state = 'active'" if active else ""
    return {
        str(row[0]): int(row[1])
        for row in conn.execute(
            f"SELECT work_id, COUNT(*) FROM {table} "
            "WHERE work_id IN (SELECT value FROM json_each(?))"
            f"{where} GROUP BY work_id", (payload,)
        ).fetchall()
    }


def _context_for_works(conn, work_ids) -> dict:
    payload = _payload(work_ids)
    if payload == "[]":
        return {}
    works = {
        str(row["id"]): dict(row)
        for row in conn.execute(
            "SELECT id, abstract, primary_manifestation_id FROM works "
            "WHERE id IN (SELECT value FROM json_each(?))", (payload,)
        ).fetchall()
    }
    manifestations = _rows_by_id(
        conn, "manifestations",
        [row.get("primary_manifestation_id") for row in works.values()],
    )
    assets = _rows_by_id(
        conn, "assets",
        [m.get("primary_asset_id") for m in manifestations.values()],
    )
    m_counts = _counts(conn, "manifestations", works)
    a_counts = _counts(conn, "assets", works, active=True)
    out = {}
    for work_id, work in works.items():
        m = manifestations.get(str(work.get("primary_manifestation_id") or ""))
        a = assets.get(str(m.get("primary_asset_id") or "")) if m else None
        out[work_id] = (work, m, a, m_counts.get(work_id, 0), a_counts.get(work_id, 0))
    return out


def _apply(row: dict, ctx) -> None:
    row.pop("citation_manifestation_id", None)
    if ctx is None:
        return
    work, manifestation, asset, m_count, a_count = ctx
    row["primary_manifestation_id"] = work.get("primary_manifestation_id")
    row["primary_asset_id"] = manifestation.get("primary_asset_id") if manifestation else None
    row["manifestation_count"] = m_count
    row["asset_count"] = a_count
    if not manifestation:
        return

    if "title" in row and manifestation.get("title") is not None:
        row["title"] = manifestation["title"]
    effective_abstract = (
        manifestation.get("abstract")
        if manifestation.get("abstract") is not None
        else work.get("abstract")
    )
    if "abstract" in row:
        row["abstract"] = effective_abstract
    if "abstract_excerpt" in row:
        row["abstract_excerpt"] = str(effective_abstract or "")[:ABSTRACT_EXCERPT_LEN]
    for field in M_FIELDS:
        if field in row:
            row[field] = manifestation.get(field)

    kind = asset.get("kind") if asset else None
    if "source_url" in row:
        row["source_url"] = (
            asset.get("url") if kind == "external_stream" and asset
            else manifestation.get("url")
        )
    for field in ("provider", "provider_id", "thumb_page", "thumb_url",
                  "canonical_annotation_set_revision",
                  "materialized_pdf_annotation_revision"):
        if field in row and asset is not None:
            row[field] = asset.get(field)

    # Slice A mirrors can contain historical spellings that must remain
    # byte-identical while the origin Manifestation is primary. A future
    # non-origin primary has no Work-column spelling to preserve, so derive
    # these three fields from its primary Asset.
    if manifestation.get("origin_work_id") != str(row.get("id") or ""):
        locator = asset.get("storage_locator") if asset else None
        if "file_path" in row:
            row["file_path"] = (
                "/api/pdfs/" + str(locator)
                if kind == "managed_file" and locator else None
            )
        if "source_kind" in row:
            row["source_kind"] = (
                "video" if kind == "external_stream"
                else "pdf" if kind == "managed_file" and locator
                else None
            )
        if "source_mime" in row:
            row["source_mime"] = asset.get("media_type") if asset else None
        if asset is None:
            for field in ("provider", "provider_id", "thumb_page", "thumb_url"):
                if field in row:
                    row[field] = None


def legacy_work_summary(conn: sqlite3.Connection, rows: Optional[List[dict]]) -> List[dict]:
    """Overlay primary Manifestation/Asset fields on Work-shaped list rows."""
    if not rows:
        return rows or []
    contexts = _context_for_works(conn, [row.get("id") for row in rows])
    for row in rows:
        _apply(row, contexts.get(str(row.get("id") or "")))
    return rows


def legacy_work(conn: sqlite3.Connection, work_id: str) -> Optional[dict]:
    """Return the legacy Work detail core through the Slice-C projection."""
    row = conn.execute("SELECT * FROM works WHERE id = ?", (work_id,)).fetchone()
    if row is None:
        return None
    out = dict(row)
    legacy_work_summary(conn, [out])
    return out


def citation_target(conn: sqlite3.Connection, work_id: str) -> Optional[str]:
    row = conn.execute(
        "SELECT COALESCE(citation_manifestation_id, primary_manifestation_id) "
        "FROM works WHERE id = ?", (work_id,)
    ).fetchone()
    return None if row is None else row[0]


def citation_record(conn: sqlite3.Connection, manifestation_id: str) -> Optional[dict]:
    """Return a Work-shaped citation record for an explicit Manifestation."""
    m = conn.execute(
        "SELECT * FROM manifestations WHERE id = ?", (manifestation_id,)
    ).fetchone()
    if m is None:
        return None
    work = conn.execute(
        "SELECT * FROM works WHERE id = ?", (m["work_id"],)
    ).fetchone()
    if work is None:
        return None
    out = dict(work)
    a = None
    if m["primary_asset_id"]:
        a = conn.execute(
            "SELECT * FROM assets WHERE id = ? AND manifestation_id = ?",
            (m["primary_asset_id"], manifestation_id),
        ).fetchone()
    m_count = conn.execute(
        "SELECT COUNT(*) FROM manifestations WHERE work_id = ?", (m["work_id"],)
    ).fetchone()[0]
    a_count = conn.execute(
        "SELECT COUNT(*) FROM assets WHERE work_id = ? AND state = 'active'",
        (m["work_id"],),
    ).fetchone()[0]
    _apply(out, (
        {"id": m["work_id"], "abstract": work["abstract"],
         "primary_manifestation_id": manifestation_id},
        dict(m), None if a is None else dict(a), m_count, a_count,
    ))
    return out
