/**
 * Research Notes restore decision (#466 slices 2 and 3).
 *
 * A pure function over what the mount path already knows: the recovery
 * records for one Work with their lineage class and stored body, the
 * acknowledged base `K` from notes-state, and the unsettled Research Notes
 * queue rows `Q`. It reads nothing and changes nothing; `works.js` applies
 * the plan.
 *
 * Automatic restore is allowed only when it cannot overwrite anything:
 * exactly one candidate, from a lineage no live editor can own (this tab
 * before reload, another pane of this tab, or a tab proven closed), typed on
 * exactly the acknowledged body the server still holds, or on a body this
 * lineage itself queued from that same base (the #475 own-predecessor rule).
 * Every other candidate is kept untouched and reported for review, with the
 * one action a user may take on it there: restore it as it is, reconcile it
 * against the current note first, or neither (inspect and copy only).
 */

import { fingerprintText, sameBaseIdentity } from './fingerprint'
import type { LineageClass } from './lineage'
import type { DraftBase, DraftRecord } from './schema'

/** The acknowledged note the mount read, never an effective overlay. */
export interface AcknowledgedNote {
  value: string
  revision: number
  /** `server`: read from the server now. `cache`, `pending-create`: not verified. */
  source: DraftBase['source']
}

/** One unsettled Research Notes operation for this Work, in queue order. */
export interface QueuedNoteRow {
  opId: string
  text: string
}

export interface RestoreCandidate {
  record: DraftRecord
  /** The stored body at the record's generation; null when it could not be read. */
  body: string | null
  lineage: LineageClass
}

export interface RestoreInput {
  /** Workspace tab id of the pane that is mounting. */
  paneId: string
  candidates: RestoreCandidate[]
  base: AcknowledgedNote | null
  /** Null when the durable queue could not be read: nothing is cleaned up or restored. */
  queue: QueuedNoteRow[] | null
  /** Another session in this page holds unsaved text for this Work. */
  otherDirtySession: boolean
  /**
   * The mounting pane's own editor already shows text other than the
   * acknowledged note, or has queued it: a restore would replace it, so it
   * is offered only as a reconciliation. False on mount.
   */
  editorDirty?: boolean
  /** Injected in tests to force collisions. */
  fingerprint?: (text: string) => string
}

export type ReviewReason =
  | 'multiple-drafts'
  | 'live-elsewhere'
  | 'other-draft-live'
  | 'ownership-unknown'
  | 'tail-missing'
  | 'body-missing'
  | 'dirty-session'
  | 'editor-dirty'
  | 'base-unverified'
  | 'foreign-queue'
  | 'queue-unknown'
  | 'base-advanced'

/**
 * What Review may offer for one candidate, judged on its own:
 * - `restore`: put it in the editor as it is (it overwrites nothing);
 * - `reconcile`: compare it with the current note and choose explicitly
 *   before anything is written (the note moved on, a foreign row is queued,
 *   the base cannot be verified, or the editor already shows other text);
 * - null: inspect and copy only (live or uncertain owner, incomplete or
 *   unreadable body, unreadable queue, unsaved text in another pane).
 */
export type ReviewAction = 'restore' | 'reconcile' | null

export interface ReviewCandidate {
  draftId: string
  reason: ReviewReason
  lineage: LineageClass
  generation: number
  bodyLength: number
  paneId: string
  updatedAt: number
  status: DraftRecord['status']
  action: ReviewAction
}

export interface RestorePlan {
  /**
   * Bodies exactly equal to the server's acknowledged note while nothing is
   * queued that could still change it, from a lineage no live editor can
   * still own: compare-and-delete.
   */
  cleanup: string[]
  /**
   * Already the queued row's exact body, from an inactive lineage: kept until
   * that row's acknowledgement, then cleared only while `pageInstanceId`
   * still owns it.
   */
  represented: Array<{ draftId: string; opId: string; generation: number; text: string; pageInstanceId: string }>
  restore: {
    record: DraftRecord
    body: string
    /** `blocked` only while its own predecessor row is still unsettled. */
    state: 'drafting' | 'blocked'
    /** This lineage's own unsettled predecessor, still in the queue. */
    predecessor: QueuedNoteRow | null
  } | null
  /** Kept and not applied; slice 3 shows them. */
  review: ReviewCandidate[]
}

export function planResearchNotesRestore(input: RestoreInput): RestorePlan {
  const print = memoized(input.fingerprint || fingerprintText)
  const K = input.base
  const plan: RestorePlan = { cleanup: [], represented: [], restore: null, review: [] }
  const review = (c: RestoreCandidate, reason: ReviewReason) =>
    plan.review.push({
      draftId: c.record.draftId,
      reason,
      lineage: c.lineage,
      generation: c.record.generation,
      bodyLength: c.record.bodyLength,
      paneId: c.record.owner.paneId,
      updatedAt: c.record.updatedAt,
      status: c.record.status,
      action: actionFor(c, input, print),
    })

  const queue = input.queue
  let liveElsewhere = false
  let unresolved = 0
  const remaining: RestoreCandidate[] = []
  for (const c of input.candidates) {
    if (c.record.status === 'discarded') continue
    // Being edited in this or another live editor: reported, never offered
    // for restore here, and its existence alone stops an automatic restore.
    if (c.lineage === 'self-live' || c.lineage === 'other-live') {
      liveElsewhere = true
      review(c, 'live-elsewhere')
      continue
    }
    // A newer generation never reached storage: the stored body is not the
    // latest text, so it proves nothing about what was saved.
    if (c.record.status === 'tail-missing') {
      unresolved++
      review(c, 'tail-missing')
      continue
    }
    if (c.body === null) {
      unresolved++
      review(c, 'body-missing')
      continue
    }
    // Equal to the note only proves it saved while no queued row can still
    // replace that note, and only for a lineage no live editor may still own.
    const inactive = c.lineage === 'same-runtime-orphan' || c.lineage === 'dead-runtime'
    if (inactive && K && K.source === 'server' && queue && !queue.length && c.body === K.value) {
      plan.cleanup.push(c.record.draftId)
      continue
    }
    // Left for its row's acknowledgement to clear, which is only safe for a
    // lineage no live editor can still be extending.
    const queuedOpId = c.record.pipeline ? c.record.pipeline.queuedOpId : null
    const last = queue ? queue[queue.length - 1] : undefined
    if (inactive && queuedOpId && last && last.opId === queuedOpId && last.text === c.body) {
      plan.represented.push({
        draftId: c.record.draftId,
        opId: queuedOpId,
        generation: c.record.generation,
        text: c.body,
        pageInstanceId: c.record.owner.pageInstanceId,
      })
      continue
    }
    remaining.push(c)
  }

  // An unreadable or incomplete draft is still a draft: it keeps the restore ambiguous.
  if (remaining.length + unresolved !== 1) {
    for (const c of remaining) review(c, 'multiple-drafts')
    return plan
  }
  if (!remaining.length) return plan
  const c = remaining[0]!
  const judged = judge(c, input, liveElsewhere, print)
  if ('reason' in judged) review(c, judged.reason)
  else plan.restore = judged
  return plan
}

/** Fingerprints the note once however many candidates are judged against it. */
function memoized(print: (text: string) => string): (text: string) => string {
  const seen = new Map<string, string>()
  return (text) => {
    let value = seen.get(text)
    if (value === undefined) {
      value = print(text)
      if (seen.size > 8) seen.clear()
      seen.set(text, value)
    }
    return value
  }
}

type Judgement = NonNullable<RestorePlan['restore']> | { reason: ReviewReason }

/** One remaining candidate on its own: restorable as it is, or why not. */
function judge(c: RestoreCandidate, input: RestoreInput, liveElsewhere: boolean, print: (text: string) => string): Judgement {
  const reason = blockingReason(c, input, liveElsewhere)
  if (reason) return { reason }

  const K = input.base
  const queue = input.queue
  const record = c.record
  const pipeline = record.pipeline
  const own = pipeline ? pipeline.ownQueued : null
  const k = K ? { revision: K.revision, length: K.value.length, fingerprint: print(K.value) } : null
  let predecessor: QueuedNoteRow | null = null
  if (!queue) return { reason: 'queue-unknown' }
  if (queue.length) {
    // Only this lineage's own predecessor may still be queued: one row, the
    // op it queued, with the length and fingerprint it recorded.
    const row = queue.length === 1 ? queue[0]! : null
    if (!own || !row || row.opId !== own.opId || row.text.length !== own.textLength || print(row.text) !== own.textFingerprint) {
      return { reason: 'foreign-queue' }
    }
    predecessor = row
  }

  const blockedBase = pipeline && pipeline.state === 'blocked' ? pipeline.blockedBase : null
  const typedOn = blockedBase || record.base
  const unchanged = sameBaseIdentity(record.base, k) || (!!blockedBase && sameBaseIdentity(blockedBase, k))
  if (predecessor) {
    // The predecessor is unsettled, so the server cannot have moved on from
    // the base it was queued from.
    if (!unchanged || !own || !sameBaseIdentity(own.base, k)) return { reason: 'base-advanced' }
  } else if (!unchanged) {
    const advancedByOwn =
      !!own &&
      !!K &&
      !!k &&
      sameBaseIdentity(own.base, typedOn) &&
      typedOn.revision !== null &&
      K.revision > typedOn.revision &&
      k.length === own.textLength &&
      k.fingerprint === own.textFingerprint
    if (!advancedByOwn) return { reason: 'base-advanced' }
  }

  return {
    record,
    body: c.body as string,
    state: predecessor && pipeline && pipeline.state === 'blocked' ? 'blocked' : 'drafting',
    predecessor,
  }
}

/** Reasons a reviewer can still act on by comparing with the current note first. */
const RECONCILABLE: ReadonlySet<ReviewReason> = new Set(['base-advanced', 'foreign-queue', 'base-unverified'])

function actionFor(c: RestoreCandidate, input: RestoreInput, print: (text: string) => string): ReviewAction {
  if (c.record.status !== 'active' || c.body === null) return null
  if (c.lineage !== 'same-runtime-orphan' && c.lineage !== 'dead-runtime') return null
  // Judged alone: other drafts and other live lineages are the reviewer's to weigh.
  const judged = judge(c, { ...input, editorDirty: false }, false, print)
  if (!('reason' in judged)) return input.editorDirty ? 'reconcile' : 'restore'
  return RECONCILABLE.has(judged.reason) ? 'reconcile' : null
}

function blockingReason(c: RestoreCandidate, input: RestoreInput, liveElsewhere: boolean): ReviewReason | null {
  // Another editor is still extending a draft of this note.
  if (liveElsewhere) return 'other-draft-live'
  if (c.lineage !== 'same-runtime-orphan' && c.lineage !== 'dead-runtime') return 'ownership-unknown'
  if (input.otherDirtySession) return 'dirty-session'
  if (input.editorDirty) return 'editor-dirty'
  if (!input.base || input.base.source !== 'server') return 'base-unverified'
  if (c.record.base.source === 'unknown' || c.record.base.revision === null) return 'base-unverified'
  return null
}
