/**
 * Saved View shapes the route surfaces paint. The wire type is
 * `SavedView` in `api/saved-views.ts`; records are read and written through
 * `records.ts`. Results are never stored with the view.
 */

/** `error`: the record read failed for a reason other than a missing view. */
export type SavedViewDetailAvailability = 'ready' | 'not-found' | 'error'

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
