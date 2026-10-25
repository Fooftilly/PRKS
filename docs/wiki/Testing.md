# Testing

PRKS uses layered tests. Contributors should run the cheapest test that proves a change while iterating, then use browser E2E for behaviors that truly require a real browser.

## Setup

Install runtime pins **and** development/test tooling before the fast suite. `python run_tests.py` preflights `openapi-core` and `hypothesis` (from `requirements-dev.txt`) even when Chromium/Playwright is not needed:

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt -r requirements-dev.txt
```

A runtime-only install is enough to run the app, not enough to run the unit suite.

## Main runner

```bash
python run_tests.py
```

Runs the Python/API/structural/Node suite without Chromium.

```bash
python run_tests.py --e2e
```

Runs the full real-browser E2E gate against real PRKS server processes.

```bash
python run_tests.py --all
```

Runs the ordinary suite followed by E2E.

There is also an opt-in UX interaction tour:

```bash
python run_tests.py --ux-tour
```

## Property-based sync tests (#854)

The Python unit suite includes a bounded [Hypothesis](https://hypothesis.readthedocs.io/) pilot for Work–Tag relationship revisions and the semantic-operation ledger. Hypothesis is **test-only** and does not ship with the PRKS server.

Run just the pilot (after installing `requirements.txt` and `requirements-dev.txt`):

```bash
./.venv/bin/python -m unittest tests.test_hypothesis_sync -v
```

Ordinary CI uses a small fixed maximum number of generated examples and state-machine steps; it is not a replacement for deterministic regression tests. A failure reports a minimized counterexample. Preserve it as an explicit `@example(...)` or a named deterministic regression test for reliable future replay; use a `@reproduce_failure(...)` blob when printed, or record a `@seed(...)` when reproducing a generated run. A plain rerun is not guaranteed to regenerate the same counterexample. Hypothesis' local example database (when enabled) is an accelerator, not a CI prerequisite. The tests deliberately set `database=None` to keep results independent of a developer's cached examples. Avoid hiding flaky failures behind relaxed deadlines or retry loops. Test databases and records are disposable, with no connection to a real research library.

## TypeScript property-based workspace tests (#854)

The frontend has a bounded **fast-check + Vitest** pilot for the canonical workspace tree and tab transitions. This checks generated move/remove/reorder sequences for leaf uniqueness, split-tree validity, identity preservation, and unchanged Main/focus. It imports the real pure workspace transitions; it does not implement a second workspace store or exercise the browser.

After `npm ci` in `frontend-app/`, run the focused suite using `npm test -- src/workspace/workspace.property.test.ts`. The normal Vitest gate discovers it automatically. Each property uses **30 bounded examples** and up to 12 operations. A failing run reports a seed and shrinking path; replay with fast-check's `seed` and `path` options, then retain significant counterexamples as explicit regression tests. No screenshots, user data or hosted service are involved.

## E2E iteration

The browser runner supports targeted/domain-oriented execution so development does not repeatedly pay the full-suite cost.

Useful modes include:

```bash
python tests/e2e/run.py --smoke
python tests/e2e/run.py --feature <group>
python tests/e2e/run.py --affected
python tests/e2e/run.py --last-failed
python tests/e2e/run.py --dev --feature <group>
```

The advertised full gate runs in parallel; debugging a single failure should normally use one worker.

`tests/e2e/policy.py` is the declarative source for feature groups, affected-file mapping, smoke cases, and related selection policy.

## E2E isolation

Tests use real PRKS server processes and isolated testing storage. Browser contexts isolate browser-local state. This is intentionally stronger than mocking the API because offline/service-worker/PDF/workspace behavior depends on real integration boundaries.

Performance optimizations must preserve those isolation guarantees.

## E2E performance

PRKS has explicit seed caching, sharding/timing history, profiling, diagnostics, and browser lifecycle controls. Do not "optimize" E2E by weakening assertions or turning real end-to-end contracts into mocks simply to reduce runtime.

See [docs/e2e-performance.md](https://github.com/Fooftilly/PRKS/blob/master/docs/e2e-performance.md).

## Static analysis and CI

Repository workflows include CodeQL and static-analysis checks. Local tooling also covers Python lint/type checks and frontend/static contracts through the project runners/configuration.

The dependency gate verifies dependency declarations/vendor state as part of repository hygiene.

## UX tour and screenshots

The UX interaction tour is separate from the normal E2E gate because it produces artifacts and has different goals.

README promotional screenshots use dedicated synthetic/public-domain demo tooling:

- `scripts/seed_demo_library.py`;
- `scripts/capture_demo_screenshots.py`.

Never use a real personal research library to generate documentation or UX-audit screenshots.


## Optional PostgreSQL query-cost profile (#858)

Run the **manual**, synthetic-only PostgreSQL 18 query profiler:

```bash
scripts/test-postgres --profile
scripts/test-postgres --profile --sizes 12,48,128 --repetitions 8
```

The wrapper starts a new disposable Docker server (or local temporary cluster)
with `shared_preload_libraries=pg_stat_statements` and `compute_query_id=on`,
creates a harness-owned migrated database and extension, seeds synthetic Works,
then deletes the database and server. The profiler **refuses**
`PRKS_TEST_DATABASE_URL`; do not enable the extension or reset its shared
statistics on a real PRKS cluster. A missing preload or extension fails
clearly. Ordinary `scripts/test-postgres` and production Compose startup
are unchanged.

Results contain PostgreSQL version, fixture tier, a known facade read operation,
warm call-wall p50/p95 and the top five *per-SQL* aggregate costs (calls,
total/mean execution time, rows, block hits/reads and temporary blocks).
Fingerprints are opaque and **not stable between clusters or PostgreSQL major
versions**. The profiler does not select or print SQL text: PostgreSQL's
representative text can contain literals even after normalization. Synthetic
fixtures only; never upload real research content. Cache-warm facade call
durations are **not** HTTP latency and cannot be compared directly with PRKS's
request-level p50/p95; run repeated like-for-like benchmarks before claiming
a performance improvement. This is advisory and never runs in mandatory PR CI.
