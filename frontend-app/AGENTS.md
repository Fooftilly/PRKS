# PRKS Vue frontend agent instructions

These rules apply to `frontend-app/` work in addition to the repository-root `AGENTS.md`.

`frontend-app/` is the Vue 3 + TypeScript source for the frontend migration. Node/npm are maintainer build tools; the Python runtime serves committed build artifacts. The sibling `frontend/` tree still contains the shipped shell, compatibility/runtime code, offline/sync machinery, and generated Vue bundle. Load `frontend/AGENTS.md` only when a change crosses that bridge or depends on those shared runtime contracts.

## Current migration status

The route/component migration under #230/#303 is complete; controller verification and closure follow the final bridge correction. `docs/frontend-migration-boundaries.md` is the audited inventory of every remaining classic/Vue crossing, its owner on each side, and why it stays. A new crossing must be added there with its owner, or it is migration debt.

- New migrated UI belongs in `frontend-app/src/`.
- Do not create a second canonical route, workspace, durable-operation, offline, or server-state model.
- Delete obsolete compatibility code when the final consumer has crossed and the owning migration slice allows it.
- Do not add Vue Router, Pinia, another QueryClient, or another workspace snapshot merely because Vue supports them.
- TanStack Query owns disposable ordinary server-read orchestration where adopted. It does not own PRKS durable semantic operations or become an offline mutation queue.
- VueUse is for generic browser lifecycle helpers where it reduces plumbing; PRKS route/workspace/durable semantics remain explicit.

## Design and components

`DESIGN.md` is authoritative for user-visible interaction and visual behavior. Read the sections relevant to the changed component rather than loading the whole file by default.

Reuse shared components in `src/components/` and existing PRKS CSS/component families before adding another button, icon button, inline message, page state, status, card, section-header, tab, or modal family. The shared set is `PrksButton`, `PrksLinkButton`, `PrksIconButton`, `PrksField`, `PrksInlineMessage`, `PrksState`, `PrksStatusText`, `PrksSectionHeader`, `PrksScopeLine`, `PrksRelSummary`, `PrksResearchRow`, `PrksResearchSectionHead`, `PrksDisclosureButton`, and `PrksWorkCard`. Ordinary labeled Vue `.prks-btn` actions use `PrksButton` (`primary`, `secondary`, `ghost`, `danger`, `quiet-danger`). Button-styled navigation uses `PrksLinkButton` (a real `<a class="prks-btn">`, currently `secondary` only). Controls that use the `.prks-icon-btn` visual family use `PrksIconButton`. Existing icon-only controls that intentionally use the `.prks-btn` family stay raw until an appropriate contract exists. Do not move the folder-tree New folder control, the folder and group expand-collapse controls, or the playlist rename and cancel controls onto `PrksIconButton`. Do not substitute `PrksIconButton` for `PrksLinkButton` or `PrksButton`. Ordinary labeled native controls use `PrksField` (bind slot `labelledBy`/`describedBy` on the control; help then error; no `required` prop). Compact Argument target Verdict stays a raw `.prks-arg-row` child, not `PrksField`. `PrksRelSummary` renders plain text and same-app links only (the Work header folder link); it never takes markup. Comboboxes, segmented status, EasyMDE, and search-advanced rows stay specialized. Storybook is a maintainer catalog and does not define product behavior independently of `DESIGN.md`.

## Route and workspace ownership

`src/route-surface/` is an owner-scoped bridge from the current PRKS route/TabContext runtime into Vue. It is not a second router. `PrksRouteInstance` in `src/route-surface/routes.ts` is the discriminated identity of one mounted owner, imported from the feature route types. It does not parse hashes; each member's `name` is a `PrksRouteName` from `src/routing/route-model.ts`, the only hash parser and route registry (`parseRoute` returns the `PrksParsedRoute` union keyed by name). Classic JS reaches that module only through `frontend/js/route-model.js` and the aliases `navigation.js` publishes. Work statuses and the People role subset are owned by `src/domain/work-status.ts` and `src/domain/people-roles.ts`; the parser imports them, and feature code imports them from there, never from routing and never as a local copy. The coordinator paints every route feature through `prksPresentVueRoute(ctx, contentDiv, feature, fields)` in `app.js`, which owns the host (fresh, or reused for the retained features in `PRKS_RETAINED_VUE_ROUTE_FEATURES`) and the request envelope (feature, owner, shell), and delivers that host-local request through `prksVuePresentRoute`; the feature presenter validates and normalizes `fields`. Only the Research Graph and Processing keep thin wrappers for their pre-paint steps. The presenter registered for that feature writes the owner's `PrksRouteInstance`; per-feature `window.prksVuePresent*` route entry points are retired. Work detail is the `work` route-surface member. `prksMountWorkDetail` is its mount entry and paints through the shared route presenter. Panel, metadata, private notes, Research Notes, and PDF annotation bridges stay. The coordinator dismisses through `prksVueDismissRoute`, registered beside `prksVuePresentRoute`. An owner holds one route-surface session, so that one entry drops whichever route feature the owner painted; per-feature `window.prksVueDismiss*` route entries are retired. Owner resources a route mounts are released by that route component's unmount, not by a feature dismiss hook: Processing releases its preview iframe and resize listener in `ProcessingFilesRoute.vue` `onBeforeUnmount`. Route-local runtime must stay owner-scoped; stale generations or asynchronous completions must not mutate a replaced route owner. The search query codec is `src/features/search/codec.ts`. Legacy JS uses `prksSearchQueryCodec` from `frontend/js/search-query-codec.js`. Vue imports the module.

`src/workspace/` owns the typed canonical workspace model and pure transforms. `src/workspace-shell/` renders a detached projection and sends intents. `frontend/js/workspace-tabs.js` is the permanent live effect coordinator for that state; the shell reaches it through the hooks recorded in `docs/frontend-migration-boundaries.md`. Do not introduce a parallel store or second drag/state system. `src/lifecycle/tab-leave.ts` is the serialized TabContext leave decision. It stays free of Person drafts, Work drafts, PDF sync, dialogs, and DOM; feature probes answer those. `src/lifecycle/owner-resource.ts` is the external resource lifetime. TabContext hosts it. Research Graph is registered and is not warm-suspendable. PDF is the suspendable `pdf` registration; `initPdfViewerForWork` captures its ticket before deferred setup. Work role, Work tag, Work source, Work metadata, Folder tag, and private notes (`privateNotesEditor`) sessions are non-suspendable registrations. Research Notes (`workNotes`, pane-local EasyMDE) is suspendable with the ticket captured when `mountWorkDetail` begins; warm park keeps it beside the PDF and cold release destroys it. Production captures the owner ticket and registers that session before subscriptions or async prepare. Editor liveness includes same-generation resource identity (`getResource(kind) === state`). Warm park disposes those sessions and keeps the PDF runtime. An owner-scoped acknowledgement subscription survives on the tab and patches its Work entity while the editor session is absent; focused-panel refresh reconstructs the sessions the focused owner needs. Durable role, tag, source, and metadata writes stay on their existing owners. The registry does not replace the leave decision. `src/lifecycle/editor-recovery/` is browser-local editor draft recovery (#466), emitted as `frontend/js/editor-recovery.js` (`prksEditorRecovery`): its own IndexedDB database `prks-editor-recovery-v1`, a fresh `pageInstanceId` per load, a claimed per-tab `runtimeId`, per-lineage `draftId` ownership, and a page-level coalescing writer registry that TabContext timers do not own. It never writes `prks-local-v1` and never decides an acknowledgement. Research Notes is its first consumer: `works.js` reports each session edit to one writer per session entry, and `detail-lifecycle.ts` calls `prksRestoreResearchNotesRecovery` before reading notes; the pure `research-notes.ts` plan restores one complete orphaned or closed-tab draft on a server-verified base with no live owner and a compatible queue, and leaves every other draft untouched for review. A closed tab is proven by its lock disappearing or by its final-pagehide record (`wasPageClosed`); silence alone is never proof. The notice and Review dialog live in `src/features/editor-recovery/` and `src/features/work/research-notes-recovery.ts`; every Review action goes back through the classic adapter, which re-plans, adopts by compare-and-swap, and refuses stale actions. That adapter is `frontend/js/work-note-recovery.js` (`prksCreateWorkNoteRecovery`), shared by Research Notes (`works.js`) and Work Reminders (`ui.js`, #474) through a kind descriptor; add a Work note kind there, not a copy. A confirmed Work deletion (#533) reaches recovery only through `work-lifecycle-state.js`: it releases each kind's sessions of that Work no pane shows, or every one on a folded creation (`prksForgetDeletedWorkNotes`) and then calls the runtime's `cleanupDeletedEntity`, which removes by compare-and-set and never touches a live editor's lineage; a requested delete changes nothing.

When a change touches the classic coordinator, offline runtime, service worker, durable operation families, or shared browser lifecycle, also load the relevant sections of `frontend/AGENTS.md` and, for offline/sync work, `docs/agent-context/sync-map.md` plus `docs/agent-rules/offline-pwa.md`.

## API and state boundaries

Use `src/api/` and the typed PRKS service/API boundary rather than issuing arbitrary transport calls from components. Keep generated/transport DTOs separate from PRKS domain semantics.

OpenAPI owns wire schema. `src/api/generated/` holds one compile-time module per checked-in family artifact (`docs/api/openapi-*.json`), produced by `npm run openapi:types` and checked by `npm run openapi:check`. The current modules are publishers, saved views, processing files, performance diagnostics, and positions. `prksApiRequest` and the hand-written feature API modules remain the transport and service layer. Do not generate fetch clients, TanStack Query hooks, mutations, cache logic, or domain services from OpenAPI. Generated types do not replace runtime response parsers. Query keys, cancellation, retry, invalidation, reconciliation, telemetry, and offline/durable semantics stay PRKS-owned. A combined `/api/openapi.json` belongs to #45; this generator does not create it. Positions Vue types in `src/features/positions/types.ts` are effective-state view models and are not aliases of `src/api/generated/positions.ts`. Processing Files keeps a narrower card PATCH body (`ProcessingFileUpdate`) beside the permissive generated request schema.

Accepted target architecture in #310/#311 means the frontend must not assume forever that PRKS is single-owner, SQLite-backed, tied to one host filesystem path, or served by one process. At the same time, do not implement future account/PostgreSQL/storage behavior in an unrelated frontend slice. Consume explicit API/application contracts as they land.

## Toolchain and validation

Use the Node version pinned by `frontend-app/package.json` / repository CI.

During implementation prefer the narrowest relevant checks:

```bash
cd frontend-app
npm ci --ignore-scripts
npm run typecheck
npm run lint
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
