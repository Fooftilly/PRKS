"""The derived Research index reads a route makes for one actor (PG-C3 #642).

A Work's Research refs (``GET /api/works/:id`` and a Research Note save's
answer) and the Research Graph (``GET /api/research-graph``), each in one
:func:`~backend.domain.identity.library_scope.library_query` snapshot for the
admitted context. Before PG-C3 the routes made these reads through the bound
``PostgresResearchIndex`` with the context bound beside the call; the reads
themselves are :mod:`backend.domain.research.reindex` and
:mod:`backend.domain.research.graph`, unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict

from backend.db.database import Database
from backend.domain.identity.library_scope import library_query
from backend.domain.identity.request_context import RequestContext

if TYPE_CHECKING:
    from backend.research_graph import GraphSnapshot

def research_refs(db: Database, ctx: RequestContext, work_id: Any) -> Dict[str, Any]:
    """``{concepts, arguments, pending}``; empty lists for a Work that does not exist."""
    from backend.domain.research import reindex

    wid = str(work_id or "").strip()
    refs = library_query(db, ctx, lambda scope: reindex.research_refs(scope, wid)) if wid else None
    return refs if refs is not None else {"concepts": [], "arguments": [], "pending": False}


def graph(db: Database, ctx: RequestContext, *, include_people: bool = False) -> GraphSnapshot:
    """The Research Graph snapshot; raises ``GraphTooLargeError`` past the bounds."""
    from backend.domain.research import graph as research_graph

    return library_query(db, ctx, lambda scope: research_graph.snapshot(scope, include_people=bool(include_people)))
