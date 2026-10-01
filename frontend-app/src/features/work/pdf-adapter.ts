/**
 * Vue-facing reads and intents for the Work PDF runtime.
 *
 * `initPdfViewerForWork` owns viewer integration, annotation-persistence
 * orchestration, and the pdf TabContext resource.
 * `pdf-work-runtime.js` owns per-tab liveness. The durable annotation
 * write stays in the existing annotation state module and local store.
 * This module does not store PDF state and does not perform those writes.
 */

/** A mount Work. `id` is required so a typed caller cannot omit the owner. */
export interface WorkPdfWorkRef {
  id: unknown
  file_path?: unknown
}

export interface WorkPdfRuntime {
  workId?: unknown
  _destroyed?: boolean
  viewer?: { resize?: () => void } | null
  viewerSetupToken?: number
  hasPendingSync?: () => boolean
  flushLastPage?: () => void
  resize?: () => void
  readSearch?: () => WorkPdfSearchRead
  openSearch?: () => boolean
  closeSearch?: () => boolean
  setSearchQuery?: (query: string) => boolean
  searchNext?: () => boolean
  searchPrevious?: () => boolean
  readAnnotationPopup?: () => WorkPdfAnnotationPopupRead
  annotationPopupStill?: (ticket: WorkPdfAnnotationPopupCapture) => boolean
  readAnnotationDrawer?: () => WorkPdfAnnotationDrawerRead
  annotationDrawerStill?: (ticket: WorkPdfAnnotationDrawerCapture) => boolean
  closeAnnotationDrawer?: () => boolean
  setAnnotationDrawerPinned?: (pinned: boolean) => boolean
  setAnnotationDrawerWidth?: (width: number, options?: { persist?: boolean }) => boolean
}

export interface WorkPdfOwner {
  destroyed?: boolean
  root?: ParentNode | null
  isCurrent?: (generation: number) => boolean
  getEntity?: (type: string) => { id?: unknown } | null
  getResource?: (name: string) => unknown
  query?: (selector: string) => Element | null
}

/** Owner identity captured when a mount is requested, checked again before forward. */
export interface WorkPdfMountCapture {
  generation: number
  workId: string
}

/** Generation captured with a search intent. Rechecked before the runtime call. */
export interface WorkPdfSearchCapture {
  generation: number
}

export type WorkPdfSearchStatus = 'idle' | 'pending' | 'ready' | 'empty'

export interface WorkPdfSearchRead {
  open: boolean
  query: string
  total: number
  activeIndex: number
  status: WorkPdfSearchStatus
  matchCountLabel: string
}

/** Generation, annotation, and epoch captured with a popup intent. */
export interface WorkPdfAnnotationPopupCapture {
  generation: number
  annId: string
  epoch: number
  directId?: string
  pageIndex?: number | null
}

export interface WorkPdfAnnotationPopupRead {
  open: boolean
  annId: string
  pageIndex: number | null
  comment: string
  meta: string
  epoch: number
  deletable: boolean
}

export interface WorkPdfAnnotationDrawerItem {
  id: string
  index: number
  text: string
  comment: string
  pageLabel: string
  pageIndex: number | null
  wikiLink: string
  metadataLabels: string[]
}

export type WorkPdfAnnotationDrawerPlacement = 'closed' | 'overlay' | 'pinned' | 'sheet'

export interface WorkPdfAnnotationDrawerRead {
  open: boolean
  epoch: number
  viewerToken: number
  selectedId: string
  status: string
  published: boolean
  items: WorkPdfAnnotationDrawerItem[]
  pinned: boolean
  width: number
  layoutWidth: number
  minWidth: number
  maxWidth: number
  interactionMax: number
  defaultWidth: number
  placement: WorkPdfAnnotationDrawerPlacement
  pinEnabled: boolean
}

/** Generation, epoch, and viewer token captured with a drawer intent. */
export interface WorkPdfAnnotationDrawerCapture {
  generation: number
  epoch: number
  viewerToken: number
}

export interface WorkPdfOrchestration {
  initPdfViewerForWork: (ctx: WorkPdfOwner, work: WorkPdfWorkRef) => void
}

export interface WorkPdfRead {
  present: boolean
  workId: string
  destroyed: boolean
  hasPendingSync: boolean
  viewerToken: number | null
  paneHost: boolean
}

type PdfWindow = Window & typeof globalThis & {
  prksHasPendingWorkAnnotationSync?: (ctx?: WorkPdfOwner) => boolean
}

const PDF_HOST = '[data-prks-role="pdf-viewer"]'

function runtimeOf(ctx: WorkPdfOwner | null | undefined): WorkPdfRuntime | null {
  if (!ctx || ctx.destroyed || typeof ctx.getResource !== 'function') return null
  const value = ctx.getResource('pdf')
  if (!value || typeof value !== 'object') return null
  return value as WorkPdfRuntime
}

function paneHost(ctx: WorkPdfOwner | null | undefined): boolean {
  if (!ctx || typeof ctx.query !== 'function') return false
  return !!ctx.query(PDF_HOST)
}

export function readWorkPdf(ctx: WorkPdfOwner | null | undefined): WorkPdfRead {
  const runtime = runtimeOf(ctx)
  const host = paneHost(ctx)
  if (!runtime) {
    return {
      present: false,
      workId: '',
      destroyed: false,
      hasPendingSync: false,
      viewerToken: null,
      paneHost: host,
    }
  }
  return {
    present: true,
    workId: runtime.workId == null ? '' : String(runtime.workId),
    destroyed: runtime._destroyed === true,
    hasPendingSync: typeof runtime.hasPendingSync === 'function' ? !!runtime.hasPendingSync() : false,
    viewerToken: typeof runtime.viewerSetupToken === 'number' ? runtime.viewerSetupToken : null,
    paneHost: host,
  }
}

function requiredWorkId(value: unknown): string {
  if (value == null || value === '') return ''
  return String(value)
}

function liveWorkId(ctx: WorkPdfOwner): string {
  if (typeof ctx.getEntity !== 'function') return ''
  const live = ctx.getEntity('work')
  if (!live || typeof live !== 'object') return ''
  return requiredWorkId(live.id)
}

/**
 * Forwards to the existing orchestration. Does not install the pdf resource.
 * The captured generation and Work id are rechecked immediately before that call.
 */
export function intentMountWorkPdf(
  ctx: WorkPdfOwner | null | undefined,
  work: WorkPdfWorkRef | null | undefined,
  orchestration: WorkPdfOrchestration | null | undefined,
  captured: WorkPdfMountCapture | null | undefined,
): boolean {
  if (!ctx || ctx.destroyed || !work || work.file_path == null || work.file_path === '') return false
  if (!orchestration || typeof orchestration.initPdfViewerForWork !== 'function') return false
  if (!captured || typeof captured.generation !== 'number') return false
  const requestedId = requiredWorkId(work.id)
  const expectedWorkId = requiredWorkId(captured.workId)
  if (!requestedId || !expectedWorkId || requestedId !== expectedWorkId) return false
  if (typeof ctx.isCurrent !== 'function' || !ctx.isCurrent(captured.generation)) return false
  const liveId = liveWorkId(ctx)
  if (!liveId || liveId !== expectedWorkId || requestedId !== liveId) return false
  orchestration.initPdfViewerForWork(ctx, work)
  return true
}

export function intentFlushWorkPdf(ctx: WorkPdfOwner | null | undefined): boolean {
  const runtime = runtimeOf(ctx)
  if (!runtime || runtime._destroyed || typeof runtime.flushLastPage !== 'function') return false
  runtime.flushLastPage()
  return true
}

const EMPTY_SEARCH: WorkPdfSearchRead = {
  open: false,
  query: '',
  total: 0,
  activeIndex: -1,
  status: 'idle',
  matchCountLabel: '',
}

function searchRuntime(
  ctx: WorkPdfOwner | null | undefined,
  captured: WorkPdfSearchCapture | null | undefined,
): WorkPdfRuntime | null {
  const runtime = runtimeOf(ctx)
  if (!runtime || runtime._destroyed) return null
  if (!ctx || typeof ctx.isCurrent !== 'function') return null
  if (!captured || typeof captured.generation !== 'number' || !ctx.isCurrent(captured.generation)) return null
  return runtime
}

/** Reflects the pdf runtime's search session. Does not keep a copy. */
export function readWorkPdfSearch(ctx: WorkPdfOwner | null | undefined): WorkPdfSearchRead {
  const runtime = runtimeOf(ctx)
  if (!runtime || runtime._destroyed || typeof runtime.readSearch !== 'function') return { ...EMPTY_SEARCH }
  const read = runtime.readSearch()
  if (!read || typeof read !== 'object') return { ...EMPTY_SEARCH }
  const status = read.status
  const known: WorkPdfSearchStatus[] = ['idle', 'pending', 'ready', 'empty']
  return {
    open: !!read.open,
    query: read.query == null ? '' : String(read.query),
    total: typeof read.total === 'number' && read.total > 0 ? read.total : 0,
    activeIndex: typeof read.activeIndex === 'number' ? read.activeIndex : -1,
    status: known.indexOf(status) >= 0 ? status : 'idle',
    matchCountLabel: read.matchCountLabel == null ? '' : String(read.matchCountLabel),
  }
}

export function intentOpenWorkPdfSearch(
  ctx: WorkPdfOwner | null | undefined,
  captured: WorkPdfSearchCapture | null | undefined,
): boolean {
  const runtime = searchRuntime(ctx, captured)
  if (!runtime || typeof runtime.openSearch !== 'function') return false
  return !!runtime.openSearch()
}

export function intentCloseWorkPdfSearch(
  ctx: WorkPdfOwner | null | undefined,
  captured: WorkPdfSearchCapture | null | undefined,
): boolean {
  const runtime = searchRuntime(ctx, captured)
  if (!runtime || typeof runtime.closeSearch !== 'function') return false
  return !!runtime.closeSearch()
}

export function intentSetWorkPdfSearchQuery(
  ctx: WorkPdfOwner | null | undefined,
  query: string,
  captured: WorkPdfSearchCapture | null | undefined,
): boolean {
  const runtime = searchRuntime(ctx, captured)
  if (!runtime || typeof runtime.setSearchQuery !== 'function') return false
  return !!runtime.setSearchQuery(query)
}

export function intentWorkPdfSearchNext(
  ctx: WorkPdfOwner | null | undefined,
  captured: WorkPdfSearchCapture | null | undefined,
): boolean {
  const runtime = searchRuntime(ctx, captured)
  if (!runtime || typeof runtime.searchNext !== 'function') return false
  return !!runtime.searchNext()
}

export function intentWorkPdfSearchPrevious(
  ctx: WorkPdfOwner | null | undefined,
  captured: WorkPdfSearchCapture | null | undefined,
): boolean {
  const runtime = searchRuntime(ctx, captured)
  if (!runtime || typeof runtime.searchPrevious !== 'function') return false
  return !!runtime.searchPrevious()
}

const EMPTY_ANNOTATION_POPUP: WorkPdfAnnotationPopupRead = {
  open: false,
  annId: '',
  pageIndex: null,
  comment: '',
  meta: '',
  epoch: 0,
  deletable: false,
}

/** Reflects the pdf runtime's comment popup. Does not keep a copy. */
export function readWorkPdfAnnotationPopup(
  ctx: WorkPdfOwner | null | undefined,
): WorkPdfAnnotationPopupRead {
  const runtime = runtimeOf(ctx)
  if (!runtime || runtime._destroyed || typeof runtime.readAnnotationPopup !== 'function') {
    return { ...EMPTY_ANNOTATION_POPUP }
  }
  const read = runtime.readAnnotationPopup()
  if (!read || typeof read !== 'object') return { ...EMPTY_ANNOTATION_POPUP }
  const page = read.pageIndex
  return {
    open: !!read.open,
    annId: read.open && read.annId != null ? String(read.annId) : '',
    pageIndex: read.open && typeof page === 'number' && Number.isFinite(page) ? page : null,
    comment: read.open && read.comment != null ? String(read.comment) : '',
    meta: read.open && read.meta != null ? String(read.meta) : '',
    epoch: typeof read.epoch === 'number' ? read.epoch : 0,
    deletable: !!(read.open && read.deletable),
  }
}

function popupRuntime(
  ctx: WorkPdfOwner | null | undefined,
  captured: WorkPdfAnnotationPopupCapture | null | undefined,
): WorkPdfRuntime | null {
  const runtime = runtimeOf(ctx)
  if (!runtime || runtime._destroyed) return null
  if (!ctx || typeof ctx.isCurrent !== 'function') return null
  if (!captured || typeof captured.generation !== 'number' || !ctx.isCurrent(captured.generation)) return null
  if (!captured.annId || typeof captured.epoch !== 'number') return null
  if (typeof runtime.annotationPopupStill !== 'function' || !runtime.annotationPopupStill(captured)) return null
  return runtime
}

type AnnotationPopupWindow = PdfWindow & {
  savePdfAnnotationComment?: (
    ctx: WorkPdfOwner,
    text: string,
    captured: WorkPdfAnnotationPopupCapture,
  ) => void
  closePdfAnnotationEditor?: (
    ctx: WorkPdfOwner,
    options: { annId: string; generation: number; reason: string },
  ) => void
  deletePdfAnnotationFromEditor?: (ctx: WorkPdfOwner, captured: WorkPdfAnnotationPopupCapture) => void
}

export function intentSaveWorkPdfAnnotationComment(
  ctx: WorkPdfOwner | null | undefined,
  text: string,
  captured: WorkPdfAnnotationPopupCapture | null | undefined,
): boolean {
  if (!ctx || !popupRuntime(ctx, captured) || !captured) return false
  const save = (globalThis as AnnotationPopupWindow).savePdfAnnotationComment
  if (typeof save !== 'function') return false
  void save(ctx, text == null ? '' : String(text), captured)
  return true
}

export function intentCloseWorkPdfAnnotationPopup(
  ctx: WorkPdfOwner | null | undefined,
  captured: WorkPdfAnnotationPopupCapture | null | undefined,
): boolean {
  if (!ctx || !popupRuntime(ctx, captured) || !captured) return false
  const close = (globalThis as AnnotationPopupWindow).closePdfAnnotationEditor
  if (typeof close !== 'function') return false
  close(ctx, { annId: captured.annId, generation: captured.generation, reason: 'intent' })
  return true
}

export function intentDeleteWorkPdfAnnotationPopup(
  ctx: WorkPdfOwner | null | undefined,
  captured: WorkPdfAnnotationPopupCapture | null | undefined,
): boolean {
  if (!ctx || !popupRuntime(ctx, captured) || !captured) return false
  const remove = (globalThis as AnnotationPopupWindow).deletePdfAnnotationFromEditor
  if (typeof remove !== 'function') return false
  void remove(ctx, captured)
  return true
}

const EMPTY_ANNOTATION_DRAWER: WorkPdfAnnotationDrawerRead = {
  open: false,
  epoch: 0,
  viewerToken: 0,
  selectedId: '',
  status: '',
  published: false,
  items: [],
  pinned: false,
  width: 352,
  layoutWidth: 352,
  minWidth: 240,
  maxWidth: 480,
  interactionMax: 480,
  defaultWidth: 352,
  placement: 'closed',
  pinEnabled: false,
}

function drawerPlacement(value: unknown, open: boolean): WorkPdfAnnotationDrawerPlacement {
  if (!open) return 'closed'
  if (value === 'pinned' || value === 'sheet' || value === 'overlay') return value
  return 'overlay'
}

function drawerItem(value: unknown): WorkPdfAnnotationDrawerItem | null {
  if (!value || typeof value !== 'object') return null
  const row = value as WorkPdfAnnotationDrawerItem
  if (row.id == null || row.id === '') return null
  const labels = Array.isArray(row.metadataLabels)
    ? row.metadataLabels.filter((label) => typeof label === 'string' && label.trim()).map((label) => label.trim())
    : []
  const page = row.pageIndex
  return {
    id: String(row.id),
    index: typeof row.index === 'number' ? row.index : 0,
    text: row.text != null ? String(row.text) : '',
    comment: row.comment != null ? String(row.comment) : '',
    pageLabel: row.pageLabel != null ? String(row.pageLabel) : '',
    pageIndex: typeof page === 'number' && Number.isFinite(page) ? page : null,
    wikiLink: row.wikiLink != null ? String(row.wikiLink) : '',
    metadataLabels: labels,
  }
}

/** Reflects the pdf runtime's annotation drawer. Does not keep a copy. */
export function readWorkPdfAnnotationDrawer(
  ctx: WorkPdfOwner | null | undefined,
): WorkPdfAnnotationDrawerRead {
  const runtime = runtimeOf(ctx)
  if (!runtime || runtime._destroyed || typeof runtime.readAnnotationDrawer !== 'function') {
    return { ...EMPTY_ANNOTATION_DRAWER, items: [] }
  }
  const read = runtime.readAnnotationDrawer()
  if (!read || typeof read !== 'object') return { ...EMPTY_ANNOTATION_DRAWER, items: [] }
  const items = Array.isArray(read.items)
    ? read.items.map(drawerItem).filter((row): row is WorkPdfAnnotationDrawerItem => row != null)
    : []
  const open = !!read.open
  return {
    open,
    epoch: typeof read.epoch === 'number' ? read.epoch : 0,
    viewerToken: typeof read.viewerToken === 'number' ? read.viewerToken : 0,
    selectedId: open && read.selectedId != null ? String(read.selectedId) : '',
    status: open && read.status != null ? String(read.status) : '',
    published: !!(open && read.published),
    items: open ? items : [],
    pinned: !!read.pinned,
    width: typeof read.width === 'number' && Number.isFinite(read.width) ? read.width : 352,
    layoutWidth:
      typeof read.layoutWidth === 'number' && Number.isFinite(read.layoutWidth)
        ? read.layoutWidth
        : typeof read.width === 'number' && Number.isFinite(read.width)
          ? read.width
          : 352,
    minWidth: typeof read.minWidth === 'number' ? read.minWidth : 240,
    maxWidth: typeof read.maxWidth === 'number' ? read.maxWidth : 480,
    interactionMax:
      typeof read.interactionMax === 'number' && Number.isFinite(read.interactionMax)
        ? read.interactionMax
        : typeof read.maxWidth === 'number'
          ? read.maxWidth
          : 480,
    defaultWidth: typeof read.defaultWidth === 'number' ? read.defaultWidth : 352,
    placement: drawerPlacement(read.placement, open),
    pinEnabled: !!read.pinEnabled,
  }
}

function drawerRuntime(
  ctx: WorkPdfOwner | null | undefined,
  captured: WorkPdfAnnotationDrawerCapture | null | undefined,
): WorkPdfRuntime | null {
  const runtime = runtimeOf(ctx)
  if (!runtime || runtime._destroyed || !ctx || !captured) return null
  if (typeof ctx.isCurrent !== 'function' || typeof captured.generation !== 'number') return null
  if (!ctx.isCurrent(captured.generation)) return null
  if (typeof captured.epoch !== 'number' || typeof captured.viewerToken !== 'number') return null
  if (typeof runtime.annotationDrawerStill !== 'function' || !runtime.annotationDrawerStill(captured)) return null
  return runtime
}

type AnnotationDrawerWindow = PdfWindow & {
  jumpToPdfAnnotationFromDrawer?: (ctx: WorkPdfOwner, annId: string) => void
  openPdfAnnotationEditorFromDrawer?: (ctx: WorkPdfOwner, annId: string) => void
  deletePdfAnnotationFromList?: (
    ctx: WorkPdfOwner,
    annId: string,
  ) => Promise<boolean> | boolean
  copyPdfAnnotationWikiLink?: (ctx: WorkPdfOwner, annId: string) => Promise<boolean> | boolean
  prksCloseAnnotationDrawer?: (ctx: WorkPdfOwner) => boolean
  prksLayoutAnnotationDrawer?: (ctx: WorkPdfOwner) => boolean
  prksPreviewAnnotationDrawerWidth?: (ctx: WorkPdfOwner, width: number) => boolean
  prksRestoreAnnotationDrawerWidth?: (ctx: WorkPdfOwner) => boolean
}

export function intentCloseWorkPdfAnnotationDrawer(
  ctx: WorkPdfOwner | null | undefined,
  captured: WorkPdfAnnotationDrawerCapture | null | undefined,
): boolean {
  if (!drawerRuntime(ctx, captured) || !ctx) return false
  const close = (globalThis as AnnotationDrawerWindow).prksCloseAnnotationDrawer
  if (typeof close !== 'function') return false
  return close(ctx) === true
}

export function intentJumpWorkPdfAnnotation(
  ctx: WorkPdfOwner | null | undefined,
  annId: string,
  captured: WorkPdfAnnotationDrawerCapture | null | undefined,
): boolean {
  if (!drawerRuntime(ctx, captured) || !ctx || !annId) return false
  const jump = (globalThis as AnnotationDrawerWindow).jumpToPdfAnnotationFromDrawer
  if (typeof jump !== 'function') return false
  void jump(ctx, annId)
  return true
}

export function intentEditWorkPdfAnnotationComment(
  ctx: WorkPdfOwner | null | undefined,
  annId: string,
  captured: WorkPdfAnnotationDrawerCapture | null | undefined,
): boolean {
  if (!drawerRuntime(ctx, captured) || !ctx || !annId) return false
  const edit = (globalThis as AnnotationDrawerWindow).openPdfAnnotationEditorFromDrawer
  if (typeof edit !== 'function') return false
  edit(ctx, annId)
  return true
}

export function intentDeleteWorkPdfAnnotation(
  ctx: WorkPdfOwner | null | undefined,
  annId: string,
  captured: WorkPdfAnnotationDrawerCapture | null | undefined,
): Promise<boolean> {
  if (!drawerRuntime(ctx, captured) || !ctx || !annId) return Promise.resolve(false)
  const remove = (globalThis as AnnotationDrawerWindow).deletePdfAnnotationFromList
  if (typeof remove !== 'function') return Promise.resolve(false)
  return Promise.resolve(remove(ctx, annId)).then((ok) => ok === true, () => false)
}

export function intentCopyWorkPdfAnnotationLink(
  ctx: WorkPdfOwner | null | undefined,
  annId: string,
  captured: WorkPdfAnnotationDrawerCapture | null | undefined,
): Promise<boolean> {
  if (!drawerRuntime(ctx, captured) || !ctx || !annId) return Promise.resolve(false)
  const copy = (globalThis as AnnotationDrawerWindow).copyPdfAnnotationWikiLink
  if (typeof copy !== 'function') return Promise.resolve(false)
  return Promise.resolve(copy(ctx, annId)).then((ok) => ok === true, () => false)
}

function layoutAnnotationDrawer(ctx: WorkPdfOwner): void {
  const layout = (globalThis as AnnotationDrawerWindow).prksLayoutAnnotationDrawer
  if (typeof layout === 'function') layout(ctx)
}

export function intentSetWorkPdfAnnotationDrawerPinned(
  ctx: WorkPdfOwner | null | undefined,
  pinned: boolean,
  captured: WorkPdfAnnotationDrawerCapture | null | undefined,
): boolean {
  const runtime = drawerRuntime(ctx, captured)
  if (!runtime || !ctx || typeof runtime.setAnnotationDrawerPinned !== 'function') return false
  if (runtime.setAnnotationDrawerPinned(pinned) !== true) return false
  layoutAnnotationDrawer(ctx)
  return true
}

export function intentResizeWorkPdfAnnotationDrawer(
  ctx: WorkPdfOwner | null | undefined,
  width: number,
  captured: WorkPdfAnnotationDrawerCapture | null | undefined,
  options?: { persist?: boolean; preview?: boolean; cancel?: boolean },
): boolean {
  const runtime = drawerRuntime(ctx, captured)
  if (!runtime || !ctx || typeof runtime.setAnnotationDrawerWidth !== 'function') return false
  if (!Number.isFinite(width)) return false
  const bridge = globalThis as AnnotationDrawerWindow
  if (options && options.cancel) {
    const restore = bridge.prksRestoreAnnotationDrawerWidth
    if (typeof restore !== 'function') return false
    return restore(ctx) === true
  }
  if (options && options.preview) {
    const preview = bridge.prksPreviewAnnotationDrawerWidth
    if (typeof preview !== 'function') return false
    return preview(ctx, width) === true
  }
  if (runtime.setAnnotationDrawerWidth(width, options) !== true) return false
  layoutAnnotationDrawer(ctx)
  return true
}

export function intentResizeWorkPdf(ctx: WorkPdfOwner | null | undefined): boolean {
  const runtime = runtimeOf(ctx)
  if (!runtime || runtime._destroyed || typeof runtime.resize !== 'function') return false
  runtime.resize()
  return true
}

/** The route leave confirm stays `prksHasPendingWorkAnnotationSync`. */
export function workPdfLeaveNeedsConfirm(ctx: WorkPdfOwner | null | undefined): boolean {
  const root = globalThis as PdfWindow
  if (typeof root.prksHasPendingWorkAnnotationSync !== 'function') return false
  return !!root.prksHasPendingWorkAnnotationSync(ctx || undefined)
}

export function registerWorkPdfAdapterBridge(root: Window & typeof globalThis): void {
  const target = root as Window & typeof globalThis & {
    prksReadWorkPdf?: typeof readWorkPdf
    prksIntentMountWorkPdf?: typeof intentMountWorkPdf
    prksIntentFlushWorkPdf?: typeof intentFlushWorkPdf
    prksIntentResizeWorkPdf?: typeof intentResizeWorkPdf
    prksWorkPdfLeaveNeedsConfirm?: typeof workPdfLeaveNeedsConfirm
    prksReadWorkPdfSearch?: typeof readWorkPdfSearch
    prksIntentOpenWorkPdfSearch?: typeof intentOpenWorkPdfSearch
    prksIntentCloseWorkPdfSearch?: typeof intentCloseWorkPdfSearch
    prksIntentSetWorkPdfSearchQuery?: typeof intentSetWorkPdfSearchQuery
    prksIntentWorkPdfSearchNext?: typeof intentWorkPdfSearchNext
    prksIntentWorkPdfSearchPrevious?: typeof intentWorkPdfSearchPrevious
    prksReadWorkPdfAnnotationPopup?: typeof readWorkPdfAnnotationPopup
    prksIntentSaveWorkPdfAnnotationComment?: typeof intentSaveWorkPdfAnnotationComment
    prksIntentCloseWorkPdfAnnotationPopup?: typeof intentCloseWorkPdfAnnotationPopup
    prksIntentDeleteWorkPdfAnnotationPopup?: typeof intentDeleteWorkPdfAnnotationPopup
    prksReadWorkPdfAnnotationDrawer?: typeof readWorkPdfAnnotationDrawer
    prksIntentCloseWorkPdfAnnotationDrawer?: typeof intentCloseWorkPdfAnnotationDrawer
    prksIntentJumpWorkPdfAnnotation?: typeof intentJumpWorkPdfAnnotation
    prksIntentEditWorkPdfAnnotationComment?: typeof intentEditWorkPdfAnnotationComment
    prksIntentDeleteWorkPdfAnnotation?: typeof intentDeleteWorkPdfAnnotation
    prksIntentCopyWorkPdfAnnotationLink?: typeof intentCopyWorkPdfAnnotationLink
    prksIntentSetWorkPdfAnnotationDrawerPinned?: typeof intentSetWorkPdfAnnotationDrawerPinned
    prksIntentResizeWorkPdfAnnotationDrawer?: typeof intentResizeWorkPdfAnnotationDrawer
  }
  target.prksReadWorkPdf = readWorkPdf
  target.prksIntentMountWorkPdf = intentMountWorkPdf
  target.prksIntentFlushWorkPdf = intentFlushWorkPdf
  target.prksIntentResizeWorkPdf = intentResizeWorkPdf
  target.prksWorkPdfLeaveNeedsConfirm = workPdfLeaveNeedsConfirm
  target.prksReadWorkPdfSearch = readWorkPdfSearch
  target.prksIntentOpenWorkPdfSearch = intentOpenWorkPdfSearch
  target.prksIntentCloseWorkPdfSearch = intentCloseWorkPdfSearch
  target.prksIntentSetWorkPdfSearchQuery = intentSetWorkPdfSearchQuery
  target.prksIntentWorkPdfSearchNext = intentWorkPdfSearchNext
  target.prksIntentWorkPdfSearchPrevious = intentWorkPdfSearchPrevious
  target.prksReadWorkPdfAnnotationPopup = readWorkPdfAnnotationPopup
  target.prksIntentSaveWorkPdfAnnotationComment = intentSaveWorkPdfAnnotationComment
  target.prksIntentCloseWorkPdfAnnotationPopup = intentCloseWorkPdfAnnotationPopup
  target.prksIntentDeleteWorkPdfAnnotationPopup = intentDeleteWorkPdfAnnotationPopup
  target.prksReadWorkPdfAnnotationDrawer = readWorkPdfAnnotationDrawer
  target.prksIntentCloseWorkPdfAnnotationDrawer = intentCloseWorkPdfAnnotationDrawer
  target.prksIntentJumpWorkPdfAnnotation = intentJumpWorkPdfAnnotation
  target.prksIntentEditWorkPdfAnnotationComment = intentEditWorkPdfAnnotationComment
  target.prksIntentDeleteWorkPdfAnnotation = intentDeleteWorkPdfAnnotation
  target.prksIntentCopyWorkPdfAnnotationLink = intentCopyWorkPdfAnnotationLink
  target.prksIntentSetWorkPdfAnnotationDrawerPinned = intentSetWorkPdfAnnotationDrawerPinned
  target.prksIntentResizeWorkPdfAnnotationDrawer = intentResizeWorkPdfAnnotationDrawer
}
