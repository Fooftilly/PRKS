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

from backend.api_contract.operation import Operation, Param, body, error, ok, revalidated
from backend.api_contract.organization import Done
from backend.api_contract.research import (
    ArgumentCard,
    ArgumentCreateRequest,
    ArgumentDetail,
    ArgumentPatch,
    ArgumentRead,
    ArgumentSourcesRequest,
    ArgumentState,
    ArgumentTargetsRequest,
    Verdict,
)
from backend.domain.research.rules import ResearchError
from backend.web.etags import etag_for_representation
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


_TAG = "arguments"
_ID = "/api/arguments/{argument_id}"
_INVALID = error("A refused value, with its code (invalid_name, invalid_kind, invalid_text, invalid_sources, "
                 "invalid_targets, invalid_pages, invalid_verdict, nothing_to_update).")
_MISSING = error("No Argument with that id (not_found), or a cited Work, Version or target that is not one "
                 "(work_not_found, argument_source_version_not_found, position_not_found, argument_not_found).")
_CONFLICT = error("A target would make a response cycle (argument_cycle), or a Work's citation must name "
                  "its Version (argument_source_version_required) of that Work "
                  "(argument_source_version_mismatch).")


def _write(op_id: str, summary: str, request: object) -> Operation:
    return Operation(op_id, summary, _TAG,
                     {200: ok(ArgumentDetail, "The Argument as it now stands."), 400: _INVALID,
                      404: _MISSING, 409: _CONFLICT},
                     request=body(request))


ROUTES = (
    route("GET", "/api/argument-verdicts", verdicts, contract=Operation(
        "listArgumentVerdicts", "The enabled verdicts, in order", _TAG,
        {200: ok(list[Verdict], "The verdicts.")})),
    route("GET", "/api/arguments", catalog, contract=Operation(
        "listArguments", "Every Argument (or every one of a kind) with its lists and counts", _TAG,
        {200: ok(list[ArgumentCard], "By name."), 400: error("An unknown kind (invalid_kind).")},
        params=(Param("kind", schema={"type": "string", "enum": ["argument", "stance"]},
                      description="Only this kind; blank: every Argument."),))),
    route("POST", "/api/arguments", create, contract=Operation(
        "createArgument", "Create an Argument or Stance", _TAG,
        {201: ok(ArgumentDetail, "The new Argument."), 400: _INVALID, 404: _MISSING, 409: _CONFLICT},
        request=body(ArgumentCreateRequest))),
    route("GET", _ID, detail, contract=Operation(
        "getArgument", "An Argument with its lists, responses, mentions and the verdicts", _TAG,
        {200: ok(ArgumentRead, "The Argument."), 404: error("No Argument with that id.")})),
    route("PATCH", _ID, update, contract=_write("updateArgument", "Change an Argument's name, kind or text",
                                                ArgumentPatch)),
    route("DELETE", _ID, delete, contract=Operation(
        "deleteArgument", "Delete an Argument", _TAG,
        {200: ok(Done, "Deleted."), 404: error("No Argument with that id (not_found)."),
         409: error("A Research Note references it (argument_in_use), or another Argument responds to it "
                    "(argument_targeted).")})),
    route("GET", _ID + "/sync-state", sync_state, contract=revalidated(
        "getArgumentState", "An Argument's field and list revisions", _TAG, ArgumentState,
        "Revisions only.", {404: error("No Argument with that id.")})),
    route("PUT", _ID + "/sources", replace_sources, contract=_write(
        "setArgumentSources", "Replace an Argument's citations", ArgumentSourcesRequest)),
    route("PUT", _ID + "/targets", replace_targets, contract=_write(
        "setArgumentTargets", "Replace what an Argument responds to", ArgumentTargetsRequest)),
)
