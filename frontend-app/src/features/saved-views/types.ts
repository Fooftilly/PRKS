/**
 * Saved View record shapes. Saved View persistence stays on the canonical
 * `/api/saved-views` wrappers in `api.js`; this is not a second store and not
 * query-cache state. Results are never stored with the view.
 */

export type SavedViewDetailAvailability = 'ready' | 'not-found'

export interface SavedViewDefinition {
  readonly mode: string
  readonly q: string
  readonly tag: string
  readonly author: string
  readonly publisher: string
}

export interface SavedViewRecord {
  readonly id: string
  readonly name: string
  readonly search: SavedViewDefinition
}
