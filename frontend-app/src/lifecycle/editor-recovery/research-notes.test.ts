import { describe, expect, it } from 'vitest'
import { fingerprintText } from './fingerprint'
import type { LineageClass } from './lineage'
import { planResearchNotesRestore, type AcknowledgedNote, type QueuedNoteRow, type RestoreCandidate, type RestoreInput } from './research-notes'
import type { DraftBase, DraftPipeline, DraftRecord } from './schema'

const SERVER = 'Saved note.'
const K: AcknowledgedNote = { value: SERVER, revision: 5, source: 'server' }

function baseOf(value: string, revision: number, source: DraftBase['source'] = 'server'): DraftBase {
  return { revision, length: value.length, fingerprint: fingerprintText(value), source }
}

function identity(value: string, revision: number) {
  return { revision, length: value.length, fingerprint: fingerprintText(value) }
}

function record(over: Partial<DraftRecord> = {}): DraftRecord {
  return {
    v: 1,
    draftId: 'd-1',
    kind: 'work-research-note',
    entityType: 'work',
    entityId: 'w1',
    entityKey: 'work-research-note:w1',
    owner: { runtimeId: 'r-1', pageInstanceId: 'p-old', paneId: 'tab-1', claimedAt: 1 },
    generation: 4,
    bodyLength: 0,
    base: baseOf(SERVER, 5),
    pipeline: null,
    status: 'active',
    createdAt: 1,
    updatedAt: 2,
    ...over,
  }
}

function candidate(body: string | null, over: Partial<DraftRecord> = {}, lineage: LineageClass = 'same-runtime-orphan'): RestoreCandidate {
  return { record: record({ bodyLength: body ? body.length : 0, ...over }), body, lineage }
}

function plan(candidates: RestoreCandidate[], over: Partial<RestoreInput> = {}) {
  return planResearchNotesRestore({ paneId: 'tab-1', candidates, base: K, queue: [], otherDirtySession: false, ...over })
}

const OWN_A = 'Saved note. A'
function ownQueued(opId = 'op-a', text = OWN_A, from = identity(SERVER, 5)): DraftPipeline['ownQueued'] {
  return { opId, textLength: text.length, textFingerprint: fingerprintText(text), base: from }
}

describe('Research Notes same-pane restore plan', () => {
  it('restores this pane\'s own draft typed on the unchanged server note', () => {
    const p = plan([candidate('Saved note. More')])
    expect(p.restore).toMatchObject({ body: 'Saved note. More', state: 'drafting', predecessor: null })
    expect(p.review).toEqual([])
  })

  it('clears a draft exactly equal to the server note, but not one that only shares its fingerprint', () => {
    expect(plan([candidate(SERVER)])).toMatchObject({ cleanup: ['d-1'], restore: null, review: [] })
    const colliding = plan([candidate('Saved notE.')], { fingerprint: () => 'f'.repeat(32) })
    expect(colliding.cleanup).toEqual([])
  })

  it('leaves a draft already queued as that exact row for the row\'s acknowledgement', () => {
    const pipeline: DraftPipeline = { state: 'queued', queuedOpId: 'op-1', queuedGeneration: 4, blockedBase: null, ownQueued: null }
    const p = plan([candidate('Queued body', { pipeline })], { queue: [{ opId: 'op-1', text: 'Queued body' }], base: { ...K, source: 'cache' } })
    expect(p.represented).toEqual([{ draftId: 'd-1', opId: 'op-1', generation: 4, text: 'Queued body' }])
    expect(p.restore).toBeNull()
    expect(p.review).toEqual([])
  })

  it('never restores when the server note changed independently', () => {
    const p = plan([candidate('Saved note. More')], { base: { value: 'Other device', revision: 6, source: 'server' } })
    expect(p.restore).toBeNull()
    expect(p.review).toMatchObject([{ draftId: 'd-1', reason: 'base-advanced' }])
  })

  it('never restores on a revision match alone', () => {
    const p = plan([candidate('Saved note. More')], { base: { value: 'Saved notE.', revision: 5, source: 'server' } })
    expect(p.review).toMatchObject([{ reason: 'base-advanced' }])
  })

  it('defers every draft when two or more exist', () => {
    const p = plan([candidate('one'), candidate('two', { draftId: 'd-2' })])
    expect(p.restore).toBeNull()
    expect(p.review.map((r) => [r.draftId, r.reason])).toEqual([
      ['d-1', 'multiple-drafts'],
      ['d-2', 'multiple-drafts'],
    ])
  })

  it('defers while a live editor elsewhere holds a draft for the Work', () => {
    const p = plan([candidate('mine'), candidate(null, { draftId: 'd-live' }, 'other-live')])
    expect(p.review).toMatchObject([{ draftId: 'd-1', reason: 'live-elsewhere' }])
  })

  it('defers unknown ownership, closed tabs and other panes to slice 3', () => {
    expect(plan([candidate('x', {}, 'unknown')]).review).toMatchObject([{ reason: 'ownership-unknown' }])
    expect(plan([candidate('x', {}, 'dead-runtime')]).review).toMatchObject([{ reason: 'other-source' }])
    const otherPane = record().owner
    expect(plan([candidate('x', { owner: { ...otherPane, paneId: 'tab-2' } })]).review).toMatchObject([{ reason: 'other-source', paneId: 'tab-2' }])
  })

  it('defers a known missing tail, another dirty session and an unread body', () => {
    expect(plan([candidate('x', { status: 'tail-missing' })]).review).toMatchObject([{ reason: 'tail-missing' }])
    expect(plan([candidate('x')], { otherDirtySession: true }).review).toMatchObject([{ reason: 'dirty-session' }])
    expect(plan([candidate(null)]).review).toMatchObject([{ reason: 'body-missing' }])
  })

  it('defers when the base cannot be verified with the server', () => {
    expect(plan([candidate('x')], { base: null }).review).toMatchObject([{ reason: 'base-unverified' }])
    expect(plan([candidate('x')], { base: { ...K, source: 'cache' } }).review).toMatchObject([{ reason: 'base-unverified' }])
    const unknown: DraftBase = { revision: null, length: null, fingerprint: null, source: 'unknown' }
    expect(plan([candidate('x', { base: unknown })]).review).toMatchObject([{ reason: 'base-unverified' }])
  })

  it('defers when another operation is queued for the Work', () => {
    const p = plan([candidate('Saved note. More')], { queue: [{ opId: 'op-other', text: 'Someone else' }] })
    expect(p.review).toMatchObject([{ reason: 'foreign-queue' }])
  })

  describe('#475 own predecessor', () => {
    const blocked = (over: Partial<DraftPipeline> = {}): DraftPipeline => ({
      state: 'blocked',
      queuedOpId: null,
      queuedGeneration: 0,
      blockedBase: identity(SERVER, 5),
      ownQueued: ownQueued(),
      ...over,
    })
    const B = 'Saved note. A then B'

    it('restores a blocked body as blocked while its own predecessor row is unsettled', () => {
      const row: QueuedNoteRow = { opId: 'op-a', text: OWN_A }
      const p = plan([candidate(B, { pipeline: blocked() })], { queue: [row] })
      expect(p.restore).toMatchObject({ body: B, state: 'blocked', predecessor: row })
    })

    it('restores a drafting body behind its own unsettled predecessor as drafting', () => {
      const row: QueuedNoteRow = { opId: 'op-a', text: OWN_A }
      const p = plan([candidate(B, { pipeline: blocked({ state: 'drafting' }) })], { queue: [row] })
      expect(p.restore).toMatchObject({ state: 'drafting', predecessor: row })
    })

    it('does not take a different row for the predecessor', () => {
      const p = plan([candidate(B, { pipeline: blocked() })], { queue: [{ opId: 'op-a', text: 'Saved note. X' }] })
      expect(p.review).toMatchObject([{ reason: 'foreign-queue' }])
      const other = plan([candidate(B, { pipeline: blocked() })], { queue: [{ opId: 'op-z', text: OWN_A }] })
      expect(other.review).toMatchObject([{ reason: 'foreign-queue' }])
    })

    it('resumes on the server note once that is exactly its own acknowledged predecessor', () => {
      const p = plan([candidate(B, { pipeline: blocked() })], { base: { value: OWN_A, revision: 6, source: 'server' } })
      expect(p.restore).toMatchObject({ body: B, state: 'drafting', predecessor: null })
    })

    it('never rebases over a foreign edit', () => {
      const p = plan([candidate(B, { pipeline: blocked() })], { base: { value: 'Saved note. C from elsewhere', revision: 6, source: 'server' } })
      expect(p.restore).toBeNull()
      expect(p.review).toMatchObject([{ reason: 'base-advanced' }])
    })

    it('requires the predecessor to come from the same blocked base', () => {
      const fromOther = blocked({ ownQueued: ownQueued('op-a', OWN_A, identity('Older', 3)) })
      const p = plan([candidate(B, { pipeline: fromOther })], { base: { value: OWN_A, revision: 6, source: 'server' } })
      expect(p.review).toMatchObject([{ reason: 'base-advanced' }])
    })

    it('does not accept a matching fingerprint without a newer revision', () => {
      const p = plan([candidate(B, { pipeline: blocked() })], { base: { value: OWN_A, revision: 5, source: 'server' } })
      expect(p.review).toMatchObject([{ reason: 'base-advanced' }])
    })

    it('rejects an injected fingerprint collision on the predecessor\'s length', () => {
      const p = plan([candidate(B, { pipeline: blocked() })], {
        base: { value: 'Saved note. Z' + 'z', revision: 6, source: 'server' },
        fingerprint: () => 'f'.repeat(32),
      })
      expect(p.restore).toBeNull()
    })
  })
})
