"""Saved Views HTTP controller.

Cohesive CRUD for ``/api/saved-views`` and ``/api/saved-views/:id``.

Parse path/body shape, invoke ``PRKSDatabase`` Saved View methods, map
``SavedViewError`` to HTTP status/JSON. Name/search normalization and SQL stay
in ``db_manager`` — this module does not open transactions or touch SQLite.

``server.py`` calls these handlers after host/origin, JSON body size, and
library-access checks. Each ``handle_*`` returns ``True`` when the path belongs
to this family (including validation failures already sent), else ``False`` so
dispatch continues.
"""

from __future__ import annotations

import logging
from typing import Any, Optional
from urllib.parse import unquote

from backend.db_manager import SavedViewError
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


def _send_saved_view_error(handler, exc: SavedViewError) -> None:
    handler.send_json(int(exc.http_status), {"error": str(exc)})


def handle_get(handler, db, path: str) -> bool:
    if path == COLLECTION:
        handler.send_json(200, db.get_saved_views())
        return True
    vid = item_id(path)
    if vid is None:
        return False
    data = db.get_saved_view(vid)
    if data:
        handler.send_json(200, data)
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
        view = db.create_saved_view(data.get("name"), data.get("search"))
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
        view = db.update_saved_view(
            vid,
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
        db.delete_saved_view(vid)
    except SavedViewError as e:
        _send_saved_view_error(handler, e)
        return True
    LOGGER.info(
        "saved_view_deleted view_id=%s",
        safe_log_id(vid),
    )
    handler.send_json(200, {"status": "deleted"})
    return True
