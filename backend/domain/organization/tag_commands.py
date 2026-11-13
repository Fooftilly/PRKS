"""The Tag operations for one actor: what ``backend/api/tags.py`` calls (PG-C3 #642).

Tags, their aliases, delete and merge, and the Tags of a Work or a Folder
with their tag options. A read runs in one
:func:`~backend.domain.identity.library_scope.library_query` snapshot,
authorized for ``READ_LIBRARY`` (reads are not fenced by epoch); a write is
one :func:`~backend.domain.identity.library_scope.library_command`
transaction needing ``EDIT_LIBRARY``, fenced by maintenance and the epoch.
SERIALIZABLE where §7.1 lists the command (``docs/postgresql-architecture.md``):
a Tag merge, and every add to its peer tables (a Tag, an alias, a Work or
Folder Tag); the rest run READ COMMITTED. Refusals are ``ValueError`` with
SQLite's message, and ``AccessRefused``.
"""

from __future__ import annotations

from typing import Any, Callable, Optional, TypeVar

from backend.db.database import Database, Isolation
from backend.db.errors import ConstraintViolationError
from backend.domain.identity.authorization import Capability
from backend.domain.identity.library_scope import LibraryScope, library_command, library_query
from backend.domain.identity.request_context import RequestContext
from backend.domain.organization import links, tags
from backend.entity_ids import generate

T = TypeVar("T")

TAG_ID_PREFIX = "T"
ALIAS_ID_PREFIX = "L"
DEFAULT_COLOR = "#6d6cf7"


def _command(db: Database, ctx: RequestContext, fn: Callable[[LibraryScope], T], *,
             serializable: bool = False) -> T:
    isolation = Isolation.SERIALIZABLE if serializable else Isolation.READ_COMMITTED
    return library_command(db, ctx, Capability.EDIT_LIBRARY, fn, isolation=isolation)


# -- Tags -------------------------------------------------------------------------

def all_tags(db: Database, ctx: RequestContext) -> list[dict[str, Any]]:
    return library_query(db, ctx, tags.all_tags)


def in_use(db: Database, ctx: RequestContext) -> list[dict[str, Any]]:
    return library_query(db, ctx, tags.tags_in_use)


def create(db: Database, ctx: RequestContext, name: Optional[str],
           color: Optional[str] = DEFAULT_COLOR) -> dict[str, Any]:
    new_id = generate(TAG_ID_PREFIX)
    return _command(db, ctx, lambda scope: tags.add_tag(scope, new_id, name, color), serializable=True)


def add_alias(db: Database, ctx: RequestContext, tag_id: str, alias: Optional[str]) -> None:
    alias_id = generate(ALIAS_ID_PREFIX)
    _command(db, ctx, lambda scope: tags.add_alias(scope, alias_id, tag_id, alias), serializable=True)


def delete_alias(db: Database, ctx: RequestContext, tag_id: str, alias: Optional[str]) -> bool:
    """Whether an alias with that identity key was removed."""
    return _command(db, ctx, lambda scope: tags.delete_alias(scope, tag_id, alias))


def merge(db: Database, ctx: RequestContext, source_tag_id: str, target_tag_id: str) -> dict[str, Any]:
    """Move every link of the source Tag to the target, drop the source, keep its name as an alias.

    A link committed during the wait by a writer that is not SERIALIZABLE
    aborts on a link key; the merge then decides again with it visible, once.
    """
    alias_id = generate(ALIAS_ID_PREFIX)

    def run(scope: LibraryScope) -> dict[str, Any]:
        return tags.merge(scope, source_tag_id, target_tag_id, alias_id)

    try:
        return _command(db, ctx, run, serializable=True)
    except ConstraintViolationError as error:
        if error.constraint not in tags.LINK_KEYS:
            raise
        return _command(db, ctx, run, serializable=True)


def delete(db: Database, ctx: RequestContext, tag_id: Optional[str]) -> dict[str, Any]:
    """``{"status": "deleted", ...affected ids}``; a blank or missing Tag is ``ValueError``."""
    tid = (tag_id or "").strip()
    if not tid:
        raise ValueError("tag_id is required")
    deleted, affected = _command(db, ctx, lambda scope: tags.delete_tag(scope, tid))
    if not deleted:
        raise ValueError("tag not found")
    return {"status": "deleted", **affected}


# -- Work and Folder Tags -----------------------------------------------------------

def _link(db: Database, ctx: RequestContext, owner: links.Link, owner_id: str, tag_id: Any) -> None:
    _command(db, ctx, lambda scope: links.edit(scope, owner, owner_id, tag_id, True), serializable=True)


def _unlink(db: Database, ctx: RequestContext, owner: links.Link, owner_id: str, tag_id: Any) -> None:
    _command(db, ctx, lambda scope: links.edit(scope, owner, owner_id, tag_id, False))


def _options(db: Database, ctx: RequestContext, owner: links.Link, owner_id: str) -> Optional[dict[str, Any]]:
    return library_query(db, ctx, lambda scope: links.options(scope, owner, owner_id))


def tag_work(db: Database, ctx: RequestContext, work_id: str, tag_id: Any) -> None:
    _link(db, ctx, links.WORK, work_id, tag_id)


def untag_work(db: Database, ctx: RequestContext, work_id: str, tag_id: Any) -> None:
    _unlink(db, ctx, links.WORK, work_id, tag_id)


def work_options(db: Database, ctx: RequestContext, work_id: str) -> Optional[dict[str, Any]]:
    """The Work's tag options, or ``None`` when it does not exist."""
    return _options(db, ctx, links.WORK, work_id)


def tag_folder(db: Database, ctx: RequestContext, folder_id: str, tag_id: Any) -> None:
    _link(db, ctx, links.FOLDER, folder_id, tag_id)


def untag_folder(db: Database, ctx: RequestContext, folder_id: str, tag_id: Any) -> None:
    _unlink(db, ctx, links.FOLDER, folder_id, tag_id)


def folder_options(db: Database, ctx: RequestContext, folder_id: str) -> Optional[dict[str, Any]]:
    """The Folder's tag options, or ``None`` when it does not exist."""
    return _options(db, ctx, links.FOLDER, folder_id)
