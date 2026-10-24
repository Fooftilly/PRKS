"""Today's Work JSON, read from the PostgreSQL Works tables (L-W-1 #628).

``docs/postgresql-architecture.md`` §5.5: the facade returns exactly the
shapes ``PRKSDatabase`` returns, so the browser and the offline caches see no
change at cutover. On SQLite those shapes come from the legacy ``works``
columns overlaid by ``backend/work_projection.py``; here they come from the
Work, its primary Manifestation and that Manifestation's primary Asset, by the
same rules:

- ``title`` and ``abstract``: the Manifestation's override, else the Work's.
- Bibliographic fields (``doc_type`` … ``urldate``): the primary
  Manifestation's.
- ``source_url``: the stream's URL for a video, else the Manifestation's
  citation URL (a PDF's provenance).
- ``file_path``: ``/api/pdfs/<storage key>`` for a managed file, else ``""``
  (what a Work created without a file stores on SQLite).
- ``source_kind``: ``"video"`` for a stream, ``"pdf"`` for a managed file,
  else ``None``.
- ``source_mime``, ``provider``, ``provider_id``, ``thumb_*`` and the two
  annotation revisions: the primary Asset's, or ``None``/``0`` without one.
- ``text_content`` and ``private_notes``: the Work's notes (``""`` and
  ``None`` when there is none).
- Timestamps in SQLite's text form, UTC: ``YYYY-MM-DD HH:MM:SS``, and
  millisecond precision for ``last_opened_at`` (``work_open_sync``).

- People fields (``roles``, ``primary_author``, ``primary_editor``,
  ``linked_authors``, ``linked_people``): ``credits(primary M)``, the roles
  :func:`backend.domain.people.roles.attach_credits` puts on the core, with
  SQLite's display rule (the trimmed credit name, else the Person's name).

- Folder fields (``folder_id``, and the detail's ``folder_title``): the
  Work's ``folder_files`` row and its Folder (the Organization lane's), in
  the same row; the detail's ``playlist_id`` and ``playlist_title`` likewise
  from its ``playlist_items`` row and Playlist.

Fields owned by lanes that have not landed (annotations, Research
references) are rendered as the empty value SQLite gives a Work
that has none; those lanes add them to this projection. The detail's
``tags`` are the Organization lane's: ``get_work`` reads them in the same
snapshot (``backend.domain.organization.links``).
The facade adds ``file_size_bytes`` and ``html_content``, which come from the
file system and the wiki-link resolver rather than from these tables.
"""

from __future__ import annotations

from typing import Any, LiteralString, Mapping, Optional

# The legacy key lists, kept equal to ``backend/db_manager.py``'s by a test.
WORK_SUMMARY_COLUMNS: tuple[str, ...] = (
    "id", "title", "status", "published_date", "abstract", "file_path", "source_kind",
    "source_url", "source_mime", "thumb_url", "provider", "provider_id", "urldate",
    "thumb_page", "author_text", "year", "publisher", "location", "edition", "journal",
    "volume", "issue", "pages", "isbn", "doi", "doc_type", "last_opened_at", "created_at",
    "updated_at",
)
WORK_BROWSE_COLUMNS: tuple[str, ...] = (
    "id", "title", "status", "doc_type", "file_path", "source_kind", "source_url",
    "thumb_url", "thumb_page", "author_text", "year", "published_date", "provider",
    "provider_id",
)
# ``SELECT * FROM works`` on SQLite, less ``citation_manifestation_id`` (the
# Slice-C projection drops it): the core of ``get_work``.
WORK_DETAIL_COLUMNS: tuple[str, ...] = (
    "id", "title", "status", "published_date", "abstract", "text_content", "file_path",
    "source_kind", "source_url", "source_mime", "thumb_url", "provider", "provider_id",
    "urldate", "thumb_page", "author_text", "year", "publisher", "location", "edition",
    "journal", "volume", "issue", "pages", "isbn", "doi", "doc_type", "private_notes",
    "hide_pdf_link_annotations", "canonical_annotation_set_revision",
    "materialized_pdf_annotation_revision", "last_opened_at", "created_at", "updated_at",
    "primary_manifestation_id",
)
# Added to every Work-shaped row by the Slice-C projection.
PROJECTION_EXTRAS: tuple[str, ...] = (
    "primary_manifestation_id", "primary_asset_id", "manifestation_count", "asset_count",
)
MANIFESTATION_FIELDS: tuple[str, ...] = (
    "doc_type", "year", "published_date", "edition", "publisher", "location", "journal",
    "volume", "issue", "pages", "isbn", "doi", "urldate",
)
ABSTRACT_EXCERPT_LEN = 100

# One row per Work: the Work, its primary Manifestation (always present: the
# pointer is a deferred NOT NULL key), the Manifestation's primary Asset and
# the Work's notes. Every join and count is filtered by the Library. The
# counts are aggregated once per Work and joined, never counted per row
# (scripts/check_correlated_catalog_counts.py); a Work always has at least
# its primary Manifestation, so that join is inner.
WORK_ROWS_SQL: LiteralString = """
SELECT
  w.id,
  w.title AS work_title,
  w.abstract AS work_abstract,
  w.status,
  w.author_text,
  w.hide_pdf_link_annotations,
  to_char(w.last_opened_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS.MS') AS last_opened_at,
  to_char(w.created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') AS created_at,
  to_char(w.updated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') AS updated_at,
  w.primary_manifestation_id,
  m.title AS manifestation_title,
  m.abstract AS manifestation_abstract,
  m.doc_type, m.year, m.published_date, m.edition, m.publisher, m.location, m.journal,
  m.volume, m.issue, m.pages, m.isbn, m.doi, m.urldate,
  m.url AS manifestation_url,
  m.primary_asset_id,
  a.kind AS asset_kind,
  a.storage_locator,
  a.url AS asset_url,
  a.media_type,
  a.provider,
  a.provider_id,
  a.thumb_page,
  a.thumb_url,
  a.canonical_annotation_set_revision,
  a.materialized_pdf_annotation_revision,
  mc.n AS manifestation_count,
  COALESCE(ac.n, 0) AS asset_count,
  rn.body AS research_note,
  pn.body AS private_note,
  ff.folder_id,
  fo.title AS folder_title,
  pi.playlist_id,
  pi.position AS playlist_position,
  pl.title AS playlist_title
FROM prks.works w
JOIN prks.manifestations m
  ON m.library_id = w.library_id AND m.id = w.primary_manifestation_id AND m.work_id = w.id
LEFT JOIN prks.assets a
  ON a.library_id = m.library_id AND a.id = m.primary_asset_id AND a.manifestation_id = m.id
JOIN (SELECT work_id, count(*) AS n FROM prks.manifestations
      WHERE library_id = %(library_id)s GROUP BY work_id) mc
  ON mc.work_id = w.id
LEFT JOIN (SELECT work_id, count(*) AS n FROM prks.assets
           WHERE library_id = %(library_id)s AND state = 'active' GROUP BY work_id) ac
  ON ac.work_id = w.id
LEFT JOIN prks.work_notes rn
  ON rn.library_id = w.library_id AND rn.work_id = w.id AND rn.kind = 'research'
LEFT JOIN prks.work_notes pn
  ON pn.library_id = w.library_id AND pn.work_id = w.id AND pn.kind = 'private'
LEFT JOIN prks.folder_files ff
  ON ff.library_id = w.library_id AND ff.work_id = w.id
LEFT JOIN prks.folders fo
  ON fo.library_id = ff.library_id AND fo.id = ff.folder_id
LEFT JOIN prks.playlist_items pi
  ON pi.library_id = w.library_id AND pi.work_id = w.id
LEFT JOIN prks.playlists pl
  ON pl.library_id = pi.library_id AND pl.id = pi.playlist_id
WHERE w.library_id = %(library_id)s
"""

# Orders. SQLite compares ids and NOCASE titles byte-wise after folding ASCII
# letters only; COLLATE "C" and an ASCII-only translate() give the same order
# whatever the database's collation. `created_at` and `last_opened_at` order
# at the precision they are rendered in, so equal rendered values fall back to
# the id exactly as they do on SQLite.
BY_CREATED_SQL: LiteralString = (
    " ORDER BY date_trunc('second', w.created_at) DESC, w.id COLLATE \"C\" ASC"
)
BY_OPENED_SQL: LiteralString = (
    " AND w.last_opened_at IS NOT NULL"
    " ORDER BY date_trunc('milliseconds', w.last_opened_at) DESC, w.id COLLATE \"C\" ASC"
)
BY_TITLE_SQL: LiteralString = (
    " ORDER BY translate(COALESCE(m.title, w.title), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ',"
    " 'abcdefghijklmnopqrstuvwxyz') COLLATE \"C\" ASC, w.id COLLATE \"C\" ASC"
)
BY_ID_SQL: LiteralString = " AND w.id = %(work_id)s"
BY_IDS_SQL: LiteralString = " AND w.id = ANY(%(work_ids)s)"
IN_FOLDER_SQL: LiteralString = " AND ff.folder_id = %(folder_id)s"
# A Playlist's items in its order; SQLite breaks a tie of positions by
# creation time, and an equal rendered time falls back to the id.
IN_PLAYLIST_SQL: LiteralString = (
    " AND pi.playlist_id = %(playlist_id)s"
    " ORDER BY pi.position, date_trunc('second', w.created_at), w.id COLLATE \"C\""
)
LIMIT_SQL: LiteralString = " LIMIT %(limit)s"


def _asset_value(row: Mapping[str, Any], field: str, missing: Any) -> Any:
    return row[field] if row["asset_kind"] is not None else missing


def work_core(row: Mapping[str, Any]) -> dict[str, Any]:
    """Every legacy field of one Work row; the shapes below pick from it."""
    kind = row["asset_kind"]
    managed = kind == "managed_file"
    stream = kind == "external_stream"
    title = row["manifestation_title"]
    abstract = row["manifestation_abstract"]
    research = row["research_note"]
    core: dict[str, Any] = {
        "id": row["id"],
        "title": title if title is not None else row["work_title"],
        "status": row["status"],
        "abstract": abstract if abstract is not None else row["work_abstract"],
        "author_text": row["author_text"],
        "text_content": research if research is not None else "",
        "private_notes": row["private_note"],
        "file_path": "/api/pdfs/" + str(row["storage_locator"]) if managed else "",
        "source_kind": "video" if stream else "pdf" if managed else None,
        "source_url": row["asset_url"] if stream else row["manifestation_url"],
        "source_mime": _asset_value(row, "media_type", None),
        "provider": _asset_value(row, "provider", None),
        "provider_id": _asset_value(row, "provider_id", None),
        "thumb_page": _asset_value(row, "thumb_page", None),
        "thumb_url": _asset_value(row, "thumb_url", None),
        "canonical_annotation_set_revision": int(
            _asset_value(row, "canonical_annotation_set_revision", 0)
        ),
        "materialized_pdf_annotation_revision": int(
            _asset_value(row, "materialized_pdf_annotation_revision", 0)
        ),
        "hide_pdf_link_annotations": 1 if row["hide_pdf_link_annotations"] else 0,
        "last_opened_at": row["last_opened_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "primary_manifestation_id": row["primary_manifestation_id"],
        "primary_asset_id": row["primary_asset_id"],
        "manifestation_count": int(row["manifestation_count"]),
        "asset_count": int(row["asset_count"]),
        "folder_id": row["folder_id"],
        "folder_title": row["folder_title"],
        "playlist_id": row["playlist_id"],
        "playlist_title": row["playlist_title"],
        "playlist_position": row["playlist_position"],
    }
    for field in MANIFESTATION_FIELDS:
        core[field] = row[field]
    return core


def _pick(core: Mapping[str, Any], columns: tuple[str, ...]) -> dict[str, Any]:
    return {column: core[column] for column in columns}


def _trim(text: Any) -> str:
    # SQLite's TRIM: spaces only.
    return str(text or "").strip(" ")


def canonical_name(person: Mapping[str, Any]) -> str:
    return _trim(str(person["first_name"] or "") + " " + str(person["last_name"] or ""))


def display_name(credit: Mapping[str, Any]) -> str:
    """The name a role shows: its trimmed credit name, else the Person's name."""
    return _trim(credit["credit_name"]) or canonical_name(credit["person"])


def _credits(core: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return list(core.get("credits") or [])


def _person_extras(core: Mapping[str, Any]) -> dict[str, Any]:
    """The summary's people columns, from ``credits(primary M)`` in role order."""
    credits = _credits(core)
    authors = [display_name(c) for c in credits if c["role_type"] == "Author"]
    editors = [display_name(c) for c in credits if c["role_type"] == "Editor"]
    return {
        "primary_author": authors[0] if authors else None,
        "primary_editor": editors[0] if editors else None,
        "linked_authors": ", ".join(authors) if authors else None,
        "linked_people": [{
            "person_id": c["person_id"],
            "role_type": c["role_type"],
            "order_index": c["order_index"],
            "canonical_name": canonical_name(c["person"]),
            "credit_name": _trim(c["credit_name"]),
            "display_name": display_name(c),
        } for c in credits],
    }


def _with_extras(out: dict[str, Any], core: Mapping[str, Any]) -> dict[str, Any]:
    out.update(_pick(core, PROJECTION_EXTRAS))
    out.update(_person_extras(core))
    return out


def role_row(credit: Mapping[str, Any]) -> dict[str, Any]:
    """SQLite's ``SELECT p.*, r.role_type, r.order_index, r.credit_name, r.manifestation_id``."""
    return {**credit["person"], "role_type": credit["role_type"], "order_index": credit["order_index"],
            "credit_name": credit["credit_name"], "manifestation_id": credit["manifestation_id"]}


def work_detail(core: Mapping[str, Any]) -> dict[str, Any]:
    """``get_work`` less ``file_size_bytes`` and ``html_content``."""
    out = _pick(core, WORK_DETAIL_COLUMNS)
    out.update(_pick(core, PROJECTION_EXTRAS))
    out.update({
        "roles": [role_row(credit) for credit in _credits(core)],
        "arguments": [],
        "research_refs": {"concepts": [], "arguments": []},
        "annotations": "[]",
        "tags": [],
        "playlist_id": core["playlist_id"],
        "playlist_title": core["playlist_title"],
        "folder_id": core["folder_id"],
        "folder_title": core["folder_title"],
    })
    return out


def work_summary(core: Mapping[str, Any], *, with_folder: bool = True) -> dict[str, Any]:
    """The list/search summary row (``get_all_works`` and friends)."""
    out = _pick(core, WORK_SUMMARY_COLUMNS)
    if with_folder:
        out["folder_id"] = core["folder_id"]
    return _with_extras(out, core)


def work_browse(core: Mapping[str, Any], *, excerpt: bool = True,
                extra: tuple[str, ...] = ()) -> dict[str, Any]:
    """The compact browse row, with the abstract excerpt or the named extras."""
    out = _pick(core, WORK_BROWSE_COLUMNS)
    if excerpt:
        out["abstract_excerpt"] = str(core["abstract"] or "")[:ABSTRACT_EXCERPT_LEN]
    for column in extra:
        out[column] = core[column]
    return _with_extras(out, core)


def person_work(core: Mapping[str, Any], credit: Mapping[str, Any]) -> dict[str, Any]:
    """One entry of ``get_person``'s ``works``: the Work row, its people columns and the role."""
    out = _pick(core, WORK_DETAIL_COLUMNS)
    _with_extras(out, core)
    out.update({"role_type": credit["role_type"], "order_index": credit["order_index"],
                "credit_name": credit["credit_name"]})
    return out


def thumbnail_fields(core: Optional[Mapping[str, Any]]) -> Optional[dict[str, Any]]:
    if core is None:
        return None
    return {"file_path": core["file_path"], "thumb_page": core["thumb_page"],
            "primary_asset_id": core["primary_asset_id"]}
