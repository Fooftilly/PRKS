"""The publisher operations for one actor: what ``backend/api/publishers.py`` calls (PG-C3 #642).

A read runs in one :func:`~backend.domain.identity.library_scope.library_query`
snapshot, authorized for ``READ_LIBRARY`` (reads are not fenced by epoch); a
write is one :func:`~backend.domain.identity.library_scope.library_command`
transaction needing ``EDIT_LIBRARY``, fenced by maintenance and the epoch, at
READ COMMITTED (publishers own no revision scope; see
:mod:`backend.domain.people.publishers`). Refusals are ``ValueError`` with
SQLite's message, and ``AccessRefused``.
"""

from __future__ import annotations

from typing import Any, Callable, Optional, TypeVar

from backend.db.database import Database
from backend.domain.identity.authorization import Capability
from backend.domain.identity.library_scope import LibraryScope, library_command, library_query
from backend.domain.identity.request_context import RequestContext
from backend.domain.people import publishers
from backend.entity_ids import generate

T = TypeVar("T")

PUBLISHER_ID_PREFIX = "R"
ALIAS_ID_PREFIX = "M"


def _command(db: Database, ctx: RequestContext, fn: Callable[[LibraryScope], T]) -> T:
    return library_command(db, ctx, Capability.EDIT_LIBRARY, fn)


def in_use(db: Database, ctx: RequestContext) -> list[dict[str, Any]]:
    """Every publisher with its aliases and Work count."""
    return library_query(db, ctx, publishers.publishers_in_use)


def create(db: Database, ctx: RequestContext, name: Optional[str]) -> dict[str, Any]:
    """``{"id", "name", "existed"}``; a name already held is that publisher."""
    new_id = generate(PUBLISHER_ID_PREFIX)
    return _command(db, ctx, lambda scope: publishers.create_publisher(scope, new_id, name))


def add_alias(db: Database, ctx: RequestContext, publisher_id: str, alias: Optional[str]) -> None:
    new_id = generate(ALIAS_ID_PREFIX)
    _command(db, ctx, lambda scope: publishers.add_alias(scope, new_id, publisher_id, alias))


def delete_alias(db: Database, ctx: RequestContext, publisher_id: str, alias: Optional[str]) -> bool:
    """Whether an alias with that identity key was removed."""
    return _command(db, ctx, lambda scope: publishers.delete_alias(scope, publisher_id, alias))


def delete(db: Database, ctx: RequestContext, publisher_id: str) -> None:
    """The publisher, its aliases and its keys; an absent one is a no-op."""
    _command(db, ctx, lambda scope: publishers.delete_publisher(scope, publisher_id))
