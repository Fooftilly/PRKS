"""The Work operations for one actor: what ``backend/api/works.py`` calls (PG-C3 #642).

Reads: the Work catalog and its browse projections, the Work detail, the
primary Asset's thumbnail fields, the metadata, source and notes revision
states, the BibTeX entry and the managed-PDF ownership check. Each runs in one
:func:`~backend.domain.identity.library_scope.library_query` snapshot,
authorized for ``READ_LIBRARY``.

Writes: create (from an existing managed PDF, an upload, a stream or no
file), the metadata PATCH, the open mark, delete and the bulk actions. Each is
one :func:`~backend.domain.identity.library_scope.library_command`
transaction needing ``EDIT_LIBRARY``, fenced by maintenance and the epoch;
they keep SQLite's ``PRKSDatabase`` rules, refusals and messages.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, TypeVar

from backend.db.database import Database, Isolation
from backend.domain.identity.authorization import Capability
from backend.domain.identity.library_scope import LibraryScope, library_command, library_query
from backend.domain.identity.request_context import RequestContext
from backend.domain.objects.content_url import is_current_version
from backend.domain.works import projection, queries
from backend.storage.objects import StorageBackend, Writer

T = TypeVar("T")

RECENT_LIMIT = 30
RECENTLY_ADDED_LIMIT = 50


def catalog(db: Database, ctx: RequestContext) -> List[dict]:
    """``GET /api/works``: every Work summary, newest first."""
    cores = library_query(db, ctx, queries.works_by_created)
    return [projection.work_summary(core) for core in cores]


def browse_catalog(db: Database, ctx: RequestContext) -> List[dict]:
    """``GET /api/works?projection=browse``: the compact browse rows, by title."""
    cores = library_query(db, ctx, queries.works_by_title)
    return [projection.work_browse(core) for core in cores]


def recent_browse(db: Database, ctx: RequestContext, limit: int = RECENT_LIMIT) -> List[dict]:
    """``GET /api/recent``: the most recently opened Works."""
    cores = library_query(db, ctx, lambda scope: queries.works_by_opened(scope, limit))
    return [projection.work_browse(core, excerpt=False, extra=("last_opened_at",)) for core in cores]


def recently_added_browse(db: Database, ctx: RequestContext, limit: int = RECENTLY_ADDED_LIMIT) -> List[dict]:
    """``GET /api/recently-added``: the most recently created Works."""
    cores = library_query(db, ctx, lambda scope: queries.works_by_created(scope, limit))
    return [projection.work_browse(core, excerpt=False, extra=("created_at", "publisher", "folder_id"))
            for core in cores]


def detail(db: Database, ctx: RequestContext, work_id: str) -> Optional[dict]:
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
    return work


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


#: The formats ``POST /api/citations`` answers in.
CITATION_FORMATS = ("csl-json", "bibtex")


_BIBTEX_KEY_RE = re.compile(r"^(@[^{]*\{)([^,\n]*)(,)")


def _unique_bibtex_keys(entries: list[str]) -> list[str]:
    """Entries whose citation keys are unique in one export.

    Each entry's key comes from its first author and year (``Doe2024``), so
    two Works can share one, and BibTeX drops the later entry. The first
    keeps its key; later ones take the first free suffix ``a``, ``b``, …
    ``z``, ``aa``, …, as BibTeX styles do, checked against every key taken.
    """
    keys = [m.group(2) if (m := _BIBTEX_KEY_RE.match(entry)) else "" for entry in entries]
    taken = set(keys)
    seen: set[str] = set()
    out: list[str] = []
    for entry, key in zip(entries, keys):
        if not key or key not in seen:
            seen.add(key)
            out.append(entry)
            continue
        n = 0
        while True:
            n += 1
            suffix, value = "", n
            while value:
                value, rest = divmod(value - 1, 26)
                suffix = chr(ord("a") + rest) + suffix
            if key + suffix not in taken:
                break
        unique = key + suffix
        taken.add(unique)
        seen.add(unique)
        match = _BIBTEX_KEY_RE.match(entry)
        assert match is not None  # the key was read from this entry
        out.append(match.group(1) + unique + entry[match.end(2):])
    return out


def citations(db: Database, ctx: RequestContext, data: Any) -> dict:
    """``POST /api/citations``: the selected Works' citations in one format, read in one snapshot.

    ``data`` is ``{"work_ids": [...], "format": "csl-json" | "bibtex"}``.
    The ids are validated as a bulk selection (at most
    ``PRKS_BULK_WORK_MAX``, deduplicated, in request order) before the
    database is read. ``csl-json`` answers ``items``, one CSL-JSON item per
    Work (``csl_format.format_csl``); ``bibtex`` answers ``text``, the
    entries ``bibtex`` gives one Work each, separated by a blank line. Ids
    this Library has no Work for are listed in ``missing`` and left out, so
    a selection made before another tab deleted a Work still exports the
    rest. Raises ``BulkWorkError`` (400) for a malformed body.
    """
    from backend.domain.works import citation
    from backend.domain.works.bibtex_format import format_bibtex
    from backend.domain.works.csl_format import format_csl
    from backend.domain.works.rules import BulkWorkError, _normalize_bulk_ids

    if not isinstance(data, dict):
        raise BulkWorkError("JSON object body required")
    fmt = data.get("format")
    if fmt not in CITATION_FORMATS:
        raise BulkWorkError("Unknown citation format.")
    work_ids = _normalize_bulk_ids(data.get("work_ids"), kind="work")

    def read(scope: LibraryScope) -> tuple[list[tuple[str, Any]], dict[str, bool]]:
        found = [(work_id, None if "\x00" in work_id else citation.citation_inputs(scope, work_id))
                 for work_id in work_ids]
        return found, (citation.bibtex_profile(scope) if fmt == "bibtex" else {})

    found, profile = library_query(db, ctx, read)
    missing = [work_id for work_id, inputs in found if inputs is None]
    present = [(work_id, inputs) for work_id, inputs in found if inputs is not None]
    if fmt == "csl-json":
        items = [format_csl(work_id, record, roles, video=record["source_kind"] == "video")
                 for work_id, (record, roles) in present]
        return {"format": fmt, "items": items, "missing": missing}
    entries = _unique_bibtex_keys([
        format_bibtex(record, roles, profile, video=record["source_kind"] == "video")
        for _work_id, (record, roles) in present])
    return {"format": fmt, "text": "\n\n".join(entries), "missing": missing}


def asset_object(db: Database, ctx: RequestContext, asset_id: str, version: Optional[str] = None) -> Optional[str]:
    """The storage key name the acting Library's managed Asset ``asset_id`` names now.

    One data root serves every Library, so the content route serves bytes
    only through an Asset of the acting Library: an id from another Library,
    a stream or an unknown id reads as missing (``None``). So does a
    ``version`` (a content URL's ``v``) that no longer names the Asset's bytes.
    """

    def read(scope: LibraryScope) -> Optional[str]:
        row = scope.execute(
            "SELECT storage_locator, content_sha256 FROM prks.assets WHERE library_id = %(library_id)s"
            " AND id = %(asset_id)s AND kind = 'managed_file'",
            {"asset_id": asset_id},
        ).fetchone()
        if row is None or not is_current_version(version, row["content_sha256"]):
            return None
        return str(row["storage_locator"])

    return library_query(db, ctx, read)


# -- writes -----------------------------------------------------------------------

#: The fields of a new Work and their defaults (SQLite's ``add_work`` arguments).
NEW_WORK_DEFAULTS: dict[str, Any] = {
    "title": "", "status": "Not Started", "abstract": "", "text_content": "", "published_date": "",
    "stored_key": "", "author_text": "", "year": "", "publisher": "", "location": "", "edition": "",
    "journal": "", "volume": "", "issue": "", "pages": "", "isbn": "", "doi": "", "doc_type": "article",
    "source_kind": "", "source_url": "", "source_mime": "", "thumb_url": "", "provider": "",
    "provider_id": "", "urldate": "", "thumb_page": None, "private_notes": "",
}

_HASH_CHUNK = 1024 * 1024


def new_work_fields(**fields: Any) -> dict[str, Any]:
    """``fields`` over :data:`NEW_WORK_DEFAULTS`; an unknown name or a missing title is ``TypeError``."""
    unknown = sorted(set(fields) - set(NEW_WORK_DEFAULTS))
    if unknown:
        raise TypeError("unknown Work fields: " + ", ".join(unknown))
    if "title" not in fields:
        raise TypeError("a new Work needs a title")
    return {**NEW_WORK_DEFAULTS, **fields}


def _command(db: Database, ctx: RequestContext, fn: Callable[[LibraryScope], T], *,
             serializable: bool = False) -> T:
    isolation = Isolation.SERIALIZABLE if serializable else Isolation.READ_COMMITTED
    return library_command(db, ctx, Capability.EDIT_LIBRARY, fn, isolation=isolation)


def _authorize_edit(db: Database, ctx: RequestContext) -> None:
    """Refuse a caller who may not edit before any shared file is touched.

    One data root serves every Library, so a managed PDF is inspected and
    hashed only for an actor who holds ``EDIT_LIBRARY`` now: otherwise the
    refusal would say whether a key exists, after reading its bytes. The
    command re-authorizes and checks the epoch in its own transaction.
    """
    library_query(db, ctx, lambda scope: None, capability=Capability.EDIT_LIBRARY)


def create(db: Database, ctx: RequestContext, objects: StorageBackend,
           fields: dict[str, Any], upload: Optional[tuple[str, Writer]] = None) -> str:
    """Create a Work; its id.

    ``fields`` are :func:`new_work_fields`. The source is decided by SQLite's
    rules (``_canonical_new_source``): a contradictory or unsupported source
    raises ``ValueError``. Then:

    - a ``stored_key`` is the name of an existing object in ``objects``
      asset namespace (an Asset of this Library already names it: the route
      finds it from the Asset's content URL), whose SHA-256 and size the new
      Asset records; an invalid or missing key raises ``ValueError``;
    - ``upload`` (file name, writer) is a new PDF: ``objects.ingest.store_pdf``
      writes the source key (and a linearized working key) and the create
      adopts them; with ``upload``, ``stored_key`` must be empty. Bytes a
      refused or failed create leaves behind are the orphan sweep's (§11.2);
    - a video becomes an ``external_stream`` Asset, and a video with a file
      is refused (a stream stores no bytes);
    - source presentation fields (``source_mime``, ``thumb_*``) belong to the
      Asset, so a Work created with no file and no stream does not keep
      them, and a ``"pdf"`` kind without a file reads back as no source.

    The caller's edit right is checked before any file is read or written.
    """
    from backend.domain.objects import ingest
    from backend.domain.works import commands
    from backend.domain.works.rules import _canonical_new_source, normalize_doc_type
    from backend.domain.entity_ids import generate

    stored_key = fields["stored_key"]
    if upload is not None and stored_key:
        raise TypeError("the uploaded PDF is the Work's file")
    _authorize_edit(db, ctx)
    # An upload has a file before it has a key: classified as one.
    kind, url, provider, provider_id = _canonical_new_source(
        fields["source_kind"], fields["source_url"], fields["provider"], fields["provider_id"],
        upload[0] if upload is not None else stored_key)
    managed: Optional[commands.ManagedFile] = None
    if upload is not None:
        if kind == "video":
            raise ValueError("a video Work has no managed file")
        managed = ingest.store_pdf(objects, *upload).managed_file()
    asset = _new_asset(objects, kind, stored_key, url, provider, provider_id, fields["source_mime"],
                       fields["thumb_url"], fields["thumb_page"], managed)
    bibliographic = {name: fields[name] for name in (
        "year", "published_date", "edition", "publisher", "location", "journal", "volume",
        "issue", "pages", "isbn", "doi")}
    bibliographic["doc_type"] = normalize_doc_type(fields["doc_type"])
    bibliographic["urldate"] = (fields["urldate"] or "").strip() or None
    stream = isinstance(asset.source, commands.Stream) if asset is not None else False
    new = commands.NewWork(
        work_id=generate("W"),
        manifestation_id=generate("MF"),
        title=fields["title"],
        status=fields["status"],
        abstract=fields["abstract"],
        author_text=fields["author_text"],
        bibliographic=bibliographic,
        citation_url=None if stream else (url or "").strip() or None,
        asset_id=generate("AS") if asset is not None else None,
        asset=asset,
        research_note=fields["text_content"],
        private_note=(fields["private_notes"] or "").strip() or None,
    )
    adopting = objects if asset is not None and isinstance(asset.source, commands.ManagedFile) else None
    created = _command(db, ctx, lambda scope: commands.create_work(scope, new, objects=adopting), serializable=True)
    if created is None:  # pragma: no cover - only an ``if_absent`` create answers None
        raise RuntimeError("the Work create returned no id")
    return created


def _new_asset(objects: StorageBackend, kind: Optional[str], stored_key: str, url: Optional[str],
               provider: Optional[str], provider_id: Optional[str], source_mime: str,
               thumb_url: str, thumb_page: Any, managed: Any = None) -> Any:
    from backend.domain.works import commands

    presentation: dict[str, Any] = {
        "media_type": (source_mime or "").strip() or None,
        "thumb_url": (thumb_url or "").strip() or None,
        "thumb_page": _thumb_page(thumb_page),
    }
    has_file = managed is not None or bool((stored_key or "").strip())
    if kind == "video":
        if has_file:
            raise ValueError("a video Work has no managed file")
        return commands.NewAsset(
            source=commands.Stream(url=str(url), provider=provider or None, provider_id=provider_id or None),
            **presentation,
        )
    if not has_file:
        return None
    return commands.NewAsset(source=managed or _managed_file(objects, stored_key), **presentation)


def _managed_file(objects: StorageBackend, name: str) -> Any:
    from backend.domain.works import commands
    from backend.storage.objects import (
        NAMESPACE_ASSET_OBJECTS, InvalidStorageKey, ObjectIntegrityError, ObjectNotFound, StorageKey,
    )

    try:
        key = StorageKey(NAMESPACE_ASSET_OBJECTS, name) if name else None
    except InvalidStorageKey:
        key = None
    if key is None:
        raise ValueError("file_path is not a managed PDF")
    digest = hashlib.sha256()
    size = 0
    try:
        with objects.open_read(key) as handle:
            for chunk in iter(lambda: handle.read(_HASH_CHUNK), b""):
                digest.update(chunk)
                size += len(chunk)
    except (ObjectNotFound, ObjectIntegrityError):
        # A key that names no regular file (a directory, a link) is no managed PDF either.
        raise ValueError("the managed PDF does not exist") from None
    return commands.ManagedFile(storage_locator=name, sha256=digest.hexdigest(), byte_size=size)


def _thumb_page(value: Any) -> Optional[int]:
    """SQLite's ``add_work`` rule: a positive integer, else no preferred page."""
    if value is None or value == "":
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 1 else None


def mark_opened(db: Database, ctx: RequestContext, work_id: str) -> bool:
    """Record an open now (server time), through the max-register; whether the Work exists."""
    from backend.domain.works import commands

    wid = (work_id or "").strip()
    if not wid:
        raise ValueError("work_id is required")
    moment = datetime.now(timezone.utc)
    existed, _changed, _effective = _command(db, ctx, lambda scope: commands.mark_opened(scope, wid, moment))
    return existed


#: The fields ``PATCH /api/works/:id`` may set (SQLite's ``update_work_metadata``).
METADATA_FIELDS = frozenset({
    "title", "status", "abstract", "published_date", "author_text", "year", "publisher", "location",
    "edition", "journal", "volume", "issue", "pages", "isbn", "doi", "text_content", "doc_type",
    "private_notes", "thumb_page", "source_kind", "source_url", "source_mime", "thumb_url", "provider",
    "provider_id", "urldate", "file_path", "hide_pdf_link_annotations", "citation_style",
})


def update_metadata(db: Database, ctx: RequestContext, work_id: str, fields: dict) -> None:
    """SQLite's ``update_work_metadata``: the same fields, refusals, messages and canonical values.

    A synchronized field advances its revision only when it changes.
    Differences: ``file_path`` (adopting or replacing a managed file) is
    refused with ``NotPortedError`` before anything is read; ``text_content``
    is written as the Research Note with its revision; an Asset field
    (``thumb_page``, ``source_mime``) on a Work with no file or stream is
    refused. Unknown fields are ignored, as on SQLite.
    """
    from backend.domain.works import metadata_rules as rules
    from backend.domain.works import source_rules
    from backend.domain.works.rules import normalize_doc_type
    from backend.library import NotPortedError

    updates = {k: v for k, v in fields.items() if k in METADATA_FIELDS}
    identity_writes = sorted(set(updates) & source_rules.SOURCE_AGGREGATE_COLUMNS)
    if identity_writes:
        raise ValueError(
            "%s cannot be set directly: a Work's source identity changes as one "
            "aggregate. Use the SET_WORK_SOURCE operation." % ", ".join(identity_writes))
    if "file_path" in updates:
        raise NotPortedError("update_work_metadata(file_path)")
    if "hide_pdf_link_annotations" in updates:
        raw = updates["hide_pdf_link_annotations"]
        updates["hide_pdf_link_annotations"] = (
            raw is True or raw == 1
            or (isinstance(raw, str) and raw.strip().lower() in ("1", "true", "yes")))
    if "doc_type" in updates:
        updates["doc_type"] = normalize_doc_type(updates["doc_type"])
    if not updates:
        return
    synced = {k: v for k, v in updates.items() if k in rules.SYNCED_FIELDS}
    notes_in = {k: updates[k] for k in ("private_notes", "text_content") if k in updates}
    plain = {k: v for k, v in updates.items() if k not in synced and k not in notes_in}
    for field, value in synced.items():
        if not rules.is_valid_field_value(field, value):
            raise ValueError("%s is not a valid value for %s" % (value, field)
                             if field in rules.FIELD_ALLOWLISTS or field in rules.FIELD_CODECS
                             else "%s exceeds the maximum supported length" % field)
    synced = {k: rules.canonical_wire(k, v) for k, v in synced.items()}
    # A note can add a work_notes row, a peer table of the Work merge (§7.1).
    _command(db, ctx, lambda scope: _patch_work(scope, work_id, synced, notes_in, plain),
             serializable=bool(notes_in))


def _patch_work(scope: LibraryScope, work_id: str, synced: dict[str, str],
                notes_in: dict[str, Any], plain: dict[str, Any]) -> None:
    """One PATCH, in one transaction, in SQLite's order: guards, plain, notes, fields."""
    from backend.domain.works import metadata_rules as rules
    from backend.domain.research.rules import ResearchError
    from backend.domain.works import metadata, notes

    row = metadata.target(scope, work_id, for_update=True)
    if row is None:
        if notes_in:
            raise ResearchError("not_found", "Work not found.", 404)
        return
    for field, wire in synced.items():
        refused = metadata.refusal(row, field, rules.wire_to_database(field, wire))
        if refused is not None:
            raise ValueError(str(refused))
    for field, value in plain.items():
        refused = metadata.refusal(row, field, value)
        if refused is not None:
            raise ValueError(str(refused))
        metadata.write(scope, row, field, value)
    for field, value in notes_in.items():
        kind = notes.PRIVATE if field == "private_notes" else notes.RESEARCH
        notes.set_note(scope, work_id, kind, "" if value is None else value)
    for field, wire in synced.items():
        metadata.set_synced_field(scope, row, field, wire)


def delete(db: Database, ctx: RequestContext, work_id: str, *, claim_pdf: bool = True) -> Any:
    """Delete the Work and what it owns; a ``DeletedWorkRecord``, or ``None`` for a missing Work.

    The delete records the cleanup claims of the stored files no other Asset
    names in its own transaction (``objects.lifecycle.release``); the bytes
    stay until a settle finds nothing naming them (L-A-1, §11.2).
    ``claim_pdf=False`` (undoing a create that adopted existing bytes)
    records none; bytes nothing names are still orphans to the sweep after
    its grace period.
    """
    from backend.domain.works import commands
    from backend.domain.works.rules import DeletedWorkRecord

    deleted = _command(db, ctx, lambda scope: commands.delete_work(scope, work_id, claim_files=claim_pdf))
    if deleted is None:
        return None
    return DeletedWorkRecord(work_id=deleted.work_id, file_path=deleted.file_path,
                             managed_pdf_still_referenced=deleted.managed_pdf_still_referenced)


def bulk_update(db: Database, ctx: RequestContext, data: dict) -> Dict[str, Any]:
    """``POST /api/works/bulk``: SQLite's ``bulk_update_works`` in one SERIALIZABLE transaction.

    The body is validated by the same ``parse_bulk_work_request`` (limits,
    messages, deduplication), and a missing Work, Folder or Tag is the same
    ``BulkWorkError`` 404 with nothing written. A Work, Folder or Tag of
    another Library is missing here. The Works change through their
    single-Work writers (``backend.domain.works.bulk``), so revisions advance
    only for Works whose state changes.
    """
    from backend.domain.works import bulk
    from backend.domain.works.rules import (
        BULK_FOLDER_MISSING,
        BULK_TAGS_MISSING,
        BULK_WORKS_MISSING,
        BulkWorkError,
        parse_bulk_work_request,
    )

    request = parse_bulk_work_request(data)
    try:
        _command(db, ctx, lambda scope: bulk.apply(
            scope, request.action, request.work_ids, status=request.status,
            folder_id=request.folder_id, tag_ids=request.tag_ids), serializable=True)
    except bulk.Missing as missing:
        message = {bulk.Missing.WORKS: BULK_WORKS_MISSING, bulk.Missing.FOLDER: BULK_FOLDER_MISSING,
                   bulk.Missing.TAGS: BULK_TAGS_MISSING}[missing.what]
        raise BulkWorkError(message, 404) from None
    return request.result()
