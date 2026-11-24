"""Positions HTTP controller (PG-C3 #642).

``/api/positions`` (GET the catalog, POST create, ``201``),
``/api/positions/:id`` (GET with its Arguments, PATCH name and description,
DELETE) and the revision state ``GET /api/positions/:id/sync-state``. The
wire shapes are ``backend/api_contract/positions.py``: a malformed body is
``400`` with the contract's envelope, and a ``ResearchError`` is its own
status with ``research_error_envelope``. A write's answer is not validated
after the commit (``docs/api-contract-boundary.md``).

The calls go to ``backend/domain/research/position_commands`` for the
admitted request's context on the served Library's database. The commands
open the transactions, so authorization stays in ``library_query`` and
``library_command``, and a write's maintenance and epoch fence in
``library_command``.
The commands are imported when a route runs: the served app loads no
PostgreSQL module at import time (``tests/test_e2e_policy.py``).
"""

from __future__ import annotations

from urllib.parse import unquote

from backend.api_contract.boundary import dump_response
from backend.api_contract.errors import research_error_envelope
from backend.api_contract.positions import (
    PositionCreateRequest,
    PositionDeleted,
    PositionDetail,
    PositionSummary,
    PositionSyncState,
    PositionUpdateRequest,
    parse_position_request,
)
from backend.domain.research.rules import ResearchError
from backend.web.etags import etag_for_representation
from backend.web.http import Request, Response, json_response
from backend.web.routing import route


def _id(req: Request) -> str:
    return unquote(req.params["position_id"])


def _refused(req: Request, error: ResearchError) -> Response:
    return json_response(req, error.http_status, research_error_envelope(message=str(error), code=error.code))


def catalog(req: Request) -> Response:
    from backend.domain.research import position_commands

    return json_response(req, 200, dump_response(PositionSummary, position_commands.catalog(*req.actor)))


def detail(req: Request) -> Response:
    from backend.domain.research import position_commands

    item = position_commands.detail(*req.actor, _id(req))
    if item:
        return json_response(req, 200, dump_response(PositionDetail, item))
    return json_response(req, 404, research_error_envelope(message="Position not found."))


def sync_state(req: Request) -> Response:
    """``GET /api/positions/:id/sync-state``."""
    from backend.domain.research import position_commands

    # REVISIONS ONLY: the Position detail already carries both values, and a
    # second copy would be a second thing to keep true.
    data = position_commands.sync_state(*req.actor, _id(req))
    if data is None:
        return json_response(req, 404, research_error_envelope(message="Position not found"))
    body = dump_response(PositionSyncState, data)
    return json_response(req, 200, body, etag=etag_for_representation("position-state", body))


def create(req: Request) -> Response:
    from backend.domain.research import position_commands

    parsed, err = parse_position_request(PositionCreateRequest, req.data)
    if err is not None or parsed is None:
        return json_response(req, 400, err)
    try:
        item = position_commands.create(*req.actor, parsed.name, parsed.description)
    except ResearchError as e:
        return _refused(req, e)
    # Domain already committed; do not dump_response here (see
    # docs/api-contract-boundary.md — post-commit response validation).
    return json_response(req, 201, item)


def update(req: Request) -> Response:
    from backend.domain.research import position_commands

    parsed, err = parse_position_request(PositionUpdateRequest, req.data)
    if err is not None or parsed is None:
        return json_response(req, 400, err)
    try:
        item = position_commands.update(*req.actor, _id(req), **parsed.domain_field_kwargs())
    except ResearchError as e:
        return _refused(req, e)
    # Domain already committed; do not dump_response here (see
    # docs/api-contract-boundary.md — post-commit response validation).
    return json_response(req, 200, item)


def delete(req: Request) -> Response:
    from backend.domain.research import position_commands

    try:
        position_commands.delete(*req.actor, _id(req))
    except ResearchError as e:
        return _refused(req, e)
    return json_response(req, 200, dump_response(PositionDeleted, {"status": "deleted"}))


ROUTES = (
    route("GET", "/api/positions", catalog),
    route("POST", "/api/positions", create),
    route("GET", "/api/positions/{position_id}", detail),
    route("PATCH", "/api/positions/{position_id}", update),
    route("DELETE", "/api/positions/{position_id}", delete),
    route("GET", "/api/positions/{position_id}/sync-state", sync_state),
)
