"""Publishers HTTP controller: ``/api/publishers`` and its aliases (PG-C3 #642).

``GET /api/publishers?used=1`` lists every publisher with its aliases and
Work count (without ``used`` the answer is ``[]``); ``POST`` creates one or
answers the publisher already named so; ``DELETE /api/publishers/:id``
removes one. ``POST``/``DELETE /api/publishers/:id/aliases`` add and remove
an alias (the alias to remove is the ``alias`` query parameter). The wire
shapes are ``backend/api_contract/publishers.py``; a domain refusal
(``ValueError``) is ``400`` with its message.

The calls go to ``backend/domain/people/publisher_commands`` for the
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

from backend.api_contract.boundary import dump_response, parse_request
from backend.api_contract.publishers import (
    PublisherAliasRequest,
    PublisherCreateRequest,
    PublisherDeleted,
    PublisherInUse,
)

COLLECTION = "/api/publishers"
_PREFIX = COLLECTION + "/"


def _commands() -> Any:
    """``backend.domain.people.publisher_commands``, imported when a route runs.

    The served app loads no PostgreSQL module at import time
    (``tests/test_e2e_policy.py``).
    """
    from backend.domain.people import publisher_commands

    return publisher_commands


def _actor(handler: Any, db: Any) -> tuple[Any, Any]:
    """The served Library's database and the admitted request's context."""
    ctx = getattr(handler, "_prks_request_context", None)
    if ctx is None:
        raise RuntimeError("a publisher route ran without an admitted request context")
    return db.database, ctx


def _is_aliases(path: str) -> bool:
    return path.startswith(_PREFIX) and path.endswith("/aliases")


def _aliases_owner(path: str) -> Optional[str]:
    """The publisher id of ``/api/publishers/:id/aliases``, else ``None``."""
    parts = path.split("/")
    if len(parts) == 5 and parts[4] == "aliases":
        return parts[3]
    return None


def handle_get(handler: Any, db: Any, path: str, query: dict[str, list[str]]) -> bool:
    if path != COLLECTION:
        return False
    if query.get("used", [""])[0] in ("1", "true", "yes"):
        handler.send_json(200, dump_response(PublisherInUse, _commands().in_use(*_actor(handler, db))))
    else:
        handler.send_json(200, [])
    return True


def handle_post(handler: Any, db: Any, path: str, data: Any) -> bool:
    if path == COLLECTION:
        request, err = parse_request(PublisherCreateRequest, data)
        if err is not None or request is None:
            handler.send_json(400, err)
            return True
        try:
            # Domain already committed on return; send its dict as is
            # (docs/api-contract-boundary.md).
            out = _commands().create(*_actor(handler, db), request.name)
        except ValueError as e:
            handler.send_json(400, {"error": str(e)})
            return True
        handler.send_json(200, out)
        return True
    if not _is_aliases(path):
        return False
    publisher_id = _aliases_owner(path)
    if publisher_id is None:
        handler.send_error(404, "API endpoint not found")
        return True
    alias_request, err = parse_request(PublisherAliasRequest, data)
    if err is not None or alias_request is None:
        handler.send_json(400, err)
        return True
    try:
        _commands().add_alias(*_actor(handler, db), publisher_id, alias_request.alias.strip())
    except ValueError as e:
        handler.send_json(400, {"error": str(e)})
        return True
    handler.send_json(200, {"status": "added"})
    return True


def handle_delete(handler: Any, db: Any, path: str, query: dict[str, list[str]]) -> bool:
    if _is_aliases(path):
        publisher_id = _aliases_owner(path)
        if publisher_id is None:
            handler.send_error(404, "API endpoint not found")
            return True
        alias = unquote((query.get("alias") or [""])[0])
        if not alias.strip():
            handler.send_json(400, {"error": "missing alias"})
            return True
        if _commands().delete_alias(*_actor(handler, db), publisher_id, alias):
            handler.send_json(200, dump_response(PublisherDeleted, {"status": "deleted"}))
        else:
            handler.send_json(404, {"error": "alias not found"})
        return True
    if path.startswith(_PREFIX) and len(path.split("/")) == 4:
        _commands().delete(*_actor(handler, db), path.split("/")[-1])
        handler.send_json(200, dump_response(PublisherDeleted, {"status": "deleted"}))
        return True
    return False
