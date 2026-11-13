"""Concepts HTTP controller (PG-C3 #642).

``/api/concepts`` (GET the catalog, each Concept with its ``mention_count``;
POST create, ``201``), ``/api/concepts/:id`` (GET with its aliases,
parents, children, ``mention_count`` and ``mentions``; PATCH name and
definition; DELETE), ``PUT /api/concepts/:id/parents`` and ``PUT
/api/concepts/:id/aliases`` (the whole set, as ``{parent_ids}`` /
``{aliases}`` or a bare JSON list), and the revision state ``GET
/api/concepts/:id/sync-state``. A ``ResearchError`` is its own status with
``{error, code}``.

The calls go to ``backend/domain/research/concept_commands`` for the
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

from backend.domain.research.rules import ResearchError
from backend.etags import etag_for_representation

COLLECTION = "/api/concepts"
_PREFIX = COLLECTION + "/"


def _commands() -> Any:
    """``backend.domain.research.concept_commands``, imported when a route runs.

    The served app loads no PostgreSQL module at import time
    (``tests/test_e2e_policy.py``).
    """
    from backend.domain.research import concept_commands

    return concept_commands


def _actor(handler: Any, db: Any) -> tuple[Any, Any]:
    """The served Library's database and the admitted request's context."""
    ctx = getattr(handler, "_prks_request_context", None)
    if ctx is None:
        raise RuntimeError("a Concept route ran without an admitted request context")
    return db.database, ctx


def _item_id(path: str) -> Optional[str]:
    """The Concept id of ``/api/concepts/:id`` (unquoted), else ``None``."""
    if path.startswith(_PREFIX) and len(path.split("/")) == 4:
        return unquote(path.split("/")[-1])
    return None


def _sub(path: str, leaf: str) -> Optional[str]:
    """The Concept id of ``/api/concepts/:id/<leaf>`` (unquoted), else ``None``."""
    parts = path.split("/")
    if path.startswith(_PREFIX) and len(parts) == 5 and parts[4] == leaf:
        return unquote(parts[3])
    return None


def _refused(handler: Any, error: ResearchError) -> None:
    handler.send_json(error.http_status, {"error": str(error), "code": error.code})


def handle_get(handler: Any, db: Any, path: str) -> bool:
    """The catalog and one Concept (the sync state is :func:`handle_sync_state`)."""
    if path == COLLECTION:
        handler.send_json(200, _commands().catalog_with_mentions(*_actor(handler, db)))
        return True
    cid = _item_id(path)
    if cid is None:
        return False
    item = _commands().detail_with_mentions(*_actor(handler, db), cid)
    if not item:
        handler.send_json(404, {"error": "Concept not found."})
        return True
    handler.send_json(200, item)
    return True


def handle_sync_state(handler: Any, db: Any, path: str) -> bool:
    """``GET /api/concepts/:id/sync-state``."""
    cid = _sub(path, "sync-state")
    if cid is None:
        return False
    # Revisions, plus the two AGGREGATE values. A field revision is a
    # number, but an identity and a hierarchy are sets: a client measuring
    # an edit against them needs what it last acknowledged, and the Concept
    # detail carries `parents` as objects rather than ids.
    data = _commands().sync_state(*_actor(handler, db), cid)
    if data is None:
        handler.send_json(404, {"error": "Concept not found"})
        return True
    etag = etag_for_representation("concept-state", data)
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
        item = _commands().create(*_actor(handler, db), data.get("name"), data.get("description", "") or "")
    except ResearchError as e:
        _refused(handler, e)
        return True
    handler.send_json(201, item)
    return True


def handle_patch(handler: Any, db: Any, path: str, data: Any) -> bool:
    cid = _item_id(path)
    if cid is None:
        return False
    if not isinstance(data, dict):
        handler.send_json(400, {"error": "JSON object body required"})
        return True
    try:
        item = _commands().update(
            *_actor(handler, db), cid,
            name=data.get("name") if "name" in data else None,
            description=data.get("description") if "description" in data else None,
        )
    except ResearchError as e:
        _refused(handler, e)
        return True
    handler.send_json(200, item)
    return True


def _set_body(data: Any, key: str) -> Any:
    """``data[key]`` from an object body, or the body itself when it is a bare list."""
    value = data.get(key) if isinstance(data, dict) else None
    if value is None and isinstance(data, list):
        value = data
    return value


def handle_put(handler: Any, db: Any, path: str, data: Any) -> bool:
    """``PUT /api/concepts/:id/parents`` and ``/aliases``."""
    cid = _sub(path, "parents")
    if cid is not None:
        try:
            item = _commands().replace_parents(*_actor(handler, db), cid, _set_body(data, "parent_ids"))
        except ResearchError as e:
            _refused(handler, e)
            return True
        handler.send_json(200, item)
        return True
    cid = _sub(path, "aliases")
    if cid is not None:
        try:
            item = _commands().replace_aliases(*_actor(handler, db), cid, _set_body(data, "aliases"))
        except ResearchError as e:
            _refused(handler, e)
            return True
        handler.send_json(200, item)
        return True
    return False


def handle_delete(handler: Any, db: Any, path: str) -> bool:
    cid = _item_id(path)
    if cid is None:
        return False
    try:
        _commands().delete(*_actor(handler, db), cid)
    except ResearchError as e:
        _refused(handler, e)
        return True
    handler.send_json(200, {"status": "deleted"})
    return True
