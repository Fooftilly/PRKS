/**
 * Typed Folder detail shapes. The record is the coordinator's already-effective
 * folder, not a second folder store.
 */

export type FolderDetailAvailability = 'ready' | 'unavailable' | 'not-found'

export interface FolderDetailRecord {
  readonly id: string
  readonly title: string
  readonly description: string
  readonly children: readonly unknown[]
  readonly works: readonly unknown[]
  /** Original effective folder. Hierarchy nav and new-folder parent read it. */
  readonly source: Record<string, unknown>
}
