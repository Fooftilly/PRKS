# PRKS Vue frontend agent instructions

These rules apply to `frontend-app/` on top of the root `AGENTS.md`.

`frontend-app/` is the Vue 3 + TypeScript source. Node/npm are maintainer build tools; the Python runtime serves committed build output. The sibling `frontend/` tree holds the shipped shell, classic runtime, offline/sync machinery and the built Vue bundle. Load `frontend/AGENTS.md` only when a change crosses that bridge or depends on its runtime contracts.

## Ownership

- `docs/frontend-migration-boundaries.md` lists every remaining classic/Vue crossing with its owner on each side. A new crossing goes there, or it is migration debt. Delete compatibility code once its last consumer has crossed.
- New UI goes in `frontend-app/src/`. The Folder, Person, Person Group, Playlist, New File, Link Person to Work and Edit Profile Using Template bodies (`src/features/create-forms/`) the Saved View body (`src/features/saved-views/`) and the restore confirmation (`src/features/settings/RestoreConfirmForm.vue`) render inside `ModalHost` as `ModalDialog` shells (`src/modal/`), which own open state; `ui.js` `openModal` / `closeModals` reach them through `window.prksModalHost`. Settings keeps its own classic shell with a Vue body.
- Never add a second route, workspace, durable-operation, offline or server-state model. No Vue Router, Pinia, second QueryClient or second workspace snapshot.
- TanStack Query orchestrates disposable server reads where adopted; it never owns durable operations or acts as an offline queue. VueUse is for generic browser lifecycle only.
- Settings content is `src/features/settings/`; `#settings-modal`, its open/close/Escape/focus return, device and library persistence (`/api/settings`), backup, restore and cache operations stay classic.

## Design and components

`DESIGN.md` is authoritative for user-visible behavior; read the sections for the component you change. Storybook is a maintainer catalog, never a separate source of product behavior.

- Reuse `src/components/` before adding another button, icon button, message, state, status, card, header, tab or modal family: `PrksButton` (`primary`, `secondary`, `ghost`, `danger`, `quiet-danger`), `PrksLinkButton` (a real `<a class="prks-btn">`), `PrksIconButton` (the `.prks-icon-btn` family only), `PrksField`, `PrksInlineMessage`, `PrksState`, `PrksStatusText`, `PrksSectionHeader`, `PrksScopeLine`, `PrksRelSummary`, `PrksResearchRow`, `PrksResearchSectionHead`, `PrksDisclosureButton`, `PrksWorkCard`.
- Deliberate exceptions: New Folder Save (`#save-folder-btn`) and New File Create File (`#save-work-btn`) stay native buttons (their opening guard needs a same-turn reopen-and-save that `PrksButton`'s busy state would swallow). Icon-only controls in the `.prks-btn` family (folder-tree New folder, expand/collapse, playlist rename/cancel) stay raw. The Argument target Verdict select stays a raw `.prks-arg-row` child. Comboboxes, segmented status, EasyMDE and advanced-search rows stay specialized.
- `PrksField`: bind the slot's `labelledBy`/`describedBy` on the control; help then error; no `required` prop. `PrksRelSummary` renders plain text and same-app links only, never markup.
- `src/modal/ModalHost.vue` is the one dialog presenter (confirm, alert, prompt, unsaved), installing `window.prksModalHost`; classic dialog helpers delegate to it. It also renders the create-form `ModalDialog` shells and owns which is open, each opening's baseline and readiness (`src/modal/dialogs.ts`). A form registers its own opening, close and Escape steps with `useDialogLifecycle`; do not add per-form hooks to `ui.js`. The remaining classic shells (Settings and the master add chooser; see the dialog inventory in `docs/frontend-migration-boundaries.md`) and leave decisions stay outside it.
- `src/palette/CommandPalette.vue` only paints `prksCommandPaletteView` and sends intents; `command-palette.js` stays the only catalog, search and dispatch owner.

## Routes and workspace

- `src/routing/route-model.ts` is the only hash parser and route registry; classic JS reaches it through `frontend/js/route-model.js`. Work statuses and People roles come from `src/domain/`, never from routing or a local copy.
- `src/route-surface/` is an owner-scoped bridge from the TabContext runtime into Vue, not a router. `PrksRouteInstance` (`routes.ts`) is one mounted owner's identity. The coordinator presents every feature through `prksPresentVueRoute` (`app.js`) and dismisses through `prksVueDismissRoute`; feature presenters validate their fields. Owner resources are released by the route component's unmount. Stale generations and late completions never touch a replaced owner.
- Work detail is the `work` member (`prksMountWorkDetail`); the video pane is Vue (`WorkVideoPane`). Panel, metadata, notes and PDF annotation bridges stay classic.
- `src/workspace/` is the typed canonical workspace model and pure transforms; `src/workspace-shell/` renders a projection and sends intents; `frontend/js/workspace-tabs.js` is the live effect coordinator. `src/app-shell/` paints sidebar, ribbon and create menu from the shell snapshot; active href, origin and TabContext stay in `navigation.js`.
- `src/lifecycle/tab-leave.ts` is the serialized leave decision and knows nothing about drafts, PDF sync, dialogs or DOM; feature probes answer those. `src/lifecycle/owner-resource.ts` is the external resource lifetime TabContext hosts (rules in `frontend/AGENTS.md` "TabContext owns route runtime").
- `src/lifecycle/editor-recovery/` is browser-local draft recovery (built to `frontend/js/editor-recovery.js`). Its storage is scoped per signed-in user (`recoveryNames(scopeKey)`) and nothing is stored without a confirmed scope. It never writes the operation queue (`prks-local-v2:<scope>`) and never decides that a body was saved. It restores automatically only a complete draft on a server-verified base with no live owner; a closed tab is proven by its lock disappearing or its pagehide record, never by silence. Every Review action goes through `frontend/js/work-note-recovery.js`, which re-plans and adopts by compare-and-swap; add a Work note kind there, not a copy. Deleted-Work cleanup arrives only through `work-lifecycle-state.js` and removes only lineages whose owner is this page or proven gone. The module docstrings hold the details.

For changes to the classic coordinator, offline runtime, service worker, durable families or shared browser lifecycle, also load `frontend/AGENTS.md`; for offline/sync work, `docs/agent-context/sync-map.md` and `docs/agent-rules/offline-pwa.md`.

## API and state boundaries

- Components call `src/api/` and the typed service layer, never raw transport. Keep transport DTOs separate from PRKS domain types.
- OpenAPI owns wire schema. `src/api/generated/` has one module per `docs/api/openapi-*.json` (`npm run openapi:types`, checked by `npm run openapi:check`). Never generate clients, hooks, cache logic or domain services from OpenAPI, and generated types never replace runtime response parsers. Feature view models (for example `src/features/positions/types.ts`) are not aliases of generated types.
- #310/#311: do not assume PRKS stays single-owner, tied to one host path, or one process, but do not build future account or storage behavior in an unrelated slice; consume API contracts as they land.

## Toolchain and validation

Use the Node version pinned in `package.json`. Prefer the narrowest checks:

```bash
cd frontend-app
npm ci --ignore-scripts
npm run typecheck
npm run lint
npm test
npm run build
```

Build Storybook only when story work needs it. Browser E2E follows `tests/e2e/AGENTS.md`.

The bundle in `frontend/vue/` and bridge scripts in `frontend/js/` are generated from this package: never hand-edit them; rebuild and commit them when the build contract requires.

## Cross-boundary changes

Preserve the owning contract instead of reimplementing it in Vue: UI in `DESIGN.md`; runtime, offline and workspace bridge in `frontend/AGENTS.md`; offline/sync in `docs/agent-rules/offline-pwa.md`; API architecture in #45, #179, #310; the Work/Manifestation/Asset model in #60 and `docs/work-identity-model.md`. Prefer a small typed adapter at a boundary over duplicating domain, routing, storage or sync logic.
