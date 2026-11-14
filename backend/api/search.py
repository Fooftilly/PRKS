"""Search HTTP controller (PG-C3 #642).

``GET /api/search``: ``?q=`` keyword search, ``?tag=`` the Tag filter,
``?author=`` and ``?publisher=`` filters, and ``?any=1`` to OR one term
across them and add the Works whose PDF text matches ``q``. The answer is a
JSON array of Work summaries (``200``, no ETag), as before PG-C3.

The call goes to ``backend/domain/search/search_commands`` for the admitted
request's context on the served Library's database, so authorization stays in
``library_query``. ``server.py`` calls :func:`handle_get` after host/origin
and library-access checks; it returns ``True`` when the path is this
family's, else ``False`` so dispatch continues.
"""

from __future__ import annotations

from typing import Any

PATH = "/api/search"

_ANY_ON = ("1", "true", "yes")


def _commands() -> Any:
    """``backend.domain.search.search_commands``, imported when a route runs.

    The served app loads no PostgreSQL module at import time
    (``tests/test_e2e_policy.py``).
    """
    from backend.domain.search import search_commands

    return search_commands


def _first(query: Any, name: str) -> str:
    return (query.get(name) or [""])[0]


def handle_get(handler: Any, db: Any, path: str, query: Any) -> bool:
    if path != PATH:
        return False
    ctx = getattr(handler, "_prks_request_context", None)
    if ctx is None:
        raise RuntimeError("a Search route ran without an admitted request context")
    if db.storage is None:
        raise RuntimeError("this PostgreSQL library has no data root")
    data = _commands().search(
        db.database, ctx, db.storage.pdfs_dir,
        q=_first(query, "q"),
        tag=_first(query, "tag"),
        author=_first(query, "author"),
        publisher=_first(query, "publisher"),
        any_mode=_first(query, "any") in _ANY_ON,
    )
    handler.send_json(200, data)
    return True
