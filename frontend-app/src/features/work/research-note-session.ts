/**
 * Mounts the Research Notes pane shell into the owning Work tile.
 * EasyMDE, pickers, and the durable note API stay in works.js.
 */
import { h, render } from 'vue'
import WorkResearchNotes from './WorkResearchNotes.vue'

export interface WorkResearchNoteOwner {
  tabId?: unknown
  destroyed?: boolean
  root?: ParentNode | null
  ui?: object | null
  domId?: (localName: string) => string
  getEntity?: (type: string) => { id?: unknown } | null
}

interface MountedNotes {
  anchor: HTMLElement
  workId: string
  tabId: string
}

const mountedByTab = new Map<string, MountedNotes>()

function unmountRecord(record: MountedNotes | undefined): void {
  if (record && record.anchor.isConnected) render(null, record.anchor)
}

export function dismissWorkResearchNotes(ctx?: { tabId?: unknown }): void {
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

export function presentWorkResearchNotes(
  ctx: WorkResearchNoteOwner,
  work: { id?: unknown },
  initialText: string,
): boolean {
  if (!ctx || ctx.destroyed || !ctx.ui || !ctx.root) return false
  const anchor = ctx.root.querySelector('[data-prks-role="work-research-notes-anchor"]')
  if (!(anchor instanceof HTMLElement)) return false
  const workId = String(work && work.id != null ? work.id : '')
  if (!workId) return false
  const live = ctx.getEntity ? ctx.getEntity('work') : null
  if (!live || String(live.id || '') !== workId) return false
  const tabId = String(ctx.tabId || '')
  const previous = mountedByTab.get(tabId)
  const sameMount = !!(
    previous &&
    previous.anchor === anchor &&
    previous.workId === workId &&
    previous.tabId === tabId
  )
  if (sameMount) return true
  /* Main and Secondary each keep a pane. Dismissing the other tile would
   * drop its EasyMDE host while that Work is still open. */
  if (previous) {
    mountedByTab.delete(tabId)
    unmountRecord(previous)
  }
  /* Vue render appends. The shell pane is already in the anchor so the tile
   * is usable before this module loads; leaving it would duplicate EasyMDE. */
  anchor.replaceChildren()
  const editorRegionId = typeof ctx.domId === 'function'
    ? ctx.domId('work-notes-editor-region')
    : 'work-notes-editor-region'
  render(
    h(WorkResearchNotes, {
      initialText: String(initialText == null ? '' : initialText),
      editorRegionId,
    }),
    anchor,
  )
  mountedByTab.set(tabId, { anchor, workId, tabId })
  return true
}

export function registerWorkResearchNotesBridge(root: Window & typeof globalThis): void {
  const target = root as Window & {
    prksVuePresentWorkResearchNotes?: (
      ctx: WorkResearchNoteOwner,
      work: { id?: unknown },
      initialText: string,
    ) => boolean
    prksVueDismissWorkResearchNotes?: () => void
  }
  target.prksVuePresentWorkResearchNotes = (ctx, work, initialText) =>
    presentWorkResearchNotes(ctx, work, initialText)
  target.prksVueDismissWorkResearchNotes = () => dismissWorkResearchNotes()
}

export function resetWorkResearchNotesForTests(): void {
  dismissWorkResearchNotes()
}
