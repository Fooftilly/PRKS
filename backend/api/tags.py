"""Tags HTTP controller (PG-C3 #642).

``/api/tags`` (GET lists every Tag, with an ETag; ``?used=1`` lists the
Tags in use; POST creates one), ``POST /api/tags/merge``, ``DELETE
/api/tags/:id``, ``POST``/``DELETE /api/tags/:id/aliases`` (the alias to
remove is the ``alias`` query parameter), and the Tags of a Work or a
Folder: ``POST /api/{works,folders}/:id/tags``, ``DELETE
/api/{works,folders}/:id/tags/:tag_id`` and ``GET
/api/{works,folders}/:id/tag-options``. A domain refusal (``ValueError``)
is ``400`` with its message; a delete of a missing Tag is ``404``.

The calls go to ``backend/domain/organization/tag_commands`` for the
admitted request's context on the served Library's database, imported when
a route runs (the served app loads no PostgreSQL module at import time,
``tests/test_e2e_policy.py``). The commands open the transactions, so
authorization stays in ``library_query`` and ``library_command``, and a
write's maintenance and epoch fence in ``library_command``.
"""

from __future__ import annotations

from backend.api_contract.operation import Operation, Param, body, error, ok, revalidated
from backend.api_contract.organization import (
    Done,
    FolderTagOptions,
    TagAliasRequest,
    TagCatalog,
    TagCreated,
    TagCreateRequest,
    TagDeleted,
    TagLinkRequest,
    TagMerged,
    TagMergeRequest,
    WorkTagOptions,
)
from backend.web.etags import etag_for_representation
from backend.web.http import Request, Response, json_response
from backend.web.routing import route


def list_tags(req: Request) -> Response:
    from backend.domain.organization import tag_commands

    if req.query_value("used") in ("1", "true", "yes"):
        return json_response(req, 200, tag_commands.in_use(*req.actor))
    data = tag_commands.all_tags(*req.actor)
    return json_response(req, 200, data, etag=etag_for_representation("tags", data))


def work_tag_options(req: Request) -> Response:
    from backend.domain.organization import tag_commands

    data = tag_commands.work_options(*req.actor, req.params["owner_id"])
    if data is None:
        return json_response(req, 404, {"error": "Work not found"})
    return json_response(req, 200, data, etag=etag_for_representation("work-tag-options", data))


def folder_tag_options(req: Request) -> Response:
    from backend.domain.organization import tag_commands

    data = tag_commands.folder_options(*req.actor, req.params["owner_id"])
    if data is None:
        return json_response(req, 404, {"error": "Folder not found"})
    return json_response(req, 200, data, etag=etag_for_representation("folder-tag-options", data))


def merge(req: Request) -> Response:
    from backend.domain.organization import tag_commands

    data = req.data
    sid = (data.get("source_tag_id") or "").strip()
    tid = (data.get("target_tag_id") or "").strip()
    try:
        out = tag_commands.merge(*req.actor, sid, tid)
    except ValueError as e:
        return json_response(req, 400, {"error": str(e)})
    return json_response(req, 200, {"status": "merged", **out})


def add_alias(req: Request) -> Response:
    from backend.domain.organization import tag_commands

    try:
        tag_commands.add_alias(*req.actor, req.params["tag_id"], (req.data.get("alias") or "").strip())
    except ValueError as e:
        return json_response(req, 400, {"error": str(e)})
    return json_response(req, 200, {"status": "added"})


def create(req: Request) -> Response:
    from backend.domain.organization import tag_commands

    data = req.data
    try:
        out = tag_commands.create(*req.actor, data.get("name"), data.get("color", "#6d6cf7"))
    except ValueError as e:
        return json_response(req, 400, {"error": str(e)})
    return json_response(req, 200, out)


def tag_work(req: Request) -> Response:
    from backend.domain.organization import tag_commands

    try:
        tag_commands.tag_work(*req.actor, req.params["owner_id"], req.data.get("tag_id"))
    except ValueError as e:
        # A missing Work, Folder or Tag (e.g. a Tag whose creation is still
        # queued on the client) is a refusal, not a server error.
        return json_response(req, 404, {"error": str(e)})
    return json_response(req, 200, {"status": "added"})


def tag_folder(req: Request) -> Response:
    from backend.domain.organization import tag_commands

    try:
        tag_commands.tag_folder(*req.actor, req.params["owner_id"], req.data.get("tag_id"))
    except ValueError as e:
        return json_response(req, 404, {"error": str(e)})
    return json_response(req, 200, {"status": "added"})


def untag_work(req: Request) -> Response:
    from backend.domain.organization import tag_commands

    tag_commands.untag_work(*req.actor, req.params["owner_id"], req.params["tag_id"])
    return json_response(req, 200, {"status": "removed"})


def untag_folder(req: Request) -> Response:
    from backend.domain.organization import tag_commands

    tag_commands.untag_folder(*req.actor, req.params["owner_id"], req.params["tag_id"])
    return json_response(req, 200, {"status": "removed"})


def delete_alias(req: Request) -> Response:
    from backend.domain.organization import tag_commands

    # parse_qs already decoded the value once; a second unquote mangled
    # aliases that contain "%" (and turned "%2500" into NUL).
    alias = req.query_value("alias")
    if not alias.strip():
        return json_response(req, 400, {"error": "missing alias"})
    if tag_commands.delete_alias(*req.actor, req.params["tag_id"], alias):
        return json_response(req, 200, {"status": "deleted"})
    return json_response(req, 404, {"error": "alias not found"})


def delete(req: Request) -> Response:
    from backend.domain.organization import tag_commands

    try:
        result = tag_commands.delete(*req.actor, req.params["tag_id"])
    except ValueError as e:
        msg = str(e)
        return json_response(req, 404 if "not found" in msg.lower() else 400, {"error": msg})
    return json_response(req, 200, result)


_TAG = "tags"
_REFUSED = error("A domain refusal, with its message (a blank, unknown or merged Tag, a taken name).")
_USED = Param("used", schema={"type": "string", "enum": ["1", "true", "yes"]},
              description="Only the Tags linked to a Work or a Folder, with the counts (no ETag).")


def _options(op_id: str, owner: str, schema: object) -> Operation:
    return revalidated(op_id, f"A {owner}'s Tags and known link revisions", _TAG, schema,
                       "Assigned Tags with their link revisions, and the absent ones that have one.",
                       {404: error(f"No {owner} with that id.")})


def _link(op_id: str, owner: str) -> Operation:
    return Operation(op_id, f"Link a Tag to a {owner}", _TAG,
                     {200: ok(Done, "Linked (or already linked)."),
                      404: error(f"No such {owner} or Tag, or a blank tag_id.")},
                     request=body(TagLinkRequest))


def _unlink(op_id: str, owner: str) -> Operation:
    return Operation(op_id, f"Unlink a Tag from a {owner}", _TAG,
                     {200: ok(Done, "Unlinked (or was not linked).")})


ROUTES = (
    route("GET", "/api/tags", list_tags, contract=revalidated(
        "listTags", "Every Tag with its aliases, by name", _TAG, TagCatalog,
        "Every Tag; with ``used`` the Tags in use and their counts.", params=(_USED,))),
    route("POST", "/api/tags", create, contract=Operation(
        "createTag", "Create a Tag, or recolour the one holding the name", _TAG,
        {200: ok(TagCreated, "The Tag holding the name."), 400: _REFUSED},
        request=body(TagCreateRequest))),
    route("POST", "/api/tags/merge", merge, contract=Operation(
        "mergeTags", "Move a Tag's links to another and keep its name as an alias", _TAG,
        {200: ok(TagMerged, "Merged; the affected owners are listed."), 400: _REFUSED},
        request=body(TagMergeRequest))),
    route("DELETE", "/api/tags/{tag_id}", delete, contract=Operation(
        "deleteTag", "Delete a Tag with its links and aliases", _TAG,
        {200: ok(TagDeleted, "Deleted; the affected owners are listed."),
         400: error("A blank tag id."), 404: error("No Tag with that id.")})),
    route("POST", "/api/tags/{tag_id}/aliases", add_alias, contract=Operation(
        "addTagAlias", "Add an alias to a Tag", _TAG,
        {200: ok(Done, "Added."), 400: _REFUSED}, request=body(TagAliasRequest))),
    route("DELETE", "/api/tags/{tag_id}/aliases", delete_alias, contract=Operation(
        "deleteTagAlias", "Remove a Tag's alias", _TAG,
        {200: ok(Done, "Removed."), 400: error("No ``alias``."), 404: error("The Tag has no such alias.")},
        params=(Param("alias", required=True, description="The alias to remove, by its identity key."),))),
    route("GET", "/api/works/{owner_id}/tag-options", work_tag_options,
          contract=_options("getWorkTagOptions", "Work", WorkTagOptions)),
    route("GET", "/api/folders/{owner_id}/tag-options", folder_tag_options,
          contract=_options("getFolderTagOptions", "Folder", FolderTagOptions)),
    route("POST", "/api/works/{owner_id}/tags", tag_work, contract=_link("tagWork", "Work")),
    route("POST", "/api/folders/{owner_id}/tags", tag_folder, contract=_link("tagFolder", "Folder")),
    route("DELETE", "/api/works/{owner_id}/tags/{tag_id}", untag_work, contract=_unlink("untagWork", "Work")),
    route("DELETE", "/api/folders/{owner_id}/tags/{tag_id}", untag_folder,
          contract=_unlink("untagFolder", "Folder")),
)
