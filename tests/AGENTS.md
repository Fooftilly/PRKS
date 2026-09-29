# PRKS test agent instructions

These rules apply to test-only changes in addition to the repository-root `AGENTS.md`.

Tests encode production contracts, so a test-only change must also load the scoped instructions for the production domain it exercises:

- backend persistence, migrations, storage, backup/restore, indexing, API/domain behavior: read `backend/AGENTS.md`;
- legacy/runtime frontend, workspace bridge, settings, Saved Views, research surfaces, offline/PWA, sync, service worker, or client-cache behavior: read `frontend/AGENTS.md`;
- Vue 3 + TypeScript source/components/services under `frontend-app/`: read `frontend-app/AGENTS.md`; also read `frontend/AGENTS.md` when the tested behavior crosses the legacy/runtime bridge, workspace ownership, offline, or sync contracts;
- mixed backend/frontend contracts: read the backend guidance plus each applicable frontend scope;
- browser E2E work: also follow `tests/e2e/AGENTS.md`;
- UX Interaction Tour work: also follow `tests/ux_tour/AGENTS.md`.

Do not copy the full domain policies into tests. Load the smallest applicable scoped policy and the canonical documentation it routes to.
