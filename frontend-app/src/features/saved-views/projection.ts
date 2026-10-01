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
  /** `prksSearchHashFromDefinition(view.search)`, computed by the coordinator. */
  readonly searchHash: string
  readonly results: SearchResultsProjection
  readonly generation: number
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
