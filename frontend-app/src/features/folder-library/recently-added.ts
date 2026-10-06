import type { FolderRow, RecentlyAddedWork } from './types'

/** Concise scan-friendly date, e.g. "Sep 5, 2026". No exact time. */
export function recentlyAddedDateLabel(createdAt: unknown): string {
  if (!createdAt) return ''
  const d = new Date(String(createdAt))
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}

/** The classic effective-Work overlay owns pending metadata; this only asks it. */
export function effectiveRecentlyAddedRows(acknowledged: RecentlyAddedWork[]): RecentlyAddedWork[] {
  const fn = window.prksEffectiveProjectionRows
  if (typeof fn !== 'function') return acknowledged
  const rows = fn(acknowledged, 'recently-added')
  return Array.isArray(rows) ? (rows as RecentlyAddedWork[]) : acknowledged
}

/**
 * Local filter over effective rows: bibliographic fields, status, type, and
 * the title of the Work's folder.
 */
export function recentlyAddedMatchesQuery(
  work: RecentlyAddedWork,
  query: string,
  folders: readonly FolderRow[],
): boolean {
  const q = String(query || '').trim().toLowerCase()
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
  if (fid) {
    const folder = folders.find((f) => f.id === fid)
    if (folder) hay.push(folder.title)
  }
  return hay.some((v) => v != null && String(v).toLowerCase().includes(q))
}
