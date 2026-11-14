"""The Argument operations for one actor: what ``backend/api/arguments.py`` calls (PG-C3 #642).

Arguments and Stances with their targets, citations, verdicts and revision
state, and the mentions the Research index holds for them. A read runs in
one :func:`~backend.domain.identity.library_scope.library_query` snapshot,
authorized for ``READ_LIBRARY`` (reads are not fenced by epoch); a write is
one :func:`~backend.domain.identity.library_scope.library_command`
transaction needing ``EDIT_LIBRARY``, fenced by maintenance and the epoch.
A write that adds a citation runs SERIALIZABLE (§7.1: ``argument_sources``
is a peer table of the Work merge), the others READ COMMITTED. Refusals are
SQLite's ``ResearchError`` codes, messages and statuses, and
``AccessRefused``; the differences from SQLite are listed in
:mod:`backend.domain.research`.

The index is kept by the database (a note save enqueues
``research.reindex_work``) and its reads join the live Arguments, so a
deleted Argument has no mentions and a delete has nothing to clean up.
"""

from __future__ import annotations

from typing import Any, Callable, Optional, TypeVar

from backend.db.database import Database, Isolation
from backend.domain.identity.authorization import Capability
from backend.domain.identity.library_scope import LibraryScope, library_command, library_query
from backend.domain.identity.request_context import RequestContext
from backend.domain.research import arguments, reindex
from backend.entity_ids import generate

T = TypeVar("T")

ID_PREFIX = "A"


def _command(db: Database, ctx: RequestContext, fn: Callable[[LibraryScope], T], *,
             serializable: bool = False) -> T:
    isolation = Isolation.SERIALIZABLE if serializable else Isolation.READ_COMMITTED
    return library_command(db, ctx, Capability.EDIT_LIBRARY, fn, isolation=isolation)


# -- Reads ---------------------------------------------------------------------
def verdicts(db: Database, ctx: RequestContext) -> list[dict[str, Any]]:
    """Every Argument verdict, in order."""
    return library_query(db, ctx, arguments.verdicts)


def catalog(db: Database, ctx: RequestContext, kind: Optional[str] = None) -> list[dict[str, Any]]:
    """Every Argument (of ``kind`` when given); a bad ``kind`` refuses with ``ResearchError``."""
    return library_query(db, ctx, lambda scope: arguments.catalog(scope, kind))


def catalog_with_mentions(db: Database, ctx: RequestContext, kind: Optional[str] = None) -> list[dict[str, Any]]:
    """:func:`catalog`, each Argument with its ``mention_count``, in one snapshot."""
    def read(scope: LibraryScope) -> list[dict[str, Any]]:
        items = arguments.catalog(scope, kind)
        counts = reindex.mention_counts(scope, "argument")
        for item in items:
            item["mention_count"] = int(counts.get(item["id"], 0))
        return items

    return library_query(db, ctx, read)


def detail(db: Database, ctx: RequestContext, argument_id: str) -> Optional[dict[str, Any]]:
    """The Argument with its targets and citations; ``None`` when absent."""
    aid = (argument_id or "").strip()
    if not aid:
        return None
    return library_query(db, ctx, lambda scope: arguments.detail(scope, aid))


def detail_with_mentions(db: Database, ctx: RequestContext, argument_id: str) -> Optional[dict[str, Any]]:
    """:func:`detail` with ``mention_count``, ``mentions`` (backlinks) and ``verdicts``, in one snapshot."""
    aid = (argument_id or "").strip()
    if not aid:
        return None

    def read(scope: LibraryScope) -> Optional[dict[str, Any]]:
        item = arguments.detail(scope, aid)
        if not item:
            return None
        item["mention_count"] = reindex.mention_count(scope, "argument", aid)
        item["mentions"] = reindex.backlinks(scope, "argument", aid)
        item["verdicts"] = arguments.verdicts(scope)
        return item

    return library_query(db, ctx, read)


def sync_state(db: Database, ctx: RequestContext, argument_id: str) -> Optional[dict[str, Any]]:
    """Field revisions plus the sources and targets aggregates; ``None`` when absent."""
    return library_query(db, ctx, lambda scope: arguments.sync_state(scope, argument_id))


# -- Writes --------------------------------------------------------------------
def create(db: Database, ctx: RequestContext, *, name: Any, kind: Any, main_text: Any = "",
           sources: Any = None, targets: Any = None) -> dict[str, Any]:
    new_id = generate(ID_PREFIX)
    return _command(db, ctx, lambda scope: arguments.create(scope, new_id, name, kind, main_text, sources,
                                                            targets),
                    serializable=True)


def update(db: Database, ctx: RequestContext, argument_id: str, *, name: Any = None, kind: Any = None,
           main_text: Any = None) -> dict[str, Any]:
    return _command(db, ctx, lambda scope: arguments.update(scope, argument_id, name, kind, main_text))


def replace_sources(db: Database, ctx: RequestContext, argument_id: str, sources: Any) -> dict[str, Any]:
    return _command(db, ctx, lambda scope: arguments.replace_sources(scope, argument_id, sources),
                    serializable=True)


def replace_targets(db: Database, ctx: RequestContext, argument_id: str, targets: Any) -> dict[str, Any]:
    return _command(db, ctx, lambda scope: arguments.replace_targets(scope, argument_id, targets))


def delete(db: Database, ctx: RequestContext, argument_id: str) -> None:
    _command(db, ctx, lambda scope: arguments.remove(scope, argument_id))
