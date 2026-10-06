# Frontend migration boundaries (B5 closeout)

Final bridge audit for #303 / #230. This records every crossing that remains
between the classic runtime (`frontend/js/`) and the Vue bundle
(`frontend/vue/prks-vue.js`, built from `frontend-app/`), who owns each side,
and why it is not migration debt. It also lists what the audit retired.

The finish line is coherent ownership, not "everything is Vue". A boundary
stays when its owner is a current product/runtime owner. A bridge goes when
its last consumer has crossed.

## Status

- B1 to B4 are complete. B5 visible cutover (#451, #452), dead-painter purge
  (#453), and browser/resource baselines (#455) are on `master`.
- #459 (long-lived listener growth) is closed. #469 merged as `ae5a7b5`; the
  B5 baseline in `docs/b5-browser-baselines.md` now shows a zero
  `longLivedListenerLive` delta. The listener-growth follow-up is resolved.
- There is no further route or component migration wave.
- After the final bridge-audit PR, the only remaining work is the #303 / #230
  completion verification. Do not close either issue from an implementation
  PR.

## Why the window boundary exists

Classic scripts load first, in `index.html` order. The Vue bundle is an ES
module that loads after `app.js`. Code in one world reaches the other only
through `window`. Each name below is one deliberate crossing with one owner on
each side. Vue reads classic services through typed declarations in
`frontend-app/env.d.ts`; a declaration with no reader is stale and is removed.

Classic files end with an explicit `window.X = X` export list. For top-level
classic functions that list documents the file's public surface; it is not a
bridge by itself.

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

## Vue to classic runtime services (classic owns, Vue calls)

These are current product or runtime owners. None is a renderer kept alive
for a migrated surface.

| Family | Classic owner | Representative globals | Why it stays |
| --- | --- | --- | --- |
| Route coordinator | `app.js`, `tab-context.js`, `navigation.js` | `prksPresentVueRoute`, `prksTabContextOwnsEntityRoute`, `prksNavigate` | Owns the host and request envelope for every route feature, and the TabContext generation checks. |
| People, Person Group, Playlist route families | `people.js`, `people-groups.js`, `playlists.js` | `renderPeopleList`, `renderPersonDetails`, `renderPlaylistDetail`, `prksRefreshPersonGroupMain`, `prksReloadPlaylistDetail` | They resolve the effective/offline/durable record and hand it to `prksPresentVueRoute`, then bind offline state. They paint no HTML. |
| Durable and offline operations | `api.js`, `local-store.js`, `offline-runtime.js`, feature `*-state.js` | `savePersonProfileDraft`, `updatePlaylist`, `prksTagsMerge`, `prksSaveWorkMetadataFields`, `prksOfflineRuntimeState` | One durable queue and one effective-state model. Vue never mirrors them. |
| Modals and dialogs | `ui.js`, feature modal openers | `prksConfirmDestructive`, `prksPromptTextDialog`, `prksOpenNewPersonModalFromPeoplePage` | The modal system is out of migration scope. |
| Folder tree and hierarchy | `folders.js`, `folder-hierarchy-nav.js` | `prksFolderLibraryTreeInnerHtml`, `prksToggleFolderNodeInHost`, `prksPaintFolderLibraryGlance`, `prksPublishFolderDashboardState`, `__prksFolderDashboardState`, `__prksFolderLibraryBrandHomeReset` | The folder tree markup, expand/collapse persistence, glance, and hierarchy navigation are the product owner. Vue routes host them. |
| Folder detail host writer | `folders.js` | `prksCommitFolderDetailSurface`, `prksFolderDetailSummaryHtml`, `prksFolderDetailNavHtml`, `prksFolderDetailSubfoldersHtml`, `prksEffectiveFolderDetailWorks` | After the Vue paint, the writer commits the hierarchy nav and subfolder rows (folder-tree rows) into the Vue host and fills the detail tree. Same owner as the folder tree. |
| Work-card infrastructure | `work-cards.js` | `prksInitLazyWorkThumbs`, `prksRegisterWorkThumbUrl`, `prksShowWorkThumbPreview`, `prksWorkBrowseModeToggleHtml` | Thumbnail lifetime, preview, and browse mode stay classic. `PrksWorkCard` owns card markup and the credit text. |
| PDF, video, Research Notes, private notes | `works-pdf.js`, `works-video.js`, `works.js`, `ui.js` | `initPdfViewerForWork`, `renderVideoViewerPane`, `initEasyMDE`, `prksResearchNotesTextForWork`, `prksPrivateNotesTextForEntity` | Runtime owners kept out of migration scope. |
| Research Graph, Processing, command palette, workspace tabs | `research-graph.js`, `processing-files.js`, `command-palette.js`, `workspace-*.js` | `prksReleaseResearchGraph`, `prksProcessingAttachResources`, `prksOpenCommandPalette`, `prksWorkspaceSubscribe` | Runtime owners kept out of migration scope. `workspace-tabs.js` is the live effect coordinator behind the Vue shell's detached projection. |
| Classic summary primitives | `overview-primitives.js` | `prksPageSummaryHtml`, `prksStateSummaryHtml`, `prksNavAttentionBadgeHtml` | Remaining callers are classic: folder glance, workspace overview, Details state strip, nav badges. |
| Generated typed modules | built from `frontend-app/` by `npm run build` | `route-model.js`, `search-query-codec.js`, `workspace-model.js`, `work-route-projection.js`, `tab-leave.js`, `owner-resource.js` | Canonical typed sources compiled for classic callers. Never hand-edited. |

## Retired by the final audit

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
  `prksOfflineIsMutationBlocked`, `prksOfflineMarkDomainChanged`, and private
  graph, tab-menu, local-store, and doc-type helpers.
- Unused classic copies of Vue-owned constants: the Work status labels and
  the Folder Library filter key.
- Selectors with no remaining markup: `.person-profile__role-*` and
  `.prks-workspace-menu__sep`.
- Stale `env.d.ts` declarations for globals Vue no longer reads.
