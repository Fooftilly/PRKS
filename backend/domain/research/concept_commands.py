"""The Concept operations for one actor: what ``backend/api/concepts.py`` calls (PG-C3 #642).

Concepts, their aliases and hierarchy, their revision state, and the
mentions the Research index holds for them. A read runs in one
:func:`~backend.domain.identity.library_scope.library_query` snapshot,
authorized for ``READ_LIBRARY`` (reads are not fenced by epoch); a write is
one READ COMMITTED :func:`~backend.domain.identity.library_scope.library_command`
transaction needing ``EDIT_LIBRARY``, fenced by maintenance and the epoch
(§7.1: a Concept's uniqueness is decided by its key index). A write that
lost a key to a concurrent writer on that index runs once more, reads the
winner and refuses with the rule's own ``ResearchError``. Refusals are
SQLite's ``ResearchError`` codes, messages and statuses, and
``AccessRefused``; the differences from SQLite are listed in
:mod:`backend.domain.research`.

The index is kept by the database (a note save enqueues
``research.reindex_work``) and its reads join the live Concepts, so a
deleted Concept has no mentions and a delete has nothing to clean up.
"""

from __future__ import annotations

from typing import Any, Callable, Optional, TypeVar

from backend.db.database import Database, Isolation
from backend.db.errors import ConstraintViolationError
from backend.domain.identity.authorization import Capability
from backend.domain.identity.library_scope import LibraryScope, library_command, library_query
from backend.domain.identity.request_context import RequestContext
from backend.domain.research import concepts, reindex
from backend.entity_ids import generate

T = TypeVar("T")

ID_PREFIX = "C"


def _command(db: Database, ctx: RequestContext, fn: Callable[[LibraryScope], T]) -> T:
    def run() -> T:
        return library_command(db, ctx, Capability.EDIT_LIBRARY, fn, isolation=Isolation.READ_COMMITTED)

    try:
        return run()
    except ConstraintViolationError as error:
        if error.constraint != concepts.KEY_INDEX:
            raise
        return run()


# -- Reads ---------------------------------------------------------------------
def catalog(db: Database, ctx: RequestContext) -> list[dict[str, Any]]:
    """Every Concept by name, with its child count, parents and aliases."""
    return library_query(db, ctx, concepts.catalog)


def detail(db: Database, ctx: RequestContext, concept_id: str) -> Optional[dict[str, Any]]:
    """The Concept with its aliases, parents and children; ``None`` when absent."""
    cid = (concept_id or "").strip()
    if not cid:
        return None
    return library_query(db, ctx, lambda scope: concepts.detail(scope, cid))


def catalog_with_mentions(db: Database, ctx: RequestContext) -> list[dict[str, Any]]:
    """:func:`catalog`, each Concept with its ``mention_count``, in one snapshot."""
    def read(scope: LibraryScope) -> list[dict[str, Any]]:
        items = concepts.catalog(scope)
        counts = reindex.mention_counts(scope, "concept")
        for item in items:
            item["mention_count"] = int(counts.get(item["id"], 0))
        return items

    return library_query(db, ctx, read)


def detail_with_mentions(db: Database, ctx: RequestContext, concept_id: str) -> Optional[dict[str, Any]]:
    """:func:`detail` with ``mention_count`` and ``mentions`` (backlinks), in one snapshot."""
    cid = (concept_id or "").strip()
    if not cid:
        return None

    def read(scope: LibraryScope) -> Optional[dict[str, Any]]:
        item = concepts.detail(scope, cid)
        if not item:
            return None
        item["mention_count"] = reindex.mention_count(scope, "concept", cid)
        item["mentions"] = reindex.backlinks(scope, "concept", cid)
        return item

    return library_query(db, ctx, read)


def sync_state(db: Database, ctx: RequestContext, concept_id: str) -> Optional[dict[str, Any]]:
    """Field revisions plus the identity and parents aggregates; ``None`` when absent."""
    return library_query(db, ctx, lambda scope: concepts.sync_state(scope, concept_id))


# -- Writes --------------------------------------------------------------------
def create(db: Database, ctx: RequestContext, name: Any, description: Any = "") -> dict[str, Any]:
    new_id = generate(ID_PREFIX)
    return _command(db, ctx, lambda scope: concepts.create(scope, new_id, name, description))


def update(db: Database, ctx: RequestContext, concept_id: str, *, name: Any = None,
           description: Any = None) -> dict[str, Any]:
    return _command(db, ctx, lambda scope: concepts.update(scope, concept_id, name, description))


def replace_aliases(db: Database, ctx: RequestContext, concept_id: str, aliases: Any) -> dict[str, Any]:
    return _command(db, ctx, lambda scope: concepts.replace_aliases(scope, concept_id, aliases))


def replace_parents(db: Database, ctx: RequestContext, concept_id: str, parent_ids: Any) -> dict[str, Any]:
    return _command(db, ctx, lambda scope: concepts.replace_parents(scope, concept_id, parent_ids))


def delete(db: Database, ctx: RequestContext, concept_id: str) -> None:
    _command(db, ctx, lambda scope: concepts.remove(scope, concept_id))
