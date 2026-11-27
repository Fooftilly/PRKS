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

from backend.api_contract.operation import Operation, Param, body, ok
from backend.api_contract.works import TextRebuildQueued, TextRebuildRequest, WorkSummary
from backend.web.http import Request, Response, json_response
from backend.web.routing import route

PATH = "/api/search"

_ANY_ON = ("1", "true", "yes")


def search(req: Request) -> Response:
    from backend.domain.search import search_commands

    data = search_commands.search(
        *req.actor,
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


def _filter(name: str, description: str) -> Param:
    return Param(name, description=description)


ROUTES = (
    route("GET", PATH, search, contract=Operation(
        "searchWorks", "Search the Works by keywords, Tag, author or publisher", "works",
        {200: ok(list[WorkSummary], "The matching Work summaries (no ETag).")},
        params=(_filter("q", "Keywords."), _filter("tag", "A Tag name."),
                _filter("author", "An author name."), _filter("publisher", "A publisher name."),
                Param("any", schema={"type": "string"},
                      description="``1``, ``true`` or ``yes``: OR one term across the filters and add "
                                  "the Works whose PDF text matches ``q`` and the author and publisher "
                                  "filters. Ignored with ``tag``: the Tag filter wins.")))),
    route("POST", "/api/works/reindex-pdf-text", reindex_pdf_text, contract=Operation(
        "queuePdfTextRebuild", "Queue the rebuild of the PDF text index", "works",
        {200: ok(TextRebuildQueued, "Queued, or one already was; the counters are zero.")},
        request=body(TextRebuildRequest, required=False))),
)
