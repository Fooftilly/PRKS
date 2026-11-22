# E2E performance

How per-test E2E cost is measured and what the rules are for changing it. Suite-level policy (tiers, sharding, cadence) is in `tests/e2e/AGENTS.md`. The investigation log that led here (#740, #761-#776, #992, #993) is in this file's git history.

## Where a test spends its time

Each browser test:

1. creates fresh temporary storage and a fresh PostgreSQL database, seeded through the domain commands (`tests/e2e/postgres_identity.py`);
2. launches a fresh `prks_app.py --testing` process and polls it until ready;
3. opens a fresh browser context, installs the `PageCollector` (console, failed requests, and a `page.route("**/*")` guard that blocks external HTTP);
4. loads `/#/folders` and waits for the real shell (`#sidebar`, `.prks-folder-library`, the canonical hash);
5. runs the scenario, then closes the context and stops the server with `AppServer.stop_later()`: it sends SIGTERM and leaves the process exit, the database drop and the temp cleanup to a background thread, so they overlap the next test (normally one teardown at a time, though one that outlives its join can overlap the next; a failed one is raised by the next stop or the module cleanup, naming its test, and `--last-failed` reruns that test too). On a signal or a parent exit, `stop_all_servers()` sends SIGTERM to every live server, then waits for the background teardowns and stops the rest within one 15 s budget, under the parent's 20 s. A server whose teardown is still running stays that teardown's: it is killed, never stopped a second time from another thread.

Chromium is reused per module. Per-test storage, database, server and context isolation is deliberate and stays: it prevents order dependence and cross-test leaks.

Measured costs that drove past decisions: server start, server stop and app readiness dominate fixed per-test cost (CI profile of all 861 tests, November 2026: 4.6 s per test, of which app readiness 0.68 s, server start 0.38 s, server stop 0.38 s, database provision, seed and drop 0.22 s; moving the stop off the test cut a local 21-test run by 16%); request interception is real but a same-origin routing fast path gave no wall-time gain (left off); bundling classic scripts cut app readiness by about 7% (shipped as the classic bundle).

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

Manual-only benchmark and profile workflows (`.github/workflows/e2e-*.yml`) run on one runner and never train timing history. The A/B benchmarks (routing, classic script deferral) run warm-up plus ABBA trials; the profiles (offline Graph navigation, offline Person profile, Argument overlay, PDF annotation rollback) run serial repeats; the Person click trace runs once; flake stress repeats a selection. Summaries are aggregate-only, but a failing run may upload privacy-safe diagnostics.

## Diagnostics

- `PRKS_E2E_DIAGNOSTIC=1` prints privacy-safe lifecycle heartbeats (`START`, `SERVER_READY`, `CONTEXT_READY`, `APP_READY`, `BODY_DONE`, `CONTEXT_CLOSED`, `SERVER_STOPPED`).
- The per-test watchdog (`PRKS_E2E_TEST_WATCHDOG`, default 120 s, `0` off) kills a stuck worker and reports the test and stage, well before `PRKS_E2E_FULL_TIMEOUT` (1200 s). It never retries.
- `PRKS_E2E_CHROMIUM_RECYCLE_EVERY` relaunches Chromium lazily after N contexts in heavy service-worker modules (default 1 for Offline Work metadata, `0` off). Contexts stay per-test either way.
- `PRKS_E2E_REDUCED_MOTION=1` opts into `prefers-reduced-motion` for motion experiments; it stays off for the gate because it changes layout that scroll-restore assertions depend on.

## Coverage map (shadow mode)

`.github/workflows/e2e-coverage-map.yml` (weekly and manual) runs the full suite with `PRKS_E2E_COVERAGE_MAP_DIR` set. `tests/e2e/coverage_map.py` then records, per test, the Python lines its server ran (coverage.py) the `frontend/js/**` lines its pages ran (Chromium precise coverage; a line counts when its innermost function ran), and the `frontend-app/src/**` lines behind the parts of the Vue bundle that ran. For the last, the job builds a hidden source map of the committed bundle and refuses it unless the rebuilt bundle is byte-identical. Each shard uploads `e2e-coverage-map-N.json.gz`.

`python -m tests.e2e.coverage_map select MAP... --base B --head H` prints what a map-based selection would run for a diff:

- each changed `backend/**`, `prks_app.py`, `frontend/js/**` or `frontend-app/src/**` line selects the tests that ran it (an insertion selects the tests that ran the lines on either side; a Vue source line that produced no bundle code, such as a prop default, selects through the nearest lines that did);
- a change inside a `.vue` file's `<style>` keeps the full suite, since CSS is not measured;
- the rebuilt bundle and its manifests (`frontend/vue/**`, `frontend/vendor/DEPENDENCY-MANIFEST.json`, and `sw.js` when only its `DEPENDENCY_REVISION` line changed) are derived: Fast Static Analysis rebuilds and diffs them, so the source change selects instead;
- lines changed on master since the map was recorded are translated back to the map's commit;
- every other file keeps the planner's rule, and one that would run the full suite keeps the whole selection full;
- smoke is always added.

On every pull request, `.github/workflows/e2e-selection-shadow.yml` (advisory, not required) runs `scripts/ci/e2e_selection_shadow.py`: it takes the newest complete map recorded on master and writes both what the map would run and what the gate plan runs to the job summary, and to the `e2e-selection-shadow` artifact (kept 90 days) for later comparison with the gate's failures.

Daily, `.github/workflows/e2e-selection-accuracy.yml` (advisory) runs `scripts/ci/e2e_selection_accuracy.py`: for each pull request head of the last 7 days (gate failure logs keep 7 days), it compares the gate's failed tests with that head's shadow selection. The standing issue **E2E selection accuracy** (label `e2e-selection-report`) keeps one summary comment, edited in place, of the tests the gate planned against the tests the map would have selected (planned counts, not timings). It gets one new comment for each gate failure the map would not have run, so each miss can be checked as a real regression or a flake.

The gate does not read the map. It is evidence for deciding whether it should. Not measured: vendored scripts, CSS and HTML, service and web workers, and app-side reloads no test call started.

## Packaged distribution gate

`.github/workflows/e2e-classic-distribution-gate.yml` (not required) re-runs browser tests on the bundled production frontend. Its `packaged-plan` job (`tests/e2e/run.py --packaged-plan`, rules in `plan_packaged_e2e` in `tests/e2e/policy.py`) picks the set:

- a pull request that changes a `PACKAGED_FULL_PATHS` file (packaging scripts, `Dockerfile`, `frontend/sw.js`, the shell page, the manifest or the gate itself) runs every test on 8 shards;
- any other pull request runs the routine set on 4 shards (`--packaged-routine`): smoke, the `offline` feature, every `*_offline` module and the local-store modules in `PACKAGED_ROUTINE_MODULES`, the tests that depend on the bundles and on what the service worker precaches;
- manual runs, including the nightly master CI (`master-ci.yml`), run every test, so the rest of the suite still runs on the packaged frontend once per night after merges.

## Moving coverage out of the browser

Decide by coverage, never by duration. Classify each contract as KEEP, SPLIT or MOVE (`tests/e2e/AGENTS.md`). Land the faster replacement before removing the browser scenario, and never delete an E2E only because a similarly named unit test exists.

Families already rationalized, with the browser boundary each keeps (contract-by-contract maps: `docs/e2e-coverage-maps.md`):

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
