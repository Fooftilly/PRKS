"""Work-Person roles HTTP controller (PG-C3 #642).

``POST /api/roles`` (add a role to an existing Work), ``PATCH
/api/works/:id/roles`` (change a role's credit), ``DELETE
/api/works/:id/roles?person_id=&role_type=&order_index=`` (remove one), and
the revision state ``GET /api/works/:id/people-state``. ``order_index`` is
validated as an integer and otherwise ignored: a role is named by Work,
Person and role type. A domain refusal (``ValueError``) is ``400`` with its
message; a role that does not exist is ``404 role link not found``.
:func:`missing_initial_person_ids` and :func:`insert_initial` serve the
Work create route.

The calls go to ``backend/domain/people/role_commands`` for the admitted
request's context on the served Library's database. The commands open the
transactions, so authorization stays in ``library_query`` and
``library_command``, and a write's maintenance and epoch fence in
``library_command``.
The commands are imported when a route runs: the served app loads no
PostgreSQL module at import time (``tests/test_e2e_policy.py``).
"""

from __future__ import annotations

from typing import Any

from backend.api.common import revalidated, text_field
from backend.api_contract.operation import Operation, Param, body, error, ok
from backend.api_contract.operation import revalidated as revalidated_read
from backend.api_contract.people import RoleAddRequest, RoleAdded, RoleCreditRequest, RoleDone, WorkPeopleState
from backend.web.http import Request, Response, json_response
from backend.web.routing import route


def missing_initial_person_ids(req: Request, initial_roles: Any) -> list[str]:
    """The Persons a new Work's roles name that do not exist (yet)."""
    from backend.domain.people import role_commands

    return role_commands.missing_initial_person_ids(*req.actor, initial_roles)


def insert_initial(req: Request, work_id: str, initial_roles: Any) -> int:
    """The roles a new Work is born with, in one transaction; ``MissingPersonError`` on a missing Person."""
    from backend.domain.people import role_commands

    return role_commands.insert_initial(*req.actor, work_id, initial_roles)


def people_state(req: Request) -> Response:
    """``GET /api/works/:id/people-state``."""
    from backend.domain.people import role_commands

    # Relationship REVISIONS, including tombstones. The Work detail already
    # carries the linked people themselves; duplicating Person objects here
    # would double what every read costs for values the client already
    # holds. A scope with a revision and no live relationship is what tells
    # an offline device its own pending removal is current rather than stale.
    return revalidated(req, "work-people-state", role_commands.work_state(*req.actor, req.params["work_id"]))


def update_credit(req: Request) -> Response:
    """``PATCH /api/works/:id/roles``: a role's credit."""
    from backend.domain.people import role_commands

    data = req.data
    person_id = text_field(data, "person_id")
    role_type = text_field(data, "role_type")
    oi_raw = data.get("order_index", 0)
    try:
        if oi_raw is not None and str(oi_raw).strip() != "":
            int(oi_raw)
    except (TypeError, ValueError, OverflowError):
        return json_response(req, 400, {"error": "order_index must be an integer"})
    if not person_id or not role_type:
        return json_response(req, 400, {"error": "person_id and role_type are required"})
    credit_name = data.get("credit_name", "")
    if credit_name is not None and not isinstance(credit_name, str):
        return json_response(req, 400, {"error": "credit_name must be a string"})
    try:
        updated = role_commands.update_credit(*req.actor, req.params["work_id"], person_id, role_type,
                                              credit_name or "")
    except ValueError as e:
        return json_response(req, 400, {"error": str(e)})
    if updated:
        return json_response(req, 200, {"status": "updated"})
    return json_response(req, 404, {"error": "role link not found"})


def remove(req: Request) -> Response:
    """``DELETE /api/works/:id/roles?person_id=&role_type=&order_index=``."""
    from backend.domain.people import role_commands

    person_id = req.query_value("person_id").strip()
    role_type = req.query_value("role_type").strip()
    oi_raw = (req.query.get("order_index") or ["0"])[0].strip()
    try:
        if oi_raw != "":
            int(oi_raw)
    except ValueError:
        return json_response(req, 400, {"error": "order_index must be an integer"})
    if not person_id or not role_type:
        return json_response(req, 400, {"error": "person_id and role_type are required"})
    if role_commands.remove(*req.actor, req.params["work_id"], person_id, role_type):
        return json_response(req, 200, {"status": "removed"})
    return json_response(req, 404, {"error": "role link not found"})


def add(req: Request) -> Response:
    """``POST /api/roles``: a role on an existing Work, appended."""
    from backend.domain.people import role_commands

    data = req.data
    p_id = text_field(data, "person_id")
    w_id = text_field(data, "work_id")
    r_type = text_field(data, "role_type")
    if not p_id or not w_id or not r_type:
        return json_response(req, 400, {"error": "person_id, work_id, and role_type are required"})
    credit_name = data.get("credit_name", "")
    if credit_name is not None and not isinstance(credit_name, str):
        return json_response(req, 400, {"error": "credit_name must be a string"})
    try:
        role_commands.add(*req.actor, p_id, w_id, r_type, credit_name or "")
    except ValueError as e:
        return json_response(req, 400, {"error": str(e)})
    return json_response(req, 200, {"status": "success"})


_TAG = "roles"
_NO_LINK = error("No such role link on the Work.")


ROUTES = (
    route("POST", "/api/roles", add, contract=Operation(
        "addRole", "Credit a Person on an existing Work, appended", _TAG,
        {200: ok(RoleAdded, "Added."),
         400: error("A missing field, a credit that is not a string, or a domain refusal.")},
        request=body(RoleAddRequest))),
    route("GET", "/api/works/{work_id}/people-state", people_state, contract=revalidated_read(
        "getWorkPeopleState", "A Work's role revisions", _TAG, WorkPeopleState,
        "Each Person-role relationship's revision, tombstones included.", {404: error("No Work with that id.")})),
    route("PATCH", "/api/works/{work_id}/roles", update_credit, contract=Operation(
        "updateRoleCredit", "Change how a Person is credited on a Work", _TAG,
        {200: ok(RoleDone, "Updated."),
         400: error("A missing field, a bad order_index or credit, or a domain refusal."),
         404: _NO_LINK},
        request=body(RoleCreditRequest))),
    route("DELETE", "/api/works/{work_id}/roles", remove, contract=Operation(
        "removeRole", "Remove a Person's role from a Work", _TAG,
        {200: ok(RoleDone, "Removed."), 400: error("A missing person_id or role_type, or a bad order_index."),
         404: _NO_LINK},
        params=(Param("person_id", required=True), Param("role_type", required=True),
                Param("order_index", description="Checked to be an integer, else ignored.")))),
)
