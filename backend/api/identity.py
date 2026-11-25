"""The contracts of the paths admission answers itself (#1431).

``POST /api/login``, ``POST /api/logout`` and ``GET /api/session``
(:mod:`backend.api.session`) and ``/api/setup`` (:mod:`backend.api.setup`)
are answered before the route table is consulted, so they are not in
``backend.api.ROUTES``. Their contracts live here, on routes that are never
served, so that the one OpenAPI document covers every ``/api/`` path.
"""

from __future__ import annotations

from backend.api_contract.identity import (
    LoginRequest,
    SessionState,
    SetupDone,
    SetupRefusal,
    SetupRequest,
    SetupState,
    SignedIn,
    SignedOut,
    StorageChoiceRequest,
    StorageChosen,
)
from backend.api_contract.errors import ApiErrorEnvelope
from backend.api_contract.operation import Header, Operation, Reply, Schema, body, error, ok
from backend.web.http import Request, Response
from backend.web.routing import Body, route


def _answered_by_admission(req: Request) -> Response:  # pragma: no cover - never routed
    raise AssertionError(f"{req.method} {req.path} is answered by admission, not the route table")


_TAG = "identity"
_SET_COOKIE = Header("The session cookie (prks_session); on a login also the login device cookie.", required=True)
_EPOCH = Header("The Library's current epoch.", required=True)
_RETRY = {"Retry-After": Header("Seconds to wait.", {"type": "string", "pattern": "^[0-9]+$"}, required=True)}
_ORIGIN = error("The Origin or Sec-Fetch-Site is not allowed (origin_not_allowed).")
_UNAVAILABLE = error("The database is unavailable (unavailable).")
_SIGN_IN_UNAVAILABLE = error("The database is unavailable (unavailable), or the server is still in "
                             "first-run setup (setup_pending).")


def _throttled(text: str, schema: Schema = ApiErrorEnvelope) -> Reply:
    return Reply(text, schema, headers=_RETRY)


_SETUP_400 = error("A field is missing or refused (invalid_request, with field and reason), or the token "
                   "is not a bootstrap token (bootstrap_token_malformed).", SetupRefusal)
_SETUP_403 = error("The bootstrap token is wrong or expired (bootstrap_token_invalid, "
                   "bootstrap_token_expired), or the Origin is not allowed (origin_not_allowed).", SetupRefusal)
_SETUP_429 = _throttled("Too many refused tokens (too_many_attempts).", SetupRefusal)

IDENTITY_ROUTES = (
    route("POST", "/api/login", _answered_by_admission, contract=Operation(
        "login", "Sign in to the Library this server serves", _TAG,
        {200: ok(SignedIn, "Signed in: sets the session and login device cookies and replaces this "
                           "browser's earlier session.",
                 headers={"Set-Cookie": _SET_COOKIE, "PRKS-Library-Epoch": _EPOCH}),
         400: error("A forwarded client address is malformed (invalid_request)."),
         401: error("Any refused login, alike for every account (invalid_credentials)."),
         403: _ORIGIN,
         404: error("The server acts as its owner without sign-in (loopback-owner mode)."),
         429: _throttled("Too many attempts (too_many_attempts)."),
         503: _SIGN_IN_UNAVAILABLE},
        request=body(LoginRequest), session="none")),
    route("POST", "/api/logout", _answered_by_admission, Body.NONE, contract=Operation(
        "logout", "Sign out: revoke the presented session", _TAG,
        {200: ok(SignedOut, "Signed out (also without a session): clears the session cookie.",
                 headers={"Set-Cookie": _SET_COOKIE}),
         403: _ORIGIN,
         404: error("The server acts as its owner without sign-in (loopback-owner mode)."),
         503: _SIGN_IN_UNAVAILABLE},
        session="optional")),
    route("GET", "/api/session", _answered_by_admission, contract=Operation(
        "getSession", "The signed-in session: user, Library, epoch and expiry", _TAG,
        {200: ok(SessionState, "The session; a session cookie is sent again with the renewed expiry.",
                 headers={"PRKS-Library-Epoch": _EPOCH}),
         401: error("No live session (not_authenticated)."),
         403: error("The session's user is no longer a member of the Library."),
         503: _SIGN_IN_UNAVAILABLE},
        session="optional",
        description="In loopback-owner mode every request acts as the owner and needs no cookie.")),
    route("GET", "/api/setup", _answered_by_admission, contract=Operation(
        "getSetupState", "How far first-run setup is", _TAG,
        {200: ok(SetupState, "The setup state; ready, with storage fixed, once the server serves normally."),
         503: _UNAVAILABLE},
        session="none")),
    route("POST", "/api/setup", _answered_by_admission, contract=Operation(
        "completeSetup", "Create the owner and the Library with the bootstrap token", _TAG,
        {200: ok(SetupDone, "Setup is complete; signed_in: a session cookie for the owner is set.",
                 headers={"Set-Cookie": Header("The owner's session cookie, when signed_in.")}),
         400: _SETUP_400, 403: _SETUP_403,
         409: error("Setup is already complete or needs a restart (already_ready, restart_required), another "
                    "attempt is running (setup_busy), or the pairing was refused (setup_refused, with reason "
                    "and message).", SetupRefusal),
         429: _SETUP_429, 503: _UNAVAILABLE},
        request=body(SetupRequest), session="none")),
    route("POST", "/api/setup/storage", _answered_by_admission, contract=Operation(
        "chooseSetupStorage", "Choose the storage folder before setup completes", _TAG,
        {200: ok(StorageChosen, "The folder is recorded; the server must restart to use it."),
         400: _SETUP_400, 403: _SETUP_403,
         409: error("Setup is already complete or needs a restart (already_ready, restart_required), another "
                    "attempt is running (setup_busy), the pairing was refused (setup_refused) or the folder "
                    "was (storage_refused), with reason and message.", SetupRefusal),
         429: _SETUP_429, 503: _UNAVAILABLE},
        request=body(StorageChoiceRequest), session="none")),
)
