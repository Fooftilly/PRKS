"""Folders HTTP controller (PG-C3 #642).

``/api/folders`` (GET the catalog with an ETag, POST create),
``/api/folders/:id`` (GET with its children, parent, Works and Tags; PATCH
fields, answering the member Works a rename stales; DELETE an empty one),
``POST /api/folders/:id/works`` (file a Work), the revision states
``GET /api/folders/:id/sync-state`` and ``GET /api/works/:id/folder-state``,
and ``GET /api/works/:id/related_folders``. The Work routes file a Work
through :func:`file_work`, :func:`ensure_uncategorized`, :func:`exists` and
:func:`move_work`.

The calls go to ``backend/domain/organization/folder_commands`` for the
admitted request's context on the served Library's database. The commands
open the transactions, so authorization stays in ``library_query`` and
``library_command``, and a write's maintenance and epoch fence in
``library_command``.
The commands are imported when a route runs: the served app loads no
PostgreSQL module at import time (``tests/test_e2e_policy.py``).
"""

from __future__ import annotations

from typing import Any, Optional
from urllib.parse import unquote

from backend.api.common import revalidated
from backend.web.http import Request, Response, error_response, json_response
from backend.web.routing import route


# -- for the Work routes ----------------------------------------------------------

def exists(req: Request, folder_id: Optional[str]) -> bool:
    from backend.domain.organization import folder_commands

    return folder_commands.exists(*req.actor, folder_id)


def ensure_uncategorized(req: Request) -> str:
    from backend.domain.organization import folder_commands

    return folder_commands.ensure_uncategorized(*req.actor)


def file_work(req: Request, folder_id: Optional[str], work_id: Optional[str]) -> None:
    """File an unfiled Work; refusals raise ``ValueError``."""
    from backend.domain.organization import folder_commands

    folder_commands.file_work(*req.actor, folder_id, work_id)


def move_work(req: Request, work_id: Optional[str], folder_id: Any) -> None:
    """Assign, move or clear a Work's Folder; refusals raise ``ValueError``."""
    from backend.domain.organization import folder_commands

    folder_commands.move_work(*req.actor, work_id, folder_id)


# -- handlers -----------------------------------------------------------------------

def catalog(req: Request) -> Response:
    from backend.domain.organization import folder_commands

    # The catalog IS the revision source, so build it once and derive the
    # ETag from it.
    return revalidated(req, "folders", folder_commands.catalog(*req.actor))


def detail(req: Request) -> Response:
    from backend.domain.organization import folder_commands

    data = folder_commands.detail(*req.actor, req.params["folder_id"])
    if data:
        return json_response(req, 200, data)
    return error_response(404, "Folder not found")


def related(req: Request) -> Response:
    from backend.domain.organization import folder_commands

    return json_response(req, 200, folder_commands.related(*req.actor, req.params["work_id"]))


def sync_state(req: Request) -> Response:
    from backend.domain.organization import folder_commands

    # REVISIONS ONLY. The folder catalogue already carries every title,
    # parent and description.
    data = folder_commands.sync_state(*req.actor, unquote(req.params["folder_id"]))
    return revalidated(req, "folder-state", data, missing="Folder not found")


def work_state(req: Request) -> Response:
    """``GET /api/works/:id/folder-state``."""
    from backend.domain.organization import folder_commands

    data = folder_commands.work_state(*req.actor, unquote(req.params["work_id"]))
    return revalidated(req, "work-folder-state", data)


def update(req: Request) -> Response:
    from backend.domain.organization import folder_commands

    data = req.data
    f_id = req.params["folder_id"]
    # A rename stales every member Work's cached detail (it embeds
    # folder_title), so collect the members BEFORE the write and report
    # them -- membership cannot change in this request.
    member_ids = folder_commands.member_work_ids(*req.actor, f_id) if "title" in data else []
    try:
        folder_commands.update(*req.actor, f_id, data)
    except ValueError as e:
        return json_response(req, 400, {"error": str(e)})
    return json_response(req, 200, {"status": "updated", "member_work_ids": member_ids})


def create(req: Request) -> Response:
    from backend.domain.organization import folder_commands

    data = req.data
    try:
        f_id = folder_commands.create(
            *req.actor,
            title=data.get("title", "Untitled Folder"),
            description=data.get("description", ""),
            parent_id=data.get("parent_id"),
        )
    except ValueError as e:
        return json_response(req, 409, {"error": str(e)})
    return json_response(req, 200, {"id": f_id})


def add_work(req: Request) -> Response:
    from backend.api import works as works_api

    f_id = req.params["folder_id"]
    raw = req.data.get("work_id")
    w_id = raw.strip() if isinstance(raw, str) else ""
    if not w_id:
        return json_response(req, 400, {"error": "work_id is required"})
    if not exists(req, f_id):
        return json_response(req, 404, {"error": "Folder not found"})
    if works_api.thumbnail_fields(req, w_id) is None:
        return json_response(req, 404, {"error": "Work not found"})
    try:
        file_work(req, f_id, w_id)
    except ValueError as e:
        return json_response(req, 409, {"error": str(e)})
    return json_response(req, 200, {"status": "added"})


def delete(req: Request) -> Response:
    from backend.domain.organization import folder_commands

    try:
        folder_commands.delete(*req.actor, req.params["folder_id"])
    except ValueError as e:
        return json_response(req, 409, {"error": str(e)})
    return json_response(req, 200, {"status": "deleted"})


ROUTES = (
    route("GET", "/api/folders", catalog),
    route("POST", "/api/folders", create),
    route("GET", "/api/folders/{folder_id}", detail),
    route("PATCH", "/api/folders/{folder_id}", update),
    route("DELETE", "/api/folders/{folder_id}", delete),
    route("GET", "/api/folders/{folder_id}/sync-state", sync_state),
    route("POST", "/api/folders/{folder_id}/works", add_work),
    route("GET", "/api/works/{work_id}/related_folders", related),
    route("GET", "/api/works/{work_id}/folder-state", work_state),
)
