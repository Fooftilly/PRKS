# E2E performance investigation

This document tracks per-test E2E cost separately from suite-level sharding.

The runner already has strong suite-level controls: individual-test timing history,
longest-processing-time-first sharding, per-worker Chromium reuse, targeted feature
selection, affected-test selection, last-failed runs, and a full-gate deadline. The
remaining target is the cost *inside one test*.

## Current per-test path

Most real-browser tests do all of the following:

1. create a fresh temporary PRKS storage tree;
2. build a deterministic seed database and fixture files;
3. launch a fresh `prks_app.py --testing` subprocess;
4. poll HTTP until the server is ready;
5. create a fresh browser context and page;
6. install the page collector/request interception;
7. load PRKS and wait for the initial Folders view;
8. run the scenario, including durable/offline async waits;
9. close the browser context;
10. terminate the PRKS subprocess and delete temporary storage.

Chromium itself is intentionally reused at module scope. Per-test storage and server
isolation remain valuable because they prevent order dependence and cross-test state
leaks.

## Implemented in this PR

### Worker-local immutable seed snapshots

A seed function is now executed once per worker. Its completed storage tree is kept
as a private immutable template. Every AppServer still receives a new
TemporaryDirectory, but the template is copied into it instead of rebuilding the
same SQLite/PDF/index fixture repeatedly. After the first build, templates are
WAL-checkpointed and leftover `-wal`/`-shm` companions are removed **only when**
`PRAGMA wal_checkpoint(TRUNCATE)` reports `busy=0`. A blocked/incomplete
checkpoint **refuses** the snapshot (raises; nothing is inserted into the
immutable seed cache) rather than caching a directory that may still have a
live SQLite user. Finalize connects with `timeout=0` so a leaked writer fails
immediately. Each build attempt uses a unique `mkdtemp` under the cache root
so a leftover failed template cannot block a later retry (`FileExistsError` on
a reused `seed-N` path — especially on Windows when an open handle blocks
`rmtree`).

The returned fixture ID dictionary is deep-copied too, so a test cannot mutate
metadata used by another test.

Disable this optimization for A/B measurement with:

```bash
python tests/e2e/run.py --no-seed-cache ...
```

The cache is process-local. Parallel workers never share a template or storage tree.

### Reduced motion in the common browser context (opt-in)

`open_app_page()` can request `prefers-reduced-motion: reduce` when
`PRKS_E2E_REDUCED_MOTION=1` is set. PRKS already treats reduced motion as a
supported accessibility mode. It is **off by default** in E2E because the
global reduced-motion CSS zeroes transition durations and changes overflow /
collapsed-chrome layout enough to break scroll-restore and compact-notes
assertions. Enable it for motion-latency experiments; leave it off for the
ordinary gate.

### Opt-in infrastructure profiling

`--profile` records per-test infrastructure phases without making ordinary runs
pay profiling overhead:

- `seed_build`
- `seed_clone`
- `server_start`
- `browser_context`
- `app_ready`
- `async_wait`
- `request_routing`
- `server_stop`

Example:

```bash
python tests/e2e/run.py --feature sync --jobs 1 --no-pointer-capture --profile
python tests/e2e/run.py --feature sync --jobs 1 --no-pointer-capture --profile --no-seed-cache
```

Compare the same selection, commit, worker count and machine. Do not compare a
parallel run to a serial run when judging an individual-test optimization.

`--profile` and `--no-seed-cache` do **not** update `.tests/e2e-timings.json`
(or last-failed history). Those files train ordinary-gate LPT sharding; writing
instrumented or cache-off durations into them would poison later normal runs.
Profile/slowest output for the current run still prints as usual.

The same applies when the run is configured through the environment instead of
the flags: `PRKS_E2E_PROFILE=1` or `PRKS_E2E_SEED_CACHE=0` make the run
non-representative on their own, so the runner suppresses history persistence
for them too and prints which benchmark mode is active. `tests/e2e/policy.py`
(`benchmark_modes()`) makes that decision once, from the effective
configuration after CLI flags are exported into the environment.

### Opt-in hang diagnostics and Chromium recycle

`PRKS_E2E_DIAGNOSTIC=1` prints privacy-safe stage heartbeats (`START`,
`SERVER_READY`, `CONTEXT_READY`, `APP_READY`, `BODY_DONE`, `CONTEXT_CLOSED`,
`SERVER_STOPPED`, plus runner `STOP`) so a hang can be attributed to a lifecycle
stage rather than only "last started test".

Heavy service-worker modules (starting with Offline Work metadata) may relaunch
Chromium after every N closed BrowserContexts. Default for that module is **1**
(fresh Chromium per test); override with `PRKS_E2E_CHROMIUM_RECYCLE_EVERY`
(`0` disables). Fresh contexts remain per-test — only the browser process is
recycled. Recycle is **lazy**: `after_context_closed` only sets a flag;
`get_browser()` relaunches before the next test, so the last test does not
spawn an unused Chromium before `tearDownModule`. Measured below intermittent
post-`APP_READY` stalls (as early as ~case 5 with recycle=12; still observed at
~case 24 with recycle=4).

## Remaining opportunities to measure

These should be changed only when profiling demonstrates a material gain and the
same behavior remains covered.

### 1. Split oversized base fixtures

Many specialized seed functions build on `seed_library()`. Some scenarios may pay
for research-note indexing, PDFs, roles or related Works that they never exercise.
Create smaller composable seeds only if profile data shows seed work remains
significant after snapshot caching.

Risk: accidentally making assertions vacuous because a fixture no longer contains
the state that makes a path meaningful.

### 2. Move async polling into the browser

`wait_for_async()` currently resolves the predicate with `page.evaluate()`, then
polls every 50 ms from Python. This is semantically correct and deliberately avoids
the Promise-truthiness trap documented in AGENTS.md, but frequent durable/offline
waits incur Python/Playwright round trips.

A candidate replacement is one browser-side async polling loop that returns only on
success/timeout while preserving the current resolved-value semantics and diagnostic
last value.

Measure `async_wait` first. Do not replace it with ordinary
`page.wait_for_function(() => promise)`, which is incorrect for these predicates.

### 3. Reduce same-origin request interception cost

`PageCollector` currently installs `page.route("**/*", ...)` so unexpected
external HTTP is blocked and recorded. Every same-origin CSS/JS/API/PDF request also
passes through that Python callback.

The `request_routing` profile phase measures this callback cost. If material,
investigate a design that preserves fail-closed external-network detection without
round-tripping normal same-origin traffic through Python.

Risk: weakening the guarantee that E2E cannot silently depend on the public network.

### 4. Faster initial application readiness

`open_app_page()` currently navigates to `/`, waits for multiple shell elements,
clicks Folders, then waits for `#/folders`.

Candidates:

- navigate directly to the canonical Folders route if behavior is identical;
- expose a stable application-ready signal derived from real initialization state;
- collapse redundant waits once that signal is proven sufficient.

Measure `app_ready` first. A synthetic E2E-only production endpoint or fake backend
is not acceptable.

### 5. Server startup work

Every test intentionally launches a fresh PRKS process. Profiling may show startup
dominates after seed caching.

Investigate startup diagnostics before considering reuse:

- dependency gate cost;
- migrations/schema verification;
- derived-index reconciliation;
- log/config initialization;
- other testing-mode startup work.

Prefer making legitimate startup work faster over skipping production startup paths
only in tests.

### 6. Server reuse with storage reset

A long-lived server per module/worker could remove process startup completely, but it
has the highest isolation risk. PRKS contains database state, derived indexes,
in-memory performance state, caches and local-first behavior. Reusing a process can
create order-dependent failures that do not exist for users.

Do not implement server reuse unless measurements show server startup is the dominant
remaining cost and there is a proven reset mechanism that is equivalent to process
restart. Keep a process-per-test validation lane if reuse is ever introduced.

### 7. Browser context/page reuse

Fresh contexts provide cookie, storage, service-worker and IndexedDB isolation.
Offline tests specifically depend on those boundaries.

Reusing contexts is therefore generally unsafe. Reusing Chromium (already done per
module) is the correct coarse optimization. Consider narrower reuse only for classes
that can prove they do not depend on context isolation.

### 8. PageCollector event volume

Besides routing, the collector observes console, failed requests and every response.
The response handler is lightweight, but profiling can be extended with event counts
if routing is not the explanation.

Do not remove failure collection just to improve timing.

### 9. Avoid unnecessary reloads/navigation inside individual scenarios

The slowest-test report plus `--profile` should be used to inspect tests that
repeatedly reload, reopen the same route, or rebuild UI state where a lower-level
assertion would prove the same contract.

Do not rewrite a true end-to-end contract as a unit test merely to make the E2E
number smaller. Instead, keep only the browser interactions required to prove the
browser-visible behavior and move redundant setup/assertions to unit/API/Node tests.

### 10. Find fixed sleeps and animation waits

The suite policy already prefers observable state over fixed delays. Continue auditing
new/slow tests for `wait_for_timeout`, sleeps, or animation-based readiness.

Some measured delays are intentional race barriers documented by stress testing; do
not delete them just because they look expensive.

### 11. Resource-heavy PDF/graph scenarios

PDF viewers, graph rendering, CodeMirror and multiple tiled workspaces can dominate
CPU/memory rather than test harness overhead. For these cases:

- keep tests focused on one browser-visible contract;
- avoid mounting duplicate heavy views unless the test is specifically about them;
- preserve realistic renderer behavior;
- use the existing timing history to avoid putting several heavy tests on one worker.

### 12. Test count and duplicated coverage

A mature E2E suite can become slow simply because multiple browser tests prove the
same invariant. Periodically map slow E2Es to unit/API/Node coverage and remove only
true duplication.

The decision must be coverage-based, not duration-based.

## Benchmark protocol

For any optimization:

1. use the same commit except for the candidate change;
2. use the same machine and pinned Chromium;
3. use `--jobs 1` when measuring individual-test changes;
4. run the same feature/test IDs;
5. compare at least two warm runs where practical;
6. report median/representative per-test timings, not only full-suite wall time;
7. inspect failures/flakiness as well as speed;
8. then run the normal parallel feature gate and finally the full gate once.

Recommended first benchmark:

```bash
python tests/e2e/run.py --feature sync --jobs 1 --no-pointer-capture --profile --no-seed-cache
python tests/e2e/run.py --feature sync --jobs 1 --no-pointer-capture --profile
```

Then repeat for a fixture-heavy domain such as graph/folders and one PDF-heavy group.

## Acceptance criteria for this optimization wave

- no production data or live `PRKS_STORAGE` is touched;
- every test still receives fresh writable storage;
- no retries are introduced;
- no browser/network assertion is weakened;
- no fake E2E API/backend is added;
- unit tests prove cached templates cannot be mutated through cloned storage or IDs;
- targeted E2E results are compared with cache on/off;
- full E2E gate passes before merge;
- measured per-test improvement is reported in the PR description.
