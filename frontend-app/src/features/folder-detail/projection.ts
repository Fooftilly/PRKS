import type { FolderDetailAvailability, FolderDetailRecord } from './types'

/** One-way Folder detail projection for one owner. */
export interface FolderDetailProjection {
  readonly availability: FolderDetailAvailability
  readonly folder: FolderDetailRecord | null
  readonly folderId: string
  /** Card rows after `prksEffectiveFolderDetailWorks`. Raw `folder.works` stays for delete eligibility. */
  readonly effectiveWorks: readonly unknown[]
  readonly offlineCached: boolean
  /** Folder→Folder in-place selection. The hierarchy writer uses this for selection-only fill. */
  readonly preserveWorkspace: boolean
  readonly generation: number
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  return value as Record<string, unknown>
}

export function acceptFolderDetail(value: unknown): FolderDetailRecord | null {
  const row = asRecord(value)
  if (!row) return null
  const id = String(row.id ?? '').trim()
  if (!id) return null
  return {
    id,
    title: row.title == null ? '' : String(row.title),
    description: row.description == null ? '' : String(row.description),
    children: Array.isArray(row.children) ? row.children : [],
    works: Array.isArray(row.works) ? row.works : [],
    source: row,
  }
}

/**
 * Embedded Work summaries. The helper in `folders.js` applies the shared
 * metadata/role overlay. This projection does not read the durable queue.
 */
export function effectiveFolderWorks(folder: FolderDetailRecord | null): unknown[] {
  if (!folder) return []
  const fn = window.prksEffectiveFolderDetailWorks
  if (typeof fn !== 'function') return [...folder.works]
  const overlaid = fn(folder.source)
  return Array.isArray(overlaid) ? overlaid : [...folder.works]
}

export function buildFolderDetailProjection(input: {
  availability?: FolderDetailAvailability
  folder?: unknown
  folderId?: string
  offlineCached?: boolean
  preserveWorkspace?: boolean
  generation: number
}): FolderDetailProjection {
  const requested = input.availability
  const folder = requested === 'unavailable' ? null : acceptFolderDetail(input.folder)
  const availability: FolderDetailAvailability =
    requested === 'unavailable' ? 'unavailable' : folder ? 'ready' : 'not-found'
  const folderId =
    folder?.id || (typeof input.folderId === 'string' ? input.folderId : '')
  return {
    availability,
    folder: availability === 'ready' ? folder : null,
    folderId,
    effectiveWorks: availability === 'ready' ? effectiveFolderWorks(folder) : [],
    offlineCached: input.offlineCached === true,
    preserveWorkspace: input.preserveWorkspace === true,
    generation: input.generation,
  }
}
