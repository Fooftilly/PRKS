import { summaryText } from '../search/codec'
import { buildSearchResultsProjection, SEARCH_NO_RESULTS } from '../search/projection'
import type { SearchResultsProjection } from '../search/types'
import type { SavedViewDefinition, SavedViewDetailAvailability, SavedViewRecord } from './types'

function text(value: unknown): string {
  return value == null ? '' : String(value)
}

export function acceptSavedViewDefinition(value: unknown): SavedViewDefinition {
  const record = value && typeof value === 'object' ? (value as Record<string, unknown>) : {}
  return {
    mode: text(record.mode),
    q: text(record.q),
    tag: text(record.tag),
    author: text(record.author),
    publisher: text(record.publisher),
  }
}

export function acceptSavedViewRecord(value: unknown): SavedViewRecord | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const record = value as Record<string, unknown>
  const id = text(record.id).trim()
  if (!id) return null
  return {
    id,
    name: text(record.name) || 'Saved View',
    search: acceptSavedViewDefinition(record.search),
  }
}

export interface SavedViewDetailProjection {
  readonly availability: SavedViewDetailAvailability
  readonly view: SavedViewRecord | null
  readonly viewId: string
  /** `hashFromDefinition(view.search)`, computed by the coordinator. */
  readonly searchHash: string
  readonly results: SearchResultsProjection
  readonly generation: number
}

export const SAVED_VIEWS_EMPTY = 'No Saved Views yet.'
export const SAVED_VIEWS_EMPTY_HINT = 'Run a search and choose “Save View” to keep it here.'

export interface SavedViewIndexRow {
  readonly id: string
  readonly name: string
  readonly summary: string
  readonly href: string
}

export interface SavedViewIndexProjection {
  readonly rows: readonly SavedViewIndexRow[]
  readonly generation: number
}

/** Summary text is `summaryText` from the search query codec. This does not restate it. */
export function savedViewSummary(search: unknown): string {
  return summaryText(search)
}

export function buildSavedViewIndexProjection(input: {
  views?: unknown
  generation: number
}): SavedViewIndexProjection {
  const rows: SavedViewIndexRow[] = []
  if (Array.isArray(input.views)) {
    for (const item of input.views) {
      const view = acceptSavedViewRecord(item)
      if (!view) continue
      rows.push({
        id: view.id,
        name: view.name,
        summary: savedViewSummary(view.search),
        href: `#/views/${encodeURIComponent(view.id)}`,
      })
    }
  }
  return { rows, generation: input.generation }
}

export function buildSavedViewDetailProjection(input: {
  availability?: SavedViewDetailAvailability
  view?: unknown
  viewId?: string
  searchHash?: string
  rows?: unknown
  generation: number
}): SavedViewDetailProjection {
  const view = acceptSavedViewRecord(input.view)
  const availability: SavedViewDetailAvailability =
    input.availability === 'ready' && view ? 'ready' : 'not-found'
  const hash = text(input.searchHash)
  return {
    availability,
    view: availability === 'ready' ? view : null,
    viewId: view?.id || text(input.viewId),
    searchHash: hash.startsWith('#/search') ? hash : '#/search',
    results: buildSearchResultsProjection({
      rows: availability === 'ready' ? input.rows : [],
      emptyMessage: SEARCH_NO_RESULTS,
      generation: input.generation,
    }),
    generation: input.generation,
  }
}
