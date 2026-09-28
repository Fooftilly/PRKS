/// <reference types="vite/client" />

interface ProgressPresentRequest {
  owner: object
  host: HTMLElement
  status: string | null | undefined
  rows: unknown
  offlineCached?: boolean
  generation?: number
  shell?: boolean
}

interface ConceptsIndexPresentRequest {
  owner: object
  host: HTMLElement
  availability?: 'ready' | 'unavailable'
  items?: unknown
  generation?: number
  shell?: boolean
}

interface ConceptDetailPresentRequest {
  owner: object
  host: HTMLElement
  availability?: 'ready' | 'unavailable' | 'not-found'
  concept?: unknown
  conceptId?: string
  generation?: number
  shell?: boolean
}

interface PositionsIndexPresentRequest {
  owner: object
  host: HTMLElement
  availability?: 'ready' | 'unavailable'
  items?: unknown
  generation?: number
  shell?: boolean
}

interface ArgumentsIndexPresentRequest {
  owner: object
  host: HTMLElement
  availability?: 'ready' | 'unavailable'
  kind?: string
  items?: unknown
  generation?: number
  shell?: boolean
}

interface ArgumentDetailPresentRequest {
  owner: object
  host: HTMLElement
  availability?: 'ready' | 'unavailable' | 'not-found'
  argument?: unknown
  argumentId?: string
  generation?: number
  shell?: boolean
}

interface PlaylistsIndexPresentRequest {
  owner: object
  host: HTMLElement
  availability?: 'ready' | 'unavailable'
  items?: unknown
  generation?: number
  shell?: boolean
}

interface PeopleIndexPresentRequest {
  owner: object
  host: HTMLElement
  availability?: 'ready' | 'unavailable' | 'unknown-role'
  items?: unknown
  roleFilter?: string
  unknownRole?: boolean
  generation?: number
  shell?: boolean
}

interface PersonDetailPresentRequest {
  owner: object
  host: HTMLElement
  availability?: 'ready' | 'unavailable' | 'not-found'
  person?: unknown
  personId?: string
  editing?: boolean
  editorActive?: boolean
  worksEditing?: boolean
  offlineCached?: boolean
  generation?: number
  shell?: boolean
}

interface PlaylistDetailPresentRequest {
  owner: object
  host: HTMLElement
  availability?: 'ready' | 'unavailable' | 'not-found'
  playlist?: unknown
  playlistId?: string
  editing?: boolean
  renaming?: unknown
  generation?: number
  shell?: boolean
}

interface PositionDetailPresentRequest {
  owner: object
  host: HTMLElement
  availability?: 'ready' | 'unavailable' | 'not-found'
  position?: unknown
  positionId?: string
  generation?: number
  shell?: boolean
}

interface FolderLibraryPresentRequest {
  owner: object
  host: HTMLElement
  contentRoot?: HTMLElement | null
  availability?: 'ready' | 'unavailable'
  folders?: unknown
  offlineCached?: boolean
  generation?: number
  shell?: boolean
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
      abstract_excerpt?: unknown
      abstract?: unknown
    },
    options: { subtitle?: string; suppressThumbnail?: boolean },
  ) => string
  prksWorkBrowseModeToggleHtml?: (hiddenId?: string) => string
  prksWorkBrowseCollectionClass?: (extraClass?: string) => string
  prksBindWorkBrowseMode?: (root: ParentNode | null) => void
  prksTagSearchIconHtml?: () => string
  prksInitLazyWorkThumbs?: (root: ParentNode | null) => void
  prksRefreshIcons?: (root: ParentNode | Document | null) => void
  prksVuePresentProgress?: (input: ProgressPresentRequest) => void
  prksVueDismissProgress?: (owner: object) => void
  prksVuePresentConceptsIndex?: (input: ConceptsIndexPresentRequest) => void
  prksVuePresentConceptDetail?: (input: ConceptDetailPresentRequest) => void
  prksVueDismissConcepts?: (owner: object) => void
  prksVuePresentPositionsIndex?: (input: PositionsIndexPresentRequest) => void
  prksVuePresentPositionDetail?: (input: PositionDetailPresentRequest) => void
  prksVueDismissPositions?: (owner: object) => void
  prksVuePresentArgumentsIndex?: (input: ArgumentsIndexPresentRequest) => void
  prksVuePresentArgumentDetail?: (input: ArgumentDetailPresentRequest) => void
  prksVueDismissArguments?: (owner: object) => void
  prksVuePresentPlaylistsIndex?: (input: PlaylistsIndexPresentRequest) => void
  prksVuePresentPlaylistDetail?: (input: PlaylistDetailPresentRequest) => void
  prksVueDismissPlaylists?: (owner: object) => void
  prksVuePresentPeopleIndex?: (input: PeopleIndexPresentRequest) => void
  prksVuePresentPersonDetail?: (input: PersonDetailPresentRequest) => void
  prksVueDismissPeople?: (owner: object) => void
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
  ) => Promise<{ ok: boolean; message: string }>
  deletePerson?: (ctx?: unknown, generation?: number) => Promise<void>
  prksTogglePersonWorksEdit?: (ctx?: unknown) => void
  prksRemoveWorkRoleLink?: (button: HTMLButtonElement) => Promise<void>
  prksPersonViewInGraph?: () => void
  prksVuePresentFolderLibrary?: (input: FolderLibraryPresentRequest) => void
  prksVueDismissFolderLibrary?: (owner: object) => void
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
    options?: { size?: string; className?: string },
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
