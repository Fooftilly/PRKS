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
- Vue reads through `window.X`, typed aliases, and string lookups such as
  `classic('prksX')`;
- every `env.d.ts` global declaration against its readers. A declaration kept
  only so a Vitest can assert Vue does *not* call a classic global
  (`prksHideWorkThumbPreview`, `prksDeleteArgumentDurably`,
  `__prksProcessingPeople`) is a test contract, not a crossing.

A Vue publication with no classic reader is dead and is removed. A new
crossing gets a row here with its owner on each side, or it is migration debt.

## Classic to Vue (Vue registers, classic calls)

| Global | Registered by | Called by | Why it stays |
| --- | --- | --- | --- |
| `prksVuePresentRoute`, `prksVueDismissRoute` | `src/route-surface/lifecycle.ts` | `app.js` (`prksDeliverVueRoute`, route leave) | The coordinator in `app.js` owns route identity, TabContext, the pane host, and effective/offline reads. Vue owns presentation. One present and one dismiss entry serve every route feature. Early host-local requests cover a route that paints before the module registers. |
| `prksMountWorkDetail` | `src/features/work/session.ts` | `app.js` `case 'work'` | The coordinator publishes the Work route projection; `detail-lifecycle.ts` owns the async mount order (notes ticket, viewer modules, shell, PDF, Research Notes, panel). |
| `prksVuePresentWorkPanelRead`, `prksVueRefreshWorkPanelRead`, `prksVueDismissWorkPanelRead` | `src/features/work/panel-session.ts` | `ui.js`, `work-metadata-editor.js` | `ui.js` owns the right-panel tab stack and focus. Vue owns the Work view-mode panel. |
| `prksVuePresentWorkMetadataEditor`, `prksVueDismissWorkMetadataEditor`, `prksVueCaptureWorkMetaDraft`, `prksVueWorkMetadataEditorOwns`, `prksVueSetWorkMetadataFieldError`, `prksVueAcceptWorkMetadataField`, `prksVueApplyWorkMetadataChrome`, `prksVueWorkMetaDraftIsDirty`, `prksVueWorkMetaSessionStill` | `src/features/work/metadata-session.ts` | `ui.js`, `work-metadata-editor.js`, `work-source-editor.js` | Durable metadata and source writes, acknowledgements, and conflicts stay with the classic durable-operation owners. Vue owns the form, draft, and field errors. |
| `prksVuePresentWorkPrivateNotes`, `prksVueDismissWorkPrivateNotes` | `src/features/work/private-note-session.ts` | `ui.js` | Private-note durability stays in `ui.js` (`privateNotesEditor` session). Vue owns the Work field. |
| `prksVueDismissWorkResearchNotes` | `src/features/work/research-note-session.ts` | `works.js` | EasyMDE and the durable note API stay in `works.js` (Research Notes product owner). Its teardown drops the Vue pane. |
| `prksVueSyncWorkPdfAnnotationPopup`, `prksVueDismissWorkPdfAnnotationPopup`, `prksVueSyncWorkPdfAnnotationDrawer`, `prksVueDismissWorkPdfAnnotationDrawer` | `src/features/work/pdf-annotation-*.ts` | `works-pdf.js` | The PDF viewer runtime and annotations own state and lifetime. Vue renders the popup and drawer. |
| `prksVueCloseTagsAliasModal`, `prksVueCloseTagsMergeModal`, `prksVueClosePublishersAliasModal` | `src/features/tags/session.ts`, `src/features/publishers/session.ts` | `ui.js` modal lifecycle, `tags.js` | The shared modal system (Escape, overlay, focus restore) is classic. Vue owns those modal bodies. |
| `prksVueReportTagsRefreshFailure` | `src/features/tags/session.ts` | `app.js` | The coordinator loads the tag vocabulary; Vue shows the failure. |
| `prksVueActivatePerformanceDiagnostics`, `__prksPerformanceDiagnosticsRequested` | `src/features/performance-diagnostics/activation.ts` | `app.js` settings | Settings navigation is classic. The flag covers activation before the module loads. |
| `prksSavedViewRecords` | `src/features/saved-views/session.ts` (records service in `records.ts`) | `app.js` Saved View detail case, `saved-views.js` shared modal, `command-palette.js` | Saved View reads and writes are owned by the typed client and TanStack Query in Vue. The classic route coordinator, the shared modal, and the palette use that one service rather than a second client. |
| `prksProcessingRecords` | `src/features/processing/session.ts` (records service in `records.ts`) | `app.js` Processing case, `processing-files.js` | Processing inbox reads and writes are owned by the typed client in Vue. The coordinator and the classic import/quick-create helpers call the same service. |
| `prksWorkMetadataGroupFields`, `prksWorkPanelReadOwns` | `src/features/work/metadata-session.ts`, `src/features/work/panel-session.ts` | `work-metadata-editor.js` | The metadata field grouping and the panel-read ownership check are Vue rules. The classic durable metadata editor asks Vue instead of keeping a copy. |
| `prksWorkspaceInitDrag`, `prksWorkspaceCancelActiveDrag`, `__prksWorkspaceShellOwned` | `src/workspace-shell/WorkspaceShell.vue`, `src/workspace-dnd/adapter.ts` | `workspace-tabs.js`, `workspace-drag.js`, `workspace-tiling.js` | `workspace-tabs.js` is the live workspace effect coordinator. The Vue shell owns presentation and Pragmatic DnD sensors; the coordinator starts and cancels drags through these hooks and checks that the shell has mounted. |

## Vue to classic runtime services (classic owns, Vue calls)

These are current product or runtime owners. None is a renderer kept alive
for a migrated surface.

| Family | Classic owner | Representative globals | Why it stays |
| --- | --- | --- | --- |
| Route coordinator | `app.js`, `tab-context.js`, `navigation.js` | `prksPresentVueRoute`, `prksTabContextOwnsEntityRoute`, `prksNavigate` | Owns the host and request envelope for every route feature, and the TabContext generation checks. |
| People, Person Group, Playlist route families | `people.js`, `people-groups.js`, `playlists.js` | `renderPeopleList`, `renderPersonDetails`, `renderPlaylistDetail`, `prksRefreshPersonGroupMain`, `prksReloadPlaylistDetail` | They resolve the effective/offline/durable record and hand it to `prksPresentVueRoute`, then bind offline state. They paint no HTML. |
| Durable and offline operations | `api.js`, `local-store.js`, `offline-runtime.js`, feature `*-state.js` | `savePersonProfileDraft`, `updatePlaylist`, `prksTagsMerge`, `prksSaveWorkMetadataFields`, `prksOfflineRuntimeState` | One durable queue and one effective-state model. Vue never mirrors them. |
| Modals and dialogs | `ui.js`, `saved-views.js`, feature modal openers | `prksConfirmDestructive`, `prksPromptTextDialog`, `prksAlertDialog`, `prksOpenNewPersonModalFromPeoplePage`, `prksOpenSavedViewModal` | The shared modal system (Escape, overlay, focus restore) is classic and out of migration scope. The Saved View modal saves through `prksSavedViewRecords`. |
| Folder tree and hierarchy | `folders.js`, `folder-hierarchy-nav.js` | `prksFolderLibraryTreeInnerHtml`, `prksToggleFolderNodeInHost`, `prksPaintFolderLibraryGlance`, `prksPublishFolderDashboardState`, `__prksFolderDashboardState`, `__prksFolderLibraryBrandHomeReset` | The folder tree markup, expand/collapse persistence, glance, and hierarchy navigation are the product owner. Vue routes host them. |
| Folder detail host writer | `folders.js` | `prksCommitFolderDetailSurface`, `prksFolderDetailSummaryHtml`, `prksFolderDetailNavHtml`, `prksFolderDetailSubfoldersHtml`, `prksEffectiveFolderDetailWorks` | After the Vue paint, the writer commits the hierarchy nav and subfolder rows (folder-tree rows) into the Vue host and fills the detail tree. Same owner as the folder tree. |
| Work-card infrastructure | `work-cards.js` | `prksInitLazyWorkThumbs`, `prksRegisterWorkThumbUrl`, `prksShowWorkThumbPreview`, `prksWorkBrowseModeToggleHtml` | Thumbnail lifetime, preview, and browse mode stay classic. `PrksWorkCard` owns card markup and the credit text. |
| PDF, video, Research Notes, private notes | `works-pdf.js`, `works-video.js`, `works.js`, `ui.js` | `initPdfViewerForWork`, `renderVideoViewerPane`, `initEasyMDE`, `prksResearchNotesTextForWork`, `prksPrivateNotesTextForEntity` | Runtime owners kept out of migration scope. |
| Research Graph, Processing, command palette, workspace tabs | `research-graph.js`, `processing-files.js`, `command-palette.js`, `workspace-*.js` | `prksReleaseResearchGraph`, `prksProcessingAttachResources`, `prksOpenCommandPalette`, `prksWorkspaceSubscribe` | Runtime owners kept out of migration scope. `workspace-tabs.js` is the live effect coordinator behind the Vue shell's detached projection. |
| Classic summary primitives | `overview-primitives.js` | `prksPageSummaryHtml`, `prksStateSummaryHtml`, `prksNavAttentionBadgeHtml` | Remaining callers are classic: folder glance, workspace overview, Details state strip, nav badges. |
| Generated typed modules | built from `frontend-app/` by `npm run build` | `route-model.js`, `search-query-codec.js`, `workspace-model.js`, `work-route-projection.js`, `tab-leave.js`, `owner-resource.js` | Canonical typed sources compiled for classic callers. Never hand-edited. |
| Icons | `icons.js` | `prksIcon`, `prksRefreshIcons`, `prksPageHeaderIconHtml`, `prksTagSearchIconHtml`, `prksTagPlusIconHtml`, `prksProgressStatusIconHtml` | One icon registry for both worlds (classic modals, palette, folder tree, panels, and Vue routes, `PrksWorkCard`, the workspace drag adapter). A second Vue registry would duplicate it. `prksPageHeaderIconHtml` is read only by Vue route headers today; it stays with the registry it belongs to. |
| Document types | `doc-types.js` | `prksDocTypeBadgeHtml`, `prksDocTypeMenuShellHtml` | The document-type vocabulary and its badge/menu markup have one owner, shared by classic Details and new-file flows and by Vue `PrksWorkCard`, Types, Work detail, and the Processing card. |
| Help and markup | `ui.js`, `api.js`, `concepts.js` | `prksHintBtnHtml`, `prksEscapeHtml`, `prksResearchMarkdownHtml` | The hint-button markup, HTML escaping, and the research Markdown renderer (wiki links, sanitizing) are single rules used by classic panels and by Vue (`research-index/markdown.ts`, Person Group detail). |
| Dates | `date-format.js` | `prksIsoToDdMmYyyy`, `prksParsePublishedDateInput` | One parser and formatter for published dates, shared by classic metadata editing and the Vue Processing card and records. |
| Tag matching and inline comboboxes | `ui.js` | `prksTagMatchesQuery`, `prksTagExactMatch`, `prksTagComboboxLabel`, `prksTagVocabularyMessage`, `prksShowInlineComboboxResults`, `prksHideInlineComboboxResults` | Tag alias matching and the inline combobox behaviour are the same for classic tag pickers and the Vue Processing card and Tags intents. |
| Shared control helpers | `ui.js` | `prksSetButtonBusy`, `prksFlashButtonLabel`, `prksSegmentedControlHtml`, `prksBindSegmentedHidden`, `prksBindAutosizeTextareas`, `prksIsSmallScreen` | Busy/flash button feedback, segmented controls, textarea autosize, and the small-screen breakpoint follow `DESIGN.md` once for both worlds. `prksFlashButtonLabel` is read only by the Vue annotation drawer today. |
| Request and error reporting | `request-coordinator.js`, `api.js` | `prksRequest`, `prksIsAbortError`, `prksConsumeApiError`, `prksReportClientError`, `prksRequestCoordinatorSnapshot`, `prksResetRequestCoordinatorDiagnostics` | One request coordinator (dedupe, abort, diagnostics) and one client-error reporter. Vue Work detail, Playlist intents, the Query cache, and performance diagnostics use them. |

## Retired by the final audit

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
- Selectors with no remaining markup: `.person-profile__role-*` and
  `.prks-workspace-menu__sep`.
- Stale `env.d.ts` declarations for globals Vue no longer reads.
