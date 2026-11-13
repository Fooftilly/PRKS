"""Saved Views HTTP controller.

Cohesive CRUD for ``/api/saved-views`` and ``/api/saved-views/:id``.

Parse path/body shape, run ``backend/domain/saved_views/commands`` for the
admitted request's context on the served Library's database, map
``SavedViewError`` to HTTP status/JSON. The wire shape is
``backend/api_contract/saved_views.py``: reads go through ``dump_response``;
writes send the committed domain row without a post-commit dump (see
docs/api-contract-boundary.md). Request field types stay domain refusals. Name/search normalization and SQL stay
in ``backend/domain/saved_views``; the commands open the transactions, so
authorization, maintenance and the epoch fence stay in ``library_command``
and ``library_query``.

``server.py`` calls these handlers after host/origin, JSON body size, and
library-access checks. Each ``handle_*`` returns ``True`` when the path belongs
to this family (including validation failures already sent), else ``False`` so
dispatch continues.
"""

from __future__ import annotations

import logging
from typing import Any, Optional
from urllib.parse import unquote

from backend.api_contract.boundary import dump_response
from backend.api_contract.saved_views import SavedView, SavedViewDeleted
from backend.domain.saved_views import commands
from backend.domain.saved_views.rules import SavedViewError
from backend.log_safety import safe_log_id, safe_log_label

LOGGER = logging.getLogger("prks.api.saved_views")

COLLECTION = "/api/saved-views"


def item_id(path: str) -> Optional[str]:
    """Return the Saved View id when ``path`` is ``/api/saved-views/:id``."""
    if not path.startswith(COLLECTION + "/"):
        return None
    parts = path.split("/")
    if len(parts) != 4:
        return None
    return unquote(parts[-1])


def handles(path: str) -> bool:
    return path == COLLECTION or item_id(path) is not None


def _actor(handler, db) -> tuple[Any, Any]:
    """The served Library's database and the admitted request's context."""
    ctx = getattr(handler, "_prks_request_context", None)
    if ctx is None:
        raise RuntimeError("a Saved View route ran without an admitted request context")
    return db.database, ctx


def _send_saved_view_error(handler, exc: SavedViewError) -> None:
    handler.send_json(int(exc.http_status), {"error": str(exc)})


def handle_get(handler, db, path: str) -> bool:
    if path == COLLECTION:
        handler.send_json(200, dump_response(SavedView, commands.all_views(*_actor(handler, db))))
        return True
    vid = item_id(path)
    if vid is None:
        return False
    data = commands.view(*_actor(handler, db), vid)
    if data:
        handler.send_json(200, dump_response(SavedView, data))
    else:
        handler.send_json(404, {"error": "Saved View not found."})
    return True


def handle_post(handler, db, path: str, data: Any) -> bool:
    if path != COLLECTION:
        return False
    if not isinstance(data, dict):
        handler.send_json(400, {"error": "JSON object body required"})
        return True
    try:
        view = commands.create(*_actor(handler, db), data.get("name"), data.get("search"))
    except SavedViewError as e:
        _send_saved_view_error(handler, e)
        return True
    LOGGER.info(
        "saved_view_created view_id=%s mode=%s",
        safe_log_id(view.get("id")),
        safe_log_label((view.get("search") or {}).get("mode")),
    )
    handler.send_json(201, view)
    return True


def handle_patch(handler, db, path: str, data: Any) -> bool:
    vid = item_id(path)
    if vid is None:
        return False
    if not isinstance(data, dict):
        handler.send_json(400, {"error": "JSON object body required"})
        return True
    has_name = "name" in data
    has_search = "search" in data
    if not has_name and not has_search:
        handler.send_json(400, {"error": "Nothing to update."})
        return True
    try:
        view = commands.update(
            *_actor(handler, db), vid,
            name=data.get("name") if has_name else None,
            search=data.get("search") if has_search else None,
        )
    except SavedViewError as e:
        _send_saved_view_error(handler, e)
        return True
    LOGGER.info(
        "saved_view_updated view_id=%s definition_changed=%s",
        safe_log_id(view.get("id")),
        "true" if has_search else "false",
    )
    handler.send_json(200, view)
    return True


def handle_delete(handler, db, path: str) -> bool:
    vid = item_id(path)
    if vid is None:
        return False
    try:
        commands.delete(*_actor(handler, db), vid)
    except SavedViewError as e:
        _send_saved_view_error(handler, e)
        return True
    # Do not log path-derived ids: CodeQL treats URL segments as
    # user-controlled even after safe_log_id, and privacy policy forbids
    # logging raw library identifiers from the request path.
    LOGGER.info("saved_view_deleted")
    handler.send_json(200, dump_response(SavedViewDeleted, {"status": "deleted"}))
    return True
