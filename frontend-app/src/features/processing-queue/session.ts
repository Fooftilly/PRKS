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
  ;(target as unknown as QueueRoot).prksProcessingQueueCount = processingQueueCount
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
