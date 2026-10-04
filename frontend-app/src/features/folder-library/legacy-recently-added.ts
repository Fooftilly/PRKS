import type { FolderRow, RecentlyAddedWork } from './types'

export function recentlyAddedDateLabel(createdAt: unknown): string {
  const fn = window.prksRecentlyAddedDateLabel
  if (typeof fn === 'function') return fn(createdAt)
  if (!createdAt) return ''
  const d = new Date(String(createdAt))
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}

export function effectiveRecentlyAddedRows(acknowledged: RecentlyAddedWork[]): RecentlyAddedWork[] {
  const fn = window.prksEffectiveProjectionRows
  if (typeof fn !== 'function') return acknowledged
  const rows = fn(acknowledged, 'recently-added')
  return Array.isArray(rows) ? (rows as RecentlyAddedWork[]) : acknowledged
}

export function recentlyAddedMatchesQuery(
  work: RecentlyAddedWork,
  query: string,
  folders: readonly FolderRow[],
): boolean {
  const fn = window.prksRecentlyAddedWorkMatchesQuery
  if (typeof fn === 'function') {
    const byId = new Map(folders.map((f) => [f.id, f]))
    return fn(work, query, byId)
  }
  const q = String(query || '').trim().toLowerCase()
  if (!q) return true
  const hay = [work.title, work.author_text, work.status].map((v) =>
    v == null ? '' : String(v).toLowerCase(),
  )
  return hay.some((v) => v.includes(q))
}
