"""Search HTTP controller (PG-C3 #642).

``GET /api/search``: ``?q=`` keyword search, ``?tag=`` the Tag filter,
``?author=`` and ``?publisher=`` filters, and ``?any=1`` to OR one term
across them and add the Works whose PDF text matches ``q``. The answer is a
JSON array of Work summaries (``200``, no ETag), as before PG-C3.

The call goes to ``backend/domain/search/search_commands`` for the admitted
request's context on the served Library's database, so authorization stays in
``library_query``.

``POST /api/works/reindex-pdf-text`` queues the text rebuild (``force`` is
accepted and asks for nothing more: a rebuild re-extracts only what has no
current row).
"""

from __future__ import annotations

from backend.api.common import pdfs_dir
from backend.web.http import Request, Response, json_response
from backend.web.routing import route

PATH = "/api/search"

_ANY_ON = ("1", "true", "yes")


def search(req: Request) -> Response:
    from backend.domain.search import search_commands

    data = search_commands.search(
        *req.actor, pdfs_dir(req),
        q=req.query_value("q"),
        tag=req.query_value("tag"),
        author=req.query_value("author"),
        publisher=req.query_value("publisher"),
        any_mode=req.query_value("any") in _ANY_ON,
    )
    return json_response(req, 200, data)


def reindex_pdf_text(req: Request) -> Response:
    from backend.domain.search import search_commands

    summary = search_commands.queue_text_rebuild(*req.actor)
    return json_response(req, 200, {"status": "ok", **summary})


ROUTES = (
    route("GET", PATH, search),
    route("POST", "/api/works/reindex-pdf-text", reindex_pdf_text),
)
