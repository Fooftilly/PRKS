# PRKS

PRKS is a local research library. Python 3.12 stdlib HTTP, SQLite, vanilla JS. Full run, Docker, and config live in README.md.

## Commands

- Tests: `python run_tests.py` (unit). Browser E2E: `python run_tests.py --e2e` (installs Chromium into `.playwright-browsers/` if missing). Both: `python run_tests.py --all`. UX Interaction Tour (separate, opt-in, artifact-producing): `python run_tests.py --ux-tour`.
- App, default for agents: `python prks_app.py --testing`
- Real app or Compose: only with run-real authorization from the user

If the user says only "run the app", your first application execution path is `python prks_app.py --testing`. Do not run unflagged `python prks_app.py`. Do not run Docker Compose. Do not target `./data`.

## Storage

Default. Do not write `data/` or a live `PRKS_STORAGE` tree. `python run_tests.py` assigns `PRKS_TESTING=1` and `PRKS_STORAGE` to `data_testing/`, and clears `PRKS_FOR_PROCESSING_DIR` and `PRKS_LOG_FILE`. Tests never target repo `data/` or `/data`.

Run-real. An instruction to run the real app or Compose authorizes normal application writes only. Creating or updating records the way the app does.

Destructive. Deleting PDFs, deleting, resetting, or replacing the production DB, or clearing production storage needs a separate explicit confirmation that names that action. Run-real is not that confirmation.

## Layout

- `prks_app.py` CLI (the only process entry)
- `backend/server.py` HTTP adapter: parsing, dispatch, status/headers, JSON, ETags, static files
- `backend/storage/config.py` frozen storage snapshot and env parser
- `backend/storage/paths.py` storage-path derivation and testing-mode containment
- `backend/db_manager.py` SQLite
- `backend/pdf_annotations.py` canonical PDF annotation metadata
- `backend/db_migrations.py` ordered schema migrations
- `backend/backup_restore.py` verified backup/restore
- `backend/research_markup.py` research-note semantic markup parser
- `backend/research_network.py` Concepts, Positions, Arguments/Stances
- `backend/research_index.py` disposable derived note-reference index
- `backend/research_graph.py` read-only Research Graph projection
- `backend/performance.py` in-memory performance diagnostics
- `frontend/` UI (`frontend/js/tab-context.js` per-tab runtime, `frontend/js/workspace-tabs.js` stacked workspace tabs, `frontend/js/workspace-persistence.js` workspace localStorage, `frontend/js/ribbon-create.js` unified New File split button)
- `frontend/js/offline-store.js` disposable IndexedDB client cache (see "Offline / PWA")
- `frontend/js/offline-runtime.js` online/offline state, read-through/mutation-guard policy (see "Offline / PWA")
- `frontend/sw.js` app-shell/static + managed-PDF service worker (see "Offline / PWA")
- `tests/` unittest

New File UI must use the canonical Work creation path (`#work-modal` / `POST /api/works`) rather than duplicate creation APIs.

New substantial behavior, in order:

1. Extend an existing focused module when the behavior belongs there (`text_index` for indexing, `pdf_linearize` for linearization, `storage.paths` only for storage-path resolution).
2. Otherwise create a focused feature or domain module.
3. Use `backend/services/` when an operation coordinates multiple concerns such as DB + filesystem + PDF + indexing.
4. Do not create `routes/`, `services/`, or other layers ahead of real behavior.

`server.py` keeps HTTP concerns. Substantial SQL, filesystem mutation, PDF processing, indexing, imports, and domain workflows live outside the handler. Do not split `server.py` or introduce a framework.

## Logging privacy

Logs are metadata-only. Never log user/library content, request bodies/query strings,
titles, notes, annotations, names, filenames/absolute paths, user URLs, headers,
raw browser messages/stacks, qpdf stderr, or str/repr(exception).

Use `backend/log_safety.py` for route/id/error normalization.
Unexpected traceback logging must go through the privacy-safe formatter.
Lowering `PRKS_LOG_LEVEL` / `PRKS_LOG_FILE_LEVEL` must never unlock raw data.

## Performance

Performance work must measure before optimizing.

Runtime performance metrics are aggregate and privacy-safe; never record request
bodies/query strings, search terms, library metadata, filenames/paths, SQL params,
or dynamic user labels.

Do not add schema indexes, caching, threading or SQLite tuning solely because an
endpoint appears theoretically expensive; use measured diagnostics first.

Performance instrumentation must never be required for application correctness.

## Backup and restore

Backup/restore code must never operate on production storage during tests.

Do not add a persistent storage component without classifying it as canonical,
derived, operational, or conditional in backup inventory.

`thumbs/`, `prks_text_index.db`, and `prks_research_index.db` (including WAL/SHM) are derived. They are not
required backup state and must be rebuilt after restore.

Restore validation must complete before live canonical state is modified.

Crash before committed restores the previous library. Only committed keeps
the restored library.

Do not use `ZipFile.extractall()` on unvalidated backup input.

Do not log archive paths, PDF names, manifest contents, or raw restore errors.

## PDF text index

`prks_text_index.db` is derived, never canonical.

Any code path that changes a work's managed PDF identity must synchronize the
text index or rely on the central reconciliation mechanism.

Do not write extracted PDF text into the canonical main DB solely for search.

Do not add text-index files to backups.

Do not treat an extraction exception as a successful empty PDF.

Do not preserve stale searchable text after the canonical PDF changes.

Derived-index schema corruption may be repaired by recreating the index;
canonical `prks_data.db` must never receive that treatment.

Normal startup must not run a full FTS5 integrity-check on a healthy derived
index. Strong FTS verification belongs to explicit rebuild/repair or when
FTS is already marked suspect.

## Database schema changes

`backend/db_schema.sql` describes the complete latest schema for fresh databases.

Any schema/data change needed by an existing database requires:

1. bump `LATEST_SCHEMA_VERSION`;
2. add exactly one ordered migration;
3. update `db_schema.sql` to the same final state;
4. add fresh-DB and upgraded-DB tests.

Never change schema only in `db_schema.sql`.
Never ALTER/CREATE/DROP schema objects from feature/request code.
Never swallow migration DDL failures.
Never manually bump `schema_version` before migration success.
Migrations may modify SQLite state only, not managed filesystem data.

## Bulk work mutations

Bulk work mutations must be validated before modification and commit atomically.

Do not implement frontend bulk operations as one HTTP mutation per selected work
when a transactional bulk backend operation exists.

Bulk selection is ephemeral route-local UI state; it is not canonical application
data.

Adding new bulk actions requires explicit server-side action validation. Never
allow arbitrary field names or dynamic method dispatch.

Bulk deletion is not part of generic organization semantics and requires a
separate reviewed design.

## Command palette

Command palette commands must use explicit allowlisted actions. Never execute
user query text as JavaScript or dynamic method names.

Navigation commands must use prksNavigate() and existing canonical hash routes.

Do not introduce duplicate CRUD forms solely for command-palette actions; reuse
existing modals and route handlers.

Global command shortcuts must not steal keyboard shortcuts while the user is
typing/editing or while another modal owns focus.

Palette queries are ephemeral UI state and must not be persisted or logged.

Any command that depends on transient command-palette operation state (e.g.
`state.splitPlacement`, set by a pane menu's explicit Split right/down request)
must snapshot that state before calling `closePalette()`, because closing the
palette clears it. Read the snapshot afterward, never the live state.

## Workspace navigation

Internal PRKS navigation uses prksNavigate.

Do not write window.location.hash directly from feature code.

Do not use window.open for ordinary internal PRKS routes.

Normal navigation targets the originating workspace context (the TabContext that owns the link, or the focused context for palette/global commands). Sidebar chrome navigates Main.

Ctrl/Cmd-click and middle-click target a background PRKS tab.

Alt-click and `prksNavigate(..., { target: "tile" })` open a Secondary leaf when the route is tile-capable.

User-facing copy says "Split view", "Split right", "Split down", "Make main", "Hide from split", "Hide split" / "Show split". Internal APIs stay `tile`, `secondaryTree`, split-node IDs, and `target: "tile"` — never user-facing.

Existing parked tabs should be tiled through `prksWorkspaceTileTab(tabId)`, not duplicated through `navigate(... { target: "tile" })`. Split right/down onto a specific focused leaf go through `prksWorkspaceSplitLeaf(targetLeafTabId, axis, options)`, reusing an existing tab (`options.tabId`) or creating one (`options.hash`) — never duplicating.

Main never recursively splits; it is permanently the single root pane. Secondary is `workspace-tree.js`'s recursive `leaf`/`split` tree (`secondaryTree`): `null` (no Secondary), a bare `{ type: "leaf", tabId }` (the common single-Secondary case — do not wrap it in a pointless split node), or a `{ type: "split", id, axis, ratio, first, second }` node whose children are themselves leaves or splits. A tab occurs at most once in `secondaryTree`, and Main's own tab never appears inside it. Split-node IDs are stable per-runtime keys (never array index, DOM position, or a child's tab ID) used for DOM reuse, resize ownership, and targeted mutation; they are in-memory only, never persisted. Route capability for a Secondary leaf is unchanged from single-Secondary v1 (works, people, concepts, positions, arguments, playlists) — no new route types become tile-capable as part of recursive splitting.

Tree mutation goes only through `workspace-tree.js`'s pure helpers (`findLeafByTabId`, `replaceLeaf`, `splitLeaf`, `removeLeaf`, `replaceTabId`, `setSplitRatio`, `normalizeTree`, `validateTree`, `collectLeafTabIds`, `containsTab`, …); routes and UI code must never mutate `secondaryTree` structure directly. Removing a leaf always normalizes the tree afterward: a split node left with one child collapses into that child, repeated upward, so the tree never carries a redundant single-child split node; if the last leaf disappears, `secondaryTree` becomes `null` and the view returns to stacked.

At most `PRKS_MAX_VISIBLE_TABS` (4: 1 Main + 3 Secondary) TabContexts are ever mounted at once. Split right/down are disabled with an explanation once the cap is reached; ordinary New Tab is unaffected and still creates a parked tab.

Generic `target:'tile'` navigation and "Open in split view" are additive: they never evict an existing Secondary leaf. There is no user-facing "Replace split pane" operation. Default placement is unambiguous only when there is no Secondary tree (new bare leaf), exactly one Secondary leaf (split it), or a focused Secondary leaf in a recursive tree (split that one); otherwise placement is ambiguous and the operation is fail-closed — no new logical tab, no tree mutation, no mount, no paint, no leave check, just the ambiguity/cap announcement and `false`. The same fail-closed rule applies at the pane cap. New Tab is the only fallback that intentionally creates a parked logical tab regardless of ambiguity or the cap.

Close: parked closes only that tab. A Secondary leaf's close removes and normalizes the tree — it must never remount or otherwise touch any other leaf's TabContext — and prefers focusing the closest surviving sibling in the collapsed subtree, else the nearest remaining leaf in deterministic depth-first tree order, else Main. Main close promotes the first surviving Secondary leaf in that same deterministic order, else the right tab-strip neighbor, then left, then Home. Do not flash Home while a successor exists. Keep leave guards. Batch close (other tabs / tabs to the right) preflights every mounted tab being closed and aborts entirely on reject.

Hide/park is two distinct operations. Global "Hide split" (the shell Split button) parks every currently-visible Secondary leaf at once, atomically (depth-first preflight, stop at first rejection, no partial parking), but preserves the whole `secondaryTree` logically for "Show split" to remount unchanged. Local "Hide from split" (per-leaf, context-menu only) removes just that one leaf from the tree and normalizes it, while keeping its logical tab open and parked — distinct from Close, which destroys the tab. Neither ever duplicates a tab: reopening a parked leaf reuses its existing tab ID.

Make main is an in-place role swap, valid from any Secondary leaf at any tree depth: the promoted leaf becomes `mainTabId`, and the old Main takes over that exact leaf position (`replaceTabId`) — never a tree rebuild, a move to the root, or a sibling reorder. Every leaf's TabContext identity (including the promoted and demoted ones) survives untouched; only DOM placement/role and Main-owned chrome (URL, History, title) change.

Promoting a Secondary leaf to Main (`makeMain`, a Secondary navigating to a non-tile-capable route, or a visible Secondary's popstate promotion) is `Promise<boolean>` and preflights the old Main through the shared `preflightMainPromotion()` helper before any state mutation. If the old Main supports tiling it is only demoted into the promoted leaf's exact old position (the role swap above) and never leaves, so no leave prompt runs. If the old Main does not support tiling, promotion would cold-park/unmount it, so it must pass `awaitLeave(oldMain.id, oldMain.route)` first — the old Main's own current route, not the incoming target's, so autosave/owned-draft guards run without a false route-change read. A rejected preflight is an atomic no-op: no tab/tree/URL/mount mutation, and (for popstate) the URL is restored. `promoteSecondaryToMain()` itself stays the synchronous, unchecked state-mutation primitive; it is only ever called after that preflight succeeds. Pure startup reconciliation has no mounted dirty runtime and may keep using the primitive directly.

Focusing a tile must not promote Main, change the URL, remount, or reset PDF/editor. Clicking a *visible* Secondary leaf's global tab-strip entry focuses it in place; it does not promote it — that is reserved for parked tabs and explicit Make main. After close, hide (global or local), split, Make main, or narrow fallback, restore focus to the resulting focused tile or its workspace tab control.

User-facing menu copy is Split view / Split right / Split down / Make main / Hide from split / Hide split / Show split. Do not expose `tileTab`, `secondaryTree`, split-node IDs, or `mainTabId`.

Secondary tiled headers keep grip, icon, title, a **Pane actions** control, and Close. Infrequent pane actions (Make main, Split right/down, Hide from split, move) must open the existing `prksWorkspaceOpenTabMenu` from `workspace-tab-menu.js` — do not add a second tile-header action list or Split dropdown in `workspace-tiling.js`.

Parked tabs use two lifecycles. Cold-parked tabs perform no API requests and own no live
DOM/resources. Ordinary global-tab
switching may warm-suspend an actual PDF Work (`ctx.getResource('pdf')`) by reparenting its
existing root into `#prks-tab-warm-parking`; warm resume reparents that same root and requests
only a container resize, never a route render, Work/PDF fetch, viewer init, fit, reload, or
layout. A Work with active metadata editing is never warm-parked: after leave approval it
cold-unmounts so normal TabContext teardown discards the draft. Warm parking is a three-context
LRU. Eviction, Close, batch close, application teardown,
Hide split / Hide from split, and narrow fallback cold-unmount and destroy normally. Non-PDF
routes always cold-park. Warm-cache state is runtime-only and never persisted.

Stacked mode mounts one TabContext (Main). Tiled mounts Main plus every visible Secondary leaf in the tree, up to the visible-pane cap. Do not introduce a fifth mounted context.

URL always represents Main, no matter how deep the focused Secondary leaf is nested. Secondary routes never mutate browser History. Make Main uses replaceState.

The right panel always follows the focused TabContext. Feature navigation uses the originating TabContext when it is known; do not use focused context as a substitute for an originating element/context. Because the shared `#panel-content` lives outside every tile's DOM, a click originating inside it resolves its owning tab from `panel.dataset.prksOwnerTabId` (verified against a live TabContext), never from current Main, the focused tab, or `location.hash`. Main remains a fallback only for genuinely shell-global links that have no TabContext or right-panel owner at all.

Do not add route-level global runtime state.

Tab switching must not create contextual Back origins.

Workspace logical state is persistent; workspace runtime state is ephemeral. `frontend/js/workspace-persistence.js` owns all workspace `localStorage` behavior (schema, validation, debounce, restore, corrupt-snapshot cleanup). Do not write workspace storage from `workspace-tabs.js`, `workspace-tree.js`, `workspace-tiling.js`, `workspace-split.js`, `workspace-drag.js`, or `tab-context.js`. Never persist TabContext/runtime objects, editor drafts, effective constrained ratios, `narrowFallback`, or split-node runtime IDs. Restore happens before first normal mount. Parked restored tabs must not fetch. The current startup URL outranks a persisted Main route. Persistence failures must not break app startup.

If a live workspace's serialized snapshot ever fails persistence validation (e.g. a tab parked on an unknown route via the router's "Section In Development" fallback), the writer invalidates/removes the previously-stored snapshot rather than leaving it behind looking authoritative for a workspace that no longer matches it, and resets its own change-tracking so the next persistable snapshot still writes normally. This never touches the running in-memory workspace and never globally disables persistence — it resumes as soon as the user returns to a persistable/known route.

Root Main/Secondary width is workspace-owned: one normalized ratio (`mainSplitRatio`,
default `0.58`) lives in workspace-tabs.js state, alongside `mainTabId` /
`focusedTabId` / `secondaryTree`. Every internal Secondary split node owns its own
local `ratio` (default `0.5`) inside its own tree node — never on either child tab,
never inherited from the root ratio or from a sibling split. Routes must never
store, read, or modify any of these ratios. Canonical preferred ratios persist
through `workspace-persistence.js` only; do not write them from feature code, and
do not persist constrained/effective ratios.

Main/Secondary ratio follows roles, not tab IDs. Make Main, adding/removing
Secondary panes, Hide/Show split, and the narrow responsive fallback must never
invert or reset the root ratio or any nested split's ratio.

The Main/Secondary divider and every nested Secondary split divider
(`workspace-split.js`, class `.prks-splitter`) share the one separator
implementation, keyed by split-node ID for nested separators. Do not implement
independent divider drag, keyboard-resize, or ARIA logic in `works.js`,
`works-pdf.js`, other route components, or a second implementation inside
`workspace-tiling.js` for nested splits. `workspace-tiling.js` only calls into
`workspace-split.js`; it does not own pointer, keyboard, ARIA, or persistence
logic itself.

Divider resizing (root or nested) is layout-only and must not remount
TabContexts, unmount/mount a route, re-render a route, or trigger a leave guard.
It must not run a full workspace paint on every pointer-move; the canonical
ratio updates via `prksWorkspaceSetMainSplitRatio(ratio, { paint: false })` (root)
or `prksWorkspaceSetNestedSplitRatio(splitId, ratio, { paint: false })` (nested),
and the DOM applies the resulting pixel width/height through a CSS custom
property. A nested split's minimum sizes are measured against that split's own
container only, never the window or workspace root; if its container is too
small for both children's minimums, its ratio clamps safely to that split's own
midpoint rather than producing a negative/overflowing pane.

Tile-local components (PDF viewer, EasyMDE, other detail routes) respond to
divider resizing through their own existing container-aware sizing /
ResizeObserver lifecycle. Do not use `window.dispatchEvent(new Event('resize'))`
as a substitute for tile-local container sizing.

TabContext owns route runtime:

- route state → TabContext (`ctx.navigation`, `ctx.lastResolvedRoute`, `ctx.entity`)
- route DOM → `ctx.root`
- page-local lookup → `ctx.query` / `data-prks-role` (`ctx.domId` only for ARIA)
- async lifetime → `ctx.beginRoute()` / `ctx.isCurrent(generation)`
- live resources → `ctx.resources` / `ctx.setTimer`
- shell → main/focused context (`prksGetMainTabContext`, `prksGetFocusedTabContext`)

People-library search runtime belongs to rendered `.prks-people-library` root
(`root.__prksPeopleLibraryState`), never a `window` singleton or tab-ID global map.
Rerender only that root. SessionStorage preserves shared query preference; it is not
workspace persistence or route state.

Group-library runtime likewise belongs to rendered `.prks-group-library` root, never
a route-scoped `window` singleton. `personGroupEditing` and
`personGroupMembersEditing` are mutually exclusive TabContext UI modes: metadata
editing owns the right panel; membership management owns the Members section.

Person profile edit state, including selected Person Groups, belongs to the Person's
TabContext. Never store Person-editor selections or drafts in a window-global
singleton. Right-panel reconstruction must render from the owning TabContext draft.

Person profile and Work metadata drafts are TabContext-owned runtime state. Never
persist them through workspace persistence. Merely focusing another mounted pane is
non-destructive and must not prompt. Any operation that will replace a route, unmount,
park, or destroy a context with a dirty editable draft must preflight through
`prksCanLeaveTabContext`; rejection is an atomic no-op that preserves route, tab order,
tree topology, focus, draft, and editor DOM. Batch operations preflight all affected
mounted contexts before mutating any of them.

Stacked mode: one mounted context. Tiled mode: Main + every visible Secondary leaf (up to the visible-pane cap), each with an independent TabContext. Do not store route-scoped state on `window`. The Research Graph
is `ctx.getResource('researchGraph')`; no module-level singleton fallback.

### Workspace drag and drop

`workspace-drag.js` is an alternate input path for existing canonical workflows, never a
parallel layout model. Drag state (`active`, `source`, `origin`, `pointerId`, `target`) is
transient and lives only in that module's own closure; it is never canonical/persisted and
never written to `localStorage`, `sessionStorage`, or IndexedDB.

`workspace-tree.js` owns recursive tree transformations (including drag-driven pane moves, via
`moveLeafRelativeToTarget`); `workspace-tabs.js` owns global tab ordering (via
`prksWorkspaceReorderTab`). `workspace-drag.js` only computes/previews user intent and invokes
those same canonical APIs on drop — it must never mutate `secondaryTree` or `state.tabs`
directly, and must never mutate either while the pointer is merely moving/hovering (preview
only; the DOM insertion marker/edge overlay are pure visual feedback with no state effect).

A pane move is one atomic tree transaction. Moving a visible pane is spatial repositioning, not
a leave operation — it must not run PDF leave confirmation, must not flush-for-unmount Research
Notes, and must not remount the moved pane or any unrelated pane. Parking a pane (grip → tab
strip) is equivalent to "Hide from split" and does require leave preflight; a rejected leave
must leave the tree, tab order, and focus completely unchanged.

Parked-tab insertion into the Secondary tree always reuses that tab's existing logical tab ID —
never a duplicate tab, never a second mount. Main can never be inserted into `secondaryTree` by
drag; only the existing "Make main" action changes Main ownership. The pane cap
(`PRKS_MAX_VISIBLE_TABS`) blocks new visible leaves being added by drag, not existing panes
being moved.

Drag cancellation (Escape, `pointercancel`, `lostpointercapture`, window blur, responsive
transition, external tile removal) must run through one idempotent cleanup that removes every
transient listener, the preview element, every overlay/marker, the autoscroll animation frame,
source styling, and the body drag class, and must leave canonical workspace state completely
untouched. `prksWorkspaceCancelActiveDrag` exists specifically so `workspace-tiling.js` can
defensively end an active drag before a real narrow/wide transition and before pruning any
stale tile that could contain the live drag source — it is always safe to call when nothing is
active. `workspace-drag.js` must never itself mutate responsive/narrow-fallback state.

## Settings

Settings category navigation is presentation state; it must not create another
settings persistence model. The active category lives only in an in-memory module
variable, never `localStorage`, never `/api/settings`, never a URL hash.

Inactive Settings category panels are hidden and inert, never removed/recreated.
Switching categories must not reset a running Backup/Maintenance operation, a
chosen restore file, or any control's in-progress value.

Diagnostics data loads only on first activation of the Diagnostics category, not
whenever Settings opens. Revisiting Diagnostics reuses the retained snapshot;
only the explicit Refresh action re-fetches.

## Saved Views

Saved Views store search definitions, never cached work membership.

Executing a Saved View must reuse the normal PRKS search implementation; do not
create a parallel search engine for Saved Views.

Saved View names and search definitions are private canonical user data. Never
include them in logs or performance diagnostics.

Any future Saved View definition expansion requires an explicit schema/search
contract rather than arbitrary executable rules.

Do not add per-view polling or background notifications as an incidental Saved
Views feature.

## Research network

Concepts, Positions, and Arguments/Stances are persistent canonical records in
`prks_data.db`. Work↔Concept membership is never stored as Work metadata.

The Work→Concept relation exists only because `works.text_content` contains
explicit `[[concept:Name]]`. `private_notes` must not participate. Ordinary
prose never auto-links. Unknown valid Concept names are created on note save in
the same transaction as the note. Removing every note reference does not delete
the Concept.

Concept aliases/search keys resolve note references. A Concept identity
rename preserves the old name as an alias. Capitalization or spacing-only
display changes update `concepts.name` without a new alias; existing notes
still resolve through the same normalized identity. Multi-parent hierarchy
is allowed; cycles are rejected. Do not add Glossaries/Concept Senses or
Debates/Theories as an incidental follow-on.

Arguments/Stances use stable IDs in notes (`[[argument:A-id|Label]]`) and do not
auto-create from unknown markup. Every target requires a verdict. Incoming
Counter/Response Arguments are reverse queries of target relations, not a
separate stored list.

`prks_research_index.db` is derived, never canonical. Mention offsets may be
stored; surrounding note prose must not be. Unknown/corrupt derived schema may
be deleted and recreated. Never touch `prks_data.db` because the research index
is corrupt. Derived indexing failure must not roll back a valid canonical note
save. Do not add research-index files to backups. Concept and Argument deletion
must inspect canonical `works.text_content` with `parse_research_markup()`;
the derived index is never the sole authority for those destructive checks.

Concept, Position, and Argument names, definitions, aliases, main text, verdict
labels, page ranges, markup, and backlink snippets are private. Never log them.

## Research Graph

The Research Graph is a read-only derived projection. It is never canonical
relationship storage and must never authorize a destructive mutation.

Graph node IDs must be namespaced by entity type; raw PRKS IDs are not globally
unique across record types.

Work→Concept and Work→Argument graph edges represent explicit research-note
semantic references only. Never infer graph relations from plain prose, PDF
text, tags or search similarity.

Argument source edges and note-mention edges have different semantics and must
remain distinguishable.

Do not add graph persistence or a main-DB schema migration merely to render the
Research Graph.

The research graph and research-reference index are read-only projections. Their
presence or absence must never authorize deletion or other canonical mutation.

The global right panel on the Research Graph route is selection-aware, not
route-aware: it follows the focused Graph runtime's own `hasSelection()`
(`ctx.getResource('researchGraph')`), never DOM markup and never an unfocused
Graph tile. Graph filter state (which node/relation types are visible) is
runtime-only; do not persist it to `localStorage`, workspace persistence,
`/api/settings`, or the URL. Toggling the graph inspector must never call
`fit()` or rerun the Cytoscape layout — only a container `resize()`.

## Client request coordinator

Ordinary first-party `/api` traffic from `frontend/js` uses `prksRequest()`. Do not
call `fetch()` for those requests. Raw `fetch()` is a reviewed bypass only:
`POST /api/client-errors` keepalive, backup progress/stage/restore, and external
YouTube oEmbed. The coordinator does not assign or replace `window.fetch`.

Reads are bounded (foreground 4, background 1). Mutations are serialized (max 1)
and never automatically retried. Safe GET retry covers network errors and
502/503/504 only, up to the initial attempt plus two retries.

Only complete-value autosaves may set `coalesceKey` (research notes and private
notes). Creates, deletes, relationships, bulk, reorder, PDF, and backup must not.

`window.__prksRouteAbortController` is for route reads. Canonical writes survive
navigation. Route generation (`window.__prksRouteGen` / `prksRouteStale`) still
guards paint after abort.

Coordinator diagnostics are aggregate counters and occupancy only. They must never
contain private URL, query, body, Work ID, search text, or coalesce-key content.

Persistent cache, IndexedDB, outbox, and offline synchronization do not belong in
`frontend/js/request-coordinator.js`. The burst catalog cache is memory-only and
short-lived. It is not offline support. The coordinator may make a best-effort
reachability signal (dynamic lookup of `prksOfflineNoteRequestSuccess` /
`prksOfflineNoteRequestFailure`) at the real `fetch` boundary only: a resolved
`Response` of any HTTP status means PRKS answered; a non-abort transport
rejection after retries are exhausted means it did not. Managed PDF GETs
(`/api/pdfs/...`) are excluded: the service worker may resolve those from Cache
Storage without the PRKS process answering. Memory-cache hits,
deduped completed responses, `AbortError` / route cancellation, `response.clone()`
failure, and JSON/domain errors must not be treated as a connectivity change.
Do not add another probe timer in the coordinator — recovery stays in
`offline-runtime.js`.

## Offline / PWA

Phase 1 is read-only offline support, and this first slice is Work-only: the
offline detail cache, provenance banner, and mutation guard cover Work detail
pages and their PDFs only. Person, Concept, Position, Argument, and Playlist
routes are not wrapped in the offline read-through path and must keep their
ordinary online-only fetch/error behavior until their own mutation surfaces
are explicitly hardened for offline use — do not add `prksOfflineDetailFetch`/
`prksOfflineGuardMutation` calls to those routes as an incidental part of
unrelated Work-page work. There is no offline mutation outbox, sync conflict
resolution, background sync, or editable offline Research Notes/annotations in
this phase — those are later-phase work.

The PRKS server (SQLite + managed files) remains the sole source of truth.
`frontend/js/offline-store.js` (IndexedDB, `prks-offline-v1`) and `frontend/sw.js`
(Cache Storage) are a disposable client-side cache, never another canonical
database, sync authority, backup, or conflict resolver. Deleting either must
never affect server data, and a corrupt/incompatible offline schema may be
discarded and recreated — canonical `prks_data.db` must never receive that
treatment.

Responsibilities stay separated:

- `offline-store.js`: IndexedDB persistence only (`entities`, `lists`,
  `metadata` object stores). No DOM, no routing, no connectivity policy. Every
  public method resolves (never rejects/throws) and degrades to a safe
  unavailable result if IndexedDB is missing, blocked, corrupt, or over quota.
- `offline-runtime.js`: online/offline/reconnecting state, the
  read-through/mutation-guard policy, and operational UI state. Holds no
  canonical domain data itself; delegates all persistence to `offline-store.js`.
- `sw.js`: app-shell/static-asset availability plus a focused whole-file
  managed-PDF cache. Every core same-origin CSS/JS/font/icon file the ordinary
  shell needs to boot is precached eagerly on install from an explicit
  manifest (`STATIC_PRECACHE_PATHS`/`SHELL_PRECACHE_PATHS`), not cached
  as-you-go — the shell must launch offline after a single earlier install,
  with no second online page load required to warm the cache. A regression
  test (`tests/test_frontend_service_worker.py`) compares that manifest
  against `index.html`'s actual `<script>`/`<link>`/`<img>` dependencies so
  the two cannot silently drift; the heavy PDF-viewer bundle is deliberately
  excluded from eager precache and stays cache-on-first-PDF-use, since it is
  not needed to launch the shell itself. `sw.js` never generically caches
  `/api/...` JSON and never queues API mutations. Eligibility rules
  (same-origin GET only, no Range response cached as a whole file, no
  4xx/5xx/redirected/cross-origin) live in small pure functions so they can be
  tested without a real `ServiceWorkerGlobalScope`
  (`tests/browser/run_sw_selftest.js`).

Connectivity reflects real PRKS server reachability, not `navigator.onLine`
and not application-level health. `navigator.onLine`/the browser's
`online`/`offline` events are early hints only: either one moves straight to
`reconnecting` and kicks off a real probe rather than being trusted directly.
The probe itself treats *any* resolved HTTP response — 2xx, 4xx, or 5xx — as
"PRKS answered" (→ online); only a rejected request with no transport response
at all means the server is unreachable (→ offline, with bounded/backoff
retry). Ordinary `prksRequest()` traffic feeds the same state: a real fetch
`Response` (including 404/409/500/503) calls `noteRequestSuccess()`, and a
non-abort transport failure after GET retries are exhausted (or a mutation
transport rejection) calls `noteRequestFailure()`. A domain-level error
(e.g. a 500) must never flip the shell into "Offline." The runtime begins a
real probe immediately on `init()`, so a runtime that starts up while PRKS is
already unreachable reaches `offline` on its own, without needing a failed
Work request first. Once `noteRequestFailure()` has moved state to offline,
the existing bounded/backoff probe owns recovery; a later ordinary request
that receives a real HTTP response may also restore online immediately.

An offline-capable read must distinguish a 404 (normal "not found," server is
reachable) from every other domain/parse failure (400/403/409/500, invalid
JSON — must propagate to the caller's normal error handling, never become a
cached fallback or a false "not found") from an actual transport/network
failure (the only case that falls back to the offline store, or reports
`unavailable` with no cache).

Do not let IndexedDB values enter the request coordinator's memory cache as if
they were fresh server responses, and do not turn `api.js` fetchers themselves
into persistent-caching functions — an offline-capable route goes through
`prksOfflineReadEntity`/`readList` (online success → render + async cache write
that never fails the online read; network/server-unreachable failure → offline
store lookup). A real HTTP domain response (404/400/etc.) is never treated as
an offline condition and never falls back to cache.

Cached values always carry explicit provenance (`source: 'server' | 'cache' |
'unavailable'`, plus `cachedAt`) — never hidden, never presented as current.

A successful canonical mutation must never leave a known-stale offline snapshot
eligible for fallback. When a complete authoritative replacement is available,
refresh the disposable cached entity; when only a partial successful mutation is
available, invalidate that entity; when a mutation fails, retain its previous
cache entry. Cache-maintenance failures are non-fatal and never change canonical
server state.

Every canonical Work mutation (metadata Save, delete, role/person links,
folder/playlist/tag attach-or-remove, PDF annotation create/edit/delete) must
call `prksOfflineGuardMutation()` first and stop when it returns `true`. Never
discard a submitted change, fake success, write it only to IndexedDB, or queue
it. Research notes and private notes follow the same rule via explicit
read-only editor state while offline, not autosave-through-cache — and that
read-only state must apply the moment the editor is constructed (read
`prksOfflineRuntimeState()` directly at init), never only via a later
`prksOfflineRuntimeSubscribe()` callback, so an editor built after the runtime
already left `online` is never briefly editable.

A previously cached PDF reopened while not online mounts the vendor viewer in
its own `mode: 'preview'` (render/scroll/zoom/navigate only — no
highlight/underline/delete/comment/save, and no annotation-sync persistence
worker installed), chosen via `prksPdfDesiredMode()` at mount time — never
`mode: 'work'` hardcoded and then locked down after the fact. Connectivity
changing while a Work PDF viewer is already mounted never destroys/recreates
the viewer or document; `prksReconcilePdfMutationMode()` flips the same
`'work'`/`'preview'` interaction boundary live via
`PrksPdfViewerHandle.setMutationEnabled()` in place, pausing/resuming (never
tearing down) the same annotation-sync worker so pendingChanges/unsaved state
survives the transition. Going offline must stop annotation tools
immediately, not merely disable them: `setMutationEnabled(false)`
synchronously clears any already-active markup tool
(`annotation.setActiveTool(null)` via `clearActiveTool()`) and returns the
plugin to pointer mode before the render that hides the markup toolbar —
never relying solely on the toolbar button disappearing a frame later. Every
mutating viewer API (`activateMarkupTool`, `createAnnotation`,
`updateAnnotation`, `deleteAnnotation`, `undo`, `redo`) requires
`mode === 'work'`; `selectAnnotation`/jump/zoom/page navigation stay
read-only in either mode. An async annotation-persistence setup started while
`'work'`/online must re-check current viewer identity, `runtime.mode`, and
connectivity (`prksPdfPersistenceSetupEligible()`) after every await boundary
and immediately before installing a worker — if any changed underneath it,
abandon without installing and reset `runtime._persistenceSetupStarted` so a
later online reconcile can retry, without destroying the viewer. An
already-installed worker uses the weaker `prksPdfPersistenceStillLive()` and
stays live (paused) while offline; its save-confirmation poll
(`confirmPersistedToken()`) stops issuing `save-confirm` requests the moment
the worker is `paused`, resuming only once reconnected.

Offline cache contents are private research data: never log cached entity
bodies, note text, titles, search text, or PDF contents. Diagnostics
(`prksOfflineDiagnostics()`, Settings → Offline storage) may expose only
aggregate counts/approximate bytes/availability/last-sync timestamp.

Reconnect refreshes only the focused, previously cache-served route — never
every mounted tile at once (no request storm), and reconnect never performs
writes (no outbox exists yet).

## Interaction feedback

Do not replace the synchronous pending-annotation-sync navigation guard
(`prksCanLeaveTabContext` and the mirrored check inside `prksRenderTabRoute`,
both in `frontend/js/app.js`) with `prksConfirmDialog`/`prksConfirmDestructive`
without redesigning the navigation contract. It must stay a native
`window.confirm`; this synchronous PDF safety decision must complete before any
async editable-draft confirmation begins. This is the sole native-confirm exception.
Person profile and Work metadata dirty-draft leave guards use the styled async
confirmation and are awaited by workspace `awaitLeave()` before mutation.

Every other confirmation, including both PDF annotation-delete entry points
(the annotation editor's Delete button and the annotation-list row's Delete
button), goes through the shared `prksConfirmDeletePdfAnnotation()` helper in
`frontend/js/ui.js`, which wraps `prksConfirmDestructive`. Keep both entry
points on that one helper rather than duplicating the confirmation copy.

`prksSetButtonBusy(button, busy, { busyLabel })` (`frontend/js/ui.js`) is the
shared busy-button helper for async mutations with meaningful latency. It
snapshots and restores exact button contents (icon markup included), so
callers must restore it from a `finally` rather than only on the success or
failure path.

## UX Interaction Tour

`tests/ux_tour/` is a separate, deliberately human-oriented, artifact-producing
review suite (video, Playwright trace, checkpoint screenshots, an action log,
and a coverage matrix). It complements the fast unit/browser-selftest/E2E
suites; it is not a replacement, and finding something there is not a reason
to delete or weaken a precise E2E regression test that already covers the
same operation.

UX Tour actions should use real user-visible controls when one exists
(buttons, menus, links, inputs, keyboard shortcuts, drag handles, the command
palette, modals) — not `window.prksNavigate(...)`,
`window.prksWorkspaceSplitLeaf(...)`, `selectGraphNode(...)`, or similar
internal calls. Internal JavaScript calls may inspect state for assertions or
instrumentation (workspace snapshot, TabContext ids, PDF runtime identity,
request counters) but should not substitute for the interaction being tested.
The one narrow exception is Cytoscape graph edges: they are canvas pixels with
no separate DOM control, so selecting one goes through the same
`window.selectGraphEdge(id)` hook the existing E2E suite already uses for that
same reason.

Do not add the UX Tour to normal unit/E2E modes (`python run_tests.py`,
`--e2e`, `--all`). It is explicit opt-in only, via `python run_tests.py
--ux-tour`. `tests/ux_tour/test_tours.py` stays out of ordinary discovery via
the same `load_tests()` gate `tests/e2e/test_app.py` uses for `PRKS_E2E` —
here keyed on `PRKS_UX_TOUR`, set only by `tests/ux_tour/run.py`.

Every scenario opens its own fresh `tests.e2e.harness.AppServer` (fresh
`TemporaryDirectory`, fresh database) and a fresh browser context — never a
long-lived one shared across tours. Recorded artifacts live under
`artifacts/ux-tour/<run-id>/`, gitignored and never committed; a passing
scenario's heavy artifacts (video/trace/screenshots) are deleted after its
manifest entry is recorded unless `PRKS_UX_RECORD=1` was set.
