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

The property tests are `tests/pg/test_tags_properties.py` and `tests/pg/test_works_metadata_properties.py`. The PostgreSQL job installs the runtime pins plus `openapi-core`, but not Hypothesis, so those sequences are enumerated exhaustively or replayed from named seeds rather than drawn by Hypothesis.

## Sync envelope fuzzing

`tests/test_sync_envelope_properties.py` fuzzes everything `POST /api/sync/operations` decides before it touches the ledger (`backend.sync.protocol.validate_operation`), with no database. It feeds arbitrary JSON, a deterministic sweep of awkward JSON shapes into every value of every seed, and Hypothesis mutations of real envelopes. The seeds in `tests/fixtures/sync_envelope_seeds.json` were recorded from the `tests/pg` sync suites, one per operation and payload shape; a test fails if any seed stops validating or an operation has none. For every input the answer must be a 400 with a known code (`INVALID_ENVELOPE`, `INVALID_BASE_REVISION`) or an operation PostgreSQL can store (no NUL, lone surrogate, NaN or infinity), and normalizing must be idempotent.

## Research markup differential

`tests/test_research_markup_differential.py` fuzzes the two research-markup parsers against each other: `backend/research_markup.py` (the research index and graph) and `frontend/js/research-links.js` (the preview), run in one Node process through `tests/browser/research_markup_oracle.js`. Notes drawn from markers, code fences, escapes, percent-encodings and both languages' whitespace must give the same references at the same code-point offsets, with the same names and labels, and the preview's Concept key must group names exactly as `normalize_concept_key` (`prks.identity_key`) does, checked on every assigned code point.

## PDF Range header fuzzing

`tests/test_byte_range_properties.py` fuzzes the PDF `Range` header parser (`backend.server.parse_byte_range`), which every Work and Processing PDF route reads before streaming. It never raises; a 206 is one range inside the file, and a single well-formed range answers exactly the slice it names or 416; another unit, signs, non-ASCII digits, `last < first` or several ranges are ignored and the whole file is sent (RFC 9110 §14).

## Hypothesis profiles

Three Hypothesis profiles live in `tests/hypothesis_profiles.py`, selected by `PRKS_HYPOTHESIS_PROFILE`:

- `pr` (default, the Unit / API / contract gate): `derandomize=True`, so every run draws the same inputs and a red pull request is caused by that pull request. About 2 seconds.
- `nightly` ([advisory workflow](https://github.com/Fooftilly/PRKS/blob/master/.github/workflows/hypothesis-nightly.yml), daily and on manual dispatch, never on pull requests): random seeds and 5,000 examples per property. Not a required check.
- `mutation` (only `scripts/ci/mutation_testing.py`): derandomized with 100 examples, since every mutant reruns the properties.

```bash
./.venv/bin/python -m unittest tests.test_sync_envelope_properties tests.test_byte_range_properties tests.test_research_markup_differential -v
PRKS_HYPOTHESIS_PROFILE=nightly ./.venv/bin/python -m unittest tests.test_sync_envelope_properties tests.test_byte_range_properties tests.test_research_markup_differential -v
```

When the nightly run fails, it prints the shrunk input and a `@reproduce_failure` blob. Fix the bug, add the input as an `@example(...)` on the property, and add a PostgreSQL regression in `tests/pg` when the input reached the database. Never relax a property or retry to get green. When a family changes its payload shape, regenerate the seed for it from the `tests/pg` sync suite rather than editing it by hand.

## Mutation testing (#651 B2)

`scripts/ci/mutation_testing.py` runs [mutmut](https://github.com/boxed/mutmut) on a few pure modules. mutmut changes one small piece of a module at a time (a comparison, a constant, a boolean operator) and reruns that module's unit tests. A mutant the tests still pass on has **survived**: a behaviour change no test notices. Each target in `TARGETS` names the modules to mutate and the database-free tests that cover them: `text-identity`, `sync-envelope` (`backend/sync_protocol.py`), `research-markup` and `docs-only-ci` (the docs-only classifier, Markdown validator and wiki link rules).

The [advisory workflow](https://github.com/Fooftilly/PRKS/blob/master/.github/workflows/mutation-testing.yml) runs every target weekly, on manual dispatch, and on pull requests that change the mutation setup. The job summary gives counts and the functions with survivors. Survivors never fail the job; only an incomplete run does. The run copies the tracked files into a temporary workspace and writes mutmut's `[mutmut]` section there, so the checkout is never changed.

```bash
python scripts/ci/install_pinned_python.py mutation
python scripts/ci/mutation_testing.py run docs-only-ci --keep /tmp/prks-mutation
python scripts/ci/mutation_testing.py show docs-only-ci scripts.ci.wiki_links.x_map_prose__mutmut_11 --workspace /tmp/prks-mutation
```

For each survivor, read the diff first. If it can change behaviour, add an assertion that fails on it. Some survivors are equivalent (they cannot change behaviour, such as `fence = None` for `fence = ""` in a truthiness test): leave those, or delete the redundant code that made them possible. `no_tests` mutants are in functions the selected tests never call, such as the ledger code in `backend/sync/protocol.py`, which needs PostgreSQL. A test that runs a module as a subprocess cannot judge a mutant, because mutmut's switch between mutants only works in-process; a target lists such tests under `deselect`.

## Scheduled results as issues

Scheduled runs report to issues, so nobody has to read the Actions page ([`scripts/ci/scheduled_report.py`](https://github.com/Fooftilly/PRKS/blob/master/scripts/ci/scheduled_report.py)).

- **Failures.** [`scheduled-report.yml`](https://github.com/Fooftilly/PRKS/blob/master/.github/workflows/scheduled-report.yml) watches the scheduled runs of the PRKS Test Gate (nightly unit repeat), nightly property fuzzing and mutation testing. A failed run opens the issue "Scheduled check failing: &lt;workflow&gt;" (label `scheduled-check`) with the run link and failed jobs. A repeat failure comments on that issue, and the next passing run closes it. Cancelled runs report nothing.
- **Mutation survivors.** The weekly mutation run keeps one comment per target on the standing issue "Mutation testing report" (label `mutation-report`), edited in place, which sends no notification. A new comment, which does notify, appears only when a target has more survivors than its last report.

A new scheduled test workflow must be added to the `workflow_run` list, or named in `NOT_TEST_RUNS` in `tests/test_scheduled_report.py` when it is not a test run.

## PostgreSQL model-based Work lifecycle sequences (#1285)

`tests/pg/test_work_lifecycle_model.py` draws seeded random sequences (10 seeds × 14 steps) of Work–Tag links, Work Folder moves, title edits, Work and Tag deletes and replays of earlier operations, sends each through the sync protocol, and compares PostgreSQL with a small reference model after every step: live Works/Tags, links, Folders, titles and every edited scope's revision as the shared decision table predicts (future base → `FUTURE_REVISION`, stale disagreeing base → `REVISION_CONFLICT`, else acknowledged and advanced only on a real change). It also checks that no link or filing dangles, no `entity_revisions` row moves backwards, a Work delete tombstones each of its scopes exactly once, and a replay answers as the first time and leaves every `entity_revisions` row as it was, deleted targets' tombstones included. Revisions are predicted by the model, never copied from the server's answer. The PostgreSQL job has no Hypothesis, so sequences come from a seeded `random.Random`; the generator picks fixtures by creation order and the trace names them `W0`, `T1`, `F0`, so a failure's seed and printed steps replay it exactly; keep a significant counterexample as a named regression test.

## PostgreSQL error-mapping property checks (#391)

The existing Unit / API / contract Test Gate also runs `tests.test_hypothesis_pg_error_mapping`. It uses **synthetic psycopg error objects, not a database** to generate SQLSTATEs, constraint-name near misses and check-field names. The invariants detect accidental retries outside `40001`/`40P01`, 503-vs-500 misclassification, acceptance of unregistered or prefix/suffix-spoofed constraints, and leakage of database driver messages. The fixtures use bounded deterministic test setup with a Hypothesis example limit and no saved example database; on failure, record the minimized case in a permanent regression test.

Run `python -m unittest tests.test_hypothesis_pg_error_mapping -v` with the existing runtime and unit/test dependency pins. These are **pure mapper tests**, not evidence that PostgreSQL, session authorization, or HTTP translation passed: `tests/pg/test_db_errors.py` remains the real disposable-PostgreSQL integration authority. No extra runner or package is installed.

## Adversarial PDF corpus (#1287)

`tests/pdf_corpus.py` generates a small synthetic corpus at test time. It is built from PyMuPDF and hand-written PDF syntax, with fixed metadata so each build of an unencrypted case gives the same bytes (AES-256 draws a random key, so the encrypted case differs), and no third-party document is committed. The cases are:

- a valid control file;
- a truncated file;
- a damaged xref table;
- an AES-256 password file;
- zero pages;
- 400 tiny pages;
- rotated pages;
- a page with no text;
- a font whose ToUnicode map sends codes to control, private-use and non-BMP characters;
- three annotations sharing one `/NM`;
- a `%PDF-` header followed by garbage;
- PNG bytes under a `.pdf` name.

`tests/pg/test_adversarial_pdf_corpus.py` sends each case through the real HTTP routes. The stages are upload into the Processing queue, import, the `text.extract` job, any-mode search, the thumbnail, the reader's byte stream, markup adoption for the shared-id case, and `ingest.store_pdf` linearization. Each stage has one asserted outcome per case, in `EXPECTED`:

- An upload is kept, or refused with `PROCESSING_NOT_A_PDF` and leaves no item.
- An import always creates exactly one Work and one Asset. Import does not parse the PDF.
- Extraction ends `indexed`, `empty` or `failed`, and its job succeeds.
- The thumbnail is an image or the documented 404.
- The reader gets the stored bytes back unchanged.
- A shared annotation id is adopted once.
- Linearization is checked with `PRKS_PDF_LINEARIZE` pinned both ways. Switched off, it reports `disabled` and keeps the source, even with qpdf installed. Switched on, it writes a copy, keeps the source on a qpdf refusal, or reports `missing-qpdf` when qpdf is absent. A qpdf that finishes with warnings (exit 3, as on `damaged_xref`) gives `ok-repaired` when PyMuPDF shows the same pages in its repaired copy (count, size, text, pixels rendered at 72 dpi, links, annotations and form fields, plus the outline, page labels and attachments, compared in a child process stopped after 30 seconds), and `qpdf-warnings` with the source kept otherwise (#1322).

Extraction and linearization each run on a watchdog thread, so a case that hangs fails after 10 seconds instead of blocking the suite (PRKS bounds them itself too: qpdf after 120 seconds, extraction after 300 seconds in a child process, #1321). Where Linux `/proc/self/clear_refs` is available, the peak resident size is reset before the run and its growth is bounded (elsewhere that check is skipped), and the server must still list every imported Work at the end. The whole module runs in about 4 seconds. A changed outcome is a behavior change: update `EXPECTED` only when it is intended, and file any crash, hang or partial import as its own bug.

## HTTP authorization matrix (#1286)

`tests/pg/test_http_authorization_matrix.py` sends one real request per `backend/api` route family (and per route that streams a managed file) over HTTP as seven callers: owner, editor, reader, a revoked member, a signed-out browser, the owner of another Library, and no session. Each row says whether it needs read, edit or Library administration; a caller without the right gets 403 `forbidden` (a member) or 401 `not_authenticated` (everyone else), the refusal names neither the Library nor the seeded entities, and every Library content table is unchanged afterwards. Every caller with the right must get a success, so each row is a real route with a valid request, except three documented route-level refusals that still come after admission: the backup download's 404 without a ready token, the stage route's 415 for a body that is not an archive, and the sync route's 400 for an empty envelope. Where a 2xx alone would not show the route did its work, the row also checks the result: the Research Notes row must save the note it sent, and the Processing import row imports a ready item uploaded just before each request and must report it imported. A `backend/api` module without a row fails `test_every_api_module_has_a_row`, so a new family joins the matrix. Run it with `scripts/test-postgres tests.pg.test_http_authorization_matrix`.

## PostgreSQL crash-point matrix (#1284)

`tests/pg/test_crash_points.py` runs whole write workflows in a child process (`tests/pg/crash_point_child.py`) that `os._exit`s at a named point, so no `finally`, rollback or cleanup of the killed process runs. The parent then restarts as a new process does (backup/restore recovery, the orphan sweep, cleanup claims settled) and checks one shared set of invariants: every key a row names is stored, every stored object is named, no write temporary or leftover claim remains, and imported/ready Processing items are complete. The matrix covers Processing upload (mid-write, after the store, after the commit), Processing import (mid-transaction, after the commit), managed-PDF replacement (after the store, inside the command, after the commit), the cleanup settle (after the unlink) and a backup killed mid-archive. A separate in-process case ends a sync write's connection from the server side (`pg_terminate_backend`) and checks only its own outcome: nothing committed, and the retry applies once. A retry of an acknowledged-but-unanswered request must return the first answer and apply nothing twice. The tests run in the ordinary PostgreSQL helper job on disposable databases and data roots:

```bash
scripts/test-postgres tests.pg.test_crash_points
```

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


## Optional Firefox/WebKit cross-browser smoke (#651 C2)

The [Firefox/WebKit smoke workflow](https://github.com/Fooftilly/PRKS/blob/master/.github/workflows/cross-browser-smoke.yml) is **manual or limited to PRs changing the pilot itself**. It reuses the existing isolated `AppServer(seed_library)`, the pinned Python Playwright package and `open_app_page` with a fresh browser context per engine. The test starts a local `prks_app.py --testing` process with disposable synthetic storage and its own disposable database on the `PRKS_TEST_DATABASE_URL` server (the workflow runs a PostgreSQL 18 service); it refuses inherited `PRKS_AUTH`/`PRKS_DATABASE_URL` configuration and never accepts an external target.

Firefox and WebKit independently exercise the folder shell, Work detail and CodeMirror editor readiness, **typing synthetic Research Notes and verifying server commit plus the same value after a full page reload**, keyboard opening/dismissal of Settings with restored focus, **real first-page PDF image rendering**, and two distinct mounted Work reading contexts in Main and Secondary panes **before and after a full browser reload**. PDF readiness reuses the existing Chromium E2E page/tab helpers; both panes must render images rather than merely show a tiled layout. Unexpected app JavaScript/console errors, off-origin requests, and HTTP 5xx also fail through the existing Playwright collector. The synthetic WebKit pilot alone tolerates up to three exact, browser-generated ResizeObserver loop notifications (a standard mechanism for deferring layout observations to the next paint), provided they carry no JavaScript exception type or script URL; four or more in the same session still fail as a potential persistent layout loop. Firefox, Chromium, and every other error remain strict. Results list only engine versions and completed check identifiers, without screenshots, PDFs, page HTML, titles, or private URLs. **This proves only synthetic notes editing and server persistence/reload, first-page PDF rendering, and a two-Work split restored with the same tab IDs, Work owners and mounted PDF readers after an explicit workspace-persistence flush and reload—not offline note recovery, annotation durability, comprehensive reader parity, PostgreSQL authentication, or every browser feature.** The existing Chromium full E2E remains required and authoritative; this does not add Firefox/WebKit to that gate or claim v1.0 browser support.

Local usage with the existing pinned runtime and dev/test packages:

```bash
export PLAYWRIGHT_BROWSERS_PATH="$PWD/.playwright-browsers"
python -m playwright install --with-deps firefox webkit
python -m unittest -v tests.test_cross_browser_smoke
python scripts/ci/cross_browser_smoke.py --engines firefox webkit
```

The browser installation can require Linux system packages. On developer machines where package installation needs elevated privileges, use the platform-supported Playwright install procedure; do not change the PRKS server or relax browser checks to get a green result.

## Advisory axe-core browser accessibility smoke (#856)

The opt-in [Advisory axe-core accessibility smoke](https://github.com/Fooftilly/PRKS/blob/master/.github/workflows/axe-playwright-advisory.yml) GitHub Actions workflow uses **PRKS's existing Python unittest/Playwright Chromium E2E harness and disposable synthetic `--testing` server**. It runs when the pilot workflow/tests change or when manually dispatched; normal full/sharded/affected E2E does **not** discover its additional test without `PRKS_E2E_AXE=1`. It does not create another frontend E2E framework, use an external accessibility SaaS, or alter the required check names.

The workflow installs the existing `frontend-app` npm lockfile's pinned **axe-core 4.14.0** as a *dev-only* tool and injects its local `axe.min.js` into a real browser after application readiness. A five-state, single-server sequence covers document/app shell (with the routed `#page-content` excluded but global language/title and workspace chrome preserved), Vue Folder library, seeded Work detail, synthetic-PDF viewer and Settings dialog. The Work detail subtree includes its PDF viewer, so per-surface rule counts may overlap and must not be summed as independent findings. Axe's WCAG 2 A/AA and WCAG 2.1 A/AA tags report rule IDs, impact, count and a few sanitized public CSS targets; the Playwright scenario separately verifies keyboard Enter opens Settings and Escape restores focus. Axe cannot by itself prove keyboard order, focus trapping or whole-app usability. Existing `@storybook/addon-a11y` remains useful for component inspection and is not replaced.

Run locally with the normal pinned E2E Python/browser environment and `npm ci --ignore-scripts` in `frontend-app`, then at the repository root:

```bash
PRKS_E2E_AXE=1 python tests/e2e/run.py --jobs 1 --no-pointer-capture \
  tests.e2e.test_axe_a11y.AxeAccessibilityPilotTests.test_advisory_scans_five_real_fixture_surfaces_and_keyboard_focus
```

The manual CI run uploads only a bounded JSON report (`synthetic-axe-advisory`, retained seven days) and a compact GitHub Actions summary. No page HTML, document titles, research text, browser screenshots, real user storage or URL queries belong in reports. A scan failure or missing page still fails this **opt-in** job; existing accessibility violations are recorded as *advisory debt*, not ignored or silently made required. Review affected selectors/rules and link established accessibility defects under #616/#745/#830/#425; measure runtime and reproducibility before considering a narrow blocking smoke or extending Firefox/WebKit.


## Advisory local Playwright visual comparison (#857)

The optional [visual pilot workflow](https://github.com/Fooftilly/PRKS/blob/master/.github/workflows/visual-regression-pilot.yml) reuses the existing PRKS isolated `AppServer`, seeded E2E Works/minimal PDF, pinned Python Playwright Chromium and Pillow. It **does not** run a second full E2E test suite, use an external screenshot host, or change protected E2E check names. The extra workflow triggers on its own test/runner changes, **frontend runtime/source assets** (`frontend/**`, `frontend-app/src/**`, public assets, app HTML and Vite config), or manual dispatch. Unit-test-only changes in `frontend-app/src/**` are excluded; backend-only and dependency-only PRs do not trigger the visual workflow. It remains **advisory**, not a required branch-protection check. **The Python app serves committed `frontend/vue/` assets, not the Vue source directory.** Before each visual capture the workflow installs the pinned frontend build dependencies, rebuilds the production Vue bundle and related generated scripts, and refuses to report a visual pass unless all canonical generated assets match their committed versions. A Vue source-only change without updated output therefore fails the visual job rather than producing a misleading screenshot of stale code. This reuses the existing `npm run build --prefix frontend-app` and generated-asset list from Fast Static Analysis, but runs it in the same job as the browser to guarantee the actual captured build. Two **pre-build-check** five-scene runs spanned about 27–28 seconds each including setup/artifact upload on cached runners; the new Node install/build adds measurable cost, so those earlier times are **not** an estimate of current job duration. Review the new job timings before enforcement; revisit the path filter or manual-only policy if runner minutes or false positives prove excessive.

The five synthetic states are Folder library, Work detail with mounted notes/PDF, the synthetic PDF region, split Main/Secondary panes, and Settings dialog. The pilot pins a 1400×900 viewport, DPR 1, `en-US` locale, UTC timezone, light color scheme and reduced motion; it waits for real route/editor/PDF/second-pane readiness and `document.fonts.ready`, with disabled screenshot animations and hidden caret. CI uses `ubuntu-24.04` plus the repository Playwright/browser pins. It does not rely on arbitrary sleeps. The PDF fixture is generated testing data, not a personal research file.

**Reviewable baseline procedure:** The first run with no `tests/visual/baseline.json` produces **candidate only**, not a passing pixel comparison. Retrieve the `synthetic-local-visual` Actions artifact (five PNGs plus the compact `prks-visual-candidate.json`), inspect every PNG for intended layout and synthetic content, then commit the candidate JSON as `tests/visual/baseline.json` in a reviewed PR. Subsequent workflow runs compare each state against this approved reference and return a nonzero result for significant changed pixels or dimensions. On an intentional UI change, manually inspect the candidate/diff artifact against the changed DOM, then explicitly regenerate and review the baseline; **never automatically accept new images**.

The committed JSON stores a small 48×30 RGB sampling fingerprint per scene (zlib/base64, not a full screenshot). The pilot tolerates mild font-antialias variations and is aimed at meaningful visual shifts, not single-pixel precision; the pure unit test deliberately inverts a controlled synthetic image and proves comparison fails. Incomplete scenario readiness, missing baselines in check mode and malformed metadata fail closed. When capture fails, a bounded `prks-visual-failure.json` contains only a fixed stage label, exception **type** and completed scene names (never the raw Playwright exception, URL, selector, document text or Work id); the advisory job summary identifies the interrupted stage. For completed comparisons, each scene reports the exact triggering class: dimensions, mean color change, changed-pixel fraction, both thresholds, or within tolerance. To replay locally from the PRKS checkout root with the same pinned E2E dependencies and Chromium, run `repo="$(pwd)"; temp="$(mktemp -d)"; (cd "$temp" && python "$repo/scripts/ci/visual_regression.py" --mode check)`; check CI environment/commit parity before treating a difference as a regression. Reusing a temporary directory is safe: each capture removes only its prior candidate/results/failure JSON and five known synthetic scene PNGs before starting, so a stale success cannot hide a new failure. The runner does not retry captures or accept new baselines automatically. PNGs/artifacts are fixture-only, retained seven days, not committed and never sent to Argos. Avoid GitHub log dumps containing real file names/URLs; user library content must never be captured. Evaluate Argos adoption separately under #857 after measuring actionable findings, flake rate, runner-minutes and privacy safeguards.


## OWASP ZAP — passive security pilot (#860)

The manual [ZAP workflow](https://github.com/Fooftilly/PRKS/blob/master/.github/workflows/zap-passive-pilot.yml) looks for runtime HTTP weaknesses that source scanners miss, such as missing security headers, cookie flags and information leaks. It is **passive only**. `scripts/ci/zap_passive_pilot.py` starts a disposable `AppServer --testing` on loopback in session-auth mode (its own database on the `PRKS_TEST_DATABASE_URL` server, an owner and one editor, the UX-tour synthetic library). It also starts a ZAP 2.17.0 container, pinned by digest, in daemon mode with `-silent` and the API bound to loopback, and switches ZAP to **safe mode**, so ZAP can neither spider nor attack. The pilot itself sends a fixed allowlist of requests through ZAP's proxy: anonymous, signed in as the owner, and signed in as the editor. The only non-GET route is `POST /api/login`, sent three times: one failed anonymous login, then the owner's and the editor's logins. ZAP's passive rules only watch that traffic. The official `zap-baseline.py` and `zap-api-scan.py` recipes are not used, because they spider or scan actively by default.

A run fails (exit 1, listed under `problems`) when:

- a request gets a different answer than the allowlist expects, for example the editor's `GET /api/backups/download` must be 403;
- ZAP's history of completed exchanges (those with a response) is not exactly the pilot's own requests;
- any Library content table changed.

Alerts are advisory and do not fail the run. The summary holds the ZAP and PRKS versions, the image digest, each request's route template and answer, and per passive rule its id, name, risk, confidence, count and route templates. It holds no dynamic or private URL values, evidence, cookies or bodies; the fixed synthetic search query (`/api/search?q=Graph`) stays in its route. Triage verified findings into focused issues, or into private reporting under `SECURITY.md` when sensitive, before any check blocks. The existing HTTP auth and Origin/CSRF suites stay authoritative.

Locally, with Docker and a disposable PostgreSQL 18 server that stays up while the pilot runs (the pilot refuses an inherited `PRKS_AUTH`, `PRKS_DATABASE_URL` or `PRKS_PUBLIC_ORIGIN`, so the command clears them):

```bash
docker pull "$(python -c 'from scripts.ci.zap_passive_pilot import ZAP_IMAGE; print(ZAP_IMAGE)')"
env -u PRKS_AUTH -u PRKS_DATABASE_URL -u PRKS_PUBLIC_ORIGIN \
  PRKS_TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:55432/postgres PRKS_TESTING=1 \
  python scripts/ci/zap_passive_pilot.py --out zap-summary.json
```

The image is about 3.7 GB unpacked. Active scans stay out of scope: they need explicit manual authorization, a target allowlist, operation exclusions, time limits and a disposable reset.

## Schemathesis — bounded read-only API contract pilot (#855)

PRKS's `/api/openapi.json` is **Positions-only**, not an application-wide contract. The opt-in [Schemathesis workflow](https://github.com/Fooftilly/PRKS/blob/master/.github/workflows/schemathesis-readonly-pilot.yml) instead verifies `docs/api/openapi-publishers.json` against its Pydantic-generated builder and live `GET /api/openapi/publishers.json`. The test creates a **second, one-operation copy** containing only `GET /api/publishers` (`listPublishersInUse`); the published fragment's POST and DELETE routes are never passed to the scanner.

The scanner runs on an existing fresh, isolated, loopback `AppServer --testing` with synthetic seeded Works and a populated Publisher catalog. It verifies the actual successful response DTO, a malformed Host refusal (HTTP 421), and then lets pinned **Schemathesis 4.32.5** generate bounded GET query variations. Defaults: seed 855, 25 generated fuzz examples per operation, examples/fuzzing phases only, 75-second process timeout, single worker and 10 requests/sec. It checks server errors, documented status codes, JSON content type and response schemas; no stateful sequences or mutations. NUL is not generated, because the server refuses it in the request target before routing, with a 400 the fragment does not document. Schemathesis generates a small built-in examples phase in addition to the 25-case fuzz bound. The coverage phase is excluded because it generates unexpected-method probes such as TRACE even with a GET filter; this pilot must never send non-GET operations. The process timeout and rate limit are additional safety limits.

Locally, install the existing exact pins with `python scripts/ci/install_pinned_python.py schemathesis`, then run `python scripts/ci/schemathesis_pilot.py` from the repository root. The command itself creates/deletes a **temporary** test server and reads no PRKS production data. Do not run it in a shell with `PRKS_AUTH` configured: the pilot refuses that setting because **it does not have a disposable PostgreSQL-backed session or authority**. This first read-only pass does **not** claim to test PG authentication, capability boundaries, mutation Origin/CSRF checks or app-wide OpenAPI conformance. The 421 Host check is only a host-admission probe, not a replacement for auth testing. Those require separate safe identities/fixtures and a future approved extension of #855.

The workflow runs manually or for changes to the pilot/Publishers schema/dependency pins. Its only reported outcome is a small sanitized route/method/seed/elapsed/exit summary. On failure, reproduce from a new disposable test server with the fixed seed; use `python scripts/ci/schemathesis_pilot.py --debug-local` for full raw Schemathesis details **only on a private local machine**. The flag is rejected in GitHub Actions, and logs are sanitized by default. No generated payloads, raw request/response bodies, cookies, secrets, research documents, database paths or dynamic query strings are uploaded to CI artifacts. Before expanding API coverage, verify the real HTTP contract for the new family and require explicit allowlisting of every method/path. Existing deterministic Pydantic/openapi-core tests remain the primary contract gate.


## Locust — bounded read-only load pilot (#859, #765 T08)

The manual [Locust workflow](https://github.com/Fooftilly/PRKS/blob/master/.github/workflows/locust-readonly-pilot.yml) measures throughput and latency of the routes a browser uses while reading the library: `GET /api/works`, `GET /api/works/:id`, `GET /api/search`, `GET /api/tags` and `GET /api/research-graph`. It complements the single-user E2E suite; it is not a PR gate. Gating it on paths would need separate stability evidence and maintainer approval.

`scripts/ci/locust_pilot.py` starts its own disposable `AppServer --testing` on loopback (its own database on the `PRKS_TEST_DATABASE_URL` server, loopback-owner session, the UX-tour synthetic library plus 8 (`small`) or 64 (`medium`) synthetic Works), checks each of the five routes once before any load, then runs `scripts/ci/locustfile_readonly.py` headless with 5 users, spawn rate 5, for 30 seconds. It refuses any target but `http://127.0.0.1:<its port>` and refuses an inherited `PRKS_AUTH` or `PRKS_DATABASE_URL`. Every answer is checked for its meaning (status 200, JSON, a catalog of exactly the seeded Works, the UX-tour fixtures included, the requested Work, the seeded search hits, exactly the seeded non-empty tag list, a non-empty graph), so a fast 4xx/5xx or an empty answer counts as a failure, never as a fast success.

The JSON summary holds the tier, users/spawn/duration, PRKS and PostgreSQL versions, the library size, and per route the request and failure counts, p50, p95, max and requests per second. It also holds the load generator's own CPU share, so a saturated generator (above 0.8 of one CPU) is flagged instead of read as a slow server, and the `pg_stat_database` counters of the pilot's database over the run (commits, rollbacks, blocks hit/read, tuples returned/fetched). A run with a route never exercised, any failed request or a saturated generator exits 1 and lists why under `problems`. No titles, ids, bodies or URLs with values are recorded.

Locally, the pilot needs a disposable PostgreSQL 18 server that keeps running between the two commands. `scripts/test-postgres` is not suitable here: it picks a random port and removes its server when its command exits. Start one separately instead, for example:

```bash
docker run -d --rm --name prks-locust-pg -p 127.0.0.1:55432:5432 \
  -e POSTGRES_HOST_AUTH_METHOD=trust postgres:18.6
python scripts/ci/install_pinned_python.py locust
export PRKS_TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:55432/postgres PRKS_TESTING=1
python scripts/ci/locust_pilot.py --tier small --out baseline.json
# later, after a change, compare per-route p95 and throughput ratios:
python scripts/ci/locust_pilot.py --tier small --compare baseline.json
docker stop prks-locust-pg
```

`--compare` accepts only a summary of the same tier. Ratios within run-to-run noise (about ±20% on a laptop for these short runs) are not a regression; repeat both runs before drawing a conclusion. Write and contention workloads (an authenticated edit under CSRF) stay a separate, later experiment so the read-only baseline remains comparable.

## Schemathesis — bounded mutation scan of allowlisted families (fuzzing slice 2)

The [mutation scan workflow](https://github.com/Fooftilly/PRKS/blob/master/.github/workflows/schemathesis-mutations.yml) runs `scripts/ci/schemathesis_mutations.py`. Unlike the read-only pilot above, it sends writes, so it is held tighter, not looser:

- It targets only a fresh `AppServer --testing` on loopback, on its own disposable PostgreSQL database seeded with synthetic data. It refuses `PRKS_AUTH` in the calling shell.
- It scans three families: Positions, Saved Views and Publishers. Each has an exact operation allowlist in the script. Before the first request, every family's checked-in fragment (`docs/api/openapi-*.json`) is compared with its Pydantic builder, its operations with the allowlist, and then with the live `/api/openapi/<family>.json`. Any difference stops the scan before any write.
- Every request carries the instance's own `Origin` and `PRKS-Library-Epoch`, so generated input reaches the route rather than the admission refusal.
- Limits: fixed seed 855, 50 examples per operation, examples and fuzzing phases only (no stateful sequences, no coverage phase), one worker, 50 requests per second, and a 120-second timeout per family.
- It checks for server errors and for status codes, content types and response bodies the fragment does not document.
- NUL is not generated. The server refuses NUL in the request target before routing with a 400, and several of the families' read and delete operations do not document a 400.

Its first run found five ways a request got a 500 or no answer at all:

- a body that is not UTF-8;
- JSON nested past the decoder's recursion limit;
- a `\ud800` escape that reached psycopg;
- `%00` in a query or path, which reached a PostgreSQL bind. The Publisher and Tag alias routes decoded their value twice, so `%2500` did too; they now decode once, like every other query value.

`backend/server.py` now answers all of these with 400. They are pinned in `tests/pg/test_http_request_fuzz_regressions.py`, and the scan's own safeguards are in `tests/test_schemathesis_mutations.py`.

To run it locally, start a disposable PostgreSQL server and install the pins with `./.venv/bin/python scripts/ci/install_pinned_python.py schemathesis`. Then run `PATH="$PWD/.venv/bin:$PATH" PRKS_TESTING=1 PRKS_TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:<port>/postgres ./.venv/bin/python scripts/ci/schemathesis_mutations.py` (the script finds the pinned `st` on `PATH`).

Output is sanitized the same way as the pilot's, and `--debug-local` is likewise refused in GitHub Actions. The workflow is advisory. It runs manually, or when a pull request changes the scanned families, the server, the harness or the pins.

## Disposable PostgreSQL application-read readiness baseline (#765, T06)

`scripts/test-postgres --profile` with `pg_stat_statements` times domain reads, not HTTP or browser work, on 12 and 48 synthetic Works with as many linked Concepts: five Work reads (full list, browse catalog, detail, recently added, ordered batch summaries), the Research Graph concept hierarchy, and an indexed Work-to-Concept mention graph. Every warmup and measured call is checked for exact count, shape and order, and a wrong or missing result aborts before timings are published. Search, pagination, mixed Argument graphs, authenticated HTTP and real libraries remain unmeasured. The [manual profile workflow](https://github.com/Fooftilly/PRKS/blob/master/.github/workflows/pg-stat-profile.yml) runs it in CI.

Run with `python scripts/ci/install_pinned_python.py runtime` and `scripts/test-postgres --profile --sizes 12,48 --repetitions 4` (Docker with an isolated PostgreSQL 18 server, or PostgreSQL 18 binaries). The wrapper **refuses** an externally supplied `PRKS_TEST_DATABASE_URL`, and the profiler verifies the disposable cluster marker, PostgreSQL 18 and the preloaded extension before it creates or migrates any database. Output carries only HMAC query fingerprints and numbers: no SQL text, identifiers or connection strings.

The report separates each operation's first call (not a true cold start) from two warmups and four measured calls (p50/p95). These are per-call wall times on shared runners, not HTTP p50/p95 or throughput, so they support no standalone regression claim. Authenticated HTTP and browser workloads, cold starts and larger datasets are the next step under #765; do not broaden the E2E shards or add a parallel benchmark framework for them.
