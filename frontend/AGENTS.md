# PRKS frontend agent instructions

This file governs the shipped `frontend/` runtime/compatibility tree. Vue/TypeScript source under the sibling `frontend-app/` tree has its own `frontend-app/AGENTS.md`; load both only when a change crosses the bridge, shared workspace/runtime ownership, or offline/sync behavior. The current Vue/legacy bridge is transitional under #230/#303 and must not be treated as the intended permanent frontend shape.

These rules apply to frontend work in addition to the repository-root `AGENTS.md`. `DESIGN.md` remains authoritative for UI and interaction decisions; read the sections relevant to the component being changed.

## Vue application

`frontend-app/` is the Vue 3 + TypeScript source (Vite, `vue-tsc`, Vitest). Node/npm are maintainer build tools (Node `>=24.15.0 <25`). The Python runtime serves the committed bundle `frontend/vue/prks-vue.js` and does not run Vite. `vite-plugin-css-injected-by-js` inlines component CSS into that bundle; the shell and service worker do not load a separate Vue stylesheet.

`frontend/js/` remains the legacy application. Do not rewrite it as part of a foundation change. Migrated UI belongs in `frontend-app/src/` and must not introduce a second canonical workspace or application state model. TanStack Query is in use for performance diagnostics. `@vueuse/core` is adopted selectively for generic browser lifecycle (Folder Library: `useDebounceFn`, `useEventListener`). Do not add Vue Router or Pinia. Do not use VueUse for PRKS route, workspace, durable, or preview semantics — those stay in PRKS helpers/composables.

The hidden `#prks-vue-root` mount proves the bundle loads and hosts the Vue app. Settings performance diagnostics teleport into `#prks-settings-perf-root`. TanStack Query owns that disposable server snapshot. Do not add a second QueryClient, Pinia, or Vue Router. VueUse is allowed only for generic browser lifecycle helpers where a slice already depends on it; do not use it as a route-surface or preview substitute. Do not persist the query cache or use it as an offline queue.

The legacy PRKS router remains canonical: `prksParseRoute`, canonical hashes, TabContext, Main/Secondary ownership, and workspace tab routing. Vue must not grow a second route model or hash parser. `frontend-app/src/route-surface/` is the typed bridge from that model into one Vue view per owner. `PrksRouteInstance` is the discriminated identity stored for that owner: route name, params, canonical hash, Main-shell ownership, and generation. `readRouteSurface` returns that owner's instance, including its params, and does not see another pane. Work/PDF and unrecognized hashes stay on the legacy router and are not members. Route presentation goes through `prksDeliverVueRoute` / `prksVuePresentRoute`. The registered feature presenter writes that owner's `PrksRouteInstance`. Per-feature route `window.prksVuePresent*` entry points are retired. The search query codec is `frontend-app/src/features/search/codec.ts`. Legacy callers use the one `prksSearchQueryCodec` bridge from `frontend/js/search-query-codec.js`, loaded before `saved-views.js`. Vue imports that module. Route-local runtime cannot live on `window`. Generations are never compared across TabContexts. Global shell and navigation state, including sidebar active state via `prksSyncSidebarActive`, belongs to the shell/router and is published for the Main TabContext before the route paints. A Vue route view renders route content and does not mutate that shell state. Generic browser lifecycle (listeners, observers, timers) and this PRKS route-instance lifecycle are different concerns. Do not treat the route-surface layer as a VueUse stand-in, and do not add Vue Router.

Progress (`#/progress?status=...`) is the first consumer (`frontend-app/src/features/progress/`). The legacy route still loads `works-browse:index` and applies `prksEffectiveBrowseRows(..., 'works-browse')`. Vue only filters that effective snapshot by status. It does not read the browse cache, durable operations, conflicts, or TanStack Query. `prksWorkCardHtml()` remains the shared Work-card contract; Progress mounts that HTML and does not own a second card. Owner session bookkeeping, mount/dismiss, generation guards, cleanup registration, and host-local early presentation live in the shared route-surface lifecycle, not in a second Progress session. An early request names its route feature. The dispatcher delivers it only to that feature's registered presenter, and the host that stored the request is the host that paints.

Concepts index/detail (`#/concepts`, `#/concepts/:id`) is the first medium-complexity route consumer (`frontend-app/src/features/concepts/`). The legacy coordinator still owns effective Concept resolution (`prksEffectiveConceptRows` / `prksEffectiveConceptDetail` / work references). Vue receives a typed projection and typed intents that call existing durable Concept APIs. Do not introduce TanStack Query for Concepts effective projection. Do not add Vue Router. `frontend/js/components/concepts.js` retains shared research-index/markdown helpers and `prksCreateConceptFlow` for Arguments and note markup; Concept-specific DOM renderers are removed.

Concepts, Positions, and Arguments share research-index search, scope paint, and markdown fallback in `frontend-app/src/research-index/`. Each route still owns its copy, ids, and intents. Positions index/detail (`#/positions`, `#/positions/:id`) follows the same route-surface pattern (`frontend-app/src/features/positions/`). The legacy coordinator still owns `prksEffectivePositionRows` / `prksEffectivePositionDetail`, pending create/delete, the pending Argument-name overlay, and Work-metadata hydration. Vue receives that already-effective projection and sends create/navigation intents to the existing durable APIs. Do not read the durable queue from Vue. Do not add Vue Router or a Pinia Position store. `frontend/js/components/positions.js` no longer paints the index or detail.

Arguments and Stances are one Argument record family (`#/arguments`, `#/arguments?kind=argument`, `#/arguments?kind=stance`, `#/arguments/:id`) in `frontend-app/src/features/arguments/`. The legacy coordinator still owns `prksEffectiveArgumentRows` / `prksEffectiveArgumentDetail`, the kind subset, pending create/delete, pending Position and Argument name overlays, and Work-title overlays. Vue receives that projection. Edit drafts stay on the mounted detail instance and persist only through `prksCommitArgumentEditorDraft` (dirty scalars and dirty source/target aggregates) and the existing create/delete APIs. Argument edit mode does not use the owned-draft leave confirm; a reconnect refresh still skips `ctx.ui.argumentEditing`. `frontend/js/components/arguments.js` keeps `prksCreateArgumentFromWork` and no longer paints the index or detail.

People index, role views, and Person detail (`#/people`, `#/people/role/:role`, `#/people/:personId`) follow the same route-surface pattern in `frontend-app/src/features/people/`. The legacy coordinator still owns `prksOfflinePeopleFetch` / `prksEffectivePersonRecord`, pending create/delete/field edits, effective Work relationships, role overlays, Person Group membership overlays, and pending Work title/metadata. Vue receives that projection. Role views filter the same collection locally. Typed intents call `savePersonProfileDraft`, `deletePerson`, `prksTogglePersonWorksEdit`, `prksRemoveWorkRoleLink`, and `prksOpenNewPersonModalFromPeoplePage`. Index create records the owning TabContext; the modal then navigates that still-valid owner with `{ tabId }`. Ribbon and command-palette Person creation stay on `openModal('person-modal')` without that origin. The profile form lives in the focused Person detail pane and mutates one `personProfileDraft` object; the sidebar stays a summary. Person Groups index and detail (`#/people/groups`, `#/people/groups/:groupId`) follow the same route-surface pattern in `frontend-app/src/features/person-groups/`. The legacy coordinator still owns the effective Group catalogue, hierarchy, pending create/delete/field edits, pending membership, and effective Person names. Vue receives that projection. Typed intents call `savePersonGroupEditor`, `deletePersonGroupEditor`, and `prksOpenNewGroupModalFromGroupsPage`. Index create records the owning TabContext; the modal then navigates that still-valid owner with `{ tabId }`. A stale origin does not navigate a replaced pane. Ribbon and command-palette Group creation stay on `openModal('group-modal')` without that origin. Search and collapse are per TabContext. The group editor lives in the detail pane; the sidebar stays a summary. `prksRemoveWorkRoleLink` remains the Work-role unlink helper for both the Person page and the Work page. Reconnect refresh still skips `ctx.ui.personDetailEditing`. Do not read the durable queue from Vue, add a Person store, or route Person mutations around those wrappers.

Work detail (`#/works/:workId`) stays on the legacy coordinator. `case 'work'` resolves offline and durable lifecycle, then publishes a typed projection from `frontend-app/src/features/work/`. The maintainer build emits that module as the classic script `frontend/js/work-route-projection.js`, loaded before `app.js`. The projection records availability (`ready`, `unavailable`, `not-found`), lifecycle (`ordinary`, `unsent-create`, `pending-delete`), provenance (`server`, `cache`, `local-unsent`), the editor Work, and `effectiveWork`. The editor Work is the acknowledged record plus the video-source and folder/playlist placement the route already applied. `effectiveWork` carries the metadata and role overlays from the existing helpers and is not stored with `ctx.setEntity`. A cached ordinary Work paints from maps already in memory; the route's durable read applies those overlays onto `effectiveWork` when it resolves, and only after `stale()` still matches this owner. It is stored on the originating TabContext. Main and Secondary do not share the object, and a stale generation does not publish. `ctx.setEntity('work', …)` remains that editor Work and still feeds `renderWorkDetails`. `WorkMainSurface` paints that tile's header, viewer host, and Research Notes anchor for the owning TabContext. The legacy shell is used only when `prksVuePresentWorkMainSurface` is absent, and only after this generation and Work are still current. A bridge that rejects the model does not dismiss the tile or paint. The Vue shell unmount is a TabContext cleanup, so it runs after the PDF runtime and Research Notes disposers. `renderVideoViewerPane` stays a pure function of the effective source and supplies the iframe. The PDF host is `[data-prks-role="pdf-viewer"]`, and `initPdfViewerForWork` still owns the runtime. Work PDF (#296) keeps that host inside the owning pane. `renderWorkDetails` calls `initPdfViewerForWork` only while this generation is current and the effective kind is `pdf` with `file_path`. Mount is the deferred `pdfDeferredSetup` timer; the callback returns when that generation is stale, so A→B does not create the viewer. The runtime is the suspendable `pdf` slot on `ctx.resourceRegistry`. `initPdfViewerForWork` captures the owner ticket before `pdfDeferredSetup` and registers with that ticket. `ctx.getResource('pdf')` reads the same slot. `prksRenderTabRoute` flushes `flushLastPage` before the next paint. Tab close destroys the TabContext, and `teardownRuntime` runs the resource disposer. Warm park is `prksWarmParkTabContext`: `ctx.suspend` keeps the host DOM and the pdf resource with the parked pane, and the right panel is not moved with that pane. Hide unmounts through `coldParkContext`. `works-pdf.js` owns viewer integration and annotation-persistence orchestration, including persistence setup, materialization, and the existing legacy annotation API path. `pdf-work-runtime.js` owns the per-tab PDF runtime and liveness state. `pdf-annotation-state.js` and `store.savePdfAnnotation` own the durable annotation intent and write boundary. P1–P5 must not create a second persistence or durable-operation owner. The service-worker whole-file cache prime is offline PDF file-byte caching in `initPdfViewerForWork` (`prksRequest(work.file_path)`), not annotation persistence. The leave confirm for `prksHasPendingWorkAnnotationSync` stays on the route leave. #39 owns later reading and annotation presentation. This boundary does not rewrite persistence, replace the viewer, or take #58.

The #296 implementation breakdown is recorded here before any #39 code. #324 stays the ownership record. These slices stay separate. Do not fold them into one `works-pdf.js` change. For PDF integration, the Vue Work surface supplies the pane-local PDF host while retaining the surrounding Work and Research Notes surface. `works-pdf.js` owns viewer integration and annotation-persistence orchestration, including persistence setup, materialization, and the existing legacy annotation API path. `pdf-work-runtime.js` owns the per-tab PDF runtime and liveness state. `pdf-annotation-state.js` and `store.savePdfAnnotation` own the durable annotation intent and write boundary. P1–P5 must not create a second persistence or durable-operation owner. `initPdfViewerForWork` owns the pdf TabContext resource (kind `pdf`, suspendable, registered with the ticket captured before `pdfDeferredSetup`). `prksRequest(work.file_path)` is offline PDF byte caching, not annotation persistence. #61 owns annotation metadata semantics. #39 may display them and must not redefine them.

1. Viewer/UI adapter and lifecycle regression harness. `frontend-app/src/features/work/pdf-adapter.ts` is the Vue-facing read and intent boundary for the existing `pdf` TabContext runtime. A mount intent carries the generation and Work id captured for that request and rechecks `ctx.isCurrent(expectedGeneration)`, the live Work id, and the requested Work id immediately before `initPdfViewerForWork`. Flush and resize intents call the runtime and do not run once that runtime is destroyed. The adapter does not store PDF state, call `savePdfAnnotation`, or own the legacy annotation POST. `renderWorkDetails` still calls `initPdfViewerForWork` directly. Behavioral coverage proves current-generation mount, a stale Work-A mount after the same TabContext advances, a same-Work generation change, A→B stale setup, repeated mount/teardown/mount, the pane-local host, independent Main and Secondary runtimes, route replacement flushing the last page before teardown, tab close, cold park, warm park and resume, bounded warm-PDF eviction, offline reopen, and the pending-annotation leave confirm. No persistence rewrite.
2. In-document search. `pdf-work-runtime.js` owns the per-tab search session on the pdf runtime: open state, query, match count, active match, and the epoch that drops a stale result. That session is not annotation state and not a Vue store. `works-pdf.js` binds Ctrl/Cmd+F on the pane-local PDF surface, stamps `dataset.prksOwnerTabId` and `dataset.prksOwnerGeneration`, and forwards the find bar through `setSearchDriver`. The EmbedPDF search plugin highlights matches and moves among them on the current viewer. Opening or closing search does not call `createPrksPdfViewer` or change `viewerSetupToken`. A result applies only while `prksPdfSearchStill` matches that generation, runtime, and viewer. Main and Secondary keep separate sessions. `pdf-adapter.ts` reads and forwards search intents and does not store them. The annotation popup and drawer are not part of this slice.
3. Anchored annotation popup editing. The pdf runtime owns the popup session (`openAnnotationPopup` / `closeAnnotationPopup`). `works-pdf.js` orchestrates comment save and delete through the existing viewer mutation and `pdf.flushAnnotations` (`store.savePdfAnnotation`). The Vue surface (`pdf-annotation-popup.ts`) positions with Floating UI and does not own annotation state, selection, lifecycle, persistence, or the viewer. The right-panel `#pdf-annotation-editor` is removed. The annotation list's Edit/Add comment control lives in the overlay drawer and opens this same popup.
4. Viewer-owned annotation drawer, overlay first. `pdf-work-runtime.js` owns the per-tab drawer session (open and epoch) on the pdf runtime. Opening or closing it does not call `createPrksPdfViewer`, change `viewerSetupToken`, or change PDF width, zoom, or reading position. `works-pdf.js` toggles that session from the viewer toolbar and still owns list refresh, jump, comment, delete, and `[[pdf:id]]` copy through the existing viewer flush path. Vue (`pdf-annotation-drawer.ts`) paints the overlay and forwards generation-checked intents. It does not store the list or write annotations. The right-panel Annotations tab no longer paints the list; it points at this drawer. Pin and resize stay in slice 5.
5. Pinned and resizable drawer, plus zoom and state preservation. `pdf-work-runtime.js` owns pin preference, width, and placement (`overlay`, `pinned`, `sheet`) on the pdf runtime. `works-pdf.js` applies that placement to the pane and calls `pdf.resize()` when the viewer box changes, including when a pinned pane changes size at the same drawer width. Pin, unpin, and width changes do not call `createPrksPdfViewer` or change `viewerSetupToken`. Overlay and the mobile sheet do not take viewer width. Pinned mode is a flex sibling, so the viewer box narrows and EmbedPDF recomputes Fit Width and Fit Page from that box; an explicit percentage is not assigned a new zoom. Width is clamped and remembered in device-local `prks.pdf.annotationDrawer`. A pinned width that would leave the viewer below its minimum is capped for that pane. A pane that cannot hold the minimum drawer plus the minimum viewer stays overlay. Forced mobile layout and a viewport at or below 900px use a full-pane sheet. The width handle is `prksBindDrawerWidthSeparator` in `workspace-split.js`. Vue renders it and forwards the ticket captured at gesture start. When the painted width or the effective min/max changes, Vue calls that binding's `refresh`, which repaints separator ARIA from the current getters and does not end or restart a drag. Rebinding stays on element change. It does not store the list or write annotations.

Research Notes still mount into that anchor. The view-mode right panel reads that projection through `WorkPanelRead`: bibliographic, status, and source fields, the people and tag summaries, and the folder and playlist lines. `editor` on that read model is the acknowledged Work plus source and placement. `display` is the overlay. Publish uses `workRouteProjection` only while that projection's Work object is still the installed entity; a later folder, playlist, tag, or metadata acknowledgement starts from the installed Work and fresh metadata and role overlays. A metadata refresh does not replace the mounted people overlay with acknowledged roles, and it recomputes the document-type badge and status icon from the overlay. A folder line is a route only when it has an id. The bibliographic host lists overlay `author_text` as Author and `thumb_page` as Thumbnail page, in the synced-field order. Those values stay off the editor base. A `source_url` is listed for every source kind, so a non-PDF Work whose only metadata is that URL is not an empty card, and a PDF still shows it as the original URL link. A focus switch copies the open metadata form into the previous owner's draft while that owner still shows the shared panel, flushes that owner's private notes, and only then replaces the panel. A note save that finishes after the switch stays on the original Work. The metadata editor is `WorkMetadataEditor`: a Vue-local draft and explicit baseline on the owning TabContext, with `workMetaEditSession`. Saves call `store.saveWorkMetadataFields` and `prksSaveWorkSource`. `prksSaveWorkFieldDurably` remains the single-field API. The draft is not the server base, and `effectiveWork` is not the baseline. The leave guard compares the draft to that baseline. A same-Work refresh keeps the session. A save checks the session again before the durable write, so a panel that now shows another Work does not take it, and an older completion does not settle a reopened session. Role links still save through `prksSaveWorkPersonRoleDurably`. The role modal records its opener, generation, and Work when `openModal('role-modal')` starts, and Create Link consumes that origin. Credit rename, unlink, and add recheck `prksWorkRoleIntentStill` before that call. An unmounted modal save rechecks that captured owner after its base read and before `store.saveWorkPersonRole`. A failed recheck returns `stale`, and Create Link leaves the modal open without the offline-prep alert. Person-page unlink passes a caller still fence through that same save: a Person route rechecks the captured Person route, entity, and generation, and a Work route rechecks the captured Work owner. A failed unlink fence is a silent stale no-op. Alerts and the busy restore belong to the role-modal opening that started them, so an older completion does not alert into a newer opening or clear the busy state that opening owns. Mode and modal close run only when that opener is still focused on the same Work and that role-modal opening is still current. View-mode people refresh through `prksRefreshOwnedWorkPanelRead`, which reads `prksEffectiveWorkDetailRoles` and does not let the role editor write chips. A non-view paint falls through to the classic people host when that refresh does not apply. Tag add and remove still call `store.coalesceWorkTag`. The Work-panel tag handler rechecks the full `owns(ctx, state)` predicate immediately before that call. View-mode tag chips go through `prksRefreshOwnedWorkPanelTags`. When that helper returns null because `prksVueRefreshWorkPanelRead` is unavailable, view mode falls through to the legacy `#work-tags-list`. A false result from an already-mounted Vue bridge does not overwrite a Vue-owned target. Folder and playlist membership writes from the Work panel pass a session `still` fence through the `prksFileWorkInFolder` and `prksSetWorkPlaylist` wrappers, including playlist remove. Ownership checks live in those Work-panel handlers and wrappers. The fence is rechecked after `prksAcknowledgedWorkFolder` / `prksAcknowledgedWorkPlaylist` and immediately before `prksSetWorkFolderDurably` / `prksSetWorkPlaylistDurably`. A failed fence is a silent stale no-op. `prksSetWorkFolderDurably` and `prksSetWorkPlaylistDurably` do not themselves check panel ownership. Work-panel New Folder and New Playlist record the opener tab id, generation, and Work id, and revalidate that captured session before the membership write and through the same fence. Creating the Folder or Playlist may finish when that session is stale; the relationship attachment does not. Work Reminders (WorkPrivateNotes) keep the unsaved session on the owning TabContext (workPrivateNoteSession), so Main and Secondary do not share a draft. Saves go through prksSaveWorkNoteDurably. A focus switch flushes that owner's session before the panel is replaced. A completion stays on the Work that owned the dirty note. A generation change remounts the Reminders card and binds a new editor for that generation. scope_busy retries through the current editor when it is the same TabContext, Work, and generation; an older generation does not retry into the new one. People and tags mode keep that card when the read surface is dismissed. An unsaved draft stays on that TabContext if its session is replaced before the write fails. A successful settlement clears that hold and does not enqueue the same body again. A committed reminder does not trip the metadata leave guard. A failed metadata-queue read does not refresh the read surface from an empty operation list. This module does not enumerate durable operations and is not a Work store, Pinia mirror, Vue Router, or TanStack mutation owner. Research Notes (WorkResearchNotes) own the pane shell on the TabContext. The mount anchor is display:contents, so .work-notes-pane stays the flex item of .work-workspace. EasyMDE, the wiki, Concept, and Argument pickers, and prksSaveWorkNoteDurably stay the editor and the durable note API. The unsaved buffer is that owner's workResearchNoteSession, so Main and Secondary do not share a draft. A save started on Work A does not paint onto Work B. A same-Work refresh still receives that save's settlement when no newer edit replaced it. A finished save with nothing queued says All changes saved; a queued save says it is waiting to sync, or saved locally while offline. A domain result keeps its own message on that refresh. A thrown save stays Error saving changes. A notes-state read started for Work A stays the base of that save, and it is stored on the TabContext only while that owner may still paint Work A. The interactive Research Notes field is the CodeMirror input, named Research Notes. A later generation of that TabContext does not take that save's status. Omitting the TabContext on prksWorkNotesMarkEdit still records the draft on the context that holds the editor. Destroying the notes editor dismisses that TabContext's Research Notes mount. Collapse stays prks.workNotesCollapsed.<workId>. Metadata writes and relationship editors stay on their existing owners. For PDF integration, the Vue Work surface supplies the pane-local PDF host while retaining the surrounding Work and Research Notes surface. `works-pdf.js` owns viewer integration and annotation-persistence orchestration, including persistence setup, materialization, and the existing legacy annotation API path. `pdf-work-runtime.js` owns the per-tab PDF runtime and liveness state. `pdf-annotation-state.js` and `store.savePdfAnnotation` own the durable annotation intent and write boundary. P1–P5 must not create a second persistence or durable-operation owner. `initPdfViewerForWork` owns the pdf TabContext resource on `ctx.resourceRegistry`. `prksRequest(work.file_path)` is offline PDF byte caching, not annotation persistence. #61 owns annotation metadata semantics. #39 may display them and must not redefine them.

Playlists index/detail (`#/playlists`, `#/playlists/:id`) follows the same route-surface pattern in `frontend-app/src/features/playlists/`. The legacy coordinator still owns `prksEffectivePlaylistRows` / `prksEffectivePlaylistDetail`, pending create/delete/field edits, Work↔Playlist membership, the single order aggregate, and Work title/metadata overlays. Vue receives that projection. Typed intents call `updatePlaylist`, `addWorkToPlaylist`, `removeWorkFromPlaylist`, `reorderPlaylist`, `deletePlaylistFromDetail`, and `prksSaveWorkFieldDurably`. Index create opens `prksOpenNewPlaylistModalFromPlaylistsPage` with the owning TabContext; the modal then navigates that still-valid owner with `{ tabId }` and does not read `location.hash`. Do not read the durable queue from Vue, add a Playlist store, or route Playlist mutations around those wrappers. `frontend/js/components/playlists.js` keeps Work→Playlist attach and those public wrappers. Reconnect refresh still skips `ctx.ui.playlistEditing`. Do not force Playlists through `frontend-app/src/research-index/`.
Folder detail (`#/folders/:id`) follows the same route-surface pattern in `frontend-app/src/features/folder-detail/`. The legacy coordinator still owns offline folder resolution, `prksEffectiveFolderDetail`, pending folder create/delete, and pending Work filing. Vue receives that projection. Embedded Work summaries go through `prksEffectiveFolderDetailWorks` (`prksEffectiveWorkSummaryRows`); Vue does not read the durable queue. Typed intents call `prksDeleteFolderFromDetail` (`deleteFolderCanonical`) and `prksOpenNewFolderFromDetail` (the shared folder modal). Hierarchy fill, layout, hierarchy nav, and offline bind stay `prksCommitFolderDetailSurface` in `frontend/js/components/folders.js`, writing into the Vue host. That host-side writer is removed by #303 B2. `prksWorkCardHtml` on this route is removed by #303 B4. Folder→Folder sets `__prksRetainFolderDetailSurface` before `beginRoute` so the hierarchy shell stays mounted. A same-generation change to the hierarchy nav projection rebinds `prksMountFolderHierarchyNav` after Vue replaces that subtree. The hierarchy tree refresh stays on the route generation inside `prksCommitFolderDetailSurface`. `renderFolderDetails` is removed. Recent (`#/recent`) follows the Progress route-surface pattern in `frontend-app/src/features/recent/`. The coordinator still owns the Recent snapshot, `prksEffectiveBrowseRows(..., 'recent')`, and `prksEffectiveRecent`. Vue does not sort or read the durable queue. It paints `prksWorkCardHtml` with the last-opened subtitle and releases that route's thumb preview and lazy thumbs before rewriting the collection or unmounting. That card mount is removed by #303 B4. `renderRecent` is removed. Unavailable Recent stays `prksOfflineRenderUnavailable`.

Search (`#/search`) and Saved View detail (`#/views/:id`) share one query/result boundary. Both are Main-only routes. The coordinator owns the read: `prksEffectiveSearchResults` in `app.js` runs `fetchSearch`, hydrates the pending maps, and applies `prksEffectiveWorksSync`. The server decides membership, and results are never cached or stored with a view. Vue receives those effective rows and does not fetch, read the durable queue, or use TanStack Query for them. `frontend-app/src/features/search/` owns the request projection, the title and form, and `SearchResultsCollection`, the one result collection both routes paint. That collection mounts `prksWorkCardHtml` with the abstract-excerpt subtitle and releases its own thumb preview and lazy thumbs before a rewrite and on unmount. The card mount is removed by #303 B4. Search intents submit through `hashFromDefinition` and navigate the owning tab. Save View passes that owner's canonical hash to `prksOpenSavedViewModalFromCurrentSearch`. `frontend-app/src/features/saved-views/` paints Saved View detail and not-found. The record is the owning TabContext's `savedView` entity. Edit opens the shared `#saved-view-modal` (`prksOpenSavedViewModal`, then `updateSavedView`). Delete is `prksDeleteSavedViewFromDetail`, which rechecks the owner after confirm and before navigating that tab to `#/views`. The search query codec (`definitionFromRoute`, `hashFromDefinition`, `optionsFromDefinition`, `summaryText`) lives in `frontend-app/src/features/search/codec.ts`. The Saved Views index (`#/views`) is painted by `SavedViewsIndexRoute` in `frontend-app/src/features/saved-views/`. The coordinator loads that list with `fetchSavedViews`; Vue does not fetch or cache it. Row summaries call `summaryText` from that codec. Index edit fetches `fetchSavedView` and opens `prksOpenSavedViewModal` only while that owner is still on `saved-views`. Index delete is `prksDeleteSavedViewFromIndex`: confirm, `deleteSavedView`, then `prksNavigate('#/views', { replace: true, tabId })` on the owning tab so the index refetches. The refresh uses that index hash, so a focused or main route elsewhere stays where it is. `renderSavedViewsIndex`, `bindIndexActions`, `openEditById`, and `confirmDelete` are removed. `components/search.js`, `renderSavedViewDetail`, `renderSavedViewNotFound`, and `prksSearchResultCardsHtml` are removed. Offline Search and Saved Views stay `prksOfflineRenderUnavailable`.

File types index (`#/types`) and type detail (`#/types/:docType`) share the works-browse snapshot. Both stay Main-only. The coordinator still owns `prksOfflineWorksBrowseFetch`, `prksEffectiveBrowseRows(..., 'works-browse')`, and the grouping in `prksTypesIndexModel` / `prksTypesDetailModel` (`frontend/js/components/types.js`). Vue receives those already-grouped rows and does not fetch, regroup, read the durable queue, or use TanStack Query. `frontend-app/src/features/types/` paints the index and the detail collection. Detail mounts `prksWorkCardHtml` with the document-type badge hidden and releases that route's thumb preview and lazy thumbs before a rewrite and on unmount. That card mount is removed by #303 B4. The index badge is `prksDocTypeBadgeHtml` and the row chevron is `prksIcon`, both removed with the shared Vue badge and icon work in #303 B4. Browse mode stays `prksWorkBrowseModeToggleHtml('prks-work-browse-mode-types')`. Sidebar counts are published by the coordinator from the same model, on the owning TabContext, and only while that generation is current. `renderTypesIndex` and `renderWorksByDocType` are removed. Unavailable Types stays `prksOfflineRenderUnavailable`.

Tags (`#/tags`) stays Main-only. The coordinator in `app.js` loads `fetchTags({ used: true })` and passes that list to `frontend-app/src/features/tags/`. Vue paints the cloud and the alias, merge, and delete controls. It does not fetch, read the durable queue, or use TanStack Query. Scale and the CSS color guard live in the Tags projection. Alias add and remove stay raw `POST`/`DELETE` `/api/tags/:id/aliases` in `frontend/js/components/tags.js` (`prksTagsAddAlias`, `prksTagsRemoveAlias`), with `prksOfflineGuardMutation` and `prksOfflineMarkTagsChanged`, because aliases are not part of a cached work or folder tag list. Delete is `prksTagsDelete` → `prksDeleteTagDurably`. Merge is `prksTagsMerge` → `mergeTags` → `prksMergeTagDurably`. Each intent rechecks the owning generation after confirm and before the write, and again before `prksReloadTagsVocabulary` repaints that owner. A stale completion does not paint another tag's aliases or another pane. A reload resumes the dialog still open in that pane, alias or merge. A dialog the user closed stays closed, and a newer alias or merge dialog is not replaced by the write that was in flight. A failed post-mutation `fetchTags` does not paint the empty state; the existing list stays and the page shows the refresh failure. Recoverable add, remove, delete, and merge failures stay inline in the open dialog. `prksCloseTagsAliasModal` and `prksCloseTagsMergeModal` close one pane when Escape passes that dialog, and every pane when called with no element for overlay dismissal. `renderTagsPage` and the `prksTagsPageCtx` singleton are removed. The merge arrow is `prksIcon('arrowRight')`, removed with the shared Vue icon work in #303 B4.

Publishers (`#/publishers`) stays Main-only and online-only. Offline stays `prksOfflineRenderUnavailable` before any read. The coordinator in `app.js` loads `fetchPublishersInUse` and passes that list to `frontend-app/src/features/publishers/`. Vue paints the list, the create field, and the alias dialog. It does not fetch or use TanStack Query. Create, alias add, alias remove, and delete stay raw HTTP in `frontend/js/components/publishers.js` (`prksPublishersCreate`, `prksPublishersAddAlias`, `prksPublishersRemoveAlias`, `prksPublishersDelete`), each behind `prksOfflineGuardMutation`. There is no durable publisher queue. Each intent rechecks the owning generation after confirm and before the write, and again before `prksReloadPublishersPage` repaints that owner. A stale completion does not paint another publisher's aliases or another pane. A reload resumes the alias dialog still open in that pane. A dialog the user closed stays closed, and a newer publisher's alias dialog is not replaced by the write that was in flight. A failed post-mutation `fetchPublishersInUse` does not paint the empty state; the existing list stays and the page shows the refresh failure. Recoverable create, alias, and delete failures stay inline on the create row or in the open dialog. The publisher name is the route button: `data-prks-route` and the middle-click flag sit on that control, and the alias button stays outside it. `prksClosePublishersAliasModal` still closes the open dialog for Escape and overlay dismissal. `renderPublishersPage` and the `prksPublishersPageCtx` singleton are removed. The row icon is `prksIcon('building-2')` and the create field icon is `prksTagPlusIconHtml`, both removed with the shared Vue icon work in #303 B4.

Processing Files (`#/processing-files`) stays Main-only. The coordinator in `app.js` loads the inbox with `fetchProcessingFiles({ rescan: true })` plus `fetchPersons` and `fetchFolders`, publishes `pendingCount` on the owning TabContext, and passes those rows to `frontend-app/src/features/processing/`. Vue paints the inbox and the metadata cards. It does not fetch, read a durable queue, or use TanStack Query. Save is `prksProcessingSave` → `patchProcessingFile`. Import is `prksProcessingImport` → `importProcessingFile` after that save. Both stay upload-style HTTP in `frontend/js/components/processing-files.js`. There is no processing-file durable queue. Tag search, tag create (`prksCreateTagDurably`), folder quick-create (`createFolder`), and person quick-create (`prksQuickCreatePersonForSearchField`) stay in that module. Those three quick-creates return a structured success or error. Recoverable failures stay on the card message; the shared person helper alerts only when the caller has no local surface (`localError` skips that alert). The Create new folder button uses a `creatingFolder` busy state, an active `Creating…` label, and `aria-busy`, and a second click while that create is pending does not start another folder. Each intent rechecks the owning generation before the write and again before `prksReloadProcessingFiles` repaints that owner. The inline PDF preview iframe and the resize listener are owned by `prksProcessingAttachResources` / `prksProcessingReleaseResources` for that owner. Dismiss clears the iframe `src` and removes the listener. `renderProcessingFilesPage` and `prksRenderProcessingFilesPageWithFetch` are removed. Segmented status and role controls and the document-type menu stay the shared classic widgets, mounted into `work-html-slot` hosts.

Vue primitives live in `frontend-app/src/components/` (`PrksButton`, `PrksStatusText`, `PrksSectionHeader`). Stories live beside them as `*.stories.ts`. Storybook config is `frontend-app/.storybook/`. `DESIGN.md` remains authoritative. Reuse these primitives before inventing another button, status, or section-heading component. They must keep the existing `.prks-btn`, `.prks-settings-hint`, and `.prks-settings-section__title` classes. Do not split `frontend/css/style.css` or copy it into a single-file component.

Storybook is maintainer/build-time only. Commands, from `frontend-app` after `npm ci --ignore-scripts`: `npm run storybook`, `npm run build-storybook`. CI builds Storybook after a successful diff against the PR base or a fetched nonzero push-before SHA shows a change in `frontend-app/` or the static-analysis workflow. A missing or unreadable baseline fails that step. A manual `workflow_dispatch` run builds Storybook without a diff. Do not commit `frontend-app/storybook-static/` or put Storybook output in `frontend/`. Accessibility checks run in the Storybook UI via `@storybook/addon-a11y`. Do not add a Playwright story runner beside Vitest.

Storybook MCP is not committed. `@storybook/addon-mcp@10.6.0` is preview, peers on the browser Vitest addon, needs Storybook running, and the agent connection is user-level. The exact setup is in `frontend-app/README.md`. Official Vue ESLint is also deferred there: the checked-in ESLint config is the legacy JavaScript bug-rule set, and a Vue/TypeScript config would be a separate lint family.

The Vue HTTP transport is a real `fetch` boundary for PRKS reachability. A resolved response, of any HTTP status, calls `prksOfflineNoteRequestSuccess`. A non-abort transport failure calls `prksOfflineNoteRequestFailure` only after that query's retries are exhausted; mutations are not retried, so their transport failure is final. Managed PDF GETs (`/api/pdfs/...`) are not a reachability signal. Abort, HTTP error envelopes, and JSON/domain failures are not connectivity changes.

## Cross-boundary bulk mutations

Frontend bulk operations must use a transactional backend bulk operation when one exists. Do not implement bulk UI behavior as one HTTP mutation per selected Work; preserve server-side validation and atomicity across the selection.

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

The shared link layer intercepts anchor clicks in the capture phase, so
`handleNavEvent` must bow out entirely — no `preventDefault`, no
`stopPropagation` — for a destination the owning component has explicitly
marked `aria-disabled="true"`, in every intent (same tab, background tab,
tile). That component then refuses the activation and explains why, from an
ordinary bubble-phase `click`/`auxclick` handler; a middle click only ever
arrives as `auxclick`. Offline pages rely on this to keep a relationship's real
`href` inspectable while saying the destination is not cached; without it the
capture-phase handler would navigate first and swallow the explanation.
`onMiddleMouseDown` is deliberately *not* part of that contract: it only
suppresses the middle-button mousedown default (autoscroll) and never
navigates, so it has nothing to bow out of.

Alt-click and `prksNavigate(..., { target: "tile" })` open a Secondary leaf when the route is tile-capable.

User-facing copy says "Split view", "Split right", "Split down", "Make main", "Hide from split", "Hide split" / "Show split". Internal APIs stay `tile`, `secondaryTree`, split-node IDs, and `target: "tile"` — never user-facing.

Existing parked tabs should be tiled through `prksWorkspaceTileTab(tabId)`, not duplicated through `navigate(... { target: "tile" })`. Split right/down onto a specific focused leaf go through `prksWorkspaceSplitLeaf(targetLeafTabId, axis, options)`, reusing an existing tab (`options.tabId`) or creating one (`options.hash`) — never duplicating.

Main never recursively splits; it is permanently the single root pane. Secondary is `workspace-tree.js`'s recursive `leaf`/`split` tree (`secondaryTree`): `null` (no Secondary), a bare `{ type: "leaf", tabId }` (the common single-Secondary case — do not wrap it in a pointless split node), or a `{ type: "split", id, axis, ratio, first, second }` node whose children are themselves leaves or splits. A tab occurs at most once in `secondaryTree`, and Main's own tab never appears inside it. Split-node IDs are stable per-runtime keys (never array index, DOM position, or a child's tab ID) used for DOM reuse, resize ownership, and targeted mutation; they are in-memory only, never persisted. Route capability for a Secondary leaf covers works, people, concepts, positions, arguments, playlists, and folder detail (`folder-detail`); the Folder library index (`#/folders`) stays main-only. Do not add further route types as an incidental follow-on to recursive splitting.

The canonical workspace model is `frontend-app/src/workspace/` (TypeScript), built to the classic script `frontend/js/workspace-model.js`. That file is the only tree and structural-state implementation. `workspace-tree.js` is a one-way adapter: it owns the per-runtime split-id counter and delegates every pure helper. `workspace-tabs.js` holds the one live workspace object and is the effect coordinator: leave/dirty preflight, history/URL, persistence notification, and TabContext mount/park/destroy. It publishes one detached frozen projection through `prksWorkspaceSubscribe` after committed canonical changes. Each publish carries a `commit` id. Subscribing reuses that current projection and does not mint another commit. A publish with no listeners drops the cache and does not advance the commit, so a later subscribe rebuilds the current state once. After the Vue shell finishes both teleports for that projection, it calls `prksWorkspaceOnShellCommit`. Focus restore, tab reveal, and overflow measurement run in that callback. Overflow is measured before reveal so the overflow control cannot undo the scroll. Multiple subscribers are allowed. None of them own state. Do not add a Pinia store, a Vue copy of `prksWorkspaceSnapshot()`, a second tab list, or a second focused/Main owner.

`frontend-app/src/workspace-shell/` is the presentation layer only. It renders the global tab strip, the Main pane frame, and the recursive Secondary tree from that projection, and it sends intents (`activate`, `focus`, `close`, `tile`, ratio, menu) back through the coordinator. It must not mutate the snapshot. Stable `.prks-tile__body` content hosts live in `workspace-hosts.js`, keyed by `tabId`, and are reparented into the pane frame. Mount places into a pane slot that is already in the document; otherwise the host is parked in `#prks-workspace-host-parking` until the shell places it, and that reparent notifies the PDF runtime to resize. Close releases exactly that host. Focus, ratio, reorder, and ordinary shell rerenders do not. Parked and hidden panes are not given a visible host; the host element stays so a later show can place the same node. TabContext and the route-surface own what is mounted inside the host.

Production no longer paints the tab strip or the recursive tree. Removed from `workspace-tabs.js`: `tabRoleFlags`, `syncTabTrailing`, `createTabWrap`, `applyTabWrap`, and `paintProduction`'s DOM reconciliation. Removed from `workspace-tiling.js`: `renderTreeNode`, `createTile`, `ensureTile`, `fillHeader`, `applyTileClasses`, `pruneStale`, and the tree-reconciling bodies of `prksWorkspaceSyncTiles` / `prksWorkspaceApplyFocus` (those two exports remain no-ops). The frozen DOM oracle for the Node split/menu selftest is `tests/browser/fixtures/workspace-tiling-legacy-painter.js`. `workspace-tiling.js` still owns the narrow-width predicate, canvas and nested ResizeObservers, and pane focus gestures.

Drag hover/preview is not canonical — a drop commits through the same commands. Production sensors live in `frontend-app/src/workspace-dnd/` (Pragmatic DnD), bound by Vue `WorkspaceShell` onto `#prks-workspace-tabs`, `.prks-workspace-tab`, `.prks-tile[data-prks-tab-id]`, `.prks-tile-header__grip`, and `.prks-workspace-canvas`. Shell projection commit calls `reconcile()`; do not add a second shell subscription or prefer MutationObserver sync. `frontend/js/workspace-drag.js` is a geometry + `prksWorkspaceInitDrag` / `prksWorkspaceCancelActiveDrag` shim only. Never run two drag systems. Context menus still open through `prksWorkspaceOpenTabMenu`. Named workspaces (#58) will serialize this model later; do not add that format here. The #251 route-surface stays per TabContext. Workspace state stores canonical hashes only and does not own feature Vue trees.

Pure transforms (`planHideLeaf`, `planMakeMain`, `planCloseTab`, `planReorder`, `planMovePane`, `planSplitLeaf`, ratio/focus/mode planners, tree helpers) take immutable inputs and return the next state. They do not touch DOM, history, async leave, or TabContext. Those planners are the shipped structural contract. Effectful steps stay in `workspace-tabs.js`. `prksWorkspaceSnapshot()` still returns the external shape, including ephemeral `titleRouteGen` reattached from the live tabs; the typed snapshot itself does not store that field.

Tree mutation goes only through those pure helpers (`findLeafByTabId`, `replaceLeaf`, `splitLeaf`, `removeLeaf`, `replaceTabId`, `setSplitRatio`, `normalizeTree`, `validateTree`, `collectLeafTabIds`, `containsTab`, …); routes and UI code must never mutate `secondaryTree` structure directly. Removing a leaf always normalizes the tree afterward: a split node left with one child collapses into that child, repeated upward, so the tree never carries a redundant single-child split node; if the last leaf disappears, `secondaryTree` becomes `null` and the view returns to stacked.

At most `PRKS_MAX_VISIBLE_TABS` (4: 1 Main + 3 Secondary) TabContexts are ever mounted at once. Split right/down are disabled with an explanation once the cap is reached; ordinary New Tab is unaffected and still creates a parked tab.

Generic `target:'tile'` navigation and "Open in split view" are additive: they never evict an existing Secondary leaf. There is no user-facing "Replace split pane" operation. Default placement is unambiguous only when there is no Secondary tree (new bare leaf), exactly one Secondary leaf (split it), or a focused Secondary leaf in a recursive tree (split that one); otherwise placement is ambiguous and the operation is fail-closed — no new logical tab, no tree mutation, no mount, no paint, no leave check, just the ambiguity/cap announcement and `false`. The same fail-closed rule applies at the pane cap. New Tab is the only fallback that intentionally creates a parked logical tab regardless of ambiguity or the cap.

Close: parked closes only that tab. A Secondary leaf's close removes and normalizes the tree — it must never remount or otherwise touch any other leaf's TabContext — and retargets focus only when that leaf owned it: the closest surviving sibling in the collapsed subtree, else the nearest remaining leaf in deterministic depth-first tree order, else Main. Stacked mode, including a hidden split, keeps focus on Main. Main close promotes the first surviving Secondary leaf in that same deterministic order, else the right tab-strip neighbor, then left, then Home. Closing the last tab is an explicit non-success until Home is minted. Do not flash Home while a successor exists. Keep leave guards. Batch close (other tabs / tabs to the right) preflights every mounted tab being closed and aborts entirely on reject.

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

Workspace logical state is persistent; workspace runtime state is ephemeral. `frontend/js/workspace-persistence.js` owns all workspace `localStorage` behavior (schema, validation, debounce, restore, corrupt-snapshot cleanup). Do not write workspace storage from `workspace-tabs.js`, `workspace-model.js`, `workspace-tree.js`, `workspace-tiling.js`, `workspace-split.js`, `workspace-drag.js`, or `tab-context.js`. Never persist TabContext/runtime objects, editor drafts, effective constrained ratios, `narrowFallback`, split-node runtime IDs, Vue trees, or AbortControllers. `titleRouteGen` stays off the typed model. Restore happens before first normal mount. Parked restored tabs must not fetch. The current startup URL outranks a persisted Main route. Persistence failures must not break app startup.

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

The Main/Secondary divider, every nested Secondary split divider
(`workspace-split.js`, class `.prks-splitter`), and the PDF annotation drawer
width handle (`prksBindDrawerWidthSeparator`) share the one separator
implementation. Nested separators are keyed by split-node ID. The drawer
handle is that implementation's width mode: the Vue drawer renders the element
and forwards a generation-checked intent. Do not implement independent divider
drag, keyboard-resize, or ARIA logic in `works.js`, `works-pdf.js`, other
route components, or a second implementation inside `workspace-tiling.js` for
nested splits. `workspace-tiling.js` only calls into `workspace-split.js`; it
does not own pointer, keyboard, ARIA, or persistence logic itself. Drawer
width clamping and the device-local preference stay on the pdf runtime.

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
- external browser resources → `ctx.resourceRegistry` (`frontend/js/owner-resource.js`, built from `frontend-app/src/lifecycle/owner-resource.ts`). Research Graph is not warm-suspendable. PDF is the suspendable `pdf` kind: `initPdfViewerForWork` captures the owner ticket before `pdfDeferredSetup` and registers with it. `getResource('pdf')`, `clearResource('pdf')`, and `registerResource` share that slot. Warm park keeps the runtime readable and does not recreate the viewer; warm resume still resizes once from `prksResumeWarmTabContext`. Work role, Work tag, Work source, Work metadata, and Folder tag sessions are non-suspendable owner resources. Each production mount captures `ctx.resourceTicket()` and `registerResource`s that exact state before sync or connectivity subscriptions and before async prepare. `getResource`, `clearResource`, and `registerResource` share that slot. Editor liveness keeps the generation and entity checks and also requires `ctx.getResource(kind) === state`, so a same-generation replacement cannot paint, prepare, acknowledge, or durably write as the mounted session. Warm park disposes these five and leaves the PDF runtime. An owner-scoped sync subscription, installed through `ctx.registerCleanup` rather than a registry slot, stays across that disposal and patches the tab Work when the matching editor session is absent, so a parked acknowledgement is on the entity when the panel is rebuilt and the pending overlay for that operation is gone. `prksResumeWarmTabContext` resumes that runtime without a route render or viewer recreate; `refreshFocusedPanel` → `prksRefreshFocusedRightPanel` reconstructs the editor sessions the focused owner needs. Active Work metadata editing still prevents warm parking. Durable writes stay on `saveWorkPersonRole`, Work and Folder tag coalescing, `saveWorkSource`, and `saveWorkMetadataFields`. Notes follow the same registry. The Reminders field (`privateNotesEditor`, Work and Folder) is a non-suspendable right-panel session: `prksBindPrivateNotesField` captures the ticket and registers before it marks or listens to the field, liveness requires `getResource('privateNotesEditor') === editor`, warm park disposes its listeners, debounce, and busy retry, and the focused-panel refresh binds a new one from the draft on `ctx.ui.workPrivateNoteSession` (Folder: the draft map). Research Notes (`workNotes`, the pane-local EasyMDE) is suspendable like the PDF because it lives in the parked pane DOM: `renderWorkDetails` captures the ticket when the paint begins and `initEasyMDE` builds nothing for a stale ticket. Warm park keeps the editor, its buffer, and the `saveNotesTimeout` TabContext timer; cold release destroys it once. Its change handler and status subscription require that exact slot. Notes editing does not prevent warm parking. Flushing stays on the leave path (`prksTabLeave` `flushOwner`): the registry never flushes and adds no second save queue. The notes acknowledgement subscription (`prksBindWorkNotesSync`) is owner-scoped through `ctx.registerCleanup`, like the Work acknowledgement subscription, so it survives warm park. The split-view `ResizeObserver` is one route `registerCleanup`. `ctx.setResource` for the registry kinds is a compatibility bridge that mints a fresh ticket for synchronous harness callers. Production async and session setup must not use it. No production `ctx.resources` entry has a disposer; the map holds ordinary TabContext values (`workNotesCanonical`, `workNotesObserved`, `workNotesCollapseSync`, wiki/Concept/Argument hint lists, `workRouteProjection`). Warm park is an owner state: while it holds, a non-suspendable registration is rejected and a suspendable one attaches already suspended.
- other named values → `ctx.resources` / `ctx.setTimer`
- shell → main/focused context (`prksGetMainTabContext`, `prksGetFocusedTabContext`)

People-library search runtime belongs to rendered `.prks-people-library` root
(`root.__prksPeopleLibraryState`), never a `window` singleton or tab-ID global map.
Rerender only that root. SessionStorage preserves shared query preference; it is not
workspace persistence or route state.

Group-library search and collapse live on the owning TabContext
(`ctx.ui.personGroupIndex`). The Vue index hydrates its refs from that object
and writes them back on search and expand/collapse. The state survives
index → detail → index in the same pane and dies with the tab. Main and
Secondary do not share it. It is not a `window` singleton, sessionStorage, or
a module-level map. `personGroupEditing` and `personGroupMembersEditing` are
mutually exclusive TabContext UI modes: metadata editing owns the right panel;
membership management owns the Members section.

Person profile edit state, including selected Person Groups, belongs to the Person's
TabContext. Never store Person-editor selections or drafts in a window-global
singleton. Right-panel reconstruction must render from the owning TabContext draft.

Person profile and Work metadata drafts are TabContext-owned runtime state. Never
persist them through workspace persistence. Merely focusing another mounted pane is
non-destructive and must not prompt. Any operation that will replace a route, unmount,
park, or destroy a context with a dirty editable draft must preflight through
`prksTabLeave` (`frontend/js/tab-leave.js`, built from `frontend-app/src/lifecycle/tab-leave.ts`).
`prksCanLeaveTabContext` is only the boolean adapter. Rejection is an atomic no-op that
preserves route, tab order, tree topology, focus, draft, and editor DOM. Batch operations
preflight every affected mounted context before mutating any of them. Concurrent attempts
on the same owner are serialized. `prksRenderTabRoute` does not ask again after an
approved preflight. Person, Work, and PDF probes stay with those features.

Stacked mode: one mounted context. Tiled mode: Main + every visible Secondary leaf (up to the visible-pane cap), each with an independent TabContext. Do not store route-scoped state on `window`. The Research Graph
is that owner's `researchGraph` resource (`ctx.getResource('researchGraph')` reads the registry); no module-level singleton fallback.

### Workspace drag and drop

Workspace drag is an alternate input path for existing canonical workflows, never a parallel
layout model. Pragmatic DnD owns sensors/lifecycle/targets/preview/autoscroll/cancel in
`frontend-app/src/workspace-dnd/`. PRKS owns the pure `WorkspaceDropIntent` resolver, edge-band
geometry (28% bands; center = no-drop; no Atlaskit hitbox), and commit through coordinator APIs.
Transient drag state lives only in the adapter session; it is never canonical/persisted and
never written to `localStorage`, `sessionStorage`, or IndexedDB.

`workspace-tree.js` owns recursive tree transformations (including drag-driven pane moves, via
`moveLeafRelativeToTarget`); `workspace-tabs.js` owns global tab ordering (via
`prksWorkspaceReorderTab`). The DnD adapter only computes/previews user intent and invokes
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

Drag cancellation (Escape, native drag end with empty targets, window blur, responsive
transition, external tile/source removal, shell prune) must run through one idempotent cleanup
that removes every overlay/marker, source styling, and the body drag class, and must leave
canonical workspace state completely untouched. `prksWorkspaceCancelActiveDrag` exists
specifically so `workspace-tiling.js` can defensively end an active drag before a real
narrow/wide transition and before pruning any stale tile that could contain the live drag
source — it is always safe to call when idle. The adapter must never itself mutate
responsive/narrow-fallback state.

## Settings

Settings category navigation is presentation state; it must not create another
settings persistence model. The active category lives only in an in-memory module
variable, never `localStorage`, never `/api/settings`, never a URL hash.

Inactive Settings category panels are hidden and inert, never removed/recreated.
Switching categories must not reset a running Backup/Maintenance operation, a
chosen restore file, or any control's in-progress value.

Performance diagnostics loads only on first activation of the Diagnostics
category, not whenever Settings opens. TanStack Query retains that disposable
server snapshot; revisiting the category does not re-fetch. Refresh and Reset
invalidate it. Reset is an online `useMutation` on the application QueryClient
(retries disabled) so a failed POST still reports PRKS reachability. Do not
move durable semantic or offline mutations into TanStack Query. The
offline-cache status in the same category is a separate read and is not query
state. The query cache is not canonical library data and is not an offline
queue.

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

`#/graph` chrome is painted by `frontend-app/src/features/research-graph/`. Vue does not fetch the projection and does not construct Cytoscape. The coordinator loads `prksOfflineResearchGraphFetch` and mounts the instance with `renderResearchGraph` into that shell. The instance is the `researchGraph` registration on that pane's owner-resource registry. It is not warm-suspendable. Registering another instance for the same owner disposes the previous one once. Destroy releases the Cytoscape instance, its listeners, the canvas `ResizeObserver`, and any pending resize frame. Warm park is an owner state. It releases the graph, and a later non-suspendable registration while the owner stays parked is rejected and not attached. The ticket stays current. Resume does not create another graph. A suspendable registration during that park attaches already suspended and runs its suspend hook. Cold park releases it and invalidates tickets captured before that release. Remounting the same owner does not make those tickets usable. A mounted `beginRoute` still accepts a ticket for the new route generation. Owner destruction releases it. A Main/Secondary role swap does not. Main and Secondary each keep their own instance; destroying one does not drop the other. A stale completion after the owner or its generation is replaced does not register or mount into the replacement. Selection stays on that pane's runtime. Only the runtime whose context is `prksGetFocusedTabContext()` may write `#prks-graph-inspector` or refresh focused-panel visibility; focusing a pane repaints through `renderGraphInspector`. When `renderResearchGraph` is missing, the graph body shows `Graph UI unavailable.` and does not alert. The Vue route host participates in the pane flex chain, and `[data-prks-role="graph-body"]` passes that height to the stage. After Vue writes the chrome, `prksRefreshIcons` runs on the painted graph element so the header and legend placeholders become icons. The non-Vue `shellHtml` mount path is removed. The Vue shell is the only chrome.

Graph node IDs must be namespaced by entity type; raw PRKS IDs are not globally
unique across record types.

Work→Concept and Work→Argument graph edges represent explicit research-note
semantic references only. Never infer graph relations from plain prose, PDF
text, tags or search similarity.

Argument source edges and note-mention edges have different semantics and must
remain distinguishable.

Do not add canonical graph persistence or a main-DB schema migration merely to
render the Research Graph. Disposable server projection snapshots may be cached
by the offline runtime; graph UI/layout state is never persisted.

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

Offline/local-first work has a large, load-bearing domain contract that is
intentionally scoped out of this global file. **Before changing offline,
local-first, synchronization, service-worker, conflict/revision, or client-cache
behavior — or tests that encode those contracts — read
`docs/agent-rules/offline-pwa.md` as the router, then load
`offline-foundations.md` plus only the relevant leaf files selected by
`docs/agent-context/sync-map.md`.**

Global rules still apply, especially:

- disposable read cache state belongs in `offline-store.js`; durable
  unsynchronized user intent belongs in `local-store.js`;
- never make disposable cache state authoritative for unsynchronized user work;
- online and offline mutation paths must converge on the same domain semantics;
- preserve revision/conflict and acknowledgement contracts rather than adding a
  second ad-hoc sync path;
- update the detailed domain contract and its regression tests when intentionally
  changing an offline/local-first invariant;
- current rollout status remains in `docs/local-first-rollout-status.md`.

The detailed operation families, projection rules, dependency ordering,
conflict semantics, service-worker behavior, test contracts, and historical
load-bearing constraints are maintained in the bounded leaf files routed by
`docs/agent-rules/offline-pwa.md` and `docs/agent-context/sync-map.md`.

## Interaction feedback

Do not replace the synchronous pending-annotation-sync navigation guard
(the `pdf-sync` probe in `frontend/js/pdf-work-runtime.js`, registered on
`prksTabLeave`) with `prksConfirmDialog`/`prksConfirmDestructive`
without redesigning the navigation contract. It must stay a native
`window.confirm`; this synchronous PDF safety decision must complete before any
async editable-draft confirmation begins. `prksRenderTabRoute` does not repeat it.
This is the sole native-confirm exception.
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
