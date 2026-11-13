"""Tags HTTP controller (PG-C3 #642).

``/api/tags`` (GET lists every Tag, with an ETag; ``?used=1`` lists the
Tags in use; POST creates one), ``POST /api/tags/merge``, ``DELETE
/api/tags/:id``, ``POST``/``DELETE /api/tags/:id/aliases`` (the alias to
remove is the ``alias`` query parameter), and the Tags of a Work or a
Folder: ``POST /api/{works,folders}/:id/tags``, ``DELETE
/api/{works,folders}/:id/tags/:tag_id`` and ``GET
/api/{works,folders}/:id/tag-options``. A domain refusal (``ValueError``)
is ``400`` with its message; a delete of a missing Tag is ``404``.

The calls go to ``backend/domain/organization/tag_commands`` for the
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

COLLECTION = "/api/tags"
_PREFIX = COLLECTION + "/"
MERGE = "/api/tags/merge"


def _commands() -> Any:
    """``backend.domain.organization.tag_commands``, imported when a route runs.

    The served app loads no PostgreSQL module at import time
    (``tests/test_e2e_policy.py``).
    """
    from backend.domain.organization import tag_commands

    return tag_commands


def _actor(handler: Any, db: Any) -> tuple[Any, Any]:
    """The served Library's database and the admitted request's context."""
    ctx = getattr(handler, "_prks_request_context", None)
    if ctx is None:
        raise RuntimeError("a Tag route ran without an admitted request context")
    return db.database, ctx


def _owner(path: str) -> Optional[str]:
    """``"work"`` or ``"folder"`` for a path under ``/api/works/`` or ``/api/folders/``."""
    if path.startswith("/api/works/"):
        return "work"
    if path.startswith("/api/folders/"):
        return "folder"
    return None


def _is_aliases(path: str) -> bool:
    return path.startswith(_PREFIX) and path.endswith("/aliases")


def _aliases_owner(path: str) -> Optional[str]:
    """The Tag id of ``/api/tags/:id/aliases``, else ``None``."""
    parts = path.split("/")
    if len(parts) == 5 and parts[4] == "aliases":
        return parts[3]
    return None


def _send_catalog(handler: Any, data: Any) -> None:
    etag = etag_for_representation("tags", data)
    if handler._prks_if_none_match(etag):
        handler._send_json_not_modified(etag)
        return
    handler.send_json(200, data, etag=etag, precondition_checked=True)


def handle_get(handler: Any, db: Any, path: str, query: dict[str, list[str]]) -> bool:
    if path == COLLECTION:
        if query.get("used", [""])[0] in ("1", "true", "yes"):
            handler.send_json(200, _commands().in_use(*_actor(handler, db)))
        else:
            _send_catalog(handler, _commands().all_tags(*_actor(handler, db)))
        return True
    owner = _owner(path)
    if owner is None or not path.endswith("/tag-options") or len(path.split("/")) != 5:
        return False
    owner_id = path.split("/")[3]
    if owner == "work":
        data = _commands().work_options(*_actor(handler, db), owner_id)
        missing, label = "Work not found", "work-tag-options"
    else:
        data = _commands().folder_options(*_actor(handler, db), owner_id)
        missing, label = "Folder not found", "folder-tag-options"
    if data is None:
        handler.send_json(404, {"error": missing})
    else:
        handler.send_json(200, data, etag=etag_for_representation(label, data))
    return True


def _post_tags_route(handler: Any, db: Any, path: str, data: Any) -> bool:
    if path == MERGE:
        sid = (data.get("source_tag_id") or "").strip()
        tid = (data.get("target_tag_id") or "").strip()
        try:
            out = _commands().merge(*_actor(handler, db), sid, tid)
        except ValueError as e:
            handler.send_json(400, {"error": str(e)})
            return True
        handler.send_json(200, {"status": "merged", **out})
        return True
    if _is_aliases(path):
        tag_id = _aliases_owner(path)
        if tag_id is None:
            handler.send_error(404, "API endpoint not found")
            return True
        try:
            _commands().add_alias(*_actor(handler, db), tag_id, (data.get("alias") or "").strip())
        except ValueError as e:
            handler.send_json(400, {"error": str(e)})
            return True
        handler.send_json(200, {"status": "added"})
        return True
    if path == COLLECTION:
        try:
            out = _commands().create(*_actor(handler, db), data.get("name"), data.get("color", "#6d6cf7"))
        except ValueError as e:
            handler.send_json(400, {"error": str(e)})
            return True
        handler.send_json(200, out)
        return True
    return False


def handle_post(handler: Any, db: Any, path: str, data: Any) -> bool:
    if _post_tags_route(handler, db, path, data):
        return True
    owner = _owner(path)
    if owner is None or not path.endswith("/tags"):
        return False
    owner_id = path.split("/")[3]
    if owner == "work":
        _commands().tag_work(*_actor(handler, db), owner_id, data.get("tag_id"))
    else:
        _commands().tag_folder(*_actor(handler, db), owner_id, data.get("tag_id"))
    handler.send_json(200, {"status": "added"})
    return True


def _delete_link(handler: Any, db: Any, path: str) -> bool:
    owner = _owner(path)
    if owner is None or "/tags/" not in path:
        return False
    # /api/{works,folders}/{id}/tags/{tag_id}
    parts = path.split("/")
    if len(parts) < 6:
        handler.send_error(404, "API endpoint not found")
        return True
    if owner == "work":
        _commands().untag_work(*_actor(handler, db), parts[3], parts[5])
    else:
        _commands().untag_folder(*_actor(handler, db), parts[3], parts[5])
    handler.send_json(200, {"status": "removed"})
    return True


def handle_delete(handler: Any, db: Any, path: str, query: dict[str, list[str]]) -> bool:
    if _delete_link(handler, db, path):
        return True
    if _is_aliases(path):
        tag_id = _aliases_owner(path)
        if tag_id is None:
            handler.send_error(404, "API endpoint not found")
            return True
        alias = unquote((query.get("alias") or [""])[0])
        if not alias.strip():
            handler.send_json(400, {"error": "missing alias"})
            return True
        if _commands().delete_alias(*_actor(handler, db), tag_id, alias):
            handler.send_json(200, {"status": "deleted"})
        else:
            handler.send_json(404, {"error": "alias not found"})
        return True
    if path.startswith(_PREFIX) and len(path.split("/")) == 4:
        try:
            result = _commands().delete(*_actor(handler, db), path.split("/")[-1])
        except ValueError as e:
            msg = str(e)
            handler.send_json(404 if "not found" in msg.lower() else 400, {"error": msg})
            return True
        handler.send_json(200, result)
        return True
    return False
