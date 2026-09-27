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
  createPosition?: (body: { name: string; description?: string }) => Promise<{ id?: string } | null>
  createConcept?: (body: { name: string }) => Promise<{ id?: string } | null>
  updateConcept?: (id: string, body: { name?: string; description?: string }) => Promise<unknown>
  deleteConcept?: (id: string) => Promise<unknown>
  putConceptAliases?: (id: string, aliases: string[]) => Promise<unknown>
  putConceptParents?: (id: string, parentIds: string[]) => Promise<unknown>
}
