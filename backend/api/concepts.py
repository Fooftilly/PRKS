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

from backend.api_contract.operation import Operation, body, error, ok, revalidated
from backend.api_contract.organization import Done
from backend.api_contract.research import (
    ConceptAliasesRequest,
    ConceptCard,
    ConceptCreateRequest,
    ConceptDetail,
    ConceptParentsRequest,
    ConceptPatch,
    ConceptRead,
    ConceptState,
)
from backend.domain.research.rules import ResearchError
from backend.web.etags import etag_for_representation
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


_TAG = "concepts"
_ID = "/api/concepts/{concept_id}"
_INVALID = error("A refused value, with its code (invalid_name, invalid_text, invalid_aliases, "
                 "invalid_parents, nothing_to_update).")
_NOT_FOUND = error("No Concept with that id (not_found), or a parent that is not one (parent_not_found).")


def _write(op_id: str, summary: str, request: object, conflict: str) -> Operation:
    return Operation(op_id, summary, _TAG,
                     {200: ok(ConceptDetail, "The Concept as it now stands."), 400: _INVALID,
                      404: _NOT_FOUND, 409: error(conflict)},
                     request=body(request))


ROUTES = (
    route("GET", "/api/concepts", catalog, contract=Operation(
        "listConcepts", "Every Concept with its parents, aliases and counts", _TAG,
        {200: ok(list[ConceptCard], "By name.")})),
    route("POST", "/api/concepts", create, contract=Operation(
        "createConcept", "Create a Concept", _TAG,
        {201: ok(ConceptDetail, "The new Concept."), 400: _INVALID,
         409: error("The name is another Concept's name or alias (concept_exists).")},
        request=body(ConceptCreateRequest))),
    route("GET", _ID, detail, contract=Operation(
        "getConcept", "A Concept with its hierarchy, aliases and mentions", _TAG,
        {200: ok(ConceptRead, "The Concept."), 404: error("No Concept with that id.")})),
    route("PATCH", _ID, update, contract=_write(
        "updateConcept", "Rename a Concept or change its definition", ConceptPatch,
        "The new name is another Concept's name or alias (concept_exists, alias_conflict).")),
    route("DELETE", _ID, delete, contract=Operation(
        "deleteConcept", "Delete a Concept", _TAG,
        {200: ok(Done, "Deleted."), 404: error("No Concept with that id (not_found)."),
         409: error("A Research Note still references it (concept_in_use).")})),
    route("GET", _ID + "/sync-state", sync_state, contract=revalidated(
        "getConceptState", "A Concept's field revisions, identity and parent set", _TAG, ConceptState,
        "Values and revisions.", {404: error("No Concept with that id.")})),
    route("PUT", _ID + "/parents", replace_parents, contract=_write(
        "setConceptParents", "Replace a Concept's parent set", ConceptParentsRequest,
        "A parent would make a cycle (concept_cycle).")),
    route("PUT", _ID + "/aliases", replace_aliases, contract=_write(
        "setConceptAliases", "Replace a Concept's alias set", ConceptAliasesRequest,
        "An alias is another Concept's name or alias (concept_exists, alias_conflict).")),
)
