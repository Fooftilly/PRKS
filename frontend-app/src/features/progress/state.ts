import { shallowRef } from 'vue'
import type { ProgressBrowseRow } from './legacy-work-card'
import type { ProgressStatus } from './status'

/**
 * Latest one-way snapshot handed to Progress.
 * Not a browse catalog, not a durable-operation store, not a query cache.
 */
export interface ProgressSnapshot {
  status: ProgressStatus
  rows: readonly ProgressBrowseRow[]
  offlineCached: boolean
  generation: number
}

export const progressSnapshot = shallowRef<ProgressSnapshot | null>(null)
