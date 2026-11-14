"""Work search for one actor: what ``backend/api/search.py`` calls (PG-C3 #642).

``GET /api/search`` in one :func:`~backend.domain.identity.library_scope.library_query`
snapshot, authorized for ``READ_LIBRARY``: the Tag filter, the keyword
search with its author and publisher filters (AND), the any-mode search (one
term OR-ed across the keyword, author and publisher matches) and any mode's
PDF text hits. The matching itself is :mod:`backend.domain.search.queries`
and :mod:`backend.domain.search.text`. Before PG-C3 the route made one facade
call per step, each its own snapshot; it now reads them all from one.

The rows are Work summaries (``projection.work_summary``) in result order,
finished outside the transaction with the on-disk PDF size
(``finish_work_summary_rows``). The Tag filter's rows carry no
``folder_id``, which is the shape SQLite's Tag filter selected.
"""

from __future__ import annotations

from typing import Any, List, Optional

from backend.db.database import Database
from backend.domain.identity.library_scope import LibraryScope, library_query
from backend.domain.identity.request_context import RequestContext

PDF_TEXT_LIMIT = 2000


def summaries(scope: LibraryScope, work_ids: List[str], *, with_folder: bool = True) -> List[dict]:
    """Work summaries in ``work_ids`` order; an id with no Work is skipped."""
    from backend.domain.works import projection, queries

    by_id = queries.works_by_ids(scope, work_ids)
    return [projection.work_summary(by_id[i], with_folder=with_folder) for i in work_ids if i in by_id]


def finish(rows: List[dict], pdfs_dir: str) -> List[dict]:
    """``rows`` with each managed PDF's size on disk (outside any transaction)."""
    from backend.domain.works.rules import finish_work_summary_rows

    finish_work_summary_rows(rows, pdfs_dir)
    return rows


def keyword_rows(scope: LibraryScope, search_term: str, author: str = "", publisher: str = "") -> List[dict]:
    """Keyword search, optionally restricted by author and publisher (AND when both).

    With no keyword the author and publisher matches alone are the result,
    most recently updated first. Nothing to match is no rows.
    """
    from backend.domain.search import queries

    q = (search_term or "").strip()
    author = (author or "").strip()
    publisher = (publisher or "").strip()
    if not q and not author and not publisher:
        return []
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
    return summaries(scope, ids)


def any_rows(scope: LibraryScope, term: str) -> List[dict]:
    """One term, OR-ed across the keyword search, the author filter and the publisher filter.

    Keyword hits come first in their order, then the Works only an author or
    publisher match adds, most recently updated first.
    """
    from backend.domain.search import queries

    t = (term or "").strip()
    if not t:
        return []
    ids = queries.keyword_work_ids(scope, t)
    seen = set(ids)
    extra = (set(queries.author_work_ids(scope, t)) | set(queries.publisher_work_ids(scope, t))) - seen
    return summaries(scope, ids + queries.by_recency(scope, sorted(extra)))


def tagged_rows(scope: LibraryScope, tag_name: str) -> List[dict]:
    """The Works tagged with the Tag whose name or alias is ``tag_name``, newest first."""
    from backend.domain.search import queries

    name = (tag_name or "").strip()
    if not name:
        return []
    return summaries(scope, queries.tagged_work_ids(scope, name), with_folder=False)


def _allowed(scope: LibraryScope, author: str, publisher: str) -> tuple[Optional[set[str]], Optional[set[str]]]:
    """The author and publisher matches as id sets, ``None`` for a blank filter."""
    from backend.domain.search import queries

    author_ids = set(queries.author_work_ids(scope, author.strip())) if author and author.strip() else None
    publisher_ids = (set(queries.publisher_work_ids(scope, publisher.strip()))
                     if publisher and publisher.strip() else None)
    return author_ids, publisher_ids


def _search(scope: LibraryScope, q: str, tag: str, author: str, publisher: str, any_mode: bool) -> List[dict]:
    from backend.domain.search import text

    if tag:
        rows = tagged_rows(scope, tag)
        author_ids, publisher_ids = _allowed(scope, author, publisher)
        if author_ids is not None:
            rows = [w for w in rows if w.get("id") in author_ids]
        if publisher_ids is not None:
            rows = [w for w in rows if w.get("id") in publisher_ids]
        return rows
    if any_mode:
        rows = any_rows(scope, (q or author or publisher or "").strip())
    else:
        rows = keyword_rows(scope, q, author.strip() if author else "", publisher.strip() if publisher else "")
    if not (any_mode and q and str(q).strip()):
        return rows
    text_ids = text.pdf_text_work_ids(scope, q, PDF_TEXT_LIMIT)
    if not text_ids:
        return rows
    existing = {str(w.get("id")) for w in rows if w.get("id")}
    author_ids, publisher_ids = _allowed(scope, author, publisher)
    extra_ids = [
        wid for wid in text_ids
        if wid not in existing
        and (author_ids is None or wid in author_ids)
        and (publisher_ids is None or wid in publisher_ids)
    ]
    if extra_ids:
        rows.extend(summaries(scope, [str(wid).strip() for wid in extra_ids if str(wid).strip()]))
    return rows


def search(db: Database, ctx: RequestContext, pdfs_dir: str, *, q: str = "", tag: str = "", author: str = "",
           publisher: str = "", any_mode: bool = False) -> List[Any]:
    """``GET /api/search``'s answer: Work summaries in result order.

    ``tag`` selects the Tag filter, narrowed by ``author`` and ``publisher``.
    Otherwise ``any_mode`` ORs one term (``q``, else ``author``, else
    ``publisher``) across the keyword, author and publisher matches, and adds
    the Works whose PDF text matches ``q`` (after the author and publisher
    filters); without it, ``q`` is the keyword search ANDed with the filters.
    """
    rows = library_query(db, ctx, lambda scope: _search(scope, q, tag, author, publisher, any_mode))
    return finish(rows, pdfs_dir)
