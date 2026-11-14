"""The Position operations for one actor: what ``backend/api/positions.py`` calls (PG-C3 #642).

Positions, their fields and their revision state. A read runs in one
:func:`~backend.domain.identity.library_scope.library_query` snapshot,
authorized for ``READ_LIBRARY`` (reads are not fenced by epoch); a write is
one READ COMMITTED :func:`~backend.domain.identity.library_scope.library_command`
transaction needing ``EDIT_LIBRARY``, fenced by maintenance and the epoch.
Refusals are SQLite's ``ResearchError`` codes, messages and statuses, and
``AccessRefused``; the differences from SQLite are listed in
:mod:`backend.domain.research`.
"""

from __future__ import annotations

from typing import Any, Callable, Optional, TypeVar

from backend.db.database import Database, Isolation
from backend.domain.identity.authorization import Capability
from backend.domain.identity.library_scope import LibraryScope, library_command, library_query
from backend.domain.identity.request_context import RequestContext
from backend.domain.research import positions
from backend.entity_ids import generate

T = TypeVar("T")

ID_PREFIX = "P"


def _command(db: Database, ctx: RequestContext, fn: Callable[[LibraryScope], T]) -> T:
    return library_command(db, ctx, Capability.EDIT_LIBRARY, fn, isolation=Isolation.READ_COMMITTED)


def catalog(db: Database, ctx: RequestContext) -> list[dict[str, Any]]:
    return library_query(db, ctx, positions.catalog)


def detail(db: Database, ctx: RequestContext, position_id: str) -> Optional[dict[str, Any]]:
    """The Position with its Arguments; ``None`` when absent."""
    pid = (position_id or "").strip()
    if not pid:
        return None
    return library_query(db, ctx, lambda scope: positions.detail(scope, pid))


def sync_state(db: Database, ctx: RequestContext, position_id: str) -> Optional[dict[str, Any]]:
    """The two field revisions; ``None`` when absent."""
    return library_query(db, ctx, lambda scope: positions.sync_state(scope, position_id))


def create(db: Database, ctx: RequestContext, name: Any, description: Any = "") -> dict[str, Any]:
    new_id = generate(ID_PREFIX)
    return _command(db, ctx, lambda scope: positions.create(scope, new_id, name, description))


def update(db: Database, ctx: RequestContext, position_id: str, *, name: Any = None,
           description: Any = None) -> dict[str, Any]:
    return _command(db, ctx, lambda scope: positions.update(scope, position_id, name, description))


def delete(db: Database, ctx: RequestContext, position_id: str) -> None:
    _command(db, ctx, lambda scope: positions.remove(scope, position_id))
