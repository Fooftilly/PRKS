"""Playlists HTTP controller (PG-C3 #642).

``/api/playlists`` (GET the catalog with an ETag, POST create),
``/api/playlists/:id`` (GET with its Works in order, PATCH fields,
DELETE), ``POST /api/playlists/:id/items`` (put a Work in it, at an
optional ``position``), ``DELETE /api/playlists/:id/items/:work_id``,
``POST /api/playlists/:id/reorder``, and the revision states
``GET /api/playlists/:id/sync-state`` and ``GET /api/works/:id/playlist-state``.
A domain refusal (``ValueError``) on add or reorder is ``400`` with its
message. :func:`add_item` also serves the Work upload's optional
``playlist_id``.

The calls go to ``backend/domain/organization/playlist_commands`` for the
admitted request's context on the served Library's database. The commands
open the transactions, so authorization stays in ``library_query`` and
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

COLLECTION = "/api/playlists"
_PREFIX = COLLECTION + "/"


def _commands() -> Any:
    """``backend.domain.organization.playlist_commands``, imported when a route runs.

    The served app loads no PostgreSQL module at import time
    (``tests/test_e2e_policy.py``).
    """
    from backend.domain.organization import playlist_commands

    return playlist_commands


def _actor(handler: Any, db: Any) -> tuple[Any, Any]:
    """The served Library's database and the admitted request's context."""
    ctx = getattr(handler, "_prks_request_context", None)
    if ctx is None:
        raise RuntimeError("a Playlist route ran without an admitted request context")
    return db.database, ctx


def _pdfs_dir(db: Any) -> Optional[str]:
    """The data root's PDF directory; ``None`` when the Library has none (the command decides)."""
    return None if db.storage is None else db.storage.pdfs_dir


def _item_id(path: str) -> Optional[str]:
    """The Playlist id of ``/api/playlists/:id``, else ``None``."""
    if path.startswith(_PREFIX) and len(path.split("/")) == 4:
        return path.split("/")[-1]
    return None


def _sub(path: str, leaf: str) -> Optional[str]:
    """The Playlist id of ``/api/playlists/:id/<leaf>``, else ``None``."""
    parts = path.split("/")
    if len(parts) == 5 and parts[4] == leaf:
        return parts[3]
    return None


def _send_revalidated(handler: Any, label: str, data: Any) -> None:
    etag = etag_for_representation(label, data)
    if handler._prks_if_none_match(etag):
        handler._send_json_not_modified(etag)
        return
    handler.send_json(200, data, etag=etag, precondition_checked=True)


def add_item(handler: Any, db: Any, playlist_id: Optional[str], work_id: Optional[str],
             position: Optional[int] = None) -> None:
    """Put a Work in a Playlist for this request; refusals raise ``ValueError``."""
    _commands().add_item(*_actor(handler, db), playlist_id, work_id, position)


def handle_get(handler: Any, db: Any, path: str) -> bool:
    if path == COLLECTION:
        _send_revalidated(handler, "playlists", _commands().catalog(*_actor(handler, db)))
        return True
    pl_id = _item_id(path)
    if pl_id is not None:
        data = _commands().detail(*_actor(handler, db), pl_id, _pdfs_dir(db))
        if data:
            handler.send_json(200, data)
        else:
            handler.send_error(404, "Playlist not found")
        return True
    if path.startswith(_PREFIX) and path.endswith("/sync-state") and len(path.split("/")) == 5:
        # REVISIONS ONLY: the three field revisions and the ORDER revision.
        # The playlist detail already carries its items in order, so echoing
        # them here would make a second cached copy.
        data = _commands().sync_state(*_actor(handler, db), unquote(path.split("/")[3]))
        if data is None:
            handler.send_json(404, {"error": "Playlist not found"})
        else:
            _send_revalidated(handler, "playlist-state", data)
        return True
    return False


def handle_work_state(handler: Any, db: Any, path: str) -> bool:
    """``GET /api/works/:id/playlist-state``."""
    if not (path.startswith("/api/works/") and path.endswith("/playlist-state") and len(path.split("/")) == 5):
        return False
    data = _commands().work_state(*_actor(handler, db), unquote(path.split("/")[3]))
    if data is None:
        handler.send_json(404, {"error": "Work not found"})
    else:
        _send_revalidated(handler, "work-playlist-state", data)
    return True


def handle_patch(handler: Any, db: Any, path: str, data: Any) -> bool:
    pl_id = _item_id(path)
    if pl_id is None:
        return False
    _commands().update(*_actor(handler, db), pl_id, data)
    handler.send_json(200, {"status": "updated"})
    return True


def _position(data: Any) -> Optional[int]:
    try:
        raw = data.get("position", None)
        return int(raw) if raw is not None and str(raw).strip() != "" else None
    except Exception:
        return None


def handle_post(handler: Any, db: Any, path: str, data: Any) -> bool:
    if path == COLLECTION:
        pl_id = _commands().create(
            *_actor(handler, db),
            title=data.get("title", "") or "",
            description=data.get("description", "") or "",
            original_url=data.get("original_url", "") or "",
        )
        handler.send_json(200, {"id": pl_id})
        return True
    if path.startswith(_PREFIX) and path.endswith("/items"):
        pl_id = _sub(path, "items")
        if pl_id is None:
            handler.send_error(404, "API endpoint not found")
            return True
        w_id = (data.get("work_id") or "").strip()
        if not w_id:
            handler.send_json(400, {"error": "work_id is required"})
            return True
        try:
            add_item(handler, db, pl_id, w_id, _position(data))
        except ValueError as e:
            handler.send_json(400, {"error": str(e)})
            return True
        handler.send_json(200, {"status": "added"})
        return True
    if path.startswith(_PREFIX) and path.endswith("/reorder"):
        pl_id = _sub(path, "reorder")
        if pl_id is None:
            handler.send_error(404, "API endpoint not found")
            return True
        work_ids = data.get("work_ids", [])
        if not isinstance(work_ids, list):
            handler.send_json(400, {"error": "work_ids must be a JSON array"})
            return True
        try:
            _commands().reorder(*_actor(handler, db), pl_id, [str(x) for x in work_ids])
        except ValueError as e:
            handler.send_json(400, {"error": str(e)})
            return True
        handler.send_json(200, {"status": "reordered"})
        return True
    return False


def handle_delete(handler: Any, db: Any, path: str) -> bool:
    pl_id = _item_id(path)
    if pl_id is not None:
        _commands().delete(*_actor(handler, db), pl_id)
        handler.send_json(200, {"status": "deleted"})
        return True
    if path.startswith(_PREFIX) and "/items/" in path:
        # /api/playlists/{id}/items/{work_id}
        parts = path.split("/")
        if len(parts) == 6 and parts[4] == "items":
            _commands().remove_item(*_actor(handler, db), parts[3], parts[5])
            handler.send_json(200, {"status": "removed"})
        else:
            handler.send_error(404, "API endpoint not found")
        return True
    return False
