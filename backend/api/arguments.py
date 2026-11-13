"""Arguments HTTP controller (PG-C3 #642).

``/api/arguments`` (GET the catalog, optionally ``?kind=``, each Argument
with its ``mention_count``; POST create, ``201``), ``/api/arguments/:id``
(GET with its targets, citations, ``mention_count``, ``mentions`` and the
``verdicts``; PATCH name, kind and main text; DELETE), PUT
``/api/arguments/:id/sources`` and ``/api/arguments/:id/targets`` (the
whole list, as ``{"sources": [...]}``/``{"targets": [...]}`` or a bare
array), ``GET /api/argument-verdicts`` and the revision state
``GET /api/arguments/:id/sync-state``. A ``ResearchError`` is its own
status with ``{error, code}``.

The calls go to ``backend/domain/research/argument_commands`` for the
admitted request's context on the served Library's database. The commands
open the transactions, so authorization stays in ``library_query`` and
``library_command``, and a write's maintenance and epoch fence in
``library_command``. The Research index is the database's, so a delete has
no mentions to remove.

``server.py`` calls these handlers after host/origin, JSON body size, and
library-access checks. Each ``handle_*`` returns ``True`` when the path
belongs to this family (including refusals already sent), else ``False`` so
dispatch continues.
"""

from __future__ import annotations

from typing import Any, Optional
from urllib.parse import unquote

from backend.domain.research.rules import ResearchError
from backend.etags import etag_for_representation

COLLECTION = "/api/arguments"
VERDICTS = "/api/argument-verdicts"
_PREFIX = COLLECTION + "/"


def _commands() -> Any:
    """``backend.domain.research.argument_commands``, imported when a route runs.

    The served app loads no PostgreSQL module at import time
    (``tests/test_e2e_policy.py``).
    """
    from backend.domain.research import argument_commands

    return argument_commands


def _actor(handler: Any, db: Any) -> tuple[Any, Any]:
    """The served Library's database and the admitted request's context."""
    ctx = getattr(handler, "_prks_request_context", None)
    if ctx is None:
        raise RuntimeError("an Argument route ran without an admitted request context")
    return db.database, ctx


def _item_id(path: str) -> Optional[str]:
    """The Argument id of ``/api/arguments/:id`` (unquoted), else ``None``."""
    if path.startswith(_PREFIX) and len(path.split("/")) == 4:
        return unquote(path.split("/")[-1])
    return None


def _sub(path: str, leaf: str) -> Optional[str]:
    """The Argument id of ``/api/arguments/:id/<leaf>`` (unquoted), else ``None``."""
    parts = path.split("/")
    if path.startswith(_PREFIX) and len(parts) == 5 and parts[4] == leaf:
        return unquote(parts[3])
    return None


def _refused(handler: Any, error: ResearchError) -> None:
    handler.send_json(error.http_status, {"error": str(error), "code": error.code})


def handle_get(handler: Any, db: Any, path: str, query: dict) -> bool:
    """The verdicts, the catalog and one Argument (the sync state is :func:`handle_sync_state`)."""
    if path == VERDICTS:
        handler.send_json(200, _commands().verdicts(*_actor(handler, db)))
        return True
    if path == COLLECTION:
        kind = (query.get("kind") or [""])[0].strip() or None
        try:
            items = _commands().catalog_with_mentions(*_actor(handler, db), kind)
        except ResearchError as e:
            _refused(handler, e)
            return True
        handler.send_json(200, items)
        return True
    aid = _item_id(path)
    if aid is None:
        return False
    item = _commands().detail_with_mentions(*_actor(handler, db), aid)
    if not item:
        handler.send_json(404, {"error": "Argument not found."})
    else:
        handler.send_json(200, item)
    return True


def handle_sync_state(handler: Any, db: Any, path: str) -> bool:
    """``GET /api/arguments/:id/sync-state``."""
    aid = _sub(path, "sync-state")
    if aid is None:
        return False
    # REVISIONS ONLY, aggregates included: the Argument detail already
    # carries its sources and its targets, and a second copy would be a
    # second thing to keep true.
    data = _commands().sync_state(*_actor(handler, db), aid)
    if data is None:
        handler.send_json(404, {"error": "Argument not found"})
        return True
    etag = etag_for_representation("argument-state", data)
    if handler._prks_if_none_match(etag):
        handler._send_json_not_modified(etag)
        return True
    handler.send_json(200, data, etag=etag, precondition_checked=True)
    return True


def handle_post(handler: Any, db: Any, path: str, data: Any) -> bool:
    if path != COLLECTION:
        return False
    if not isinstance(data, dict):
        handler.send_json(400, {"error": "JSON object body required"})
        return True
    try:
        item = _commands().create(
            *_actor(handler, db),
            name=data.get("name"),
            kind=data.get("kind"),
            main_text=data.get("main_text", "") or "",
            sources=data.get("sources"),
            targets=data.get("targets"),
        )
    except ResearchError as e:
        _refused(handler, e)
        return True
    handler.send_json(201, item)
    return True


def handle_patch(handler: Any, db: Any, path: str, data: Any) -> bool:
    aid = _item_id(path)
    if aid is None:
        return False
    if not isinstance(data, dict):
        handler.send_json(400, {"error": "JSON object body required"})
        return True
    try:
        item = _commands().update(
            *_actor(handler, db),
            aid,
            name=data.get("name") if "name" in data else None,
            kind=data.get("kind") if "kind" in data else None,
            main_text=data.get("main_text") if "main_text" in data else None,
        )
    except ResearchError as e:
        _refused(handler, e)
        return True
    handler.send_json(200, item)
    return True


def _list_body(data: Any, key: str) -> Any:
    """``data[key]`` of an object body, else the bare array, else ``None`` (the domain refuses)."""
    value = data.get(key) if isinstance(data, dict) else None
    if value is None and isinstance(data, list):
        value = data
    return value


def handle_put(handler: Any, db: Any, path: str, data: Any) -> bool:
    for leaf in ("sources", "targets"):
        aid = _sub(path, leaf)
        if aid is None:
            continue
        replace = _commands().replace_sources if leaf == "sources" else _commands().replace_targets
        try:
            item = replace(*_actor(handler, db), aid, _list_body(data, leaf))
        except ResearchError as e:
            _refused(handler, e)
            return True
        handler.send_json(200, item)
        return True
    return False


def handle_delete(handler: Any, db: Any, path: str) -> bool:
    aid = _item_id(path)
    if aid is None:
        return False
    try:
        _commands().delete(*_actor(handler, db), aid)
    except ResearchError as e:
        _refused(handler, e)
        return True
    handler.send_json(200, {"status": "deleted"})
    return True
