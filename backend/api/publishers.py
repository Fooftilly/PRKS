"""Publishers HTTP controller: ``/api/publishers`` and its aliases (PG-C3 #642).

``GET /api/publishers?used=1`` lists every publisher with its aliases and
Work count (without ``used`` the answer is ``[]``); ``POST`` creates one or
answers the publisher already named so; ``DELETE /api/publishers/:id``
removes one. ``POST``/``DELETE /api/publishers/:id/aliases`` add and remove
an alias (the alias to remove is the ``alias`` query parameter). The wire
shapes are ``backend/api_contract/publishers.py``; a domain refusal
(``ValueError``) is ``400`` with its message.

The calls go to ``backend/domain/people/publisher_commands`` for the
admitted request's context on the served Library's database. The commands
open the transactions, so authorization stays in ``library_query`` and
``library_command``, and a write's maintenance and epoch fence in
``library_command``.
The commands are imported when a route runs: the served app loads no
PostgreSQL module at import time (``tests/test_e2e_policy.py``).
"""

from __future__ import annotations

from backend.api_contract.boundary import dump_response, parse_request
from backend.api_contract.publishers import (
    PublisherAliasRequest,
    PublisherCreateRequest,
    PublisherDeleted,
    PublisherInUse,
)
from backend.web.http import Request, Response, json_response
from backend.web.routing import route


def in_use(req: Request) -> Response:
    from backend.domain.people import publisher_commands

    if req.query_value("used") in ("1", "true", "yes"):
        return json_response(req, 200, dump_response(PublisherInUse, publisher_commands.in_use(*req.actor)))
    return json_response(req, 200, [])


def create(req: Request) -> Response:
    from backend.domain.people import publisher_commands

    parsed, err = parse_request(PublisherCreateRequest, req.data)
    if err is not None or parsed is None:
        return json_response(req, 400, err)
    try:
        # Domain already committed on return; send its dict as is
        # (docs/api-contract-boundary.md).
        out = publisher_commands.create(*req.actor, parsed.name)
    except ValueError as e:
        return json_response(req, 400, {"error": str(e)})
    return json_response(req, 200, out)


def add_alias(req: Request) -> Response:
    from backend.domain.people import publisher_commands

    parsed, err = parse_request(PublisherAliasRequest, req.data)
    if err is not None or parsed is None:
        return json_response(req, 400, err)
    try:
        publisher_commands.add_alias(*req.actor, req.params["publisher_id"], parsed.alias.strip())
    except ValueError as e:
        return json_response(req, 400, {"error": str(e)})
    return json_response(req, 200, {"status": "added"})


def delete_alias(req: Request) -> Response:
    from backend.domain.people import publisher_commands

    # parse_qs already decoded the value once; a second unquote mangled
    # aliases that contain "%" (and turned "%2500" into NUL).
    alias = req.query_value("alias")
    if not alias.strip():
        return json_response(req, 400, {"error": "missing alias"})
    if publisher_commands.delete_alias(*req.actor, req.params["publisher_id"], alias):
        return json_response(req, 200, dump_response(PublisherDeleted, {"status": "deleted"}))
    return json_response(req, 404, {"error": "alias not found"})


def delete(req: Request) -> Response:
    from backend.domain.people import publisher_commands

    publisher_commands.delete(*req.actor, req.params["publisher_id"])
    return json_response(req, 200, dump_response(PublisherDeleted, {"status": "deleted"}))


ROUTES = (
    route("GET", "/api/publishers", in_use),
    route("POST", "/api/publishers", create),
    route("DELETE", "/api/publishers/{publisher_id}", delete),
    route("POST", "/api/publishers/{publisher_id}/aliases", add_alias),
    route("DELETE", "/api/publishers/{publisher_id}/aliases", delete_alias),
)
