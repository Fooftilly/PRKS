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

``server.py`` calls these handlers after host/origin, JSON body size, and
library-access checks. Each ``handle_*`` returns ``True`` when the path
belongs to this family (including refusals already sent), else ``False`` so
dispatch continues.
"""

from __future__ import annotations

from typing import Any, Optional
from urllib.parse import unquote

from backend.etags import etag_for_representation

COLLECTION = "/api/person-groups"
_PREFIX = COLLECTION + "/"


def _commands() -> Any:
    """``backend.domain.people.group_commands``, imported when a route runs.

    The served app loads no PostgreSQL module at import time
    (``tests/test_e2e_policy.py``).
    """
    from backend.domain.people import group_commands

    return group_commands


def _actor(handler: Any, db: Any) -> tuple[Any, Any]:
    """The served Library's database and the admitted request's context."""
    ctx = getattr(handler, "_prks_request_context", None)
    if ctx is None:
        raise RuntimeError("a Person Group route ran without an admitted request context")
    return db.database, ctx


def _item_id(path: str) -> Optional[str]:
    """The group id of ``/api/person-groups/:id``, else ``None``."""
    if path.startswith(_PREFIX) and len(path.split("/")) == 4:
        return path.split("/")[-1]
    return None


def _send_revalidated(handler: Any, label: str, data: Any) -> None:
    etag = etag_for_representation(label, data)
    if handler._prks_if_none_match(etag):
        handler._send_json_not_modified(etag)
        return
    handler.send_json(200, data, etag=etag, precondition_checked=True)


def handle_get(handler: Any, db: Any, path: str) -> bool:
    if path == COLLECTION:
        _send_revalidated(handler, "person-groups", _commands().catalog(*_actor(handler, db)))
        return True
    if path.startswith(_PREFIX) and path.endswith("/sync-state") and len(path.split("/")) == 5:
        # REVISIONS ONLY. The catalogue already carries every group's
        # name, parent and description and the detail carries its
        # members, so echoing values here would make a second copy.
        data = _commands().sync_state(*_actor(handler, db), unquote(path.split("/")[3]))
        if data is None:
            handler.send_json(404, {"error": "Group not found"})
        else:
            _send_revalidated(handler, "person-group-state", data)
        return True
    g_id = _item_id(path)
    if g_id is not None:
        data = _commands().detail(*_actor(handler, db), g_id)
        if data:
            handler.send_json(200, data)
        else:
            handler.send_error(404, "Group not found")
        return True
    return False


def handle_person_state(handler: Any, db: Any, path: str) -> bool:
    """``GET /api/persons/:id/group-state``."""
    if not (path.startswith("/api/persons/") and path.endswith("/group-state") and len(path.split("/")) == 5):
        return False
    # Membership revisions for one Person, including tombstones: a pair
    # this device once removed has a revision, and adding it back is a
    # mutation of that scope rather than a first write.
    data = _commands().person_state(*_actor(handler, db), unquote(path.split("/")[3]))
    if data is None:
        handler.send_json(404, {"error": "Person not found"})
    else:
        _send_revalidated(handler, "person-group-state", data)
    return True


def handle_patch(handler: Any, db: Any, path: str, data: Any) -> bool:
    g_id = _item_id(path)
    if g_id is None:
        return False
    try:
        _commands().update(*_actor(handler, db), g_id, data)
    except ValueError as e:
        handler.send_json(400, {"error": str(e)})
        return True
    handler.send_json(200, {"status": "updated"})
    return True


def handle_post(handler: Any, db: Any, path: str, data: Any) -> bool:
    if path == COLLECTION:
        pid = data.get("parent_id")
        if pid == "":
            pid = None
        try:
            g_id = _commands().create(
                *_actor(handler, db),
                name=data.get("name", ""),
                parent_id=pid,
                parent_name=None if pid else data.get("parent_name"),
                description=data.get("description", "") or "",
            )
        except ValueError as e:
            handler.send_json(400, {"error": str(e)})
            return True
        handler.send_json(200, {"id": g_id})
        return True
    if path.startswith(_PREFIX) and path.endswith("/members"):
        parts = path.split("/")
        if len(parts) != 5:
            handler.send_error(404, "API endpoint not found")
            return True
        try:
            _commands().add_member(*_actor(handler, db), parts[3], data.get("person_id", ""))
        except ValueError as e:
            handler.send_json(400, {"error": str(e)})
            return True
        handler.send_json(200, {"status": "added"})
        return True
    return False


def handle_delete(handler: Any, db: Any, path: str) -> bool:
    if not path.startswith(_PREFIX):
        return False
    parts = path.split("/")
    # /api/person-groups/{group_id}/members/{person_id}
    if len(parts) == 6 and parts[4] == "members":
        _commands().remove_member(*_actor(handler, db), parts[3], parts[5])
        handler.send_json(200, {"status": "removed"})
    elif len(parts) == 4:
        try:
            _commands().delete(*_actor(handler, db), parts[3])
        except ValueError as e:
            handler.send_json(400, {"error": str(e)})
            return True
        handler.send_json(200, {"status": "deleted"})
    else:
        handler.send_error(404, "API endpoint not found")
    return True
