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
4. Wire the HTTP handler with the family's parse helper (e.g.
   `parse_position_request`) / `dump_response` / `research_error_envelope`.
   Pass **plain** `.name`-style attributes into domain functions — never the
   model instance as business state. If the domain already owned type-refusal
   codes for a field, map them at the parse helper rather than emitting
   generic `invalid_request`.
5. Extend `positions_openapi_document()` (or add `*_openapi_document()` and
   merge) so paths and `components.schemas` stay generated from the same
   models. Document the **real** HTTP status codes the adapter returns
   (e.g. `position_in_use` → 409, not 400).
6. Regenerate/commit `docs/api/openapi-*.json` (see script in the Positions
   tests) so #45 has a machine-readable artifact without requiring FastAPI.
7. Add unit tests that (a) exercise happy-path HTTP behavior unchanged,
   (b) assert live responses validate through openapi-core
   (`validate_request` / `validate_response`), and (c) cover wrong-type
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

## Compatibility with #45 / #67 / #68

- **#45** owns the long-term full OpenAPI surface and versioning policy. This
  slice contributes the first executable fragment and the migration pattern.
- **#67** may later extract Positions routes into `backend/api/…`; keep calling
  the same domain functions and the same `api_contract` models.
- **#68** supplies canonical `*_on_conn` mutations. Prefer typing families that
  already share HTTP + sync (as Positions does) or rebase onto that tip when
  typing a family that Track A is actively moving.
