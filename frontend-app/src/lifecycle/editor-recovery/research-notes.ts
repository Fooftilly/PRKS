/**
 * Research Notes same-pane restore decision (#466 slice 2).
 *
 * A pure function over what the mount path already knows: the recovery
 * records for one Work with their lineage class and stored body, the
 * acknowledged base `K` from notes-state, and the unsettled Research Notes
 * queue rows `Q`. It reads nothing and changes nothing; `works.js` applies
 * the plan.
 *
 * Automatic restore is allowed only when it cannot overwrite anything:
 * exactly one candidate, written by this pane before reload, typed on exactly
 * the acknowledged body the server still holds, or on a body this lineage
 * itself queued from that same base (the #475 own-predecessor rule). Every
 * other candidate is kept untouched and reported for review (slice 3).
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
  queue: QueuedNoteRow[]
  /** Another session in this page holds unsaved text for this Work. */
  otherDirtySession: boolean
  /** Injected in tests to force collisions. */
  fingerprint?: (text: string) => string
}

export type ReviewReason =
  | 'multiple-drafts'
  | 'live-elsewhere'
  | 'ownership-unknown'
  | 'other-source'
  | 'tail-missing'
  | 'body-missing'
  | 'dirty-session'
  | 'base-unverified'
  | 'foreign-queue'
  | 'base-advanced'

export interface ReviewCandidate {
  draftId: string
  reason: ReviewReason
  lineage: LineageClass
  generation: number
  bodyLength: number
  paneId: string
  updatedAt: number
}

export interface RestorePlan {
  /** Bodies exactly equal to the server's acknowledged note: compare-and-delete. */
  cleanup: string[]
  /** Already the queued row's exact body: kept until that row's acknowledgement. */
  represented: Array<{ draftId: string; opId: string; generation: number; text: string }>
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
  const print = input.fingerprint || fingerprintText
  const K = input.base
  let kPrint: string | null = null
  const kIdentity = () => {
    if (!K) return null
    if (kPrint === null) kPrint = print(K.value)
    return { revision: K.revision, length: K.value.length, fingerprint: kPrint }
  }
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
    })

  let liveElsewhere = false
  const remaining: RestoreCandidate[] = []
  for (const c of input.candidates) {
    if (c.record.status === 'discarded') continue
    // Being edited in this or another live editor: not offered, and its
    // existence alone stops an automatic restore of anything else.
    if (c.lineage === 'self-live' || c.lineage === 'other-live') {
      liveElsewhere = true
      continue
    }
    if (c.body === null) {
      review(c, 'body-missing')
      continue
    }
    if (K && K.source === 'server' && c.body === K.value) {
      plan.cleanup.push(c.record.draftId)
      continue
    }
    const queuedOpId = c.record.pipeline ? c.record.pipeline.queuedOpId : null
    const last = input.queue[input.queue.length - 1]
    if (queuedOpId && last && last.opId === queuedOpId && last.text === c.body) {
      plan.represented.push({ draftId: c.record.draftId, opId: queuedOpId, generation: c.record.generation, text: c.body })
      continue
    }
    remaining.push(c)
  }

  if (remaining.length !== 1) {
    for (const c of remaining) review(c, 'multiple-drafts')
    return plan
  }
  const c = remaining[0]!
  const reason = blockingReason(c, input, liveElsewhere)
  if (reason) {
    review(c, reason)
    return plan
  }

  const record = c.record
  const pipeline = record.pipeline
  const own = pipeline ? pipeline.ownQueued : null
  const k = kIdentity()
  let predecessor: QueuedNoteRow | null = null
  if (input.queue.length) {
    // Only this lineage's own predecessor may still be queued: one row, the
    // op it queued, with the length and fingerprint it recorded.
    const row = input.queue.length === 1 ? input.queue[0]! : null
    if (!own || !row || row.opId !== own.opId || row.text.length !== own.textLength || print(row.text) !== own.textFingerprint) {
      review(c, 'foreign-queue')
      return plan
    }
    predecessor = row
  }

  const blockedBase = pipeline && pipeline.state === 'blocked' ? pipeline.blockedBase : null
  const typedOn = blockedBase || record.base
  const unchanged = sameBaseIdentity(record.base, k) || (!!blockedBase && sameBaseIdentity(blockedBase, k))
  if (predecessor) {
    // The predecessor is unsettled, so the server cannot have moved on from
    // the base it was queued from.
    if (!unchanged || !own || !sameBaseIdentity(own.base, k)) {
      review(c, 'base-advanced')
      return plan
    }
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
    if (!advancedByOwn) {
      review(c, 'base-advanced')
      return plan
    }
  }

  plan.restore = {
    record,
    body: c.body as string,
    state: predecessor && pipeline && pipeline.state === 'blocked' ? 'blocked' : 'drafting',
    predecessor,
  }
  return plan
}

function blockingReason(c: RestoreCandidate, input: RestoreInput, liveElsewhere: boolean): ReviewReason | null {
  if (liveElsewhere) return 'live-elsewhere'
  if (c.lineage === 'unknown') return 'ownership-unknown'
  // Adoptable, but from a closed tab or another pane: slice 3 offers it.
  if (c.lineage !== 'same-runtime-orphan' || c.record.owner.paneId !== input.paneId) return 'other-source'
  if (c.record.status === 'tail-missing') return 'tail-missing'
  if (input.otherDirtySession) return 'dirty-session'
  if (!input.base || input.base.source !== 'server') return 'base-unverified'
  if (c.record.base.source === 'unknown' || c.record.base.revision === null) return 'base-unverified'
  return null
}
