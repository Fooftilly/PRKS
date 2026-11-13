"""Application settings HTTP controller: ``/api/settings`` (PG-C3 #642).

GET answers the Library's settings; PATCH checks the body is a JSON object,
applies the patch through ``backend/domain/settings/commands`` for the
admitted request's context, and answers the settings as they now are. A
refused key is ``400`` with its message. The commands open the transactions,
so authorization (a patch needs ``ADMIN_LIBRARY``; ``backend/api/session.py``
also admits only that), maintenance and the epoch fence stay in
``library_command`` and ``library_query``.

``server.py`` calls these handlers after host/origin, JSON body size, and
library-access checks. Each ``handle_*`` returns ``True`` when the path is
``/api/settings``, else ``False`` so dispatch continues.
"""

from __future__ import annotations

from typing import Any

PATH = "/api/settings"


def _commands() -> Any:
    """``backend.domain.settings.commands``, imported when a route runs.

    The served app loads no PostgreSQL module at import time
    (``tests/test_e2e_policy.py``).
    """
    from backend.domain.settings import commands

    return commands


def _actor(handler: Any, db: Any) -> tuple[Any, Any]:
    """The served Library's database and the admitted request's context."""
    ctx = getattr(handler, "_prks_request_context", None)
    if ctx is None:
        raise RuntimeError("a settings route ran without an admitted request context")
    return db.database, ctx


def handle_get(handler: Any, db: Any, path: str) -> bool:
    if path != PATH:
        return False
    handler.send_json(200, _commands().response(*_actor(handler, db)))
    return True


def handle_patch(handler: Any, db: Any, path: str, data: Any) -> bool:
    if path != PATH:
        return False
    if not isinstance(data, dict):
        handler.send_json(400, {"error": "JSON object body required"})
        return True
    actor = _actor(handler, db)
    try:
        _commands().patch(*actor, data)
    except ValueError as e:
        handler.send_json(400, {"error": str(e)})
        return True
    handler.send_json(200, _commands().response(*actor))
    return True
