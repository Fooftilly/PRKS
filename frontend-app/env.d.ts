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
  prksRefreshIcons?: (root: ParentNode | Document | null) => void
  prksVuePresentProgress?: (input: ProgressPresentRequest) => void
  prksVueDismissProgress?: (owner: object) => void
  prksVuePresentConceptsIndex?: (input: ConceptsIndexPresentRequest) => void
  prksVuePresentConceptDetail?: (input: ConceptDetailPresentRequest) => void
  prksVueDismissConcepts?: (owner: object) => void
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
