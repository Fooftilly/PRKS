"""Positions HTTP controller (PG-C3 #642).

``/api/positions`` (GET the catalog, POST create, ``201``),
``/api/positions/:id`` (GET with its Arguments, PATCH name and description,
DELETE) and the revision state ``GET /api/positions/:id/sync-state``. The
wire shapes are ``backend/api_contract/positions.py``: a malformed body is
``400`` with the contract's envelope, and a ``ResearchError`` is its own
status with ``research_error_envelope``. A write's answer is not validated
after the commit (``docs/api-contract-boundary.md``).

The calls go to ``backend/domain/research/position_commands`` for the
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

from backend.api_contract.boundary import dump_response
from backend.api_contract.errors import research_error_envelope
from backend.api_contract.positions import (
    PositionCreateRequest,
    PositionDeleted,
    PositionDetail,
    PositionSummary,
    PositionSyncState,
    PositionUpdateRequest,
    parse_position_request,
)
from backend.domain.research.rules import ResearchError
from backend.etags import etag_for_representation

COLLECTION = "/api/positions"
_PREFIX = COLLECTION + "/"


def _commands() -> Any:
    """``backend.domain.research.position_commands``, imported when a route runs.

    The served app loads no PostgreSQL module at import time
    (``tests/test_e2e_policy.py``).
    """
    from backend.domain.research import position_commands

    return position_commands


def _actor(handler: Any, db: Any) -> tuple[Any, Any]:
    """The served Library's database and the admitted request's context."""
    ctx = getattr(handler, "_prks_request_context", None)
    if ctx is None:
        raise RuntimeError("a Position route ran without an admitted request context")
    return db.database, ctx


def _item_id(path: str) -> Optional[str]:
    """The Position id of ``/api/positions/:id`` (unquoted), else ``None``."""
    if path.startswith(_PREFIX) and len(path.split("/")) == 4:
        return unquote(path.split("/")[-1])
    return None


def _refused(handler: Any, error: ResearchError) -> None:
    handler.send_json(error.http_status, research_error_envelope(message=str(error), code=error.code))


def handle_get(handler: Any, db: Any, path: str) -> bool:
    """The catalog and one Position (the sync state is :func:`handle_sync_state`)."""
    if path == COLLECTION:
        handler.send_json(200, dump_response(PositionSummary, _commands().catalog(*_actor(handler, db))))
        return True
    pid = _item_id(path)
    if pid is None:
        return False
    item = _commands().detail(*_actor(handler, db), pid)
    if item:
        handler.send_json(200, dump_response(PositionDetail, item))
    else:
        handler.send_json(404, research_error_envelope(message="Position not found."))
    return True


def handle_sync_state(handler: Any, db: Any, path: str) -> bool:
    """``GET /api/positions/:id/sync-state``."""
    parts = path.split("/")
    if not (path.startswith(_PREFIX) and path.endswith("/sync-state") and len(parts) == 5):
        return False
    # REVISIONS ONLY: the Position detail already carries both values, and a
    # second copy would be a second thing to keep true.
    data = _commands().sync_state(*_actor(handler, db), unquote(parts[3]))
    if data is None:
        handler.send_json(404, research_error_envelope(message="Position not found"))
        return True
    body = dump_response(PositionSyncState, data)
    etag = etag_for_representation("position-state", body)
    if handler._prks_if_none_match(etag):
        handler._send_json_not_modified(etag)
        return True
    handler.send_json(200, body, etag=etag, precondition_checked=True)
    return True


def handle_post(handler: Any, db: Any, path: str, data: Any) -> bool:
    if path != COLLECTION:
        return False
    req, err = parse_position_request(PositionCreateRequest, data)
    if err is not None or req is None:
        handler.send_json(400, err)
        return True
    try:
        item = _commands().create(*_actor(handler, db), req.name, req.description)
    except ResearchError as e:
        _refused(handler, e)
        return True
    # Domain already committed; do not dump_response here (see
    # docs/api-contract-boundary.md — post-commit response validation).
    handler.send_json(201, item)
    return True


def handle_patch(handler: Any, db: Any, path: str, data: Any) -> bool:
    pid = _item_id(path)
    if pid is None:
        return False
    req, err = parse_position_request(PositionUpdateRequest, data)
    if err is not None or req is None:
        handler.send_json(400, err)
        return True
    try:
        item = _commands().update(*_actor(handler, db), pid, **req.domain_field_kwargs())
    except ResearchError as e:
        _refused(handler, e)
        return True
    # Domain already committed; do not dump_response here (see
    # docs/api-contract-boundary.md — post-commit response validation).
    handler.send_json(200, item)
    return True


def handle_delete(handler: Any, db: Any, path: str) -> bool:
    pid = _item_id(path)
    if pid is None:
        return False
    try:
        _commands().delete(*_actor(handler, db), pid)
    except ResearchError as e:
        _refused(handler, e)
        return True
    handler.send_json(200, dump_response(PositionDeleted, {"status": "deleted"}))
    return True
