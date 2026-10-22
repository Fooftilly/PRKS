"""Organization family of the PostgreSQL transition facade (L-O-1).

Owned by the Organization lane (``docs/postgresql-architecture.md`` §15.2):
tags, folders, playlists, Saved Views and settings. Each ported method
keeps the ``PRKSDatabase`` signature and return shape and is registered by
one line, ``@ported`` from :mod:`backend.pg_facade.registry`, directly
above it.

A method acts for the bound
:class:`~backend.domain.identity.request_context.RequestContext` (see
:mod:`backend.pg_facade.works`): reads run in one ``library_query``
snapshot, commands in one ``library_command`` transaction, SERIALIZABLE
where §7.1 lists the command (a Tag merge, and every add to its peer
tables: a Tag, an alias, a Work or Folder tag; and filing a Work in a
Folder or a Playlist, peer tables of the Work merge). The domain modules (and so
psycopg) are imported inside the methods.

Ported here: Tags, their aliases, delete and merge, Work and Folder tags,
and their tag-option revision states (L-O-1 PR 2); Folders, their
hierarchy and fields, the Folder a Work is in, and their revision states
(PR 3); Playlists, their fields, membership and order, and their revision
states (PR 4). Saved Views and settings are later slices. Differences from
SQLite, each deliberate:

- Names, aliases and Folder titles compare by ``prks.identity_key`` (§8.1),
  not ASCII-only ``LOWER``: "Straße" and "STRASSE", or "Λόγος" and "ΛΌΓΟΣ",
  are one name.
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
from backend.pg_facade.registry import FacadeMixin, ported

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
    """Tags, Work and Folder tags (L-O-1 PR 2), Folders and Work filing (PR 3), Playlists (PR 4)."""

    # -- Tags -------------------------------------------------------------------

    @ported
    def add_tag(self, name: str, color: str = "#6d6cf7") -> Dict[str, Any]:
        from backend.domain.organization import tags

        new_id = _new_id("T")
        return _command(self, lambda scope: tags.add_tag(scope, new_id, name, color), serializable=True)

    @ported
    def add_tag_alias(self, tag_id: str, alias: str) -> None:
        from backend.domain.organization import tags

        alias_id = _new_id("L")
        _command(self, lambda scope: tags.add_alias(scope, alias_id, tag_id, alias), serializable=True)

    @ported
    def delete_tag_alias(self, tag_id: str, alias: str) -> bool:
        from backend.domain.organization import tags

        return _command(self, lambda scope: tags.delete_alias(scope, tag_id, alias))

    @ported
    def merge_tags_into(self, source_tag_id: str, target_tag_id: str) -> Dict[str, Any]:
        """Move all links from source tag to target, drop source row, add source name as alias of target."""
        from backend.domain.organization import tags

        from backend.db.errors import ConstraintViolationError

        alias_id = _new_id("L")

        def run(scope: LibraryScope) -> Dict[str, Any]:
            return tags.merge(scope, source_tag_id, target_tag_id, alias_id)

        try:
            return _command(self, run, serializable=True)
        except ConstraintViolationError as error:
            # A link committed during the wait, by a writer that is not
            # SERIALIZABLE: decide again with it visible, once.
            if error.constraint not in tags.LINK_KEYS:
                raise
            return _command(self, run, serializable=True)

    @ported
    def delete_tag(self, tag_id: str) -> Dict[str, Any]:
        from backend.domain.organization import tags

        tid = (tag_id or "").strip()
        if not tid:
            raise ValueError("tag_id is required")
        deleted, affected = _command(self, lambda scope: tags.delete_tag(scope, tid))
        if not deleted:
            raise ValueError("tag not found")
        return {"status": "deleted", **affected}

    @ported
    def resolve_tag_id_by_label(self, label: str) -> Optional[str]:
        from backend.domain.organization import tags

        return _read(self, lambda scope: tags.resolve_label(scope, label))

    @ported
    def get_all_tags(self) -> List[dict]:
        from backend.domain.organization import tags

        return _read(self, tags.all_tags)

    @ported
    def get_tags_in_use(self) -> List[dict]:
        from backend.domain.organization import tags

        return _read(self, tags.tags_in_use)

    @ported
    def etag_tags_all(self) -> str:
        from backend.db_manager import PRKSDatabase

        return PRKSDatabase.etag_for_representation("tags", self.get_all_tags())

    # -- Work and Folder tags ---------------------------------------------------

    @ported
    def add_tag_to_work(self, work_id: str, tag_id: str):
        from backend.domain.organization import links

        _command(self, lambda scope: links.edit(scope, links.WORK, work_id, tag_id, True), serializable=True)

    @ported
    def remove_tag_from_work(self, work_id: str, tag_id: str):
        from backend.domain.organization import links

        _command(self, lambda scope: links.edit(scope, links.WORK, work_id, tag_id, False))

    @ported
    def get_work_tag_options(self, work_id: str):
        from backend.domain.organization import links

        return _read(self, lambda scope: links.options(scope, links.WORK, work_id))

    @ported
    def get_work_tags(self, work_id: str) -> List[dict]:
        from backend.domain.organization import links

        return _read(self, lambda scope: links.tags_of(scope, links.WORK, work_id))

    @ported
    def add_tag_to_folder(self, folder_id: str, tag_id: str):
        from backend.domain.organization import links

        _command(self, lambda scope: links.edit(scope, links.FOLDER, folder_id, tag_id, True),
                 serializable=True)

    @ported
    def remove_tag_from_folder(self, folder_id: str, tag_id: str):
        from backend.domain.organization import links

        _command(self, lambda scope: links.edit(scope, links.FOLDER, folder_id, tag_id, False))

    @ported
    def get_folder_tag_options(self, folder_id: str):
        from backend.domain.organization import links

        return _read(self, lambda scope: links.options(scope, links.FOLDER, folder_id))

    @ported
    def get_folder_tags(self, folder_id: str) -> List[dict]:
        from backend.domain.organization import links

        return _read(self, lambda scope: links.tags_of(scope, links.FOLDER, folder_id))

    # -- Folders ----------------------------------------------------------------

    @ported
    def ensure_default_uncategorized_folder_id(self) -> str:
        """Top-level folder titled 'Uncategorized'; create if missing."""
        from backend.domain.organization import folders

        new_id = _new_id("F")
        return _command(self, lambda scope: folders.ensure_uncategorized(scope, new_id))

    @ported
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

    @ported
    def get_all_folders(self) -> List[dict]:
        from backend.domain.organization import folders

        return _read(self, folders.catalog)

    @ported
    def etag_folders_catalog(self, rows: Optional[List[dict]] = None) -> str:
        from backend.db_manager import PRKSDatabase

        data = self.get_all_folders() if rows is None else rows
        return PRKSDatabase.etag_for_representation("folders", data)

    @ported
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

    @ported
    def get_folder_work_ids(self, folder_id: str) -> List[str]:
        from backend.domain.organization import folders

        return _read(self, lambda scope: folders.member_work_ids(scope, folder_id))

    @ported
    def folder_exists(self, folder_id: str) -> bool:
        from backend.domain.organization import folders

        fid = (folder_id or "").strip()
        if not fid:
            return False
        return _read(self, lambda scope: folders.exists(scope, fid))

    @ported
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

    @ported
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

    @ported
    def get_folder_sync_state(self, folder_id: str) -> Optional[dict]:
        from backend.domain.organization import folders

        return _read(self, lambda scope: folders.folder_state(scope, folder_id))

    @ported
    def get_related_folders_for_work(self, work_id: str) -> List[dict]:
        from backend.domain.organization import folders

        return _read(self, lambda scope: folders.related(scope, work_id))

    # -- the Folder a Work is in --------------------------------------------------

    @ported
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

    @ported
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

    @ported
    def get_work_folder_state(self, work_id: str) -> Optional[dict]:
        from backend.domain.organization import folders

        return _read(self, lambda scope: folders.work_folder_state(scope, work_id))

    # -- Playlists --------------------------------------------------------------

    @ported
    def add_playlist(self, title: str, description: str = "", original_url: str = "") -> str:
        from backend.domain.organization import playlists

        playlist_id = _new_id("PL")
        _command(self, lambda scope: playlists.insert(scope, playlist_id, title, description, original_url))
        return playlist_id

    @ported
    def update_playlist(self, playlist_id: str, fields: dict) -> None:
        """Edit a Playlist through the revision-aware boundary."""
        from backend import playlist_sync
        from backend.domain.organization import playlists

        updates = {k: v for k, v in fields.items() if k in playlist_sync.FIELD_SET}
        if not updates:
            return

        def run(scope: LibraryScope) -> None:
            if not playlists.lock(scope, playlist_id):
                raise ValueError("Playlist not found.")
            for field in playlist_sync.FIELDS:
                if field in updates:
                    value = updates[field]
                    playlists.set_field(scope, playlist_id, field, "" if value in (None, False) else str(value))

        _command(self, run)

    @ported
    def delete_playlist(self, playlist_id: str) -> None:
        """Destruction: the Playlist's Works are released and its scopes advance."""
        from backend.domain.organization import playlists

        _command(self, lambda scope: playlists.delete(scope, playlist_id))

    @ported
    def get_all_playlists(self) -> List[dict]:
        """Complete playlist catalog: the Playlist columns plus ``item_count``."""
        from backend.domain.organization import playlists

        return _read(self, playlists.catalog)

    @ported
    def etag_playlists_catalog(self, rows: List[dict]) -> str:
        from backend.db_manager import PRKSDatabase

        return PRKSDatabase.etag_for_representation("playlists", rows)

    @ported
    def get_playlist(self, playlist_id: str) -> Optional[dict]:
        from backend.domain.organization import playlists
        from backend.domain.works import projection, queries

        def read(scope: LibraryScope) -> Optional[Dict[str, Any]]:
            playlist = playlists.playlist_row(scope, playlist_id)
            if playlist is None:
                return None
            playlist["items"] = [{**projection.work_summary(core, with_folder=False),
                                  "position": core["playlist_position"]}
                                 for core in queries.works_in_playlist(scope, playlist_id)]
            return playlist

        found = _read(self, read)
        if found is not None:
            # The Works lane's file sizes (``WorksMixin._finish``) on the composed library.
            getattr(self, "_finish")(found["items"])
        return found

    @ported
    def add_work_to_playlist(self, playlist_id: str, work_id: str, position: Optional[int] = None) -> None:
        """Put a Work in a Playlist (membership, then the order when ``position`` is given)."""
        from backend import playlist_sync
        from backend.domain.organization import playlists

        pid = (playlist_id or "").strip()
        wid = (work_id or "").strip()
        if not pid or not wid:
            raise ValueError("Playlist or work not found.")

        def run(scope: LibraryScope) -> None:
            if not playlists.lock_work(scope, wid):
                raise ValueError("Playlist or work not found.")
            try:
                playlists.set_work_playlist(scope, wid, pid)
            except playlist_sync.PlaylistRuleError as error:
                raise ValueError("Playlist or work not found.") from error
            if position is None:
                return
            order = [w for w in playlists.order(scope, pid) if w != wid]
            order.insert(max(0, min(int(position), len(order))), wid)
            playlists.set_order(scope, pid, order)

        _command(self, run, serializable=True)

    @ported
    def remove_work_from_playlist(self, playlist_id: str, work_id: str) -> None:
        """Take a Work out of the named Playlist; nothing changes when it is not in it."""
        from backend.domain.organization import playlists

        wid = (work_id or "").strip()
        pid = (playlist_id or "").strip()

        def run(scope: LibraryScope) -> None:
            if playlists.lock_work(scope, wid) and playlists.work_playlist(scope, wid) == pid:
                playlists.set_work_playlist(scope, wid, "")

        _command(self, run)

    @ported
    def reorder_playlist(self, playlist_id: str, work_ids: List[str]) -> None:
        """Rewrite the whole order, through the boundary the aggregate owns."""
        from backend import playlist_sync
        from backend.domain.organization import playlists

        if not work_ids:
            return

        def run(scope: LibraryScope) -> None:
            try:
                playlists.set_order(scope, playlist_id, list(work_ids))
            except playlist_sync.PlaylistRuleError as error:
                raise ValueError("Playlist not found.") from error

        _command(self, run)

    @ported
    def get_playlist_sync_state(self, playlist_id: str) -> Optional[dict]:
        """Field revisions and the order revision for one Playlist."""
        from backend.domain.organization import playlists

        return _read(self, lambda scope: playlists.playlist_state(scope, playlist_id))

    @ported
    def get_work_playlist_state(self, work_id: str) -> Optional[dict]:
        """Which playlist a Work is in, and the revision that says so."""
        from backend.domain.organization import playlists

        return _read(self, lambda scope: playlists.work_playlist_state(scope, work_id))
