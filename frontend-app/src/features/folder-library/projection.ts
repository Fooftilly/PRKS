import type { FolderLibraryAvailability, FolderRow } from './types'

/** One-way Folder Library projection for one owner. */
export interface FolderLibraryProjection {
  readonly availability: FolderLibraryAvailability
  readonly folders: readonly FolderRow[]
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

/** Narrow an already-effective folder row. */
export function acceptFolderRow(value: unknown): FolderRow | null {
  if (!value || typeof value !== 'object') return null
  const row = value as Record<string, unknown>
  const id = asString(row.id).trim()
  if (!id) return null
  return {
    id,
    title: asString(row.title).trim() || 'Folder',
    parent_id: asNullableId(row.parent_id),
    work_count: Number(row.work_count) || 0,
    child_count: Number(row.child_count) || 0,
  }
}

export function acceptFolderRows(value: unknown): FolderRow[] {
  if (!Array.isArray(value)) return []
  return value.map((row) => acceptFolderRow(row)).filter((row): row is FolderRow => row != null)
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
    folders: availability === 'ready' ? acceptFolderRows(input.folders) : [],
    offlineCached: input.offlineCached === true,
    generation: input.generation,
  }
}
