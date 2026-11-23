"""``/api/setup``: browser first-run setup over HTTP (#612; :mod:`backend.domain.identity.web_setup`).

While a web process waits for setup it serves the **setup phase**
(:func:`handle_setup_phase`); every ``/api/`` request goes here instead of
through session admission:

- ``GET /api/setup``: ``{"state": "unconfigured" | "bootstrapping" | "ready"}``.
  Nothing else: no path, address, version or configuration value.
- ``POST /api/setup`` ``{"token", "login", "display_name"?, "password",
  "library_name"?}``: runs setup. 200 ``{"state": "ready", "created",
  "signed_in"}``, with the owner's ``prks_session`` cookie when ``signed_in``.
  The CSRF rule of every mutation applies (``backend.api.session``).
- Every other ``/api/`` route: 503 ``setup_pending``.

Refusals (``error`` is a stable code; the page picks its own text):

- 400 ``invalid_request`` with ``field`` and ``reason`` for unusable input,
  ``bootstrap_token_malformed`` for a token that cannot be one;
- 403 ``origin_not_allowed``; 403 ``bootstrap_token_invalid`` or
  ``bootstrap_token_expired``;
- 409 ``already_ready``, ``setup_busy``, or ``setup_refused`` with the
  bootstrap's ``reason`` and fixed ``message`` (a data root that belongs to
  another database, say);
- 429 ``too_many_attempts`` with ``Retry-After``;
- 503 ``unavailable`` when the database could not be reached (retry).

Once the process serves normally, :func:`handle_after_setup` answers the
same two routes: ``GET`` is ``{"state": "ready"}`` and ``POST`` is 409
``already_ready``, for any visitor, signed in or not.

Nothing here logs. No reply carries a token, a password, a hash or a path.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from backend.api.session import ANSWERED, HttpRequest, Reply, origin_matches
from backend.domain.identity.errors import RootPairingRefused
from backend.domain.identity.secret_tokens import SESSION_COOKIE, SESSION_COOKIE_PATH
from backend.domain.identity.web_setup import (
    STATE_READY,
    SetupBusy,
    SetupInputError,
    SetupPhase,
    TooManyAttempts,
    parse_request,
)
from backend.db.errors import DatabaseError

SETUP_PATH = "/api/setup"

_TOKEN_REFUSALS = frozenset({"bootstrap_token_invalid", "bootstrap_token_expired"})
_UNAVAILABLE = Reply(503, {"error": "unavailable"})


def _error(status: int, error: str, **extra: Any) -> Reply:
    return Reply(status, {"error": error, **extra})


def _session_cookie(token: str, expires_at: datetime, *, secure: bool) -> tuple[str, str]:
    remaining = max(0, int((expires_at - datetime.now(timezone.utc)).total_seconds()))
    parts = [
        f"{SESSION_COOKIE}={token}", f"Path={SESSION_COOKIE_PATH}", f"Max-Age={remaining}",
        "HttpOnly", "SameSite=Strict",
    ]
    if secure:
        parts.append("Secure")
    return ("Set-Cookie", "; ".join(parts))


def _state(phase: SetupPhase) -> Reply:
    try:
        return Reply(200, {"state": phase.state()})
    except DatabaseError:
        return _UNAVAILABLE


def _refusal(exc: RootPairingRefused) -> Reply:
    if exc.reason in _TOKEN_REFUSALS:
        return _error(403, exc.reason)
    if exc.reason == "already_ready":
        return _error(409, "already_ready")
    if exc.retryable or exc.reason == "database_unavailable":
        return _UNAVAILABLE
    # A fixed message naming at most instance and storage root IDs (errors.py).
    return _error(409, "setup_refused", reason=exc.reason, message=exc.message)


def _complete(phase: SetupPhase, request: HttpRequest) -> Optional[Reply]:
    if not origin_matches(request, phase.settings.public_origin):
        return _error(403, "origin_not_allowed")
    data = request.read_json()
    if data is ANSWERED:
        return None  # the body reader already answered
    try:
        setup = parse_request(data)
    except SetupInputError as exc:
        if exc.reason == "bootstrap_token_malformed":
            return _error(400, "bootstrap_token_malformed", field="token")
        return _error(400, "invalid_request", field=exc.field, reason=exc.reason)
    try:
        done = phase.complete(setup)
    except TooManyAttempts as exc:
        return Reply(429, {"error": "too_many_attempts"}, (("Retry-After", str(exc.retry_after)),))
    except SetupBusy:
        return _error(409, "setup_busy")
    except RootPairingRefused as exc:
        return _refusal(exc)
    headers: tuple[tuple[str, str], ...] = ()
    if done.session is not None:
        secure = phase.settings.secure_cookies or request.scheme == "https"
        headers = (_session_cookie(done.session.token.reveal(), done.session.expires_at, secure=secure),)
    return Reply(
        200,
        {"state": STATE_READY, "created": done.created, "signed_in": done.session is not None},
        headers,
    )


def handle_setup_phase(phase: SetupPhase, request: HttpRequest) -> Optional[Reply]:
    """Answer an ``/api/`` request of the setup phase; ``None`` when the body reader already answered."""
    if request.path == SETUP_PATH:
        if request.method in ("GET", "HEAD"):
            return _state(phase)
        if request.method == "POST":
            return _complete(phase, request)
        return Reply(405, {"error": "method_not_allowed"}, (("Allow", "GET, HEAD, POST"),))
    return _error(503, "setup_pending")


def handle_after_setup(request: HttpRequest) -> Optional[Reply]:
    """``/api/setup`` of a process that serves normally; ``None`` for every other route."""
    if request.path != SETUP_PATH:
        return None
    if request.method in ("GET", "HEAD"):
        return Reply(200, {"state": STATE_READY})
    if request.method == "POST":
        return _error(409, "already_ready")
    return Reply(405, {"error": "method_not_allowed"}, (("Allow", "GET, HEAD, POST"),))
