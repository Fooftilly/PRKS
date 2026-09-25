# E2E performance investigation

This document tracks per-test E2E cost separately from suite-level sharding.

The runner already has strong suite-level controls: individual-test timing history,
longest-processing-time-first sharding, per-worker Chromium reuse, targeted feature
selection, affected-test selection, last-failed runs, and a full-gate deadline. The
remaining target is the cost *inside one test*.

## Committed timing baseline

Fresh checkouts (especially cloud agents) have no machine-local timing history.
`tests/e2e/timing-baseline.json` supplies coarse `module.*` / `module.Class.*`
prefix weights so LPT sharding is useful on the first run. At schedule time the
runner merges:

1. committed baseline prefixes;
2. machine-local exact timings from `.tests/e2e-timings.json`, which override the baseline.

Invariants:

- local exact IDs always win over baseline prefixes;
- the baseline is never copied into `.tests/e2e-timings.json`;
- the human "slowest tests" report uses observed/local exact timings only — never baseline prefixes.

### Refreshing the baseline

Do **not** hand-edit `timing-baseline.json` indefinitely, and do **not** treat a
developer laptop's `.tests/e2e-timings.json` as the authoritative source. Refresh
from representative CI or full-gate measurement exports (same JSON shape as the
runner's exact timing history: unittest id → seconds):

```bash
# Dry-run (stdout JSON)
scripts/e2e update-timing-baseline --from /tmp/ci-e2e-timings.json

# Median across multiple representative runs, then write the committed file
scripts/e2e update-timing-baseline \
  --from /tmp/ci-run-a.json \
  --from /tmp/ci-run-b.json \
  --write
```

`--write` updates `tests/e2e/timing-baseline.json`. Review the result as an
ordinary JSON diff in the PR. The generator emits only `prefix.*` keys (module
medians, plus class-level prefixes when a class is a clear outlier), so the
committed file stays small and does not absorb exact per-test IDs.

Committed writes fail closed unless the measurement export covers **every**
current E2E module and every discovered exact test ID (so a single shard or
affected-run file cannot wipe most bootstrap weights). Partial / experimental
generation needs `--allow-partial` or an alternate `--output` path.

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
configuration after CLI flags are exported into the environment, and the
infrastructure-profile report prints for `PRKS_E2E_PROFILE=1` exactly as it
does for `--profile`. Those exports last for the one runner invocation.

### Opt-in hang diagnostics and Chromium recycle

`PRKS_E2E_DIAGNOSTIC=1` prints privacy-safe stage heartbeats (`START`,
`SERVER_READY`, `CONTEXT_READY`, `APP_READY`, `BODY_DONE`, `CONTEXT_CLOSED`,
`SERVER_STOPPED`, plus runner `STOP`) so a hang can be attributed to a lifecycle
stage rather than only "last started test".

The runner always tracks the current test id + latest stage in-process (and in
a per-worker heartbeat file under parallel jobs). A **per-test watchdog**
(`PRKS_E2E_TEST_WATCHDOG`, default 300s, `0` disables) kills a stuck worker and
reports that test id + stage without waiting for the full-suite
`PRKS_E2E_FULL_TIMEOUT` (default 1200s). It does not retry. Playwright assertion
timeouts remain independent.

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

### 2. Move async polling into the browser — done

`wait_for_async()` runs one browser-side async polling loop inside a single
`page.evaluate`, preserving resolved-value semantics and the diagnostic last
value (see `tests/e2e/test_wait_for_async.py`). Do not replace it with ordinary
`page.wait_for_function(() => promise)`, which remains incorrect for these
predicates. Re-measure `async_wait` when changing the poll interval or the
in-page helper.

### 3. Reduce same-origin request interception cost

`PageCollector` currently installs `page.route("**/*", ...)` so unexpected
external HTTP is blocked and recorded. Every same-origin CSS/JS/API/PDF request also
passes through that Python callback.

The `request_routing` profile phase measures this callback cost. If material,
investigate a design that preserves fail-closed external-network detection without
round-tripping normal same-origin traffic through Python.

Risk: weakening the guarantee that E2E cannot silently depend on the public network.

### 4. Faster initial application readiness

`open_app_page()` loads the canonical Folders route directly (`/#/folders`) and waits
on real shell + Folders UI (`#sidebar`, `.prks-folder-library`,
`location.hash === '#/folders'`). The previous `/` + Folders-nav click was a pure
re-navigation once empty-hash canonicalize already mounted that surface.

Implemented:

- navigate directly to the canonical Folders route (same real-UI readiness waits;
  no synthetic ready fakes or E2E-only init endpoints).

Remaining candidates (measure `app_ready` first):

- expose a stable application-ready signal derived from real initialization state;
- collapse redundant waits once that signal is proven sufficient.

A synthetic E2E-only production endpoint or fake backend is not acceptable.

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

Classify each contract before deleting Chromium coverage:

- **KEEP** — DOM, focus, real UI wiring, reload/remount, offline browser state,
  user-visible conflict controls, cache-visible projections Node cannot prove.
- **SPLIT** — thin browser boundary + move protocol/state-machine branches lower.
- **MOVE** — prove the invariant in Node/API/Python first, then drop the redundant E2E.

Hard constraints: no retries to hide flakes; no fake E2E backend; no weakened
assertions; no shared browser/server state without proven isolation; do not delete
an E2E merely because a similarly named unit exists — map the contract first and
land the replacement fast coverage before removing the browser scenario.

### Work Source family (applied)

`tests/e2e/test_work_source_offline.py` retains **14** Chromium scenarios for the
browser boundaries above, including editor `state.observed` paths that Node
store/handler tests cannot replace:

- offline edit + reload, conflict UI, non-video editor absence, invalid-URL
  editor refusal, open-editor remount base, cache-visible thumbnail,
  Apply/Use-server projections including offline recovery;
- **editor coalescing** (double-edit before send; return-to-base);
- **post-Apply cancel** (Apply button updates observed base, then choosing the
  server's video cancels);
- **convergent ACK open-editor spelling** (`acceptAck` writes the stored URL).

Moved out of Chromium only where the contract is proven without the editor:

| Removed E2E | Replacement |
| --- | --- |
| four-column pending identity | Node `effectiveSourceOverlay` in `run_work_source_sync_selftest.js` |
| respelling same video not a conflict | Python `test_a_stale_but_convergent_choice_is_not_a_conflict` |

Store-layer Node coverage (`coalescing`, `conflictResolution`, handler
`isResult` / `reconcile` for SHORT→WATCH convergent ACK) remains as fast
regression for the durable store and sync handler. It does **not** authorize
dropping the Chromium editor scenarios above: a hand-built `serverBase` or a
direct `reconcileWorkSource` call still passes if the Apply button or
`acceptAck` regresses.

Op-id replay, protocol bounds, thumbnail invalidation on the server write, and
revision/conflict arithmetic remain in `tests/test_work_source_sync.py` (and the
frontend contract module that runs the Node selftest). They were never Chromium-
only contracts.

#### Work-Tag rationalization pilot

The first coverage-based reduction applies the KEEP / SPLIT / MOVE rule to
`tests/e2e/test_work_tags_offline.py`.

| Former browser scenario | Replacement fast coverage | Decision |
| --- | --- | --- |
| Coalescing across reload and repeated intent | `tests/browser/run_local_store_selftest.js` owns repeated intent and opposite cancel across a reopened store; `test_post_reload_opposite_edit_cancels_pending` keeps the thin remount → Manage tags boundary for both opposite UI directions (chip remove and picker re-add) | SPLIT |
| Lost response replays once with the same operation identity | `tests/browser/run_work_tag_sync_selftest.js` proves a transport loss leaves the original envelope pending and replays the exact same `op_id`/envelope; `tests/test_work_tag_sync.py` proves server replay of an existing `op_id` is exact/idempotent | SPLIT → fast layers |

The browser module deliberately retains scenarios whose value is the integrated
boundary itself: offline add/remove through the real Work UI, reload/restart/reconnect,
post-reload opposite edit through the remounted tag editor, conflict buttons and
visible optimistic state, degraded Tag-catalog behavior, Settings-driven cache clear,
online UI wiring to the durable queue, and Tag create/delete/merge lifecycle behavior.

This is the model for later families: add or confirm the lower-level regression
coverage first, then remove only the redundant Chromium composition. Do not batch
large numbers of removals without a per-contract mapping.

### Work-Open rationalization

Applies the same KEEP / SPLIT / MOVE model as the Work-Tag pilot (#204) to
`tests/e2e/test_work_opens_offline.py`.

| Former browser scenario | Replacement fast coverage | Decision |
| --- | --- | --- |
| Lost response applies the event once | `tests/browser/run_work_open_sync_selftest.js` proves a transport loss leaves the original envelope pending and replays the exact same `op_id`/envelope; `tests/test_work_open_sync.py` proves server replay of an existing `op_id` is exact/idempotent | SPLIT → fast layers |
| Older event never overwrites a newer one (both arrival orders) | `tests/test_work_open_sync.py` `test_max_register_over_event_time` and `test_arrival_order_does_not_decide` | MOVE |
| Repeated opens of one Work are one event | Node `recording()` in `run_work_open_sync_selftest.js` | MOVE |
| Different Works keep their own events | Node `recording()` + `overlay()` | MOVE |
| Open event for a deleted Work is consumed, not parked | Node `terminal()` ENTITY_NOT_FOUND discard; Python `test_missing_work_is_terminal` | MOVE |

The browser module retains the integrated boundaries that still need Chromium:
offline open reorders Recent and sends nothing, overlay survives real reload,
reconnect records event time (not sync time) with in-place Recent reconcile,
pending open survives a PRKS server restart, and a missing Recent snapshot stays
honestly unavailable rather than fabricating cards.

Add or confirm lower-level coverage first, then remove only the redundant
Chromium composition. Do not batch-delete cache/routing families (folders,
playlists, person-groups, browse, graph) without a per-contract map.
#### Concepts durable — definition cancel + parent-set

Applies the KEEP / SPLIT / MOVE model to the two pure queue invariants in
`tests/e2e/test_concepts_durable.py` that were already proven below Chromium.

| Former browser scenario | Replacement fast coverage | Decision |
| --- | --- | --- |
| Definition taken back before send leaves no intent | Node `theDefinitionCoalescesAndCancels` (store) **and** `theDefinitionCancelGoesThroughApiWrapper` — production `updateConcept` from `frontend/js/api.js` against fake IndexedDB (plus dirty-fields overlay asserts) | MOVE |
| Same parents (order-insensitive set) leave no intent | Node `theParentSetIsASet` (store) **and** `theParentSetCancelGoesThroughApiWrapper` — production `putConceptParents` from `frontend/js/api.js` | MOVE |

Retained browser boundaries in that module stay Chromium: offline create +
reload, named refusals (`CONCEPT_EXISTS`, cycle, note-named delete), definition
edit shows and lands, identity rename→alias / alias reload / rename+alias one
conflict unit, reparent shows at both ends, tombstone hide + refused delete
comes back, fold created-offline delete. Cache matrices in giant
`test_offline.py` are out of scope (giant-modules stream).

Do not expand this slice into Positions or Arguments durable modules. Stack
doc edits carefully against other family sections in this file (#204 Work-Tag,
Work Source, Work-Open).

#### Work-Notes rationalization

Applies the same KEEP / SPLIT / MOVE model as the Work-Tag pilot (#204) and
Work-Open rationalization (#211) to `tests/e2e/test_work_notes_offline.py`.

| Former browser scenario | Replacement fast coverage | Decision |
| --- | --- | --- |
| Editing back to the acknowledged body cancels the pending op | Node `coalescingFor(RESEARCH)` A→B→A cancel plus `mutationTestAtoBtoA` in `tests/browser/run_work_note_sync_selftest.js`; static `tests/test_frontend_work_note_sync.py` mutation pin | MOVE |
| Private ACK does not fence Concepts | Node `reconciliation()` Private ACK must not advance Concepts/Arguments/Graph generations (and patches only `private_notes`) | MOVE |
| Stale research revision is a conflict (compact body privacy) | Node `handlerContract` rejects body-carrying conflicts; Python `test_a_stale_research_base_against_a_different_value_conflicts` and `test_compact_conflict_results_omit_note_bodies` | SPLIT → fast layers for shape; thin Chromium retains reconnect park |

The browser module retains the integrated boundaries that still need Chromium:
offline research note survives CodeMirror remount and creates a Concept on ACK,
and research + private editors remain independent across reload (private markup
must not create Concepts). The stale-revision scenario keeps only the real
reconnect conflict park; field-level compact-result privacy is not re-asserted
in Chromium.

Add or confirm lower-level coverage first, then remove only the redundant
Chromium composition. Do not batch-delete cache/routing families without a
per-contract map.

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
