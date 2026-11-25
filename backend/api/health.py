"""Probes: ``GET``/``HEAD /healthz`` (liveness) and ``/readyz`` (readiness).

Public routes: neither runs session admission nor returns configuration.
``/healthz`` does no I/O. ``/readyz`` answers 200 only when the server
carries a readiness check (``backend.runtime.readiness``: pool, schema, setup,
paired root and reconciled start generation) and it passes; a server
started without one stays not ready. A trailing slash or a percent-encoded
spelling of the path reaches the same probe.
"""

from __future__ import annotations

import logging

from backend.runtime.log_safety import safe_error_type
from backend.api_contract.operation import Header, Operation, ok
from backend.web.http import Request, Response, bytes_response
from backend.web.routing import route

LOGGER = logging.getLogger("prks.server")


def _answer(ready: bool) -> Response:
    return bytes_response(200 if ready else 503, b"ok" if ready else b"not ready",
                          "text/plain; charset=utf-8", [("Cache-Control", "no-store")])


def healthz(req: Request) -> Response:
    return _answer(True)


def readyz(req: Request) -> Response:
    readiness = req.app.readiness
    try:
        ready = readiness is not None and bool(readiness.ready())
    except Exception as exc:  # a probe fails closed, never with a 500
        LOGGER.warning("readiness_check_failed error_type=%s", safe_error_type(exc))
        ready = False
    return _answer(ready)


_TEXT = "text/plain"
_NO_STORE = {"Cache-Control": Header("Always no-store.")}
_HEALTHZ = Operation(
    "healthz", "Liveness: the process answers", "probes",
    {200: ok({"type": "string", "const": "ok"}, "Alive.", media_type=_TEXT, headers=_NO_STORE)},
    description="Public: no session, no I/O.")
_READYZ = Operation(
    "readyz", "Readiness: database, schema, setup and storage root are ready", "probes",
    {200: ok({"type": "string", "const": "ok"}, "Ready.", media_type=_TEXT, headers=_NO_STORE),
     503: ok({"type": "string", "const": "not ready"}, "Not ready (or no readiness check).",
             media_type=_TEXT, headers=_NO_STORE)},
    description="Public: no session.")

ROUTES = (
    route("GET", "/healthz", healthz, contract=_HEALTHZ),
    route("HEAD", "/healthz", healthz, contract=_HEALTHZ.head()),
    route("GET", "/readyz", readyz, contract=_READYZ),
    route("HEAD", "/readyz", readyz, contract=_READYZ.head()),
)
