/// <reference types="vite/client" />

interface TagsMutationOutcome {
  ok?: boolean
  reason?: string
}

/** Painted inbox people. The coordinator stores the person rows; quick-create appends `{ id, name }`. */
interface ProcessingPeopleCatalogueEntry {
  id?: string
  name?: string
  first_name?: string
  last_name?: string
}

interface ProcessingDraft {
  title?: string
  status_draft?: string
  abstract?: string
  source_url?: string
  published_date?: string
  year?: string
  publisher?: string
  location?: string
  edition?: string
  journal?: string
  volume?: string
  issue?: string
  pages?: string
  isbn?: string
  doi?: string
  doc_type?: string
  private_notes?: string
  thumb_page?: string
  target_folder_id?: string
  roles?: Array<{ person_id?: string; person_name?: string; role_type?: string }>
  tags?: Array<{ id?: string; name?: string }>
}

interface ProcessingPreviewTarget {
  id: string
  filename?: string
  relPath?: string
  canPreview?: boolean
}

interface PrksPromptTextOptions {
  title: string
  message?: string
  defaultValue?: string
  multiline?: boolean
  okLabel?: string
}

interface PrksConfirmDestructiveOptions {
  title: string
  message: string
  confirmLabel?: string
}

interface PrksAlertOptions {
  title: string
  message: string
}

interface PrksNavigateOptions {
  replace?: boolean
  tabId?: string
  target?: string
}

interface Window {
  /** Sanitized `/api/client-errors` reporter from `api.js` (deduped there). */
  prksReportClientError?: (input: { kind: string; source: string; request_id?: string }) => void
  prksVueActivatePerformanceDiagnostics?: () => void
  __prksPerformanceDiagnosticsRequested?: boolean
  prksRequestCoordinatorSnapshot?: () => {
    counts?: Record<string, unknown>
    current?: Record<string, unknown>
    peaks?: Record<string, unknown>
    waits?: Record<string, unknown>
  } | null
  prksResetRequestCoordinatorDiagnostics?: () => void
  PRKS_REQUEST_MAX_READS?: number
  prksAbstractExcerpt?: (value: unknown) => string
  prksWorkCardHtml?: (
    work: {
      id?: unknown
      title?: unknown
      status?: unknown
      file_path?: unknown
      thumb_url?: unknown
      thumb_page?: unknown
      doc_type?: unknown
      year?: unknown
      published_date?: unknown
      file_size_bytes?: unknown
      linked_authors?: unknown
      author_text?: unknown
      primary_author?: unknown
      primary_editor?: unknown
      source_kind?: unknown
      source_url?: unknown
      provider?: unknown
      provider_id?: unknown
      abstract_excerpt?: unknown
      abstract?: unknown
    },
    options: { subtitle?: string; suppressThumbnail?: boolean; hideDocTypeBadge?: boolean },
  ) => string
  prksDocTypeBadgeHtml?: (docType: string) => string
  prksWorkBrowseModeToggleHtml?: (hiddenId?: string) => string
  prksWorkBrowseCollectionClass?: (extraClass?: string) => string
  prksBindWorkBrowseMode?: (root: ParentNode | null) => void
  prksTagSearchIconHtml?: () => string
  prksInitLazyWorkThumbs?: (root: ParentNode | null) => void
  prksRefreshIcons?: (root: ParentNode | Document | null) => void
  prksBindAutosizeTextareas?: (root: ParentNode | null) => void
  prksProgressStatusIconHtml?: (status: string, opts?: { size?: string | number }) => string
  initPrksDocTypeMenu?: (hiddenInputId: string, opts?: { disabled?: boolean; selectedValue?: string }) => void
  prksSaveWorkMetadataFields?: (workId: string, groupName?: string) => void
  prksSaveWorkSource?: (workId: string) => void
  prksCancelWorkMetaEdit?: () => void | Promise<void>
  prksBindPrivateNotesField?: (entityType: string, entityId: string, owner?: object) => void
  prksEnsureWorkPrivateNoteSession?: (
    owner: object,
    workId: string,
    initialText: string,
  ) => { draftText: string; retired?: boolean } | null
  prksPrivateNotesTextForEntity?: (entityType: string, entityId: string, serverText: string) => string
  prksResolveWorkMetadataFieldConflict?: (opId: string, apply: boolean, group: string) => void
  prksResolveWorkSourceConflict?: (opId: string, apply: boolean) => void
  prksVueAcceptWorkMetadataField?: (ctx: object, field: string, value: unknown) => boolean
  prksVuePresentWorkResearchNotes?: (
    ctx: object,
    work: { id?: unknown },
    initialText: string,
  ) => boolean
  prksVuePresentRoute?: (request: unknown) => boolean
  prksVueDismissRoute?: (owner: object) => void
  prksVueCloseTagsAliasModal?: (modal?: Element | null) => void
  prksVueCloseTagsMergeModal?: (modal?: Element | null) => void
  prksVueReportTagsRefreshFailure?: (owner: object, message: string) => void
  prksReloadTagsVocabulary?: (
    owner: object,
    generation: number,
    resume?: {
      aliasTagId?: string | null
      mergeSourceId?: string | null
      mergeTargetId?: string | null
    } | null,
  ) => Promise<boolean>
  prksTagsAddAlias?: (tagId: string, alias: string) => Promise<TagsMutationOutcome>
  prksTagsRemoveAlias?: (tagId: string, alias: string) => Promise<TagsMutationOutcome>
  prksTagsDelete?: (tagId: string) => Promise<TagsMutationOutcome>
  prksTagsMerge?: (sourceId: string, targetId: string) => Promise<TagsMutationOutcome>
  fetchTags?: (options?: { used?: boolean; signal?: AbortSignal }) => Promise<unknown>
  prksTagVocabularyMessage?: (error: unknown, action: string) => string
  prksVueClosePublishersAliasModal?: () => void
  renderResearchGraph?: (
    container: HTMLElement,
    options?: {
      adoptShell?: boolean
      ctx?: object
      focus?: string
      signal?: AbortSignal
      routeGen?: number
      stale?: () => boolean
      loadSnapshot?: (people: boolean, signal?: AbortSignal) => Promise<unknown>
      onSnapshot?: (result: unknown) => void
    },
  ) => Promise<unknown>
  prksReleaseResearchGraph?: (owner: object) => void
  __prksProcessingPeople?: ProcessingPeopleCatalogueEntry[]
  prksReloadProcessingFiles?: (
    owner: object,
    generation: number,
    resume?: { visibleCount?: number | null } | null,
  ) => Promise<boolean | string>
  prksProcessingRoleTypes?: () => string[]
  prksProcessingDomPrefix?: (owner: { tabId?: string } | null | undefined) => string
  prksProcessingAttachResources?: (owner: object, host: HTMLElement) => void
  prksProcessingReleaseResources?: (owner: object) => void
  prksProcessingSetPreview?: (
    owner: object,
    file: ProcessingPreviewTarget,
  ) => 'card' | 'side' | 'unavailable'
  prksProcessingSave?: (fileId: string, draft: ProcessingDraft) => Promise<unknown>
  prksProcessingImport?: (fileId: string) => Promise<unknown>
  prksProcessingSearchTags?: () => Promise<unknown>
  prksProcessingCreateTag?: (name: string) => Promise<{ id: string; name: string }>
  prksProcessingQuickCreateFolder?: (title: string) => Promise<{
    ok?: boolean
    id?: string
    title?: string
    folders?: unknown
    foldersFailed?: boolean
    message?: string
  }>
  prksProcessingQuickCreatePerson?: (name: string) => Promise<{
    ok?: boolean
    id?: string
    name?: string
    people?: unknown
    message?: string
  } | null>
  prksSegmentedControlHtml?: (
    hiddenId: string,
    ariaLabel: string,
    labels: string[],
    selectedValue: string,
    variant: string,
    options?: { dataField?: string; compact?: boolean; dataRole?: string; withRoleIcons?: boolean },
  ) => string
  prksBindSegmentedHidden?: (hiddenId: string) => void
  prksDocTypeMenuShellHtml?: (prefix: string, selectedValue: string, disabled?: boolean) => string
  prksIsoToDdMmYyyy?: (iso: string) => string
  prksShowInlineComboboxResults?: (input: HTMLElement, results: HTMLElement) => void
  prksHideInlineComboboxResults?: (results: HTMLElement) => void
  prksTagMatchesQuery?: (tag: { name?: string; aliases?: string[] }, query: string) => boolean
  prksTagExactMatch?: (tag: { name?: string; aliases?: string[] }, query: string) => boolean
  prksTagComboboxLabel?: (tag: { name?: string; aliases?: string[] }, query: string) => string
  personMatchesComboboxQuery?: (person: Record<string, unknown>, query: string) => boolean
  formatPersonComboboxSubtitle?: (person: Record<string, unknown>) => string
  prksWorkHasRoleLink?: (
    roles: Array<{ person_id?: string; role_type?: string }>,
    personId: string,
    roleType: string,
  ) => boolean
  prksTagPlusIconHtml?: () => string
  /** Classic-script bridge for `frontend-app/src/features/search/codec.ts`. Vue imports that module. */
  prksSearchQueryCodec?: {
    definitionFromRoute: (route: unknown) => {
      ok: boolean
      empty?: boolean
      unsavable?: boolean
      message?: string
      definition?: { mode: string; q: string; tag: string; author: string; publisher: string }
    }
    hashFromDefinition: (definition: unknown) => string
    optionsFromDefinition: (definition: unknown) => {
      q: string
      tag: string | null
      options: { any?: string; author?: string; publisher?: string }
    }
    summaryText: (definition: unknown) => string
  }
  prksOpenSavedViewModalFromCurrentSearch?: (hash?: string) => void
  prksOpenSavedViewModal?: (options: {
    viewId?: string
    name?: string
    definition?: { mode: string; q: string; tag: string; author: string; publisher: string }
  }) => void
  /** Saved View records for classic callers (coordinator, modal, palette). Owned by frontend-app. */
  prksSavedViewRecords?: import('./src/features/saved-views/records').SavedViewRecords
  prksOpenCommandPalette?: () => void
  prksScopeLineHtml?: (options: { total?: number; label?: string }) => string
  prksEffectiveFolderDetailWorks?: (folder: unknown) => unknown[]
  prksFolderDetailSummaryHtml?: (folder: unknown) => string
  prksFolderDetailNavHtml?: (ctx: unknown, folder: unknown) => string
  prksFolderDetailSubfoldersHtml?: (children: readonly unknown[]) => string
  prksCommitFolderDetailSurface?: (
    ctx: unknown,
    folder: unknown,
    container: HTMLElement,
    options?: { preserveFolderWorkspace?: boolean },
  ) => void
  prksMountFolderHierarchyNav?: (ctx: unknown, folder: unknown, container: ParentNode | null) => void
  prksDeleteFolderFromDetail?: (folderId: string, still?: () => boolean) => Promise<void>
  prksOpenNewFolderFromDetail?: (folder: Record<string, unknown>) => void
  prksOpenNewGroupModalFromGroupsPage?: (owner?: object) => void
  prksTakePersonGroupCreateNavigation?: () => { mode?: string; tabId?: string }
  prksClearPersonGroupIndexCreateOrigin?: () => void
  openPersonGroupEdit?: (owner?: object) => void
  closePersonGroupEdit?: (owner?: object) => void
  prksTogglePersonGroupMembersEdit?: (owner?: object) => void
  prksBindPersonGroupDetailChrome?: (owner?: object) => void
  prksSetButtonBusy?: (button: HTMLElement | null, busy: boolean, options?: { busyLabel?: string }) => void
  prksHintBtnHtml?: (hintType: string, ariaLabel: string, extraClass?: string) => string
  savePersonGroupEditor?: (
    owner: object | null | undefined,
    groupId: string,
    draft: { name: string; description: string; parent_id: string; parent_name: string },
    baseline: { name: string; description: string; parent_id: string; parent_name: string },
    session: number,
  ) => Promise<{ ok: boolean; quiet?: boolean; message?: string }>
  deletePersonGroupEditor?: (owner: object | null | undefined, groupId: string, session: number) => Promise<void>
  prksTabContextIsFocused?: (ctx: unknown) => boolean
  prksApplyPersonOfflineState?: (container: ParentNode | null) => void
  personDateToDisplayFormat?: (stored: string) => string
  personLifespanDisplay?: (person: { birth_date?: string; death_date?: string }) => string
  safeHttpUrl?: (url: string) => string | null
  openPersonProfileEdit?: () => void
  closePersonProfileEdit?: () => void
  prksRefreshPersonDetailMain?: (ctx: unknown) => void
  prksBindPersonProfileDraft?: (
    ctx: unknown,
    personId: string,
    fields: {
      first_name: string
      last_name: string
      aliases: string
      about: string
      birth_date: string
      death_date: string
      image_url: string
      link_wikipedia: string
      link_stanford_encyclopedia: string
      link_iep: string
      links_other: string
    },
    groups: readonly { id?: string; name?: string }[],
    replaceGroups: boolean,
  ) => void
  prksMountPersonProfileGroupPicker?: (ctx: unknown, person: unknown, editor: HTMLElement) => Promise<void> | void
  savePersonProfileDraft?: (
    ctx: unknown,
    personId: string,
    draft: {
      first_name: string
      last_name: string
      aliases: string
      about: string
      birth_date: string
      death_date: string
      image_url: string
      link_wikipedia: string
      link_stanford_encyclopedia: string
      link_iep: string
      links_other: string
    },
    baseline: {
      first_name: string
      last_name: string
      aliases: string
      about: string
      birth_date: string
      death_date: string
      image_url: string
      link_wikipedia: string
      link_stanford_encyclopedia: string
      link_iep: string
      links_other: string
    },
    groupIds: readonly string[],
    baselineGroupIds: readonly string[],
    session: number,
    generation: number,
  ) => Promise<{ ok: boolean; message: string }>
  prksOpenNewPersonModalFromPeoplePage?: (owner?: object) => void
  deletePerson?: (ctx?: unknown, generation?: number) => Promise<void>
  prksTogglePersonWorksEdit?: (ctx?: unknown) => void
  prksRemoveWorkRoleLink?: (button: HTMLButtonElement) => Promise<void>
  prksPersonViewInGraph?: () => void
  prksFolderLibraryTreeInnerHtml?: (
    list: unknown,
    filterQuery?: string,
    options?: { delegateToggle?: boolean },
  ) => string
  prksFolderLibraryCatalogGlanceParts?: (folders: unknown) => Array<string | null>
  prksPaintFolderLibraryGlance?: (host: HTMLElement, parts: unknown) => void
  prksScheduleFolderLibraryGlance?: (
    root: ParentNode | HTMLElement,
    options?: {
      folders?: unknown[]
      recentlyAddedWorks?: unknown[] | null
    },
  ) => void
  prksPublishFolderDashboardState?: (state: {
    container?: HTMLElement | null
    vueOwned?: boolean
    recentlyAddedLoading?: boolean
    recentlyAddedWorks?: unknown[] | null
    switchTab?: (tab: string) => void | Promise<void>
    [key: string]: unknown
  }) => void
  prksUnpublishFolderDashboardState?: (container: HTMLElement) => void
  prksFolderDashboardStateForRoot?: (root: ParentNode | HTMLElement | null) =>
    | {
        container?: HTMLElement | null
        folders?: unknown[]
        recentlyAddedWorks?: unknown[] | null
        [key: string]: unknown
      }
    | undefined
  prksFolderLibraryExpandToggleLabel?: (folders: unknown) => string
  prksFolderLibraryExpandToggleInnerHtml?: () => string
  prksFolderTreeHasCollapsibleNodes?: (folders: unknown) => boolean
  prksFolderTreeAllCollapsed?: (folders: unknown) => boolean
  prksRecentlyAddedDateLabel?: (createdAt: unknown) => string
  prksRecentlyAddedWorkMatchesQuery?: (
    work: unknown,
    query: string,
    foldersById: Map<string, unknown>,
  ) => boolean
  prksBindFolderOfflineState?: (ctx: unknown, container: HTMLElement) => void
  prksOpenFolderModalFromLibrarySearch?: (query?: string) => void
  prksToggleFolderNode?: (folderId: string) => void
  prksToggleFolderNodeInHost?: (
    treeHost: HTMLElement,
    folderId: string,
    folders?: readonly unknown[],
  ) => void
  prksToggleAllFolderNodes?: () => void
  prksToggleAllFolderNodesInHost?: (
    treeHost: HTMLElement,
    folders: readonly unknown[],
  ) => void
  prksOfflineRecentlyAddedFetch?: () => Promise<{
    source?: string
    value?: unknown
  } | null>
  prksResolveOfflineRecentlyAdded?: (offline: unknown) => unknown[] | null
  prksEffectiveProjectionRows?: (rows: unknown[], domain: string) => unknown[]
  prksOfflineDomainGeneration?: (domain: string) => unknown
  prksRefreshPendingWorkMetadata?: () => Promise<void>
  prksPendingWorkMetadataGeneration?: () => unknown
  prksHideWorkThumbPreview?: () => void
  prksReleaseWorkThumbPreview?: (root: ParentNode | null) => void
  prksReleaseLazyWorkThumbs?: (root: ParentNode | null) => void
  openModal?: (id: string) => void
  __prksFolderDashboardState?: {
    vueOwned?: boolean
    container?: HTMLElement | null
    recentlyAddedLoading?: boolean
    recentlyAddedWorks?: unknown[] | null
    switchTab?: (tab: string) => void | Promise<void>
    [key: string]: unknown
  }
  __prksRecentlyAddedDirty?: boolean
  __prksFolderLibraryBrandHomeReset?: boolean
  prksSync?: { subscribe?: (listener: () => void) => () => void }
  prksCreateConceptFlow?: (
    initialName?: string,
    ownerOpts?: {
      tabId?: string
      generation?: number
      isCurrent?: (generation: number) => boolean
    },
  ) => Promise<unknown>
  prksResearchIndexRowHtml?: (opts: {
    href: string
    title: string
    kind?: string
    icon?: string
    meta?: string[]
  }) => string
  prksResearchSectionHeadHtml?: (
    title: string,
    opts?: {
      headingId?: string
      actionId?: string
      actionLabel?: string
      actionRole?: string
      count?: number | string | null
      sub?: string
    },
  ) => string
  prksResearchMarkdownHtml?: (text: string) => string
  prksEscapeHtml?: (value: unknown) => string
  prksPageHeaderIconHtml?: (name: string) => string
  prksIcon?: (
    name: string,
    options?: { size?: string | number; className?: string },
  ) => string
  prksPaintScopeHost?: (
    rootEl: ParentNode | HTMLElement | null,
    options: {
      shown?: number
      total?: number
      filter?: string
      label?: string
    },
  ) => void
  prksRelSummaryHtml?: (options: { parts?: Array<string | null | undefined> }) => string
  prksPromptTextDialog?: (opts: PrksPromptTextOptions) => Promise<string | null>
  prksConfirmDestructive?: (opts: PrksConfirmDestructiveOptions) => Promise<boolean>
  prksAlertDialog?: (opts: PrksAlertOptions) => Promise<void>
  prksNavigate?: (hash: string, options?: PrksNavigateOptions) => void
  prksGraphFocusHash?: (kind: string, id: string) => string
  prksTabContextOwnsEntityRoute?: (
    ctx: unknown,
    generation: number,
    entityType: string,
    entityId: string,
    routeName: string,
  ) => boolean
  prksPrepareArgumentEdit?: (argumentId: string) => Promise<void>
  prksCommitArgumentEditorDraft?: (
    argumentId: string,
    draft: {
      name: string
      kind: 'argument' | 'stance'
      main_text: string
      targets: Array<{
        type: 'position' | 'argument'
        id: string
        name: string
        kind: string
        verdict_id: string
      }>
      sources: Array<{ work_id: string; work_title: string; pages: string }>
    },
  ) => Promise<{ id?: string } | null>
  prksDeleteArgumentDurably?: (argumentId: string) => Promise<unknown>
  prksArgumentSaveMessage?: (err: unknown, fallback: string) => string
  deleteArgument?: (id: string) => Promise<{ status?: string } | unknown>
  createArgument?: (body: {
    name: string
    kind?: string
    main_text?: string
    targets?: Array<{ type: string; id: string; verdict_id: string }>
    sources?: Array<{ work_id: string; pages?: string }>
  }) => Promise<{ id?: string } | null>
  fetchArguments?: () => Promise<Array<{ id?: string; name?: string; kind?: string }>>
  fetchPositions?: () => Promise<Array<{ id?: string; name?: string }>>
  fetchWorks?: (options?: {
    signal?: AbortSignal
    errorOwner?: object
  }) => Promise<Array<{ id?: string; title?: string }>>
  prksConsumeApiError?: (owner: object) => { message?: string } | null
  prksInferWorkSourceKind?: (work: unknown) => string
  prksOfflineRuntimeState?: () => string
  /** True when the offline runtime refused an online-only write and showed `message`. */
  prksOfflineGuardMutation?: (message: string) => boolean
  prksOfflineRuntimeSubscribe?: (listener: (state: string) => void) => () => void
  prksAlertMessage?: (message: string, title?: string) => Promise<void> | void
  prksApplyPlaylistOfflineState?: (container: ParentNode | null) => void
  prksOpenNewPlaylistModalFromPlaylistsPage?: (owner?: object) => void
  prksReloadPlaylistDetail?: (ctx: unknown, playlistId: string) => Promise<unknown>
  updatePlaylist?: (
    playlistId: string,
    fields: Record<string, string>,
    options?: object,
  ) => Promise<unknown>
  reorderPlaylist?: (playlistId: string, workIds: string[]) => Promise<unknown>
  addWorkToPlaylist?: (playlistId: string, workId: string) => Promise<unknown>
  removeWorkFromPlaylist?: (playlistId: string, workId: string) => Promise<unknown>
  deletePlaylistFromDetail?: (
    ctx: unknown,
    playlist: { id: string; title?: string; items?: ReadonlyArray<{ id: string }> },
    generation?: number,
  ) => Promise<void>
  renderPlaylistDetail?: (ctx: unknown, playlist: unknown, container: HTMLElement) => void
  updatePanelContent?: (tab?: string) => void
  prksSaveWorkFieldDurably?: (
    workId: string,
    field: string,
    value: string,
    options?: { label?: string },
  ) => Promise<{ code?: string; error?: string } | null>
  prksOpenResearchPicker?: (opts: {
    title: string
    items: () => Array<{ id: string; label: string; kind: string; pickType: string; haystack: string }>
    onPick: (id: string, pickType?: string) => void
  }) => void
  createPosition?: (body: { name: string; description?: string }) => Promise<{ id?: string } | null>
  createConcept?: (body: { name: string }) => Promise<{ id?: string } | null>
  updateConcept?: (id: string, body: { name?: string; description?: string }) => Promise<unknown>
  deleteConcept?: (id: string) => Promise<unknown>
  putConceptAliases?: (id: string, aliases: string[]) => Promise<unknown>
  putConceptParents?: (id: string, parentIds: string[]) => Promise<unknown>
}
