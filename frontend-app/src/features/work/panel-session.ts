/**
 * One shared right-panel read surface. It follows the dataset owner.
 * A late refresh from another TabContext does not paint.
 */
import { h, render } from 'vue'
import WorkPanelRead from './WorkPanelRead.vue'
import {
  projectWorkPanelRead,
  refreshWorkPanelDisplay,
  type WorkPanelReadInput,
  type WorkPanelReadModel,
} from './panel-read'

export const WORK_PANEL_READ_ANCHOR = 'work-panel-read-anchor'
const PENDING_KEY = '__prksWorkPanelReadRequest'

const SLOT_ATTRS = [
  'work-panel-summary',
  'work-panel-identity',
  'work-panel-dates',
  'work-bib-rows',
  'work-panel-source',
  'work-people-read',
  'work-tags-read',
  'work-folder-read',
  'work-playlist-read',
] as const

export interface WorkPanelReadRequest extends WorkPanelReadInput {
  readonly effectiveWork?: Record<string, unknown> | null
}

export interface WorkPanelReadRefresh {
  readonly ownerTabId: string
  readonly ownerGeneration: number
  readonly workId: string
  readonly effectiveWork?: Record<string, unknown> | null
  readonly people?: readonly unknown[] | null
  readonly tags?: readonly unknown[] | null
  readonly publishedDisplay?: string
  readonly docType?: WorkPanelReadInput['docType']
  readonly statusIcon?: string
}

interface MountedRead {
  anchor: HTMLElement
  panel: HTMLElement
  model: WorkPanelReadModel
}

interface OwnerLike {
  readonly tabId?: unknown
  readonly generation?: unknown
  destroyed?: boolean
  getEntity?(type: string): unknown
}

let mounted: MountedRead | null = null

function panelElement(): HTMLElement | null {
  return document.getElementById('panel-content')
}

function sameOwner(panel: HTMLElement, ownerTabId: string, ownerGeneration: number): boolean {
  return (
    (panel.dataset.prksOwnerTabId || '') === String(ownerTabId) &&
    (panel.dataset.prksOwnerGeneration || '') === String(ownerGeneration)
  )
}

function slotMap(panel: HTMLElement): WorkPanelReadSlots {
  const has = (role: string) => !!panel.querySelector(`[data-prks-role="${role}"]`)
  return {
    summary: has('work-panel-summary'),
    identity: has('work-panel-identity'),
    dates: has('work-panel-dates'),
    bib: has('work-bib-rows'),
    source: has('work-panel-source'),
    people: has('work-people-read'),
    tags: has('work-tags-read'),
    folder: has('work-folder-read'),
    playlist: has('work-playlist-read'),
  }
}

export interface WorkPanelReadSlots {
  summary: boolean
  identity: boolean
  dates: boolean
  bib: boolean
  source: boolean
  people: boolean
  tags: boolean
  folder: boolean
  playlist: boolean
}

function clearSlots(panel: HTMLElement): void {
  for (const role of SLOT_ATTRS) {
    const node = panel.querySelector(`[data-prks-role="${role}"]`)
    if (node) node.replaceChildren()
  }
}

function paint(panel: HTMLElement, anchor: HTMLElement, model: WorkPanelReadModel): void {
  render(h(WorkPanelRead, { model, slots: slotMap(panel) }), anchor)
  const refreshIcons = (window as Window & { prksRefreshIcons?: (root: ParentNode) => void }).prksRefreshIcons
  if (typeof refreshIcons === 'function') refreshIcons(panel)
}

export function dismissWorkPanelRead(): void {
  if (!mounted) return
  const anchor = mounted.anchor
  mounted = null
  if (anchor.isConnected) render(null, anchor)
}

export function workPanelReadOwns(ctx: OwnerLike | null | undefined): boolean {
  if (!mounted || !ctx || ctx.destroyed) return false
  const panel = panelElement()
  if (!panel || panel !== mounted.panel || !mounted.anchor.isConnected) return false
  if (!sameOwner(panel, mounted.model.ownerTabId, mounted.model.ownerGeneration)) return false
  if (String(ctx.tabId ?? '') !== mounted.model.ownerTabId) return false
  if (String(ctx.generation ?? '') !== String(mounted.model.ownerGeneration)) return false
  const live = ctx.getEntity ? ctx.getEntity('work') : null
  const liveId =
    live && typeof live === 'object' && 'id' in live ? String((live as { id?: unknown }).id ?? '') : ''
  return liveId === mounted.model.workId
}

export function presentWorkPanelRead(request: WorkPanelReadRequest | null | undefined): boolean {
  if (!request || !request.workId) return false
  const panel = panelElement()
  if (!panel || !sameOwner(panel, String(request.ownerTabId), request.ownerGeneration)) return false
  const anchor = panel.querySelector(`[data-prks-role="${WORK_PANEL_READ_ANCHOR}"]`)
  if (!(anchor instanceof HTMLElement)) return false
  dismissWorkPanelRead()
  clearSlots(panel)
  const model = projectWorkPanelRead(request)
  paint(panel, anchor, model)
  mounted = { anchor, panel, model }
  return true
}

export function refreshMountedWorkPanelRead(patch: WorkPanelReadRefresh | null | undefined): boolean {
  if (!mounted || !patch) return false
  if (String(patch.ownerTabId) !== mounted.model.ownerTabId) return false
  if (String(patch.ownerGeneration) !== String(mounted.model.ownerGeneration)) return false
  if (String(patch.workId) !== mounted.model.workId) return false
  const panel = panelElement()
  if (!panel || panel !== mounted.panel || !mounted.anchor.isConnected) return false
  if (!sameOwner(panel, mounted.model.ownerTabId, mounted.model.ownerGeneration)) return false
  const model = refreshWorkPanelDisplay(mounted.model, patch)
  paint(panel, mounted.anchor, model)
  mounted = { anchor: mounted.anchor, panel, model }
  return true
}

export function resetWorkPanelReadForTests(): void {
  dismissWorkPanelRead()
}

interface PendingPanel extends HTMLElement {
  [PENDING_KEY]?: WorkPanelReadRequest
}

export function registerWorkPanelReadBridge(root: Window & typeof globalThis): void {
  const target = root as Window & {
    prksVuePresentWorkPanelRead?: (request: WorkPanelReadRequest) => boolean
    prksVueRefreshWorkPanelRead?: (patch: WorkPanelReadRefresh) => boolean
    prksVueDismissWorkPanelRead?: () => void
    prksWorkPanelReadOwns?: (ctx: OwnerLike | null | undefined) => boolean
  }
  target.prksVuePresentWorkPanelRead = presentWorkPanelRead
  target.prksVueRefreshWorkPanelRead = refreshMountedWorkPanelRead
  target.prksVueDismissWorkPanelRead = dismissWorkPanelRead
  target.prksWorkPanelReadOwns = workPanelReadOwns
  const panel = document.getElementById('panel-content') as PendingPanel | null
  const pending = panel?.[PENDING_KEY]
  if (pending) {
    delete panel[PENDING_KEY]
    presentWorkPanelRead(pending)
  }
}
