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

``server.py`` calls these handlers after host/origin, JSON body size, and
library-access checks. Each ``handle_*`` returns ``True`` when the path
belongs to this family (including refusals already sent), else ``False`` so
dispatch continues.
"""

from __future__ import annotations

from typing import Any, Optional
from urllib.parse import unquote

from backend.api import works as works_api
from backend.etags import etag_for_representation

COLLECTION = "/api/folders"
_PREFIX = COLLECTION + "/"


def _commands() -> Any:
    """``backend.domain.organization.folder_commands``, imported when a route runs.

    The served app loads no PostgreSQL module at import time
    (``tests/test_e2e_policy.py``).
    """
    from backend.domain.organization import folder_commands

    return folder_commands


def _actor(handler: Any, db: Any) -> tuple[Any, Any]:
    """The served Library's database and the admitted request's context."""
    ctx = getattr(handler, "_prks_request_context", None)
    if ctx is None:
        raise RuntimeError("a Folder route ran without an admitted request context")
    return db.database, ctx


def _pdfs_dir(db: Any) -> Optional[str]:
    """The data root's PDF directory; ``None`` when the Library has none (the command decides)."""
    return None if db.storage is None else db.storage.pdfs_dir


def _item_id(path: str) -> Optional[str]:
    """The Folder id of ``/api/folders/:id``, else ``None``."""
    if path.startswith(_PREFIX) and len(path.split("/")) == 4:
        return path.split("/")[-1]
    return None


def _send_revalidated(handler: Any, label: str, data: Any) -> None:
    etag = etag_for_representation(label, data)
    if handler._prks_if_none_match(etag):
        handler._send_json_not_modified(etag)
        return
    handler.send_json(200, data, etag=etag, precondition_checked=True)


# -- for the Work routes ----------------------------------------------------------

def exists(handler: Any, db: Any, folder_id: Optional[str]) -> bool:
    return _commands().exists(*_actor(handler, db), folder_id)


def ensure_uncategorized(handler: Any, db: Any) -> str:
    return _commands().ensure_uncategorized(*_actor(handler, db))


def file_work(handler: Any, db: Any, folder_id: Optional[str], work_id: Optional[str]) -> None:
    """File an unfiled Work; refusals raise ``ValueError``."""
    _commands().file_work(*_actor(handler, db), folder_id, work_id)


def move_work(handler: Any, db: Any, work_id: Optional[str], folder_id: Any) -> None:
    """Assign, move or clear a Work's Folder; refusals raise ``ValueError``."""
    _commands().move_work(*_actor(handler, db), work_id, folder_id)


# -- handlers -----------------------------------------------------------------------

def handle_get(handler: Any, db: Any, path: str) -> bool:
    if path == COLLECTION:
        # The catalog IS the revision source, so build it once and derive
        # the ETag from it.
        _send_revalidated(handler, "folders", _commands().catalog(*_actor(handler, db)))
        return True
    f_id = _item_id(path)
    if f_id is not None:
        data = _commands().detail(*_actor(handler, db), f_id, _pdfs_dir(db))
        if data:
            handler.send_json(200, data)
        else:
            handler.send_error(404, "Folder not found")
        return True
    if path.startswith("/api/works/") and path.endswith("/related_folders"):
        handler.send_json(200, _commands().related(*_actor(handler, db), path.split("/")[3]))
        return True
    if path.startswith(_PREFIX) and path.endswith("/sync-state") and len(path.split("/")) == 5:
        # REVISIONS ONLY. The folder catalogue already carries every title,
        # parent and description.
        data = _commands().sync_state(*_actor(handler, db), unquote(path.split("/")[3]))
        if data is None:
            handler.send_json(404, {"error": "Folder not found"})
        else:
            _send_revalidated(handler, "folder-state", data)
        return True
    return False


def handle_work_state(handler: Any, db: Any, path: str) -> bool:
    """``GET /api/works/:id/folder-state``."""
    if not (path.startswith("/api/works/") and path.endswith("/folder-state") and len(path.split("/")) == 5):
        return False
    data = _commands().work_state(*_actor(handler, db), unquote(path.split("/")[3]))
    if data is None:
        handler.send_json(404, {"error": "Work not found"})
    else:
        _send_revalidated(handler, "work-folder-state", data)
    return True


def handle_patch(handler: Any, db: Any, path: str, data: Any) -> bool:
    f_id = _item_id(path)
    if f_id is None:
        return False
    actor = _actor(handler, db)
    # A rename stales every member Work's cached detail (it embeds
    # folder_title), so collect the members BEFORE the write and report
    # them -- membership cannot change in this request.
    renames = isinstance(data, dict) and "title" in data
    member_ids = _commands().member_work_ids(*actor, f_id) if renames else []
    try:
        _commands().update(*actor, f_id, data)
    except ValueError as e:
        handler.send_json(400, {"error": str(e)})
        return True
    handler.send_json(200, {"status": "updated", "member_work_ids": member_ids})
    return True


def handle_post(handler: Any, db: Any, path: str, data: Any) -> bool:
    if path == COLLECTION:
        try:
            f_id = _commands().create(
                *_actor(handler, db),
                title=data.get("title", "Untitled Folder"),
                description=data.get("description", ""),
                parent_id=data.get("parent_id"),
            )
        except ValueError as e:
            handler.send_json(409, {"error": str(e)})
        else:
            handler.send_json(200, {"id": f_id})
        return True
    if not (path.startswith(_PREFIX) and path.endswith("/works")):
        return False
    parts = path.split("/")
    if len(parts) != 5 or parts[4] != "works":
        handler.send_error(404, "API endpoint not found")
        return True
    f_id = parts[3]
    w_id = (data.get("work_id") or "").strip()
    if not w_id:
        handler.send_json(400, {"error": "work_id is required"})
        return True
    if not exists(handler, db, f_id):
        handler.send_json(404, {"error": "Folder not found"})
        return True
    if works_api.thumbnail_fields(handler, db, w_id) is None:
        handler.send_json(404, {"error": "Work not found"})
        return True
    try:
        file_work(handler, db, f_id, w_id)
    except ValueError as e:
        handler.send_json(409, {"error": str(e)})
        return True
    handler.send_json(200, {"status": "added"})
    return True


def handle_delete(handler: Any, db: Any, path: str) -> bool:
    f_id = _item_id(path)
    if f_id is None:
        return False
    try:
        _commands().delete(*_actor(handler, db), f_id)
    except ValueError as e:
        handler.send_json(409, {"error": str(e)})
        return True
    handler.send_json(200, {"status": "deleted"})
    return True
