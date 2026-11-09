/**
 * Mount the Processing queue (`#/processing-files` on a PostgreSQL Library).
 *
 * The coordinator (frontend/js/app.js) asks for this feature instead of the
 * path-based inbox when the session names PostgreSQL content. It passes the
 * folder catalogue and the document types the cards pick from; the page reads
 * the queue itself. The nav badge follows the ready items the page holds.
 */
import { createVNode } from 'vue'
import {
  presentRouteSurface,
  registerEarlyRoutePresenter,
  registerRouteWindowBridge,
  type RouteSurfaceOwner,
} from '../../route-surface/lifecycle'
import { listProcessingItems } from '../../api/processing-items'
import type { LeaveAssessResult, LeaveProbe } from '../../lifecycle/tab-leave'
import type { ProcessingRouteInstance } from '../processing/route'
import {
  ProcessingQueue,
  browserProcessingQueueApi,
  type ProcessingQueueApi,
  type ProcessingQueueOption,
} from './queue'
import ProcessingQueueRoute from './ProcessingQueueRoute.vue'

export const PROCESSING_QUEUE_FEATURE = 'processing-queue'
/** The badge counts at most this many ready items (one page read). */
export const PROCESSING_QUEUE_BADGE_LIMIT = 500

export interface ProcessingQueueOwner extends RouteSurfaceOwner {
  isCurrent?: (generation: number) => boolean
}

export interface ProcessingQueuePresentInput {
  owner: ProcessingQueueOwner
  host: HTMLElement
  folders: unknown
  docTypes: unknown
  generation?: number
  shell?: boolean
  api?: ProcessingQueueApi
}

interface QueueRoot {
  prksSetProcessingAttentionCount?: (count: number | null) => void
  prksProcessingQueueCount?: (options?: { signal?: AbortSignal }) => Promise<number>
  prksTabLeave?: { registerProbe?: (probe: LeaveProbe) => void }
  prksConfirmUnsavedRouteLeave?: (options: { title: string; message: string }) => Promise<boolean> | boolean
}

/** The queue each owner (TabContext) shows; a card clears its dirty mark when it unmounts. */
const queues = new WeakMap<object, ProcessingQueue>()

/** The leave probe's order, after pdf-sync 10, person-profile 20 and work-metadata 30. */
export const PROCESSING_QUEUE_LEAVE_ORDER = 40

/**
 * `prksTabLeave` probe: an owner whose queue has a draft with unsaved edits
 * asks before its route is replaced, parked or destroyed, with the shared
 * unsaved-changes confirmation. Any other owner is not this probe's concern.
 */
export async function assessProcessingQueueLeave(ctx: unknown): Promise<LeaveAssessResult> {
  const queue = ctx && typeof ctx === 'object' ? queues.get(ctx) : undefined
  if (!queue || queue.state.dirty.size === 0) return null
  const blocked = { status: 'rejected-unsaved-edit', feature: PROCESSING_QUEUE_FEATURE } as const
  const confirm = (globalThis as QueueRoot).prksConfirmUnsavedRouteLeave
  if (typeof confirm !== 'function') return blocked
  const ok = await confirm({
    title: 'Discard draft changes?',
    message: 'Your unsaved Processing draft changes will be discarded.',
  })
  return ok ? null : blocked
}

function text(value: unknown): string {
  return typeof value === 'string' ? value : typeof value === 'number' ? String(value) : ''
}

/** Folder rows as the coordinator's folder read returns them. */
export function folderOptions(value: unknown): ProcessingQueueOption[] {
  if (!Array.isArray(value)) return []
  const options: ProcessingQueueOption[] = []
  for (const row of value) {
    if (!row || typeof row !== 'object') continue
    const record = row as Record<string, unknown>
    const id = text(record.id)
    if (!id) continue
    options.push({ value: id, label: text(record.title) || text(record.name) || '(Untitled folder)' })
  }
  return options.sort((a, b) => a.label.localeCompare(b.label))
}

/** `PRKS_DOC_TYPES` rows (`{ value, label }`). */
export function docTypeOptions(value: unknown): ProcessingQueueOption[] {
  if (!Array.isArray(value)) return []
  const options: ProcessingQueueOption[] = []
  for (const row of value) {
    if (!row || typeof row !== 'object') continue
    const record = row as Record<string, unknown>
    const option = text(record.value)
    if (option) options.push({ value: option, label: text(record.label) || option })
  }
  return options
}

function setBadge(count: number): void {
  const set = (globalThis as QueueRoot).prksSetProcessingAttentionCount
  if (typeof set === 'function') set(count)
}

export function presentProcessingQueue(input: ProcessingQueuePresentInput): void {
  if (!input || !input.owner || typeof input.owner !== 'object') return
  const owner = input.owner
  const folders = folderOptions(input.folders)
  const docTypes = docTypeOptions(input.docTypes)
  const route: Omit<ProcessingRouteInstance, 'generation'> & { generation?: number } = {
    name: 'processing-files',
    canonicalHash: '#/processing-files',
    params: {},
    ownsMainShell: input.shell !== false,
    generation: input.generation,
  }
  presentRouteSurface({
    owner,
    host: input.host,
    route,
    render: (generation) => {
      const queue = new ProcessingQueue(input.api ?? browserProcessingQueueApi, (count) => {
        // A page this owner no longer shows does not move the badge.
        if (typeof owner.isCurrent === 'function' && !owner.isCurrent(generation)) return
        setBadge(count)
      })
      queues.set(owner, queue)
      return createVNode(ProcessingQueueRoute, { queue, folders, docTypes })
    },
  })
}

function isQueueRequest(value: unknown): value is Omit<ProcessingQueuePresentInput, 'host'> & { feature: string } {
  if (!value || typeof value !== 'object') return false
  const record = value as { feature?: unknown; owner?: unknown }
  return record.feature === PROCESSING_QUEUE_FEATURE && !!record.owner
}

/** The ready items the badge shows: one bounded page read. */
export async function processingQueueCount(options: { signal?: AbortSignal } = {}): Promise<number> {
  const page = await listProcessingItems({ limit: PROCESSING_QUEUE_BADGE_LIMIT, signal: options.signal })
  return page.items.length
}

export function registerProcessingQueueBridge(target: Window = window): void {
  registerRouteWindowBridge(target)
  const root = target as unknown as QueueRoot
  root.prksProcessingQueueCount = processingQueueCount
  root.prksTabLeave?.registerProbe?.({
    id: PROCESSING_QUEUE_FEATURE,
    order: PROCESSING_QUEUE_LEAVE_ORDER,
    assess: assessProcessingQueueLeave,
  })
  registerEarlyRoutePresenter(
    PROCESSING_QUEUE_FEATURE,
    (request, host) => {
      if (!isQueueRequest(request)) return false
      presentProcessingQueue({
        owner: request.owner,
        host,
        folders: request.folders,
        docTypes: request.docTypes,
        generation: request.generation,
        shell: request.shell,
      })
      return true
    },
    target,
  )
}
