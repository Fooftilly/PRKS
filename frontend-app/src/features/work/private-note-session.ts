/**
 * Mounts the Work Reminders card into the shared right panel.
 * The unsaved session lives on the owning TabContext. The durable note API stays in ui.js.
 */
import { h, render } from 'vue'
import WorkPrivateNotes from './WorkPrivateNotes.vue'

export interface WorkPrivateNoteOwner {
  tabId?: unknown
  generation?: unknown
  destroyed?: boolean
  ui?: {
    workPrivateNoteSession?: { draftText?: string; retired?: boolean; dirty?: boolean } | null
    workMetaDraft?: unknown
  } | null
  getEntity?: (type: string) => { id?: unknown; private_notes?: unknown } | null
}

interface PrivateNoteGlobals {
  prksEnsureWorkPrivateNoteSession?: (
    owner: WorkPrivateNoteOwner,
    workId: string,
    initialText: string,
  ) => { draftText: string; retired?: boolean } | null
  prksPrivateNotesTextForEntity?: (entityType: string, entityId: string, serverText: string) => string
}

interface MountedNotes {
  anchor: HTMLElement
  workId: string
  tabId: string
}

let mounted: MountedNotes | null = null

function panelElement(): HTMLElement | null {
  return document.getElementById('panel-content')
}

export function dismissWorkPrivateNotes(): void {
  const anchor = mounted?.anchor
  mounted = null
  if (anchor && anchor.isConnected) render(null, anchor)
}

export function presentWorkPrivateNotes(ctx: WorkPrivateNoteOwner, workId: string): boolean {
  if (!ctx || ctx.destroyed || !ctx.ui) return false
  const panel = panelElement()
  const anchor = panel?.querySelector('[data-prks-role="work-private-notes-anchor"]')
  if (!panel || !(anchor instanceof HTMLElement)) return false
  if ((panel.dataset.prksOwnerTabId || '') !== String(ctx.tabId || '')) return false
  const ownerGeneration = panel.dataset.prksOwnerGeneration || ''
  if (ownerGeneration && ownerGeneration !== String(ctx.generation ?? '')) return false
  const id = String(workId)
  const work = ctx.getEntity ? ctx.getEntity('work') : null
  if (!work || String(work.id || '') !== id) return false
  const root = window as unknown as PrivateNoteGlobals
  const server = work.private_notes == null ? '' : String(work.private_notes)
  const painted = typeof root.prksPrivateNotesTextForEntity === 'function'
    ? root.prksPrivateNotesTextForEntity('work', id, server)
    : server
  const session = root.prksEnsureWorkPrivateNoteSession?.(ctx, id, painted) || null
  const initialText = session && !session.retired ? String(session.draftText || '') : painted
  if (mounted && (mounted.anchor !== anchor || mounted.workId !== id || mounted.tabId !== String(ctx.tabId || ''))) {
    dismissWorkPrivateNotes()
  }
  render(h(WorkPrivateNotes, { workId: id, initialText, owner: ctx }), anchor)
  mounted = { anchor, workId: id, tabId: String(ctx.tabId || '') }
  return true
}

export function registerWorkPrivateNotesBridge(root: Window & typeof globalThis): void {
  const target = root as Window & {
    prksVuePresentWorkPrivateNotes?: (ctx: WorkPrivateNoteOwner, workId: string) => boolean
    prksVueDismissWorkPrivateNotes?: () => void
  }
  target.prksVuePresentWorkPrivateNotes = (ctx, workId) => presentWorkPrivateNotes(ctx, workId)
  target.prksVueDismissWorkPrivateNotes = () => dismissWorkPrivateNotes()
}

export function resetWorkPrivateNotesForTests(): void {
  dismissWorkPrivateNotes()
}
