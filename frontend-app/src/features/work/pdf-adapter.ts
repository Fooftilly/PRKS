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
}
