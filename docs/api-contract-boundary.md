# Typed API boundary migration pattern (#180 / #45)

This document describes how to migrate an additional PRKS HTTP family onto
typed request/response models after the Positions vertical slice.

## Boundary rule

```text
HTTP request
    → Pydantic request model (shape / JSON types only)
    → PRKS canonical domain / application function (plain values)
    → transaction / storage (commits)
    → HTTP response body from the domain result
```

**Request** models run **before** the domain mutation. **Response** DTOs are
the OpenAPI / test contract for the wire shape. Until a transactional
application boundary can validate the response **while the write txn is still
open**, mutating Positions handlers (POST create / PATCH update) must **not**
call `dump_response` after the domain returns: a Pydantic failure there would
surface as HTTP 500 after a durable commit, leaving the client unsure whether
the write stuck. GET (and other non-mutating) paths may still use
`dump_response` — a read-time shape error does not create that ambiguity.
DELETE’s static `{status: deleted}` dump is likewise not a post-write shape
risk from domain payload drift.

Enforce response DTOs in unit/HTTP/openapi-core tests instead. When #67/#68 (or
a later app-service layer) own the txn across “mutate + build response”, move
response validation back inside that boundary before commit.

Pydantic **must not** own:

- synchronization conflict semantics or revisions;
- entity lifecycle / tombstones;
- canonical mutation rules (uniqueness, cycles, IN_USE refusals);
- database transactions (`library_command` / `library_query`);
- authorization / Origin policy;
- research-domain normalization beyond JSON type checks.

Those stay in `backend/domain/`, including the family rule modules `*_rules.py` (the route modules in
`backend/api/` call its commands with the `backend/library.py` handle's database), and related domain modules. The HTTP adapter (`backend/web/` and the route
families in `backend/api/`) only parses, dispatches, maps status codes, and
serializes.

## Layout

| Piece | Location |
| --- | --- |
| Shared error envelope | `backend/api_contract/errors.py` |
| Parse / dump helpers | `backend/api_contract/boundary.py` |
| Per-family DTOs | `backend/api_contract/<family>.py` |
| Route contract types | `backend/api_contract/operation.py` (`Operation`, `Reply`, `Param`, `body`, `ok`, `error`) |
| Route contracts | the `contract=` of each row of `ROUTES` in `backend/api/<family>.py` |
| OpenAPI builder | `backend/api_contract/openapi.py` (`openapi_document()`, from `backend.api.ROUTES`) |
| Checked-in document | `docs/api/openapi.json` (`python scripts/write_openapi.py`) |
| Live document | `GET /api/openapi.json` |
| TypeScript types | `frontend-app/src/api/generated/openapi.ts` (`npm run openapi:types`) |

There is one document (#1431). A route's contract is declared once, on its
route row, so a route and its description cannot drift apart. The builder
adds the refusals every route of a kind shares (`components.responses`): the
JSON body gate (400 `invalid_request`, 413, 415) for a JSON route, and
admission (401, 403, 503; for a mutation admission holds to the Library
epoch, `backend.api.session.epoch_fenced`, also 400 `LIBRARY_EPOCH_REQUIRED`,
409 `LIBRARY_EPOCH_CHANGED` and 503 `MAINTENANCE`, which only that path
checks) for an `/api/` route. A read that fills a derived cache on a miss
(`backend.api.session.fills_derived_cache`: a Person's portrait, a saved
remote image) runs that path too, so its 503 may also be `MAINTENANCE`. A
route declares only its own answers; when it declares a status the builder
would add, the shared refusal's description is appended to the route's own,
and a body of the route's own shape becomes `anyOf` that shape and the
shared envelope. Every `/api/` operation names the `session` security scheme
(the `prks_session` cookie); the health probes name none. The builder
refuses a route that declares no contract.

Admission answers sign-in, session and setup (`POST /api/login`,
`POST /api/logout`, `GET /api/session`, `/api/setup`, `/api/setup/storage`)
before the route table is consulted. Their contracts are declared on the
never-served rows of `backend.api.identity.IDENTITY_ROUTES`, which the
builder documents with their own refusals and the JSON body gate only; an
`Operation`'s `session` says whether the cookie is needed (`optional` for
logout and the session read, `none` for login and setup). The HTTP test
clients hold every answer on these paths to its contract
(`ContractCheck.check_admission`).

Do **not** introduce a framework solely to obtain OpenAPI.

## Steps for the next family

1. Confirm the family already has (or will get via #68) a **canonical domain
   mutation** shared by ordinary HTTP and durable sync. Do not duplicate SQL
   inside the Pydantic model or a new adapter-only path.
2. Add request/response models under `backend/api_contract/`. Prefer
   `extra="ignore"` on **requests** to preserve today’s loose clients.
   **Response** DTOs (and nested response records) should use
   `extra="forbid"` and require established wire keys — nullable values may
   stay `T | None`, but without defaults that omit the key from OpenAPI —
   so the schema matches the production payload and `dump_response` cannot
   silently synthesize or drop fields. Prefer intentional request null
   contracts (omit vs string; empty string to clear) over publishing
   nullable fields solely to mirror accidental legacy omit-via-null quirks.
   Fixed sync-state field maps should be named models (`extra="forbid"`),
   not open-ended `dict[str, …]`.
3. Keep domain rules in the domain module. Request models check JSON types
   (string vs object vs array). Length, emptiness, control characters, and
   uniqueness remain in the domain.
4. Wire the HTTP handler with the family's parse helper (e.g.
   `parse_position_request`) / `research_error_envelope`. Pass **plain**
   `.name`-style attributes into domain functions — never the model instance
   as business state. If the domain already owned type-refusal codes for a
   field, map them at the parse helper rather than emitting generic
   `invalid_request`. For **mutating** routes, send the domain result dict
   after commit without `dump_response` (see Boundary rule above); use
   `dump_response` on safe GETs if desired.
5. Declare the route's `contract=Operation(...)` on its `ROUTES` row:
   operation id, summary, tag, query/header `Param`s, `body(Model)` for a
   request body and `ok(Model, ...)` / `error(...)` replies by status.
   Document the **real** HTTP status codes the route returns (e.g.
   `position_in_use` → 409, not 400). A `HEAD` route uses the `GET`
   contract's `.head()`. A route without a contract fails the build of the
   document.
6. Regenerate and commit `docs/api/openapi.json` and the TypeScript types:

   ```bash
   python scripts/write_openapi.py
   (cd frontend-app && npm run openapi:types)
   ```

   `tests/test_openapi_document.py` and `npm run openapi:check` fail when
   either is stale; they never rewrite the files.
7. A Vue client that owns typed transport DTOs aliases the generated schema
   types from `frontend-app/src/api/generated/openapi.ts` instead of
   hand-written wire types. Do not generate fetch clients, TanStack Query
   hooks, or domain services. Keep `prksApiRequest`, the hand-written service,
   and the runtime response parsers.
8. The PostgreSQL HTTP test servers (`tests/pg/http_fixtures.py`) hold
   every answer of a typed route to its contract (`tests/contract_check.py`):
   an undocumented status, a JSON answer outside its schema, or a request
   body answered `2xx` that its request schema refuses fails the test that
   sent it. It sees only what a route handler returns: a handler that raises,
   and the dispatcher's own answers (admission refusals, the JSON body gate,
   the exception mapper), go unchecked there and keep their own tests. Add unit tests that (a) exercise happy-path HTTP behavior unchanged,
   (b) assert live responses validate through openapi-core
   (`validate_request` / `validate_response`) and response DTOs
   (`PositionDetail.model_validate`, …), and (c) cover wrong-type
   bodies for domain-owned fields so error codes cannot drift.

## Common error shape

```json
{ "error": "<human message>", "code": "<stable_code>" }
```

`code` may be omitted for legacy not-found bodies that historically lacked
one. New typed handlers should include `code` when the domain provides it
(`ResearchError.code`, sync refusal codes, `invalid_request` for schema
failures).

## Dependency policy

- **Pydantic** is a **runtime** pin in `requirements.txt` (HTTP boundary).
- **openapi-core** is a **test** pin in `requirements-dev.txt`. The Unit / API /
  contract Test Gate job installs it alongside runtime pins, and
  `python run_tests.py` runs a **unit-contract** preflight (`openapi-core` only;
  no Playwright) so discovery cannot hit `ModuleNotFoundError`. Contributors
  who run the fast suite must install both requirement files:

  ```bash
  python -m pip install -r requirements.txt -r requirements-dev.txt
  ```

  A runtime-only install is enough for `prks_app.py`, not for `run_tests.py`.
  openapi-core is not required to run the production server.
- Update `dependency-inventory.json` whenever either pin changes
  (`scripts/dependency_gate.py` / `python run_tests.py` preflight).

## Schemathesis / openapi-core evaluation

| Tool | Role | Adoption for this slice |
| --- | --- | --- |
| **openapi-core** | Validate request/response against an OpenAPI document | **Required in the unit/API contract CI job.** Live Positions HTTP tests call `OpenAPI.validate_request` / `validate_response` (via `MockRequest` / `MockResponse`). |
| **Schemathesis** | Property-based HTTP fuzzing from OpenAPI | **Not in default CI yet.** Needs a live PRKS server per worker, careful Origin/mutation rules, and can exercise destructive paths. Prefer targeted unit/API tests that prove schema↔impl agreement; revisit Schemathesis as an optional maintainer job once more families are documented. |

## Preserving domain error codes at the typed boundary

When a request field was already validated by the domain with a stable code
(e.g. Position `invalid_name` / `invalid_text` for wrong-type values), the
typed boundary must **not** replace that with a generic `invalid_request`.
Map those Pydantic type/missing failures at the family parse helper (see
`parse_position_request`) so clients keep the established codes and messages.
Keep length, emptiness, control characters, and uniqueness in the domain.

## Relation to #45 / #1424

- **#45** owns the long-term API surface and versioning policy; the one
  document's `info.version` is `CONTRACT_VERSION` in
  `backend/api_contract/openapi.py`.
- **#1424** made every route a row of the route table; #1431 attaches each
  route's contract to that row and builds the document from the table.
