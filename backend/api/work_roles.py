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

``server.py`` calls these handlers after host/origin, JSON body size, and
library-access checks. Each ``handle_*`` returns ``True`` when the path
belongs to this family (including refusals already sent), else ``False`` so
dispatch continues.
"""

from __future__ import annotations

from typing import Any, Optional

from backend.etags import etag_for_representation

ROLES = "/api/roles"


def _commands() -> Any:
    """``backend.domain.people.role_commands``, imported when a route runs.

    The served app loads no PostgreSQL module at import time
    (``tests/test_e2e_policy.py``).
    """
    from backend.domain.people import role_commands

    return role_commands


def _actor(handler: Any, db: Any) -> tuple[Any, Any]:
    """The served Library's database and the admitted request's context."""
    ctx = getattr(handler, "_prks_request_context", None)
    if ctx is None:
        raise RuntimeError("a role route ran without an admitted request context")
    return db.database, ctx


def _work_sub(path: str, leaf: str) -> Optional[str]:
    """The Work id of ``/api/works/:id/<leaf>``, else ``None``."""
    parts = path.split("/")
    if path.startswith("/api/works/") and len(parts) == 5 and parts[4] == leaf:
        return parts[3]
    return None


def missing_initial_person_ids(handler: Any, db: Any, initial_roles: Any) -> list[str]:
    """The Persons a new Work's roles name that do not exist (yet)."""
    return _commands().missing_initial_person_ids(*_actor(handler, db), initial_roles)


def insert_initial(handler: Any, db: Any, work_id: str, initial_roles: Any) -> int:
    """The roles a new Work is born with, in one transaction; ``MissingPersonError`` on a missing Person."""
    return _commands().insert_initial(*_actor(handler, db), work_id, initial_roles)


def handle_get(handler: Any, db: Any, path: str) -> bool:
    """``GET /api/works/:id/people-state``."""
    if not (path.startswith("/api/works/") and path.endswith("/people-state") and len(path.split("/")) == 5):
        return False
    # Relationship REVISIONS, including tombstones. The Work detail already
    # carries the linked people themselves; duplicating Person objects here
    # would double what every read costs for values the client already
    # holds. A scope with a revision and no live relationship is what tells
    # an offline device its own pending removal is current rather than stale.
    data = _commands().work_state(*_actor(handler, db), path.split("/")[3])
    if data is None:
        handler.send_json(404, {"error": "Work not found"})
        return True
    etag = etag_for_representation("work-people-state", data)
    if handler._prks_if_none_match(etag):
        handler._send_json_not_modified(etag)
        return True
    handler.send_json(200, data, etag=etag, precondition_checked=True)
    return True


def handle_patch(handler: Any, db: Any, path: str, data: Any) -> bool:
    """``PATCH /api/works/:id/roles``: a role's credit."""
    if not (path.startswith("/api/works/") and path.endswith("/roles")):
        return False
    w_id = _work_sub(path, "roles")
    if w_id is None:
        handler.send_error(404, "API endpoint not found")
        return True
    if not isinstance(data, dict):
        handler.send_json(400, {"error": "JSON object body required"})
        return True
    person_id = (data.get("person_id") or "").strip()
    role_type = (data.get("role_type") or "").strip()
    oi_raw = data.get("order_index", 0)
    try:
        if oi_raw is not None and str(oi_raw).strip() != "":
            int(oi_raw)
    except (TypeError, ValueError):
        handler.send_json(400, {"error": "order_index must be an integer"})
        return True
    if not person_id or not role_type:
        handler.send_json(400, {"error": "person_id and role_type are required"})
        return True
    credit_name = data.get("credit_name", "")
    if credit_name is not None and not isinstance(credit_name, str):
        handler.send_json(400, {"error": "credit_name must be a string"})
        return True
    try:
        updated = _commands().update_credit(*_actor(handler, db), w_id, person_id, role_type,
                                            credit_name or "")
    except ValueError as e:
        handler.send_json(400, {"error": str(e)})
        return True
    if updated:
        handler.send_json(200, {"status": "updated"})
    else:
        handler.send_json(404, {"error": "role link not found"})
    return True


def handle_delete(handler: Any, db: Any, path: str, query: dict[str, list[str]]) -> bool:
    """``DELETE /api/works/:id/roles?person_id=&role_type=&order_index=``."""
    if not (path.startswith("/api/works/") and path.endswith("/roles")):
        return False
    w_id = _work_sub(path, "roles")
    if w_id is None:
        handler.send_error(404, "API endpoint not found")
        return True
    person_id = (query.get("person_id") or [""])[0].strip()
    role_type = (query.get("role_type") or [""])[0].strip()
    oi_raw = (query.get("order_index") or ["0"])[0].strip()
    try:
        if oi_raw != "":
            int(oi_raw)
    except ValueError:
        handler.send_json(400, {"error": "order_index must be an integer"})
        return True
    if not person_id or not role_type:
        handler.send_json(400, {"error": "person_id and role_type are required"})
        return True
    if _commands().remove(*_actor(handler, db), w_id, person_id, role_type):
        handler.send_json(200, {"status": "removed"})
    else:
        handler.send_json(404, {"error": "role link not found"})
    return True


def handle_post(handler: Any, db: Any, path: str, data: Any) -> bool:
    """``POST /api/roles``: a role on an existing Work, appended."""
    if path != ROLES:
        return False
    p_id = (data.get("person_id") or "").strip()
    w_id = (data.get("work_id") or "").strip()
    r_type = (data.get("role_type") or "").strip()
    if not p_id or not w_id or not r_type:
        handler.send_json(400, {"error": "person_id, work_id, and role_type are required"})
        return True
    credit_name = data.get("credit_name", "")
    if credit_name is not None and not isinstance(credit_name, str):
        handler.send_json(400, {"error": "credit_name must be a string"})
        return True
    try:
        _commands().add(*_actor(handler, db), p_id, w_id, r_type, credit_name or "")
    except ValueError as e:
        handler.send_json(400, {"error": str(e)})
        return True
    handler.send_json(200, {"status": "success"})
    return True
