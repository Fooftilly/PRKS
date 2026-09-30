/**
 * Vue-facing reads and intents for the Work PDF runtime.
 *
 * `initPdfViewerForWork` owns viewer integration, annotation-persistence
 * orchestration, and the pdf TabContext resource.
 * `pdf-work-runtime.js` owns per-tab liveness. The durable annotation
 * write stays in the existing annotation state module and local store.
 * This module does not store PDF state and does not perform those writes.
 */

export interface WorkPdfWorkRef {
  id?: unknown
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
  }
  target.prksReadWorkPdf = readWorkPdf
  target.prksIntentMountWorkPdf = intentMountWorkPdf
  target.prksIntentFlushWorkPdf = intentFlushWorkPdf
  target.prksIntentResizeWorkPdf = intentResizeWorkPdf
  target.prksWorkPdfLeaveNeedsConfirm = workPdfLeaveNeedsConfirm
}
