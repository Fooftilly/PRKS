/**
 * Mounts the Work tile shell for one TabContext.
 * Video HTML comes from renderVideoViewerPane. The PDF host is filled by
 * initPdfViewerForWork. Research Notes mount into the anchor afterwards.
 */
import { h, render } from 'vue'
import WorkMainSurface from './WorkMainSurface.vue'

export type WorkMainSurfaceKind = 'pdf' | 'video' | 'empty'

export interface WorkMainSurfaceModel {
  workId: string
  generation?: number | null
  kind: WorkMainSurfaceKind
  hasFile: boolean
  showHeader: boolean
  title: string
  docTypeHtml: string
  relSummaryHtml: string
  viewerHtml: string
  editorRegionId: string
}

export interface WorkMainSurfaceOwner {
  tabId?: unknown
  destroyed?: boolean
  root?: ParentNode | null
  ui?: object | null
  generation?: number
  isCurrent?: (generation: number) => boolean
  getEntity?: (type: string) => { id?: unknown } | null
  getResource?: (name: string) => unknown
  setResource?: (name: string, value: unknown, disposer?: () => void) => unknown
  registerCleanup?: (fn: () => void) => (() => void) | void
}

interface MountedSurface {
  root: HTMLElement
  workId: string
  tabId: string
}

const mountedByTab = new Map<string, MountedSurface>()
const shellCleanupArmed = new WeakMap<object, true>()

function unmountRecord(record: MountedSurface | undefined): void {
  if (record) render(null, record.root)
}

function armWorkMainSurfaceCleanup(ctx: WorkMainSurfaceOwner): void {
  if (typeof ctx.registerCleanup !== 'function' || shellCleanupArmed.has(ctx)) return
  shellCleanupArmed.set(ctx, true)
  ctx.registerCleanup(() => {
    shellCleanupArmed.delete(ctx)
    dismissWorkMainSurface(ctx)
  })
}

export function dismissWorkMainSurface(ctx?: { tabId?: unknown }): void {
  if (ctx && ctx.tabId != null && ctx.tabId !== '') {
    const tabId = String(ctx.tabId)
    const record = mountedByTab.get(tabId)
    mountedByTab.delete(tabId)
    unmountRecord(record)
    return
  }
  for (const record of mountedByTab.values()) unmountRecord(record)
  mountedByTab.clear()
}

export function presentWorkMainSurface(
  ctx: WorkMainSurfaceOwner,
  model: WorkMainSurfaceModel,
): boolean {
  if (!ctx || ctx.destroyed || !ctx.ui || !ctx.root) return false
  if (!(ctx.root instanceof HTMLElement)) return false
  const workId = String(model && model.workId != null ? model.workId : '')
  if (!workId) return false
  if (
    typeof model.generation === 'number' &&
    typeof ctx.isCurrent === 'function' &&
    !ctx.isCurrent(model.generation)
  ) {
    return false
  }
  const live = ctx.getEntity ? ctx.getEntity('work') : null
  if (!live || String(live.id || '') !== workId) return false
  const tabId = String(ctx.tabId || '')
  const root = ctx.root
  const previous = mountedByTab.get(tabId)
  if (previous && previous.root !== root) unmountRecord(previous)
  /* Child resources (pdf, workNotes) dispose in insertion order during
   * clearAllResources. The shell unmount is a cleanup so it runs after them
   * and the PDF host and Research Notes anchor are still connected. */
  armWorkMainSurfaceCleanup(ctx)
  const kind = model.kind === 'pdf' || model.kind === 'video' ? model.kind : 'empty'
  // The route paints "Loading view..." into this root before the tile exists.
  // Vue's first mount appends; it does not remove that placeholder. A warm
  // resume then parks the leftover with the PDF host.
  if (root.querySelector(':scope > .prks-route-loading')) {
    render(null, root)
    root.replaceChildren()
    root.removeAttribute('aria-busy')
  }
  render(
    h(WorkMainSurface, {
      showHeader: !!model.showHeader,
      title: String(model.title || 'Document'),
      docTypeHtml: String(model.docTypeHtml || ''),
      relSummaryHtml: String(model.relSummaryHtml || ''),
      workId,
      kind,
      hasFile: !!model.hasFile,
      viewerHtml: String(model.viewerHtml || ''),
      editorRegionId: String(model.editorRegionId || 'work-notes-editor-region'),
    }),
    root,
  )
  mountedByTab.set(tabId, { root, workId, tabId })
  return true
}

export function registerWorkMainSurfaceBridge(root: Window & typeof globalThis): void {
  const target = root as Window & {
    prksVuePresentWorkMainSurface?: (
      ctx: WorkMainSurfaceOwner,
      model: WorkMainSurfaceModel,
    ) => boolean
    prksVueDismissWorkMainSurface?: (ctx?: { tabId?: unknown }) => void
  }
  target.prksVuePresentWorkMainSurface = (ctx, model) => presentWorkMainSurface(ctx, model)
  target.prksVueDismissWorkMainSurface = (ctx) => dismissWorkMainSurface(ctx)
}

export function resetWorkMainSurfaceForTests(): void {
  dismissWorkMainSurface()
}
