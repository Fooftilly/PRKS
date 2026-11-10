"""Search family of the PostgreSQL transition facade (L-S-1).

Owned by the Search lane (``docs/postgresql-architecture.md`` §15.2): Work
search, catalogue filters and Saved View execution. Each ported method keeps
the ``PRKSDatabase`` signature and return shape and is registered by one
line, ``@ported`` from :mod:`backend.pg_facade.registry`, directly above it.

Each method runs in one read transaction, so its id sets and the summaries
it returns come from one snapshot. The matching itself is
:mod:`backend.domain.search.queries`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, List, Optional, TypeVar

from backend.pg_facade.context import current_context
from backend.pg_facade.registry import FacadeMixin, ported

if TYPE_CHECKING:
    from backend.domain.identity.library_scope import LibraryScope

T = TypeVar("T")


class SearchMixin(FacadeMixin):
    """Work search, its author and publisher filters, and the Tag filter."""

    def _search_read(self, fn: Callable[[LibraryScope], T]) -> T:
        from backend.domain.identity.library_scope import library_query

        return library_query(self._database, current_context(), fn)

    def _summaries(self, scope: LibraryScope, work_ids: List[str], *, with_folder: bool = True) -> List[dict]:
        """Work summaries in ``work_ids`` order (``get_work_summaries_by_ids_ordered``'s shape).

        ``with_folder=False`` is the shape SQLite's Tag filter selects (no ``folder_id``).
        """
        from backend.domain.works import projection, queries

        by_id = queries.works_by_ids(scope, work_ids)
        return [projection.work_summary(by_id[i], with_folder=with_folder) for i in work_ids if i in by_id]

    def _finish_summaries(self, rows: List[dict]) -> List[dict]:
        from backend.domain.works.rules import finish_work_summary_rows

        if self._storage is None:
            raise RuntimeError("this PostgreSQL library has no data root")
        finish_work_summary_rows(rows, self._storage.pdfs_dir)
        return rows

    @ported
    def work_ids_matching_author(self, author: str) -> List[str]:
        from backend.domain.search import queries

        return self._search_read(lambda scope: queries.author_work_ids(scope, author))

    @ported
    def work_ids_matching_publisher(self, pub: str) -> List[str]:
        from backend.domain.search import queries

        return self._search_read(lambda scope: queries.publisher_work_ids(scope, pub))

    @ported
    def search_works(
        self,
        search_term: str,
        author_filter: str = "",
        publisher_filter: str = "",
    ) -> List[dict]:
        """Keyword search, optionally restricted by author and publisher (AND when both)."""
        q = (search_term or "").strip()
        author = (author_filter or "").strip()
        publisher = (publisher_filter or "").strip()
        if not q and not author and not publisher:
            return []
        rows = self._search_read(lambda scope: self._search(scope, q, author, publisher))
        return self._finish_summaries(rows)

    def _search(self, scope: LibraryScope, q: str, author: str, publisher: str) -> List[dict]:
        from backend.domain.search import queries

        allowed: Optional[set[str]] = None
        for matching, needle in ((queries.author_work_ids, author), (queries.publisher_work_ids, publisher)):
            if not needle:
                continue
            found = set(matching(scope, needle))
            allowed = found if allowed is None else allowed & found
            if not allowed:
                return []
        if q:
            ids = queries.keyword_work_ids(scope, q)
            if allowed is not None:
                ids = [wid for wid in ids if wid in allowed]
        else:
            ids = queries.by_recency(scope, sorted(allowed or ()))
        return self._summaries(scope, ids)

    @ported
    def search_works_any(self, term: str) -> List[dict]:
        """One term, OR-ed across the keyword search, the author filter and the publisher filter.

        Keyword hits come first in their order, then the Works only an author
        or publisher match adds, most recently updated first.
        """
        t = (term or "").strip()
        if not t:
            return []
        rows = self._search_read(lambda scope: self._search_any(scope, t))
        return self._finish_summaries(rows)

    def _search_any(self, scope: LibraryScope, term: str) -> List[dict]:
        from backend.domain.search import queries

        ids = queries.keyword_work_ids(scope, term)
        seen = set(ids)
        extra = (set(queries.author_work_ids(scope, term))
                 | set(queries.publisher_work_ids(scope, term))) - seen
        return self._summaries(scope, ids + queries.by_recency(scope, sorted(extra)))

    @ported
    def get_works_by_tag_name(self, tag_name: str) -> List[dict]:
        """The Works tagged with the Tag whose name or alias is ``tag_name``, newest first."""
        from backend.domain.search import queries

        name = (tag_name or "").strip()
        if not name:
            return []
        rows = self._search_read(
            lambda scope: self._summaries(scope, queries.tagged_work_ids(scope, name), with_folder=False))
        return self._finish_summaries(rows)
