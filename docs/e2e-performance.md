# E2E performance investigation

This document tracks per-test E2E cost separately from suite-level sharding.


## Packaged smoke critical-defect coverage audit (#993)

The packaged smoke-plus-affected planner remains **inactive**; every automatic
packaged pull-request gate still runs the full six-shard suite. To get a
read-only, deterministic coverage-gap report on the **live, complete source**
unittest discovery:

```bash
python -m scripts.ci.plan_packaged_e2e --coverage-audit
```

This audit is distinct from the existing runtime comparator and reuses the
source E2E runner's full module inventory and the packaged planner's exact
**nine curated smoke IDs plus five packaged additions**. It does not select
new tests, change either workflow, or dispatch any browser job. The advisory
report includes an explicitly versioned set of critical obligations, naming
concrete browser test IDs (not substring-matched "equivalent" tests).
It compares which IDs are discovered in the source full selection, therefore
available to packaged full, and which are in the current dormant smoke floor.

Expected gaps with the existing 14-ID smoke floor include:

- **Offline install/disconnect** and durable offline field reconciliation
  (the cached-work reload and pending-operation reload cases *are* included).
- **Pending PDF annotation reconnect** and PDF highlight persistence after
  handshake/reload/delete (offline PDF reopen and annotation-tool availability
  *are* included).
- **Vue Processing route readiness** and interrupted Research Notes draft
  recovery (basic app-shell navigation and divider network-safety tests
  *are* included).

There are two **independent guard-only** obligations:
`Docker classic bundle and rollback build smoke` and
`Classic bundle SW upgrade and rollback (real Chromium)`. These are not
source full-suite E2E test IDs and are *not* credited as smoke coverage.
Their status must be checked as separate passing GitHub Actions jobs for a
specific run; this read-only inventory cannot establish that any such run
passed. The source-only unbundled↔bundled transition test is deliberately
excluded from default source and packaged E2E inventory.

Missing required IDs, new/renamed test IDs, missing declared modules,
undiscovered smoke IDs, unknown candidate IDs, malformed feature
selectors, or a feature map with no matches anywhere in discovery cause an
invalid/fail-closed audit. The report lists missing IDs, unmapped source IDs,
and individual unmatched feature selectors separately; those last two
categories are diagnostic (some E2E tests are legitimately ungrouped, and
some feature selectors may describe optional scenarios). Test-list ordering
does not affect results. Contract tests use synthetic complete/partial fixtures.
A real incomplete suite cannot be certified merely by an unchanged total:
the runner's module list and the explicit obligations are the conservative
floor, **not** a mathematical proof that all possible regressions were
exercised.

A valid inventory and even a completely populated critical-class checklist
do **not** demonstrate defect equivalence with six full shards. No activation,
skip, CI-runner reduction, or change to required PR gates is authorized.
A separate maintainer decision with real defect-class and timing evidence
would be needed.

## Read-only, same-commit full-gate cost breakdown (#992)

To compare a **successful, first-attempt** PR source-checkout six-shard run
and a successful packaged-distribution six-shard run on the **exact same
head SHA**, use the existing analyzer (provide a GitHub API token in
`GH_TOKEN` or `GITHUB_TOKEN`; do not put tokens in command arguments):

```bash
python scripts/ci/compare_e2e_gates.py \
  --repo Fooftilly/PRKS \
  --source-run SOURCE_RUN_ID \
  --packaged-run PACKAGED_RUN_ID
```

It fetches GitHub's run/job/step snapshots and verifies the successful
source planner's **full** mode from its job log. It rejects missing or
unverified job status/attempt fields, retries, missing or failed guard jobs,
incomplete six-shard matrices, wrong SHA, stale/incorrect workflow mode,
missing test-step timestamps and out-of-order/overlapping executed steps.
The source gate's explicitly non-blocking Mergify reporting step may fail;
its elapsed time remains in the non-test residual, and a failed browser-test
step or any other executed step still invalidates the comparison.
This report is read-only: it does **not** dispatch E2E runs, change required
checks or modify source/packaged test selection. No benchmark CI runners
are added.

For each of the six browser shards in each mode, the report shows:

- **Job span:** `started_at → completed_at`, in seconds. Summed across
  shards this is **runner-span minutes**, *not* GitHub's billed minutes.
  Concurrent shards intentionally overlap; never treat their sum as elapsed
  workflow wall time or a billing invoice.
- **Browser step:** `Run E2E selection shard` or
  `Run complete E2E shard on the packaged frontend`, measured from the
  successful step's own timestamps. All executed steps must be within the job
  and nonoverlapping. The other steps may legitimately be zero seconds in
  second-resolution GitHub timestamps.
- **Non-test residual:** job span minus browser step span; this combines
  checkout, dependency installation, setup, idle gaps, reporting, cleanup and
  teardown. These categories are **not** inferred or independently timed.
  Per shard, browser + residual exactly equals the job span.
- **Created-to-start lag:** `created_at → started_at`, only where GitHub
  supplies both timestamps consistently. It is a **queue/scheduling proxy**,
  which can include unmet `needs` dependencies, not verified runner-only
  queue time. Lag is **not** part of the job span. If any of six job
  creation timestamps is unavailable, the per-shard observation is marked
  unavailable and the six-shard lag sum is also unavailable.
- **Start-time spread:** difference between earliest and latest browser
  job starts; not total queue delay. **Browser-shard envelope:** earliest
  browser job start to latest browser job completion, inclusive of parallel
  execution; not full workflow wall time.
- **Workflow wall time:** only reported if the workflow-run response includes
  an explicit, consistent start and completion timestamp. GitHub's
  `updated_at` can change after completion and is **not** substituted for
  `completed_at`; unavailable is printed otherwise.

Workflow guards (Docker, SW, planner, pointer) are required to pass, but their
durations are not added to the six browser-shard totals. Differences can
reflect runner scheduling, shard imbalance and dependency-install variability
as well as application execution. One same-SHA pair is only a sample, and
even equal full-suite behavior does **not** demonstrate defect-class
equivalence for a reduced smoke policy. A future maintainer decision is
required before altering the active E2E gate.


The runner already has strong suite-level controls: individual-test timing history,
longest-processing-time-first sharding, per-worker Chromium reuse, targeted feature
selection, affected-test selection, last-failed runs, and a full-gate deadline. The
remaining target is the cost *inside one test*.

## Committed timing baseline

Fresh checkouts (especially cloud agents) have no machine-local timing history.
`tests/e2e/timing-baseline.json` now contains 847 calibrated exact test-ID
weights plus coarse `module.*` / `module.Class.*` fallback weights for new or
renamed tests. The exact values are medians from complete six-shard timing
artifacts, not measurements of the current run. At schedule time the runner merges:

1. committed exact/fallback weights;
2. machine-local exact timings from `.tests/e2e-timings.json`, which override them.

Invariants:

- local exact IDs always win over committed weights;
- the baseline is never copied into `.tests/e2e-timings.json`;
- the "slowest tests" report uses observed/local exact timings only — never committed metadata;
- E2E test selection, full six-shard coverage and the LPT assignment algorithm are unchanged.

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
  --include-exact --write
```

`--write` updates `tests/e2e/timing-baseline.json`. Review the result as an
ordinary JSON diff in the PR. With `--include-exact`, the generator combines
median-calibrated exact IDs with module/class fallbacks; without it, the
legacy prefix-only output remains available. Baseline data is scheduling
metadata, not an assertion of measured speedup.

The 2026-10-28 weights use complete `e2e-timings-*-of-6` artifacts from
[standard run #40769898987](https://github.com/Fooftilly/PRKS/actions/runs/40769898987)
and [class-locality run #40769904276](https://github.com/Fooftilly/PRKS/actions/runs/40769904276),
both green on the same commit. An offline held-out schedule simulation
indicated a better-balanced six-shard distribution, but the real performance
effect was measured by [manual full gate #40779596638](https://github.com/Fooftilly/PRKS/actions/runs/40779596638),
which passed all 847 unique tests with no failures, errors, or skips: longest
test step **606s → 589s** and workflow **673s → 651s** versus the previous
standard run, with aggregate six-shard job time **3587s → 3622s**. This is one
positive wall-clock sample, not a demonstrated reduction in runner-minutes.
No class-locality scheduling policy is enabled.

Committed writes fail closed unless the measurement export covers **every**
current E2E module and every discovered exact test ID (so a single shard or
affected-run file cannot wipe most bootstrap weights). Partial / experimental
generation needs `--allow-partial` or an alternate `--output` path.
Committed writes also require `--include-exact` even when the measurement
export is complete, to prevent accidental loss of calibrated exact weights.
Prefix-only output without the flag remains available for dry-run or alternate
`--output` experiments.

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

[The first one-off writer run #39104403107](https://github.com/Fooftilly/PRKS/actions/runs/39104403107)
completed all four Chromium executions (one excluded warm-up, three measured).
The complete test wall median was **16.327s**. Target/response write medians
were **1.972s / 2.015s**. The acknowledged-base subphases were
**1.968s / 2.011s**, while durable enqueue was **0.002s** for both.
Within base acquisition, target/response revision-state reads took
**0.915s / 0.972s**, and Argument record reads **1.046s / 0.932s**.

A reviewer identified a measurement-validity limitation in that first run:
the original global cache-read wrappers classified by kind alone. Follow-up
instrumentation matches the edited Argument id, measures cached reads only
while acquiring that acknowledged base, and requires **exactly one** completed
call for every expected stage; unexpected extra/missing calls or an impossible
parent/child relationship invalidate the profile.

[The corrected one-off run #39136910046](https://github.com/Fooftilly/PRKS/actions/runs/39136910046)
passed one excluded warm-up and all three independent Chromium trials.
Median whole-test wall time was **15.839s**. Per-rename medians (seconds):

| Inner step | Target | Response |
| --- | ---: | ---: |
| Complete offline write | 1.767 | 1.986 |
| Acknowledged base (inclusive) | 1.762 | 1.981 |
| Revision-state read | 0.876 | 0.988 |
| Argument record read | 0.888 | 0.957 |
| Durable field enqueue/refresh | 0.002 | 0.002 |

The corrected evidence points to two **sequential offline read-through
attempts** during acknowledged-base acquisition, not to IndexedDB enqueue.
A guarded follow-up implements opt-in `preferCacheWhenOffline` on entity
read-through, used **only** for Argument acknowledged-base revision and
record reads. When PRKS is confirmed offline and the cached entity is
eligible, this skips a network attempt already known to be failing.

The default remains server-first. The opt-in refuses to return a cached
entry after an entity/domain coherence invalidation, when a domain is
blocked, when reconnecting/online, or after a generation/connectivity
change while an IndexedDB read is pending. Connectivity uses a monotonic
transition generation, so even offline → online → offline while a cache read
is pending cannot expose its older snapshot. Missing cache entries fall
through to the existing read-through behavior: an unknown base is never
synthesized. Future server acknowledgments and revision conflict checks
remain mandatory.

[The initial same-runner Chromium A/B run #39140250984](https://github.com/Fooftilly/PRKS/actions/runs/39140250984)
passed baseline and candidate warm-ups plus three alternating measured trials
for each side, checking out the pre-change master SHA `76be455` as baseline.
Median test wall time changed **16.476s → 12.412s** (a **4.064s reduction,
24.7%**). Target write time changed **1.875s → 0.005s**, and response
write time **1.949s → 0.005s**. These figures apply to the profiled
offline Argument E2E test, not the full suite or production usage. A later
guard adds a monotonic connectivity generation for reconnect cycles; verify
the final candidate with another exact-head test gate and compare on the
same runner if available before merging.

The `write_base` parent **includes** its `write_revision_read` and
`write_record_read` children. The profile fails when the child timings
exceed the parent. `write_enqueue` is the existing durable writer including
its pending-name refresh. Treat the original `write` Python timer as
inclusive; browser timings can differ slightly because of IPC and
diagnostic overhead. Collect measurements on one runner before deciding
whether the dominant cost is safe to optimize. This is **not** authorization
to bypass revision validation, cache invalidation, or durable persistence.

Generic infrastructure and async wait telemetry may overlap all these
diagnostics; do not double-count them. Use the dominant measured inner step
to motivate a *separate* same-runner baseline/candidate experiment before
altering production logic. Never infer speedup from profiled-vs-normal CI
times alone.

### Graph → Concept offline cache read: same-runner A/B (#761)

[Baseline/candidate Chromium run 39144040444](https://github.com/Fooftilly/PRKS/actions/runs/39144040444)
completed one excluded warm-up **per ref** and three alternating measured
trials per ref on the same runner. All eight isolated browser tests passed,
retaining Graph selection, back-navigation, uncached destination, and
uncached Graph assertions. The baseline was merged master `570239c`;
the candidate opted in **only** Concept detail navigation to the
already-merged `preferCacheWhenOffline` guard. All numbers below are
medians of three independent measured runs, in seconds:

| Real-browser phase | Baseline | Candidate | Delta |
| --- | ---: | ---: | ---: |
| Full Graph navigation test | 27.260 | 22.928 | -4.332 |
| Concept roundtrip (inclusive) | 3.187 | 1.387 | -1.800 |
| Concept detail entry | 0.969 | 0.038 | -0.931 |
| Concept detail return | 1.117 | 0.068 | -1.049 |
| Position roundtrip (control) | 3.162 | 3.292 | +0.130 |
| Uncached Concept destination | 2.042 | 2.144 | +0.102 |
| Uncached Graph | 3.033 | 0.994 | -2.039 |

The 4.332-second (15.9%) whole-test improvement is **specific to this
profiled test**, not a full-suite speedup claim. No source-graph test or
availability assertion was removed. The position control and uncached
destination did not materially improve; the Concept entry/return changes
identify the targeted win. Other phase medians cannot be treated as additive
because the test overlaps nested timing intervals.

The Concept detail route retains its original `PRKS_CONCEPTS_DOMAIN`,
`prksIsConceptShape` validation, pending-creation/deletion handling, and
route generation checks. The opt-in reader only uses coherent, eligible
cached entities in *confirmed offline* state and falls back to the normal
server-first request for online/transition and cache misses. Its coherence
and connectivity fences are owned and tested by `offline-runtime.js`
(PR #760); this PR adds a route/wrapper contract test without changing that
shared runtime. The temporary A/B workflow was removed after the result.

### Graph Argument/Stance detail: confirmed-offline read-through A/B (#763)

[One-runner Chromium A/B #39158307945](https://github.com/Fooftilly/PRKS/actions/runs/39158307945)
tested the unchanged `ResearchGraphOfflineTests.test_record_graph_navigation_both_directions`
from merged master `173fdad` and exact candidate head `dc6a982`. Each
revision ran an excluded warm-up, then three **alternating**, passing,
isolated Chromium trials on the same runner. The selected phase keys were
required and missing samples or test failures aborted the experiment.

| Real-browser profile phase | Baseline median (s) | Candidate median (s) | Change (s) |
| --- | ---: | ---: | ---: |
| Whole test | 21.559 | 17.606 | **-3.953 (-18.3%)** |
| Argument roundtrip | 3.102 | 1.375 | -1.727 |
| Argument detail entry | 1.033 | 0.025 | -1.008 |
| Argument detail return | 0.926 | 0.075 | -0.851 |
| Stance roundtrip | 3.328 | 1.194 | -2.134 |
| Stance detail entry | 1.167 | 0.023 | -1.144 |
| Stance detail return | 1.106 | 0.069 | -1.037 |
| Concept roundtrip (control) | 1.210 | 1.272 | +0.062 |
| Position roundtrip (control) | 1.365 | 1.299 | -0.066 |
| Person roundtrip (control) | 3.479 | 3.321 | -0.158 |
| Uncached Concept detail | 2.240 | 2.359 | +0.119 |
| Uncached Graph | 1.199 | 1.166 | -0.033 |

The opt-in is limited to **Argument detail route** reads. That route
also renders Stances and preserves the same explicit `PRKS_ARGUMENTS_DOMAIN`,
`prksIsArgumentShape` guard, pending creates/deletions, and Work/Position
/Argument name overlays. The shared runtime's online server-first behavior,
coherence/invalidations and connectivity-generation fences were not
changed. The index remains server-first. Missing cached details still show
the original unavailable state and don't fabricate entities.

The percentages refer to this profiled test only, not the full E2E suite.
Nested stage durations overlap their parents and cannot be summed; no
assertions or browser isolation were relaxed. The temporary experiment
workflow was deleted after recording its results.

### Graph Person detail: coherent confirmed-offline cached read A/B (#764)

[Same-runner Chromium run #39161854813](https://github.com/Fooftilly/PRKS/actions/runs/39161854813)
compared exact merged master `6f9564a` with candidate `293ef4e`,
using one excluded warm-up for each version followed by three alternating
measured real-Chromium trials per version on one runner. All eight
executions passed the unchanged Graph navigation and unavailable-case
assertions. Results are medians of **three measured executions**, in seconds:

| Profiled phase | Baseline | Candidate | Difference |
| --- | ---: | ---: | ---: |
| Whole Graph test | 16.592 | 15.168 | **-1.424 (-8.6%)** |
| Person roundtrip | 3.158 | 2.048 | **-1.110** |
| Person detail entry | 1.050 | 0.031 | -1.019 |
| Person Graph navigation | 1.082 | 1.245 | +0.163 |
| Person detail return | 1.043 | 0.922 | -0.121 |
| Concept control | 1.352 | 1.160 | -0.192 |
| Position control | 1.150 | 1.366 | +0.217 |
| Argument control | 1.233 | 1.100 | -0.133 |
| Stance control | 1.200 | 1.150 | -0.050 |
| Uncached Concept | 2.148 | 2.013 | -0.135 |
| Uncached Graph | 1.048 | 1.133 | +0.085 |

Only the **Person detail route** opts into the already-merged
`preferCacheWhenOffline` reader. The shared runtime remains responsible
for entity and People-domain coherence, blocked/ineligible entries,
abort signals, and reconnect-generation fencing. Online/transitioning and
missing-cache reads stay server-first, as do People index reads so a device
continues warming its offline catalogue. The route retains
`prksIsPersonShape`, pending Person create/delete intent, editable
profile overlays and cache provenance; cached profiles still suppress
offline portrait/thumbnail requests.

This is a measured **single profiled Graph test** gain, not a whole-suite
claim. The 0.9-second Person return after the candidate is a remaining
measured cost, but its causes are unproven and require separate
instrumentation before further optimization. Nested phases are not
additive. The temporary one-off A/B workflow was deleted after
recording these results; no regular CI coverage was reduced.

### Graph detail return attribution (#767)

[Manual real-Chromium Graph profile #39165518995](https://github.com/Fooftilly/PRKS/actions/runs/39165518995)
ran against exact PR head `3fadc95` on one runner (one excluded
warm-up and **three passing** measured trials). The added opt-in
`graph_nav_stage_*_return_{click,hash,banner}` phases were present
for all five record visits in every measured trial. All Graph navigation
and offline-unavailable assertions were unchanged. Stage medians in seconds:

| Graph return stage | Concept | Position | Argument | Stance | Person |
| --- | ---: | ---: | ---: | ---: | ---: |
| Click | 0.047 | 0.046 | 0.048 | 0.044 | 0.046 |
| Hash confirmation | 0.002 | 0.002 | 0.002 | 0.002 | 0.002 |
| Offline banner DOM wait | 0.020 | 0.021 | 0.025 | 0.022 | **1.041** |
| Entire return | 0.069 | 0.070 | 0.070 | 0.068 | **1.081** |

The three individual **Person banner waits** were **1.041, 1.132,
0.026s**, while Person return totals were 1.081, 1.182, 0.074s.
The test wall median was **16.065s**. For context, a separate manual
[master profile #39165176532](https://github.com/Fooftilly/PRKS/actions/runs/39165176532)
on `059e56e` passed all three trials and found Person return timings
0.062, 0.086, 0.895s (median 0.086s). Those independent runs are
**not** a controlled cross-commit A/B; do not infer a regression or
suite-wide speedup from their median difference.

The measured intermittent delay is **after hash change and before the
focused Person route's offline-provenance banner exists in the DOM**,
not in the click or hash confirmation. The banner is appended *after*
the route resolves durable lifecycle, cached Person detail, effective
profile/group overlays, and pending Work/relationship/name metadata
hydration. These possible contributing operations have **not**
been individually timed yet; this measurement does not prove which
one is slow. The banner wait checks DOM attachment, **not browser paint**.
Next instrument those awaited Person route stages independently with
strict route ownership and opt-in profiling before proposing production
optimizations; keep all revision/cache coherence and pending-overlay
safeguards. The split timers are nested into the existing inclusive
return timers and must not be summed with their parents.

### Graph Person return: awaited-route diagnosis (#768)

[Manual source-Chromium profile #39199744078](https://github.com/Fooftilly/PRKS/actions/runs/39199744078)
on PR #768 head `fdf071c` passed one excluded warm-up and three
serial measured runs. Every run produced the eight required nested
Person-route phases, both inclusive sum/max metrics, and the chronological
per-call trace. All normal Graph/offline and unavailable-state assertions
passed. Results below are **browser-side `performance.now()`** timings from
the same runner, in **milliseconds**, not Playwright IPC durations:

| Awaited Person return stage | Trial 1 | Trial 2 | Trial 3 | Median |
| --- | ---: | ---: | ---: | ---: |
| Full route commit (inclusive) | 958.0 | 881.0 | 1085.9 | **958.0** |
| `prksOfflineDetailFetch` (inclusive) | 928.1 | 851.7 | 1054.3 | **928.1** |
| Durable queue reads (sum; **two calls**) | 9.6 | 10.8 | 11.8 | 10.8 |
| Pending metadata hydration | 2.3 | 2.4 | 2.5 | 2.4 |
| Person renderer | 6.6 | 5.5 | 5.8 | 5.8 |
| Banner insertion (DOM operation) | 2.1 | 2.1 | 2.3 | 2.1 |

The raw trace also shows one route commit, one detail fetch, one Person
effective-profile projection and one metadata hydration per measured trial,
with **no recorded aborts or non-abort exceptions**. No overlapping
Person route commits were observed in these samples. Person Graph return
wait medians were 1.009s for the inclusive return and 0.951s for the
offline banner wait; the single-test wall median was 16.443s.

**Finding:** On these three measured returns, the awaited
`prksOfflineDetailFetch` **accounts for nearly all of the delay**.
The legacy helper delegates to `prksOfflineReadEntity`, which can use
an explicitly opted-in coherent IndexedDB snapshot when confirmed offline,
or can fall back to its usual network/error-classification read-through
path when its safety guards disallow that snapshot. **This run does not
distinguish those internal branches or establish why one took 0.85–1.05s.**
The next diagnostic should capture the reader's initial connectivity
state, returned source (`cache`/`server`/`unavailable`), guarded
cache eligibility versus read-through fallback, and relevant actual
network attempts. Instrument only opt-in tests or the runtime's existing
optional diagnostics, without changing connectivity/coherence/abort
fences, cache publication, or ordinary E2E coverage. Only consider
a production change after branch attribution and controlled same-runner
A/B correctness evidence.

This is **attribution**, not a measured production speedup; the child
durable read and metadata timings are inclusive and must not be summed
with their enclosing parent intervals.

### Person offline read branch diagnosis (#774)

[Manual real-Chromium run #39239438391](https://github.com/Fooftilly/PRKS/actions/runs/39239438391)
on PR #774 implementation commit `ed2cbc6` completed successfully, with
one unmeasured warm-up and three passing serial measured trials. The opt-in
probe observed the offline reader's entry/exit state, result provenance and
browser-context Person GET events during the Graph → Person return:

| Diagnostic | Trial 1 | Trial 2 | Trial 3 |
| --- | ---: | ---: | ---: |
| `prksOfflineReadEntity('person', …)` inclusive ms | 1159.9 | 1047.8 | 1045.1 |
| Total route commit ms | 1185.4 | 1073.1 | 1071.5 |
| Reader entry state | reconnecting | reconnecting | reconnecting |
| Reader exit state | offline | offline | offline |
| Returned source | cache | cache | cache |
| Browser-observed Person-detail GET events | 3 | 3 | 3 |

Reader median was **1047.8 ms** and total route-commit median **1073.1 ms**.
Every measured call opted in to cache-first reads, began with an unblocked
People domain and an un-aborted signal, and returned `source: 'cache'`.
The profile did not record concurrent readers or aborted route commits.

**Branch attribution:** `frontend/js/offline-runtime.js` permits the
cache-first fast path only when its state is `STATE_OFFLINE`. When the
runtime is `STATE_RECONNECTING`, that condition fails before IndexedDB is
queried, and `readThroughEntity` attempts the ordinary network request;
on a network failure it can return a cached row. A reconnect probe calls
`setState(STATE_RECONNECTING)` while checking server reachability.
Consequently, the measured `reconnecting → offline` state transitions,
three observed Person GET request events per trial and cached fallback
outcomes are **consistent with the slow network-error/fallback branch**.
The fast cache-first branch cannot run at entry while state is
`reconnecting`. There is a second, independently inspectable contributor:
`frontend/js/request-coordinator.js` defaults ordinary foreground GETs
to **one initial request plus two transport retries**, with jittered
base-backoff delays of **250 ms and 750 ms**. Its `performReadNetwork`
retries non-abort transport failures before reporting unreachability.
Thus three browser-observed Person GET events and about one second per
offline-reader call strongly match the default retry path. The exact
attempt sequence is **not independently timed within the coordinator** by
this profile, and the request count is a window-wide observation rather
than a per-reader transport log. A future limited experiment could use
the existing `requestPolicy.retry = false` contract for a carefully
scoped opt-in Person detail read under `reconnecting`, retaining its
server-first attempt and cached fallback; however a one-attempt policy
would be **less tolerant of transient failures** and must not be
presented as a proven-safe production optimization without separate
tests and a same-runner performance comparison.

**Safety:** Do not turn `reconnecting` into `offline` or enable cache-first
reads in that state without a separate correctness design and test.
A reconnect in progress may succeed, so returning an older cached snapshot
could defeat the existing reconnect, entity-generation or domain-fencing
semantics; `navigator.onLine` is not authoritative. Follow-up work should
investigate source-specific request policy or an opt-in/controlled experiment
that preserves server-first behavior during uncertain reachability, and use
same-runner A/B plus genuine offline/reconnect race tests before proposing
a production change. This is diagnostic attribution, **not** an achieved
application speedup.

### Cached Person reconnect retry A/B (#776)

The manual [source-Chromium workflow run #39270914591](https://github.com/Fooftilly/PRKS/actions/runs/39270914591)
on commit `e0fd8c9` **passed**, including one excluded warm-up and
three passing serial measured trials. Each trial used a newly isolated
browser context and test server, warmed the Person detail in the normal
online route, established actual Playwright offline transport, then made
four direct runtime Person detail reads in A-B-B-A order. Each arm began
with the runtime in `reconnecting` after an actual offline event,
attempted the server, and returned the correct cached Person record.

| Request policy | GET attempts per read | Trial median 1 | Trial median 2 | Trial median 3 | Median of trial medians |
| --- | ---: | ---: | ---: | ---: | ---: |
| Default coordinator retries | 3 | 0.962s | 1.011s | 1.030s | **1.011s** |
| Existing `requestPolicy.retry=false` | 1 | 0.004s | 0.005s | 0.005s | **0.005s** |

The test observes and asserts the network GET count and cached
provenance of *each* read: **3 vs 1 GET events** in all four A/B arms.
No production read-path or retry policy was changed. The one-attempt
case avoided approximately 1.006 seconds per read in these controlled
fully disconnected conditions. Because this is a tiny, source-runner,
opt-in study, do not describe the difference as a statistically
generalizable production speedup, a UX improvement in all network states,
or a reason to treat `reconnecting` as `offline`.

**Correctness trade-off:** `retry=false` is an existing coordinator
contract but will abandon an initially failing server request instead
of retrying through transient trouble. Before adopting it for a
production Person route, test a server that fails once and succeeds
on retry, as well as offline/online/offline races, response provenance,
abort propagation, and People domain/record invalidation across pending
reads. Preserve server-first behavior while reachability is uncertain
and never bypass offline cache-coherence fences. This is **A/B evidence
for a candidate policy**, not a proposed or deployed production fix.

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

### Position / Playlist direct-API pilot (six MOVE contracts, #740)

The six E2Es listed below invoked the classic public functions directly via
`page.evaluate`, then inspected durable pending operations or canonical state.
They never clicked the Position form, Playlist editor, or drag target. Four
were queue-only; two additionally reconnected and checked that a never-sent
create/delete pair had not produced a database row. Their fast replacements now
wake the **real sync runtime** with an observed transport and prove that no
operation is transmitted after the wrapper folds the pair. Other Position and
Playlist E2Es retain real browser offline/reload/projection and server handoff
coverage.

| Former E2E (removed) | Fast replacement (production code under test) |
| --- | --- |
| `DurablePositionTests.test_an_edit_taken_back_before_it_is_sent_leaves_no_intent` | `run_position_sync_selftest.js`: real `api.js updatePosition` → `prksAcknowledgedPositionBase` → dirty fields → durable writer. The known revision 3 stays attached to A→B→A, which must leave zero `SET_POSITION_FIELD` rows. |
| `DurablePositionTests.test_deleting_a_position_created_offline_folds_the_whole_case_away` | Same Node selftest: real `api.js createPosition` / `deletePosition`, permanent distributed ID, queued create and empty final operation list. |
| `DurablePlaylistTests.test_a_rename_taken_back_before_it_is_sent_leaves_no_intent` | `run_playlist_sync_selftest.js`: real `components/playlists.js updatePlaylist`, a cached acknowledged title at revision 3, A→B→A, zero `SET_PLAYLIST_FIELD` operations. |
| `DurablePlaylistTests.test_a_second_drag_replaces_the_first_rather_than_racing_it` | Same Node selftest: real `reorderPlaylist` over acknowledged order at revision 2; first replacement is one ordered operation, return to original order has zero queued reorders. The browser drag event path is separately exercised by retained scenarios. |
| `DurablePlaylistTests.test_putting_a_video_back_where_it_started_leaves_no_intent` | Same Node selftest: real `addWorkToPlaylist` / `prksSetWorkPlaylist` using a cached Work playlist-state at revision 4; moving away and back leaves zero membership operations. |
| `DurablePlaylistTests.test_deleting_a_playlist_created_offline_folds_the_whole_case_away` | Same Node selftest: real `createPlaylist` / `deletePlaylistCanonical`; unsent create folds to zero queue rows. |

Each Node selftest has a **mutation negative-control**: temporarily replace
the real durable field writer with a no-op during the cancel call, confirm the
zero-pending assertion *fails*, restore the writer, and prove the genuine wrapper
cancels the pending operation. The folded-create tests also verify that reconnect wakes the sync runtime
without sending any request. The existing local-store Node tests still own
coalescing, dependency, and fold behavior; Python backend sync tests own
ordinary server acceptance and idempotency. The pre-existing
`tests.test_frontend_position_sync` and `tests.test_frontend_playlist_sync`
Python tests invoke these Node scripts in the normal unit/contract gate.

**Retained in Chromium**: Position/Playlist creation and editing offline
across real reload, user-visible list/detail/Work projections, separate
revision conflict UI, deletion tombstone/refusal, genuine playlist drag
persistence and reconnect, ordering/dependency races, and actual API/server
handoff. Do not remove these merely because a Node store test has a similar
name.

The October 28 calibrated timing-baseline weights for the six removed IDs
sum to about **31.2 seconds** per complete E2E inventory, about **62.4
aggregate test-execution seconds** across source and packaged gates. These
are *historical scheduling weights*, not measured runner-minute savings or a
promise of shorter CI wall time. Both gates must still run their entire
remaining six-shard inventories on the exact PR head. No change to selection,
runner deadlines, retries, browser isolation, or packaged policy.

### Offline read-model validator matrix migration (four MOVE contracts, #740)

The larger offline suites contain a distinct category of deterministic tests
that booted Chromium and a seeded server solely to call **pure payload shape
predicates** with `page.evaluate`. These did not test cache publication,
offline render, service-worker behavior, or real IndexedDB persistence:

| Removed Chromium case | Fast replacement |
| --- | --- |
| `OfflinePositionTests.test_position_shape_validators_reject_unusable_rows` | Position index/detail shape matrices, 18 asserted outcomes |
| `OfflineArgumentTests.test_argument_shape_validators_reject_unusable_rows` | Argument/Stance index/detail shape matrices, 45 asserted outcomes |
| `OfflinePeopleTests.test_people_validator_matches_renderer_scalar_contract` | People index/detail optional-string and Work year/date scalar matrix, 8 contract-wide Boolean assertions |
| `PlaylistsOfflineTests.test_predicates_protect_every_rendered_field` | Playlist index/detail scalar, embedded Work, position/count matrix, 31 asserted outcomes |

The replacement `tests/browser/run_offline_read_model_shape_selftest.js`
extracts and executes the **unchanged production validator definitions** and
People string-field declaration from `frontend/js/app.js`, then executes
the original table-driven JavaScript inputs and expected outcomes. Missing
or renamed functions, ambiguous declarations, malformed payloads, and
invalid expected results fail the test. A separate
`tests/test_frontend_offline_read_model_shapes.py` invokes the Node suite
in the ordinary Python Unit/API/contract test gate. **102 individual
expectations** remain, with no reimplemented test-side predicates or
new production exports.

Retained Chromium coverage continues to prove that **malformed authoritative
HTTP responses do not poison a good cache**, malformed persisted rows are
discarded, genuine cached rows render offline, legitimate empty indexes
remain distinct from unavailability, and real page navigation, disconnected
operation, cache fencing and concurrency behave correctly. Existing
`tests/test_frontend_offline_runtime.py` also pins the app's
`validate: prksIs...` read-through wiring.

The committed 2026-10-28 weights for the four removed browser IDs sum to
**7.341s per full E2E inventory** (14.682s combined across source and
packaged inventories). These are historical *test scheduling weights*, not
measured runner or wall-time savings. This is intentionally a small first
slice of the 146-test `test_offline.py` and 34-test
`test_playlists_offline.py` inventories: the larger cache/invalidation
matrix is KEEP-heavy and must be audited by complete invariant, not by
name or execution time. No CI or production changes.

### Argument ACK cache-coherence rationalization (two MOVE contracts, #740)

Two short E2Es that called the public Argument API directly and asserted
only cache invalidation/generation outcomes had stronger surviving coverage:

| Former E2E (removed) | Preserved browser coverage | Fast contract |
| --- | --- | --- |
| `OfflinePositionTests.test_argument_source_change_does_not_invalidate_positions` | `OfflineArgumentCoherenceTests.test_argument_sources_invalidate_arguments_but_not_positions` already proves actual successful source synchronization fences Arguments but preserves Positions and Concepts | Real `prksOfflineReconcileArgumentSources` with unchanged and changed ACK responses; an unchanged ACK retains every cache, changed ACK sweeps Arguments and Graph but leaves Position/Concept/People snapshots and lists eligible |
| `OfflineArgumentCoherenceTests.test_argument_targets_invalidate_arguments_and_positions_only` | `OfflinePositionTests.test_argument_target_change_invalidates_the_position_domain` verifies server ACK, updated Position verdict, authoritative refetch and real offline reload; the Argument-source coherence E2E retains Argument cache-fencing integration | Real `prksOfflineReconcileArgumentTargets` ACK clears Arguments/Positions/Graph but preserves Concepts and People |

The new `tests/browser/run_argument_ack_coherence_selftest.js` calls the
**production `frontend/js/offline-runtime.js` singleton's exported
`prksOfflineReconcileArgumentSources` and `prksOfflineReconcileArgumentTargets`**,
using its real per-domain sweep and entity-patch logic with a disposable
store. It checks the source/target distinction, clean unchanged ACK,
state-revision advancement, cache/list invalidation and unrelated domain
isolation against exact expected footprints. This is not a hand-rebuilt
cache-invalidation policy. The Python wrapper
`tests/test_frontend_argument_ack_coherence.py` runs it in the existing
unit/API/contract gate.

**Not moved:** actual server ACK and conflict/retry behavior, real offline
Position reopens and verdict rendering, IndexedDB retention across reload,
canonical browser cache and Graph integration. Those remain E2E.

Historical calibrated timing weights: 2.451 + 3.575 =
**6.026s of summed test execution** per full source/package E2E inventory,
12.052s across two inventories; this is *not* measured CI wall-time
or billed-minute savings. No runtime or CI selection change.

### Folder ACK cache-footprint rationalization (three MOVE contracts, #740)

Three `FoldersOfflineTests` E2Es launched Chromium and a seeded
PostgreSQL server, called canonical Folder functions using `page.evaluate`,
and inspected only cached entities/lists and domain generations (no UI
interaction, disconnect, or IndexedDB lifecycle):

| Removed E2E | Fast production-owned assertion |
| --- | --- |
| `test_folder_create_invalidates_only_folders` | `reconcileCreatedFolder` inserts the canonical row into the existing cached Folder index and increments only the Folder generation; existing cached Works and six other read-model domains survive |
| `test_description_only_update_keeps_member_work_snapshots` | `reconcileFolderField` patches description into Folder index and detail while keeping both member and unrelated Work snapshots, preserving other domains |
| `test_reparent_invalidates_folders_but_no_work_snapshots` | `reconcileFolderField` updates the parent pointer in the Folder index, invalidates the now-stale Folder detail containing a parent object, and keeps existing Work snapshots and unrelated domains |

The existing `tests/browser/run_offline_runtime_selftest.js` now runs
`verifyFolderAckCacheFootprint`, **directly invoking the shipped
`createPrksOfflineRuntime` reconcilers with a disposable store**.
Its 101 exact assertions include each domain-generation change, cached list
retention, Work snapshot preservation, and a rename-ACK negative comparator
that evicts *only* the server-listed member Work. This extends the
existing fast Node test already exercised by
`tests/test_frontend_offline_runtime.py`; no new production test hooks.

**KEEP in Chromium:** `test_folder_rename_invalidates_exactly_its_member_work_snapshots`
still proves actual end-to-end server ACK and selective Work eviction;
Folder creation dialogs, hierarchy rendering, offline page reload,
Folder/Work route changes, cache corruption and refetch, and Tag coherence
remain under browser coverage. The pure ACK replacement is not offered as
proof of any of those browser-specific boundaries.

The pre-existing 2026-10-28 E2E baseline weights for these three tests
sum to **14.384 historical test-execution seconds per full inventory**,
28.768s over source and packaged inventories. This is not an observed
critical-path wall-clock improvement. No production, retry, E2E selection,
shard, or packaged-test policy changes.

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
