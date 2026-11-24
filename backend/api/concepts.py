"""Concepts HTTP controller (PG-C3 #642).

``/api/concepts`` (GET the catalog, each Concept with its ``mention_count``;
POST create, ``201``), ``/api/concepts/:id`` (GET with its aliases,
parents, children, ``mention_count`` and ``mentions``; PATCH name and
definition; DELETE), ``PUT /api/concepts/:id/parents`` and ``PUT
/api/concepts/:id/aliases`` (the whole set, as ``{parent_ids}`` /
``{aliases}``), and the revision state ``GET
/api/concepts/:id/sync-state``. A ``ResearchError`` is its own status with
``{error, code}``.

The calls go to ``backend/domain/research/concept_commands`` for the
admitted request's context on the served Library's database. The commands
open the transactions, so authorization stays in ``library_query`` and
``library_command``, and a write's maintenance and epoch fence in
``library_command``.
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
    return unquote(req.params["concept_id"])


def _refused(req: Request, error: ResearchError) -> Response:
    return json_response(req, error.http_status, {"error": str(error), "code": error.code})


def catalog(req: Request) -> Response:
    from backend.domain.research import concept_commands

    return json_response(req, 200, concept_commands.catalog_with_mentions(*req.actor))


def detail(req: Request) -> Response:
    from backend.domain.research import concept_commands

    item = concept_commands.detail_with_mentions(*req.actor, _id(req))
    if not item:
        return json_response(req, 404, {"error": "Concept not found."})
    return json_response(req, 200, item)


def sync_state(req: Request) -> Response:
    """``GET /api/concepts/:id/sync-state``."""
    from backend.domain.research import concept_commands

    # Revisions, plus the two AGGREGATE values. A field revision is a
    # number, but an identity and a hierarchy are sets: a client measuring
    # an edit against them needs what it last acknowledged, and the Concept
    # detail carries `parents` as objects rather than ids.
    data = concept_commands.sync_state(*req.actor, _id(req))
    if data is None:
        return json_response(req, 404, {"error": "Concept not found"})
    return json_response(req, 200, data, etag=etag_for_representation("concept-state", data))


def create(req: Request) -> Response:
    from backend.domain.research import concept_commands

    data = req.data
    try:
        item = concept_commands.create(*req.actor, data.get("name"), data.get("description", "") or "")
    except ResearchError as e:
        return _refused(req, e)
    return json_response(req, 201, item)


def update(req: Request) -> Response:
    from backend.domain.research import concept_commands

    data = req.data
    try:
        item = concept_commands.update(
            *req.actor, _id(req),
            name=data.get("name") if "name" in data else None,
            description=data.get("description") if "description" in data else None,
        )
    except ResearchError as e:
        return _refused(req, e)
    return json_response(req, 200, item)


def replace_parents(req: Request) -> Response:
    from backend.domain.research import concept_commands

    try:
        item = concept_commands.replace_parents(*req.actor, _id(req), req.data.get("parent_ids"))
    except ResearchError as e:
        return _refused(req, e)
    return json_response(req, 200, item)


def replace_aliases(req: Request) -> Response:
    from backend.domain.research import concept_commands

    try:
        item = concept_commands.replace_aliases(*req.actor, _id(req), req.data.get("aliases"))
    except ResearchError as e:
        return _refused(req, e)
    return json_response(req, 200, item)


def delete(req: Request) -> Response:
    from backend.domain.research import concept_commands

    try:
        concept_commands.delete(*req.actor, _id(req))
    except ResearchError as e:
        return _refused(req, e)
    return json_response(req, 200, {"status": "deleted"})


ROUTES = (
    route("GET", "/api/concepts", catalog),
    route("POST", "/api/concepts", create),
    route("GET", "/api/concepts/{concept_id}", detail),
    route("PATCH", "/api/concepts/{concept_id}", update),
    route("DELETE", "/api/concepts/{concept_id}", delete),
    route("GET", "/api/concepts/{concept_id}/sync-state", sync_state),
    route("PUT", "/api/concepts/{concept_id}/parents", replace_parents),
    route("PUT", "/api/concepts/{concept_id}/aliases", replace_aliases),
)
