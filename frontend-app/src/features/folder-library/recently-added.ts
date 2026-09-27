import type { FolderLibraryItem, RecentlyAddedWork } from './types'

/** Concise scan-friendly date for Recently Added cards, e.g. "Sep 5, 2026". */
export function recentlyAddedDateLabel(createdAt: unknown): string {
  if (!createdAt) return ''
  const d = new Date(String(createdAt))
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}

export function recentlyAddedWorkMatchesQuery(
  work: RecentlyAddedWork | null | undefined,
  query: string,
  foldersById: Map<string, FolderLibraryItem>,
): boolean {
  const q = String(query || '')
    .trim()
    .toLowerCase()
  if (!q || !work) return true
  const hay: unknown[] = [
    work.title,
    work.author_text,
    work.linked_authors,
    work.primary_author,
    work.primary_editor,
    work.year,
    work.published_date,
    work.publisher,
    work.status,
    work.doc_type,
  ]
  const fid = work.folder_id != null ? String(work.folder_id).trim() : ''
  if (fid && foldersById.has(fid)) {
    hay.push(foldersById.get(fid)?.title)
  }
  return hay.some((v) => v != null && String(v).toLowerCase().includes(q))
}

export function foldersByIdMap(
  folders: readonly FolderLibraryItem[],
): Map<string, FolderLibraryItem> {
  return new Map(folders.map((f) => [f.id, f]))
}

/**
 * Apply the effective-projection overlay when available, then local filter.
 * Acknowledged rows stay what the server said; pending field edits overlay only.
 */
export function effectiveRecentlyAddedRows(
  works: readonly RecentlyAddedWork[] | null | undefined,
): RecentlyAddedWork[] {
  const acknowledged = Array.isArray(works) ? [...works] : []
  const fn = window.prksEffectiveProjectionRows
  if (typeof fn !== 'function') return acknowledged
  const next = fn(acknowledged, 'recently-added')
  return Array.isArray(next) ? (next as RecentlyAddedWork[]) : acknowledged
}

export function filterRecentlyAddedWorks(
  works: readonly RecentlyAddedWork[] | null | undefined,
  query: string,
  folders: readonly FolderLibraryItem[],
): RecentlyAddedWork[] {
  const all = effectiveRecentlyAddedRows(works)
  const q = String(query || '').trim()
  if (!q) return all
  const byId = foldersByIdMap(folders)
  return all.filter((w) => recentlyAddedWorkMatchesQuery(w, q, byId))
}
