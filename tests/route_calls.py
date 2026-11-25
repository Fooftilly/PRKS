"""Call one served route in-process, for the bound actor.

:func:`call_route` resolves ``method target`` through the served route table
and runs its handler with :func:`tests.acting_context.current_context`, with
no threaded HTTP server.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from tests.acting_context import current_context


class _Bytes:
    """A request body already in memory."""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self.consumed = not payload

    def read(self, size: int = -1) -> bytes:
        self.consumed = True
        out, self._payload = self._payload, b""
        return out

    def read1(self, size: int = -1) -> bytes:
        return self.read(size)

    def settimeout(self, value: Optional[float]) -> None:
        pass

    def gettimeout(self) -> Optional[float]:
        return None


def call_route(library: Any, method: str, target: str, body: Any = None) -> tuple[int, Any]:
    """``(status, JSON body)`` of ``method target`` through the served route table.

    The route runs for the bound actor (:func:`tests.acting_context.current_context`)
    on ``library``; a JSON body is read by the dispatcher's own body rules.
    """
    from backend.web.app import App
    from backend.web.dispatch import BodyRefused, read_json_object
    from backend.web.http import Headers, Request
    from backend.web.routing import Body, Match

    path, _, query = target.partition("?")
    app = App(library=library)
    found = app.routes.resolve(method, path)
    assert isinstance(found, Match), (method, target)
    headers: list[tuple[str, str]] = []
    payload = b""
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        headers = [("Content-Type", "application/json"), ("Content-Length", str(len(payload)))]
    request = Request(method, path, query, Headers(headers), app, params=found.params,
                      context=current_context(), body=_Bytes(payload))
    if found.route.body_kind is Body.JSON:
        if body is None:
            request.headers = Headers([("Content-Type", "application/json")])
        try:
            read_json_object(request)
        except BodyRefused as refused:
            return refused.response.status, json.loads(refused.response.body)
    response = found.route.handler(request)
    return response.status, json.loads(response.body) if response.body else None
