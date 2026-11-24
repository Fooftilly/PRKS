"""Arguments HTTP controller (PG-C3 #642).

``/api/arguments`` (GET the catalog, optionally ``?kind=``, each Argument
with its ``mention_count``; POST create, ``201``), ``/api/arguments/:id``
(GET with its targets, citations, ``mention_count``, ``mentions`` and the
``verdicts``; PATCH name, kind and main text; DELETE), PUT
``/api/arguments/:id/sources`` and ``/api/arguments/:id/targets`` (the
whole list, as ``{"sources": [...]}``/``{"targets": [...]}``), ``GET /api/argument-verdicts`` and the revision state
``GET /api/arguments/:id/sync-state``. A ``ResearchError`` is its own
status with ``{error, code}``.

The calls go to ``backend/domain/research/argument_commands`` for the
admitted request's context on the served Library's database. The commands
open the transactions, so authorization stays in ``library_query`` and
``library_command``, and a write's maintenance and epoch fence in
``library_command``. The Research index is the database's, so a delete has
no mentions to remove.
The commands are imported when a route runs: the served app loads no
PostgreSQL module at import time (``tests/test_e2e_policy.py``).
"""

from __future__ import annotations

from urllib.parse import unquote

from backend.domain.research.rules import ResearchError
from backend.etags import etag_for_representation
from backend.web.http import Request, Response, json_response
from backend.web.routing import route


def _id(req: Request) -> str:
    return unquote(req.params["argument_id"])


def _refused(req: Request, error: ResearchError) -> Response:
    return json_response(req, error.http_status, {"error": str(error), "code": error.code})


def verdicts(req: Request) -> Response:
    from backend.domain.research import argument_commands

    return json_response(req, 200, argument_commands.verdicts(*req.actor))


def catalog(req: Request) -> Response:
    from backend.domain.research import argument_commands

    kind = req.query_value("kind").strip() or None
    try:
        items = argument_commands.catalog_with_mentions(*req.actor, kind)
    except ResearchError as e:
        return _refused(req, e)
    return json_response(req, 200, items)


def detail(req: Request) -> Response:
    from backend.domain.research import argument_commands

    item = argument_commands.detail_with_mentions(*req.actor, _id(req))
    if not item:
        return json_response(req, 404, {"error": "Argument not found."})
    return json_response(req, 200, item)


def sync_state(req: Request) -> Response:
    """``GET /api/arguments/:id/sync-state``."""
    from backend.domain.research import argument_commands

    # REVISIONS ONLY, aggregates included: the Argument detail already
    # carries its sources and its targets, and a second copy would be a
    # second thing to keep true.
    data = argument_commands.sync_state(*req.actor, _id(req))
    if data is None:
        return json_response(req, 404, {"error": "Argument not found"})
    return json_response(req, 200, data, etag=etag_for_representation("argument-state", data))


def create(req: Request) -> Response:
    from backend.domain.research import argument_commands

    data = req.data
    try:
        item = argument_commands.create(
            *req.actor,
            name=data.get("name"),
            kind=data.get("kind"),
            main_text=data.get("main_text", "") or "",
            sources=data.get("sources"),
            targets=data.get("targets"),
        )
    except ResearchError as e:
        return _refused(req, e)
    return json_response(req, 201, item)


def update(req: Request) -> Response:
    from backend.domain.research import argument_commands

    data = req.data
    try:
        item = argument_commands.update(
            *req.actor,
            _id(req),
            name=data.get("name") if "name" in data else None,
            kind=data.get("kind") if "kind" in data else None,
            main_text=data.get("main_text") if "main_text" in data else None,
        )
    except ResearchError as e:
        return _refused(req, e)
    return json_response(req, 200, item)


def replace_sources(req: Request) -> Response:
    from backend.domain.research import argument_commands

    try:
        item = argument_commands.replace_sources(*req.actor, _id(req), req.data.get("sources"))
    except ResearchError as e:
        return _refused(req, e)
    return json_response(req, 200, item)


def replace_targets(req: Request) -> Response:
    from backend.domain.research import argument_commands

    try:
        item = argument_commands.replace_targets(*req.actor, _id(req), req.data.get("targets"))
    except ResearchError as e:
        return _refused(req, e)
    return json_response(req, 200, item)


def delete(req: Request) -> Response:
    from backend.domain.research import argument_commands

    try:
        argument_commands.delete(*req.actor, _id(req))
    except ResearchError as e:
        return _refused(req, e)
    return json_response(req, 200, {"status": "deleted"})


ROUTES = (
    route("GET", "/api/argument-verdicts", verdicts),
    route("GET", "/api/arguments", catalog),
    route("POST", "/api/arguments", create),
    route("GET", "/api/arguments/{argument_id}", detail),
    route("PATCH", "/api/arguments/{argument_id}", update),
    route("DELETE", "/api/arguments/{argument_id}", delete),
    route("GET", "/api/arguments/{argument_id}/sync-state", sync_state),
    route("PUT", "/api/arguments/{argument_id}/sources", replace_sources),
    route("PUT", "/api/arguments/{argument_id}/targets", replace_targets),
)
