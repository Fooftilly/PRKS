import { canonicalProgressStatus, type ProgressStatus } from './status'

export interface ProgressBrowseRow {
  id?: unknown
  title?: unknown
  status?: unknown
  abstract_excerpt?: unknown
  abstract?: unknown
}

function prksAbstractExcerpt(value: unknown): string {
  const fn = window.prksAbstractExcerpt
  if (typeof fn !== 'function') return ''
  return fn(value)
}

/**
 * Subtitle under a Progress card.
 * `abstract_excerpt` is already bounded by the server (or by the projection
 * that reproduces that bound). Only a legacy full `abstract` is excerpted,
 * and only by the shared helper — never a second UTF-16 slice.
 */
export function progressCardSubtitle(work: ProgressBrowseRow): string {
  const excerpt =
    work.abstract_excerpt != null
      ? String(work.abstract_excerpt)
      : prksAbstractExcerpt(work.abstract)
  return excerpt ? excerpt + '…' : ''
}

export function acceptEffectiveRows(rows: unknown): ProgressBrowseRow[] {
  if (!Array.isArray(rows)) return []
  const out: ProgressBrowseRow[] = []
  for (const row of rows) {
    if (!row || typeof row !== 'object' || Array.isArray(row)) continue
    out.push(row as ProgressBrowseRow)
  }
  return out
}

/** Alphabetical title order, base sensitivity, matching the legacy Progress renderer. */
export function compareProgressTitles(a: ProgressBrowseRow, b: ProgressBrowseRow): number {
  return String(a.title || '').localeCompare(String(b.title || ''), undefined, { sensitivity: 'base' })
}

/**
 * Progress group membership. `status` on each row is already the effective
 * value. This filter does not read durable operations.
 */
export function progressVisibleRows(rows: readonly ProgressBrowseRow[], status: ProgressStatus): ProgressBrowseRow[] {
  return rows.filter((w) => w != null && w.status === status).sort(compareProgressTitles)
}

export function progressFileCountLabel(count: number): string {
  return count === 1 ? '1 file' : `${count} files`
}

export function progressPageTitle(status: ProgressStatus): string {
  return `Files · ${status}`
}

export function progressRowsForStatus(rows: unknown, rawStatus: string | null | undefined): ProgressBrowseRow[] {
  return progressVisibleRows(acceptEffectiveRows(rows), canonicalProgressStatus(rawStatus))
}
