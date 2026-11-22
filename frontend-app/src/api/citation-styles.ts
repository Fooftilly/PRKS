/**
 * Client for `/api/citation-styles`: the citation styles a Library imported
 * (Citation V1, #41). Listing and reading need a connection to PRKS; an
 * import or a delete is Library administration. Runtime parsers check each
 * answer's shape.
 */
import { PrksApiError, prksApiRequest } from './http'

export interface ImportedStyleSummary {
  readonly id: string
  readonly title: string
  readonly created_at: string
}

export interface ImportedStyle {
  readonly id: string
  readonly title: string
  readonly csl: string
}

const PATH = '/api/citation-styles'
/** An imported style's id: `custom-` and 32 hex digits (`backend/domain/works/citation_styles.py`). */
export const IMPORTED_STYLE_ID = /^custom-[0-9a-f]{32}$/

/** The server's own size cap, checked before a file is read (`MAX_CSL_BYTES`). */
export const MAX_CSL_BYTES = 1024 * 1024

function invalid(): PrksApiError {
  return new PrksApiError('Could not read the citation styles.', 200, 'invalid_response')
}

function asRecord(value: unknown): Record<string, unknown> {
  if (value == null || typeof value !== 'object' || Array.isArray(value)) throw invalid()
  return value as Record<string, unknown>
}

function text(record: Record<string, unknown>, key: string): string {
  const value = record[key]
  if (typeof value !== 'string') throw invalid()
  return value
}

function summary(value: unknown): ImportedStyleSummary {
  const record = asRecord(value)
  const id = text(record, 'id')
  if (!IMPORTED_STYLE_ID.test(id)) throw invalid()
  return { id, title: text(record, 'title'), created_at: text(record, 'created_at') }
}

export async function listImportedStyles(signal?: AbortSignal): Promise<ImportedStyleSummary[]> {
  const styles = asRecord(await prksApiRequest(PATH, { signal })).styles
  if (!Array.isArray(styles)) throw invalid()
  return styles.map(summary)
}

export async function readImportedStyle(id: string, signal?: AbortSignal): Promise<ImportedStyle> {
  if (!IMPORTED_STYLE_ID.test(id)) throw new PrksApiError('Citation style not found.', 404)
  const record = asRecord(await prksApiRequest(`${PATH}/${id}`, { signal }))
  return { id: text(record, 'id'), title: text(record, 'title'), csl: text(record, 'csl') }
}

/** Store a `.csl` file's text; a refused file is a 400 whose message says why. */
export async function importStyle(csl: string): Promise<ImportedStyleSummary> {
  return summary(await prksApiRequest(PATH, { method: 'POST', body: JSON.stringify({ csl }) }))
}

export async function deleteImportedStyle(id: string): Promise<void> {
  if (!IMPORTED_STYLE_ID.test(id)) throw new PrksApiError('Citation style not found.', 404)
  await prksApiRequest(`${PATH}/${id}`, { method: 'DELETE' })
}
