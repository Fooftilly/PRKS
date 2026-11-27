"""Saved Views HTTP controller.

Cohesive CRUD for ``/api/saved-views`` and ``/api/saved-views/:id``.

Parse path/body shape, run ``backend/domain/saved_views/commands`` for the
admitted request's context on the served Library's database, map
``SavedViewError`` to HTTP status/JSON. The wire shape is
``backend/api_contract/saved_views.py``: reads go through ``dump_response``;
writes send the committed domain row without a post-commit dump (see
docs/api-contract-boundary.md). Request field types stay domain refusals. Name/search normalization and SQL stay
in ``backend/domain/saved_views``; the commands open the transactions, so
authorization stays in ``library_query`` and ``library_command``, and a
write's maintenance and epoch fence in ``library_command``.

The commands are imported when a route runs: the served app loads no
PostgreSQL module at import time (``tests/test_e2e_policy.py``).
"""

from __future__ import annotations

import logging
from urllib.parse import unquote

from backend.api_contract.boundary import dump_response
from backend.api_contract.operation import Operation, body, error, ok
from backend.api_contract.saved_views import (
    SavedView,
    SavedViewCreateRequest,
    SavedViewDeleted,
    SavedViewUpdateRequest,
)
from backend.domain.saved_views.rules import SavedViewError
from backend.runtime.log_safety import safe_log_id, safe_log_label
from backend.web.http import Request, Response, json_response
from backend.web.routing import route

LOGGER = logging.getLogger("prks.api.saved_views")


def _id(req: Request) -> str:
    return unquote(req.params["view_id"])


def _refused(req: Request, exc: SavedViewError) -> Response:
    body: dict[str, str] = {"error": str(exc)}
    if exc.code:
        body["code"] = exc.code
    return json_response(req, int(exc.http_status), body)


def all_views(req: Request) -> Response:
    from backend.domain.saved_views import commands

    return json_response(req, 200, dump_response(SavedView, commands.all_views(*req.actor)))


def view(req: Request) -> Response:
    from backend.domain.saved_views import commands

    data = commands.view(*req.actor, _id(req))
    if data:
        return json_response(req, 200, dump_response(SavedView, data))
    return json_response(req, 404, {"error": "Saved View not found."})


def create(req: Request) -> Response:
    from backend.domain.saved_views import commands

    data = req.data
    try:
        created = commands.create(*req.actor, data.get("name"), data.get("search"))
    except SavedViewError as e:
        return _refused(req, e)
    LOGGER.info(
        "saved_view_created view_id=%s mode=%s",
        safe_log_id(created.get("id")),
        safe_log_label((created.get("search") or {}).get("mode")),
    )
    return json_response(req, 201, created)


def update(req: Request) -> Response:
    from backend.domain.saved_views import commands

    data = req.data
    has_name = "name" in data
    has_search = "search" in data
    if not has_name and not has_search:
        return json_response(req, 400, {"error": "Nothing to update."})
    try:
        updated = commands.update(
            *req.actor, _id(req),
            base_revision=data.get("base_revision"),
            name=data.get("name") if has_name else None,
            search=data.get("search") if has_search else None,
        )
    except SavedViewError as e:
        return _refused(req, e)
    LOGGER.info(
        "saved_view_updated view_id=%s definition_changed=%s",
        safe_log_id(updated.get("id")),
        "true" if has_search else "false",
    )
    return json_response(req, 200, updated)


def delete(req: Request) -> Response:
    from backend.domain.saved_views import commands

    try:
        commands.delete(*req.actor, _id(req))
    except SavedViewError as e:
        return _refused(req, e)
    # Do not log path-derived ids: CodeQL treats URL segments as
    # user-controlled even after safe_log_id, and privacy policy forbids
    # logging raw library identifiers from the request path.
    LOGGER.info("saved_view_deleted")
    return json_response(req, 200, dump_response(SavedViewDeleted, {"status": "deleted"}))


_TAG = "saved-views"
_ID = "/api/saved-views/{view_id}"
_NOT_FOUND = error("No such Saved View.")

ROUTES = (
    route("GET", "/api/saved-views", all_views, contract=Operation(
        "listSavedViews", "List Saved Views", _TAG,
        {200: ok(list[SavedView], "Saved Views ordered by name, case-insensitively.")})),
    route("POST", "/api/saved-views", create, contract=Operation(
        "createSavedView", "Save a named search definition", _TAG,
        {201: ok(SavedView, "Created."),
         400: error("Domain refusal: wrong type, empty or too long name, or an incomplete or invalid "
                    "search definition."),
         409: error("Name already used, or the library has the maximum number of Saved Views.")},
        request=body(SavedViewCreateRequest))),
    route("GET", _ID, view, contract=Operation(
        "getSavedView", "Read one Saved View", _TAG,
        {200: ok(SavedView, "The Saved View."), 404: _NOT_FOUND})),
    route("PATCH", _ID, update, contract=Operation(
        "updateSavedView", "Rename a Saved View or change its search", _TAG,
        {200: ok(SavedView, "Updated."),
         400: error("Nothing to update, or a domain refusal of the name or search definition."),
         404: _NOT_FOUND,
         409: error("Name already used by another Saved View, or the view changed since `base_revision` "
                    "(code `revision_conflict`).")},
        request=body(SavedViewUpdateRequest))),
    route("DELETE", _ID, delete, contract=Operation(
        "deleteSavedView", "Delete a Saved View", _TAG,
        {200: ok(SavedViewDeleted, "Deleted."), 404: _NOT_FOUND},
        description="Deleting a view never deletes Works or files.")),
)
