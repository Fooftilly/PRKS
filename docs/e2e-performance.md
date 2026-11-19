# E2E performance

How per-test E2E cost is measured and what the rules are for changing it. Suite-level policy (tiers, sharding, cadence) is in `tests/e2e/AGENTS.md`. The investigation log that led here (#740, #761-#776, #992, #993) is in this file's git history.

## Where a test spends its time

Each browser test:

1. creates fresh temporary storage and a fresh PostgreSQL database, seeded through the domain commands (`tests/e2e/postgres_identity.py`);
2. launches a fresh `prks_app.py --testing` process and polls it until ready;
3. opens a fresh browser context, installs the `PageCollector` (console, failed requests, and a `page.route("**/*")` guard that blocks external HTTP);
4. loads `/#/folders` and waits for the real shell (`#sidebar`, `.prks-folder-library`, the canonical hash);
5. runs the scenario, then closes the context, stops the server and drops the database.

Chromium is reused per module. Per-test storage, database, server and context isolation is deliberate and stays: it prevents order dependence and cross-test leaks.

Measured costs that drove past decisions: server start and app readiness dominate fixed per-test cost; request interception is real but a same-origin routing fast path gave no wall-time gain (left off); bundling classic scripts cut app readiness by about 7% (shipped as the classic bundle).

## Timing baseline

`tests/e2e/timing-baseline.json` holds calibrated exact test-ID weights plus `module.*` / `module.Class.*` fallbacks, so fresh checkouts schedule shards sensibly. At schedule time machine-local `.tests/e2e-timings.json` exact IDs override it. The baseline is never copied into `.tests/` and never appears in the slowest-tests report. It is scheduling metadata, not a speed claim.

Refresh it only from representative CI or full-gate exports (unittest id → seconds), never from a laptop:

```bash
scripts/e2e update-timing-baseline --from /tmp/ci-e2e-timings.json          # dry run
scripts/e2e update-timing-baseline --from a.json --from b.json --include-exact --write
```

`--write` takes the median across exports and fails closed unless they cover every current module and test ID and `--include-exact` is given. Use `--allow-partial` or another `--output` for experiments.

## Profiling

`--profile` (or `PRKS_E2E_PROFILE=1`) records per-test infrastructure phases:

- `seed_build`, `browser_context`, `async_wait`, `request_routing` (with `routing_decision`), `server_stop`;
- `server_start`, partitioned into `server_spawn` and `server_http_ready`;
- `app_ready`, partitioned into `app_navigation`, `app_sidebar_wait`, `app_folder_wait`, `app_route_wait`;
- browser Navigation Timing `nav_*` intervals (a subset of navigation, not extra work).

Nested phases partition their parent: never add them to it. Failed attempts are reported as `*_failed` phases, separately from the successful attempt. `nav_timing_*` entries are counts, not seconds.

```bash
python tests/e2e/run.py --feature sync --jobs 1 --no-pointer-capture --profile
```

Profiled runs (and `PRKS_E2E_ROUTE_FASTPATH=1`) never write timing or last-failed history (`policy.benchmark_modes()`), because instrumented durations would poison normal sharding. On GitHub, run **PRKS Full E2E Gate** manually with the `profile` input.

Manual-only benchmark and profile workflows (`.github/workflows/e2e-*.yml`) run warm-up plus ABBA trials on one runner and publish aggregate-only summaries; they never upload test data or train history: routing A/B, classic script deferral and coalescing A/B, offline Graph navigation, offline Person profile and click trace, Argument overlay, PDF annotation rollback, and flake stress.

## Diagnostics

- `PRKS_E2E_DIAGNOSTIC=1` prints privacy-safe lifecycle heartbeats (`START`, `SERVER_READY`, `CONTEXT_READY`, `APP_READY`, `BODY_DONE`, `CONTEXT_CLOSED`, `SERVER_STOPPED`).
- The per-test watchdog (`PRKS_E2E_TEST_WATCHDOG`, default 120 s, `0` off) kills a stuck worker and reports the test and stage, well before `PRKS_E2E_FULL_TIMEOUT` (1200 s). It never retries.
- `PRKS_E2E_CHROMIUM_RECYCLE_EVERY` relaunches Chromium lazily after N contexts in heavy service-worker modules (default 1 for Offline Work metadata, `0` off). Contexts stay per-test either way.
- `PRKS_E2E_REDUCED_MOTION=1` opts into `prefers-reduced-motion` for motion experiments; it stays off for the gate because it changes layout that scroll-restore assertions depend on.

## Moving coverage out of the browser

Decide by coverage, never by duration. Classify each contract as KEEP, SPLIT or MOVE (`tests/e2e/AGENTS.md`). Land the faster replacement before removing the browser scenario, and never delete an E2E only because a similarly named unit test exists.

Families already rationalized, with the browser boundary each keeps:

| Family | Kept in Chromium | Moved to |
| --- | --- | --- |
| Work Tags | offline/reload/reconnect, conflict UI, degraded catalog, cache clear, queue wiring, Tag lifecycle; one remount coalescing check | `run_local_store_selftest.js`, `run_work_tag_sync_selftest.js`, `test_frontend_work_tag_sync.py`, `tests/pg/test_works_sync.py` |
| Work source | 14 scenarios incl. editor `state.observed`, Apply, convergent ACK | `run_work_source_sync_selftest.js`, `test_work_source_rules.py` |
| Work opens | offline open and reconnect UI | `run_work_open_sync_selftest.js`, `test_work_open_rules.py` |
| Work notes, metadata, people | editor, reload and conflict UI | `run_work_note_sync_selftest.js`, `test_work_metadata_rules.py`, `run_work_role_domain_coherence_selftest.js` |
| Concepts, Positions, Arguments, Playlists, Folders | route, cache-visible projection and navigation guards | `run_argument_ack_coherence_selftest.js`, `run_offline_read_model_shape_selftest.js`, `test_frontend_playlist_sync.py`, HTTP tests in `tests/pg/` |

## Still worth measuring

Change these only when profiling shows a material gain and the behavior stays covered:

1. Smaller composable seeds, if seed work is significant (risk: vacuous assertions).
2. Cheaper same-origin interception, without letting a same-origin redirect bypass the external-network guard.
3. A real application-ready signal to replace stacked readiness waits. Never an E2E-only endpoint or fake backend.
4. Faster legitimate server startup, never skipping production startup paths in tests.
5. Fewer reloads and duplicate heavy views (PDF, graph, editors) inside one scenario.

Not to do without a proven equivalent reset: server reuse across tests or browser-context reuse. Never remove failure collection, add retries, or shorten Playwright timeouts for speed. Measured settle delays (for example `_wait_entity_cached`) stay.

## Benchmark protocol

1. Same commit apart from the candidate, same machine and Chromium.
2. `--jobs 1` for per-test changes; same feature or test IDs.
3. At least two warm runs; report median per-test timings, not only suite wall time.
4. Check failures and flakiness as well as speed.
5. Then run the parallel feature gate and the full gate once, and put the measured change in the PR description.
