import type { SearchRequest, SearchResultRow, SearchResultsProjection } from './types'

export const SEARCH_NO_RESULTS = 'No results found matching your query.'
export const SEARCH_NO_TAGGED_FILES = 'No files have this tag yet.'

function text(value: unknown): string {
  return value == null ? '' : String(value).trim()
}

/** Same truthiness `fetchSearch` sends as `any=1`. */
export function isAnySearch(raw: unknown): boolean {
  const value = text(raw).toLowerCase()
  return value === '1' || value === 'true' || value === 'yes'
}

export function acceptSearchRequest(params: unknown): SearchRequest {
  const record = params && typeof params === 'object' ? (params as Record<string, unknown>) : {}
  return {
    q: text(record.q),
    tag: text(record.tag),
    author: text(record.author),
    publisher: text(record.publisher),
    any: isAnySearch(record.any),
  }
}

export function searchTitle(request: SearchRequest): string {
  const { q, tag, author, publisher } = request
  if (tag) return `Files tagged “${tag}”`
  if (author && q && publisher) {
    return `Search: “${q}” · author “${author}” · publisher “${publisher}”`
  }
  if (author && publisher && !q) return `Author “${author}” · publisher “${publisher}”`
  if (publisher && q && !author) return `Search: “${q}” · publisher “${publisher}”`
  if (publisher && !q && !author) return `Files with publisher matching “${publisher}”`
  if (author && q) return `Search: “${q}” · author “${author}”`
  if (author) return `Files with author matching “${author}”`
  return `Search results for “${q}”`
}

export function searchEmptyMessage(request: SearchRequest): string {
  return request.tag ? SEARCH_NO_TAGGED_FILES : SEARCH_NO_RESULTS
}

/** Save View is offered for any non-empty request. The modal reports unsavable mixes. */
export function searchCanOfferSave(request: SearchRequest): boolean {
  return !!(request.q || request.tag || request.author || request.publisher)
}

export function acceptSearchRows(value: unknown): SearchResultRow[] {
  if (!Array.isArray(value)) return []
  const out: SearchResultRow[] = []
  for (const row of value) {
    if (!row || typeof row !== 'object' || Array.isArray(row)) continue
    out.push(row as SearchResultRow)
  }
  return out
}

export function buildSearchResultsProjection(input: {
  rows?: unknown
  emptyMessage?: string
  generation: number
}): SearchResultsProjection {
  return {
    rows: acceptSearchRows(input.rows),
    emptyMessage: input.emptyMessage || SEARCH_NO_RESULTS,
    generation: input.generation,
  }
}

export interface SearchRouteProjection {
  readonly request: SearchRequest
  readonly canonicalHash: string
  readonly title: string
  readonly canOfferSave: boolean
  readonly results: SearchResultsProjection
  readonly generation: number
}

export function buildSearchRouteProjection(input: {
  request?: unknown
  canonicalHash: string
  rows?: unknown
  generation: number
}): SearchRouteProjection {
  const request = acceptSearchRequest(input.request)
  return {
    request,
    canonicalHash: input.canonicalHash,
    title: searchTitle(request),
    canOfferSave: searchCanOfferSave(request),
    results: buildSearchResultsProjection({
      rows: input.rows,
      emptyMessage: searchEmptyMessage(request),
      generation: input.generation,
    }),
    generation: input.generation,
  }
}
