"""Admit one request, run its route and finish its answer (#1424).

The ASGI adapter (:mod:`backend.web.asgi`) calls :func:`begin`,
:func:`dispatch` and :func:`finish` on a request thread for every request:

1. A URL holding NUL is refused (400) before anything else.
2. The Host header must name this server (400 malformed, 421 untrusted).
3. A path outside ``/api/`` is a public route of the table (the probes) or
   a frontend file (``GET``/``HEAD`` only; anything else 405).
4. An ``/api/`` request passes admission (``backend.api.session``; during a
   first run's setup ``backend.api.setup``), which answers the identity
   routes itself and admits every other route with its context.
5. The route table finds the route (404 unknown path, 405 other method),
   the route's body is read (:func:`read_json_body`: one JSON object, else
   one 400/413/415 envelope) and the route runs.
6. A refusal raised in the route is answered as admission answers it, a
   ``NotPortedError`` 501, anything else a logged 500 that names no data.

:func:`finalize` then adds what every answer carries: the security headers
(#1305), HSTS over PRKS's own TLS, ``X-Request-ID``, ``Server-Timing``, the
Library epoch and ``Connection: close`` when request body was left unread.
"""

from __future__ import annotations

import itertools
import json
import logging
import math
import re
from typing import Any, NoReturn, Optional, Union
from urllib.parse import unquote

from backend.log_safety import safe_error_type, safe_log_id, safe_log_label, safe_route
from backend.performance import (
    begin_request,
    clear_request,
    finish_request,
    is_excluded_route,
    server_timing_header,
    set_response_bytes,
    set_status,
)
from backend.web.hosts import is_trusted_request_host, json_content_type_allowed, parse_request_host, parse_request_origin
from backend.web.http import Request, Response, error_response, json_response, reply_response
from backend.web.routing import Body, Match
from backend.web.static import serve_frontend

LOGGER = logging.getLogger("prks.server")

# Sent on every response served over PRKS's own TLS (#1339).
HSTS_VALUE = "max-age=31536000"
# Sent on every response (#1305). The policy allows only PRKS's own scripts:
# no inline script or handler, no eval. 'wasm-unsafe-eval' lets the PDF viewer
# compile its PDFium WebAssembly, and blob: workers and images are what that
# viewer makes. No inline style either (ZAP 10055): per-element CSS variables
# go through CSSOM (prksCssVarClass), and the Vue bundle adopts its CSS as a
# constructable stylesheet. Images are PRKS's own too: a video thumbnail or a
# note figure on another site is served as a saved copy (/api/remote-images,
# #1351). The only other site PRKS talks to is YouTube: its player frame and
# the oEmbed lookup behind New File's autofill.
# Nothing may frame PRKS, and no page URL leaves in Referer.
CONTENT_SECURITY_POLICY = "; ".join((
    "default-src 'self'",
    "script-src 'self' 'wasm-unsafe-eval'",
    "style-src 'self'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    "connect-src 'self' data: blob: https://www.youtube.com",
    "worker-src 'self' blob:",
    "media-src 'self' blob:",
    "frame-src https://www.youtube.com",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'self'",
    "frame-ancestors 'none'",
))
SECURITY_HEADERS = (
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Content-Security-Policy", CONTENT_SECURITY_POLICY),
    ("Referrer-Policy", "no-referrer"),
)

#: The largest JSON request body a route reads.
MAX_JSON_BODY_BYTES = 50 * 1024 * 1024
#: The methods PRKS serves; anything else is 501.
METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"})

_LONE_SURROGATE = re.compile("[\ud800-\udfff]")
_JSON_WALK_DONE = object()


# -- request body -------------------------------------------------------------------


class BodyRefused(Exception):
    """The request body was refused; ``response`` says why."""

    def __init__(self, response: Response) -> None:
        super().__init__(response.status)
        self.response = response


def _invalid_request(error: str) -> NoReturn:
    """Refuse a body the route cannot take: the one 400 envelope (``code`` ``invalid_request``)."""
    raise BodyRefused(_bare_json(400, {"error": error, "code": "invalid_request"}))


def _bare_json(status: int, payload: dict[str, Any]) -> Response:
    body = json.dumps(payload).encode("utf-8")
    return Response(status, [("Content-Type", "application/json; charset=utf-8"),
                             ("Vary", "Accept-Encoding"), ("Content-Length", str(len(body)))], body)


def _refuse_non_finite(text: str) -> Any:
    raise ValueError("JSON number out of range")


def _finite_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("JSON number out of range")
    return value


def json_holds_lone_surrogate(value: Any) -> bool:
    """A lone surrogate anywhere in a decoded JSON document (keys included).

    Iterative: the document may be nested as deep as the decoder allows.
    """
    if isinstance(value, str):
        return _LONE_SURROGATE.search(value) is not None
    # One iterator per open container: memory bounded by depth, not width.
    stack: list[Any] = [iter((value,))]
    while stack:
        item = next(stack[-1], _JSON_WALK_DONE)
        if item is _JSON_WALK_DONE:
            stack.pop()
        elif isinstance(item, str):
            if _LONE_SURROGATE.search(item):
                return True
        elif isinstance(item, dict):
            stack.append(itertools.chain.from_iterable(item.items()))
        elif isinstance(item, list):
            stack.append(iter(item))
    return False


def decode_json(payload: bytes) -> Any:
    """``payload`` as JSON; ``ValueError`` for anything a route could not store.

    Refused: text that is not UTF-8 JSON, nesting past the decoder's
    recursion limit, ``NaN``/``Infinity`` and numbers past the float range
    (``1e309``; #1304), integers past Python's digit limit, and lone
    surrogates (no column, log line or response can carry one).
    """
    try:
        data = json.loads(payload, parse_constant=_refuse_non_finite, parse_float=_finite_float)
    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
        raise ValueError("invalid_json") from exc
    if json_holds_lone_surrogate(data):
        raise ValueError("invalid_json")
    return data


def read_json_body(request: Request) -> tuple[Any, bool]:
    """``(document, absent)``: the request's JSON body, read once.

    ``absent`` when no ``Content-Length`` or an empty body was sent. Raises
    :class:`BodyRefused` for a missing or other media type (415), a bad or
    too large ``Content-Length`` (400/413), a failed read or a body that is
    not JSON (400).
    """
    types = request.headers.get_all("Content-Type")
    if len(types) != 1 or not json_content_type_allowed(types[0]):
        LOGGER.info("request_rejected reason=unsupported_media_type request_id=%s",
                    safe_log_id(request.request_id))
        raise BodyRefused(_bare_json(415, {"error": "unsupported_media_type"}))
    raw_length = request.headers.get("Content-Length")
    if raw_length is None:
        return {}, True
    try:
        content_length = int(raw_length)
    except (TypeError, ValueError):
        _invalid_request("invalid Content-Length")
    if content_length < 0:
        _invalid_request("invalid Content-Length")
    if content_length > MAX_JSON_BODY_BYTES:
        raise BodyRefused(_bare_json(413, {"error": "request_too_large"}))
    try:
        payload = request.body.read(content_length) if content_length else b""
    except Exception:
        _invalid_request("request_read_failed")
    if not payload:
        return {}, True
    try:
        return decode_json(payload), False
    except ValueError:
        _invalid_request("invalid_json")


def read_json_object(request: Request) -> None:
    """Read a JSON route's body into ``request.json``: an object, or ``{}`` when absent."""
    data, absent = read_json_body(request)
    if not isinstance(data, dict):
        _invalid_request("JSON object body required")
    request.json, request.json_absent = data, absent


# -- the request's lifecycle --------------------------------------------------------


def begin(request: Request) -> None:
    """Start the request's performance record (``/api/`` requests only)."""
    try:
        path = request.path or "/"
        if path.startswith("/api/"):
            route = safe_route(path)
            begin_request(request.method or "GET", route,
                          excluded=is_excluded_route(route) or is_excluded_route(path))
    except Exception:
        pass


def finish(request: Request) -> None:
    """Close the request's performance record."""
    try:
        finish_request(request_id=request.request_id or "")
    except Exception:
        pass
    try:
        clear_request()
    except Exception:
        pass


def _rejected(request: Request, status: int, error: str, reason: str) -> Response:
    LOGGER.info("request_rejected reason=%s request_id=%s",
                safe_log_label(reason), safe_log_id(request.request_id))
    return json_response(request, status, {"error": error})


def _url_holds_nul(target: str) -> bool:
    """NUL in the request target, raw or after the one percent-decode routes apply.

    One round only: ``%2500`` is the literal text ``%00`` to every route
    (``parse_qs`` and ``unquote`` each decode once), a valid search.
    """
    return "\x00" in target or "\x00" in unquote(target)


def _check_host(request: Request) -> Optional[Response]:
    hosts = request.headers.get_all("Host")
    if len(hosts) != 1:
        return _rejected(request, 400, "invalid_host", "invalid_host")
    parsed = parse_request_host(hosts[0])
    if parsed is None:
        return _rejected(request, 400, "invalid_host", "invalid_host")
    hostname, _port = parsed
    app = request.app
    if not is_trusted_request_host(hostname, bind_host=app.bind_host, extra_hosts=app.trusted_hosts):
        return _rejected(request, 421, "untrusted_host", "untrusted_host")
    request.host = parsed
    return None


def dispatch(request: Request) -> Response:
    """The answer to ``request``, its headers not yet finalized (:func:`finalize`)."""
    if request.method not in METHODS:
        return error_response(501, "Unsupported method")
    if _url_holds_nul(request.target):
        # Before any route or static file: no identifier or query PRKS
        # stores can hold NUL, psycopg refuses to bind one and os.path
        # refuses it for a static file.
        answer = _rejected(request, 400, "invalid_request", "url_nul")
        answer.close = True
        return answer
    refused = _check_host(request)
    if refused is not None:
        return refused
    if not request.path.startswith("/api/"):
        return _public(request)
    admitted = _admit(request)
    if admitted is not None:
        return admitted
    found = request.app.routes.resolve(request.method, request.path)
    if not isinstance(found, Match):
        if found.allowed:
            answer = error_response(405, "Method Not Allowed")
            answer.headers.append(("Allow", ", ".join(found.allowed)))
            return answer
        return error_response(404, "API endpoint not found")
    return _run(request, found)


def _public(request: Request) -> Response:
    """A probe (a public route of the table), else a frontend file."""
    normalized = unquote(request.path).rstrip("/") or "/"
    found = request.app.routes.resolve(request.method, normalized)
    if isinstance(found, Match):
        return found.route.handler(request)
    if request.method in ("GET", "HEAD"):
        return serve_frontend(request)
    return error_response(405, "Method Not Allowed")


def _admit(request: Request) -> Optional[Response]:
    """Authentication admission for an ``/api/`` request (L-ID-1 #626).

    ``None`` to run the route, with ``request.context`` and the epoch set;
    else the answer. ``backend.api.session`` answers the identity routes
    and admits every other route only for a live session with the route's
    capability and, for a Library mutation, the Library's current epoch. A
    server started without identity fails closed: every ``/api/`` request
    is answered 503, never served without an actor (PG-C2 #641).
    """
    from backend.api import session as session_api
    from backend.api import setup as setup_api

    refusal: list[Response] = []

    def read_json() -> object:
        try:
            data, _absent = read_json_body(request)
        except BodyRefused as exc:
            refusal.append(exc.response)
            return session_api.ANSWERED
        return data

    def answered(outcome: Union[session_api.Reply, object, None]) -> Optional[Response]:
        if refusal:
            return refusal[0]
        if isinstance(outcome, session_api.Reply):
            return _session_reply(request, outcome)
        return None

    assert request.host is not None
    http_request = session_api.HttpRequest(
        method=request.method,
        path=request.path,
        headers=request.headers,
        peer=request.peer[0],
        request_host=request.host,
        scheme=request.scheme,
        parse_origin=parse_request_origin,
        read_json=read_json,
        route_fences_epoch=True,
    )
    app = request.app
    if app.setup is not None:
        # The setup phase of a first run (#612): only /api/setup answers.
        reply = setup_api.handle_setup_phase(app.setup, http_request)
        return answered(reply) or error_response(404, "API endpoint not found")
    if app.auth is None:
        return _rejected(request, 503, "identity_not_started", "identity_not_started")
    reply = setup_api.handle_after_setup(http_request)
    if reply is not None:
        return answered(reply) or error_response(404, "API endpoint not found")
    outcome = session_api.handle(app.auth, http_request)
    if isinstance(outcome, session_api.Admitted):
        request.library_epoch = outcome.epoch
        request.context = outcome.context
        return None
    return answered(outcome) or error_response(404, "API endpoint not found")


def _session_reply(request: Request, reply: Any) -> Response:
    """A ``backend.api.session.Reply``."""
    if reply.status >= 400:
        LOGGER.info("request_rejected reason=%s request_id=%s",
                    safe_log_label(str(reply.body.get("error", ""))), safe_log_id(request.request_id))
    return reply_response(reply.status, reply.body, reply.headers)


def _run(request: Request, found: Match) -> Response:
    request.params = found.params
    try:
        if found.route.body_kind is Body.JSON:
            read_json_object(request)
        return found.route.handler(request)
    except BodyRefused as exc:
        return exc.response
    except Exception as exc:
        return internal_error(request, exc)


def internal_error(request: Request, exc: Optional[BaseException] = None) -> Response:
    """The answer to ``exc`` raised in an admitted route (or a bare 500).

    A refusal or a mapped database error keeps its own status (§5.4, §9.4);
    a documented gap of the PostgreSQL Library is 501, never a fallback
    (PG-C1 #640). Anything else is logged by type and route only.
    """
    if exc is not None and request.context is not None:
        from backend.api import session as session_api
        from backend.domain.identity.authorization import AccessRefused
        from backend.library import NotPortedError

        if isinstance(exc, NotPortedError):
            return not_ported(request, str(exc))
        reply = session_api.route_failure(exc)
        if reply is not None:
            if isinstance(exc, AccessRefused):
                # Admission's epoch may be stale by now (a restore since);
                # the client re-reads it from /api/session.
                request.library_epoch = None
            return _session_reply(request, reply)
    fields = request.log_fields()
    if exc is not None:
        LOGGER.error(
            "unhandled_api_error method=%s route=%s request_id=%s error_type=%s",
            fields["method"], fields["route"], fields["request_id"], safe_error_type(exc),
            exc_info=exc,
        )
    else:
        LOGGER.error("internal_error method=%s route=%s request_id=%s",
                     fields["method"], fields["route"], fields["request_id"])
    return json_response(request, 500, {"error": "internal_error", "request_id": request.request_id})


def not_ported(request: Request, message: str) -> Response:
    """501 ``not_ported``: a route the PostgreSQL Library does not serve yet."""
    return json_response(request, 501, {"error": message, "code": "not_ported", "request_id": request.request_id})


# -- the answer's head --------------------------------------------------------------


def _declares_body(request: Request) -> bool:
    if request.headers.get_all("Transfer-Encoding"):
        return True
    raw = request.headers.get("Content-Length")
    return raw is not None and raw.strip() not in ("", "0")


def finalize(request: Request, response: Response) -> Response:
    """Add the headers every answer carries, and log it (metadata only)."""
    try:
        set_status(int(response.status))
        length = response.header("Content-Length")
        if length is not None:
            set_response_bytes(int(length))
    except Exception:
        pass
    headers = response.headers
    try:
        timing = server_timing_header()
        if timing:
            headers.append(("Server-Timing", timing))
    except Exception:
        pass
    if request.request_id:
        headers.append(("X-Request-ID", request.request_id))
    if request.library_epoch is not None:  # set only by an authenticated process's admission
        from backend.api.session import EPOCH_HEADER

        headers.append((EPOCH_HEADER, str(request.library_epoch)))
    # Every response declares its own Content-Type; no response may be
    # re-interpreted as HTML by MIME sniffing. Framing, CSP and Referer
    # limits ride on every answer too (#1305).
    headers.extend(SECURITY_HEADERS)
    if request.scheme == "https":
        # Served over PRKS's own TLS (#1339): browsers keep to https for a
        # year. Not includeSubDomains: other hosts under the name are not ours.
        headers.append(("Strict-Transport-Security", HSTS_VALUE))
    if not response.close and _declares_body(request) and not request.body.consumed:
        # Unread body bytes must never be parsed as the next request.
        response.close = True
    if response.close and response.header("Connection") is None:
        headers.append(("Connection", "close"))
    fields = request.log_fields()
    LOGGER.debug("request_access method=%s route=%s status=%s request_id=%s",
                 fields["method"], fields["route"], response.status, fields["request_id"])
    return response
