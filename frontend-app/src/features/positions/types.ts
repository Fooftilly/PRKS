/**
 * Typed Position shapes for Vue route projections.
 * Already-effective rows/detail from the legacy coordinator — not a second
 * catalogue or durable-operation store.
 *
 * These are not aliases of `src/api/generated/positions.ts`. That module is
 * the compile-time HTTP contract (`PositionSummary`, wire `PositionDetail`,
 * sync state, create/update bodies). The projection below drops timestamps,
 * coerces description to text, and carries Argument rows the coordinator has
 * already overlaid. Domain validation stays on the research-network backend.
 */

/** One Argument/Stance row already overlaid onto a Position detail. */
export interface PositionArgumentRef {
  readonly id: string
  readonly name: string
  readonly kind: string
  readonly verdict_id: string
  readonly verdict_label: string
}

/** One effective Position index row. */
export interface PositionIndexItem {
  readonly id: string
  readonly name: string
  readonly description: string
}

/**
 * One effective Position detail record.
 * Argument names and any Work-derived values are already resolved by the
 * coordinator. Vue does not re-read the durable queue.
 */
export interface PositionDetail {
  readonly id: string
  readonly name: string
  readonly description: string
  readonly arguments: readonly PositionArgumentRef[]
}

export type PositionIndexAvailability = 'ready' | 'unavailable'
export type PositionDetailAvailability = 'ready' | 'unavailable' | 'not-found'
