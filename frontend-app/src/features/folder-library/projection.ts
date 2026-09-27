import type { FolderLibraryAvailability, FolderLibraryItem } from './types'

/**
 * One-way Folder Library projection for one owner.
 * Built after the coordinator resolves effective folders:index rows.
 */
export interface FolderLibraryProjection {
  readonly availability: FolderLibraryAvailability
  readonly folders: readonly FolderLibraryItem[]
  readonly offlineCached: boolean
  readonly generation: number
}

function asString(value: unknown): string {
  return value == null ? '' : String(value)
}

function asNullableId(value: unknown): string | null {
  if (value == null || value === '') return null
  return String(value)
}

/** Narrow an already-effective folder row into the typed projection item. */
export function acceptFolderLibraryItem(value: unknown): FolderLibraryItem | null {
  if (!value || typeof value !== 'object') return null
  const row = value as Record<string, unknown>
  const id = asString(row.id).trim()
  if (!id) return null
  const workRaw = row.work_count
  const workCount =
    workRaw == null || workRaw === ''
      ? null
      : Number.isFinite(Number(workRaw))
        ? Number(workRaw)
        : null
  return {
    id,
    title: asString(row.title).trim() || 'Folder',
    parent_id: asNullableId(row.parent_id),
    child_count: Number(row.child_count) || 0,
    work_count: workCount,
    description: asString(row.description),
  }
}

export function acceptFolderLibraryItems(value: unknown): FolderLibraryItem[] {
  if (!Array.isArray(value)) return []
  return value
    .map((item) => acceptFolderLibraryItem(item))
    .filter((item): item is FolderLibraryItem => item != null)
}

export function buildFolderLibraryProjection(input: {
  availability?: FolderLibraryAvailability
  folders?: unknown
  offlineCached?: boolean
  generation: number
}): FolderLibraryProjection {
  const availability = input.availability === 'unavailable' ? 'unavailable' : 'ready'
  return {
    availability,
    folders: availability === 'ready' ? acceptFolderLibraryItems(input.folders) : [],
    offlineCached: !!input.offlineCached,
    generation: input.generation,
  }
}

/** Catalog glance parts from the effective hierarchy only (no warm fetches). */
export function folderLibraryCatalogGlanceParts(
  folders: readonly FolderLibraryItem[],
): Array<string | null> {
  const list = Array.isArray(folders) ? folders : []
  const folderCount = list.length
  let workSum = 0
  let workSumKnown = true
  for (let i = 0; i < list.length; i += 1) {
    const n = list[i]?.work_count
    if (n == null || !Number.isFinite(Number(n))) {
      workSumKnown = false
      break
    }
    workSum += Number(n)
  }
  return [
    folderCount + (folderCount === 1 ? ' folder' : ' folders'),
    workSumKnown
      ? workSum + (workSum === 1 ? ' file in folders' : ' files in folders')
      : null,
  ]
}
