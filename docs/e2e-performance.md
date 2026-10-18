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

### Opt-in offline Research Graph navigation phases

The normal six-shard CI logs recorded a **27.1s** duration for
`ResearchGraphOfflineTests.test_record_graph_navigation_both_directions`
on [PR #742's checkout gate](https://github.com/Fooftilly/PRKS/actions/runs/39054070761).
That was a single unprofiled CI observation, **not** a phase attribution or
same-commit comparison.

Use **Actions → E2E Offline Graph Navigation Profile → Run workflow**
on a commit containing the profiler. The manual-only job runs one excluded
warm-up, then **three isolated full Chromium test repeats on the same runner**
using the normal application source checkout, with `--profile`. It publishes
per-phase timings in the Actions job summary (no fixture paths, user records,
or test data artifacts). Its phases show:
- startup/initial page readiness, then warming five record caches;
- warming core/people Graph snapshots and switching offline;
- five record → Graph → record round trips (Concept, Position, two Arguments, Person);
- separate `graph_nav_stage_*` intervals for **entering the record** (navigate,
  provenance, drawer), **opening the Graph** (click, Cytoscape mount/focus,
  provenance), and **returning** (click, hash, provenance);
- the uncached destination and missing Graph cases.

A [temporary, PR-only counterbalanced probe](https://github.com/Fooftilly/PRKS/actions/runs/39070620726)
on #752 tested Concept-first vs Position-first on one runner, using two
excluded warm-ups and six measured runs. Concept still took **5.250s vs
5.235s** when switched from first to second, while Position took **3.332s vs
3.364s** when switched from second to first. Therefore the ~1.9s gap is
**Concept-detail navigation/return work, not a first Graph mount effect**.
The Concept-to-Graph step itself took ~1.2s in both orders, similar to
Position. Do not infer the precise inner route function responsible from
these grouped timings alone; a narrower experiment is needed before changing
production behavior.

The fine-grained stages are **nested in their parent roundtrip totals**,
not extra time to add. A completed trial with an optional stage is displayed
in the manual workflow summary only if every trial measured that stage.
The phase labels start `graph_nav_` and **overlap** generic `async_wait`,
request routing and startup phase totals. They are diagnostic breakdowns,
excluded from the infrastructure-additive slowest-test ranking. Profiling
runs do not train shard timing history and must not be compared as if ordinary
CI were also instrumented. Keep all real DOM/navigation/offline assertions;
use the largest measured phase to motivate a distinct, same-runner A/B
optimization before touching production logic or test assertions.

### Opt-in offline Argument rename/Graph propagation phases

The slowest non-Graph case observed in [PR #742's full checkout gate](https://github.com/Fooftilly/PRKS/actions/runs/39054070761)
was `DurableArgumentTests.test_pending_rename_reaches_position_argument_picker_and_graph`,
at **15.1s** in one unprofiled Chromium shard. That observation is not
a measurement of which substep costs the time.

Use **Actions → E2E Argument Overlay Phase Profile → Run workflow** after
merging the instrumentation. The manual-only workflow runs the unchanged test
with fresh test storage/browser contexts for one excluded warm-up and three
serial profile repeats on the same hosted runner. It fails rather than
summarizing incomplete or failing browser runs.

The ten `arg_overlay_*` phases distinguish browser/server setup; warming
Argument, Position and Graph snapshots; offline rename; Position, picker and
Graph propagation; and renamed target/response navigation. The summary
reports the median and each sample for every phase plus the whole-test wall
median. These phases do **not** add default E2E cost, and neither storage
isolation nor the real-browser/offline assertions are changed.

The [first master run #39102509934](https://github.com/Fooftilly/PRKS/actions/runs/39102509934)
passed all four Chromium executions: one excluded warm-up and three
measured. Median single-test wall time was **16.176s**. The slowest
**inclusive** phases were `arg_overlay_verify_target` (**2.862s**) and
`arg_overlay_verify_response` (**3.095s**); together **5.957s**. The
offline state transition plus first rename was **2.079s**, Argument cache
warming **1.896s**, and initial setup **1.690s**. This does not prove
whether time within the target/response stages was spent writing the
offline operation, starting navigation or waiting for the visible names.

A [temporary same-branch real-Chromium probe](https://github.com/Fooftilly/PRKS/actions/runs/39103351420)
passed its excluded warm-up plus three measured trials. Median whole-test
wall time was **16.353s**. Nested subphase medians (seconds) were:

| Verification | Full phase | Offline write | Awaited route + detail render | Residual visible-text wait |
| --- | ---: | ---: | ---: | ---: |
| Target rename | 3.438 | 2.221 | 1.179 | 0.002 |
| Response rename | 3.040 | 1.960 | 1.031 | 0.004 |

The writer calls (≈2 seconds each) account for the largest component of
these stages. The `render` step also includes route navigation: `prksNavigate`
returns a promise that is only fulfilled after the current detail render
completes. It must **not** be interpreted as just URL/history routing cost.
The residual DOM checks are negligible because the awaited navigation already
renders the visible content. The nested timers
`arg_overlay_target_{write,render,visible}` and
`arg_overlay_response_{write,render,visible}` are contained inside their
respective `arg_overlay_verify_{target,response}` totals; they explain, not
supplement, those parents. Missing or failing subphases invalidate the manual
profiler report. No real-browser assertion or test operation was removed.

The next opt-in probe measures the real offline `updateArgument` writer
*inside* each existing `arg_overlay_{target,response}_write` phase. Browser
`performance.now()` wrappers time its operations snapshot, acknowledged
revision/base acquisition, cached revision/entity reads, and durable field
enqueue/after-hook. Wrappers are installed only around the existing awaited
write, then restored in `finally`; ordinary E2E is not instrumented.
The browser timings are returned to the Python runner and rejected if missing,
non-finite, or negative. No acknowledged revision is guessed, no new network
mutations are permitted, and no offline queue ordering is changed.

The `write_base` parent **includes** its `write_revision_read` and
`write_record_read` children. `write_enqueue` is the existing durable
writer including its pending-name refresh. Treat the original `write` Python
timer as inclusive; browser timings can differ slightly because of IPC and
diagnostic overhead. Collect measurements on one runner before deciding
whether the dominant cost is safe to optimize. This is **not** authorization
to bypass revision validation, cache invalidation, or durable persistence.

Generic infrastructure and async wait telemetry may overlap all these
diagnostics; do not double-count them. Use the dominant measured inner step
to motivate a *separate* same-runner baseline/candidate experiment before
altering production logic. Never infer speedup from profiled-vs-normal CI
times alone.

### Opt-in infrastructure profiling

`--profile` records per-test infrastructure phases without making ordinary runs
pay profiling overhead:

- `seed_build`
- `seed_clone`
- `server_start` (inclusive: process spawn + real HTTP readiness)
  - `server_spawn` (launch the fresh PRKS subprocess)
  - `server_http_ready` (poll the real `/api/works` endpoint to readiness)
- `browser_context`
- `app_ready` (inclusive: navigation + existing real UI readiness checks)
  - `app_navigation` (navigate to `/#/folders`, wait for `domcontentloaded`)
  - `app_sidebar_wait` (visible `#sidebar`)
  - `app_folder_wait` (visible `.prks-folder-library`)
  - `app_route_wait` (canonical `#/folders` hash confirmation)
- `nav_response_wait` (browser Navigation Timing: `fetchStart` → `responseStart`)
- `nav_response_body` (browser: `responseStart` → `responseEnd`)
- `nav_dom_content_loaded` (browser: `responseEnd` → `domContentLoadedEventEnd`, inclusive)
  - `nav_until_interactive` (`responseEnd` → `domInteractive`: parsing, blocking scripts, resource waits)
  - `nav_interactive_to_dcl` (`domInteractive` → `domContentLoadedEventStart`)
  - `nav_dcl_handlers` (`domContentLoadedEventStart` → `domContentLoadedEventEnd`)
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

The indented setup phases **partition their inclusive parent**, rather than
representing extra work: do not add `server_spawn` and `server_http_ready`
to `server_start`, or add the four `app_*` detail phases to `app_ready`.
Timers do not alter the HTTP probe, fresh subprocess/storage, Playwright
context, navigation or any UI assertions; a failed readiness check still fails.
On bind-conflict retries, `server_spawn`, `server_http_ready` and
`server_start` cover **only the successful attempt**; discarded attempts
are reported separately as `server_spawn_failed` and
`server_http_ready_failed`, rather than silently polluting the successful
breakdown. If a UI navigation/readiness step raises, **all measured stages
of that page-open attempt** use `app_navigation_failed`,
`app_sidebar_wait_failed`, `app_folder_wait_failed`, or
`app_route_wait_failed` as applicable. The inclusive `app_ready` covers
only successful page opens. Thus a later failed page-open cannot be lost
when an earlier page-open within the same unittest has already recorded
`app_ready`. Ordinary unprofiled runs keep only their original inclusive
clock reads: extra subphase clock samples are gated on an active profile.

The first [same-runner route benchmark](https://github.com/Fooftilly/PRKS/actions/runs/38799768587)
found a negligible **−0.68%** wall-time improvement from the experimental
routing fast path (40 E2E tests, 4 ABBA trials), despite a measurable reduction
in Python URL-decision time. Leave that mode off by default. The same run
reported roughly `server_start=15.5s` and `app_ready=18.5s` in 40 serial
tests. The subphases above identify whether launch/probing or navigation/DOM
readiness is responsible before attempting another optimization. The sums are
**profile-instrumented** and must not be mistaken for an unprofiled speedup.
The runner's **slowest profiled tests** sort key excludes successful
nested detail phases when an inclusive parent exists; otherwise it would count
the same intervals twice. Failed `*_failed` phases remain additive even when
the test contains other successful attempts. Browser-native `nav_*` diagnostics
are also excluded from the additive ranking when `app_navigation` or
`app_ready` is present. Every successful profiled page-open counts a
`nav_timing_attempts` event and every validated browser Navigation Timing
entry counts a `nav_timing_samples` event. These are *counts, not seconds*:
the manual serial benchmark exports them in the distinct
`navigation_samples` JSON object, excluding them from phase sums and
slowest-test rankings. The browser Navigation Timing intervals are a **subset
of browser navigation**, not additional app setup work and not an exact
partition of the Playwright-measured `app_navigation`: browser event and
Python RPC clocks differ. The three nested DOM milestones partition
`nav_dom_content_loaded` (which is an inclusive parent, not extra work).
`nav_until_interactive` includes synchronous scripts and blocking resource
fetches; it is not a pure JavaScript execution/CPU timer. They are read only after real app readiness succeeds,
under active profiling, with missing/unsupported browser entries skipped
without masking or causing test failures. Individual phase details and
aggregate phase totals remain visible.

For a comparable measurement after this profiling change, manually run
**Actions → E2E Routing A/B Benchmark → Run workflow** on the merged commit.
Its aggregate JSON report already carries all profile phase totals; no extra
CI mode or artifact upload is needed.

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

### Profiling on GitHub Actions (manual only)

The `PRKS Full E2E Gate` workflow has a `workflow_dispatch` input named
`profile` (off by default). In **Actions → PRKS Full E2E Gate → Run workflow**,
check **Collect per-test infrastructure phase timings** to obtain the phase
totals and slowest profiled tests in each `E2E (N/total)` job log. The normal
PR and master-push gates are unchanged and continue saving their usual local
timing history; an explicitly profiled manual run does not train timing history
or mutate last-failed data. As with local `--profile`, measurements include
instrumentation overhead and must not be compared as if they were ordinary
gate timings.

This switch intentionally does not upload benchmark artifacts or test data.
Compare equivalent test selections on the same runner type and examine which
phases dominate before modifying the harness or its isolation boundaries.

### Experimental same-origin routing fast path (A/B only)

The October 14 profiled full gate (836 tests, six runners; Actions run
`38631524680`, attempt 2) reported `request_routing=4042s` across shards,
more than the approximately 3272s combined test wall time. This metric wraps
synchronous `route.continue_()`/Playwright dispatch and can overlap other
work; it is **not** 4042 seconds of exclusively removable Python overhead.
The new `routing_decision` profile phase stops at dispatch and isolates the
Python URL decision work. It is also a sampled sum, not an additive component
of `request_routing`.

The ordinary path keeps `page.route("**/*", ...)` and validates the parsed
origin. `PRKS_E2E_ROUTE_FASTPATH=1` tries a conservative authority-and-path
prefix first, falling back to the same parser for aliases/edge cases. **Both
paths still intercept the same request patterns** and block off-origin HTTP
through the same handler. Redirect behavior is unchanged; this experiment
makes no new guarantees about Playwright redirect handling. There is no
proxy/network-policy change, no test isolation change, and no change to what
tests are selected. Playwright's
routing still disables the browser HTTP cache in both modes, so this benchmark
does not measure any hypothetical cache win.

To compare GitHub-hosted runners after merging, launch
**Actions → PRKS Full E2E Gate → Run workflow** with **profile** enabled,
first with **route_fastpath** unchecked and then checked. Repeat both runs
on the same commit and compare shard/test wall time and `routing_decision`;
`request_routing` alone is not evidence of a speedup. The fast-path mode is
non-representative and does not train E2E timing/last-failed history, even if
run without `profile`. Reject it if the wall-time difference is negligible,
the regression gate fails, or external-network blocking weakens. Do not turn
it on for normal PR/master gates without maintainer approval.

### One-run, same-runner A/B spot check (manual only)

The independent **E2E Routing A/B Benchmark** GitHub Actions workflow runs
the real Chromium E2E runner with `--feature shell --feature work-create`,
`--jobs 1`, `--no-pointer-capture`, and `--profile`. It performs four
separate, fresh-isolation runs in **baseline → fast path → fast path → baseline**
order on the same runner and commit, **after an unmeasured baseline warm-up**
of the identical selection (profiling enabled, no benchmark JSON output).
The warm-up absorbs first-use Chromium, server, and Python import costs so the
baseline is not automatically disadvantaged. All five selections must pass.
Ordinary PR and master full gates are not changed or shortened. This workflow
is **manual-only**; it does not upload
test artifacts or mutate ordinary E2E timing/last-failed history, including
when the per-test watchdog terminates a hung test. The serial runner emits a
versioned, aggregate-only JSON report to `PRKS_E2E_BENCHMARK_RESULT` **only**
when explicitly configured. Each trial writes its own temporary report on the
runner; nothing is uploaded. The summary validates the JSON schema, active
benchmark modes, test counts and phase metrics. Missing, mismatched or
unprofiled data fails the job without presenting a misleading A/B result;
the workflow does not depend on human-readable log formatting.

Open **Actions → E2E Routing A/B Benchmark → Run workflow** on `master`.
The job summary reports each trial's E2E runtime and the instrumented
`routing_decision` total, the two per-mode medians, and the relative change
(negative is faster). When browser Navigation Timing is available in all four
trials, an additional table shows browser response wait, response transfer,
and the inclusive response-to-DOMContentLoaded aggregate, split into response
end → DOM interactive, DOM interactive → DCL event start, and DCL event
handlers. The summary validates this nested partition without double-counting.
Missing navigation entries instead
produce an explicit coverage note without failing the routing A/B experiment;
invalid/nonfinite navigation values are rejected. The benchmark requires
`captured == attempted > 0` **in each trial**, with matching attempted
navigation counts across all four trials, before showing browser milestone
medians. Missing phase sums with complete coverage mean a *valid zero*
(the profile serializer omits zero-duration sums). Partial/uneven coverage
never produces misleading A/B browser medians; it prints each trial's
captured/attempted count instead. The structured diagnostic report uses
schema version 2 to separate integer counters from elapsed phase seconds.
It also reports **baseline and fast-path median phase
sums** for the successful server startup (`server_spawn`,
`server_http_ready`) and application readiness (`app_navigation`,
`app_sidebar_wait`, `app_folder_wait`, `app_route_wait`) breakdowns from
#699. The report fails closed if these metrics are absent, invalid, or do not
partition their inclusive parent within rounding tolerance. Parent and child
sums are not additive to each other and should not be confused with removable
wall-clock time.
A failed warm-up or measured trial fails the job;
none is skipped or automatically retried. Run the experiment more than once, and confirm any
promising improvement using paired, fully profiled six-shard runs before
changing defaults. Comparing four small feature selections does **not**
establish a full-suite performance gain.

### One-run classic-script deferral A/B (manual only)

The independent **E2E Classic Script Deferral A/B Benchmark** is a targeted
experiment following measured browser response-end → `domInteractive` costs.
The baseline is the committed `frontend/index.html`. The alternate HTML
adds `defer` to external classic script tags, preserving classic script
source paths and relative order. Because a module tag originally appears amid
parser-blocking classics (and normally runs after them), the temporary
candidate moves the **unmodified module tags after the deferred classics**
to preserve classic-before-module execution order. This is deliberate:
deferring classics without moving the module tags would change their execution
order and confound the experiment. The candidate preserves module source URLs
and relative order. Only the manual workflow generates this variant in a
temporary file and copies it over the
working tree's HTML between runs. A shell trap restores the original HTML.
**Production HTML, JavaScript, service-worker precache and ordinary CI are
not changed.**

The workflow runs the same real `--feature shell --feature work-create` E2E
selection in **baseline → deferred → deferred → baseline** order, after an
unmeasured baseline warm-up, on the same runner and commit. The routing fast
path is OFF in both arms, and both use real browser readiness checks and
per-test process/storage/context isolation. All trials must pass. The
summary reports elapsed wall time, the inclusive startup phases, browser DOM
milestones and verified navigation sample coverage. A failed/partial run
cannot be treated as evidence for the alternate loading strategy. If the
alternate mode is faster, repeat before proposing any production loading
change, then confirm it with the full six-shard E2E gate. If it is slower or
breaks behavior, do not change production script loading.

Open **Actions → E2E Classic Script Deferral A/B Benchmark → Run workflow**
on a `master` commit containing that workflow. Keep the existing routing
A/B benchmark unchanged; the two experiments measure different decisions.

### Measured script-loading experiments and next diagnostic

The [October 17 routing benchmark](https://github.com/Fooftilly/PRKS/actions/runs/39011721006)
found a **+1.49%** (slower) fast-path wall change on the 40-test serial
selection, despite lower in-process routing decision time. Keep the route
fast path disabled. Navigation Timing split the baseline response-end to DCL
interval into ~82% before DOM interactive, 7% interactive to DCL event start,
and 12% DCL handlers; these percentages are browser milestones, **not**
exclusive CPU work.

The [October 17 deferral benchmark](https://github.com/Fooftilly/PRKS/actions/runs/39017400245)
passed all trials and observed baseline **59.50s** versus deferred **58.96s**
median wall time (**−0.91%**). Across 38 page opens per trial, the baseline
response-end to DCL sum was **14.70s**, versus **14.02s** deferred. Most of
the time simply moved from before DOM interactive (12.00s → 0.88s)
to between DOM interactive and the DCL event (0.95s → 11.63s).
**Do not adopt blanket deferral from this small, marginal result.**

A separate manual-only **E2E Classic Script Coalescing A/B Benchmark** now
tests a different hypothesis: request volume may be material because
`frontend/index.html` includes 84 external classic scripts. The runner
generates **two temporary bundles**, one for head classics and one for body
classics, keeping both parser-blocking boundaries, original classic source
order, existing module tags and all real UI waits. It uses the same 40-test
selection on one runner: unmeasured baseline warm-up, then baseline → bundled
→ bundled → baseline. Both arms disable the route fast path and use real
per-test server/storage/browser isolation and off-origin request blocking.

This tests whether request coalescing is **worth investigating**, not whether
the generated bundles are safe to ship. Combining separate scripts can change
`document.currentScript`, strict-mode directive scope, source URLs,
lexical initialization timing and runtime error attribution; offline service
worker precache and content updates would also need separate design. The
workflow syntax-checks each bundle before running, fails if a selected E2E
test fails, requires complete structured benchmark summaries and restores
the original index and removes temporary assets on normal exit. It never
commits or uploads the candidate. As with other small experiments, require
repeated measurements, dependency/semantics review, offline coverage and a
full six-shard E2E gate before proposing any production adoption.

Open **Actions → E2E Classic Script Coalescing A/B Benchmark → Run workflow**
on a master commit that includes the workflow.

### Classic script coalescing: measured improvement and full-suite canary

The [October 17 classic-script coalescing benchmark](https://github.com/Fooftilly/PRKS/actions/runs/39022151709)
passed all 40 selected tests in each of its four ABBA trials. Same-runner
baseline trials took **61.69s** and **62.01s**; temporary head/body bundles
took **57.38s** and **57.56s**. Median wall time changed **61.85s → 57.47s**
(**−7.08%**). Across the same 38/38 navigations in each trial,
response-end→DOMContentLoaded aggregate fell **15.67s → 11.59s**,
and Playwright app-readiness aggregate fell **19.50s → 15.45s**.
Nested timings overlap their parents and do not represent additional savings.
This result supports *further testing*, not production deployment.

The **E2E Temporary Classic Bundle Full-Suite Canary** workflow tests the
same generated candidate against all six complete external E2E shards in
parallel, without replacing the normal full gate. It must be invoked manually
from Actions on a commit containing the workflow. Each job checks out an
isolated workspace, generates the exact two temporary bundles using the
existing diagnostic builder, validates JavaScript with `node --check`,
rewrites the local `index.html` only for the job, then executes
`python tests/e2e/run.py --jobs 1 --shard INDEX/6 --no-pointer-capture`.
The routing fast path remains OFF. All jobs restore original HTML and remove
temporary assets even on failure; pre-existing candidate filenames cause a
fail-closed abort. Every shard must pass; there is no aggregate check that
can mask one failed shard. The manual workflow never edits master or
production assets.

**Remaining caveats before adoption:** E2E browser contexts block service
workers, so even a green six-shard canary does **not** establish offline/PWA
equivalence. The service worker currently maintains an explicit static precache
for all 84 individual script assets, and `DEPENDENCY_REVISION` is linked to
the dependency manifest. A future production design must account for cache
revision, offline install/upgrade, rollback and generated artifact freshness,
and assess global/script semantics and source attribution. Recheck the
performance result in additional same-runner ABBA runs before changing
production. Do not merge a production bundle without these checks.

### Bundled full-suite canary: missing offline asset coverage

The [October 17 full-suite canary](https://github.com/Fooftilly/PRKS/actions/runs/39023728966)
failed all six shards in both attempts (~139–140 tests per shard).
The failures concentrate in offline/service-worker cases and include
missing `prksNavigate`, `createPrksOfflineStore`,
`prksOfflineNoteRequestFailure` and `prksGetResearchGraphDebug`
after offline reloads. The smaller 40-test A/B ran with service workers
blocked, so its **−7.08%** timing result cannot establish offline safety.

Root cause for the missing globals in those scenarios: the diagnostic's
temporary index referenced `/__e2e_classic_head_bundle.js` and
`/__e2e_classic_body_bundle.js`, neither of which is present in the normal
service worker's required static precache or its strict static-fetch allowlist.
An offline reload used the cached shell but could not reload its two scripts.
This is a **canary candidate/asset staging error**, not yet evidence of a
JavaScript concatenation semantic regression.

The manual-only canary now stages a **temporary copy of `sw.js`** alongside
the generated bundle files and HTML in each isolated Actions checkout.
The copy adds only the two temporary root-level script URLs to the required
static precache and static fetch allowlist; it is validated with `node --check`
and restored byte-for-byte even if the browser or E2E command fails. No
production service worker, asset manifest, or normal E2E gate is changed.

The canary's preflight now exercises the **failing path**: start the real
server and Chromium with `service_workers='allow'`, complete service-worker
installation and control, go offline and reload the cached shell, then verify
all four globals, successful HTTP statuses for both bundle scripts, and that
both responses came from the service worker. Preflight failure skips all
six expensive shards. A passing preflight is a smoke gate, not proof of
full-suite parity: all six shards must still pass. Before production adoption,
a permanent bundler needs its own cache revision, dependency/update/rollback
design, complete offline tests, and repeated speed measurements.

### Opt-in hang diagnostics and Chromium recycle

`PRKS_E2E_DIAGNOSTIC=1` prints privacy-safe stage heartbeats (`START`,
`SERVER_READY`, `CONTEXT_READY`, `APP_READY`, `BODY_DONE`, `CONTEXT_CLOSED`,
`SERVER_STOPPED`, plus runner `STOP`) so a hang can be attributed to a lifecycle
stage rather than only "last started test".

The runner always tracks the current test id + latest stage in-process (and in
a per-worker heartbeat file under parallel jobs). A **per-test watchdog**
(`PRKS_E2E_TEST_WATCHDOG`, default 120s, `0` disables) kills a stuck worker and
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

The `request_routing` phase includes blocking Playwright route dispatch and
can overlap other timings; `routing_decision` isolates the pre-dispatch Python
part. A conservative same-origin fast-path A/B experiment is available above;
normal same-origin requests still cross Python and HTTP caching remains
disabled. If off-origin `route.abort()` itself fails, the handler now raises
instead of falling back to `route.continue_()` (which could fail open).
Avoid narrowing `page.route("**/*")` to an external-only URL matcher
without proving same-origin redirects cannot bypass the network guard.

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
| Editing back to the acknowledged body cancels the pending op | Node `coalescingFor(RESEARCH)` A→B→A plus `mutationTestAtoBtoA`; editor-path coverage via `durableSaveThroughObservedBaseCancels` and production `enqueuePathAtoBtoACancels` (`prksEnqueueWorkResearchNotesSave` with editor context → pending returns to zero); static enqueue/flush pins in `run_work_note_sync_selftest.js` / `test_frontend_work_note_sync.py` | MOVE |
| Private ACK does not fence Concepts | Node `reconciliation()` dispatches Private ACK through `prksNoteSyncHandler.reconcile` → `prksOfflineReconcilePrivateNote` (must not advance Concepts/Arguments/Graph generations; patches only `private_notes`) | MOVE |
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

#### Work-People rationalization

Applies the KEEP / SPLIT / MOVE model defined in
[`tests/e2e/AGENTS.md`](../tests/e2e/AGENTS.md#coverage-layer-rule-keep--split--move)
to `tests/e2e/test_work_people_offline.py`.

| Scenario | Fast coverage | Decision |
| --- | --- | --- |
| Linking then unlinking before send leaves no intent | Node `coalescing()` in `run_work_role_sync_selftest.js` owns absent→ADD→REMOVE / present→REMOVE→ADD empty-queue arithmetic | **SPLIT** — thin Chromium: role-modal link → optimistic chip → unlink confirm → empty list → reconnect transmits nothing (`test_linking_and_unlinking_before_it_sends_leaves_no_intent`) |

The browser module also retains the integrated KEEP boundaries that still need
Chromium: offline Author link outranks `author_text` across Manage people /
Progress / Person page (including reload), last-Author unlink reveals text
credit, Editor fallback credit, cross-family pending role + `author_text`
composition, credit override through the role modal, live Research Graph Author
edge, online link→unlink without remount (base revision advances), and
stale-base convergent link with reconnect UI.

Credit / UI KEEP rows stay intact; folders / playlists / person-groups /
browse / graph cache matrices remain KEEP-heavy until per-contract Node maps
exist.

#### Work metadata rationalization — Slice A (core protocol)

Applies KEEP / SPLIT / MOVE to the durable-queue core of
`tests/e2e/test_work_metadata_offline.py`, independent of Work read-projection
work (#207). Projection-coupled E2Es (cards, Recently Added, Progress groups,
credit overlays, thumb cards, abstract excerpts) are deferred.

| Former browser scenario | Replacement fast coverage | Decision |
| --- | --- | --- |
| Repeated edits coalesce and returning to base cancels | Store `coalescing()` owns A→B→C / repeat-noop / edit-back-to-base; `editorCoalescing()` drives the real `prksSaveWorkMetadataFields` bib Save (DOM harness) so a never-sent pending field stays editable and subsequent Saves dirty against the displayed pending value | SPLIT |
| Lost response applies the edit once | Selftest `lostResponse()` proves transport loss leaves the original envelope pending and replays the exact same `op_id`/envelope; `tests/test_work_metadata_sync.py` `test_idempotency_normalization_and_reuse` plus the exact-replay matrix prove ledger idempotency / `OP_ID_REUSE`; `tests/test_server_api.py` `test_work_metadata_sync_http_lost_response_replay` covers `/api/sync/operations` commit-then-lost-response replay with DOI revision staying 1 | SPLIT → fast layers |

Module count after this slice: **55** Chromium scenarios (from 57). Conflict UI
buttons, offline reload+reconnect, diagnostics, hydration, and all projection
families remain in the browser module.

#### Work metadata rationalization — Slice B (abstract oversize refuse)

Non-projection bounds check. Independent of Work read-projection work (#207).
Progress / `abstract_excerpt` overlay E2Es remain deferred.

| Former browser scenario | Replacement fast coverage | Decision |
| --- | --- | --- |
| Oversize Abstract refused without touching durable state or the server | Selftest `installWorkMetadataEditorHarness` + `prksSaveWorkMetadataFields('bib')` with a spy store: paints the exact refusal status, retains the draft, never calls `saveWorkMetadataFields` / leaves the queue empty; plus limit helper + editor abort static + Python abstract limit | MOVE |

Module count after this slice alone on master: **56** Chromium scenarios (from 57).
Combined with Slice A (core protocol): **54**.

#### Folder / Playlist already-proven runtime fences

Small MOVE slice for contracts already owned by Node with no replacement gap:

| Former browser scenario | Replacement fast coverage | Decision |
| --- | --- | --- |
| Folder stale GET cannot repopulate after `markDomainChanged` | `tests/browser/run_offline_runtime_selftest.js` Folder list+entity stale block | MOVE |
| Folder cleanup failure stays domain-local | Same selftest Folder cleanup locality | MOVE |
| Work deletion / Work metadata mark helpers bump folders generation | Offline-runtime domain mark helpers + `run_work_metadata_sync_selftest.js` (vacuous Chromium helper-call E2Es) | MOVE |

KEEP (entrypoint UI, not helper-call):

| Browser scenario | Why Chromium | Fast layer still useful |
| --- | --- | --- |
| Work deletion via `.delete-work-btn` invalidates folders generation + `folders:index` | Real DELETE_WORK UI → ACK → `reconcileDeletedWork` folders fence | Node mark helpers do not replace the entrypoint |
| Work metadata editor `#save-work-identity-btn` reconciles playlist titles in place | Editor save path is distinct from playlist inline-rename | Node `embeddedReconciliation` owns ACK patch mechanics |

Playlist stale-hold E2Es (in-flight GET held across a durable membership/delete) stay in Chromium until the Node selftest covers that richer hold/release composition. Folder/Playlist cache render, route ownership, and disconnect control matrices remain KEEP.

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
