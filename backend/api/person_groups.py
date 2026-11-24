"""Person Groups HTTP controller (PG-C3 #642).

``/api/person-groups`` (GET the catalog with an ETag, POST create, resolving
or creating a parent named by ``parent_name`` when no ``parent_id`` is
given), ``/api/person-groups/:id`` (GET with its children, members and
parent; PATCH fields; DELETE), ``POST /api/person-groups/:id/members`` (add
the body's ``person_id``), ``DELETE /api/person-groups/:id/members/:person_id``,
and the revision states ``GET /api/person-groups/:id/sync-state`` and
``GET /api/persons/:id/group-state``. A domain refusal (``ValueError``) on
create, update, delete or add is ``400`` with its message.

The calls go to ``backend/domain/people/group_commands`` for the admitted
request's context on the served Library's database. The commands open the
transactions, so authorization stays in ``library_query`` and
``library_command``, and a write's maintenance and epoch fence in
``library_command``.
The commands are imported when a route runs: the served app loads no
PostgreSQL module at import time (``tests/test_e2e_policy.py``).
"""

from __future__ import annotations

from urllib.parse import unquote

from backend.api.common import revalidated
from backend.web.http import Request, Response, error_response, json_response
from backend.web.routing import route


def catalog(req: Request) -> Response:
    from backend.domain.people import group_commands

    return revalidated(req, "person-groups", group_commands.catalog(*req.actor))


def sync_state(req: Request) -> Response:
    from backend.domain.people import group_commands

    # REVISIONS ONLY. The catalogue already carries every group's name,
    # parent and description and the detail carries its members, so
    # echoing values here would make a second copy.
    data = group_commands.sync_state(*req.actor, unquote(req.params["group_id"]))
    return revalidated(req, "person-group-state", data, missing="Group not found")


def detail(req: Request) -> Response:
    from backend.domain.people import group_commands

    data = group_commands.detail(*req.actor, req.params["group_id"])
    if data:
        return json_response(req, 200, data)
    return error_response(404, "Group not found")


def person_state(req: Request) -> Response:
    """``GET /api/persons/:id/group-state``."""
    from backend.domain.people import group_commands

    # Membership revisions for one Person, including tombstones: a pair
    # this device once removed has a revision, and adding it back is a
    # mutation of that scope rather than a first write.
    data = group_commands.person_state(*req.actor, unquote(req.params["person_id"]))
    return revalidated(req, "person-group-state", data, missing="Person not found")


def update(req: Request) -> Response:
    from backend.domain.people import group_commands

    try:
        group_commands.update(*req.actor, req.params["group_id"], req.data)
    except ValueError as e:
        return json_response(req, 400, {"error": str(e)})
    return json_response(req, 200, {"status": "updated"})


def create(req: Request) -> Response:
    from backend.domain.people import group_commands

    data = req.data
    pid = data.get("parent_id")
    if pid == "":
        pid = None
    try:
        g_id = group_commands.create(
            *req.actor,
            name=data.get("name", ""),
            parent_id=pid,
            parent_name=None if pid else data.get("parent_name"),
            description=data.get("description", "") or "",
        )
    except ValueError as e:
        return json_response(req, 400, {"error": str(e)})
    return json_response(req, 200, {"id": g_id})


def add_member(req: Request) -> Response:
    from backend.domain.people import group_commands

    try:
        group_commands.add_member(*req.actor, req.params["group_id"], req.data.get("person_id", ""))
    except ValueError as e:
        return json_response(req, 400, {"error": str(e)})
    return json_response(req, 200, {"status": "added"})


def remove_member(req: Request) -> Response:
    from backend.domain.people import group_commands

    group_commands.remove_member(*req.actor, req.params["group_id"], req.params["person_id"])
    return json_response(req, 200, {"status": "removed"})


def delete(req: Request) -> Response:
    from backend.domain.people import group_commands

    try:
        group_commands.delete(*req.actor, req.params["group_id"])
    except ValueError as e:
        return json_response(req, 400, {"error": str(e)})
    return json_response(req, 200, {"status": "deleted"})


ROUTES = (
    route("GET", "/api/person-groups", catalog),
    route("POST", "/api/person-groups", create),
    route("GET", "/api/person-groups/{group_id}", detail),
    route("PATCH", "/api/person-groups/{group_id}", update),
    route("DELETE", "/api/person-groups/{group_id}", delete),
    route("GET", "/api/person-groups/{group_id}/sync-state", sync_state),
    route("POST", "/api/person-groups/{group_id}/members", add_member),
    route("DELETE", "/api/person-groups/{group_id}/members/{person_id}", remove_member),
    route("GET", "/api/persons/{person_id}/group-state", person_state),
)
