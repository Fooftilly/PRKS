"""The Research index and graph reads of the PostgreSQL transition facade (L-R-1 #634, PR 5).

``server.py`` reads mentions through the bound ``PRKSResearchIndex``
(``backend/research_index.py``, a separate SQLite file) and the graph
through ``ResearchGraphBuilder``, not through ``PRKSDatabase``, so these
reads are not facade methods. :class:`PostgresResearchIndex` stands where
that index object is used: its read methods keep the ``PRKSResearchIndex``
names, signatures and return shapes (the ``db`` argument is accepted and
unused: the reads come from the same database), and :meth:`build_graph`
answers what ``ResearchGraphBuilder().build(db, index, include_people=…)``
does. Each read runs in one ``library_query`` snapshot for the bound
:class:`~backend.domain.identity.request_context.RequestContext`, through
:mod:`backend.domain.research.reindex` and :mod:`backend.domain.research.graph`.

Differences from SQLite, each deliberate:

- :meth:`work_research_refs` also answers ``pending`` (the Research-ACK
  fence, §8.4), and leaves out a Concept deleted since the note was indexed
  (SQLite answers it with an empty name until the server removes its
  mentions); aliases are ordered by spelling (SQLite: storage order).
- Counts, backlinks and graph edges leave out the mentions of a Work
  deleted before its reindex ran and of a deleted Concept or Argument (SQLite
  removes those rows when the record goes); the per-record count maps hold
  only live Concepts and Arguments.
- ``derived_note_edges_available`` is always true: the index cannot be
  missing or corrupt apart from the canonical tables.

The index writes (``sync_work``, ``remove_*``, ``reconcile_all``) have no
counterpart: on PostgreSQL ``research.reindex_work`` keeps the index, and a
full rebuild is L-S-1's. Nothing in the running application constructs this
class yet (the route cutover, §15.3).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Dict, List, TypeVar

from backend.pg_facade.context import current_context

if TYPE_CHECKING:
    from backend.db.database import Database
    from backend.domain.identity.library_scope import LibraryScope
    from backend.research_graph import GraphSnapshot

T = TypeVar("T")


class PostgresResearchIndex:
    """``PRKSResearchIndex``'s reads (and the graph) for the bound Library."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def _read(self, fn: Callable[[LibraryScope], T]) -> T:
        from backend.domain.identity.library_scope import library_query

        return library_query(self._database, current_context(), fn)

    def mention_count_for_concept(self, concept_id: str) -> int:
        from backend.domain.research import reindex

        return self._read(lambda scope: reindex.mention_count(scope, "concept", concept_id))

    def mention_count_for_argument(self, argument_id: str) -> int:
        from backend.domain.research import reindex

        return self._read(lambda scope: reindex.mention_count(scope, "argument", argument_id))

    def concept_mention_counts(self) -> Dict[str, int]:
        from backend.domain.research import reindex

        return self._read(lambda scope: reindex.mention_counts(scope, "concept"))

    def argument_mention_counts(self) -> Dict[str, int]:
        from backend.domain.research import reindex

        return self._read(lambda scope: reindex.mention_counts(scope, "argument"))

    def work_research_refs(self, work_id: str, db: Any) -> Dict[str, Any]:
        """``{concepts, arguments, pending}``; empty lists for a Work that does not exist."""
        from backend.domain.research import reindex

        wid = (work_id or "").strip()
        refs = self._read(lambda scope: reindex.research_refs(scope, wid)) if wid else None
        return refs if refs is not None else {"concepts": [], "arguments": [], "pending": False}

    def concept_backlinks(self, concept_id: str, db: Any) -> List[dict]:
        from backend.domain.research import reindex

        cid = (concept_id or "").strip()
        return self._read(lambda scope: reindex.backlinks(scope, "concept", cid))

    def argument_backlinks(self, argument_id: str, db: Any) -> List[dict]:
        from backend.domain.research import reindex

        aid = (argument_id or "").strip()
        return self._read(lambda scope: reindex.backlinks(scope, "argument", aid))

    def build_graph(self, *, include_people: bool = False) -> GraphSnapshot:
        """``ResearchGraphBuilder().build``; raises ``GraphTooLargeError`` past the bounds."""
        from backend.domain.research import graph

        return self._read(lambda scope: graph.snapshot(scope, include_people=bool(include_people)))
