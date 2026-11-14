"""The Work reads for one actor: what ``backend/api/works.py`` calls (PG-C3 #642).

The Work catalog and its browse projections, the Work detail, the primary
Asset's thumbnail fields, the metadata, source and notes revision states, the
BibTeX entry and the managed-PDF ownership check. Each read runs in one
:func:`~backend.domain.identity.library_scope.library_query` snapshot,
authorized for ``READ_LIBRARY``. A list's rows are finished outside the
transaction with each managed PDF's size on disk
(``finish_work_summary_rows``), as the facade finished them.
"""

from __future__ import annotations

import json
from typing import Any, List, Optional

from backend.db.database import Database
from backend.domain.identity.library_scope import LibraryScope, library_query
from backend.domain.identity.request_context import RequestContext
from backend.domain.works import projection, queries
from backend.domain.works.rules import finish_work_summary_rows

RECENT_LIMIT = 30
RECENTLY_ADDED_LIMIT = 50


def _finish(rows: List[dict], pdfs_dir: str) -> List[dict]:
    finish_work_summary_rows(rows, pdfs_dir)
    return rows


def catalog(db: Database, ctx: RequestContext, pdfs_dir: str) -> List[dict]:
    """``GET /api/works``: every Work summary, newest first."""
    cores = library_query(db, ctx, queries.works_by_created)
    return _finish([projection.work_summary(core) for core in cores], pdfs_dir)


def browse_catalog(db: Database, ctx: RequestContext, pdfs_dir: str) -> List[dict]:
    """``GET /api/works?projection=browse``: the compact browse rows, by title."""
    cores = library_query(db, ctx, queries.works_by_title)
    return _finish([projection.work_browse(core) for core in cores], pdfs_dir)


def recent_browse(db: Database, ctx: RequestContext, pdfs_dir: str, limit: int = RECENT_LIMIT) -> List[dict]:
    """``GET /api/recent``: the most recently opened Works."""
    cores = library_query(db, ctx, lambda scope: queries.works_by_opened(scope, limit))
    return _finish([projection.work_browse(core, excerpt=False, extra=("last_opened_at",))
                    for core in cores], pdfs_dir)


def recently_added_browse(db: Database, ctx: RequestContext, pdfs_dir: str,
                          limit: int = RECENTLY_ADDED_LIMIT) -> List[dict]:
    """``GET /api/recently-added``: the most recently created Works."""
    cores = library_query(db, ctx, lambda scope: queries.works_by_created(scope, limit))
    return _finish([projection.work_browse(core, excerpt=False, extra=("created_at", "publisher", "folder_id"))
                    for core in cores], pdfs_dir)


def detail(db: Database, ctx: RequestContext, pdfs_dir: str, work_id: str) -> Optional[dict]:
    """``GET /api/works/:id`` without its Research refs; ``None`` for a missing Work.

    The Work, its Tags, its annotations and the Research Note's wiki links
    resolved to HTML come from one snapshot.
    """
    from backend.domain.annotations import store as annotation_store
    from backend.domain.organization import links
    from backend.domain.works import wiki_links

    def read(scope: LibraryScope) -> Optional[tuple[dict, List[dict], Optional[str], List[dict]]]:
        found = queries.work(scope, work_id)
        if found is None:
            return None
        text = found.get("text_content")
        html_content = wiki_links.render(scope, text) if text else None
        return (found, links.tags_of(scope, links.WORK, work_id), html_content,
                annotation_store.work_annotations(scope, work_id))

    taken = library_query(db, ctx, read)
    if taken is None:
        return None
    core, tags, html_content, annotations = taken
    work = projection.work_detail(core)
    work["tags"] = tags
    work["annotations"] = json.dumps(annotations)
    if html_content is not None:
        work["html_content"] = html_content
    return _finish([work], pdfs_dir)[0]


def thumbnail_fields(db: Database, ctx: RequestContext, work_id: str) -> Optional[dict]:
    """The primary Asset's ``file_path`` and thumbnail fields; ``None`` for a missing Work."""
    return projection.thumbnail_fields(library_query(db, ctx, lambda scope: queries.work(scope, work_id)))


def metadata_state(db: Database, ctx: RequestContext, work_id: str) -> Optional[dict]:
    from backend.domain.works import metadata

    return library_query(db, ctx, lambda scope: metadata.field_state(scope, work_id))


def source_state(db: Database, ctx: RequestContext, work_id: str) -> Optional[dict[str, Any] | str]:
    """The source's revision, or ``INVALID_SOURCE_STATE``; ``None`` when the Work is missing."""
    from backend.domain.works import source

    return library_query(db, ctx, lambda scope: source.source_state(scope, work_id))


def notes_state(db: Database, ctx: RequestContext, work_id: str) -> Optional[dict]:
    from backend.domain.works import notes

    return library_query(db, ctx, lambda scope: notes.notes_state(scope, work_id))


def bibtex(db: Database, ctx: RequestContext, work_id: str) -> str:
    """The Work's BibTeX entry, read in one snapshot; ``""`` for a Work not in this Library.

    The citation record, the citation Manifestation's credits and the
    Library's export profile come from ``backend.domain.works.citation``;
    ``bibtex_format.format_bibtex`` turns them into the entry, so equal data
    exports byte-identical text. An id PostgreSQL text cannot hold (NUL)
    gives ``""``.
    """
    from backend.domain.works import citation
    from backend.domain.works.bibtex_format import format_bibtex

    if "\x00" in work_id:
        return ""
    inputs = library_query(db, ctx, lambda scope: citation.bibtex_inputs(scope, work_id))
    if inputs is None:
        return ""
    record, roles, profile = inputs
    # The projection's "video" means the target's primary Asset is a stream.
    return format_bibtex(record, roles, profile, video=record["source_kind"] == "video")


def holds_managed_pdf(db: Database, ctx: RequestContext, name: str) -> bool:
    """Whether the acting Library has an Asset whose bytes are the managed PDF ``name``.

    One data root serves every Library, so ``/api/pdfs/<name>`` serves a file
    only to a member of a Library that references it: a name from another
    Library reads as missing.
    """

    def read(scope: LibraryScope) -> bool:
        row = scope.execute(
            "SELECT 1 FROM prks.assets WHERE library_id = %(library_id)s"
            " AND (storage_locator = %(name)s OR source_locator = %(name)s) LIMIT 1",
            {"name": name},
        ).fetchone()
        return row is not None

    return library_query(db, ctx, read)
