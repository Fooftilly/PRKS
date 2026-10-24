# Dependencies and Vendoring

PRKS deliberately keeps its runtime dependency surface small and makes dependency state auditable.

## Python runtime dependencies

Exact runtime package pins live in `requirements.txt`. PRKS validates the running Python version and installed package versions before normal startup work.

Development-only Python tooling is declared separately in `requirements-dev.txt` (Playwright for E2E, `openapi-core` for the unit-contract preflight). Contributors who run `python run_tests.py` must install both files; a runtime-only install is enough to run the app, not the fast suite.

The application must not silently install or upgrade packages at runtime.

## Frontend/vendor dependencies

The shipped shell still loads legacy JavaScript plus vendored frontend assets and a vendored/custom PDF viewer. Vendored code must be treated as a tracked dependency rather than an invisible copy.

The Vue 3 application source lives in `frontend-app/` (its own `package.json` and lockfile), including `@tanstack/vue-query` for disposable server state and `@vueuse/core` for selective generic browser lifecycle helpers. `openapi-typescript` is a build-time types generator only: it writes committed compile-time transport types from checked-in OpenAPI artifacts and is not part of `prks-vue.js`. `npm run build` writes the committed runtime bundle `frontend/vue/prks-vue.js`, with component CSS inlined into that file. Node `>=24.15.0 <25` stays a maintainer/build-time tool; the Python application serves that file and does not install npm packages.

`dependency-inventory.json` records dependency inventory used by the repository's dependency checks.

The production Vue bundle's `frontend/vue/THIRD_PARTY_LICENSES.md` is
generated from `frontend-app/THIRD_PARTY_LICENSES.md`. The production
dependency inventory inside the latter is regenerated from
`frontend-app/package-lock.json` by `npm run build`; keep the original
upstream copyright and license **texts below the inventory** reviewed and
up to date when introducing new libraries. A dependency bump needs a build
commit, not manual editing of every inventory version row. The build rejects
missing package versions, direct-dependency version mismatches, and license
metadata outside a deliberately small reviewed SPDX identifier allowlist
before Vite clears its output. The check does not establish whether an
upstream project's declared license is accurate or whether license changes
require new notices; maintainers must still review those changes.

## Dependency gate

The repository contains dependency-gate logic in `backend/dependency_gate.py` and `scripts/dependency_gate.py`. It exists to catch mismatches between declarations, vendored artifacts, and the supported dependency model.

When adding/updating a dependency, update every authoritative declaration/inventory required by the gate rather than patching only the file that first fails.

## Distribution and licensing

The project's own code is published under AGPL-3.0-only; this does not
change the licenses of vendored third-party works. See the root
[third-party notices](https://github.com/Fooftilly/PRKS/blob/master/THIRD_PARTY_NOTICES.md) and keep each vendored
`LICENSE`, `LICENSES/`, copyright header and any required upstream notices
intact when rebuilding or distributing artifacts.

PyMuPDF is offered by its upstream publisher under AGPL terms or a
separate commercial agreement. Anyone choosing a different license for a
distribution must independently satisfy that dependency's licensing terms.
Do not present this project's AGPL license as a grant over PyMuPDF.

## PDF viewer

The PRKS PDF viewer has its own tooling/documentation under `tools/pdf-viewer/` and vendored output under `frontend/vendor/prks-pdf-viewer/`.

Treat viewer source and built/vendor artifacts according to the repository guidance; do not hand-edit generated output when the source/build path should own the change.

## Platform-aware instructions

Installation/remediation commands shown to users should account for the platform/package-management context. In particular, PEP 668 distributions must not be instructed to bypass their package manager with unsafe global pip flags.

## Maintainer dependency-update helpers

CI installs Python dependencies through `python scripts/ci/install_pinned_python.py PROFILE`
(`runtime`, `unit`, `postgres`, `plan`, or `e2e`). The helper reads only exact
`name==version` declarations in `requirements.txt` and `requirements-dev.txt`,
selects the intended subset, and installs those pins with an argument-list
subprocess and binary-only policy. The workflow files must never maintain
second copies of those version strings.

For an npm dependency PR, rebuild the affected island with
`bash scripts/refresh_dependency_artifacts.sh frontend-app`,
`frontend-vendor`, `research-graph`, or `pdf-viewer` as the argument.
This installs the directory's locked dependencies, executes its existing
deterministic build, and runs the offline repository dependency gate. Review
and commit the resulting bundle, dependency manifest, service-worker revision,
and licensing notices; do not suppress the build-consistency checks.

This is a maintainer-only operation, never part of application startup or
a blanket automatic write to untrusted pull-request branches.

## Update discipline

A dependency update is complete only when:

- runtime/dev declarations are correct;
- vendored/build artifacts are refreshed where applicable;
- dependency inventory/gate passes;
- targeted tests pass;
- browser behavior is checked when the dependency affects the UI/viewer.
