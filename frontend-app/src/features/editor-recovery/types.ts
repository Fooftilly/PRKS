/**
 * View models for browser-local editor recovery (#466 slice 3): what an
 * editor pane's notice and the Review dialog show. The editor's adapter
 * (`works.js` for Research Notes) builds them from `prksEditorRecovery`; the
 * components never read recovery storage or the queue themselves.
 */

/** What the pane's notice shows, or null when there is nothing to say. */
export interface RecoveryNoticeView {
  workId: string
  /** Drafts left for review, never one another live editor owns. */
  drafts: number
  /** Of those, how many are incomplete or unreadable. */
  incomplete: number
  /** Whether other Research Notes changes are waiting to sync, as last read. */
  pendingSync?: 'none' | 'queued' | 'unknown'
  /** This pane's newest text has no recovery copy: the storage error code, or null. */
  unprotected: string | null
}

export type RecoveryLineage = 'self-live' | 'other-live' | 'same-runtime-orphan' | 'dead-runtime' | 'unknown'

/** The one action Review may offer for a draft now (see `planResearchNotesRestore`). */
export type RecoveryAction = 'restore' | 'reconcile' | null

/** What the reviewer saw of a record; an action applies only while it still holds. */
export interface RecoveryExpectation {
  draftId: string
  pageInstanceId: string
  generation: number
  status: 'active' | 'tail-missing'
}

export interface RecoveryCandidateView {
  expect: RecoveryExpectation
  draftId: string
  lineage: RecoveryLineage
  /** Written by the pane that is reviewing. */
  samePane: boolean
  status: 'active' | 'tail-missing'
  reason: string
  action: RecoveryAction
  generation: number
  updatedAt: number
  length: number
  /** The stored text; null when unreadable or when another live editor owns it. */
  body: string | null
  typedOnRevision: number | null
  pipelineState: string | null
}

export interface RecoveryCurrentNote {
  /** What the editor shows now. */
  text: string
  /** The acknowledged revision, or null when none was read. */
  revision: number | null
  /** `server`: read from the server; anything else is not verified. */
  source: string
  queue: 'none' | 'queued' | 'unknown'
  queued: number
  /** This pane's session has text not yet handed to the save queue. */
  unsaved: boolean
}

export interface RecoveryDetails {
  workId: string
  /** The editor session this review belongs to. */
  token: string
  current: RecoveryCurrentNote
  candidates: RecoveryCandidateView[]
}

export type RecoveryActionResult =
  | { ok: true }
  | { ok: false; code: 'stale' | 'changed' | 'current-changed' | 'failed' | 'unavailable' }

/** What Review asks of the editor's adapter. */
export interface ReviewActions {
  load(): Promise<RecoveryDetails | null>
  restore(details: RecoveryDetails, candidate: RecoveryCandidateView): Promise<RecoveryActionResult>
  replace(details: RecoveryDetails, candidate: RecoveryCandidateView, text: string, shown: RecoveryCurrentNote): Promise<RecoveryActionResult>
  discard(details: RecoveryDetails, candidate: RecoveryCandidateView): Promise<RecoveryActionResult>
  copy(text: string): Promise<void>
  confirm(options: { title: string; message: string; confirmLabel: string; danger: boolean }): Promise<boolean>
}
