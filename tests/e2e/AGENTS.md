# PRKS browser E2E agent instructions

These rules apply to browser E2E work in addition to the repository-root
`AGENTS.md`. UX Interaction Tour policy lives in `tests/ux_tour/AGENTS.md`.
They are intentionally scoped here so ordinary implementation workers do not
carry the full Playwright policy in every task.

## E2E TESTING POLICY

PRKS E2E is Python unittest + Playwright Chromium via `tests/e2e/run.py`
(not an npm Playwright project). Tiers:

| Tier | Command | Meaning |
| --- | --- | --- |
| Targeted | `python tests/e2e/run.py --jobs 1 <test id>` | One test/class/module |
| Feature | `python tests/e2e/run.py --feature graph` | One domain group |
| Smoke | `python tests/e2e/run.py --smoke` | Curated essential suite (~9 tests) |
| Affected | `python tests/e2e/run.py --affected` | Git diff → feature groups |
| Last-failed | `python tests/e2e/run.py --last-failed` | unresolved failures from prior runs |
| Dev | `python tests/e2e/run.py --dev --feature tabs` | Fail-fast + no pointer-capture |
| Full | `python tests/e2e/run.py --jobs 4` | Complete regression gate (runner hard-limits at 1200s; `timeout 1200 …` still fine) |
| Full (CI shard) | `python tests/e2e/run.py --jobs 1 --shard INDEX/TOTAL` | One external slice of the full gate (GitHub Actions matrix; TOTAL defaults to 4) |

Convenience wrapper: `scripts/e2e smoke|feature|affected|last-failed|dev|full`.
Catalog: `python tests/e2e/run.py --list-features`. On-demand counts/timing:
`scripts/e2e inventory` (or `--inventory` / `--inventory-json`). Mapping lives in
`tests/e2e/policy.py` (declarative, edit there).

Reports always name the tier. A PASS on targeted/feature/smoke/affected/dev
is **not** equivalent to a full E2E gate. Say which tier ran.

### Coverage-layer rule: KEEP / SPLIT / MOVE

Before adding or retaining a browser E2E, classify the contract:

- **KEEP** — the assertion inherently needs a real browser/app lifecycle: DOM focus,
  layout/scroll, navigation/history, service worker behavior, actual IndexedDB across
  page teardown, browser offline mode, renderer integration, or a user-visible flow
  that can only be proven end to end.
- **SPLIT** — keep one thin browser boundary, but move protocol/state-machine,
  validation, coalescing, idempotency, revision arithmetic, serialization, retry,
  projection, and other deterministic branches to Node/API/Python tests.
- **MOVE** — when the entire invariant is already provable below the browser layer,
  replace it with fast coverage before deleting the E2E.

Do not keep Chromium coverage merely because it already exists. Conversely, never
delete an E2E just because a similarly named unit test exists: identify the exact
contract and prove the replacement covers it. For durable sync families, prefer
testing local queue mechanics in Node and server semantics in Python; retain E2E for
the real UI/offline/service-worker boundary.

The first rationalized family is Work Tags. Transactional coalescing across a fresh
store instance is owned by `tests/browser/run_local_store_selftest.js`, with one thin
browser assertion that a remounted `work-tag-editor` still routes post-reload opposite
clicks through coalescing in both UI directions — chip remove and search/picker
re-add (`test_post_reload_opposite_edit_cancels_pending`). Lost-response retry
identity is owned by `tests/browser/run_work_tag_sync_selftest.js` plus backend
op-id replay/idempotency coverage. The Work-Tag E2Es therefore keep the user-visible
offline/reload/reconnect, conflict-resolution UI, degraded catalog, cache-clear,
durable-queue wiring, and Tag lifecycle flows rather than re-testing pure
state-machine branches in Chromium.

Per-family KEEP / SPLIT / MOVE maps and retained browser boundaries are recorded
under `docs/e2e-performance.md` (Work-Tag pilot, Work-People, …). Hard constraints
for that wave: no retries to hide flakes; no fake E2E backend; no weakened
assertions for speed; no shared browser/server state without proven isolation.

### During implementation

1. Run the relevant unit/self-tests first.
2. Prefer fast targeted tests (unit, Node selftests, static contracts, API).
   Run browser E2E for the changed feature (`--feature` or a test id) **only**
   when the behavior can only be verified in a browser.
3. Prefer `--affected` when the working-tree diff is the right scope and browser
   verification is needed.
4. Use `--dev` (fail-fast, no pointer-capture) while debugging. Retries are
   already off; do not add retries that hide failures.
5. Stop quickly on failures (`--fail-fast` / `--dev`).
6. If an E2E test fails, reproduce that specific failure before any broader run.
7. After fixing, rerun the failed test (`--last-failed` or the test id).
8. After a coherent vertical slice (or after browser-required debugging), rerun
   the affected feature group if E2E was in scope.
9. Then run `--smoke` if the change touches shell/navigation/shared paths.
10. Run the complete E2E suite **only** when the implementation is otherwise
    ready for final validation.

Agents must not perform more than **two consecutive full E2E executions**
without narrowing a failure to targeted tests and investigating it.

### When the full E2E suite fails

* Do **not** immediately rerun the entire suite.
* Narrow to the failing test/spec (`--last-failed` or the printed test id).
* Reproduce it individually (`--jobs 1`).
* Diagnose and fix it.
* Rerun the failing test until it passes reliably.
* Run its feature group.
* Run smoke if relevant.
* Only then rerun the full gate **once**.

### `--affected` defaults

Compares the working tree (+ relevant untracked production/E2E paths) to
`HEAD`, or to `--base <ref>` when given. Prints every changed path, the rule
that matched, and the selected features. Unmapped production files fall back
to **smoke** (not the full suite). Shared core (`app.js`, `server.py`,
`db_manager.py`, …) selects smoke + shell/tabs/offline/sync. Docs/unit-only
paths select nothing. Add new production areas by editing `AFFECTED_RULES`
and `FEATURES` in `tests/e2e/policy.py`. Classify a new E2E module by adding
its module/class prefix to the right feature's `selectors`.

`--affected` fails closed: when Git change discovery itself fails (invalid
`--base` ref, unusable checkout, missing/failing `git`, or untracked-file
discovery failure) the runner exits nonzero with a diagnostic instead of
reporting zero affected tests. A genuine empty diff remains a successful no-op.
`--base` must name a single revision: a leading `-` is rejected, the base is
verified to resolve to one commit (a range like `a..b` compares commit to
commit and would drop the working tree), and the diff terminates revision
parsing with `--`. An option-like, range or path-like base therefore fails
closed instead of quietly answering a different question.

Benchmark/profile runs never train history. `--profile` / `--no-seed-cache`
**and** their environment equivalents (`PRKS_E2E_PROFILE`,
`PRKS_E2E_SEED_CACHE=0`) suppress `.tests/e2e-timings.json` and last-failed
persistence; `policy.benchmark_modes()` is the single decision.

Optional stress: `tests/e2e/stress_cache_offline.py` — never part of normal
iteration or the full gate.

## E2E test workflow

`tests/e2e/run.py` is the only entry point for the real-Chromium suite.

```
python tests/e2e/run.py --smoke --jobs 2
python tests/e2e/run.py --feature graph --jobs 2 --no-pointer-capture
python tests/e2e/run.py --affected
python tests/e2e/run.py --affected --base origin/master
python tests/e2e/run.py --last-failed
python tests/e2e/run.py --dev --feature tabs
python tests/e2e/run.py --jobs 4                # full regression gate
python tests/e2e/run.py --jobs 1 tests.e2e.test_playlists_offline.OfflinePlaylistTests.test_x
python tests/e2e/run.py --jobs 4 --no-pointer-capture tests.e2e.test_playlists_offline
scripts/e2e smoke
scripts/e2e feature graph --jobs 2
scripts/e2e full --jobs 4
```

`--jobs` wins over `PRKS_E2E_JOBS`; the default is 1 so debugging is never
accidentally parallel. There is no Playwright retry loop in this runner;
failed tests stay failed.

The E2E performance milestone is closed. Console suppression ignores only
`blob:` resource failures containing `ERR_FILE_NOT_FOUND`; other blob errors
remain failures. Do not broaden that teardown exception.

### When to run what

**During normal implementation iterations, do not run browser E2E tests at all
-- neither the full suite nor an entire module** -- unless the behavior being
debugged can only be verified at browser level. Prefer unit tests, Node
selftests, static contracts, API tests and other fast targeted tests. Almost
every sync, projection, validator and coherence question is answerable that
way, in seconds rather than minutes.

**After a complete user-visible vertical slice, or once the milestone's
implementation is finished, run the relevant targeted E2E feature/module once.**
That is where a browser earns its cost: real navigation, real IndexedDB, real
service worker. Always through the sharding runner, never raw unittest:

```
python tests/e2e/run.py --jobs 4 --no-pointer-capture --feature sync
python tests/e2e/run.py --jobs 4 --no-pointer-capture tests.e2e.test_x
```

`python -m unittest tests.e2e.<module>` runs SERIALLY -- a browser launch and a
PRKS server per test, one at a time -- which turns a two-minute module into
half an hour. It is for isolating one already-identified failure, not for
feature completion. `--no-pointer-capture` skips the once-per-run pointer
checks, which belong to the full gate rather than to a module.

**After all milestone work and targeted verification are complete, run the full
parallel E2E suite once:**

```
python run_tests.py
python run_tests.py --e2e
# equivalent: scripts/e2e full   or   python tests/e2e/run.py --jobs 4
# optional shell wrapper still fine: timeout 1200 python tests/e2e/run.py --jobs 4
```

The full run already contains every module, so do not run a module separately
immediately before it.

**Budget the full run and treat an overrun as a bug, not as patience.**

| | |
| --- | --- |
| Expected `--jobs 4` runtime | ~7-10 min |
| Investigate | > 15 min |
| Hard outer timeout | ~20 min (runner `PRKS_E2E_FULL_TIMEOUT` default 1200; `timeout 1200` optional) |

A generous outer timeout does not make a run succeed; it hides a hang. If the
suite overruns, do NOT wait it out and do NOT restart it: read the log, see
which shards reported and which did not, check for orphaned Chromium or PRKS
processes, and reproduce the suspect test alone. A worker that never reports
while its peers finish in eight minutes is a stuck test or a stuck teardown,
and the full suite is the worst possible instrument for finding it. Raise the
limit only when the suite genuinely grows, and re-measure rather than guess.

**If an E2E test fails, stop running the full suite.** Reproduce that one test:

```
python tests/e2e/run.py --jobs 1 tests.e2e.test_x.Class.test_name
python tests/e2e/run.py --last-failed
```

Fix it, re-run its feature group, and only then spend the full parallel gate once
more. Never rerun the full suite to find out whether a fix worked.

A full run costs several minutes of wall clock and saturates the machine; a
single module still costs a browser launch and a PRKS server per test. The
optimization is faster execution, not less verification: the full suite is
still mandatory before declaring a milestone complete, and a failure it reports
is never dismissed without being reproduced and classified.

Worker count guidance (do not hard-code suite size here — discovery grows):
on a 12-core development machine, `--jobs 4` was the best measured full-gate
setting in an earlier benchmark pass (serial much slower; 2–3 better than
serial; 4 recommended). Re-measure when the suite or machine changes rather
than trusting a fixed case count. Discover current modules/cases via
`tests/e2e/run.py` / unittest discovery with `PRKS_E2E=1`. **4 remains the
recommended default gate** until a fresh benchmark says otherwise. More
workers are not automatically better -- Chromium plus a PRKS server per test
is memory- and CPU-hungry, and per-worker overhead was already substantial at
4 -- so re-benchmark rather than raising it on a different machine. Drop to
`--jobs 3` if a machine shows contention-driven flakiness.

Debugging. `--jobs 1` is the mode for reproducing a flake, reading one clean
traceback, or checking that the suite still passes serially. It does not need to
run after every milestone once the parallel suite is reliable. A test that fails
only under `--jobs > 1` is a real defect — investigate port collisions, shared
filesystem paths, hardcoded ports, singleton temp files, CPU-sensitive timing
assumptions, or unowned browser/server processes. Never paper over it with a
retry: there is deliberately no blanket retry mechanism, because retrying
conceals races.

Sharding. Workers receive individual test IDs, not whole modules, balanced
longest-processing-time-first from machine-local `.tests/e2e-timings.json`
(gitignored runner metadata, written after each run) merged over the committed
coarse bootstrap in `tests/e2e/timing-baseline.json`. Local exact IDs always
override baseline `prefix.*` weights. The baseline is scheduling metadata only:
it is never copied into `.tests/` and never appears in the human "slowest tests"
report. That history is an optimisation hint and never required state: absent,
corrupt, or full of renamed tests, the runner still works and unknown tests
take a default estimate. Refresh the committed baseline from representative
CI/full-gate measurement exports with
`scripts/e2e update-timing-baseline --from PATH [--write]` (see
`docs/e2e-performance.md`); committed writes fail closed unless the export covers
every discovered E2E module/exact ID (`--allow-partial` / alternate `--output`
for experiments). Do not treat a laptop's `.tests/e2e-timings.json` as
authoritative. Each shard keeps one module's tests contiguous, because these
modules launch Chromium in `setUpModule` and unittest re-runs a module fixture
whenever the module changes. The scheduling and aggregation logic lives in
`tests/e2e/sharding.py` as pure functions covered by `tests/test_e2e_sharding.py`
— no Chromium needed.

External CI sharding. `--shard INDEX/TOTAL` (1-based) selects one bucket from
the same LPT partition used by `--jobs TOTAL`. The authoritative GitHub Actions
full E2E gate (`.github/workflows/e2e-gate.yml`) prefers about four runners
each with `--jobs 1` over one runner with four local Chromium stacks
(`FULL_GATE_EXTERNAL_SHARDS` in `tests/e2e/policy.py`). Pointer capture runs
once after every matrix shard passes (dedicated CI job), not once per shard.
Docs/unit/ignored-only diffs skip the matrix via
`python tests/e2e/run.py --ci-plan --base <ref>` (same noop policy as
`--affected`). An unresolvable comparison base (force-push `before` SHA)
fails closed to **run** the full gate — never a plan exit 2. Rename/copy
discovery keeps both path images so a production→docs move cannot look
docs-only.

The parent fails the gate if any worker fails, errors, crashes, or exits without
writing a result document; a vanished worker is never read as a pass. Pointer
capture (`tests/browser/pointer_capture.py`) runs exactly once, in the parent,
after every shard has passed — never once per worker.

## E2E isolation invariant

Parallel execution relies on each E2E test keeping independent PRKS
storage/database and browser-context state: a fresh `TemporaryDirectory`, a
fresh database, a fresh port, and a fresh browser context. Do not introduce
fixed ports, shared writable temp files, shared test databases, a shared browser
context, or cross-worker mutable globals.

Ports come from `find_free_port()`, which binds and closes before the server
subprocess binds for real. Each worker gets a disjoint port window below the
Linux ephemeral range via `PRKS_E2E_PORT_BASE`/`PRKS_E2E_PORT_SPAN`, and
`AppServer.start()` retries a bounded number of times on a genuine bind
conflict only — other startup failures are reported as themselves, never
disguised as port conflicts.

Server reuse across tests (one persistent PRKS process, database rollback
between tests, a shared IndexedDB or service-worker profile) is deliberately
**not** implemented. It trades a large correctness risk for time that
parallelism already recovered.

## Never return a live object from `page.evaluate`

`page.evaluate` serializes whatever its expression evaluates to. Handing back a
live application object -- a cytoscape instance, a store, anything with a deep
internal graph -- makes Playwright walk all of it across the protocol. Measured
on `prksGetResearchGraphDebug().cy`: **7-10 seconds per call**, enough
allocation to crash the renderer (`Target crashed`) under `--jobs 4`, and the
value arrives as `None` regardless.

Two defects had shipped from this, in the same file:

- `page.evaluate('window.__x = prksGetResearchGraphDebug().cy')` is an
  assignment EXPRESSION, so it returns the instance. Use a block body:
  `page.evaluate('() => { window.__x = ...; }')`.
- `assertIsNone(page.evaluate('prksGetResearchGraphDebug().cy'))` passed whether
  or not a graph was mounted, because a live instance serializes to `None` too.
  That assertion could not fail. Ask the page for a **boolean**:
  `page.evaluate('() => !!prksGetResearchGraphDebug().cy')`.

Return scalars, booleans, ids, counts and small plain data. Compare identity
inside the page (`a === b`), never by shipping both objects out.

## Scope a "nothing is showing" assertion to where it must not show

A page-wide `page.locator('.work-detail').count() == 0` is not the claim
"the Folders tab shows no Work detail". A tab whose PDF resource has mounted is
**warm-parked** when you switch away (`prksWarmParkTabContext`): its DOM is
moved into the hidden `#prks-tab-warm-parking` host and deliberately kept alive
so returning to it is instant. The node still matches a page-wide selector.

Whether it survives depends on whether the PDF finished mounting before the
switch, so the page-wide count passed when the test ran alone and failed under
`--jobs 4` -- which looks exactly like a render race and is not one. Measuring
it settled the question: the two views stayed co-mounted for the whole 2.5 s
sample window with no transition, so nothing was waiting to finish.

Scope to the visible surface (`.prks-tile--main .work-detail`), assert the
scope itself is non-empty so it cannot pass vacuously, and where a survivor is
legitimate, pin WHERE it survives:

    self.assertEqual(page.locator(".prks-tile--main").count(), 1)
    self.assertEqual(page.locator(".prks-tile--main .work-detail").count(), 0)
    self.assertEqual(page.locator(".work-detail").count(),
                     page.locator("#prks-tab-warm-parking .work-detail").count())

Cold-parked routes (anything without a PDF resource) are destroyed on switch,
so their page-wide counts are safe -- that asymmetry is why only the Work
detail flaked.

## No arbitrary sleeps in E2E

E2E tests prefer observable application/browser state over fixed delays. Wait on
the request, the API response, the domain generation, the IndexedDB row, the
route, the DOM state, the right-panel owner, or the button state — not
`page.wait_for_timeout(500)` after a mutation.

A fixed delay is appropriate only when elapsed time itself is under test:
debounce, backoff, probe intervals, and the settle windows that prove an action
issues *no* request. Those must stay — absence of behavior cannot be observed
without a window. Do not "optimize" them away, and do not lower Playwright's
default timeouts for speed: a shorter timeout does not make a passing test
faster, only a slow one fail sooner.

One measured exception, which looks like an arbitrary sleep and is not: the
250 ms settle delay in `_wait_entity_cached()` / `_wait_list_cached()`
(`tests/e2e/test_offline.py`). An IndexedDB row that a separate read
transaction can already observe is still lost when the page is torn down (go
offline + reload) immediately afterwards — `getEntity` returns null after the
reload and the route renders "not available offline". `offline-store.js`
resolving `readwrite` from `tx.oncomplete` does **not** make that safe.
Measured on the Concept-detail transition with
`tests/e2e/stress_cache_offline.py`: 20/20 iterations pass with the delay,
6–9/20 fail without it. There is no page-observable "durable across teardown"
signal to wait on instead, so the delay stays. Re-run that script before
believing any claim to the contrary.

## `wait_for_function` cannot express an async condition

`page.wait_for_function` **awaits a returned Promise and then tests the settled
promise for truthiness** — and a promise is always truthy. So this gate

```python
page.wait_for_function("() => prksSync.store.listOperations().then(r => r.length === 0)")
```

passes on its first poll whether the queue is empty or not: it waits one round
trip and reports success. It does not fail loudly; it silently stops being a
gate, which is worse than either passing or failing. Verified directly:
a predicate returning `Promise.resolve(false)` passes in 0.01 s, and one
resolving to `false` after 3 s passes in 3.03 s — the delay comes from awaiting
the promise, not from the answer.

Almost every durable-queue, IndexedDB and offline-store condition in this suite
is asynchronous, so **use `wait_for_async(page, expression, arg=..., timeout=...)`
from `tests/e2e/harness.py` for any predicate whose body contains `.then(`,
`async` or `await`.** One `page.evaluate` runs a browser-side poll that awaits
each predicate's resolved value (Python-like truthiness on that value), and
raises an `AssertionError` naming the last value it saw. Do not replace it with
`page.wait_for_function(() => promise)`. `wait_for_function` remains correct —
and preferred — for a synchronous predicate (DOM state, a global, `location.hash`).

This was found when a Title save appeared to reach an empty queue instantly
while its operation was in fact still `syncing`. Forty gates across seven
modules were vacuous; converting them exposed two real defects that had been
passing.
