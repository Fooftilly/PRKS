import type { ProgressBrowseRow } from './rows'
import type { ProgressStatus } from './status'

/**
 * One-way snapshot handed to one Progress owner.
 * Not a browse catalog, not a durable-operation store, not a query cache.
 */
export interface ProgressSnapshot {
  status: ProgressStatus
  rows: readonly ProgressBrowseRow[]
  offlineCached: boolean
  generation: number
}
