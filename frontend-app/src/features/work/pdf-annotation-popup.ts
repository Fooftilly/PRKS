/**
 * Paints the anchored annotation comment popup for one TabContext.
 * The pdf runtime owns the session. This module only mounts the surface
 * and forwards intents.
 */
import { h, reactive, render } from 'vue'
import {
  intentCloseWorkPdfAnnotationPopup,
  intentDeleteWorkPdfAnnotationPopup,
  intentSaveWorkPdfAnnotationComment,
  readWorkPdfAnnotationPopup,
  type WorkPdfAnnotationPopupCapture,
} from './pdf-adapter'
import WorkPdfAnnotationPopup, { type AnnotationPopupView } from './WorkPdfAnnotationPopup.vue'

export interface AnnotationPopupOwner {
  tabId?: unknown
  destroyed?: boolean
  root?: ParentNode | null
  generation?: number
  isCurrent?: (generation: number) => boolean
  getResource?: (name: string) => unknown
  query?: (selector: string) => Element | null
}

interface PopupSession {
  host: HTMLElement
  state: AnnotationPopupView
}

const sessions = new Map<string, PopupSession>()
const owners = new Map<string, AnnotationPopupOwner>()

function emptyView(): AnnotationPopupView {
  return {
    open: false,
    annId: '',
    epoch: 0,
    comment: '',
    meta: '',
    pageIndex: null,
    generation: null,
    deletable: false,
  }
}

function tabIdOf(ctx: { tabId?: unknown } | null | undefined): string {
  if (!ctx || ctx.tabId == null || ctx.tabId === '') return ''
  return String(ctx.tabId)
}

function ensureHost(ctx: AnnotationPopupOwner): HTMLElement | null {
  const root = ctx.root
  if (!(root instanceof HTMLElement)) return null
  const existing = root.querySelector('[data-prks-role="pdf-annotation-popup-host"]')
  if (existing instanceof HTMLElement) return existing
  const pane = root.querySelector('.work-pdf-pane')
  const parent = pane instanceof HTMLElement ? pane : root
  const host = document.createElement('div')
  host.setAttribute('data-prks-role', 'pdf-annotation-popup-host')
  parent.appendChild(host)
  return host
}

function anchorFor(ctx: AnnotationPopupOwner, annId: string): HTMLElement | null {
  const root = ctx.root
  if (!(root instanceof HTMLElement) || !annId) return null
  const nodes = root.querySelectorAll('[data-prks-role="pdf-annotation-anchor"]')
  for (const node of nodes) {
    if (!(node instanceof HTMLElement)) continue
    if (node.getAttribute('data-prks-annotation-id') === annId) return node
  }
  return null
}

function boundaryFor(ctx: AnnotationPopupOwner): HTMLElement | null {
  const root = ctx.root
  if (!(root instanceof HTMLElement)) return null
  const pane = root.querySelector('.work-pdf-pane')
  return pane instanceof HTMLElement ? pane : null
}

function unmount(session: PopupSession | undefined): void {
  if (session) render(null, session.host)
}

export function dismissWorkPdfAnnotationPopup(ctx?: { tabId?: unknown }): void {
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

function applyRead(state: AnnotationPopupView, ctx: AnnotationPopupOwner): void {
  const read = readWorkPdfAnnotationPopup(ctx)
  state.open = read.open
  state.annId = read.annId
  state.epoch = read.epoch
  state.comment = read.comment
  state.meta = read.meta
  state.pageIndex = read.pageIndex
  state.generation = typeof ctx.generation === 'number' ? ctx.generation : null
  state.deletable = read.deletable
}

export function syncWorkPdfAnnotationPopup(ctx: AnnotationPopupOwner | null | undefined): boolean {
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
    render(
      h(WorkPdfAnnotationPopup, {
        state,
        tabId,
        anchor: () => {
          const owner = owners.get(tabId)
          return owner ? anchorFor(owner, state.annId) : null
        },
        boundary: () => {
          const owner = owners.get(tabId)
          return owner ? boundaryFor(owner) : null
        },
        onSave: (text: string, ticket: WorkPdfAnnotationPopupCapture) => {
          const owner = owners.get(tabId)
          if (owner) intentSaveWorkPdfAnnotationComment(owner, text, ticket)
        },
        onClose: (ticket: WorkPdfAnnotationPopupCapture) => {
          const owner = owners.get(tabId)
          if (owner) intentCloseWorkPdfAnnotationPopup(owner, ticket)
        },
        onDelete: (ticket: WorkPdfAnnotationPopupCapture) => {
          const owner = owners.get(tabId)
          if (owner) intentDeleteWorkPdfAnnotationPopup(owner, ticket)
        },
      }),
      host,
    )
  }
  applyRead(session.state, ctx)
  return true
}

export function registerWorkPdfAnnotationPopupBridge(root: Window & typeof globalThis): void {
  const target = root as Window & {
    prksVueSyncWorkPdfAnnotationPopup?: (ctx: AnnotationPopupOwner) => boolean
    prksVueDismissWorkPdfAnnotationPopup?: (ctx?: { tabId?: unknown }) => void
  }
  target.prksVueSyncWorkPdfAnnotationPopup = (ctx) => syncWorkPdfAnnotationPopup(ctx)
  target.prksVueDismissWorkPdfAnnotationPopup = (ctx) => dismissWorkPdfAnnotationPopup(ctx)
}

export function resetWorkPdfAnnotationPopupForTests(): void {
  dismissWorkPdfAnnotationPopup()
}
