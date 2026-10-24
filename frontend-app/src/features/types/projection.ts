/**
 * Already-grouped File types rows. Order and membership belong to
 * `prksTypesIndexModel` / `prksTypesDetailModel`. This projection does not
 * regroup, sort, or read the durable queue.
 */

export interface TypesIndexRow {
  readonly value: string
  readonly label: string
  readonly count: number
}

export interface TypesBrowseRow {
  readonly id?: unknown
  readonly title?: unknown
  readonly doc_type?: unknown
}

export interface TypesIndexProjection {
  readonly rows: readonly TypesIndexRow[]
  readonly generation: number
}

export interface TypeDetailProjection {
  readonly docType: string
  readonly label: string
  readonly rows: readonly TypesBrowseRow[]
  readonly offlineCached: boolean
  readonly generation: number
}

export function acceptTypesIndexRows(value: unknown): TypesIndexRow[] {
  if (!Array.isArray(value)) return []
  const out: TypesIndexRow[] = []
  for (const row of value) {
    if (!row || typeof row !== 'object' || Array.isArray(row)) continue
    const record = row as { value?: unknown; label?: unknown; count?: unknown }
    const typeValue = typeof record.value === 'string' ? record.value : ''
    if (!typeValue) continue
    const label = typeof record.label === 'string' && record.label ? record.label : typeValue
    const count = Number(record.count)
    out.push({
      value: typeValue,
      label,
      count: Number.isFinite(count) && count > 0 ? count : 0,
    })
  }
  return out
}

export function acceptTypeDetailRows(value: unknown): TypesBrowseRow[] {
  if (!Array.isArray(value)) return []
  const out: TypesBrowseRow[] = []
  for (const row of value) {
    if (!row || typeof row !== 'object' || Array.isArray(row)) continue
    out.push(row as TypesBrowseRow)
  }
  return out
}

/** Matches the legacy index row destination. */
export function typeDetailHref(value: string): string {
  return `#/types/${encodeURIComponent(value)}`
}

/** Matches the legacy "N file(s)" line. */
export function typeFileCountLabel(count: number): string {
  const n = Number(count) || 0
  return `${n} file${n === 1 ? '' : 's'}`
}

export function buildTypesIndexProjection(input: {
  rows?: unknown
  generation: number
}): TypesIndexProjection {
  return {
    rows: acceptTypesIndexRows(input.rows),
    generation: input.generation,
  }
}

export function buildTypeDetailProjection(input: {
  docType?: unknown
  label?: unknown
  rows?: unknown
  offlineCached?: boolean
  generation: number
}): TypeDetailProjection {
  const docType = typeof input.docType === 'string' && input.docType ? input.docType : 'misc'
  const label = typeof input.label === 'string' && input.label ? input.label : docType
  return {
    docType,
    label,
    rows: acceptTypeDetailRows(input.rows),
    offlineCached: input.offlineCached === true,
    generation: input.generation,
  }
}
