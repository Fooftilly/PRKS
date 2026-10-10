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

  it('clears a draft the server stores as the note when the kind stores text trimmed (#534)', () => {
    const stored = (text: string) => text.trim()
    expect(plan([candidate(SERVER + '\n')], { stored })).toMatchObject({ cleanup: ['d-1'], restore: null, review: [] })
    // A Work note is stored exactly: a trailing newline is a change.
    expect(plan([candidate(SERVER + '\n')]).cleanup).toEqual([])
  })

  it('leaves a draft already queued as that exact row for the row\'s acknowledgement', () => {
    const pipeline: DraftPipeline = { state: 'queued', queuedOpId: 'op-1', queuedGeneration: 4, blockedBase: null, ownQueued: null }
    const p = plan([candidate('Queued body', { pipeline })], { queue: [{ opId: 'op-1', text: 'Queued body' }], base: { ...K, source: 'cache' } })
    expect(p.represented).toEqual([{ draftId: 'd-1', opId: 'op-1', generation: 4, text: 'Queued body', pageInstanceId: 'p-old' }])
    expect(p.restore).toBeNull()
    expect(p.review).toEqual([])
  })

  it('never leaves an uncertain owner\'s queued body for the acknowledgement to clear', () => {
    const pipeline: DraftPipeline = { state: 'queued', queuedOpId: 'op-1', queuedGeneration: 4, blockedBase: null, ownQueued: null }
    const p = plan([candidate('Queued body', { pipeline }, 'unknown')], { queue: [{ opId: 'op-1', text: 'Queued body' }] })
    expect(p.represented).toEqual([])
    expect(p.review).toMatchObject([{ reason: 'ownership-unknown' }])
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
    expect(p.restore).toBeNull()
    expect(p.review.map((r) => [r.draftId, r.reason])).toEqual([
      ['d-live', 'live-elsewhere'],
      ['d-1', 'other-draft-live'],
    ])
  })

  it('reports a live draft elsewhere even when it is the only one', () => {
    const p = plan([candidate(null, { draftId: 'd-live' }, 'other-live')])
    expect(p.review).toMatchObject([{ draftId: 'd-live', reason: 'live-elsewhere' }])
  })

  it('counts an unreadable draft as a second draft', () => {
    const p = plan([candidate('Saved note. More'), candidate(null, { draftId: 'd-2' })])
    expect(p.restore).toBeNull()
    expect(p.review.map((r) => [r.draftId, r.reason])).toEqual([
      ['d-2', 'body-missing'],
      ['d-1', 'multiple-drafts'],
    ])
  })

  it('neither cleans up, leaves as represented nor restores when the queue could not be read', () => {
    expect(plan([candidate('Saved note. More')], { queue: null }).review).toMatchObject([{ reason: 'queue-unknown' }])
    const equal = plan([candidate(SERVER)], { queue: null })
    expect(equal.cleanup).toEqual([])
    expect(equal.review).toMatchObject([{ reason: 'queue-unknown' }])
  })

  it('does not clear a body equal to the server note while a queued row can still replace that note', () => {
    const pipeline: DraftPipeline = { state: 'blocked', queuedOpId: null, queuedGeneration: 0, blockedBase: identity(SERVER, 5), ownQueued: ownQueued() }
    const p = plan([candidate(SERVER, { pipeline })], { queue: [{ opId: 'op-a', text: OWN_A }] })
    expect(p.cleanup).toEqual([])
    // Typed back to the note behind its own predecessor: restored, so it saves after that row.
    expect(p.restore).toMatchObject({ body: SERVER, state: 'blocked', predecessor: { opId: 'op-a' } })
    const foreign = plan([candidate(SERVER)], { queue: [{ opId: 'op-x', text: 'Other' }] })
    expect(foreign.cleanup).toEqual([])
    expect(foreign.review).toMatchObject([{ reason: 'foreign-queue' }])
  })

  it('restores a closed tab\'s draft and another pane\'s orphan; never one of unknown ownership', () => {
    const unknown = plan([candidate('x', {}, 'unknown')])
    expect(unknown.restore).toBeNull()
    expect(unknown.review).toMatchObject([{ reason: 'ownership-unknown', action: null }])
    expect(plan([candidate('Saved note. Closed tab', {}, 'dead-runtime')]).restore).toMatchObject({ body: 'Saved note. Closed tab' })
    const otherPane = record().owner
    const fromTab2 = plan([candidate('Saved note. Tab 2', { owner: { ...otherPane, paneId: 'tab-2' } })])
    expect(fromTab2.restore).toMatchObject({ body: 'Saved note. Tab 2' })
    expect(fromTab2.review).toEqual([])
  })

  it('offers each reviewed draft the one action it allows, judged on its own', () => {
    const two = plan([candidate('Saved note. One'), candidate('Saved note. Two', { draftId: 'd-2' }, 'dead-runtime')])
    expect(two.restore).toBeNull()
    expect(two.review.map((r) => [r.draftId, r.reason, r.action])).toEqual([
      ['d-1', 'multiple-drafts', 'restore'],
      ['d-2', 'multiple-drafts', 'restore'],
    ])
    // The note moved on, a foreign row is queued, or the base is not verified: compare first.
    expect(plan([candidate('x')], { base: { value: 'Other', revision: 6, source: 'server' } }).review)
      .toMatchObject([{ reason: 'base-advanced', action: 'reconcile' }])
    expect(plan([candidate('x')], { queue: [{ opId: 'op-9', text: 'Foreign' }] }).review)
      .toMatchObject([{ reason: 'foreign-queue', action: 'reconcile' }])
    expect(plan([candidate('x')], { base: { ...K, source: 'cache' } }).review)
      .toMatchObject([{ reason: 'base-unverified', action: 'reconcile' }])
    // The editor already shows other text: a restore would replace it.
    const dirty = plan([candidate('Saved note. One'), candidate('Saved note. Two', { draftId: 'd-2' })], { editorDirty: true })
    expect(dirty.review.map((r) => r.action)).toEqual(['reconcile', 'reconcile'])
    const one = plan([candidate('Saved note. One')], { editorDirty: true })
    expect(one.restore).toBeNull()
    expect(one.review).toMatchObject([{ reason: 'editor-dirty', action: 'reconcile' }])
    // Inspect and copy only.
    expect(plan([candidate('x')], { queue: null }).review).toMatchObject([{ reason: 'queue-unknown', action: null }])
    // An unverified base does not hide an unreadable queue: no Compare either.
    expect(plan([candidate('x')], { queue: null, base: null }).review).toMatchObject([{ reason: 'base-unverified', action: null }])
    expect(plan([candidate('x')], { queue: null, editorDirty: true }).review).toMatchObject([{ action: null }])
    expect(plan([candidate('x')], { otherDirtySession: true }).review).toMatchObject([{ reason: 'dirty-session', action: null }])
    expect(plan([candidate('x', { status: 'tail-missing' })]).review).toMatchObject([{ reason: 'tail-missing', status: 'tail-missing', action: null }])
    expect(plan([candidate(null)]).review).toMatchObject([{ reason: 'body-missing', action: null }])
    expect(plan([candidate('x', {}, 'other-live')]).review).toMatchObject([{ reason: 'live-elsewhere', action: null }])
  })

  it('keeps a missing-tail draft for review even when its stored body equals the note or the queued row', () => {
    const equal = plan([candidate(SERVER, { status: 'tail-missing' })])
    expect(equal.cleanup).toEqual([])
    expect(equal.review).toMatchObject([{ reason: 'tail-missing' }])
    const pipeline: DraftPipeline = { state: 'queued', queuedOpId: 'op-1', queuedGeneration: 4, blockedBase: null, ownQueued: null }
    const queued = plan([candidate('Queued body', { status: 'tail-missing', pipeline })], { queue: [{ opId: 'op-1', text: 'Queued body' }] })
    expect(queued.represented).toEqual([])
    expect(queued.review).toMatchObject([{ reason: 'tail-missing' }])
    // And it keeps another draft from restoring on its own.
    const both = plan([candidate('Saved note. More'), candidate(SERVER, { draftId: 'd-2', status: 'tail-missing' })])
    expect(both.restore).toBeNull()
    expect(both.review.map((r) => [r.draftId, r.reason])).toEqual([
      ['d-2', 'tail-missing'],
      ['d-1', 'multiple-drafts'],
    ])
  })

  it('cleans up an equal draft only from a lineage no live editor can own', () => {
    expect(plan([candidate(SERVER, {}, 'dead-runtime')]).cleanup).toEqual(['d-1'])
    const unknown = plan([candidate(SERVER, {}, 'unknown')])
    expect(unknown.cleanup).toEqual([])
    expect(unknown.review).toMatchObject([{ reason: 'ownership-unknown' }])
    // An uncertain owner keeps another draft from restoring.
    const both = plan([candidate('Saved note. More'), candidate(SERVER, { draftId: 'd-2' }, 'unknown')])
    expect(both.restore).toBeNull()
    expect(both.review.map((r) => r.reason)).toEqual(['multiple-drafts', 'multiple-drafts'])
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

    it('rejects a server note of the predecessor\'s length whose fingerprint differs', () => {
      const sameLength = 'Saved note. Z'
      expect(sameLength.length).toBe(OWN_A.length)
      const p = plan([candidate(B, { pipeline: blocked() })], { base: { value: sameLength, revision: 6, source: 'server' } })
      expect(p.restore).toBeNull()
      expect(p.review).toMatchObject([{ reason: 'base-advanced' }])
    })

    it('rejects a queued row of the predecessor\'s length whose fingerprint differs', () => {
      const p = plan([candidate(B, { pipeline: blocked() })], { queue: [{ opId: 'op-a', text: 'Saved note. Z' }] })
      expect(p.review).toMatchObject([{ reason: 'foreign-queue' }])
    })

    it('resumes on what the server stores for its predecessor, never on the raw text alone (#534)', () => {
      const raw = OWN_A + '\n'
      const trimmed = { ...ownQueued('op-a', raw)!, storedLength: OWN_A.length, storedFingerprint: fingerprintText(OWN_A) }
      const stored = (text: string) => text.trim()
      const acked = { base: { value: OWN_A, revision: 6, source: 'server' as const }, stored }
      expect(plan([candidate(B, { pipeline: blocked({ ownQueued: trimmed }) })], acked).restore)
        .toMatchObject({ body: B, predecessor: null })
      // Without the stored identity the raw text is all it has, which the server does not hold.
      expect(plan([candidate(B, { pipeline: blocked({ ownQueued: ownQueued('op-a', raw) }) })], acked).review)
        .toMatchObject([{ reason: 'base-advanced' }])
      // The queued row is still matched by its exact text.
      expect(plan([candidate(B, { pipeline: blocked({ ownQueued: trimmed }) })], { queue: [{ opId: 'op-a', text: OWN_A }], stored }).review)
        .toMatchObject([{ reason: 'foreign-queue' }])
    })
  })
})
