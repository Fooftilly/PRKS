# PRKS frontend agent instructions

This file governs the shipped `frontend/` runtime. It applies on top of the root `AGENTS.md`. Vue/TypeScript source in `frontend-app/` has its own `frontend-app/AGENTS.md`; load both only when a change crosses the bridge, shared workspace/runtime ownership, or offline/sync behavior. Remaining classic/Vue crossings are listed with their owners in `docs/frontend-migration-boundaries.md`; add any new crossing there. `DESIGN.md` is authoritative for UI and interaction; read the sections for the component you change.

## Vue application

### Build and ownership

- `frontend-app/` is the Vue 3 + TypeScript source (Vite, `vue-tsc`, Vitest; Node `>=24.15.0 <25` for maintainers only). The Python runtime serves the committed bundle `frontend/vue/prks-vue.js` and never runs Vite. Component CSS is inlined into that bundle.
- `frontend/js/` keeps the classic runtime owners: route coordinator, durable/offline operations, the Settings and Create New Entity modal shells, the shared backdrop (`openModal` / `closeModals`), folder tree, PDF, graph and workspace effects. Do not rewrite them only because they are classic JavaScript. Migrated UI goes in `frontend-app/src/` and must not add a second workspace or application state model.
- Do not add Vue Router or Pinia. Use VueUse only for generic browser lifecycle (listeners, observers, timers), never for PRKS route, workspace, durable or preview semantics.
- TanStack Query is used only for performance diagnostics, read-only deployment settings, the read-only storage status, Publishers and Saved View records. There is one QueryClient (`prksQueryClient()`); route surfaces pass it to `useQuery`. Query keys come only from `prksQueryKeys` (`frontend-app/src/query/keys.ts`): one domain prefix per family, never a URL. Every online write that was sent invalidates its domain's `all()` prefix, on success or failure, instead of editing cached rows. The query cache is never persisted and is not an offline queue.
- The Settings modal body is Vue, teleported into `#prks-settings-root`; `#settings-modal` stays the shell for open, close, Escape and focus return. The shared confirm/alert/prompt/unsaved dialogs are the Vue modal host.

### Routing and route surfaces

- One typed route model, `frontend-app/src/routing/route-model.ts`, owns the hash parser, canonical hashes, route names/params and `ROUTE_META`. It is built to `frontend/js/route-model.js` (`prksRouteModel`), which `navigation.js` aliases onto `window`. Never add a second parser, registry or copy of its lists. Work status and People role vocabularies are domain values (`frontend-app/src/domain/`), imported by the parser, not the other way round.
- Every registry name except `unknown` has exactly one `case` in the `app.js` `switch (route.name)` dispatch (`tests/test_frontend_route_model.py`).
- TabContext, Main/Secondary ownership and tab routing stay in the classic runtime. `frontend-app/src/route-surface/` bridges a route into one Vue view per owner: `PrksRouteInstance` holds route name, params, canonical hash, Main ownership and generation. `readRouteSurface` sees only its own owner. Presentation goes through `prksDeliverVueRoute` / `prksVuePresentRoute`; there are no per-feature `window.prksVuePresent*` entry points. Unrecognized hashes stay classic.
- Route-local runtime never lives on `window`. Generations are never compared across TabContexts. A Vue route view renders content and never mutates shell state.
- Shell chrome (`frontend-app/src/app-shell/`: sidebar, ribbon, create menu) reads the snapshot `prksSyncSidebarActive` publishes through `prksNoteShellChrome`. Disclosure preferences stay `prks.nav.*Expanded` via `prksWriteNavExpandedPref`.
- The search query codec is `frontend-app/src/features/search/codec.ts`; classic callers use the `prksSearchQueryCodec` bridge (`frontend/js/search-query-codec.js`).

### The route-surface pattern

Most library routes follow one pattern: the classic coordinator in `app.js` resolves the effective, offline-aware projection (pending creates, deletes, field edits and overlays) for the owning TabContext and generation, and the Vue feature in `frontend-app/src/features/<route>/` paints it and sends typed intents to the existing durable APIs. In that pattern Vue never fetches the projection, reads the durable queue, uses TanStack Query for it, or keeps a feature store. Routes that follow it: Progress, Recent, Concepts, Positions, Arguments/Stances, People and role views, Person Groups, Playlists, Folder detail, File types, Tags, Search.

Route-specific rules:

- Work cards use the shared `PrksWorkCard`. Each card collection releases its thumb preview and lazy thumbs when its source changes and on unmount.
- Index "New" actions record the owning TabContext; the modal then navigates that still-valid owner with `{ tabId }` and does not read `location.hash`. Ribbon and command-palette creation open the modal without an origin. A stale origin never navigates a replaced pane.
- Reconnect refresh skips owners in edit mode (`ctx.ui.argumentEditing`, `personDetailEditing`, `playlistEditing`).
- Concepts: the Definition and Aliases editors read their base (text plus revision from the one `concept-state` entry) when they open, never at Save, and Save returns `changed_elsewhere` when PRKS no longer holds the text the editor showed. A stale aliases Save preserves another tab's renames and aliases until the user chooses. Rename and parents still read their base at Save.
- Concepts, Positions and Arguments share research-index search and markdown fallback (`frontend-app/src/research-index/`); each route owns its copy, ids and intents. Do not force Playlists through it. Generated OpenAPI types (`frontend-app/src/api/generated/`) are transport types, never the effective projection type.
- People: the profile form edits one `personProfileDraft` in the focused Person pane; the sidebar is a summary. `prksRemoveWorkRoleLink` is the one Work-role unlink helper for Person and Work pages.
- Folder detail: layout, hierarchy nav and its refresh stay in `frontend/js/components/folders.js` (`prksCommitFolderDetailSurface`), which publishes a typed tree snapshot that Vue renders. Tree expand/collapse and the filter live on `ctx.ui.folderTree`, one per pane.
- Tags: alias add/remove stay raw requests in `frontend/js/components/tags.js` (aliases are not part of a cached tag list); delete and merge go through `prksDeleteTagDurably` / `prksMergeTagDurably`. Each intent rechecks the owner after confirm and before the write and repaint. A failed post-write reload keeps the existing list and shows the refresh failure, never the empty state.
- Search and Saved View detail are Main-only and share one result boundary: the coordinator runs `prksEffectiveSearchResults`; the server decides membership and results are never cached or stored with a view.
- Main-only, online-only routes (offline shows `prksOfflineRenderUnavailable`): Search, Saved Views, Publishers, Processing.
- File types (`#/types`, `#/types/:docType`) are Main-only and render from the cached works-browse snapshot offline; only a missing snapshot shows `prksOfflineRenderUnavailable`.

Routes that own their server state through `prksApiRequest` and TanStack Query:

- Saved Views: `frontend-app/src/features/saved-views/` owns records end to end through the typed client and `records.ts`, which every surface uses; classic callers reach it as `window.prksSavedViewRecords`. Reads always ask the server; a 404 detail is `null`. Writes are TanStack mutations (no retry) behind `prksOfflineGuardMutation`. An open detail follows writes and re-resolves its route only when its record changed, was deleted or cannot be read. The dialog is the Vue `SavedViewForm`, rendered by `ModalHost` as the body of the `#saved-view-modal` `ModalDialog`. The full dialog inventory is in `docs/frontend-migration-boundaries.md`.
- Publishers: `frontend-app/src/features/publishers/` with the typed client, list under `prksQueryKeys.publishers.inUse()`. No durable queue. A failed refetch keeps rows and shows the refresh failure.
- Read failures report once to `/api/client-errors` under the query's `meta.clientErrorSource`, never with an id or body.

Processing (`#/processing-files`) is the PostgreSQL upload queue (`frontend-app/src/features/processing-queue/`), mounted only once the session confirms PostgreSQL content mode.

### Work detail

- The coordinator's `case 'work'` resolves availability, lifecycle and provenance, then publishes a typed projection (`frontend-app/src/features/work/`, built to `frontend/js/work-route-projection.js`) on the originating TabContext only while its generation is current. The editor Work (`ctx.setEntity('work', …)`) is the acknowledged record plus applied source and placement; `effectiveWork` adds metadata and role overlays and is never stored as the entity.
- `mountWorkDetail` (`detail-lifecycle.ts`) is the painter. It captures the Research Notes ticket before any await; a rejected generation does not paint. The Vue shell's unmount is a TabContext cleanup that runs after the PDF runtime and Research Notes disposers.
- Right-panel editors are owned by the TabContext and fenced: every intent rechecks owner, generation and Work immediately before its durable write, and a failed fence is a silent stale no-op. Alerts, busy state, mode changes and modal closes belong to the opening that started them.
  - Metadata: `WorkMetadataEditor` keeps a Vue-local draft and an explicit baseline (`workMetaEditSession`) and saves through `store.saveWorkMetadataFields` / `prksSaveWorkSource`. The draft is never the server base and `effectiveWork` is never the baseline.
  - Roles: the role modal records opener, generation and Work when it opens; `prksLinkPersonFromRoleModal` keeps those checks. Writes go through `prksSaveWorkPersonRoleDurably`.
  - Tags: `store.coalesceWorkTag`, rechecking `owns(ctx, state)` just before the call.
  - Folder/Playlist membership: the `prksFileWorkInFolder` / `prksSetWorkPlaylist` wrappers carry the session fence; the durable setters do not check panel ownership. New Folder/Playlist from the panel may create the record after the session went stale, but never attach it.
  - Reminders (`WorkPrivateNotes`) and Research Notes keep their unsaved session on the owning TabContext, so Main and Secondary never share a draft, and a save started on Work A never paints onto Work B. Notes save through `prksSaveWorkNoteDurably`. A `scope_busy` refusal keeps the body `blocked` and retries once no unsettled row is left; a blocked body that can no longer be proven against its base is shown as not saved, never as saved. The full note-session contract is in `docs/agent-rules/offline-entity-surfaces.md` ("Note editor sessions").
- Concept insertion uses `prksFormatConceptMarkup` (`[[concept:Name]]`, or `[[concept-encoded:…]]` for names with delimiters). The Python and JS research-markup parsers share fixtures; do not add a third parser in Vue.

### Work PDF

- `works-pdf.js` owns viewer integration and annotation-persistence orchestration; `pdf-work-runtime.js` owns the per-tab PDF runtime, its search, popup and drawer sessions; `pdf-annotation-state.js` and `store.savePdfAnnotation` own the durable annotation write. No change may add a second persistence or durable-operation owner. Annotation metadata semantics belong to #61.
- `initPdfViewerForWork` owns the suspendable `pdf` resource on `ctx.resourceRegistry` and runs only for the current generation of a `pdf` Work with `file_path`; stale setup never creates a viewer. `frontend-app/src/features/work/pdf-adapter.ts` is the Vue read/intent boundary and stores no PDF state.
- Search, popup and drawer changes never call `createPrksPdfViewer`, change `viewerSetupToken`, or change zoom or reading position. Results apply only while `prksPdfSearchStill` matches. Main and Secondary keep separate sessions.
- Drawer placement is `overlay`, `pinned` or `sheet` (forced mobile layout or ≤900 px). Pinned narrows the viewer box and calls `pdf.resize()`; width is clamped and remembered in device-local `prks.pdf.annotationDrawer`. The width handle is `prksBindDrawerWidthSeparator` (see "Workspace navigation").
- `prksRequest(work.file_path)` is offline byte caching, not annotation persistence. The pending-annotation leave confirm stays on the route leave (see "Interaction feedback").

### Primitives, Storybook and lint

- Reuse the primitives in `frontend-app/src/components/` (`PrksButton`, `PrksLinkButton`, `PrksIconButton`, `PrksField`, `PrksInlineMessage`, `PrksState`, `PrksStatusText`, `PrksSectionHeader`, `PrksWorkCard`, …) before adding another button, message, state or heading component. They keep the existing `.prks-*` classes, which stay in `frontend/css/style.css` while classic JS uses them; do not split or copy that stylesheet. Icon-only controls that intentionally use `.prks-btn` (folder-tree New folder, expand/collapse, playlist rename/cancel) stay raw.
- Storybook is build-time only (`npm run storybook`, `npm run build-storybook` in `frontend-app`); never commit `storybook-static/` or put its output in `frontend/`. The browser story runner is deferred until an addon supports this Vitest major; do not add a Playwright story runner. Storybook MCP is user-level setup (`frontend-app/README.md`).
- Vue/TS lint is `npm run lint` in `frontend-app`; the root ESLint config is the legacy JavaScript bug-rule set.

### Reachability

The Vue HTTP transport is a real `fetch` boundary for PRKS reachability, with the same rules as the classic coordinator (see "Client request coordinator"): any resolved response is success; a non-abort transport failure counts only after retries are exhausted; managed PDF GETs, aborts, HTTP error envelopes and domain failures are not connectivity changes. Capture `prksOfflineRuntimeGeneration()` at actual fetch start and pass it to `prksOfflineNoteRequestSuccess(token)`, so an older response cannot reopen a newer disconnect. Never erase attempt history after dispatch.

## Cross-boundary bulk mutations

Use the transactional backend bulk mutation wherever one exists; never substitute per-Work writes or give up atomic validation.

## Command palette

- Commands are explicit allowlisted actions; never execute query text as JavaScript or a dynamic method name.
- Navigation commands use prksNavigate() and canonical hash routes. Reuse existing modals and handlers instead of palette-only forms.
- Global shortcuts never fire while the user is typing or another modal owns focus.
- Palette queries are ephemeral UI state: never persisted or logged. `CommandPalette.vue` paints from `prksCommandPaletteView`.
- A command that depends on transient palette state (for example `state.splitPlacement`) snapshots it before `closePalette()`, which clears it.

## Workspace navigation

### Navigation

- Internal navigation uses `prksNavigate`. Never write `window.location.hash` from feature code or use `window.open` for internal routes.
- Normal navigation targets the originating TabContext (the link's owner, or the focused context for palette/global commands); sidebar chrome navigates Main. Ctrl/Cmd-click and middle-click open a background tab. Alt-click and `target: "tile"` open a Secondary leaf when the route is tile-capable.
- The link layer intercepts clicks in the capture phase, so `handleNavEvent` must bow out entirely (no `preventDefault`, no `stopPropagation`) for a destination marked `aria-disabled="true"`. The owning component then refuses and explains from a bubble-phase `click`/`auxclick` handler. Offline pages rely on this.
- Clicks inside the shared `#panel-content` resolve their owner from `panel.dataset.prksOwnerTabId` (checked against a live TabContext), never from Main, focus or `location.hash`. Main is the fallback only for shell-global links.
- URL always represents Main. Secondary routes never touch History; Make main uses `replaceState`. Tab switching never creates a contextual Back origin. Do not add route-level global runtime state.

### Model and roles

- The canonical workspace model is `frontend-app/src/workspace/` (built to `frontend/js/workspace-model.js`), the only tree and structural-state implementation. Its pure planners (`planCloseTab`, `planSplitLeaf`, `planMakeMain`, …) and tree helpers (`splitLeaf`, `removeLeaf`, `replaceTabId`, `normalizeTree`, …) are the only way to change structure; never mutate `secondaryTree` or `state.tabs` directly.
- `workspace-tabs.js` holds the one live workspace and coordinates effects (leave preflight, History, persistence, mount/park/destroy). It publishes a frozen projection through `prksWorkspaceSubscribe`; subscribers own no state. Do not add a Vue copy of the snapshot, a second tab list or a second focus/Main owner.
- `frontend-app/src/workspace-shell/` is presentation only: tab strip, Main frame and the recursive Secondary tree, sending intents back. Content hosts (`workspace-hosts.js`, keyed by `tabId`) are reparented, never recreated, except on close. After both teleports the shell calls `prksWorkspaceOnShellCommit` for focus restore, overflow measurement, then reveal.
- Main is the single root pane and never splits. Secondary is a recursive tree: `null`, a bare `{ type: "leaf", tabId }`, or `{ type: "split", id, axis, ratio, first, second }`. A tab appears at most once; Main's tab never appears in it. Split ids are stable in-memory keys, never persisted. Removing a leaf normalizes the tree (single-child splits collapse; no leaves means `null`).
- Tile-capable routes: works, people, concepts, positions, arguments, playlists, folder detail. Do not add more as a side effect of other work.
- At most `PRKS_MAX_VISIBLE_TABS` (4: Main plus 3 Secondary) TabContexts are mounted. Stacked mode mounts only Main.
- User-facing copy is Split view, Split right, Split down, Make main, Hide from split, Hide split / Show split. Internal names (`tile`, `secondaryTree`, split ids, `mainTabId`) are never shown.

### Operations

- Opening in split is additive and never evicts a leaf. Placement is unambiguous only with no tree, exactly one leaf, or a focused leaf; otherwise, and at the pane cap, the operation fails closed with an announcement and no state change. New Tab is the only fallback that creates a parked tab regardless.
- Existing parked tabs are tiled with `prksWorkspaceTileTab(tabId)`; split right/down onto a leaf uses `prksWorkspaceSplitLeaf`, reusing or creating one tab, never duplicating.
- Close: a Secondary leaf's close removes it, normalizes the tree, touches no other leaf's TabContext, and moves focus only if it held it (closest sibling, then depth-first order, then Main). Main close promotes the first Secondary leaf, then the tab-strip neighbor, then Home. Closing the last tab fails until Home is minted. Batch close preflights every mounted tab and aborts entirely on any rejection.
- Hide split parks every visible leaf atomically and keeps the tree for Show split. Hide from split removes one leaf and keeps its tab parked. Neither duplicates a tab.
- Make main is an in-place role swap from any depth (`replaceTabId`): every TabContext survives; only placement and Main chrome change. Any promotion is `Promise<boolean>` and first runs `preflightMainPromotion()`: an old Main that cannot tile must pass `awaitLeave(oldMain.id, oldMain.route)`. A rejected preflight changes nothing (popstate restores the URL).
- Focusing a tile never promotes Main, changes the URL, remounts, or resets PDF/editor state. Clicking a visible leaf's tab focuses it in place. After close, hide, split, Make main or narrow fallback, focus returns to the resulting tile or its tab control.
- Secondary tile headers have grip, icon, title, a **Pane actions** control and Close. Infrequent actions open `prksWorkspaceOpenTabMenu`; do not add a second action list.

### Parking

- Parked tabs: cold-parked tabs make no requests and own no live DOM or resources. Tab switching may warm-suspend a PDF Work (three-context LRU) by reparenting its root into `#prks-tab-warm-parking`; resume only resizes. A Work with active metadata editing is never warm-parked. Eviction, close, hide, narrow fallback and teardown cold-unmount. Non-PDF routes always cold-park. Warm state is never persisted.

### Leave guards and drafts

- Person profile and Work metadata drafts are TabContext runtime state, never persisted. Focusing another pane never prompts. Anything that replaces, unmounts, parks or destroys a context with a dirty draft preflights through `prksTabLeave` (`frontend/js/tab-leave.js`). Rejection is an atomic no-op. Batch operations preflight all affected contexts first; concurrent attempts on one owner are serialized; `prksRenderTabRoute` does not ask again after approval.
- Person and Person Group editor state lives on the owning TabContext (`ctx.ui.personGroupIndex`, `personGroupEditing` / `personGroupMembersEditing`, which are mutually exclusive), never in a `window` singleton or module map. People-library search is the Vue index's local state plus sessionStorage `prks-people-library-filter`.

### Persistence

Workspace logical state is persistent; workspace runtime state is ephemeral. `frontend/js/workspace-persistence.js` owns all workspace `localStorage` (schema, validation, debounce, restore, corrupt-snapshot cleanup); no other workspace module writes storage. Never persist TabContexts, drafts, effective ratios, `narrowFallback`, split ids, Vue trees or AbortControllers. Restore happens before first normal mount; restored parked tabs do not fetch; the startup URL outranks a persisted Main route; persistence failures never break startup. A snapshot that fails validation removes the stored one and resumes normally once the workspace is persistable again.

### Ratios and dividers

- Root width is workspace-owned: `mainSplitRatio` (default `0.58`) in workspace state; each Secondary split node owns its own `ratio` (default `0.5`). Routes never read or write ratios. Ratios follow roles, not tab ids, and never reset on Make main, hide/show or narrow fallback. Only preferred ratios persist, through `workspace-persistence.js`.
- The root divider, nested dividers (`.prks-splitter`) and the PDF drawer width handle share one separator in `workspace-split.js`. Do not implement divider drag, keyboard resize or ARIA anywhere else.
- Resizing is layout-only: no remount, rerender or leave guard, and no full workspace paint per pointer move (`prksWorkspaceSetMainSplitRatio` / `prksWorkspaceSetNestedSplitRatio` with `{ paint: false }` plus a CSS custom property). Nested minimums are measured against that split's own container. Tile content resizes through its own container sizing; never dispatch a synthetic window `resize`.

### TabContext owns route runtime

- Route state: `ctx.navigation`, `ctx.lastResolvedRoute`, `ctx.entity`. Route DOM: `ctx.root`. Lookup: `ctx.query` / `data-prks-role`. Async lifetime: `ctx.beginRoute()` / `ctx.isCurrent(generation)`. Other values: `ctx.resources` / `ctx.setTimer`. Shell: `prksGetMainTabContext`, `prksGetFocusedTabContext`.
- External resources live in `ctx.resourceRegistry` (`frontend/js/owner-resource.js`). Register through `registerResource(ctx.resourceTicket(), …)` only; `ctx.setResource` refuses registry kinds. Suspendable kinds: `pdf` and `workNotes` (they live in the parked pane DOM). Non-suspendable: the Work role, tag, source and metadata sessions, Folder tag session, the Reminders field (`privateNotesEditor`) and `researchGraph`. Editor liveness requires `ctx.getResource(kind) === state` as well as the generation and entity checks.
- Warm park disposes non-suspendable sessions; owner-scoped sync subscriptions (`ctx.registerCleanup`) survive and patch the tab's entity, and the focused-panel refresh rebuilds sessions on resume. The registry never flushes: flushing stays on the leave path (`prksTabLeave`).
- Work and Folder Reminders share one session lifecycle (`PRKS_PRIVATE_NOTE_FAMILIES` in `ui.js`): the session holds unsaved text across entity switches, saves only against the base it was typed on, never coalesces over another pane's or tab's unsettled row, and reports `changed_elsewhere` when that base cannot be proven. Browser-local recovery uses `work-note-recovery.js`.

### Workspace drag and drop

- Drag is another input for the same canonical commands, never a parallel layout model. Pragmatic DnD sensors live in `frontend-app/src/workspace-dnd/`; PRKS owns the pure `WorkspaceDropIntent` resolver (28% edge bands, center is no-drop). `workspace-drag.js` is a geometry shim; never run two drag systems.
- Hover and preview never change state; drag state is never persisted. A drop commits through `moveLeafRelativeToTarget` or `prksWorkspaceReorderTab`.
- Moving a visible pane is one atomic tree transaction and not a leave: no PDF confirm, no notes flush, no remount. Dragging a pane to the tab strip parks it and does need leave preflight. Parked-tab insertion reuses its tab id. Main is never dropped into the tree. The pane cap blocks new leaves, not moves.
- Every cancellation path runs one idempotent cleanup that removes all visual state and leaves the workspace untouched. `prksWorkspaceCancelActiveDrag` is safe to call when idle.

## Settings

Settings category navigation is presentation state; it must not create another settings persistence model. The active category is an in-memory ref, never stored or routed. Inactive panels are hidden and inert, never recreated, so switching never resets a running backup, a chosen restore file or an in-progress value.

Performance diagnostics loads on first activation of the Diagnostics category and stays in TanStack Query until Refresh or Reset (an online mutation, no retry). Deployment settings and the storage status load on first activation of their category and read again each time it reopens; they have no writes. Do not move durable or offline mutations into TanStack Query.

## Saved Views

- Saved Views store search definitions, never cached work membership. Running one reuses the normal search; no parallel engine.
- Names and definitions are private: never in logs or diagnostics.
- Expanding the definition needs an explicit schema/search contract, never executable rules. No per-view polling or notifications.

## Research network

- Concepts, Positions and Arguments/Stances are canonical PostgreSQL records. Work↔Concept membership is never Work metadata.
- A Work references a Concept only through explicit `[[concept:Name]]` in its Research Note (`text_content`); Reminders (`private_notes`) and plain prose never link. Saving a note creates unknown valid Concepts in the same transaction; removing references never deletes a Concept.
- A Concept rename keeps the old name as an alias; case or spacing-only changes do not add one. Multiple parents are allowed, cycles are refused. Do not add Glossaries, Senses, Debates or Theories as a side effect.
- Arguments are referenced by stable id (`[[argument:A-id|Label]]`) and never auto-created. Every target needs a verdict. Counter/Response Arguments are reverse queries, not a stored list.
- The note-reference index is derived (`derived` schema), never canonical and never backed up. Its failure never rolls back a note save. Concept and Argument delete checks read the canonical notes, never only the derived index.
- Research names, definitions, aliases, text, verdict labels, pages, markup and snippets are private; never log them.

## Research Graph

- The Research Graph is a read-only derived projection: never canonical storage and never a reason to allow a destructive change. Do not add canonical graph persistence or a schema change merely to render it. Graph UI, layout and filter state are runtime-only.
- Graph node IDs are namespaced by entity type. Work→Concept and Work→Argument edges come only from explicit note references, never from prose, PDF text, tags or similarity. Argument source edges and note-mention edges stay distinguishable.
- `frontend-app/src/features/research-graph/` paints the chrome; the coordinator loads `prksOfflineResearchGraphFetch` and mounts Cytoscape with `renderResearchGraph`. The instance is the pane's `researchGraph` registration on the owner-resource registry and is not warm-suspendable; Main and Secondary keep their own, and destroy releases the instance, listeners, observer and pending frame. A stale completion never mounts into a replacement owner.
- Only the focused pane's runtime writes `#prks-graph-inspector`; the right panel follows its `hasSelection()`. Toggling the inspector only resizes, never `fit()` or relayout.

## Client request coordinator

- First-party `/api` traffic from `frontend/js` uses `prksRequest()`. Raw `fetch()` is a reviewed bypass only for `POST /api/client-errors` keepalive, backup progress/stage/restore and YouTube oEmbed. The owner-only API reference page (`/api/docs`, `frontend/js/api-docs.js`) is not part of the app: it loads no classic runtime, and Swagger UI fetches `/api/openapi.json` and runs Try it out reads itself. The coordinator never replaces `window.fetch`.
- Reads are bounded (foreground 4, background 1). Mutations are serialized, never retried, never merged: every accepted write is sent in order. Coalescing belongs to the durable store owners. Safe GET retry covers network errors and 502/503/504, at most two retries.
- `window.__prksRouteAbortController` aborts route reads only; writes survive navigation. Route generation still guards paint.
- Diagnostics are aggregate counts only, never URLs, queries, bodies, ids or search text.
- No persistent cache, IndexedDB, outbox or sync in `request-coordinator.js`. Reachability signals happen only at the real `fetch` boundary (see "Reachability"); memory-cache hits, deduped responses, aborts, clone failures and domain errors are not connectivity changes. Recovery probing stays in `offline-runtime.js`.

### Server-state ownership

Publishers, Saved Views, Performance Diagnostics, Deployment settings and the storage status (both read-only) use `prksApiRequest` and TanStack Query and must not call `prksRequest`. The Processing queue uses `prksApiRequest` through its typed client (`frontend-app/src/api/processing-items.ts`) without TanStack Query. Every other family stays on `prksRequest` because it has an effective or durable owner: Folders, Persons, Tags, Search, PDF bytes and annotations, browse/Recent snapshots, Concepts/Positions/Arguments, Person Groups, Playlists, Settings and maintenance, BibTeX export and the online Work create. Do not add a Query family for any of them. `fetchFolders`, `fetchConcepts`, `fetchPositions` and `fetchArguments` read only the effective catalogue and must not grow a raw fallback.

## Offline / PWA

Offline/local-first work has a large domain contract kept outside this file. **Before changing offline, local-first, sync, service-worker, conflict/revision or client-cache behavior, or tests that encode them, read `docs/agent-rules/offline-pwa.md` as the router, then load `offline-foundations.md` plus only the leaf files `docs/agent-context/sync-map.md` selects.**

Always:

- Disposable read cache belongs in `offline-store.js`; durable unsynchronized intent in `local-store.js`; editor draft recovery in `editor-recovery.js`, never `prks-local-v2:<scope>`.
- Never make disposable cache authoritative for unsynchronized work.
- Online and offline mutation paths converge on the same domain semantics; keep the revision, conflict and acknowledgement contracts instead of adding a second sync path.
- When you intentionally change an offline invariant, update the leaf contract and its regression tests. Current status lives in `docs/local-first-rollout-status.md`.

## Interaction feedback

- The pending-annotation-sync leave guard (the `pdf-sync` probe in `pdf-work-runtime.js`, registered on `prksTabLeave`) stays a native synchronous `window.confirm` and completes before any async draft confirmation. It is the only native confirm. Person and Work draft leave guards use the styled async confirmation, awaited by `awaitLeave()`.
- Every other confirmation uses the shared helpers; both PDF annotation-delete entry points use `prksConfirmDeletePdfAnnotation()` in `ui.js`.
- `prksSetButtonBusy(button, busy, { busyLabel })` is the busy-button helper for slow async mutations. Restore it in a `finally`.
