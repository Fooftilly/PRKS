# Typed API boundary migration pattern (#180 / #45)

This document describes how to migrate an additional PRKS HTTP family onto
typed request/response models after the Positions vertical slice.

## Boundary rule

```text
HTTP request
    → Pydantic request model (shape / JSON types only)
    → PRKS canonical domain / application function (plain values)
    → transaction / storage
    → Pydantic response model (serialize / verify shape)
    → HTTP response
```

Pydantic **must not** own:

- synchronization conflict semantics or revisions;
- entity lifecycle / tombstones;
- canonical mutation rules (uniqueness, cycles, IN_USE refusals);
- SQLite transactions;
- authorization / Origin policy;
- research-domain normalization beyond JSON type checks.

Those stay in `backend/*_sync.py`, `research_network.py`, `db_manager.py`, and
related domain modules. The HTTP adapter (`backend/server.py` or a future
extracted controller) only parses, dispatches, maps status codes, and
serializes.

## Layout

| Piece | Location |
| --- | --- |
| Shared error envelope | `backend/api_contract/errors.py` |
| Parse / dump helpers | `backend/api_contract/boundary.py` |
| Per-family DTOs | `backend/api_contract/<family>.py` |
| OpenAPI builder | `backend/api_contract/openapi.py` (extend or add sibling builders) |
| Checked-in OpenAPI artifact | `docs/api/openapi-positions.json` (and later siblings) |
| Live document | `GET /api/openapi.json` (Positions slice today; grows with families) |

Do **not** introduce a framework or split `server.py` solely to obtain OpenAPI.
Route extraction (#67) is a separate track; typed models do not require it.

## Steps for the next family

1. Confirm the family already has (or will get via #68) a **canonical domain
   mutation** shared by ordinary HTTP and durable sync. Do not duplicate SQL
   inside the Pydantic model or a new adapter-only path.
2. Add request/response models under `backend/api_contract/`. Prefer
   `extra="ignore"` on requests to preserve today’s loose clients; forbid
   unknown keys only when the product already rejects them.
3. Keep domain rules in the domain module. Request models check JSON types
   (string vs object vs array). Length, emptiness, control characters, and
   uniqueness remain in the domain.
4. Wire the HTTP handler with `parse_request` / `dump_response` /
   `research_error_envelope`. Pass **plain** `.name`-style attributes into
   domain functions — never the model instance as business state.
5. Extend `positions_openapi_document()` (or add `*_openapi_document()` and
   merge) so paths and `components.schemas` stay generated from the same
   models.
6. Regenerate/commit `docs/api/openapi-*.json` (see script in the Positions
   tests) so #45 has a machine-readable artifact without requiring FastAPI.
7. Add unit tests that (a) exercise happy-path HTTP behavior unchanged and
   (b) assert live responses validate against the OpenAPI document (and/or
   response models). Prefer `openapi-core` for document validation when it
   accepts the slice; do not block on Schemathesis (see below).

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
- **openapi-core** is a **test** pin in `requirements-dev.txt` for contract
  checks. It is not required to run the production server.
- Update `dependency-inventory.json` whenever either pin changes
  (`scripts/dependency_gate.py` / `python run_tests.py` preflight).

## Schemathesis / openapi-core evaluation

| Tool | Role | Adoption for this slice |
| --- | --- | --- |
| **openapi-core** | Validate request/response against an OpenAPI document | **Adopted for unit contract tests** when the Positions document validates cleanly. |
| **Schemathesis** | Property-based HTTP fuzzing from OpenAPI | **Not in default CI yet.** Needs a live PRKS server per worker, careful Origin/mutation rules, and can exercise destructive paths. Prefer targeted unit/API tests that prove schema↔impl agreement; revisit Schemathesis as an optional maintainer job once more families are documented. |

## Compatibility with #45 / #67 / #68

- **#45** owns the long-term full OpenAPI surface and versioning policy. This
  slice contributes the first executable fragment and the migration pattern.
- **#67** may later extract Positions routes into `backend/api/…`; keep calling
  the same domain functions and the same `api_contract` models.
- **#68** supplies canonical `*_on_conn` mutations. Prefer typing families that
  already share HTTP + sync (as Positions does) or rebase onto that tip when
  typing a family that Track A is actively moving.
