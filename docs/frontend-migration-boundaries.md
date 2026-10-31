# Frontend migration boundaries (B5 closeout)

Final bridge inventory for #303 / #230. This records every crossing that remains
between the classic runtime (`frontend/js/`) and the Vue bundle
(`frontend/vue/prks-vue.js`, built from `frontend-app/`), who owns each side,
and why it is not migration debt. It also lists what the audit retired.

The finish line is coherent ownership, not "everything is Vue". A boundary
stays when its owner is a current product/runtime owner. A bridge goes when
its last consumer has crossed.

## Status

- B1 to B4 are complete.
- B5 implementation is complete: shared Work card (#451), Work-detail cutover
  (#452), dead-painter purge and bridge inventory (#453), browser/resource
  baselines (#455), long-lived listener growth (#459, resolved by #469 at
  `ae5a7b5`; `docs/b5-browser-baselines.md` shows a zero
  `longLivedListenerLive` delta), and the production-tree bridge audit (#470
  at `24956a4`).
- Final verification after #470 found one more dead bridge (the PDF adapter
  `window` registration) and gaps in this inventory. The follow-up PR retires
  that bridge and completes the tables below, using the alias-aware audit
  described in "How the inventory is checked".
- There is no further route or component migration wave. Controller
  verification and closure of #303 / #230 follow that PR.

## Why the window boundary exists

Classic scripts load first, in `index.html` order. The Vue bundle is an ES
module that loads after `app.js`. Code in one world reaches the other only
through `window`. Each name below is one deliberate crossing with one owner on
each side. Vue reads classic services through typed declarations in
`frontend-app/env.d.ts`; a declaration with no reader is stale and is removed.

Classic files end with an explicit `window.X = X` export list. For top-level
classic functions that list documents the file's public surface; it is not a
bridge by itself.

## How the inventory is checked

A crossing is any name one world publishes and the other reads. The audit
covers every publication mechanism, not only `window.X =`:

- Vue `register*Bridge(target)` functions called from `src/main.ts`, and any
  assignment through an alias of `window`, `globalThis`, or `root`
  (`target.prksX =`, `bridge.prksX =`, `root.prksX =`), plus
  `Object.assign` / `defineProperty` on those objects;
- generated classic modules (`scripts/build-*.mjs` IIFE names and their
  entries);
- Vue reads through `window.X`, typed aliases (including multi-line
  `(window as … & { prksX?: … }).prksX` casts), and string lookups such as
  `classic('prksX')`;
- properties one world sets on a shared host, TabContext, or error object and
  the other reads (see "Host and object markers");
- every `env.d.ts` global declaration against its readers. A declaration kept
  only so a Vitest can assert Vue does *not* call a classic global
  (`prksHideWorkThumbPreview`, `prksDeleteArgumentDurably`,
  `__prksProcessingPeople`) is a test contract, not a crossing.

A Vue publication with no classic reader is dead and is removed. The
classic-owned table names families with representative globals: every name
Vue reads from classic code belongs to a row whose classic owner file is
listed and whose reason covers it. A new crossing gets a row here with its
owner on each side, or it is migration debt.

## Classic to Vue (Vue registers, classic calls)

| Global | Registered by | Called by | Why it stays |
| --- | --- | --- | --- |
| `prksVuePresentRoute`, `prksVueDismissRoute` | `src/route-surface/lifecycle.ts` | `app.js` (`prksDeliverVueRoute`, route leave) | The coordinator in `app.js` owns route identity, TabContext, the pane host, and effective/offline reads. Vue owns presentation. One present and one dismiss entry serve every route feature. Early host-local requests cover a route that paints before the module registers. |
| `prksMountWorkDetail` | `src/features/work/session.ts` | `app.js` `case 'work'` | The coordinator publishes the Work route projection; `detail-lifecycle.ts` owns the async mount order (notes ticket, viewer modules, shell, PDF, Research Notes, panel). |
| `prksVuePresentWorkPanelRead`, `prksVueRefreshWorkPanelRead`, `prksVueDismissWorkPanelRead` | `src/features/work/panel-session.ts` | `ui.js`, `work-metadata-editor.js` | `ui.js` owns the right-panel tab stack and focus. Vue owns the Work view-mode panel. |
| `prksVuePresentWorkMetadataEditor`, `prksVueDismissWorkMetadataEditor`, `prksVueCaptureWorkMetaDraft`, `prksVueWorkMetadataEditorOwns`, `prksVueSetWorkMetadataFieldError`, `prksVueAcceptWorkMetadataField`, `prksVueApplyWorkMetadataChrome`, `prksVueWorkMetaDraftIsDirty`, `prksVueWorkMetaSessionStill` | `src/features/work/metadata-session.ts` | `ui.js`, `work-metadata-editor.js`, `work-source-editor.js` | Durable metadata and source writes, acknowledgements, and conflicts stay with the classic durable-operation owners. Vue owns the form, draft, and field errors. |
| `prksVuePresentWorkPrivateNotes`, `prksVueDismissWorkPrivateNotes` | `src/features/work/private-note-session.ts` | `ui.js` | Private-note durability stays in `ui.js` (`privateNotesEditor` session). Vue owns the Work field. |
| `prksVueDismissWorkResearchNotes` | `src/features/work/research-note-session.ts` | `works.js` | EasyMDE and the durable note API stay in `works.js` (Research Notes product owner). Its teardown drops the Vue pane. |
| `prksModalHost` | `src/modal/ModalHost.vue` | `ui.js` (`prksConfirmDialog`, `prksPromptTextDialog`, `prksOpenModalUnsavedConfirm`, `requestModalClose`) | One dialog presenter. Vue paints confirm, alert, prompt, and unsaved dismissal, and owns Escape, focus containment, and focus return. Classic functions stay the call sites. Dirty baselines, feature-modal shells, and leave decisions stay in `ui.js` and `tab-leave.ts`. A dirty close before the host is installed leaves the modal open; it does not discard, and the next close request confirms once the host exists. |
| `prksVueUpdateResearchNotesRecovery`, `prksVueUpdateWorkPrivateNotesRecovery`, `prksVueCloseRecoveryReview` | `src/features/work/research-notes-recovery.ts`, `src/features/work/private-notes-recovery.ts`, `src/features/editor-recovery/recovery-presenter.ts` | `works.js`, `ui.js`, `work-note-recovery.js`, Vue modal host | Recovery planning, adoption, and discard stay in the shared classic adapter `work-note-recovery.js` over `prksEditorRecovery` (#466, #474); `works.js` (Research Notes) and `ui.js` (Reminders) supply their sessions. Vue owns the recovery notices and the one Review dialog; the Vue modal host routes Escape to that close. |
| `prksVueSyncWorkPdfAnnotationPopup`, `prksVueDismissWorkPdfAnnotationPopup`, `prksVueSyncWorkPdfAnnotationDrawer`, `prksVueDismissWorkPdfAnnotationDrawer` | `src/features/work/pdf-annotation-*.ts` | `works-pdf.js` | The PDF viewer runtime and annotations own state and lifetime. Vue renders the popup and drawer. |
| `prksVueCloseTagsAliasModal`, `prksVueCloseTagsMergeModal`, `prksVueClosePublishersAliasModal` | `src/features/tags/session.ts`, `src/features/publishers/session.ts` | Vue modal host, `tags.js` | The Vue modal host routes Escape to these closes. Vue owns those modal bodies. |
| `prksVueReportTagsRefreshFailure` | `src/features/tags/session.ts` | `app.js` | The coordinator loads the tag vocabulary; Vue shows the failure. |
| `prksVueActivatePerformanceDiagnostics`, `__prksPerformanceDiagnosticsRequested` | `src/features/performance-diagnostics/activation.ts` | `app.js` settings | Settings navigation is classic. The flag covers activation before the module loads. |
| `prksSavedViewRecords` | `src/features/saved-views/session.ts` (records service in `records.ts`) | `app.js` Saved View detail case, `saved-views.js` shared modal, `command-palette.js` | Saved View reads and writes are owned by the typed client and TanStack Query in Vue. The classic route coordinator, the shared modal, and the palette use that one service rather than a second client. |
| `prksProcessingRecords` | `src/features/processing/session.ts` (records service in `records.ts`) | `app.js` Processing case | Processing inbox reads and writes are owned by the typed client in Vue, which also owns file save and import. The coordinator loads the inbox through this service rather than a second client. The classic quick-create helpers in `processing-files.js` call their own durable APIs and do not use it. |
| `prksWorkMetadataGroupFields`, `prksWorkPanelReadOwns` | `src/features/work/metadata-session.ts`, `src/features/work/panel-session.ts` | `work-metadata-editor.js` | The metadata field grouping and the panel-read ownership check are Vue rules. The classic durable metadata editor asks Vue instead of keeping a copy. |
| `prksWorkspaceInitDrag`, `prksWorkspaceCancelActiveDrag` | `src/workspace-shell/WorkspaceShell.vue`, `src/workspace-dnd/adapter.ts` | `workspace-tabs.js` (init), `workspace-tiling.js` (cancel) | `workspace-tabs.js` is the live workspace effect coordinator. The Vue shell owns presentation and Pragmatic DnD sensors; the coordinator starts and cancels drags through these hooks. `workspace-drag.js` only publishes idle no-op defaults that the Vue shell replaces, so pre-Vue callers stay safe. `__prksWorkspaceShellOwned` is set by `workspace-tabs.js` before bootstrap and again by the Vue shell on mount; only `workspace-tabs.js` reads it, to take the presentation path instead of the legacy tile reconciler. |
| `prksVueApplyShellChrome` | `src/app-shell/AppChrome.vue` | `navigation.js` `prksNoteShellChrome` | Vue owns sidebar, top ribbon, and create-menu presentation (#1038). Classic publishes the Main route's active href, disclosure revision, drawer flags, connectivity, processing count, and sync-queue cue. `__prksShellChrome` is the early snapshot for a publish that lands before the module registers. |

## Vue to classic runtime services (classic owns, Vue calls)

These are current product or runtime owners. None is a renderer kept alive
for a migrated surface.

| Family | Classic owner | Representative globals | Why it stays |
| --- | --- | --- | --- |
| Route coordinator | `app.js`, `tab-context.js`, `navigation.js` | `prksPresentVueRoute`, `prksTabContextOwnsEntityRoute`, `prksNavigate` | Owns the host and request envelope for every route feature, and the TabContext generation checks. |
| App chrome semantics | `navigation.js`, `ui.js`, `command-palette.js`, `app.js` | `prksSyncSidebarActive`, `prksNavDisclosureExpanded`, `prksWriteNavExpandedPref`, `prksToggleSidebarDrawer`, `prksToggleRightPanelOverlay`, `prksOpenCommandPalette`, `openModal`, `prksRenderConnectivityIndicator` | Active href uses canonical origins. Disclosure keys stay `prks.nav.*Expanded`. Drawer body classes, the palette body, connectivity, and the sync-queue count stay classic. Vue calls these; it does not copy them. |
| People, Person Group, Playlist route families | `people.js`, `people-groups.js`, `playlists.js` | `renderPeopleList`, `renderPersonDetails`, `renderPlaylistDetail`, `prksRefreshPersonGroupMain`, `prksReloadPlaylistDetail` | They resolve the effective/offline/durable record and hand it to `prksPresentVueRoute`, then bind offline state. They paint no HTML. |
| Durable and offline operations | `api.js`, `local-store.js`, `offline-runtime.js`, `sync-runtime.js`, `tags.js`, feature `*-state.js`, `work-metadata-editor.js`, `work-source-editor.js` | `savePersonProfileDraft`, `updatePlaylist`, `prksTagsMerge`, `prksTagsDelete`, `prksSaveWorkMetadataFields`, `prksSaveWorkSource`, `prksResolveWorkMetadataFieldConflict`, `prksOfflineRuntimeState`, `prksOfflineGuardMutation`, `prksSync` | One durable queue and one effective-state model. Vue never mirrors them. |
| Modals and dialogs | `ui.js` feature shells, `saved-views.js`, `concepts.js`, feature modal openers; presentation in `src/modal/ModalHost.vue` | `openModal`, `closeModals`, `requestModalClose`, `prksConfirmDestructive`, `prksPromptTextDialog`, `prksAlertDialog`, `prksOpenNewPersonModalFromPeoplePage`, `prksOpenSavedViewModal`, `prksCreateConceptFlow` | Feature-modal shells and their dirty baselines stay classic until each form moves. Confirm, alert, prompt, unsaved dismissal, Escape, and focus return are the Vue host, reached through `window.prksModalHost`. The Saved View modal saves through `prksSavedViewRecords`. |
| Folder hierarchy refresh and library glance data | `folders.js`, `folder-hierarchy-nav.js` | `prksFillFolderDetailTree`, `prksCommitFolderDetailSurface`, `prksScheduleFolderLibraryGlance`, `prksFolderLibraryCatalogGlanceParts`, `prksPublishFolderDashboardState`, `__prksFolderDashboardState`, `__prksFolderLibraryBrandHomeReset`, `prksFolderNavTriggerHtml`, `prksMountFolderHierarchyNav` | Vue renders Folder Library and Folder detail tree rows, the subfolder list, and glance/summary text from typed state. Expand/collapse and the library filter live on the owning TabContext (`ctx.ui.folderTree`). Session storage restores only the first Folder Library mount in the page; a later pane starts unfiltered. The refresh claim, offline bind, layout observer, catalogue load, and glance extras stay here and publish a snapshot or part list. The hierarchy switcher (`prksFolderNavTriggerHtml`) still owns crumb and nearby markup because its popover paints that DOM. |
| Folder detail effective works | `folders.js` | `prksEffectiveFolderDetailWorks` | Embedded Work summaries. Vue does not read the durable queue. |
| Work-card infrastructure | `work-cards.js` | `prksInitLazyWorkThumbs`, `prksRegisterWorkThumbUrl`, `prksShowWorkThumbPreview`, `prksWorkBrowseModeToggleHtml` | Thumbnail lifetime, preview, and browse mode stay classic. `PrksWorkCard` owns card markup and the credit text. |
| PDF, Research Notes, private notes | `works-pdf.js`, `pdf-work-runtime.js`, `works.js`, `ui.js` | `initPdfViewerForWork`, `prksLayoutAnnotationDrawer`, `prksHasPendingWorkAnnotationSync`, `initEasyMDE`, `prksResearchNotesTextForWork`, `prksRestoreResearchNotesRecovery`, `prksResearchNotesRecoveryView`, `prksResearchNotesRecoveryDetails`, `prksResearchNotesRecoveryRestore`, `prksResearchNotesRecoveryReplace`, `prksResearchNotesRecoveryDiscard`, `prksRefreshResearchNotesRecovery`, `prksRestoreWorkPrivateNoteRecovery`, `prksWorkPrivateNotesRecoveryView`, `prksWorkPrivateNotesRecoveryDetails`, `prksWorkPrivateNotesRecoveryRestore`, `prksWorkPrivateNotesRecoveryReplace`, `prksWorkPrivateNotesRecoveryDiscard`, `prksRefreshWorkPrivateNotesRecovery`, `prksPrivateNotesTextForEntity`, `prksBindPrivateNotesField` | Runtime owners kept out of migration scope. The Work video player is the Vue pane `WorkVideoPane`. |
<<<<<<< HEAD
| Research Graph, Processing, command palette, workspace tabs | `research-graph.js`, `processing-files.js`, `command-palette.js`, `workspace-*.js` | `prksReleaseResearchGraph`, `prksProcessingAttachResources`, `prksOpenCommandPalette`, `prksWorkspaceSubscribe`, `prksWorkspaceOnShellCommit`, `prksWorkspacePlaceContentHost`, `prksWorkspaceWatchCanvas`, `prksWorkspaceOpenTabMenu` | Runtime owners kept out of migration scope. `workspace-tabs.js` is the live effect coordinator behind the Vue shell's detached projection. |
| Classic summary primitives | `overview-primitives.js` | `prksPageSummaryHtml`, `prksStateSummaryHtml`, `prksNavAttentionBadgeHtml` | Remaining callers are classic: workspace overview, Details state strip, nav badges. Folder Library glance and Folder detail summary render the same classes from Vue. |
=======
| Research Graph, Processing, workspace tabs | `research-graph.js`, `processing-files.js`, `workspace-*.js` | `prksReleaseResearchGraph`, `prksProcessingAttachResources`, `prksWorkspaceSubscribe`, `prksWorkspaceOnShellCommit`, `prksWorkspacePlaceContentHost`, `prksWorkspaceWatchCanvas`, `prksWorkspaceOpenTabMenu` | Runtime owners kept out of migration scope. `workspace-tabs.js` is the live effect coordinator behind the Vue shell's detached projection. |
| Command palette | `command-palette.js` catalog and dispatch; dialog in `src/palette/CommandPalette.vue` | `prksOpenCommandPalette`, `prksCloseCommandPalette`, `prksCommandPaletteView`, `prksCommandPaletteSubscribe`, `prksCommandPaletteSetQuery`, `prksCommandPaletteHandleKey`, `prksCommandPaletteActivateFromPointer` | One catalog, search, freshness, and allowlisted dispatch. Vue paints the dialog, input, rows, status, and hints, and sends those intents back. The ribbon launch control is bound once in `command-palette.js`. |
| Classic summary primitives | `overview-primitives.js` | `prksPageSummaryHtml`, `prksStateSummaryHtml`, `prksNavAttentionBadgeHtml` | Remaining callers are classic: folder glance, workspace overview, Details state strip, nav badges. |
>>>>>>> 994c648a (feat(ui): paint the command palette dialog in Vue)
| Generated typed modules | built from `frontend-app/` by `npm run build` | `route-model.js`, `search-query-codec.js`, `workspace-model.js`, `work-route-projection.js`, `tab-leave.js`, `owner-resource.js`, `editor-recovery.js` | Canonical typed sources compiled for classic callers. Never hand-edited. Vue's Settings → Diagnostics recovery section (`features/editor-recovery/EditorRecoveryDiagnostics.vue`) reads `prksEditorRecovery.diagnostics()`, because the page has one recovery runtime and a second copy in the Vue bundle would be a second owner. |
| Icons | `icons.js` | `prksIcon`, `prksRefreshIcons`, `prksPageHeaderIconHtml`, `prksTagSearchIconHtml`, `prksTagPlusIconHtml`, `prksProgressStatusIconHtml` | One icon registry for both worlds (classic modals, palette, folder tree, panels, and Vue routes, `PrksWorkCard`, the workspace drag adapter). A second Vue registry would duplicate it. `prksPageHeaderIconHtml` is read only by Vue route headers today; it stays with the registry it belongs to. |
| Document types | `doc-types.js` | `prksDocTypeBadgeHtml`, `prksDocTypeMenuShellHtml` | The document-type vocabulary and its badge/menu markup have one owner, shared by classic Details and new-file flows and by Vue `PrksWorkCard`, Types, Work detail, and the Processing card. |
| Help and markup | `ui.js`, `api.js`, `concepts.js` | `prksHintBtnHtml`, `prksEscapeHtml`, `prksResearchMarkdownHtml` | The hint-button markup, HTML escaping, and the research Markdown renderer (wiki links, sanitizing) are single rules used by classic panels and by Vue (`research-index/markdown.ts`, Person Group detail). |
| Dates | `date-format.js` | `prksIsoToDdMmYyyy`, `prksParsePublishedDateInput` | One parser and formatter for published dates, shared by classic metadata editing and the Vue Processing card and records. |
| Tag matching and inline comboboxes | `ui.js`, `combobox-keys.js` | `prksTagMatchesQuery`, `prksTagExactMatch`, `prksTagComboboxLabel`, `prksTagVocabularyMessage`, `prksShowInlineComboboxResults`, `prksHideInlineComboboxResults`, `prksBindComboboxKeys`, `prksComboboxRenderEpoch`, `prksComboboxRendered` | Tag alias matching, inline open/close, and the combobox keyboard model (arrows, Enter, Escape, ARIA) are one behavior for New File, Work Tags, Folder Tags, and the Vue Processing card. The card reports a finished result render, including one that reuses the same rows, so Enter is not left stale. |
| Shared control helpers | `ui.js` | `prksSetButtonBusy`, `prksFlashButtonLabel`, `prksCopyTextToClipboard`, `prksSegmentedControlHtml`, `prksBindSegmentedHidden`, `prksBindAutosizeTextareas`, `prksIsSmallScreen` | Busy/flash button feedback, segmented controls, textarea autosize, and the small-screen breakpoint follow `DESIGN.md` once for both worlds. `prksFlashButtonLabel` is read only by the Vue annotation drawer today. `prksCopyTextToClipboard` is the one clipboard write (Clipboard API, then the textarea fallback) for the classic PDF annotation link and the Vue recovery Review and Settings → Diagnostics copy actions (#532). |
| Separators and resize | `workspace-split.js` | `prksBindDrawerWidthSeparator`, `prksWorkspaceSyncSplitSeparator`, `prksWorkspaceSyncNestedSeparator`, `prksWorkspaceReleaseRootSeparator`, `prksWorkspaceReleaseNestedSeparator` | One separator implementation owns pointer drag, keyboard resize, and separator ARIA for the Main/Secondary divider, nested Secondary splits, and the PDF annotation drawer width handle (its width mode). Vue renders the separator elements: the workspace canvas and tree, and the PDF annotation drawer, which forwards the resize ticket captured at gesture start and calls the binding's `refresh` when the painted width or min/max changes. A second implementation in Vue would split drag and ARIA behaviour. |
| Request and error reporting | `request-coordinator.js`, `api.js` | `prksRequest`, `prksIsAbortError`, `prksConsumeApiError`, `prksReportClientError`, `prksRequestCoordinatorSnapshot`, `prksResetRequestCoordinatorDiagnostics`, `prksLibraryEpochHeaders`, `prksNoteLibraryEpoch` | One request coordinator (dedupe, abort, diagnostics) and one client-error reporter. Vue Work detail, Playlist intents, the Query cache, and performance diagnostics use them. The coordinator also owns the page's Library epoch (#629): Vue `http.ts` asks it for the `PRKS-Library-Epoch` header on every mutation and hands it every response to note; Vue keeps no epoch state of its own. |

## Host and object markers

Some crossings are properties on a shared object rather than `window` globals.
Each has one writer and one reader.

| Marker | Written by | Read by | Purpose |
| --- | --- | --- | --- |
| `host.__prksVueRouteRequest` | `app.js` (`prksDeliverVueRoute`) | `src/route-surface/lifecycle.ts` | Early host-local route request for a route that paints before the Vue module registers. |
| `__prksShellChrome` | `navigation.js` `prksNoteShellChrome` (also `app.js` connectivity, processing, and sync-queue counts, and `ui.js` drawer flags) | `src/app-shell/AppChrome.vue` | Early shell snapshot so the first Vue paint matches the Main route already published. |
| `panel.__prksWorkPanelReadRequest` | `ui.js` | `src/features/work/panel-session.ts` | Same early-request handoff for the Work view-mode panel. |
| `panel.dataset.prksOwnerTabId`, `panel.dataset.prksOwnerGeneration` | `ui.js` | Work panel, metadata, and private-note sessions | TabContext ownership of the right panel; Vue drops stale presents. |
| `ctx.__prksRetain*Surface` (People, Person Groups, Playlists, Arguments, Positions, Concepts, Folder Library, Folder detail) | `app.js` | the matching `src/features/*/session.ts` cleanup | Same-route refresh in one TabContext keeps the Vue host instead of disposing it. |
| `contentRoot.__prksFolderOfflineDispose` | `folders.js` | `src/features/folder-library/intents.ts` | Folder offline-state binding teardown. |
| `host.__prksFolderDetailTreeSnapshot`, `host.__prksApplyFolderDetailTree` | `folders.js` (`prksFillFolderDetailTree`) | `src/features/folder-detail/FolderDetailRoute.vue` | Typed hierarchy snapshot for one Folder detail tree host. Vue renders it. A stale claim does not publish. |
| `textarea.dataset.prksNotesBound` | `ui.js` | `src/features/work/detail-lifecycle.ts` | Marks the private-notes field as bound so mount does not bind twice. |
| `err.prksOfflineRefused` | `api.js` offline guard, and the Saved View records error in `src/features/saved-views/records.ts` | `prksOfflineWasGuardRefusal` in `api.js` | One offline-refusal flag, so classic callers skip a duplicate alert for a refusal from either client. |

## Shell chrome (#1038)

`#sidebar` and `#prks-top-ribbon` in `index.html` are permanent hosts. They stay empty until `AppChrome` mounts. That empty host is the load gap before the module, not a second renderer and not a no-JS product surface. `ribbon-create.js`, `prksInitNavDisclosures`, and `prksSetDisclosureVisual` are retired: the last click and DOM writers moved into `src/app-shell/`.

## Retired by the final audit

- `renderVideoViewerPane` and `frontend/js/components/works-video.js` (#1040).
  The Work video player is `WorkVideoPane`, which resolves the effective
  source through `resolveWorkVideoEmbed`. The shell receives those source
  fields and does not receive classic video HTML.

- The PDF adapter `window` bridge (`registerWorkPdfAdapterBridge`): 23
  globals (`prksReadWorkPdf*`, `prksIntent*WorkPdf*`,
  `prksWorkPdfLeaveNeedsConfirm`) that no production code read. Vue imports
  `src/features/work/pdf-adapter.ts` directly; classic PDF code reaches Vue
  only through the `prksVue*WorkPdfAnnotation*` entries above.
- The `env.d.ts` declaration of `prksSearchQueryCodec`: Vue imports
  `src/features/search/codec.ts`; only classic callers read the generated
  global.
- `prksRelSummaryHtml`: the Work header relationship strip is Vue
  `PrksRelSummary`, which now links same-app routes.
- `prksScopeLineHtml`, `prksPaintScopeHost`: Vue `PrksScopeLine` and
  `scopeLineParts` own collection scope.
- `prksWorkCardCreditText`, `prksWorkCardCreditLine`: `workCardCreditText` in
  `src/components/work-card.ts` is the one credit rule.
- `prksRecentlyAddedDateLabel`, `prksRecentlyAddedWorkMatchesQuery`: owned by
  `src/features/folder-library/recently-added.ts`.
- The `prksVuePresentWorkResearchNotes` window round trip: Work detail imports
  the presenter directly.
- Unreachable classic fallback painters behind `typeof prksPresentVueRoute`
  in the People, Person Group, Person, and Playlist presenters.
- Classic renderers and helpers with no caller: Person external links, image
  source, and role blocks; Playlist edit sidebar; Work role credit picker and
  role linker; folder dropdown; tag combobox initializer; role-link failure
  notifier; Person Group draft saver; folder tree host lookup.
- Unreferenced runtime helpers and exports found during the audit:
  annotation drawer pin/width setters, the old PDF viewer destroy helper and
  scroll-point helper, workspace-overview close/toggle/refresh exports,
  `prksAssignRouteEntity`, `prksFocusedRouteIsCurrent`,
  `prksOfflineIsMutationBlocked`, the generic `prksOfflineMarkDomainChanged`
  (domains use their named `prksOfflineMark*Changed` wrappers), and private
  graph, tab-menu, local-store, and doc-type helpers.
- Unused classic copies of Vue-owned constants: the Work status labels and
  the Folder Library filter key.
- Folder tree and folder-detail row HTML (`prksFolderLibraryTreeInnerHtml`,
  `prksFolderDetailSummaryHtml`, `prksFolderDetailNavHtml`,
  `prksFolderDetailSubfoldersHtml`, `prksFolderDetailTreeHtml`,
  `prksPaintFolderLibraryGlance`, and the global
  `__prksFolderTreeCollapsed` toggles). Vue renders those rows. The hierarchy
  refresh claim and the switcher popover stay classic.
- Selectors with no remaining markup: `.person-profile__role-*` and
  `.prks-workspace-menu__sep`.
- Stale `env.d.ts` declarations for globals Vue no longer reads.
