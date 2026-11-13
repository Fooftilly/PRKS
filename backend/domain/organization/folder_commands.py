"""The Folder operations for one actor: what ``backend/api/folders.py`` calls (PG-C3 #642).

Folders, their hierarchy and fields, the Folder a Work is in, and their
revision states. A read runs in one
:func:`~backend.domain.identity.library_scope.library_query` snapshot,
authorized for ``READ_LIBRARY`` (reads are not fenced by epoch); a write is
one :func:`~backend.domain.identity.library_scope.library_command`
transaction needing ``EDIT_LIBRARY``, fenced by maintenance and the epoch.
Filing a Work runs SERIALIZABLE, as §7.1 lists it
(``docs/postgresql-architecture.md``); the rest run READ COMMITTED.
Refusals are ``ValueError`` (a ``FolderRuleError`` is one) with SQLite's
message, and ``AccessRefused``.
"""

from __future__ import annotations

from typing import Any, Callable, Optional, TypeVar

from backend import folder_sync
from backend.db.database import Database, Isolation
from backend.db.errors import ConstraintViolationError
from backend.domain.identity.authorization import Capability
from backend.domain.identity.library_scope import LibraryScope, library_command, library_query
from backend.domain.identity.request_context import RequestContext
from backend.domain.organization import folders, links
from backend.domain.works import projection, queries
from backend.domain.works.rules import finish_work_summary_rows
from backend.entity_ids import generate

T = TypeVar("T")

ID_PREFIX = "F"


def _command(db: Database, ctx: RequestContext, fn: Callable[[LibraryScope], T], *,
             serializable: bool = False) -> T:
    isolation = Isolation.SERIALIZABLE if serializable else Isolation.READ_COMMITTED
    return library_command(db, ctx, Capability.EDIT_LIBRARY, fn, isolation=isolation)


def _command_deciding_titles(db: Database, ctx: RequestContext, fn: Callable[[LibraryScope], T]) -> T:
    """:func:`_command`, run once more when a concurrent writer of the title won on its index.

    The index aborted the first transaction; the rerun reads the winner and
    refuses with the rule's own ``ValueError`` (``TITLE_TAKEN``).
    """
    try:
        return _command(db, ctx, fn)
    except ConstraintViolationError as error:
        if error.constraint != folders.TITLE_KEY:
            raise
        return _command(db, ctx, fn)


# -- reads --------------------------------------------------------------------------

def catalog(db: Database, ctx: RequestContext) -> list[dict[str, Any]]:
    """Every Folder; the catalog is also its own revision source (its ETag)."""
    return library_query(db, ctx, folders.catalog)


def detail(db: Database, ctx: RequestContext, folder_id: str, pdfs_dir: str) -> Optional[dict[str, Any]]:
    """One Folder with its children, parent, Works and Tags, or ``None``.

    The Works' file sizes are read from ``pdfs_dir`` after the snapshot, as
    every Work summary's are.
    """

    def read(scope: LibraryScope) -> Optional[dict[str, Any]]:
        folder = folders.folder_row(scope, folder_id)
        if folder is None:
            return None
        folder["children"] = folders.children(scope, folder_id)
        folder["parent"] = folders.parent(scope, folder_id)
        folder["works"] = [projection.work_summary(core, with_folder=False)
                           for core in queries.works_in_folder(scope, folder_id)]
        folder["tags"] = links.tags_of(scope, links.FOLDER, folder_id)
        return folder

    found = library_query(db, ctx, read)
    if found is not None:
        finish_work_summary_rows(found["works"], pdfs_dir)
    return found


def member_work_ids(db: Database, ctx: RequestContext, folder_id: str) -> list[str]:
    return library_query(db, ctx, lambda scope: folders.member_work_ids(scope, folder_id))


def exists(db: Database, ctx: RequestContext, folder_id: Optional[str]) -> bool:
    fid = (folder_id or "").strip()
    if not fid:
        return False
    return library_query(db, ctx, lambda scope: folders.exists(scope, fid))


def related(db: Database, ctx: RequestContext, work_id: str) -> list[dict[str, Any]]:
    """The Folders related to a Work (``/api/works/:id/related_folders``)."""
    return library_query(db, ctx, lambda scope: folders.related(scope, work_id))


def sync_state(db: Database, ctx: RequestContext, folder_id: str) -> Optional[dict[str, Any]]:
    """The Folder's field revisions, or ``None``."""
    return library_query(db, ctx, lambda scope: folders.folder_state(scope, folder_id))


def work_state(db: Database, ctx: RequestContext, work_id: str) -> Optional[dict[str, Any]]:
    """Which Folder a Work is in and the revision that says so, or ``None``."""
    return library_query(db, ctx, lambda scope: folders.work_folder_state(scope, work_id))


# -- writes -------------------------------------------------------------------------

def create(db: Database, ctx: RequestContext, title: Any, description: Any = "",
           parent_id: Any = None) -> str:
    """The new Folder's id; a missing parent is ``ValueError``."""
    pid = None if parent_id in (None, "", False) else str(parent_id).strip()
    folder_id = generate(ID_PREFIX)

    def run(scope: LibraryScope) -> str:
        if pid and not folders.share(scope, pid):
            raise ValueError("Parent folder not found.")
        folders.insert(scope, folder_id, title, description, pid or "")
        return folder_id

    return _command(db, ctx, run)


def ensure_uncategorized(db: Database, ctx: RequestContext) -> str:
    """The root Folder titled "Uncategorized", created when missing."""
    new_id = generate(ID_PREFIX)
    return _command(db, ctx, lambda scope: folders.ensure_uncategorized(scope, new_id))


def update(db: Database, ctx: RequestContext, folder_id: str, fields: dict[str, Any]) -> None:
    """Edit a Folder's fields through the revision-aware writer (rename before move)."""
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

    _command_deciding_titles(db, ctx, run)


def delete(db: Database, ctx: RequestContext, folder_id: Optional[str]) -> None:
    """Destroy an empty Folder; its Tag links go and its scopes advance, Tag identity stays."""
    fid = (folder_id or "").strip()
    if not fid:
        raise ValueError("Folder not found.")
    deleted, refusal = _command(db, ctx, lambda scope: folders.delete(scope, fid))
    if refusal == "FOLDER_NOT_EMPTY":
        raise ValueError("Cannot delete folder: Folder is not empty. Please remove all files first.")
    if refusal == "FOLDER_HAS_SUBFOLDERS":
        raise ValueError("Cannot delete folder: Folder has subfolders. Please move or delete them first.")
    if not deleted:
        raise ValueError("Folder not found.")


def file_work(db: Database, ctx: RequestContext, folder_id: Optional[str], work_id: Optional[str]) -> None:
    """File an unfiled Work in a Folder; one already in another Folder is refused."""
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

    _command(db, ctx, run, serializable=True)


def move_work(db: Database, ctx: RequestContext, work_id: Optional[str], folder_id: Any) -> None:
    """Assign, move or clear (``None`` or ``""``) the Folder a Work is in."""
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

    _command(db, ctx, run, serializable=True)
