# Dependencies and Vendoring

PRKS deliberately keeps its runtime dependency surface small and makes dependency state auditable.

## Python runtime dependencies

Exact runtime package pins live in `requirements.txt`. PRKS validates the running Python version and installed package versions before normal startup work.

Development-only Python tooling is declared separately in `requirements-dev.txt` (Playwright for E2E, `openapi-core` for the unit-contract preflight). Contributors who run `python run_tests.py` must install both files; a runtime-only install is enough to run the app, not the fast suite.

The application must not silently install or upgrade packages at runtime.

## Frontend/vendor dependencies

The shipped shell still loads legacy JavaScript plus vendored frontend assets and a vendored/custom PDF viewer. Vendored code must be treated as a tracked dependency rather than an invisible copy.

The Vue 3 application source lives in `frontend-app/` (its own `package.json` and lockfile), including `@tanstack/vue-query` for disposable server state and `@vueuse/core` for selective generic browser lifecycle helpers. `npm run build` writes the committed runtime bundle `frontend/vue/prks-vue.js`, with component CSS inlined into that file. Node `>=24.15.0 <25` stays a maintainer/build-time tool; the Python application serves that file and does not install npm packages.

`dependency-inventory.json` records dependency inventory used by the repository's dependency checks.

## Dependency gate

The repository contains dependency-gate logic in `backend/dependency_gate.py` and `scripts/dependency_gate.py`. It exists to catch mismatches between declarations, vendored artifacts, and the supported dependency model.

When adding/updating a dependency, update every authoritative declaration/inventory required by the gate rather than patching only the file that first fails.

## PDF viewer

The PRKS PDF viewer has its own tooling/documentation under `tools/pdf-viewer/` and vendored output under `frontend/vendor/prks-pdf-viewer/`.

Treat viewer source and built/vendor artifacts according to the repository guidance; do not hand-edit generated output when the source/build path should own the change.

## Platform-aware instructions

Installation/remediation commands shown to users should account for the platform/package-management context. In particular, PEP 668 distributions must not be instructed to bypass their package manager with unsafe global pip flags.

## Update discipline

A dependency update is complete only when:

- runtime/dev declarations are correct;
- vendored/build artifacts are refreshed where applicable;
- dependency inventory/gate passes;
- targeted tests pass;
- browser behavior is checked when the dependency affects the UI/viewer.
