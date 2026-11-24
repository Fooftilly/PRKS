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

from backend.etags import etag_for_representation
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


ROUTES = (
    route("GET", "/api/tags", list_tags),
    route("POST", "/api/tags", create),
    route("POST", "/api/tags/merge", merge),
    route("DELETE", "/api/tags/{tag_id}", delete),
    route("POST", "/api/tags/{tag_id}/aliases", add_alias),
    route("DELETE", "/api/tags/{tag_id}/aliases", delete_alias),
    route("GET", "/api/works/{owner_id}/tag-options", work_tag_options),
    route("GET", "/api/folders/{owner_id}/tag-options", folder_tag_options),
    route("POST", "/api/works/{owner_id}/tags", tag_work),
    route("POST", "/api/folders/{owner_id}/tags", tag_folder),
    route("DELETE", "/api/works/{owner_id}/tags/{tag_id}", untag_work),
    route("DELETE", "/api/folders/{owner_id}/tags/{tag_id}", untag_folder),
)
