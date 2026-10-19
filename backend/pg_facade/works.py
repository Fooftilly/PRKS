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
(``finish_work_summary_rows``) and ``html_content`` from the wiki-link
resolver, which is not ported yet (a Work with a Research Note reaches it).
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, List, Optional, TypeVar

from backend.pg_facade.context import current_context
from backend.pg_facade.registry import FacadeMixin, ported

if TYPE_CHECKING:
    from backend.domain.identity.library_scope import LibraryScope
    from backend.domain.works.commands import ManagedFile, NewAsset

T = TypeVar("T")

_HASH_CHUNK = 1024 * 1024


class WorksMixin(FacadeMixin):
    """Work create and reads (L-W-1 PR 2)."""

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

    def _pdfs_dir(self) -> str:
        if self._storage is None:
            raise RuntimeError("this PostgreSQL library has no data root")
        return self._storage.pdfs_dir

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
        from backend.db_manager import _canonical_new_source, normalize_doc_type
        from backend.domain.works import commands

        kind, url, provider, provider_id = _canonical_new_source(
            source_kind, source_url, provider, provider_id, file_path)
        asset = self._new_asset(kind, file_path, url, provider, provider_id, source_mime,
                                thumb_url, thumb_page)
        bibliographic = {
            "doc_type": normalize_doc_type(doc_type), "year": year,
            "published_date": published_date, "edition": edition, "publisher": publisher,
            "location": location, "journal": journal, "volume": volume, "issue": issue,
            "pages": pages, "isbn": isbn, "doi": doi,
            "urldate": (urldate or "").strip() or None,
        }
        stream = isinstance(asset.source, commands.Stream) if asset is not None else False
        new = commands.NewWork(
            work_id=self.generate_id("W"),
            manifestation_id=self.generate_id("MF"),
            title=title,
            status=status,
            abstract=abstract,
            author_text=author_text,
            bibliographic=bibliographic,
            citation_url=None if stream else (url or "").strip() or None,
            asset_id=self.generate_id("AS") if asset is not None else None,
            asset=asset,
            research_note=text_content,
            private_note=(private_notes or "").strip() or None,
        )
        return self._command(lambda scope: commands.create_work(scope, new), serializable=True)

    def _new_asset(self, kind: Optional[str], file_path: str, url: Optional[str],
                   provider: Optional[str], provider_id: Optional[str], source_mime: str,
                   thumb_url: str, thumb_page: Any) -> Optional[NewAsset]:
        from backend.domain.works import commands

        presentation: dict[str, Any] = {
            "media_type": (source_mime or "").strip() or None,
            "thumb_url": (thumb_url or "").strip() or None,
            "thumb_page": _thumb_page(thumb_page),
        }
        has_file = bool((file_path or "").strip())
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
        return commands.NewAsset(source=self._managed_file(file_path), **presentation)

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
        from backend.domain.works import projection, queries

        core = self._read(lambda scope: queries.work(scope, work_id))
        if core is None:
            return None
        work = projection.work_detail(core)
        if work.get("text_content"):
            # Not ported yet: reached through the facade's NotPortedError refusal.
            resolve_wiki_links = getattr(self, "resolve_wiki_links")
            work["html_content"] = resolve_wiki_links(work["text_content"])
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


def _thumb_page(value: Any) -> Optional[int]:
    """``add_work``'s rule: a positive integer, else no preferred page."""
    if value is None or value == "":
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 1 else None
