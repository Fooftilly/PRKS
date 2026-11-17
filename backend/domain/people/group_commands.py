"""The Person Group operations for one actor: what ``backend/api/person_groups.py`` calls (PG-C3 #642).

Person Groups, their hierarchy, fields and members, and their revision
states. A read runs in one
:func:`~backend.domain.identity.library_scope.library_query` snapshot,
authorized for ``READ_LIBRARY`` (reads are not fenced by epoch); a write is
one :func:`~backend.domain.identity.library_scope.library_command`
transaction needing ``EDIT_LIBRARY``, fenced by maintenance and the epoch.
Adding a member runs SERIALIZABLE; the rest run READ COMMITTED. A name lost
to a concurrent create or rename reads as SQLite's refusal
(``GroupRuleError`` ``NAME_TAKEN``). Refusals are ``ValueError`` (a
``GroupRuleError`` is one) with SQLite's message, and ``AccessRefused``.
"""

from __future__ import annotations

from typing import Any, Callable, Optional, TypeVar

from backend import person_group_sync as rules
from backend.db.database import Database, Isolation
from backend.db.errors import ConstraintViolationError
from backend.domain.identity.authorization import Capability
from backend.domain.identity.library_scope import LibraryScope, library_command, library_query
from backend.domain.identity.request_context import RequestContext
from backend.domain.people import groups, queries
from backend.entity_ids import generate

T = TypeVar("T")

ID_PREFIX = "PG"
NAME_KEY = "person_groups_library_id_name_key_key"


def _command(db: Database, ctx: RequestContext, fn: Callable[[LibraryScope], T], *,
             serializable: bool = False) -> T:
    isolation = Isolation.SERIALIZABLE if serializable else Isolation.READ_COMMITTED
    try:
        return library_command(db, ctx, Capability.EDIT_LIBRARY, fn, isolation=isolation)
    except ConstraintViolationError as error:
        if error.constraint == NAME_KEY:
            raise rules.GroupRuleError("NAME_TAKEN", "A group with this name already exists.") from error
        raise


# -- Reads ---------------------------------------------------------------------
def catalog(db: Database, ctx: RequestContext) -> list[dict[str, Any]]:
    """Every group with ``member_count`` and ``child_count``."""
    return library_query(db, ctx, queries.group_catalog)


def detail(db: Database, ctx: RequestContext, group_id: str) -> Optional[dict[str, Any]]:
    """The group, its children, its members and its parent; ``None`` when absent."""
    return library_query(db, ctx, lambda scope: queries.group_detail(scope, group_id))


def sync_state(db: Database, ctx: RequestContext, group_id: str) -> Optional[dict[str, Any]]:
    """The group's field and membership revisions; ``None`` when absent."""
    return library_query(db, ctx, lambda scope: groups.group_state(scope, group_id))


def person_state(db: Database, ctx: RequestContext, person_id: str) -> Optional[dict[str, Any]]:
    """One Person's membership revisions, tombstones included; ``None`` when absent."""
    return library_query(db, ctx, lambda scope: groups.person_group_state(scope, person_id))


# -- Writes --------------------------------------------------------------------
def create(db: Database, ctx: RequestContext, name: Optional[str], parent_id: Optional[str] = None,
           parent_name: Optional[str] = None, description: str = "") -> str:
    """Resolve or create the parent, then create the group, in one transaction; the new id."""
    group_id, parent_new_id = generate(ID_PREFIX), generate(ID_PREFIX)
    n = (name or "").strip()
    pn = (parent_name or "").strip()

    def run(scope: LibraryScope) -> str:
        if parent_id or pn:
            groups.lock_hierarchy(scope)
        if not parent_id and pn and groups.same_name(scope, pn, n):
            raise ValueError("Parent group cannot have the same name as the new group.")
        pid = parent_id or (groups.resolve_parent(scope, pn, parent_new_id) if pn else None)
        return groups.insert_group(scope, group_id, n, pid or "", description or "")

    return _command(db, ctx, run)


def update(db: Database, ctx: RequestContext, group_id: str, fields: dict[str, Any]) -> None:
    """The fields in ``person_group_sync.FIELDS``; a ``parent_name`` is resolved or created."""
    parent_new_id = generate(ID_PREFIX)

    def run(scope: LibraryScope) -> None:
        changes = dict(fields)
        if "parent_name" in changes or "parent_id" in changes:
            groups.lock_hierarchy(scope)
        if groups.group(scope, group_id) is None:
            raise ValueError("Group not found.")
        if "parent_name" in changes:
            raw = changes.pop("parent_name")
            pn = str(raw).strip() if raw is not None else ""
            changes["parent_id"] = groups.resolve_parent(scope, pn, parent_new_id) if pn else None
        for field in rules.FIELDS:
            if field in changes:
                value = changes[field]
                groups.set_field(scope, group_id, field,
                                 "" if value in (None, False) else str(value))

    _command(db, ctx, run)


def delete(db: Database, ctx: RequestContext, group_id: str) -> None:
    if not _command(db, ctx, lambda scope: groups.delete_group(scope, group_id)):
        raise ValueError("Group not found.")


def add_member(db: Database, ctx: RequestContext, group_id: str, person_id: str) -> None:
    def run(scope: LibraryScope) -> None:
        group_found, person_found = groups.lock_pair(scope, group_id, person_id)
        if not group_found or not person_found:
            raise ValueError("Person or group not found.")
        groups.set_member(scope, group_id, person_id, True)

    _command(db, ctx, run, serializable=True)


def remove_member(db: Database, ctx: RequestContext, group_id: str, person_id: str) -> None:
    _command(db, ctx, lambda scope: groups.set_member(scope, group_id, person_id, False))
