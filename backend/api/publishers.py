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
    PublisherAliasAdded,
    PublisherAliasRequest,
    PublisherCreated,
    PublisherCreateRequest,
    PublisherDeleted,
    PublisherInUse,
)
from backend.api_contract.operation import Operation, Param, body, error, ok
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


_TAG = "publishers"
_ALIASES = "/api/publishers/{publisher_id}/aliases"

ROUTES = (
    route("GET", "/api/publishers", in_use, contract=Operation(
        "listPublishersInUse", "List canonical Publishers with aliases and Work counts", _TAG,
        {200: ok(list[PublisherInUse], "Publishers ordered by name, case-insensitively.")},
        params=(Param("used", description="`1`, `true`, or `yes` returns the catalog. Any other value "
                                          "returns an empty list."),))),
    route("POST", "/api/publishers", create, contract=Operation(
        "createPublisher", "Create a canonical Publisher, or return the existing one", _TAG,
        {200: ok(PublisherCreated, "Created or existing Publisher."),
         400: error("Schema or domain refusal (empty name).")},
        request=body(PublisherCreateRequest))),
    route("DELETE", "/api/publishers/{publisher_id}", delete, contract=Operation(
        "deletePublisher", "Delete a Publisher group and its aliases", _TAG,
        {200: ok(PublisherDeleted, "Deleted.")},
        description="Works keep their publisher text. Deleting a missing id is not an error.")),
    route("POST", _ALIASES, add_alias, contract=Operation(
        "addPublisherAlias", "Add an alternate spelling to a Publisher", _TAG,
        {200: ok(PublisherAliasAdded, "Added, or already an alias of this Publisher."),
         400: error("Schema or domain refusal (empty, unknown Publisher, canonical name, or used elsewhere).")},
        request=body(PublisherAliasRequest))),
    route("DELETE", _ALIASES, delete_alias, contract=Operation(
        "removePublisherAlias", "Remove an alternate spelling from a Publisher", _TAG,
        {200: ok(PublisherDeleted, "Removed."), 400: error("Missing alias."),
         404: error("This Publisher has no such alias.")},
        params=(Param("alias", required=True),))),
)
