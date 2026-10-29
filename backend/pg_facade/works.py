"""Works family of the PostgreSQL transition facade (L-W-1, L-W-2).

Owned by the Works lane (``docs/postgresql-architecture.md`` §15.2): Works,
Manifestations, notes, lifecycle, metadata, bulk actions, BibTeX and wiki
links. Each ported method keeps the ``PRKSDatabase`` signature and return
shape and is registered by one line, ``@ported`` from
:mod:`backend.pg_facade.registry`, directly above it.

A method acts for the :class:`~backend.domain.identity.request_context.RequestContext`
bound with :func:`backend.pg_facade.context.acting_as`; it refuses to run
without one. Reads run in one ``library_query`` snapshot, commands in one
``library_command`` transaction, so authorization and the epoch check happen
inside the same transaction as the work (L-ID-1). The domain modules (and so
psycopg) are imported inside the methods: importing the facade stays free of
psycopg.

What the SQLite methods take from outside the database is taken the same
way here: ``file_size_bytes`` from the managed PDF on disk
(``finish_work_summary_rows``); ``html_content`` is the wiki-link resolver's,
read in the Work detail's snapshot.
"""

from __future__ import annotations

import hashlib
import json
import inspect
import os
from collections.abc import Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional, TypeVar

from backend.pg_facade.context import current_context
from backend.pg_facade.registry import FacadeMixin, ported

if TYPE_CHECKING:
    from backend.db_manager import DeletedWorkRecord
    from backend.domain.identity.library_scope import LibraryScope
    from backend.domain.works.commands import ManagedFile, NewAsset, NewWork
    from backend.storage.objects import StorageBackend, Writer

T = TypeVar("T")

_HASH_CHUNK = 1024 * 1024


def new_work(fields: dict[str, Any], asset: Optional[NewAsset], url: Optional[str]) -> NewWork:
    """The Work ``add_work`` creates from its arguments (``fields``), its Asset and source URL.

    ``fields`` holds every ``add_work`` argument; ``url`` is the canonical
    source URL (``_canonical_new_source``). Also the import's
    (``ProcessingMixin.import_processing_file``).
    """
    from backend.db_manager import normalize_doc_type
    from backend.domain.works import commands
    from backend.entity_ids import generate

    bibliographic = {name: fields[name] for name in (
        "year", "published_date", "edition", "publisher", "location", "journal", "volume",
        "issue", "pages", "isbn", "doi")}
    bibliographic["doc_type"] = normalize_doc_type(fields["doc_type"])
    bibliographic["urldate"] = (fields["urldate"] or "").strip() or None
    stream = isinstance(asset.source, commands.Stream) if asset is not None else False
    return commands.NewWork(
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


class WorksMixin(FacadeMixin):
    """Work create, reads and mutations (L-W-1), and bulk actions (L-W-2)."""

    # -- plumbing ---------------------------------------------------------------

    def _read(self, fn: Callable[[LibraryScope], T]) -> T:
        from backend.domain.identity.library_scope import library_query

        return library_query(self._database, current_context(), fn)

    def _command(self, fn: Callable[[LibraryScope], T], *, serializable: bool = False) -> T:
        from backend.db.database import Isolation
        from backend.domain.identity.authorization import Capability
        from backend.domain.identity.library_scope import library_command

        isolation = Isolation.SERIALIZABLE if serializable else Isolation.READ_COMMITTED
        return library_command(
            self._database, current_context(), Capability.EDIT_LIBRARY, fn, isolation=isolation
        )

    def _authorize_edit(self) -> None:
        """Refuse a caller who may not edit before any shared file is touched.

        One data root serves every Library, so a managed PDF is inspected and
        hashed only for an actor who holds ``EDIT_LIBRARY`` now: otherwise the
        refusal would say whether a key exists, after reading its bytes. The
        command re-authorizes and checks the epoch in its own transaction.
        """
        from backend.domain.identity.authorization import Capability
        from backend.domain.identity.library_scope import library_query

        library_query(self._database, current_context(), lambda scope: None,
                      capability=Capability.EDIT_LIBRARY)

    def _pdfs_dir(self) -> str:
        if self._storage is None:
            raise RuntimeError("this PostgreSQL library has no data root")
        return self._storage.pdfs_dir

    def _objects(self) -> StorageBackend:
        from backend.storage.objects import LocalFilesystemStorage

        if self._storage is None:
            raise RuntimeError("this PostgreSQL library has no data root")
        return LocalFilesystemStorage.for_config(self._storage)

    def _finish(self, rows: List[dict]) -> List[dict]:
        from backend.db_manager import finish_work_summary_rows

        finish_work_summary_rows(rows, self._pdfs_dir())
        return rows

    # -- ids and ETags ----------------------------------------------------------

    @ported
    def generate_id(self, prefix: str) -> str:
        from backend.entity_ids import generate

        return generate(prefix)

    @staticmethod
    @ported
    def etag_for_representation(label: str, rows: List[dict]) -> str:
        from backend.db_manager import PRKSDatabase

        return PRKSDatabase.etag_for_representation(label, rows)

    @ported
    def etag_works_catalog(self, rows: List[dict]) -> str:
        return self.etag_for_representation("works", rows)

    @ported
    def etag_recent_works(self) -> str:
        from backend.domain.works import queries

        count, newest = self._read(queries.recent_etag_parts)
        return f'W/"prks-recent-{count}-{newest}"'

    @ported
    def etag_recently_added_works(self) -> str:
        from backend.domain.works import queries

        count, newest = self._read(queries.recently_added_etag_parts)
        return f'W/"prks-recently-added-{count}-{newest}"'

    # -- create -----------------------------------------------------------------

    @ported
    def add_work(self, title: str, status: str = 'Not Started', abstract: str = "",
                 text_content: str = "", published_date: str = "", file_path: str = "",
                 author_text: str = "", year: str = "", publisher: str = "",
                 location: str = "",
                 edition: str = "",
                 journal: str = "", volume: str = "", issue: str = "",
                 pages: str = "", isbn: str = "", doi: str = "",
                 doc_type: str = "article",
                 source_kind: str = "",
                 source_url: str = "",
                 source_mime: str = "",
                 thumb_url: str = "",
                 provider: str = "",
                 provider_id: str = "",
                 urldate: str = "",
                 thumb_page=None,
                 private_notes: str = "") -> str:
        """``PRKSDatabase.add_work`` on PostgreSQL.

        The source is decided by the SQLite rules (``_canonical_new_source``):
        a contradictory or unsupported source raises ``ValueError``. Then:

        - a managed ``file_path`` must name an existing file under the data
          root's PDF directory, whose SHA-256 and size the Asset records (a
          missing or non-managed path raises ``ValueError``; SQLite stores
          the dangling path);
        - a video becomes an ``external_stream`` Asset, and a video with a
          ``file_path`` is refused (a stream stores no bytes);
        - source presentation fields (``source_mime``, ``thumb_*``) belong
          to the Asset, so a Work created with no file and no stream does not
          keep them, and a ``"pdf"`` kind without a file reads back as no
          source.
        """
        fields = {name: value for name, value in locals().items() if name != "self"}
        return self._add_work(fields, None)

    def _add_work_with_pdf(self, file_name: str, write: Writer, **fields: Any) -> str:
        """Create a Work from an uploaded PDF: ``add_work`` with the bytes stored here.

        Not a ``PRKSDatabase`` method, so not public: on SQLite the HTTP
        adapter stores the upload first
        (``work_pdf_replace.store_new_managed_pdf_bytes``) and passes its
        ``file_path``; routing the upload here is PG-C1's. Here the caller's
        edit right is checked
        before anything is written, ``objects.ingest.store_pdf`` writes the
        source key (and a linearized working key), and the create adopts
        them in its own transaction. ``fields`` are ``add_work``'s other
        arguments. Bytes a refused or failed create leaves behind are never
        removed here; the orphan sweep claims them (§11.2).
        """
        if "file_path" in fields:
            raise TypeError("the uploaded PDF is the Work's file")
        bound = inspect.signature(WorksMixin.add_work).bind(self, **fields)
        bound.apply_defaults()
        arguments = {name: value for name, value in bound.arguments.items() if name != "self"}
        return self._add_work(arguments, (file_name, write))

    def _add_work(self, fields: dict[str, Any], upload: Optional[tuple[str, Writer]]) -> str:
        from backend.db_manager import _canonical_new_source
        from backend.domain.objects import ingest
        from backend.domain.works import commands

        file_path = fields["file_path"]
        self._authorize_edit()
        # An upload has a file before it has a key: classified as one.
        kind, url, provider, provider_id = _canonical_new_source(
            fields["source_kind"], fields["source_url"], fields["provider"], fields["provider_id"],
            upload[0] if upload is not None else file_path)
        managed: Optional[ManagedFile] = None
        if upload is not None:
            if kind == "video":
                raise ValueError("a video Work has no managed file")
            managed = ingest.store_pdf(self._objects(), *upload).managed_file()
        asset = self._new_asset(kind, file_path, url, provider, provider_id, fields["source_mime"],
                                fields["thumb_url"], fields["thumb_page"], managed)
        new = new_work(fields, asset, url)
        objects = self._objects() if asset is not None and isinstance(asset.source, commands.ManagedFile) else None
        return self._command(lambda scope: commands.create_work(scope, new, objects=objects),
                             serializable=True)

    def _new_asset(self, kind: Optional[str], file_path: str, url: Optional[str],
                   provider: Optional[str], provider_id: Optional[str], source_mime: str,
                   thumb_url: str, thumb_page: Any,
                   managed: Optional[ManagedFile] = None) -> Optional[NewAsset]:
        from backend.domain.works import commands

        presentation: dict[str, Any] = {
            "media_type": (source_mime or "").strip() or None,
            "thumb_url": (thumb_url or "").strip() or None,
            "thumb_page": _thumb_page(thumb_page),
        }
        has_file = managed is not None or bool((file_path or "").strip())
        if kind == "video":
            if has_file:
                raise ValueError("a video Work has no managed file")
            return commands.NewAsset(
                source=commands.Stream(url=str(url), provider=provider or None,
                                       provider_id=provider_id or None),
                **presentation,
            )
        if not has_file:
            return None
        return commands.NewAsset(source=managed or self._managed_file(file_path), **presentation)

    def _managed_file(self, file_path: str) -> ManagedFile:
        from backend.db_manager import managed_pdf_filename, safe_pdf_path_under_dir
        from backend.domain.works import commands

        name = managed_pdf_filename(file_path)
        path = safe_pdf_path_under_dir(self._pdfs_dir(), name) if name else None
        if name is None or path is None:
            raise ValueError("file_path is not a managed PDF")
        if not os.path.isfile(path):
            raise ValueError("the managed PDF does not exist")
        digest = hashlib.sha256()
        size = 0
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(_HASH_CHUNK), b""):
                digest.update(chunk)
                size += len(chunk)
        return commands.ManagedFile(storage_locator=name, sha256=digest.hexdigest(), byte_size=size)

    # -- reads ------------------------------------------------------------------

    @ported
    def get_work(self, work_id: str) -> Optional[dict]:
        from backend.domain.organization import links
        from backend.domain.works import projection, queries

        from backend.domain.works import wiki_links

        from backend.domain.annotations import store as annotation_store

        def read(scope: LibraryScope) -> Optional[tuple[dict, List[dict], Optional[str], List[dict]]]:
            found = queries.work(scope, work_id)
            if found is None:
                return None
            text = found.get("text_content")
            # The Research Note's links resolve in the snapshot the Work was read in.
            html_content = wiki_links.render(scope, text) if text else None
            return (found, links.tags_of(scope, links.WORK, work_id), html_content,
                    annotation_store.work_annotations(scope, work_id))

        detail = self._read(read)
        if detail is None:
            return None
        core, tags, html_content, annotations = detail
        work = projection.work_detail(core)
        work["tags"] = tags  # the Organization lane's (L-O-1), in the same snapshot
        work["annotations"] = json.dumps(annotations)  # the Annotations lane's (L-A-1), likewise
        if html_content is not None:
            work["html_content"] = html_content
        self._finish([work])
        return work

    @ported
    def get_primary_thumbnail_fields(self, work_id: str) -> Optional[dict]:
        from backend.domain.works import projection, queries

        return projection.thumbnail_fields(self._read(lambda scope: queries.work(scope, work_id)))

    @ported
    def get_all_works(self) -> List[dict]:
        from backend.domain.works import projection, queries

        cores = self._read(queries.works_by_created)
        return self._finish([projection.work_summary(core) for core in cores])

    @ported
    def get_works_browse_catalog(self) -> List[dict]:
        from backend.domain.works import projection, queries

        cores = self._read(queries.works_by_title)
        return self._finish([projection.work_browse(core) for core in cores])

    @ported
    def get_recent_browse(self, limit: int = 30) -> List[dict]:
        from backend.domain.works import projection, queries

        cores = self._read(lambda scope: queries.works_by_opened(scope, limit))
        return self._finish([
            projection.work_browse(core, excerpt=False, extra=("last_opened_at",)) for core in cores
        ])

    @ported
    def get_recently_added_browse(self, limit: int = 50) -> List[dict]:
        from backend.domain.works import projection, queries

        cores = self._read(lambda scope: queries.works_by_created(scope, limit))
        return self._finish([
            projection.work_browse(core, excerpt=False,
                                   extra=("created_at", "publisher", "folder_id"))
            for core in cores
        ])

    @ported
    def get_recent_works(self, limit: int = 30) -> List[dict]:
        from backend.domain.works import projection, queries

        cores = self._read(lambda scope: queries.works_by_opened(scope, limit))
        return self._finish([projection.work_summary(core, with_folder=False) for core in cores])

    @ported
    def get_recently_added_works(self, limit: int = 50) -> List[dict]:
        from backend.domain.works import projection, queries

        cores = self._read(lambda scope: queries.works_by_created(scope, limit))
        return self._finish([projection.work_summary(core) for core in cores])

    @ported
    def get_work_summaries_by_ids_ordered(self, work_ids: List[str]) -> List[dict]:
        from backend.domain.works import projection, queries

        ordered_ids = [str(wid).strip() for wid in (work_ids or []) if str(wid).strip()]
        if not ordered_ids:
            return []
        by_id = self._read(lambda scope: queries.works_by_ids(scope, ordered_ids))
        return self._finish([projection.work_summary(by_id[i]) for i in ordered_ids if i in by_id])


    # -- mutations (L-W-1 PR 3) -------------------------------------------------

    @ported
    def mark_work_opened(self, work_id: str) -> bool:
        """Record an open now (server time), through the max-register."""
        from backend.domain.works import commands

        wid = (work_id or "").strip()
        if not wid:
            raise ValueError("work_id is required")
        moment = datetime.now(timezone.utc)
        existed, _changed, _effective = self._command(
            lambda scope: commands.mark_opened(scope, wid, moment))
        return existed

    @ported
    def update_work_metadata(self, work_id: str, fields: dict):
        """``PRKSDatabase.update_work_metadata`` on PostgreSQL.

        The same allowed fields, refusals and messages, the same canonical
        values and the same rule that a synchronized field advances its
        revision only when it changes. Differences: ``file_path`` (adopting
        or replacing a managed file) belongs to the objects lane and is
        refused with ``NotPortedError``; ``text_content`` is written as the
        Research Note with its revision (SQLite writes the column without
        one; the HTTP adapter routes notes elsewhere); an Asset field
        (``thumb_page``, ``source_mime``) on a Work with no file or stream is
        refused. Returns ``()``: no file is released here.
        """
        from backend import work_metadata_sync as rules
        from backend import work_source_sync
        from backend.db_manager import normalize_doc_type
        from backend.pg_facade.registry import NotPortedError

        allowed = {'title', 'status', 'abstract', 'published_date',
                   'author_text', 'year', 'publisher', 'location', 'edition', 'journal',
                   'volume', 'issue', 'pages', 'isbn', 'doi', 'text_content', 'doc_type',
                   'private_notes', 'thumb_page',
                   'source_kind', 'source_url', 'source_mime', 'thumb_url', 'provider', 'provider_id', 'urldate',
                   'file_path', 'hide_pdf_link_annotations'}
        updates = {k: v for k, v in fields.items() if k in allowed}
        identity_writes = sorted(set(updates) & work_source_sync.SOURCE_AGGREGATE_COLUMNS)
        if identity_writes:
            raise ValueError(
                "%s cannot be set directly: a Work's source identity changes as one "
                "aggregate. Use the SET_WORK_SOURCE operation."
                % ", ".join(identity_writes))
        if 'file_path' in updates:
            raise NotPortedError("update_work_metadata(file_path)")
        if 'hide_pdf_link_annotations' in updates:
            raw = updates['hide_pdf_link_annotations']
            updates['hide_pdf_link_annotations'] = (
                raw is True or raw == 1
                or (isinstance(raw, str) and raw.strip().lower() in ('1', 'true', 'yes')))
        if 'doc_type' in updates:
            updates['doc_type'] = normalize_doc_type(updates['doc_type'])
        if not updates:
            return ()
        synced = {k: v for k, v in updates.items() if k in rules.SYNCED_FIELDS}
        notes = {k: updates[k] for k in ("private_notes", "text_content") if k in updates}
        plain = {k: v for k, v in updates.items() if k not in synced and k not in notes}
        for field, value in synced.items():
            if not rules.is_valid_field_value(field, value):
                raise ValueError("%s is not a valid value for %s" % (value, field)
                                 if field in rules.FIELD_ALLOWLISTS
                                    or field in rules.FIELD_CODECS
                                 else "%s exceeds the maximum supported length" % field)
        synced = {k: rules.canonical_wire(k, v) for k, v in synced.items()}
        # A note can add a work_notes row, a peer table of the Work merge (§7.1).
        self._command(lambda scope: _patch_work(scope, work_id, synced, notes, plain),
                      serializable=bool(notes))
        return ()

    @ported
    def get_work_metadata_state(self, work_id: str) -> Optional[dict]:
        from backend.domain.works import metadata

        return self._read(lambda scope: metadata.field_state(scope, work_id))

    @ported
    def get_work_notes_state(self, work_id: str) -> Optional[dict]:
        from backend.domain.works import notes

        return self._read(lambda scope: notes.notes_state(scope, work_id))

    @ported
    def delete_work_record(self, work_id: str, *, claim_pdf: bool = True) -> Optional[DeletedWorkRecord]:
        """Delete the Work and what it owns, claiming its managed file for cleanup.

        The delete records the cleanup claims of the stored files no other
        Asset names in its own transaction (``objects.lifecycle.release``);
        the bytes stay until a settle finds nothing naming them (L-A-1,
        §11.2). ``claim_pdf=False`` (undoing a create that adopted existing
        bytes) records none; bytes nothing names are still orphans to the
        sweep after its grace period.
        """
        from backend.db_manager import DeletedWorkRecord
        from backend.domain.works import commands

        deleted = self._command(lambda scope: commands.delete_work(scope, work_id, claim_files=claim_pdf))
        if deleted is None:
            return None
        return DeletedWorkRecord(work_id=deleted.work_id, file_path=deleted.file_path,
                                 managed_pdf_still_referenced=deleted.managed_pdf_still_referenced)


    # -- bulk actions (L-W-2) ----------------------------------------------------

    @ported
    def bulk_update_works(self, data: dict) -> Dict[str, Any]:
        """``PRKSDatabase.bulk_update_works`` on PostgreSQL: one SERIALIZABLE transaction.

        The body is validated by the same ``parse_bulk_work_request`` (limits,
        messages, deduplication), and a missing Work, Folder or Tag is the
        same ``BulkWorkError`` 404 with nothing written. A Work, Folder or Tag
        of another Library is missing here. The Works change through their
        single-Work writers (``backend.domain.works.bulk``), so revisions
        advance only for Works whose state changes.
        """
        from backend.db_manager import (
            BULK_FOLDER_MISSING,
            BULK_TAGS_MISSING,
            BULK_WORKS_MISSING,
            BulkWorkError,
            parse_bulk_work_request,
        )
        from backend.domain.works import bulk

        request = parse_bulk_work_request(data)
        try:
            self._command(lambda scope: bulk.apply(
                scope, request.action, request.work_ids, status=request.status,
                folder_id=request.folder_id, tag_ids=request.tag_ids), serializable=True)
        except bulk.Missing as missing:
            message = {bulk.Missing.WORKS: BULK_WORKS_MISSING, bulk.Missing.FOLDER: BULK_FOLDER_MISSING,
                       bulk.Missing.TAGS: BULK_TAGS_MISSING}[missing.what]
            raise BulkWorkError(message, 404) from None
        return request.result()

    # -- wiki links (L-W-2) -------------------------------------------------------

    @ported
    def resolve_wiki_links(self, text: str) -> str:
        """``PRKSDatabase.resolve_wiki_links`` on PostgreSQL: the same renderer, read in one snapshot.

        Targets resolve against this Library only (``backend.domain.works.wiki_links``),
        so a Work or Person of another Library renders as unresolved.
        """
        from backend.domain.works import wiki_links

        return self._read(lambda scope: wiki_links.render(scope, text))

    # -- BibTeX (L-W-2) -----------------------------------------------------------

    @ported
    def generate_bibtex(self, work_id: str) -> str:
        """``PRKSDatabase.generate_bibtex`` on PostgreSQL: the same formatter, read in one snapshot.

        The citation record, the citation Manifestation's credits and the
        Library's export profile come from ``backend.domain.works.citation``;
        ``PRKSDatabase.format_bibtex`` turns them into the entry, so equal
        data exports byte-identical text. A Work that is not in this
        Library, or an id PostgreSQL text cannot hold (NUL), gives ``""``.
        """
        from backend.db_manager import PRKSDatabase
        from backend.domain.works import citation

        if "\x00" in work_id:
            return ""
        inputs = self._read(lambda scope: citation.bibtex_inputs(scope, work_id))
        if inputs is None:
            return ""
        record, roles, profile = inputs
        # The projection's "video" means the target's primary Asset is a stream.
        return PRKSDatabase.format_bibtex(record, roles, profile,
                                          video=record["source_kind"] == "video")


def _patch_work(scope: LibraryScope, work_id: str, synced: dict[str, str],
                notes_in: dict[str, Any], plain: dict[str, Any]) -> None:
    """One PATCH, in one transaction, in SQLite's order: guards, plain, notes, fields."""
    from backend import work_metadata_sync as rules
    from backend.domain.works import metadata, notes
    from backend.research_network import ResearchError

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


def _thumb_page(value: Any) -> Optional[int]:
    """``add_work``'s rule: a positive integer, else no preferred page."""
    if value is None or value == "":
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 1 else None
