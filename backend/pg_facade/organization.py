"""Organization family of the PostgreSQL transition facade (L-O-1).

Owned by the Organization lane (``docs/postgresql-architecture.md`` §15.2):
folders. Each method keeps the signature and return shape of the
SQLite library it replaced.

A method acts for the bound
:class:`~backend.domain.identity.request_context.RequestContext` (see
:mod:`backend.pg_facade.works`): reads run in one ``library_query``
snapshot, commands in one ``library_command`` transaction, SERIALIZABLE
where §7.1 lists the command (filing a Work in a Folder, a peer table of
the Work merge). The domain modules (and so
psycopg) are imported inside the methods.

Ported here: Folders, their
hierarchy and fields, the Folder a Work is in, and their revision states
(PR 3). Tags with their aliases, delete, merge and Work and Folder Tags
(L-O-1 PR 2), Playlists with their fields, membership, order and revision
states (PR 4), Saved Views (PR 5) and the application settings (PR 6) left
the facade in PG-C3: ``backend/api/tags.py``, ``backend/api/playlists.py``,
``backend/api/saved_views.py`` and ``backend/api/settings.py`` call their
domain commands. Differences from
SQLite, each deliberate:

- Names, aliases and Folder titles compare by
  ``prks.identity_key`` (§8.1), not ASCII-only ``LOWER``: "Straße" and
  "STRASSE", or "Λόγος" and "ΛΌΓΟΣ", are one name.
- A name or alias over 200 UTF-8 bytes, or a name, alias or Folder field
  holding NUL, is refused with ``ValueError`` (SQLite stores it); a blank
  colour stores the default.
- A link to a missing Work, Folder or Tag is refused with ``ValueError``
  (SQLite's foreign key raises ``IntegrityError``), and so is filing a
  missing Work into a Folder.
- ``add_work_to_folder`` advances the Work's ``work-folder`` revision when it
  files the Work: the Work exists unfiled before it, so filing it is a
  change another device must see (SQLite writes no revision). Filing at
  ``CREATE_WORK`` is construction and advances nothing on either backend.
- ``ensure_default_uncategorized_folder_id`` returns the root Folder whose
  title has the key of "Uncategorized" (SQLite fails to create one when a
  root Folder holds the title in another case).
- A Folder delete removes its Tag links and leaves every field and
  Folder-Tag scope one revision on (SQLite cascades the links and leaves
  the scopes), so a base observed before the delete is stale on a Folder
  recreated under the id.
- A Playlist delete leaves every field scope one revision on, and a Work
  delete advances its Playlist's order revision (SQLite advances neither),
  so a base observed before the delete is stale. A Playlist's empty
  ``original_url`` is stored as ``''`` and read back as ``None``, as SQLite
  stores it.
- ``get_work_tags``, ``get_folder_tags``, ``get_related_folders_for_work``
  and the affected-id lists of a delete or merge are ordered by id (SQLite
  gives storage order); equal Folder titles, and Works of one Folder created
  in the same second, are ordered by id.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Dict, List, Optional, TypeVar

from backend.pg_facade.context import current_context
from backend.pg_facade.registry import FacadeMixin

if TYPE_CHECKING:
    from backend.domain.identity.library_scope import LibraryScope

T = TypeVar("T")


def _read(library: FacadeMixin, fn: Callable[[LibraryScope], T]) -> T:
    from backend.domain.identity.library_scope import library_query

    return library_query(library._database, current_context(), fn)


def _command(library: FacadeMixin, fn: Callable[[LibraryScope], T], *, serializable: bool = False) -> T:
    from backend.db.database import Isolation
    from backend.domain.identity.authorization import Capability
    from backend.domain.identity.library_scope import library_command

    isolation = Isolation.SERIALIZABLE if serializable else Isolation.READ_COMMITTED
    return library_command(library._database, current_context(), Capability.EDIT_LIBRARY, fn,
                           isolation=isolation)


def _command_deciding_titles(library: FacadeMixin, fn: Callable[[LibraryScope], T], *,
                             serializable: bool = False) -> T:
    """:func:`_command`, run once more when a concurrent writer of the title won on its index.

    The index aborted the first transaction; the rerun reads the winner and
    refuses with the rule's own ``ValueError`` (``TITLE_TAKEN``).
    """
    from backend.db.errors import ConstraintViolationError
    from backend.domain.organization import folders

    try:
        return _command(library, fn, serializable=serializable)
    except ConstraintViolationError as error:
        if error.constraint != folders.TITLE_KEY:
            raise
        return _command(library, fn, serializable=serializable)


def _new_id(prefix: str) -> str:
    from backend.entity_ids import generate

    return generate(prefix)


class OrganizationMixin(FacadeMixin):
    """Folders and Work filing (L-O-1 PR 3)."""

    # -- Folders ----------------------------------------------------------------
    def ensure_default_uncategorized_folder_id(self) -> str:
        """Top-level folder titled 'Uncategorized'; create if missing."""
        from backend.domain.organization import folders

        new_id = _new_id("F")
        return _command(self, lambda scope: folders.ensure_uncategorized(scope, new_id))
    def add_folder(self, title: str, description: str = "", parent_id: Optional[str] = None) -> str:
        from backend.domain.organization import folders

        pid = None if parent_id in (None, "", False) else str(parent_id).strip()
        folder_id = _new_id("F")

        def run(scope: LibraryScope) -> str:
            if pid and not folders.share(scope, pid):
                raise ValueError("Parent folder not found.")
            folders.insert(scope, folder_id, title, description, pid or "")
            return folder_id

        return _command(self, run)
    def get_all_folders(self) -> List[dict]:
        from backend.domain.organization import folders

        return _read(self, folders.catalog)
    def etag_folders_catalog(self, rows: Optional[List[dict]] = None) -> str:
        from backend.etags import etag_for_representation

        data = self.get_all_folders() if rows is None else rows
        return etag_for_representation("folders", data)
    def get_folder(self, folder_id: str) -> Optional[dict]:
        from backend.domain.organization import folders, links
        from backend.domain.works import projection, queries

        def read(scope: LibraryScope) -> Optional[Dict[str, Any]]:
            folder = folders.folder_row(scope, folder_id)
            if folder is None:
                return None
            folder["children"] = folders.children(scope, folder_id)
            folder["parent"] = folders.parent(scope, folder_id)
            folder["works"] = [projection.work_summary(core, with_folder=False)
                               for core in queries.works_in_folder(scope, folder_id)]
            folder["tags"] = links.tags_of(scope, links.FOLDER, folder_id)
            return folder

        found = _read(self, read)
        if found is not None:
            # The Works lane's file sizes (``WorksMixin._finish``) on the composed library.
            getattr(self, "_finish")(found["works"])
        return found
    def get_folder_work_ids(self, folder_id: str) -> List[str]:
        from backend.domain.organization import folders

        return _read(self, lambda scope: folders.member_work_ids(scope, folder_id))
    def folder_exists(self, folder_id: str) -> bool:
        from backend.domain.organization import folders

        fid = (folder_id or "").strip()
        if not fid:
            return False
        return _read(self, lambda scope: folders.exists(scope, fid))
    def update_folder_metadata(self, folder_id: str, fields: dict) -> None:
        """Update editable folder fields through the revision-aware writer (rename before move)."""
        from backend import folder_sync
        from backend.domain.organization import folders

        updates = {k: v for k, v in fields.items() if k in folder_sync.FIELD_SET}
        if not updates:
            return

        def run(scope: LibraryScope) -> None:
            if "parent_id" in updates:
                folders.lock_hierarchy(scope)  # before any Folder row (the lock order)
            if not folders.lock(scope, folder_id, key="title" in updates or "parent_id" in updates):
                raise ValueError("Folder not found.")
            for field in ("title", "parent_id", "description", "private_notes"):
                if field in updates:
                    value = updates[field]
                    folders.set_field(scope, folder_id, field, "" if value in (None, False) else str(value))

        _command_deciding_titles(self, run)
    def delete_empty_folder(self, folder_id: str) -> None:
        """Destroy an empty Folder; its Tag links go and its scopes advance, Tag identity stays."""
        from backend.domain.organization import folders

        fid = (folder_id or "").strip()
        if not fid:
            raise ValueError("Folder not found.")
        deleted, refusal = _command(self, lambda scope: folders.delete(scope, fid))
        if refusal == "FOLDER_NOT_EMPTY":
            raise ValueError("Cannot delete folder: Folder is not empty. Please remove all files first.")
        if refusal == "FOLDER_HAS_SUBFOLDERS":
            raise ValueError("Cannot delete folder: Folder has subfolders. Please move or delete them first.")
        if not deleted:
            raise ValueError("Folder not found.")
    def get_folder_sync_state(self, folder_id: str) -> Optional[dict]:
        from backend.domain.organization import folders

        return _read(self, lambda scope: folders.folder_state(scope, folder_id))
    def get_related_folders_for_work(self, work_id: str) -> List[dict]:
        from backend.domain.organization import folders

        return _read(self, lambda scope: folders.related(scope, work_id))

    # -- the Folder a Work is in --------------------------------------------------
    def add_work_to_folder(self, folder_id: str, work_id: str) -> None:
        """Attach a work to a folder. Fails if the work is already in a different folder."""
        from backend.domain.organization import folders

        fid = (folder_id or "").strip()
        if not fid:
            raise ValueError("folder_id is required")
        wid = (work_id or "").strip()
        if not wid:
            raise ValueError("work_id is required")

        def run(scope: LibraryScope) -> None:
            found = folders.lock_work(scope, wid)
            current = folders.work_folder(scope, wid) if found else ""
            if current == fid:
                return
            if current:
                raise ValueError("This file is already in another folder.")
            if not folders.share(scope, fid):
                raise ValueError("The selected folder no longer exists.")
            if not found:
                raise ValueError("This file no longer exists.")
            folders.set_work_folder(scope, wid, fid)

        _command(self, run, serializable=True)
    def move_work_to_folder(self, work_id: str, folder_id: Optional[str]) -> None:
        """Assign / move / clear, through the revision-aware boundary."""
        from backend.domain.organization import folders

        wid = (work_id or "").strip()
        if not wid:
            raise ValueError("work_id is required")
        desired = "" if folder_id is None else str(folder_id)

        def run(scope: LibraryScope) -> None:
            if folders.lock_work(scope, wid):
                folders.set_work_folder(scope, wid, desired)
                return
            target = desired.strip()
            if target and not folders.exists(scope, target):
                raise folders.FolderRuleError("FOLDER_NOT_FOUND", "Folder not found.")
            if target:
                raise ValueError("work not found")

        _command(self, run, serializable=True)
    def get_work_folder_state(self, work_id: str) -> Optional[dict]:
        from backend.domain.organization import folders

        return _read(self, lambda scope: folders.work_folder_state(scope, work_id))
