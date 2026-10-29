"""Processing family of the PostgreSQL transition facade (L-PR-1).

Owned by the Processing lane (``docs/postgresql-architecture.md`` §15.2):
the processing inbox. Each ported method keeps the ``PRKSDatabase``
signature and return shape and is registered by one line, ``@ported`` from
:mod:`backend.pg_facade.registry`, directly above it.

A method acts for the bound
:class:`~backend.domain.identity.request_context.RequestContext` (see
:mod:`backend.pg_facade.works`). Reads run in one ``library_query``
snapshot. ``scan_processing_files`` and ``import_processing_file`` check the
caller's edit right first, then hold the Library's inbox lock across their
transactions and file changes (``backend.domain.processing.inbox``); the
import's catalogue transaction is SERIALIZABLE, and so is an update that
sets tags (§7.1). The inbox is the data root's ``processing_dir``. The
domain modules (and so psycopg) are imported inside the methods.

``scan_processing_files`` scans in the request, as the SQLite adapter
expects; the ``processing.scan`` job (``backend.domain.processing.jobs``) is
the same scan on a worker, and routing ``?rescan=1`` to it is the cutover's
(§11.5). Differences from SQLite, each deliberate:

- No absolute path is stored (``abs_path`` is gone, §11.5); a row names its
  file by ``rel_path`` under the inbox.
- An import deletes its row once the source file is removed, so the file
  leaves the list and a second import of the id is "Processing file not
  found." (SQLite keeps an ``imported`` row and answers the Work again).
  Between the commit and the removal the row is ``imported`` with the
  ingested bytes' hash; a crash there is finished by the next scan, which
  removes the file only while it has that hash and otherwise lists the new
  file as pending.
- An import is one transaction: the Work, its roles, tags and Folder
  commit together or not at all (SQLite creates the Work, then deletes it
  when a later step fails). Bytes a failed import stored are left to the
  objects sweep (§11.2).
- An update is atomic: a refused role or tag leaves the fields unwritten
  (SQLite commits the fields first). A field holding NUL is refused with
  ``ValueError``.
- ``scan_processing_files`` and ``import_processing_file`` need
  ``EDIT_LIBRARY`` (the scan writes rows); a reader's ``?rescan=1`` is
  refused.
- A row a restore holds (``restore_hold``) is never deleted by a scan while
  its file is missing (§11.4).
- A deleted target Folder clears the file's target (the key's ``SET
  NULL``), so the import files the Work in "Uncategorized".
- List ties (equal status and case-folded path) are ordered by discovery,
  then id.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Dict, List, Optional, TypeVar

from backend.pg_facade.context import current_context
from backend.pg_facade.registry import FacadeMixin, ported

if TYPE_CHECKING:
    from backend.db.database import Database
    from backend.domain.identity.library_scope import LibraryScope
    from backend.domain.works.commands import ManagedFile, NewWork

T = TypeVar("T")


class _RequestRunner:
    """The inbox protocol's :class:`~backend.domain.processing.inbox.Runner` for the bound context."""

    def __init__(self, database: Database) -> None:
        self._database = database
        library_id = current_context().library_id
        if library_id is None:
            from backend.domain.identity.authorization import AccessRefused, Refusal

            raise AccessRefused(Refusal.FORBIDDEN)
        self._library_id = library_id

    @property
    def database(self) -> Database:
        return self._database

    @property
    def library_id(self) -> str:
        return self._library_id

    def read(self, fn: Callable[[LibraryScope], T]) -> T:
        from backend.domain.identity.library_scope import library_query

        return library_query(self._database, current_context(), fn)

    def command(self, fn: Callable[[LibraryScope], T], *, serializable: bool = False) -> T:
        from backend.db.database import Isolation
        from backend.domain.identity.authorization import Capability
        from backend.domain.identity.library_scope import library_command

        isolation = Isolation.SERIALIZABLE if serializable else Isolation.READ_COMMITTED
        return library_command(self._database, current_context(), Capability.EDIT_LIBRARY, fn,
                               isolation=isolation)


def _new_work(row: dict[str, Any], managed: ManagedFile) -> NewWork:
    """The Work ``import_processing_file`` creates from the staged row (SQLite's ``add_work`` call)."""
    from backend.db_manager import _canonical_new_source
    from backend.domain.works import commands
    from backend.pg_facade.works import _thumb_page, new_work

    filename = str(row["filename"] or "")
    title = str(row["title"] or "").strip() or os.path.splitext(filename or "Untitled")[0]
    _kind, url, _provider, _provider_id = _canonical_new_source(
        "pdf", row["source_url"] or "", "", "", filename)
    fields: dict[str, Any] = {
        "title": title,
        "status": str(row["status_draft"] or "Not Started").strip() or "Not Started",
        "abstract": row["abstract"] or "",
        "author_text": row["author_text"] or "",
        "text_content": "",
        "urldate": "",
        "private_notes": row["private_notes"] or "",
        "doc_type": row["doc_type"] or "article",
    }
    for name in ("year", "published_date", "edition", "publisher", "location", "journal", "volume",
                 "issue", "pages", "isbn", "doi"):
        fields[name] = row[name] or ""
    asset = commands.NewAsset(source=managed, thumb_page=_thumb_page(row["thumb_page"]))
    return new_work(fields, asset, url)


class ProcessingMixin(FacadeMixin):
    """The processing inbox: staged files, their edits, the scan and the import."""

    # -- plumbing ---------------------------------------------------------------

    def _processing_read(self, fn: Callable[[LibraryScope], T]) -> T:
        from backend.domain.identity.library_scope import library_query

        return library_query(self._database, current_context(), fn)

    def _processing_command(self, fn: Callable[[LibraryScope], T], *, serializable: bool = False) -> T:
        return _RequestRunner(self._database).command(fn, serializable=serializable)

    def _inbox_dir(self) -> str:
        if self._storage is None:
            raise RuntimeError("this PostgreSQL library has no data root")
        return self._storage.processing_dir

    def _authorized_runner(self) -> _RequestRunner:
        """A runner for the bound context, after checking its edit right (before any file is touched)."""
        from backend.domain.identity.authorization import Capability
        from backend.domain.identity.library_scope import library_query

        library_query(self._database, current_context(), lambda scope: None,
                      capability=Capability.EDIT_LIBRARY)
        return _RequestRunner(self._database)

    # -- reads ------------------------------------------------------------------

    @ported
    def get_processing_files(self, include_imported: bool = False) -> List[dict]:
        from backend.domain.processing import files

        inbox_dir = self._inbox_dir()
        return self._processing_read(
            lambda scope: files.all_files(scope, inbox_dir, include_imported=include_imported))

    @ported
    def get_processing_file(self, processing_file_id: str) -> Optional[dict]:
        from backend.domain.processing import files

        inbox_dir = self._inbox_dir()
        return self._processing_read(lambda scope: files.one_file(scope, inbox_dir, processing_file_id))

    @ported
    def get_processing_file_pdf_path(self, processing_file_id: str) -> Optional[str]:
        from backend.domain.processing import files, inbox

        rel_path = self._processing_read(lambda scope: files.rel_path_of(scope, processing_file_id))
        # The stored spelling is the file's identity, never stripped.
        if rel_path is None or not rel_path.lower().endswith(".pdf"):
            return None
        path = inbox.resolve(self._inbox_dir(), rel_path)
        return path if path and os.path.isfile(path) else None

    # -- commands ---------------------------------------------------------------

    @ported
    def scan_processing_files(self) -> List[dict]:
        from backend.domain.processing import inbox
        from backend.entity_ids import generate

        inbox.scan(self._authorized_runner(), self._inbox_dir(), lambda: generate("PF"))
        return self.get_processing_files(include_imported=False)

    @ported
    def update_processing_file(self, processing_file_id: str, fields: Dict[str, Any]) -> dict:
        from backend.domain.processing import files

        try:
            request = files.parse_update(fields)
        except ValueError:
            # SQLite looks the file up before it reads the request.
            if self.get_processing_file(processing_file_id) is None:
                raise ValueError(files.NOT_FOUND) from None
            raise
        found = self._processing_command(lambda scope: files.update(scope, processing_file_id, request),
                                         serializable=request.tags is not None)
        out = self.get_processing_file(processing_file_id) if found else None
        if out is None:
            raise ValueError(files.NOT_FOUND)
        return out

    @ported
    def import_processing_file(self, processing_file_id: str) -> Dict[str, Any]:
        from backend.domain.processing import inbox
        from backend.storage.objects import LocalFilesystemStorage

        runner = self._authorized_runner()
        if self._storage is None:
            raise RuntimeError("this PostgreSQL library has no data root")
        objects = LocalFilesystemStorage.for_config(self._storage)
        return inbox.import_file(runner, self._inbox_dir(), objects, processing_file_id, _new_work)
