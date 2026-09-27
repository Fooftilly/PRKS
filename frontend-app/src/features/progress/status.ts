/** Canonical Work progress statuses. Mirrors the DB check and navigation.js. */
export const PROGRESS_STATUSES = ['Not Started', 'Planned', 'In Progress', 'Completed', 'Paused'];

export type ProgressStatus = 'Not Started' | 'Planned' | 'In Progress' | 'Completed' | 'Paused'

const STATUS_SET: ReadonlySet<string> = new Set(PROGRESS_STATUSES)

export function isProgressStatus(value: string): value is ProgressStatus {
  return STATUS_SET.has(value)
}

/**
 * Invalid or missing status parameters are not a status.
 * Navigation canonicalizes those hashes to Not Started before paint.
 */
export function normalizeProgressStatusParam(raw: string | null | undefined): ProgressStatus | null {
  if (raw == null || String(raw).trim() === '') return null
  let decoded: string
  try {
    decoded = decodeURIComponent(String(raw).trim())
  } catch {
    return null
  }
  return isProgressStatus(decoded) ? decoded : null
}

/** Hash parser for `#/progress?status=...`. Null when the route or status is not canonical. */
export function progressStatusFromHash(hash: string | null | undefined): ProgressStatus | null {
  const h = hash || ''
  const withoutHash = h.startsWith('#') ? h.slice(1) : h
  if (!withoutHash.startsWith('/progress')) return null
  const q = withoutHash.indexOf('?')
  if (q < 0) return null
  const params = new URLSearchParams(withoutHash.slice(q + 1))
  return normalizeProgressStatusParam(params.get('status'))
}

/** Route paint status. Missing and invalid values use the navigation default. */
export function canonicalProgressStatus(raw: string | null | undefined): ProgressStatus {
  return normalizeProgressStatusParam(raw) ?? 'Not Started'
}

export function progressCanonicalHash(status: ProgressStatus): string {
  return '#/progress?status=' + encodeURIComponent(status)
}
