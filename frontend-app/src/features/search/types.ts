/**
 * Typed search request and result shapes shared by Search and Saved View detail.
 * The query codec is `codec.ts` (`definitionFromRoute`, `hashFromDefinition`,
 * `optionsFromDefinition`, `summaryText`). `PrksRouteInstance` records the
 * already-parsed search params; it does not replace that codec or parse hashes.
 * Rows are the coordinator's already-effective server results, not a second Work store.
 */

export interface SearchRequest {
  readonly q: string
  readonly tag: string
  readonly author: string
  readonly publisher: string
  readonly any: boolean
}

export interface SearchResultRow {
  readonly id?: unknown
  readonly title?: unknown
  readonly abstract?: unknown
  readonly [field: string]: unknown
}

export interface SearchResultsProjection {
  readonly rows: readonly SearchResultRow[]
  readonly emptyMessage: string
  readonly generation: number
}

/** Unsubmitted form values. Local to the mounted Search surface. */
export interface SearchFormDraft {
  readonly any: boolean
  readonly q: string
  readonly author: string
  readonly publisher: string
}
