# PRKS Vue frontend agent instructions

These rules apply to `frontend-app/` work in addition to the repository-root `AGENTS.md`.

`frontend-app/` is the Vue 3 + TypeScript source for the frontend migration. Node/npm are maintainer build tools; the Python runtime serves committed build artifacts. The sibling `frontend/` tree still contains the shipped shell, compatibility/runtime code, offline/sync machinery, and generated Vue bundle. Load `frontend/AGENTS.md` only when a change crosses that bridge or depends on those shared runtime contracts.

## Current migration status

The Vue/legacy bridge is transitional under #230/#303. Preserve current behavior while the migration is active, but do not encode the bridge itself as permanent architecture.

- New migrated UI belongs in `frontend-app/src/`.
- Do not create a second canonical route, workspace, durable-operation, offline, or server-state model.
- Delete obsolete compatibility code when the final consumer has crossed and the owning migration slice allows it.
- Do not add Vue Router, Pinia, another QueryClient, or another workspace snapshot merely because Vue supports them.
- TanStack Query owns disposable ordinary server-read orchestration where adopted. It does not own PRKS durable semantic operations or become an offline mutation queue.
- VueUse is for generic browser lifecycle helpers where it reduces plumbing; PRKS route/workspace/durable semantics remain explicit.

## Design and components

`DESIGN.md` is authoritative for user-visible interaction and visual behavior. Read the sections relevant to the changed component rather than loading the whole file by default.

Reuse shared components in `src/components/` and existing PRKS CSS/component families before adding another button, status, card, section-header, tab, or modal family. Storybook is a maintainer catalog and does not define product behavior independently of `DESIGN.md`.

## Route and workspace ownership

`src/route-surface/` is an owner-scoped bridge from the current PRKS route/TabContext runtime into Vue. It is not a second router. `PrksRouteInstance` in `src/route-surface/routes.ts` is the discriminated identity of one mounted owner, imported from the feature route types. It does not parse hashes. The coordinator paints every route feature through `prksPresentVueRoute(ctx, contentDiv, feature, fields)` in `app.js`, which owns the host (fresh, or reused for the retained features in `PRKS_RETAINED_VUE_ROUTE_FEATURES`) and the request envelope (feature, owner, shell), and delivers that host-local request through `prksVuePresentRoute`; the feature presenter validates and normalizes `fields`. Only the Research Graph and Processing keep thin wrappers for their pre-paint steps. The presenter registered for that feature writes the owner's `PrksRouteInstance`; per-feature `window.prksVuePresent*` route entry points are retired. Work surfaces keep their own window bridges. The coordinator dismisses through `prksVueDismissRoute`, registered beside `prksVuePresentRoute`. An owner holds one route-surface session, so that one entry drops whichever route feature the owner painted; per-feature `window.prksVueDismiss*` route entries are retired. Owner resources a route mounts are released by that route component's unmount, not by a feature dismiss hook: Processing releases its preview iframe and resize listener in `ProcessingFilesRoute.vue` `onBeforeUnmount`. Route-local runtime must stay owner-scoped; stale generations or asynchronous completions must not mutate a replaced route owner. The search query codec is `src/features/search/codec.ts`. Legacy JS uses `prksSearchQueryCodec` from `frontend/js/search-query-codec.js`. Vue imports the module.

`src/workspace/` owns the typed canonical workspace model and pure transforms. `src/workspace-shell/` renders a detached projection and sends intents. The classic runtime coordinator remains responsible for the live effectful state while #303 is incomplete. Do not introduce a parallel store or second drag/state system. `src/lifecycle/tab-leave.ts` is the serialized TabContext leave decision. It stays free of Person drafts, Work drafts, PDF sync, dialogs, and DOM; feature probes answer those. `src/lifecycle/owner-resource.ts` is the external resource lifetime. TabContext hosts it. Research Graph is registered and is not warm-suspendable. PDF is the suspendable `pdf` registration; `initPdfViewerForWork` captures its ticket before deferred setup. Work role, Work tag, Work source, Work metadata, Folder tag, and private notes (`privateNotesEditor`) sessions are non-suspendable registrations. Research Notes (`workNotes`, pane-local EasyMDE) is suspendable with the ticket captured when `renderWorkDetails` begins; warm park keeps it beside the PDF and cold release destroys it. Production captures the owner ticket and registers that session before subscriptions or async prepare. Editor liveness includes same-generation resource identity (`getResource(kind) === state`). Warm park disposes those sessions and keeps the PDF runtime. An owner-scoped acknowledgement subscription survives on the tab and patches its Work entity while the editor session is absent; focused-panel refresh reconstructs the sessions the focused owner needs. Durable role, tag, source, and metadata writes stay on their existing owners. The registry does not replace the leave decision.

When a change touches the classic coordinator, offline runtime, service worker, durable operation families, or shared browser lifecycle, also load the relevant sections of `frontend/AGENTS.md` and, for offline/sync work, `docs/agent-context/sync-map.md` plus `docs/agent-rules/offline-pwa.md`.

## API and state boundaries

Use `src/api/` and the typed PRKS service/API boundary rather than issuing arbitrary transport calls from components. Keep generated/transport DTOs separate from PRKS domain semantics.

Accepted target architecture in #310/#311 means the frontend must not assume forever that PRKS is single-owner, SQLite-backed, tied to one host filesystem path, or served by one process. At the same time, do not implement future account/PostgreSQL/storage behavior in an unrelated frontend slice. Consume explicit API/application contracts as they land.

## Toolchain and validation

Use the Node version pinned by `frontend-app/package.json` / repository CI.

During implementation prefer the narrowest relevant checks:

```bash
cd frontend-app
npm ci --ignore-scripts
npm run typecheck
npm test
npm run build
```

Run Storybook/build-storybook when component/story work requires it. Browser E2E follows the repository and `tests/e2e/AGENTS.md` policy: do not iterate with the full browser suite; use it only when the behavior cannot be proven below the browser layer or for final targeted/full-gate validation.

## Generated / shipped artifacts

The committed production bundle is generated from this package and served from `frontend/vue/`; some typed bridge artifacts are emitted into `frontend/js/`. Do not hand-edit generated output when the source lives in `frontend-app/`. Rebuild with the documented package scripts and keep generated artifacts in sync when the owning build contract requires them.

## Cross-boundary changes

A Vue change that modifies semantics owned elsewhere must preserve the owning contract rather than reimplement it:

- UI/interaction: `DESIGN.md`
- current runtime/offline/workspace bridge: `frontend/AGENTS.md`
- offline/sync: `docs/agent-context/sync-map.md` and `docs/agent-rules/offline-pwa.md`
- API/application architecture: #45, #179, #310
- Work/Manifestation/Asset design: #60 and `docs/work-identity-model.md`, interpreted with #310/#311 for future persistence/storage compatibility

Prefer a small typed adapter at a boundary over duplicating domain, routing, storage, or synchronization logic inside Vue.
