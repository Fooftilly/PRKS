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

## KEEP / MOVE / RETIRE (#1033)

Every crossing in this file has one of these decisions:

- **KEEP**: every row in "Classic to Vue", "Vue to classic runtime services"
  and "Host and object markers". The row's last column is the reason. Each one
  is a single owner that both worlds call, such as the route coordinator, the
  durable queue, the PDF and EasyMDE runtimes, or a shared rule like icons,
  dates or comboboxes. It is not a classic renderer kept alive for a surface
  that Vue already paints.
- **MOVE**: the dialogs marked MOVED in the dialog inventory are done. The one
  open MOVE is the bulk edit sheet for selected Works (#1325).
- **RETIRE**: everything under "Retired by the final audit" and in "Shell
  chrome" is gone. Rule for a future crossing: a Vue publication with no
  classic reader is dead, and it is removed rather than kept.

Known debt inside a KEEP row is tracked in its own issue, not left in the
row: the combobox keyboard model never disconnects its observers (#1327).

## Classic to Vue (Vue registers, classic calls)

| Global | Registered by | Called by | Why it stays |
| --- | --- | --- | --- |
| `prksVuePresentRoute`, `prksVueDismissRoute` | `src/route-surface/lifecycle.ts` | `app.js` (`prksDeliverVueRoute`, route leave) | The coordinator in `app.js` owns route identity, TabContext, the pane host, and effective/offline reads. Vue owns presentation. One present and one dismiss entry serve every route feature. Early host-local requests cover a route that paints before the module registers. |
| `prksMountWorkDetail` | `src/features/work/session.ts` | `app.js` `case 'work'` | The coordinator publishes the Work route projection; `detail-lifecycle.ts` owns the async mount order (notes ticket, viewer modules, shell, PDF, Research Notes, panel). |
| `prksVuePresentWorkPanelRead`, `prksVueRefreshWorkPanelRead`, `prksVueDismissWorkPanelRead` | `src/features/work/panel-session.ts` | `ui.js`, `work-metadata-editor.js` | `ui.js` owns the right-panel tab stack and focus. Vue owns the Work view-mode panel. |
| `prksVuePresentWorkMetadataEditor`, `prksVueDismissWorkMetadataEditor`, `prksVueCaptureWorkMetaDraft`, `prksVueWorkMetadataEditorOwns`, `prksVueSetWorkMetadataFieldError`, `prksVueAcceptWorkMetadataField`, `prksVueApplyWorkMetadataChrome`, `prksVueWorkMetaDraftIsDirty`, `prksVueWorkMetaSessionStill` | `src/features/work/metadata-session.ts` | `ui.js`, `work-metadata-editor.js`, `work-source-editor.js` | Durable metadata and source writes, acknowledgements, and conflicts stay with the classic durable-operation owners. Vue owns the form, draft, and field errors. |
| `prksVuePresentWorkPrivateNotes`, `prksVueDismissWorkPrivateNotes` | `src/features/work/private-note-session.ts` | `ui.js` | Private-note durability stays in `ui.js` (`privateNotesEditor` session). Vue owns the Work field. |
| `prksVueDismissWorkResearchNotes` | `src/features/work/research-note-session.ts` | `works.js` | EasyMDE and the durable note API stay in `works.js` (Research Notes product owner). Its teardown drops the Vue pane. |
| `prksModalHost` | `src/modal/ModalHost.vue` | `ui.js` (`prksConfirmDialog`, `prksPromptTextDialog`, `prksOpenModalUnsavedConfirm`, `requestModalClose`, the baseline helpers, and `openModal` / `closeModals` through `ownsDialog`, `showDialog`, `beginOpening`, `restoreDialog`, `hideDialogs`, `dialogsClosed`, `isDialogDirty`, `captureDialogBaseline`, `resetDialogBaselines`), `app.js` (skips its Node-harness Folder and Playlist submit fallback when the host renders the Folder dialog) | One dialog presenter. Vue paints confirm, alert, prompt, and unsaved dismissal, and owns Escape, focus containment, and focus return. It also renders the create-form dialogs (`src/modal/ModalDialog.vue`, one per form, state in `src/modal/dialogs.ts`) and owns which one is open, each opening's generation, its inert state, its unsaved-changes baseline and readiness; the dialog root reflects them as `hidden`, `data-prks-open-generation`, `inert` and `data-prks-dialog-ready` for DOM readers. Each form registers its own opening, close and Escape steps with its dialog (`useDialogLifecycle`): `openModal` shows the dialog, sets the opener's own state (the role linker's opener, an index's create origin, Settings orientation), then calls `beginOpening`, which runs the form's prepare (inert while it loads), takes the baseline and focuses the first field; `closeModals` calls `dialogsClosed`, so the closed Folder and Group dialogs drop their drafts. Classic functions stay the call sites. Dirty baselines for shells that are still classic, and leave decisions, stay in `ui.js` and `tab-leave.ts`. A dirty close before the host is installed leaves the modal open; it does not discard, and the next close request confirms once the host exists. |
| `prksRefreshFolderModalValidation`, `openPersonProfileTemplateModal`, `prksOpenSavedViewModal` | `src/features/create-forms/` (`FolderCreateForm.vue`, `PersonTemplateForm.vue`), `src/features/saved-views/SavedViewForm.vue` | `folders.js` (refreshes the Folder title check after a Folder change), `people.js` (the Person sidebar's template button), the Saved View openers | The Folder, Person, Person Group, Playlist, New File, Saved View, Link Person to Work and person template dialogs are Vue: `ModalHost` renders each form inside a `ModalDialog` shell (no teleport, no shell in `frontend/index.html`). Vue owns markup, validation feedback, and view-local draft/dirty/busy, and calls the existing durable writers. Each form runs its own opening, close and Escape steps through its dialog (see the `prksModalHost` row); there is no per-form adapter on `window` any more. The New File (`#work-modal`) body and footer are Vue too (`NewFileForm.vue` and `new-file/`): source, details, people, tags, playlist and the typed submit (`new-file/save.ts`) are the component's, gated to one opening; a PDF create and its first Tag links go through the typed `src/api/works-create.ts` service, online-only and outside the durable queue because PDF bytes have no offline Blob design yet (the form refuses it offline first, and the service reports a lost connection to the offline runtime); a video create stays the durable CREATE_WORK writer; `ui.js` keeps shared services the Vue sections call: the combobox and YouTube helpers, person quick-create and the person catalogue (`prksSetPersonCatalogue`), and the role-credit picker (`prksBindRoleCreditPicker`, `prksRefreshRoleCreditPicker`, `prksSetRoleCreditPickerValue`, `prksReadRoleCreditName`, `prksResolveRoleCreditNameForLink`), which the role link dialog shares. The Saved View body and title are Vue as well (`src/features/saved-views/SavedViewForm.vue`, no unsaved-changes confirm). The Link Person to Work (`#role-modal`) body is Vue too (`RoleLinkForm.vue` and `role-link/save.ts`): person and file comboboxes, quick-create, the role radiogroup, the credit picker and the outcome messages are the component's; `ui.js` keeps `prksLinkPersonFromRoleModal`, which owns the opener checks and the durable `prksSaveWorkPersonRoleDurably` write and tells the form whether the result may still settle that opening. The Edit Profile Using Template (`#person-template-modal`) body is Vue too (`PersonTemplateForm.vue` and `person-template/template.ts`): it installs `openPersonProfileTemplateModal`, builds and strictly parses the JSON template, copies the field guide, and on Apply calls `openPersonProfileEdit` and fills the Vue profile editor's fields through input events, so the editor's Save stays the only write (no unsaved-changes confirm). Settings keeps only its classic shell (its body is Vue). |
| `prksVueUpdateResearchNotesRecovery`, `prksVueUpdateWorkPrivateNotesRecovery`, `prksVueCloseRecoveryReview` | `src/features/work/research-notes-recovery.ts`, `src/features/work/private-notes-recovery.ts`, `src/features/editor-recovery/recovery-presenter.ts` | `works.js`, `ui.js`, `work-note-recovery.js`, Vue modal host | Recovery planning, adoption, and discard stay in the shared classic adapter `work-note-recovery.js` over `prksEditorRecovery` (#466, #474); `works.js` (Research Notes) and `ui.js` (Reminders) supply their sessions. Vue owns the recovery notices and the one Review dialog; the Vue modal host routes Escape to that close. |
| `prksVueSyncWorkPdfAnnotationPopup`, `prksVueDismissWorkPdfAnnotationPopup`, `prksVueSyncWorkPdfAnnotationDrawer`, `prksVueDismissWorkPdfAnnotationDrawer` | `src/features/work/pdf-annotation-*.ts` | `works-pdf.js` | The PDF viewer runtime and annotations own state and lifetime. Vue renders the popup and drawer. |
| `prksVueCloseTagsAliasModal`, `prksVueCloseTagsMergeModal`, `prksVueClosePublishersAliasModal` | `src/features/tags/session.ts`, `src/features/publishers/session.ts` | Vue modal host, `tags.js` | The Vue modal host routes Escape to these closes. Vue owns those modal bodies. |
| `prksVueReportTagsRefreshFailure` | `src/features/tags/session.ts` | `app.js` | The coordinator loads the tag vocabulary; Vue shows the failure. |
| `prksOpenSettingsToLastCategory`, `prksActivateSettingsCategory`, `prksSyncSettingsNavOrientation` | `src/features/settings/navigation.ts` | `ui.js` `openModal('settings-modal')` | Vue owns the Settings category nav, panels, and controls. `#settings-modal` stays the shell: open, close, Escape, and focus return stay on `requestModalClose` and the Vue modal host. Category selection is one in-memory ref, not a TabContext and not stored. Device and library settings still persist through the classic preference and `/api/settings` functions. Backup, restore, reindex, linearize, and offline-cache reads stay those classic operations. A settings open before the surface is installed sets `__prksSettingsOpenRequested` and does not discard. |
| `prksVueActivatePerformanceDiagnostics`, `__prksPerformanceDiagnosticsRequested` | `src/features/performance-diagnostics/activation.ts` | Vue settings navigation | The first Diagnostics activation enables the read. The flag covers activation before the module loads. |
| `prksSavedViewRecords` | `src/features/saved-views/session.ts` (records service in `records.ts`) | `app.js` Saved View detail case, `command-palette.js` | Saved View reads and writes are owned by the typed client and TanStack Query in Vue. The classic route coordinator and the palette use that one service rather than a second client, and so does the Vue Saved View dialog. |
| `prksProcessingRecords` | `src/features/processing/session.ts` (records service in `records.ts`) | `app.js` Processing case | Processing inbox reads and writes are owned by the typed client in Vue, which also owns file save and import. The coordinator loads the inbox through this service rather than a second client. The classic quick-create helpers in `processing-files.js` call their own durable APIs and do not use it. |
| `prksProcessingQueueCount` | `src/features/processing-queue/session.ts` (queue reads in `api/processing-items.ts`) | `app.js` `prksRefreshProcessingNavBadge` | On a PostgreSQL Library the Processing page is the upload queue (`/api/processing-items`), whose typed client is in Vue. The classic nav badge refresh reads its count through this one bounded page read rather than a second client; `app.js` picks it or `prksProcessingRecords` by `prksLibraryContentMode()`. |
| `prksWorkMetadataGroupFields`, `prksWorkPanelReadOwns` | `src/features/work/metadata-session.ts`, `src/features/work/panel-session.ts` | `work-metadata-editor.js` | The metadata field grouping and the panel-read ownership check are Vue rules. The classic durable metadata editor asks Vue instead of keeping a copy. |
| `prksCitations` | `src/features/citations/session.ts` (formatting in `engine.ts`, export in `export.ts`) | `work-selection.js` Cite sheet | Citation V1 (#41): the CSL-JSON and BibTeX reads, citeproc-js formatting, the clipboard and the downloads are Vue. The selection sheet only lists the four actions and shows the result or the error. The Work panel citation is a Vue Teleport into the classic shell's `work-panel-citation` slot. `__prksCitationStyle` is the Library's default style, set by `api.js` from `/api/settings` reads and patches. |
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
| Modals and dialogs | `ui.js` feature shells, `concepts.js`, feature modal openers; presentation in `src/modal/ModalHost.vue`; Settings body in `src/features/settings/`; Folder, Person, Group, Playlist, New File, Link Person to Work, and person template bodies in `src/features/create-forms/`; the Saved View body in `src/features/saved-views/SavedViewForm.vue` | `openModal`, `closeModals`, `requestModalClose`, `prksConfirmDestructive`, `prksPromptTextDialog`, `prksAlertDialog`, `prksOpenNewPersonModalFromPeoplePage`, `prksOpenSavedViewModal`, `prksCreateConceptFlow` | The create-form dialogs (Folder, Person, Person Group, Playlist, New File, Saved View, Link Person to Work, person template) and the restore confirmation are `ModalHost`'s: it renders their shells and owns which is open, and each form runs its own opening, close and Escape steps through its dialog. The remaining classic shells (Settings and the master add chooser; see the dialog inventory) and the shared backdrop and focus restore to the opener stay classic in `openModal` / `closeModals`; Escape routing and dialog focus are the Vue host's. The Settings body is Vue; its shell stays `#settings-modal`. Folder, Person, Person Group, and Playlist bodies are Vue and report their own dirty state. The New File body, footer and submit are Vue. Confirm, alert, prompt, unsaved dismissal, Escape, and focus return are the Vue host, reached through `window.prksModalHost`. The Saved View body and title are Vue (`#saved-view-modal`); that form installs the Saved View openers and saves through `prksSavedViewRecords`. The Link Person to Work and Edit Profile Using Template bodies are Vue too (see the create-form row). |
| Folder hierarchy refresh and library glance data | `folders.js`, `folder-hierarchy-nav.js` | `prksFillFolderDetailTree`, `prksCommitFolderDetailSurface`, `prksScheduleFolderLibraryGlance`, `prksFolderLibraryCatalogGlanceParts`, `prksPublishFolderDashboardState`, `__prksFolderDashboardState`, `__prksFolderLibraryBrandHomeReset`, `prksFolderNavTriggerHtml`, `prksMountFolderHierarchyNav` | Vue renders Folder Library and Folder detail tree rows, the subfolder list, and glance/summary text from typed state. Expand/collapse and the library filter live on the owning TabContext (`ctx.ui.folderTree`). Session storage restores only the first Folder Library mount in the page; a later pane starts unfiltered. The refresh claim, offline bind, layout observer, catalogue load, and glance extras stay here and publish a snapshot or part list. The hierarchy switcher (`prksFolderNavTriggerHtml`) still owns crumb and nearby markup because its popover paints that DOM. |
| Folder detail effective works | `folders.js` | `prksEffectiveFolderDetailWorks` | Embedded Work summaries. Vue does not read the durable queue. |
| Work-card infrastructure | `work-cards.js` | `prksInitLazyWorkThumbs`, `prksRegisterWorkThumbUrl`, `prksShowWorkThumbPreview`, `prksWorkBrowseModeToggleHtml` | Thumbnail lifetime, preview, and browse mode stay classic. `PrksWorkCard` owns card markup and the credit text. |
| PDF, Research Notes, private notes | `works-pdf.js`, `pdf-work-runtime.js`, `works.js`, `ui.js` | `initPdfViewerForWork`, `prksLayoutAnnotationDrawer`, `prksHasPendingWorkAnnotationSync`, `initEasyMDE`, `prksResearchNotesTextForWork`, `prksRestoreResearchNotesRecovery`, `prksResearchNotesRecoveryView`, `prksResearchNotesRecoveryDetails`, `prksResearchNotesRecoveryRestore`, `prksResearchNotesRecoveryReplace`, `prksResearchNotesRecoveryDiscard`, `prksRefreshResearchNotesRecovery`, `prksRestoreWorkPrivateNoteRecovery`, `prksWorkPrivateNotesRecoveryView`, `prksWorkPrivateNotesRecoveryDetails`, `prksWorkPrivateNotesRecoveryRestore`, `prksWorkPrivateNotesRecoveryReplace`, `prksWorkPrivateNotesRecoveryDiscard`, `prksRefreshWorkPrivateNotesRecovery`, `prksPrivateNotesTextForEntity`, `prksBindPrivateNotesField` | Runtime owners kept out of migration scope. The Work video player is the Vue pane `WorkVideoPane`. |
| Research Graph, Processing, workspace tabs | `research-graph.js`, `processing-files.js`, `workspace-*.js` | `prksReleaseResearchGraph`, `prksProcessingAttachResources`, `prksWorkspaceSubscribe`, `prksWorkspaceOnShellCommit`, `prksWorkspacePlaceContentHost`, `prksWorkspaceWatchCanvas`, `prksWorkspaceOpenTabMenu` | Runtime owners kept out of migration scope. `workspace-tabs.js` is the live effect coordinator behind the Vue shell's detached projection. |
| Command palette | `command-palette.js` catalog and dispatch; dialog in `src/palette/CommandPalette.vue` | `prksOpenCommandPalette`, `prksCloseCommandPalette`, `prksCommandPaletteView`, `prksCommandPaletteSubscribe`, `prksCommandPaletteSetQuery`, `prksCommandPaletteHandleKey`, `prksCommandPaletteActivateFromPointer` | One catalog, search, freshness, and allowlisted dispatch. Vue paints the dialog, input, rows, status, and hints, and sends those intents back. The Vue ribbon binds the launch control once (`data-palette-bound`) and calls `prksOpenCommandPalette`; `command-palette.js` does not add a second listener. |
| Processing queue runtime hooks | `ui.js`, `app.js`, `api.js`, `works-pdf.js`; `tab-leave.js` (generated) | `prksConfirmUnsavedRouteLeave`, `prksSetProcessingAttentionCount`, `prksMarkProcessingImportChanged`, `prksMountPdfPreview`, `prksTabLeave.registerProbe` | The Vue Processing queue (`src/features/processing-queue/`) uses the same owners as the classic Processing page: the shared unsaved-leave confirmation and the leave-probe registry, the nav badge, the offline-cache coherence marks after an import, and the PRKS PDF viewer for the preview. A second copy of any of these would split their rules. |
| Classic summary primitives | `overview-primitives.js` | `prksPageSummaryHtml`, `prksStateSummaryHtml`, `prksNavAttentionBadgeHtml` | Remaining callers are classic: workspace overview, Details state strip, nav badges. Folder Library glance and Folder detail summary render the same classes from Vue. |
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

## Dialog inventory (#1036)

Every live dialog and dialog-like surface, with its owner and its decision.
**MOVED** is Vue now; **KEEP** stays classic for the reason given;
**DEFERRED** has its own issue.

| Surface | Owner | Decision |
| --- | --- | --- |
| New File, Folder, Playlist, Saved View, Person Group, Person, Edit Profile Using Template, Link Person to Work | `ModalDialog` shells in `ModalHost` (`CreateFormsHost.vue`); bodies in `src/features/create-forms/` and `src/features/saved-views/` | MOVED. Each form runs its own opening, close and Escape steps (`useDialogLifecycle`). |
| Replace library? (restore confirmation, `#prks-restore-confirm-modal`) | `ModalDialog` in `ModalHost` (`src/features/settings/SettingsDialogs.vue`, `RestoreConfirmForm.vue`); the Backup panel runs the restore (`restore-session.ts`) | MOVED |
| Confirm, alert, prompt, unsaved dismissal | `ModalHost.vue` | MOVED (#1035) |
| Settings (`#settings-modal`) | classic shell in `index.html`, Vue body (`SettingsSurface.vue`) | KEEP shell: `openModal` corrects its orientation before returning, and the body is Vue (#1037). |
| Create New Entity chooser (`#master-add-modal`) | `index.html` | KEEP: four buttons that open the Vue dialogs, with no form state. |
| Tag alias and merge, Publisher alias | `TagsRoute.vue`, `PublishersRoute.vue` | MOVED with their routes; standalone page dialogs closed through `prksCloseStandalonePageModal`. |
| Editor recovery review | `EditorRecoveryReview.vue` | MOVED |
| Command palette | `CommandPalette.vue` | MOVED (#1039) |
| PDF annotation popup and drawer | `WorkPdfAnnotationPopup.vue`, `pdf-annotation-*.ts` | MOVED presentation; the PDF runtime keeps their lifetime. |
| Research link picker (`#prks-research-picker`) | `works.js` | KEEP: it lives inside the Research Notes editor (EasyMDE) runtime, which #1033 keeps. |
| Sign in (`sign-in.js`) | classic | KEEP: it must work before the Vue bundle and without a session. |
| Folder hierarchy panel, workspace overview | `folder-hierarchy-nav.js`, `workspace-overview.js` | KEEP: navigation popovers in the shell, not forms. |
| Bulk edit sheet for selected Works (`#prks-bulk-sheet`) | `work-selection.js` | DEFERRED to #1325. |

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
