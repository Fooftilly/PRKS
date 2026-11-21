"""Imported citation styles HTTP controller: ``/api/citation-styles`` (Citation V1, #41).

- ``GET /api/citation-styles``: ``{"styles": [{id, title, created_at}]}``, the
  Library's imported styles by title.
- ``GET /api/citation-styles/:id``: ``{id, title, csl}``, the style's text for
  the browser's formatter; ``404`` when the Library has no such style.
- ``POST /api/citation-styles`` with ``{"csl": "<style …>"}``: checks and stores
  the style (``backend.domain.works.custom_citation_styles``) and answers its
  summary with ``201``; a refused file is ``400`` with the reason.
- ``DELETE /api/citation-styles/:id``: ``{"deleted": id}``, or ``404``.

An import or a delete is Library administration (``backend/api/session.py``
admits only ``ADMIN_LIBRARY``); the commands open the transactions.
``server.py`` calls these handlers after host/origin, JSON body size and
library-access checks; each returns ``False`` for another path.
"""

from __future__ import annotations

from typing import Any, Optional

PATH = "/api/citation-styles"
_PREFIX = PATH + "/"


def _commands() -> Any:
    """``custom_citation_styles``, imported when a route runs (no PostgreSQL module at import time)."""
    from backend.domain.works import custom_citation_styles

    return custom_citation_styles


def _actor(handler: Any, db: Any) -> tuple[Any, Any]:
    ctx = getattr(handler, "_prks_request_context", None)
    if ctx is None:
        raise RuntimeError("a citation style route ran without an admitted request context")
    return db.database, ctx


def _style_id(path: str) -> Optional[str]:
    if not path.startswith(_PREFIX):
        return None
    rest = path[len(_PREFIX):]
    return rest if rest and "/" not in rest else None


def _not_found(handler: Any) -> bool:
    handler.send_json(404, {"error": "Citation style not found."})
    return True


def handle_get(handler: Any, db: Any, path: str) -> bool:
    if path == PATH:
        handler.send_json(200, {"styles": _commands().listed(*_actor(handler, db))})
        return True
    style_id = _style_id(path)
    if style_id is None:
        return False
    style = _commands().read(*_actor(handler, db), style_id)
    if style is None:
        return _not_found(handler)
    handler.send_json(200, style)
    return True


def handle_post(handler: Any, db: Any, path: str, data: Any) -> bool:
    if path != PATH:
        return False
    commands = _commands()
    try:
        summary = commands.imported(*_actor(handler, db), data)
    except commands.StyleRefused as e:
        handler.send_json(400, {"error": str(e)})
        return True
    handler.send_json(201, summary)
    return True


def handle_delete(handler: Any, db: Any, path: str) -> bool:
    style_id = _style_id(path)
    if style_id is None:
        return False
    if not _commands().deleted(*_actor(handler, db), style_id):
        return _not_found(handler)
    handler.send_json(200, {"deleted": style_id})
    return True
