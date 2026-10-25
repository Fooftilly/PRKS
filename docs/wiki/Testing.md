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

Run the **manual**, synthetic-only PostgreSQL query profiler:

```bash
scripts/test-postgres --profile
scripts/test-postgres --profile --sizes 12,48,128 --repetitions 8
```

The wrapper starts a disposable Docker PostgreSQL 18.6 server (digest-pinned in `scripts/test-postgres`) or a temporary local PostgreSQL 18+ cluster
with `shared_preload_libraries=pg_stat_statements` and `compute_query_id=on`,
creates a harness-owned migrated database and extension, seeds synthetic Works,
then deletes the database and server. The profiler **refuses**
`PRKS_TEST_DATABASE_URL`; do not enable the extension or reset its shared
statistics on a real PRKS cluster. A missing preload or extension causes a nonzero exit without emitting connection
strings or SQL text. Ordinary `scripts/test-postgres` and production Compose startup
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

## Advisory runtime-image SBOM pilot (#861)

The manual/path-relevant GitHub Actions workflow [Runtime image SBOM and Grype pilot](https://github.com/Fooftilly/PRKS/blob/master/.github/workflows/runtime-sbom-pilot.yml) builds the repository Dockerfile as a disposable local image, then creates a **Syft SPDX JSON SBOM for the built runtime image**, not a source-only Python dependency listing. It runs a pinned Grype scanner against that *same* SBOM without treating unaudited vulnerabilities as build blockers. The workflow runs on explicit dispatch or path-relevant changes to its workflow, Dockerfile, entrypoint, runtime requirements, dependency inventory, triage summarizer and summarizer regression tests. Other backend/frontend changes do not trigger a new container build, and the pilot does not upload release assets.

To run after the workflow is available on GitHub Actions: **Actions → Runtime image SBOM and Grype pilot (advisory) → Run workflow**. Inspect the job summary for total matches, **unique advisory IDs vs unique package/advisory pairs**, OS/Python/binary package groups, fix-listed/wont-fix/not-fixed counts, up to 12 sanitized priority examples, the built image ID, scanner version and Grype database timestamp/age. Download the 7-day **prks-runtime-sbom** and **prks-runtime-grype-advisory** artifacts for per-finding review; the latter contains the raw Grype JSON, image ID and bounded **prks-runtime-triage.json**. To rerun the summary offline, place `prks-runtime.spdx.json`, `prks-runtime.grype.json`, and `prks-runtime-image-id.txt` in a disposable directory, `cd` into that directory, and execute `python3 /path/to/PRKS/scripts/ci/summarize_runtime_sbom.py`. It reads only those fixed basenames and writes `prks-runtime-triage.json` and `prks-runtime-triage.md` there (stdlib-only; never use real library material). Tool versions are pinned, but the vulnerability database updates independently, so check its freshness and the scan time before comparing reports. The Dockerfile's base images currently use floating tags: the recorded image ID identifies this run, but the workflow does not yet retain resolved base-image digests or attestations needed for reproducible release builds.

This pilot covers OS packages (including `qpdf`) and Python packages actually present in the packaged runtime, which source-only dependency review can miss. It deliberately has no enforcement threshold, waiver/expiry policy, provenance attestation, automatic release asset, or CycloneDX companion yet. Before any release or required check, triage fixes, false positives, package context, CVE database freshness, and ownership under #861 and the release/supply-chain roadmap. Never include user libraries, local secrets, or private runtime state in an image or exported SBOM.


**Triage policy:** The GitHub Dependency Review workflow flags *newly introduced* advisories in supported manifests; the built-image Syft/Grype scan additionally identifies OS packages, native dependencies and interpreter components in the runnable Docker artifact. Python package advisories may appear in both, so do not add or compare their raw totals as independent findings. Check IDs, package/installed version, ecosystem and upstream namespace before classifying overlap. Listed upgrade versions can be incompatible with the pinned runtime (for example a Python-major upgrade); `not-fixed` means a vendor fix is not currently listed; `wont-fix` indicates a vendor does not intend to issue a fix. Neither constitutes PRKS risk acceptance, and the actions differ. Prioritize confirmed, fixable, reachable vulnerabilities separately from unpatched OS packages; document decisions in #861, never auto-suppress findings. Scanner matches are not proof of exploitability, and fixing them can require Docker base updates, Python runtime changes or an upstream release. This remains advisory and does not alter branch protection or upload scan output to third-party SaaS beyond the existing workflow's GitHub Actions artifacts.


## Advisory axe-core browser accessibility smoke (#856)

The opt-in [Advisory axe-core accessibility smoke](https://github.com/Fooftilly/PRKS/blob/master/.github/workflows/axe-playwright-advisory.yml) GitHub Actions workflow uses **PRKS's existing Python unittest/Playwright Chromium E2E harness and disposable synthetic `--testing` server**. It runs when the pilot workflow/tests change or when manually dispatched; normal full/sharded/affected E2E does **not** discover its additional test without `PRKS_E2E_AXE=1`. It does not create another frontend E2E framework, use an external accessibility SaaS, or alter the required check names.

The workflow installs the existing `frontend-app` npm lockfile's pinned **axe-core 4.13.0** as a *dev-only* tool and injects its local `axe.min.js` into a real browser after application readiness. A five-state, single-server sequence covers document/app shell (with the routed `#page-content` excluded but global language/title and workspace chrome preserved), Vue Folder library, seeded Work detail, synthetic-PDF viewer and Settings dialog. The Work detail subtree includes its PDF viewer, so per-surface rule counts may overlap and must not be summed as independent findings. Axe's WCAG 2 A/AA and WCAG 2.1 A/AA tags report rule IDs, impact, count and a few sanitized public CSS targets; the Playwright scenario separately verifies keyboard Enter opens Settings and Escape restores focus. Axe cannot by itself prove keyboard order, focus trapping or whole-app usability. Existing `@storybook/addon-a11y` remains useful for component inspection and is not replaced.

Run locally with the normal pinned E2E Python/browser environment and `npm ci --ignore-scripts` in `frontend-app`, then at the repository root:

```bash
PRKS_E2E_AXE=1 python tests/e2e/run.py --jobs 1 --no-pointer-capture \
  tests.e2e.test_axe_a11y.AxeAccessibilityPilotTests.test_advisory_scans_five_real_fixture_surfaces_and_keyboard_focus
```

The manual CI run uploads only a bounded JSON report (`synthetic-axe-advisory`, retained seven days) and a compact GitHub Actions summary. No page HTML, document titles, research text, browser screenshots, real user storage or URL queries belong in reports. A scan failure or missing page still fails this **opt-in** job; existing accessibility violations are recorded as *advisory debt*, not ignored or silently made required. Review affected selectors/rules and link established accessibility defects under #616/#745/#830/#425; measure runtime and reproducibility before considering a narrow blocking smoke or extending Firefox/WebKit.


## Advisory local Playwright visual comparison (#857)

The optional [visual pilot workflow](https://github.com/Fooftilly/PRKS/blob/master/.github/workflows/visual-regression-pilot.yml) reuses the existing PRKS isolated `AppServer`, seeded E2E Works/minimal PDF, pinned Python Playwright Chromium and Pillow. It **does not** run a second full E2E test suite, use an external screenshot host, or change protected E2E check names. The extra workflow triggers only for its own changed files or a manual dispatch.

The five synthetic states are Folder library, Work detail with mounted notes/PDF, the synthetic PDF region, split Main/Secondary panes, and Settings dialog. The pilot pins a 1400×900 viewport, DPR 1, `en-US` locale, UTC timezone, light color scheme and reduced motion; it waits for real route/editor/PDF/second-pane readiness and `document.fonts.ready`, with disabled screenshot animations and hidden caret. CI uses `ubuntu-24.04` plus the repository Playwright/browser pins. It does not rely on arbitrary sleeps. The PDF fixture is generated testing data, not a personal research file.

**Reviewable baseline procedure:** The first run with no `tests/visual/baseline.json` produces **candidate only**, not a passing pixel comparison. Retrieve the `synthetic-local-visual` Actions artifact (five PNGs plus the compact `prks-visual-candidate.json`), inspect every PNG for intended layout and synthetic content, then commit the candidate JSON as `tests/visual/baseline.json` in a reviewed PR. Subsequent workflow runs compare each state against this approved reference and return a nonzero result for significant changed pixels or dimensions. On an intentional UI change, manually inspect the candidate/diff artifact against the changed DOM, then explicitly regenerate and review the baseline; **never automatically accept new images**.

The committed JSON stores a small 48×30 RGB sampling fingerprint per scene (zlib/base64, not a full screenshot). The pilot tolerates mild font-antialias variations and is aimed at meaningful visual shifts, not single-pixel precision; the pure unit test deliberately inverts a controlled synthetic image and proves comparison fails. Incomplete scenario readiness, missing baselines in check mode and malformed metadata fail closed. PNGs/artifacts are fixture-only, retained seven days, not committed and never sent to Argos. Avoid GitHub log dumps containing real file names/URLs; user library content must never be captured. Evaluate Argos adoption separately under #857 after measuring actionable findings, flake rate, runner-minutes and privacy safeguards.


## Schemathesis — bounded read-only API contract pilot (#855)

PRKS's `/api/openapi.json` is **Positions-only**, not an application-wide contract. The opt-in [Schemathesis workflow](https://github.com/Fooftilly/PRKS/blob/master/.github/workflows/schemathesis-readonly-pilot.yml) instead verifies `docs/api/openapi-publishers.json` against its Pydantic-generated builder and live `GET /api/openapi/publishers.json`. The test creates a **second, one-operation copy** containing only `GET /api/publishers` (`listPublishersInUse`); the published fragment's POST and DELETE routes are never passed to the scanner.

The scanner runs on an existing fresh, isolated, loopback `AppServer --testing` with synthetic seeded Works and a populated Publisher catalog. It verifies the actual successful response DTO, a malformed Host refusal (HTTP 421), and then lets pinned **Schemathesis 4.32.4** generate bounded GET query variations. Defaults: seed 855, 25 generated fuzz examples per operation, examples/fuzzing phases only, 75-second process timeout, single worker and 10 requests/sec. It checks server errors, documented status codes, JSON content type and response schemas; no stateful sequences or mutations. Schemathesis generates a small built-in examples phase in addition to the 25-case fuzz bound. The coverage phase is excluded because it generates unexpected-method probes such as TRACE even with a GET filter; this pilot must never send non-GET operations. The process timeout and rate limit are additional safety limits.

Locally, install the existing exact pins with `python scripts/ci/install_pinned_python.py schemathesis`, then run `python scripts/ci/schemathesis_pilot.py` from the repository root. The command itself creates/deletes a **temporary** test server and reads no PRKS production data. Do not run it in a shell with `PRKS_AUTH` configured: the pilot refuses that setting because **it does not have a disposable PostgreSQL-backed session or authority**. This first read-only pass does **not** claim to test PG authentication, capability boundaries, mutation Origin/CSRF checks or app-wide OpenAPI conformance. The 421 Host check is only a host-admission probe, not a replacement for auth testing. Those require separate safe identities/fixtures and a future approved extension of #855.

The workflow runs manually or for changes to the pilot/Publishers schema/dependency pins. Its only reported outcome is a small sanitized route/method/seed/elapsed/exit summary. On failure, reproduce from a new disposable test server with the fixed seed; use `python scripts/ci/schemathesis_pilot.py --debug-local` for full raw Schemathesis details **only on a private local machine**. The flag is rejected in GitHub Actions, and logs are sanitized by default. No generated payloads, raw request/response bodies, cookies, secrets, research documents, database paths or dynamic query strings are uploaded to CI artifacts. Before expanding API coverage, verify the real HTTP contract for the new family and require explicit allowlisting of every method/path. Existing deterministic Pydantic/openapi-core tests remain the primary contract gate.
