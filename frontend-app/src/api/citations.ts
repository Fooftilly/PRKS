/**
 * Typed client for `POST /api/citations` (Citation V1, #41).
 * Transport types come from docs/api/openapi-citations.json. Runtime parsers
 * still check the response shape. A read: it changes nothing on the server.
 */
import { PrksApiError, prksApiRequest } from './http'
import type { CitationsBibtex, CitationsCslJson, CslItem } from './generated/citations'

export type { CitationsBibtex, CitationsCslJson, CslItem }

export const CITATIONS_CONTRACT_VERSION = '0.1.0'

const READ_ERROR = 'Could not read the citations.'

function invalid(): PrksApiError {
  return new PrksApiError(READ_ERROR, 200, 'invalid_response')
}

function asRecord(value: unknown): Record<string, unknown> {
  if (value == null || typeof value !== 'object' || Array.isArray(value)) throw invalid()
  return value as Record<string, unknown>
}

function stringList(value: unknown): string[] {
  if (!Array.isArray(value) || !value.every((entry) => typeof entry === 'string')) throw invalid()
  return value as string[]
}

/** A CSL item needs `id` and `type`; citeproc reads the rest and ignores what it does not know. */
function parseCslItem(value: unknown): CslItem {
  const record = asRecord(value)
  if (typeof record.id !== 'string' || typeof record.type !== 'string') throw invalid()
  return record as CslItem
}

export function parseCitationsCslJson(payload: unknown): CitationsCslJson {
  const record = asRecord(payload)
  if (record.format !== 'csl-json' || !Array.isArray(record.items)) throw invalid()
  return { format: 'csl-json', items: record.items.map(parseCslItem), missing: stringList(record.missing) }
}

export function parseCitationsBibtex(payload: unknown): CitationsBibtex {
  const record = asRecord(payload)
  if (record.format !== 'bibtex' || typeof record.text !== 'string') throw invalid()
  return { format: 'bibtex', text: record.text, missing: stringList(record.missing) }
}

function request(workIds: readonly string[], format: 'csl-json' | 'bibtex', signal?: AbortSignal) {
  return prksApiRequest('/api/citations', {
    method: 'POST',
    body: JSON.stringify({ work_ids: [...workIds], format }),
    signal,
  })
}

/** The Works' CSL-JSON items, in request order; ids with no Work are in `missing`. */
export async function fetchCslItems(workIds: readonly string[], signal?: AbortSignal): Promise<CitationsCslJson> {
  return parseCitationsCslJson(await request(workIds, 'csl-json', signal))
}

/** The Works' BibTeX entries as one text, separated by blank lines. */
export async function fetchBibtex(workIds: readonly string[], signal?: AbortSignal): Promise<CitationsBibtex> {
  return parseCitationsBibtex(await request(workIds, 'bibtex', signal))
}
