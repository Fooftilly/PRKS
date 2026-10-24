/**
 * Already-effective Recent rows. Order is the coordinator's top-N by
 * last_opened_at. This projection does not sort, filter, or read the
 * durable queue.
 */

export interface RecentRow {
  readonly id?: unknown
  readonly title?: unknown
  readonly last_opened_at?: unknown
}

export interface RecentProjection {
  readonly rows: readonly RecentRow[]
  readonly offlineCached: boolean
  readonly generation: number
}

export function acceptRecentRows(value: unknown): RecentRow[] {
  if (!Array.isArray(value)) return []
  const out: RecentRow[] = []
  for (const row of value) {
    if (!row || typeof row !== 'object' || Array.isArray(row)) continue
    out.push(row as RecentRow)
  }
  return out
}

/** Matches the legacy Recent card subtitle, including an invalid date string. */
export function recentOpenedSubtitle(lastOpenedAt: unknown): string {
  const label = lastOpenedAt ? new Date(lastOpenedAt as string | number).toLocaleString() : 'Unknown'
  return `Last opened: ${label}`
}

export function buildRecentProjection(input: {
  rows?: unknown
  offlineCached?: boolean
  generation: number
}): RecentProjection {
  return {
    rows: acceptRecentRows(input.rows),
    offlineCached: input.offlineCached === true,
    generation: input.generation,
  }
}
