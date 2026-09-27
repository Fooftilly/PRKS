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

interface FolderLibraryPresentRequest {
  owner: object
  host: HTMLElement
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
  prksInitLazyWorkThumbs?: (root: ParentNode | null) => void
  prksReleaseLazyWorkThumbs?: (root: ParentNode | null) => void
  prksReleaseWorkThumbPreview?: (root: ParentNode | null) => void
  prksRefreshIcons?: (root: ParentNode | Document | null) => void
  prksVuePresentProgress?: (input: ProgressPresentRequest) => void
  prksVueDismissProgress?: (owner: object) => void
  prksVuePresentConceptsIndex?: (input: ConceptsIndexPresentRequest) => void
  prksVuePresentConceptDetail?: (input: ConceptDetailPresentRequest) => void
  prksVueDismissConcepts?: (owner: object) => void
  prksVuePresentFolderLibrary?: (input: FolderLibraryPresentRequest) => void
  prksVueDismissFolderLibrary?: (owner: object) => void
  prksPresentVueFolderLibrary?: (
    ctx: object | null,
    contentDiv: HTMLElement,
    detail: {
      availability?: 'ready' | 'unavailable'
      folders?: unknown
      offlineCached?: boolean
      generation?: number
    },
  ) => void
  prksOpenFolderModalFromLibrarySearch?: (query?: string) => void
  openModal?: (id: string) => void
  prksToggleFolderNode?: (folderId: string) => void
  prksToggleAllFolderNodes?: () => void
  prksFolderLibraryTreeInnerHtml?: (folders: unknown, filterQuery?: string) => string
  prksFolderTreeHasCollapsibleNodes?: (folders: unknown) => boolean
  prksFolderLibraryExpandToggleLabel?: (folders: unknown) => string
  prksFolderLibraryExpandToggleInnerHtml?: () => string
  prksFolderTreeAllCollapsed?: (folders: unknown) => boolean
  prksCollectFolderLibraryGlanceExtras?: () => Promise<
    Array<string | { text: string; href?: string }>
  >
  prksPageSummaryHtml?: (opts: {
    parts?: Array<string | { text: string; href?: string } | null | undefined>
    ariaLabel?: string
  }) => string
  prksTagSearchIconHtml?: () => string
  prksEffectiveProjectionRows?: (rows: unknown[], domain: string) => unknown[]
  prksOfflineDomainGeneration?: (domain: string) => unknown
  prksPendingWorkMetadataGeneration?: () => unknown
  prksRefreshPendingWorkMetadata?: () => Promise<void>
  prksOfflineRecentlyAddedFetch?: () => Promise<{ source?: string } | null>
  prksResolveOfflineRecentlyAdded?: (offline: unknown) => unknown[] | null
  __prksRecentlyAddedDirty?: boolean
  __prksFolderDashboardState?: {
    folders?: unknown
    container?: HTMLElement | null
    activeTab?: string
    filterQuery?: string
    recentlyAddedFilterQuery?: string
    recentlyAddedWorks?: unknown
    recentlyAddedGeneration?: unknown
    recentlyAddedPendingGeneration?: unknown
    recentlyAddedCached?: boolean
    recentlyAddedLoading?: boolean
  } | null
  prksSync?: { subscribe?: (fn: () => void) => () => void }
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
  createConcept?: (body: { name: string }) => Promise<{ id?: string } | null>
  updateConcept?: (id: string, body: { name?: string; description?: string }) => Promise<unknown>
  deleteConcept?: (id: string) => Promise<unknown>
  putConceptAliases?: (id: string, aliases: string[]) => Promise<unknown>
  putConceptParents?: (id: string, parentIds: string[]) => Promise<unknown>
}
