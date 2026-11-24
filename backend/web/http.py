"""The typed request and response every PRKS route handles (#1424).

A route is a function ``(Request) -> Response`` in the route table
(:mod:`backend.web.routing`). The ASGI adapter (:mod:`backend.web.asgi`)
builds the :class:`Request` from the ASGI scope and sends the
:class:`Response`; the dispatcher (:mod:`backend.web.dispatch`) admits the
request, parses its body once and adds the headers every answer carries.

A response body is bytes, or a :data:`Streamer` the adapter calls with a
``write`` function once the head is sent (PDF bytes, backup progress). A
``write`` after the client left raises ``BrokenPipeError``.
"""

from __future__ import annotations

import gzip
import json
import logging
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, Callable, Iterable, Iterator, Optional, Protocol
from urllib.parse import parse_qs

from backend.log_safety import safe_log_id, safe_log_label, safe_route
from backend.performance import clock_ns, record_span

if TYPE_CHECKING:
    from backend.db.database import Database
    from backend.domain.identity.request_context import RequestContext
    from backend.library import PostgresLibrary
    from backend.web.app import App

LOGGER = logging.getLogger("prks.server")

#: Minimum uncompressed JSON size before gzip (``Accept-Encoding: gzip``).
JSON_GZIP_MIN_BYTES = 1024
JSON_CONTENT_TYPE = "application/json; charset=utf-8"

Write = Callable[[bytes], None]
Streamer = Callable[[Write], None]

_NO_BODY_STATUSES = frozenset({HTTPStatus.NO_CONTENT, HTTPStatus.RESET_CONTENT, HTTPStatus.NOT_MODIFIED})


class Headers:
    """Request header fields, matched without regard to case; a name may repeat."""

    def __init__(self, fields: Iterable[tuple[str, str]] = ()) -> None:
        self._fields: list[tuple[str, str]] = list(fields)

    def get(self, name: str, default: Optional[str] = None) -> Optional[str]:
        """The first value of ``name``, else ``default``."""
        wanted = name.lower()
        for key, value in self._fields:
            if key.lower() == wanted:
                return value
        return default

    def get_all(self, name: str) -> list[str]:
        """Every value of ``name``, in order (empty when absent)."""
        wanted = name.lower()
        return [value for key, value in self._fields if key.lower() == wanted]

    def items(self) -> list[tuple[str, str]]:
        return list(self._fields)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and self.get(name) is not None

    def __len__(self) -> int:
        return len(self._fields)

    def __iter__(self) -> Iterator[str]:
        return (key for key, _ in self._fields)


class RequestBody(Protocol):
    """The request body as a stream; reads wait at most the set timeout."""

    def read(self, size: int = -1) -> bytes: ...

    def read1(self, size: int = -1) -> bytes: ...

    def settimeout(self, value: Optional[float]) -> None: ...

    def gettimeout(self) -> Optional[float]: ...

    @property
    def consumed(self) -> bool:
        """Whether the whole body has been read."""
        ...


class EmptyBody:
    """The body of a request built without one (tests, internal callers)."""

    def __init__(self) -> None:
        self._timeout: Optional[float] = None

    def read(self, size: int = -1) -> bytes:
        return b""

    def read1(self, size: int = -1) -> bytes:
        return b""

    def settimeout(self, value: Optional[float]) -> None:
        self._timeout = value

    def gettimeout(self) -> Optional[float]:
        return self._timeout

    @property
    def consumed(self) -> bool:
        return True


@dataclass
class Request:
    """One HTTP request, as the routes see it.

    ``path`` is the request path as sent (not percent-decoded); ``params``
    are the route pattern's segments of it, also as sent. ``json`` is the
    parsed body of a JSON route (``{}`` when none was sent: ``json_absent``).
    ``context`` is the :class:`RequestContext` admission authorized.
    """

    method: str
    path: str
    query_string: str
    headers: Headers
    app: "App"
    request_id: str = ""
    scheme: str = "http"
    peer: tuple[str, int] = ("", 0)
    body: RequestBody = field(default_factory=EmptyBody)
    params: dict[str, str] = field(default_factory=dict)
    context: "Optional[RequestContext]" = None
    host: Optional[tuple[str, Optional[int]]] = None
    json: Any = None
    json_absent: bool = True
    #: The Library epoch the response names (``X-PRKS-Library-Epoch``);
    #: admission sets it, and a restore or a refusal changes it.
    library_epoch: Optional[int] = None
    _query: Optional[dict[str, list[str]]] = None

    @property
    def target(self) -> str:
        """The request target: path and query string."""
        return f"{self.path}?{self.query_string}" if self.query_string else self.path

    @property
    def query(self) -> dict[str, list[str]]:
        """The query string, decoded once (blank values dropped)."""
        if self._query is None:
            self._query = parse_qs(self.query_string)
        return self._query

    def query_value(self, name: str) -> str:
        """The first value of query parameter ``name``, else ``""``."""
        return (self.query.get(name) or [""])[0]

    @property
    def ctx(self) -> "RequestContext":
        """The admitted request's context; admission sets it before any route runs."""
        if self.context is None:
            raise RuntimeError("route reached without an admitted request context")
        return self.context

    @property
    def library(self) -> "PostgresLibrary":
        """The served Library."""
        return self.app.require_library()

    @property
    def actor(self) -> "tuple[Database, RequestContext]":
        """The served Library's database and the admitted request's context."""
        return self.library.database, self.ctx

    @property
    def data(self) -> dict[str, Any]:
        """The JSON object body (the dispatcher refused anything else)."""
        if not isinstance(self.json, dict):
            raise RuntimeError("route reached without a JSON object body")
        return self.json

    def log_fields(self) -> dict[str, str]:
        """Method, templated route and request id: what a log line may carry."""
        return {
            "method": safe_log_label(self.method or "unknown", fallback="unknown"),
            "route": safe_route(self.path or "/"),
            "request_id": safe_log_id(self.request_id),
        }

    def if_none_match(self, etag: str) -> bool:
        """Whether ``If-None-Match`` names ``etag`` (or ``*``)."""
        client = (self.headers.get("If-None-Match") or "").strip()
        if not client:
            return False
        if client == "*":
            return True
        want = _etag_value_for_compare(etag)
        return any(_etag_value_for_compare(part) == want for part in client.split(","))


@dataclass
class Response:
    """One answer: status, headers and a bytes body or a :data:`Streamer`.

    ``close`` ends the connection after the answer. ``cleanup`` runs once the
    answer is sent or abandoned (an open file a stream reads, for one).
    """

    status: int
    headers: list[tuple[str, str]] = field(default_factory=list)
    body: bytes = b""
    stream: Optional[Streamer] = None
    close: bool = False
    cleanup: Optional[Callable[[], None]] = None

    def header(self, name: str) -> Optional[str]:
        wanted = name.lower()
        for key, value in self.headers:
            if key.lower() == wanted:
                return value
        return None


def _etag_value_for_compare(raw: str) -> str:
    s = (raw or "").strip()
    if s.upper().startswith("W/"):
        s = s[2:].lstrip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        s = s[1:-1]
    return s


def phrase(code: int) -> str:
    try:
        return HTTPStatus(code).phrase
    except ValueError:
        return ""


# -- answers ------------------------------------------------------------------------


def not_modified(etag: str, cache_control: str = "private, no-cache", *, vary: bool = True) -> Response:
    """304 for a representation the client already holds."""
    headers = [("ETag", etag), ("Cache-Control", cache_control)]
    if vary:
        headers.append(("Vary", "Accept-Encoding"))
    return Response(304, headers)


def json_response(
    request: Request,
    status: int,
    payload: Any,
    *,
    etag: Optional[str] = None,
    cache_control: Optional[str] = None,
    headers: Iterable[tuple[str, str]] = (),
) -> Response:
    """JSON ``payload``, gzipped for a client that accepts it.

    With ``etag`` a 200 carries it (and ``private, no-cache`` unless
    ``cache_control`` says otherwise), and a client that holds it gets 304.
    """
    if etag and status == 200 and request.if_none_match(etag):
        return not_modified(etag)
    t0 = clock_ns()
    body = json.dumps(payload).encode("utf-8")
    try:
        record_span("json_encode", clock_ns() - t0)
    except Exception:
        pass
    accept = (request.headers.get("Accept-Encoding") or "").lower()
    use_gzip = "gzip" in accept and len(body) >= JSON_GZIP_MIN_BYTES
    if use_gzip:
        t1 = clock_ns()
        body = gzip.compress(body, compresslevel=6)
        try:
            record_span("gzip", clock_ns() - t1)
        except Exception:
            pass
    out = [("Content-Type", JSON_CONTENT_TYPE)]
    if etag and status == 200:
        out.append(("ETag", etag))
        out.append(("Cache-Control", cache_control or "private, no-cache"))
    elif cache_control:
        out.append(("Cache-Control", cache_control))
    out.append(("Vary", "Accept-Encoding"))
    out.extend(headers)
    out.append(("Content-Length", str(len(body))))
    if use_gzip:
        out.append(("Content-Encoding", "gzip"))
    return Response(status, out, body)


def reply_response(status: int, payload: dict[str, Any], headers: Iterable[tuple[str, str]] = ()) -> Response:
    """An admission answer (``backend.api.session.Reply``): JSON, never stored."""
    body = json.dumps(payload).encode("utf-8")
    out = [("Content-Type", JSON_CONTENT_TYPE), ("Cache-Control", "no-store"), *headers]
    out.append(("Content-Length", str(len(body))))
    return Response(status, out, body)


def error_response(status: int, message: Optional[str] = None) -> Response:
    """A plain-text error answer; the connection closes after it."""
    message = message or phrase(status)
    LOGGER.debug("http_error status=%s", int(status))
    body = b""
    headers: list[tuple[str, str]] = [("Connection", "close")]
    if status >= 200 and status not in _NO_BODY_STATUSES:
        body = f"{int(status)} {message}\n".encode("utf-8", "replace")
        headers.append(("Content-Type", "text/plain; charset=utf-8"))
        headers.append(("Content-Length", str(len(body))))
    return Response(status, headers, body, close=True)


def not_found(message: str = "API endpoint not found") -> Response:
    return error_response(404, message)


def bytes_response(
    status: int, body: bytes, content_type: str, headers: Iterable[tuple[str, str]] = ()
) -> Response:
    out = [("Content-Type", content_type), *headers, ("Content-Length", str(len(body)))]
    return Response(status, out, body)
