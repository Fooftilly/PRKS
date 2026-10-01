/**
 * Paints the viewer-owned annotation drawer for one TabContext.
 * The pdf runtime owns the session. This module only mounts the overlay
 * and forwards intents.
 */
import { h, reactive, render } from 'vue'
import {
  intentCloseWorkPdfAnnotationDrawer,
  intentCopyWorkPdfAnnotationLink,
  intentDeleteWorkPdfAnnotation,
  intentEditWorkPdfAnnotationComment,
  intentJumpWorkPdfAnnotation,
  intentResizeWorkPdfAnnotationDrawer,
  intentSetWorkPdfAnnotationDrawerPinned,
  readWorkPdfAnnotationDrawer,
  type WorkPdfAnnotationDrawerCapture,
} from './pdf-adapter'
import WorkPdfAnnotationDrawer, { type AnnotationDrawerView } from './WorkPdfAnnotationDrawer.vue'

export interface AnnotationDrawerOwner {
  tabId?: unknown
  destroyed?: boolean
  root?: ParentNode | null
  generation?: number
  isCurrent?: (generation: number) => boolean
  getResource?: (name: string) => unknown
  query?: (selector: string) => Element | null
}

interface DrawerSession {
  host: HTMLElement
  state: AnnotationDrawerView
}

const sessions = new Map<string, DrawerSession>()
const owners = new Map<string, AnnotationDrawerOwner>()

function emptyView(): AnnotationDrawerView {
  return {
    open: false,
    epoch: 0,
    viewerToken: 0,
    selectedId: '',
    status: '',
    published: false,
    items: [],
    generation: null,
    pinned: false,
    width: 352,
    minWidth: 240,
    maxWidth: 480,
    defaultWidth: 352,
    placement: 'closed',
    pinEnabled: false,
  }
}

function tabIdOf(ctx: { tabId?: unknown } | null | undefined): string {
  if (!ctx || ctx.tabId == null || ctx.tabId === '') return ''
  return String(ctx.tabId)
}

function ensureHost(ctx: AnnotationDrawerOwner): HTMLElement | null {
  const root = ctx.root
  if (!(root instanceof HTMLElement)) return null
  const existing = root.querySelector('[data-prks-role="pdf-annotation-drawer-host"]')
  if (existing instanceof HTMLElement) return existing
  const pane = root.querySelector('.work-pdf-pane')
  const parent = pane instanceof HTMLElement ? pane : root
  const host = document.createElement('div')
  host.setAttribute('data-prks-role', 'pdf-annotation-drawer-host')
  parent.appendChild(host)
  return host
}

function unmount(session: DrawerSession | undefined): void {
  if (session) render(null, session.host)
}

export function dismissWorkPdfAnnotationDrawer(ctx?: { tabId?: unknown }): void {
  if (ctx && ctx.tabId != null && ctx.tabId !== '') {
    const tabId = String(ctx.tabId)
    const session = sessions.get(tabId)
    sessions.delete(tabId)
    owners.delete(tabId)
    unmount(session)
    return
  }
  for (const session of sessions.values()) unmount(session)
  sessions.clear()
  owners.clear()
}

function applyRead(state: AnnotationDrawerView, ctx: AnnotationDrawerOwner): void {
  const read = readWorkPdfAnnotationDrawer(ctx)
  state.open = read.open
  state.epoch = read.epoch
  state.viewerToken = read.viewerToken
  state.selectedId = read.selectedId
  state.status = read.status
  state.published = read.published
  state.items = read.items.map((item) => ({ ...item, metadataLabels: item.metadataLabels.slice() }))
  state.generation = typeof ctx.generation === 'number' ? ctx.generation : null
  state.pinned = read.pinned
  state.width = read.width
  state.minWidth = read.minWidth
  state.maxWidth = read.maxWidth
  state.defaultWidth = read.defaultWidth
  state.placement = read.placement
  state.pinEnabled = read.pinEnabled
}

export function syncWorkPdfAnnotationDrawer(ctx: AnnotationDrawerOwner | null | undefined): boolean {
  if (!ctx || ctx.destroyed) return false
  const tabId = tabIdOf(ctx)
  if (!tabId || !(ctx.root instanceof HTMLElement)) return false
  if (typeof ctx.isCurrent === 'function' && typeof ctx.generation === 'number' && !ctx.isCurrent(ctx.generation)) {
    return false
  }
  const host = ensureHost(ctx)
  if (!host) return false
  owners.set(tabId, ctx)
  let session = sessions.get(tabId)
  if (!session || session.host !== host) {
    unmount(session)
    const state = reactive(emptyView())
    session = { host, state }
    sessions.set(tabId, session)
    const ticket = (): WorkPdfAnnotationDrawerCapture => ({
      generation: typeof state.generation === 'number' ? state.generation : Number.NaN,
      epoch: state.epoch,
      viewerToken: state.viewerToken,
    })
    render(
      h(WorkPdfAnnotationDrawer, {
        state,
        tabId,
        onClose: () => {
          const owner = owners.get(tabId)
          if (owner) intentCloseWorkPdfAnnotationDrawer(owner, ticket())
        },
        onJump: (annId: string) => {
          const owner = owners.get(tabId)
          if (owner) intentJumpWorkPdfAnnotation(owner, annId, ticket())
        },
        onEdit: (annId: string) => {
          const owner = owners.get(tabId)
          if (owner) intentEditWorkPdfAnnotationComment(owner, annId, ticket())
        },
        onDelete: (annId: string) => {
          const owner = owners.get(tabId)
          if (!owner) return Promise.resolve(false)
          return intentDeleteWorkPdfAnnotation(owner, annId, ticket())
        },
        onCopy: (annId: string) => {
          const owner = owners.get(tabId)
          if (!owner) return Promise.resolve(false)
          return intentCopyWorkPdfAnnotationLink(owner, annId, ticket())
        },
        onPin: (pinned: boolean) => {
          const owner = owners.get(tabId)
          if (owner) intentSetWorkPdfAnnotationDrawerPinned(owner, pinned, ticket())
        },
        onResize: (width: number, _ticket, options) => {
          const owner = owners.get(tabId)
          if (owner) intentResizeWorkPdfAnnotationDrawer(owner, width, ticket(), options)
        },
      }),
      host,
    )
  }
  applyRead(session.state, ctx)
  return true
}

export function registerWorkPdfAnnotationDrawerBridge(root: Window & typeof globalThis): void {
  const target = root as Window & {
    prksVueSyncWorkPdfAnnotationDrawer?: (ctx: AnnotationDrawerOwner) => boolean
    prksVueDismissWorkPdfAnnotationDrawer?: (ctx?: { tabId?: unknown }) => void
  }
  target.prksVueSyncWorkPdfAnnotationDrawer = (ctx) => syncWorkPdfAnnotationDrawer(ctx)
  target.prksVueDismissWorkPdfAnnotationDrawer = (ctx) => dismissWorkPdfAnnotationDrawer(ctx)
}

export function resetWorkPdfAnnotationDrawerForTests(): void {
  dismissWorkPdfAnnotationDrawer()
}
