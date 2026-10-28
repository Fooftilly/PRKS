"""HTTP authentication boundary (``docs/postgresql-architecture.md`` §9.3, §9.4, §10.4; L-ID-1 #626).

Used only when ``PRKS_AUTH`` is set (:mod:`backend.domain.identity.web_start`);
the SQLite application without it is unchanged. ``backend.server`` calls
:func:`handle` once for every ``/api/`` request, before any route runs, and
either dispatches the route (:class:`Admitted`) or sends the :class:`Reply`.

**Identity routes** (no Library epoch; they act on no Library content):

- ``POST /api/login`` ``{"login", "password"}``: the PR 3/5 login service.
  200 sets ``prks_session`` and a rotated ``prks_login_device`` and returns
  the session payload; 401 ``invalid_credentials`` for every refusal that
  could tell accounts apart; 429 ``too_many_attempts`` with ``Retry-After``;
  503 ``unavailable``.
- ``POST /api/logout``: revokes the presented session and clears its cookie;
  always 200 (signing out twice is not an error).
- ``GET /api/session``: 401 ``not_authenticated``, or the session payload:
  instance, user, Library (id, name, role, ``epoch``) and expiry, the scope
  key of §10.3. It also refreshes the session cookie's lifetime.

In loopback-owner mode there is no login or logout (404); every request acts
as the owner.

**Every other ``/api/`` route** needs a live session for the one Library this
process serves (401), then ``authorize()`` for the route's capability in its
own transaction: ``library_query`` for reads and for mutations that change no
Library content, ``library_command`` (maintenance lock, epoch fence) for
Library mutations. A Library mutation must send ``PRKS-Library-Epoch``
exactly once, as a decimal integer from 1 to the largest ``bigint`` without
sign, spaces or leading zeros (400 ``LIBRARY_EPOCH_REQUIRED`` otherwise,
checked before the capability); then, for an authorized actor only,
maintenance is 503 ``MAINTENANCE`` and another epoch than the Library's 409
``LIBRARY_EPOCH_CHANGED``. Admitted responses carry the Library's current
epoch in ``PRKS-Library-Epoch``.

The admission check runs in its own transaction and commits before the route
runs: today's routes still write SQLite, so no PostgreSQL transaction spans
their write. It is a gate in front of the route, not transactional
authorization or epoch fencing of the route's write (see "What admission does
not cover before the cutover" in ``docs/login-security.md``). Commands that move to PostgreSQL run inside ``library_command``
themselves and are fenced up to their commit. As with every caller of these
helpers, the route owns its Library predicates (``library_scope`` trust
boundary); nothing here scopes rows.

**CSRF** (every ``/api/`` mutation, the identity routes included): at most one
``Sec-Fetch-Site``, and if present it is ``same-origin``; then either exactly
one ``Origin`` that equals the expected origin, or no ``Origin`` with
``Sec-Fetch-Site: same-origin``. The expected origin is ``PRKS_PUBLIC_ORIGIN``
when it is set (and then only it: a plain-http request to the same host is
cross-origin), otherwise the request's own ``http`` origin from its validated
``Host``.
The expected origin is never derived from ``X-Forwarded-*``. The only
forwarded value used anywhere is ``X-Forwarded-For`` for the login limiter's
address key, and only from a peer in ``PRKS_TRUSTED_PROXIES``.

Nothing here logs. Tokens leave the process only in ``Set-Cookie``; no reply
body carries a token, a hash or a password.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import Message
from typing import Any, Optional, Union

from backend.db.errors import DatabaseError
from backend.domain.identity import sessions
from backend.domain.identity.authorization import AccessRefused, Capability, Refusal
from backend.domain.identity.library_scope import LibraryScope, library_command, library_query
from backend.domain.identity.login import DEVICE_TOKEN_LIFETIME, LoginOutcome, login
from backend.domain.identity.login_limiter import ClientAddress
from backend.domain.identity.request_context import RequestContext, parse_epoch
from backend.domain.identity.secret_tokens import (
    LOGIN_DEVICE_COOKIE,
    LOGIN_DEVICE_COOKIE_PATH,
    SESSION_COOKIE,
    SESSION_COOKIE_PATH,
)
from backend.domain.identity.sessions import ValidSession
from backend.domain.identity.web_start import AuthMode, Origin, OriginParser, WebAuth

EPOCH_HEADER = "PRKS-Library-Epoch"
LOGIN_PATH = "/api/login"
LOGOUT_PATH = "/api/logout"
SESSION_PATH = "/api/session"

_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_READ_METHODS = frozenset({"GET", "HEAD"})

# Mutations that change Library administration rather than content.
_ADMIN_MUTATIONS = frozenset({"/api/settings"})
# Reads only the Library owner may make: instance-level job diagnostics.
_ADMIN_READS = frozenset({"/api/diagnostics/jobs"})
# Mutations that change no Library content: authorized, never epoch-fenced.
_NON_LIBRARY_MUTATIONS = {
    "/api/client-errors": Capability.READ_LIBRARY,  # a browser error report, logged only
    "/api/diagnostics/performance/reset": Capability.ADMIN_LIBRARY,  # process counters
}


@dataclass(frozen=True)
class Reply:
    """A response :func:`handle` decided; the route does not run."""

    status: int
    body: dict[str, Any]
    headers: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Admitted:
    """Run the route; ``epoch`` is the Library's current epoch for the response header.

    ``context`` is the :class:`RequestContext` admission authorized: the
    server binds it for the route (:mod:`backend.pg_facade.context`), so a
    PostgreSQL facade call acts for this session and claimed epoch.
    """

    epoch: int
    context: RequestContext


class _Answered:
    """The body reader already answered the request (bad media type, bad JSON)."""


ANSWERED = _Answered()
Outcome = Union[Reply, Admitted, _Answered]


@dataclass(frozen=True)
class HttpRequest:
    method: str
    path: str
    headers: Message
    peer: str  # the TCP peer address
    request_host: tuple[str, Optional[int]]  # the validated Host header
    parse_origin: OriginParser
    # Reads the JSON body; returns ANSWERED when it already sent a refusal.
    read_json: Callable[[], object]

    def all(self, name: str) -> list[str]:
        return list(self.headers.get_all(name) or [])


# --- replies -------------------------------------------------------------------------------


def _error(status: int, error: str, headers: tuple[tuple[str, str], ...] = ()) -> Reply:
    return Reply(status, {"error": error}, headers)


def _refused(refusal: Refusal) -> Reply:
    return Reply(refusal.http_status, {"error": refusal.code, "code": refusal.code, "message": refusal.message})


_UNAVAILABLE = _error(503, "unavailable")


def _set_cookie(name: str, value: str, *, path: str, max_age: int, secure: bool) -> tuple[str, str]:
    parts = [f"{name}={value}", f"Path={path}", f"Max-Age={max(0, max_age)}", "HttpOnly", "SameSite=Strict"]
    if secure:
        parts.append("Secure")
    return ("Set-Cookie", "; ".join(parts))


def _session_cookie(auth: WebAuth, token: str, expires_at: datetime) -> tuple[str, str]:
    remaining = int((expires_at - datetime.now(timezone.utc)).total_seconds())
    return _set_cookie(
        SESSION_COOKIE, token, path=SESSION_COOKIE_PATH, max_age=remaining,
        secure=auth.settings.secure_cookies,
    )


def _cleared_session_cookie(auth: WebAuth) -> tuple[str, str]:
    return _set_cookie(
        SESSION_COOKIE, "", path=SESSION_COOKIE_PATH, max_age=0, secure=auth.settings.secure_cookies
    )


def _device_cookie(auth: WebAuth, token: str) -> tuple[str, str]:
    return _set_cookie(
        LOGIN_DEVICE_COOKIE, token, path=LOGIN_DEVICE_COOKIE_PATH,
        max_age=int(DEVICE_TOKEN_LIFETIME.total_seconds()), secure=auth.settings.secure_cookies,
    )


# --- request inputs ------------------------------------------------------------------------


def cookie(request: HttpRequest, name: str) -> Optional[str]:
    """The one value of cookie ``name``; ``None`` when absent or sent more than once."""
    values = []
    for header in request.all("Cookie"):
        for pair in header.split(";"):
            key, sep, value = pair.strip().partition("=")
            if sep and key.strip() == name:
                values.append(value.strip())
    return values[0] if len(values) == 1 else None


def _default_port(scheme: str) -> int:
    return 443 if scheme == "https" else 80


def _normalized(origin: Origin) -> tuple[str, str, int]:
    scheme, hostname, port = origin
    return scheme, hostname, _default_port(scheme) if port is None else port


def origin_allowed(request: HttpRequest, auth: WebAuth) -> bool:
    """The §9.3 CSRF rule for a mutation (see the module docs)."""
    fetch_sites = request.all("Sec-Fetch-Site")
    if len(fetch_sites) > 1:
        return False
    same_origin = bool(fetch_sites) and fetch_sites[0].strip().lower() == "same-origin"
    if fetch_sites and not same_origin:
        return False
    origins = request.all("Origin")
    if not origins:
        return same_origin
    if len(origins) != 1:
        return False
    origin = request.parse_origin(origins[0])
    if origin is None:
        return False
    expected = auth.settings.public_origin
    if expected is None:
        hostname, port = request.request_host
        expected = ("http", hostname, port)
    return _normalized(origin) == _normalized(expected)


def _epoch_claim(request: HttpRequest) -> Optional[int]:
    values = request.all(EPOCH_HEADER)
    return parse_epoch(values[0]) if len(values) == 1 else None


def route_capability(method: str, path: str) -> Capability:
    """What a route needs (§9.4): backups, settings and job diagnostics are Library administration."""
    if path == "/api/backups" or path.startswith("/api/backups/"):
        return Capability.ADMIN_LIBRARY
    if method in _MUTATING_METHODS:
        if path in _ADMIN_MUTATIONS:
            return Capability.ADMIN_LIBRARY
        return _NON_LIBRARY_MUTATIONS.get(path, Capability.EDIT_LIBRARY)
    if path in _ADMIN_READS:
        return Capability.ADMIN_LIBRARY
    return Capability.READ_LIBRARY


def _is_loopback_peer(peer: str) -> bool:
    try:
        address = ipaddress.ip_address(peer.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address.is_loopback


def _principal(auth: WebAuth, request: HttpRequest) -> Optional[ValidSession]:
    """Who the request is from: a resolved server-side session for the served Library, or nobody."""
    if auth.mode is AuthMode.LOOPBACK_OWNER:
        # The bind is loopback-only; a peer that is not is refused all the same.
        session = auth.loopback_session() if _is_loopback_peer(request.peer) else None
    else:
        # A session for another Library is nobody here, and is not renewed.
        session = sessions.resolve_session(
            auth.db, cookie(request, SESSION_COOKIE), idle=auth.settings.idle, library_id=auth.library_id
        )
    # The SQLite routes behind admission act on the one served Library's store.
    return session if session is not None and session.library_id == auth.library_id else None


# --- identity routes -----------------------------------------------------------------------


def _current_epoch(scope: LibraryScope) -> int:
    return scope.epoch


def _session_payload(auth: WebAuth, session: ValidSession) -> tuple[dict[str, Any], int]:
    """The ``GET /api/session`` body, read in one authorized snapshot."""
    ctx = RequestContext.for_session(session)

    def read(scope: LibraryScope) -> tuple[dict[str, Any], int]:
        library = scope.execute(
            "SELECT name FROM prks.libraries WHERE id = %(library_id)s"
        ).fetchone()
        member = scope.execute(
            "SELECT role FROM prks.library_memberships "
            "WHERE library_id = %(library_id)s AND user_id = %(user_id)s",
            {"user_id": session.user_id},
        ).fetchone()
        user = scope.execute_unscoped(
            "SELECT display_name FROM prks.users WHERE id = %s", (session.user_id,)
        ).fetchone()
        if library is None or member is None or user is None:  # authorize() just saw all three
            raise AccessRefused(Refusal.NOT_AUTHENTICATED)
        body = {
            "auth_mode": auth.mode.value,
            "instance_id": auth.instance_id,
            "user": {"id": session.user_id, "display_name": user["display_name"]},
            "library": {
                "id": scope.library_id,
                "name": library["name"],
                "role": member["role"],
                "epoch": scope.epoch,
            },
            "expires_at": session.expires_at.astimezone(timezone.utc).isoformat(),
        }
        return body, scope.epoch

    return library_query(auth.db, ctx, read)


def _get_session(auth: WebAuth, request: HttpRequest) -> Reply:
    try:
        session = _principal(auth, request)
        if session is None:
            return _refused(Refusal.NOT_AUTHENTICATED)
        body, epoch = _session_payload(auth, session)
    except AccessRefused as exc:
        return _refused(exc.refusal)
    except DatabaseError:
        return _UNAVAILABLE
    headers: list[tuple[str, str]] = [(EPOCH_HEADER, str(epoch))]
    presented = cookie(request, SESSION_COOKIE)
    if auth.mode is AuthMode.SESSION and presented is not None:
        # Sliding expiry: the cookie lives as long as the renewed row.
        headers.append(_session_cookie(auth, presented, session.expires_at))
    return Reply(200, body, tuple(headers))


def _client(auth: WebAuth, request: HttpRequest) -> ClientAddress:
    return ClientAddress.resolve(
        request.peer, request.all("X-Forwarded-For"), trusted=auth.settings.trusted_proxies
    )


def _login(auth: WebAuth, request: HttpRequest) -> Union[Reply, _Answered]:
    data = request.read_json()
    if isinstance(data, _Answered):
        return data
    if not isinstance(data, dict):
        return _error(400, "invalid_request")
    try:
        client = _client(auth, request)
    except ValueError:
        return _error(400, "invalid_request")
    result = login(
        auth.db,
        data.get("login"),
        data.get("password"),
        client=client,
        device_cookie=cookie(request, LOGIN_DEVICE_COOKIE),
        # Only the served Library: any other answers like a wrong password,
        # with no session, device token or limiter reset.
        library_id=auth.library_id,
        limits=auth.settings.limits,
        idle=auth.settings.idle,
        hasher=auth.hasher,
    )
    if result.outcome is LoginOutcome.THROTTLED:
        return _error(429, result.public_reason, (("Retry-After", str(result.retry_after_seconds or 1)),))
    if result.outcome is not LoginOutcome.SUCCEEDED or result.session is None or result.device_token is None:
        return _error(result.http_status, result.public_reason)
    return _signed_in(auth, request, result.session.token.reveal(), result.device_token.reveal())


def _signed_in(auth: WebAuth, request: HttpRequest, token: str, device_token: str) -> Reply:
    """Answer a successful login: replace the browser's earlier session and set both cookies."""
    headers = [_device_cookie(auth, device_token)]
    try:
        session = sessions.resolve_session(auth.db, token, idle=auth.settings.idle, library_id=auth.library_id)
        if session is None:
            # Signed out or removed in the meantime, or a Library this process
            # does not serve; the browser's earlier session is left as it was.
            sessions.revoke_session(auth.db, token)
            return _error(401, "invalid_credentials", tuple(headers))
        previous = cookie(request, SESSION_COOKIE)
        if previous is not None and previous != token:
            sessions.revoke_session(auth.db, previous)  # the browser's earlier session, if any
        body, epoch = _session_payload(auth, session)
    except AccessRefused:
        return _error(401, "invalid_credentials", tuple(headers))
    except DatabaseError:
        return Reply(503, {"error": "unavailable"}, tuple(headers))
    headers += [_session_cookie(auth, token, session.expires_at), (EPOCH_HEADER, str(epoch))]
    return Reply(200, {"signed_in": True, "session": body}, tuple(headers))


def _logout(auth: WebAuth, request: HttpRequest) -> Reply:
    presented = cookie(request, SESSION_COOKIE)
    if presented is not None:
        try:
            sessions.revoke_session(auth.db, presented)
        except DatabaseError:
            return _UNAVAILABLE
    return Reply(200, {"signed_out": True}, (_cleared_session_cookie(auth),))


def _identity_route(auth: WebAuth, request: HttpRequest) -> Optional[Outcome]:
    path, method = request.path, request.method
    if path == SESSION_PATH:
        return _get_session(auth, request) if method in _READ_METHODS else _error(405, "method_not_allowed")
    if path not in (LOGIN_PATH, LOGOUT_PATH):
        return None
    if auth.mode is AuthMode.LOOPBACK_OWNER:
        return _error(404, "not_found")
    if method != "POST":
        return _error(405, "method_not_allowed")
    return _login(auth, request) if path == LOGIN_PATH else _logout(auth, request)


# --- admission -----------------------------------------------------------------------------


def _admit(auth: WebAuth, request: HttpRequest) -> Union[Reply, Admitted]:
    method, path = request.method, request.path
    mutation = method in _MUTATING_METHODS
    capability = route_capability(method, path)
    fenced = mutation and path not in _NON_LIBRARY_MUTATIONS
    epoch = _epoch_claim(request) if fenced else None
    try:
        session = _principal(auth, request)
        if session is None:
            return _refused(Refusal.NOT_AUTHENTICATED)
        if not fenced:
            ctx = RequestContext.for_session(session)
            return Admitted(library_query(auth.db, ctx, _current_epoch, capability=capability), ctx)
        # A missing or malformed claim is None, which library_command refuses
        # (LIBRARY_EPOCH_REQUIRED) before any database work.
        ctx = RequestContext.for_session(session, epoch=epoch)
        return Admitted(library_command(auth.db, ctx, capability, _current_epoch), ctx)
    except AccessRefused as exc:
        return _refused(exc.refusal)
    except DatabaseError:
        return _UNAVAILABLE


def handle(auth: WebAuth, request: HttpRequest) -> Outcome:
    """Decide one ``/api/`` request: answer it, or admit it to its route."""
    if request.method in _MUTATING_METHODS and not origin_allowed(request, auth):
        return _error(403, "origin_not_allowed")
    identity = _identity_route(auth, request)
    if identity is not None:
        return identity
    return _admit(auth, request)
