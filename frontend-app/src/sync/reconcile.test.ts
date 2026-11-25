import { describe, expect, it } from 'vitest'
import {
  adoptFresh,
  applyOwnerAck,
  carryObservation,
  compareRevisions,
  decideAck,
  entityKey,
  isRevision,
  observe,
  observedAt,
  overlay,
  pendingFor,
  pinEditBase,
  pinnedEdits,
  planAck,
  readBefore,
  timeOf,
} from './reconcile'

describe('revisions', () => {
  it('are non-negative safe integers', () => {
    expect(isRevision(0)).toBe(true)
    expect(isRevision(7)).toBe(true)
    for (const bad of [-1, 1.5, Number.MAX_SAFE_INTEGER + 1, '3', null, undefined, NaN]) {
      expect(isRevision(bad)).toBe(false)
    }
  })

  it('compare numerically', () => {
    expect(compareRevisions(1, 2)).toBeLessThan(0)
    expect(compareRevisions(2, 2)).toBe(0)
    expect(compareRevisions(10, 9)).toBeGreaterThan(0)
    expect(() => compareRevisions(-1, 2)).toThrow(TypeError)
  })

  it('share the offline runtime entity key', () => {
    expect(entityKey('work', 'W-1')).toBe('work\u0000W-1')
  })
})

describe('decideAck with a held revision', () => {
  it('applies an equal or newer ACK', () => {
    expect(decideAck({ ackRevision: 3, heldRevision: 3 })).toBe('apply')
    expect(decideAck({ ackRevision: 4, heldRevision: 3 })).toBe('apply')
    expect(decideAck({ ackRevision: 1, heldRevision: 0 })).toBe('apply')
  })

  it('never lets an older ACK replace a newer value', () => {
    expect(decideAck({ ackRevision: 1, heldRevision: 2 })).toBe('superseded')
    // A held revision outranks agreement and read time.
    expect(decideAck({ ackRevision: 1, heldRevision: 2, agrees: true, observedAt: 1, operationCreatedAt: 5 }))
      .toBe('superseded')
  })

  it('treats a malformed held revision as no proof', () => {
    expect(decideAck({ ackRevision: 1, heldRevision: -4 })).toBe('unproven')
  })

  it('refuses an ACK without a revision', () => {
    expect(() => decideAck({ ackRevision: null })).toThrow(TypeError)
  })
})

describe('decideAck without a held revision', () => {
  const created = '2026-11-25T10:00:00.000Z'
  const before = Date.parse(created) - 1
  const after = Date.parse(created) + 1

  it('applies when the value was read before the operation existed', () => {
    expect(decideAck({ ackRevision: 1, observedAt: before, operationCreatedAt: created })).toBe('apply')
  })

  it('is unproven when the value was read at or after the operation was created', () => {
    expect(decideAck({ ackRevision: 1, observedAt: after, operationCreatedAt: created })).toBe('unproven')
    expect(decideAck({ ackRevision: 1, observedAt: Date.parse(created), operationCreatedAt: created }))
      .toBe('unproven')
  })

  it('is unproven when either time is unknown', () => {
    expect(decideAck({ ackRevision: 1, operationCreatedAt: created })).toBe('unproven')
    expect(decideAck({ ackRevision: 1, observedAt: before })).toBe('unproven')
    expect(decideAck({ ackRevision: 1, observedAt: before, operationCreatedAt: 'not a time' })).toBe('unproven')
  })

  it('needs no proof when the value already shows the ACK', () => {
    expect(decideAck({ ackRevision: 1, observedAt: after, operationCreatedAt: created, agrees: true })).toBe('apply')
  })
})

describe('times', () => {
  it('accept epoch ms and ISO strings only', () => {
    expect(timeOf(5)).toBe(5)
    expect(timeOf('1970-01-01T00:00:00.010Z')).toBe(10)
    expect(timeOf('')).toBeNull()
    expect(timeOf(Infinity)).toBeNull()
    expect(timeOf({})).toBeNull()
    expect(readBefore(9, '1970-01-01T00:00:00.010Z')).toBe(true)
    expect(readBefore(10, 10)).toBe(false)
  })
})

describe('pending overlay', () => {
  const ops = [
    { op_id: 'b', sequence: 2, status: 'pending', target: 'x', add: 2 },
    { op_id: 'a', sequence: 1, status: 'syncing', target: 'x', add: 1 },
    { op_id: 'c', sequence: 3, status: 'acknowledged', target: 'x', add: 100 },
    { op_id: 'd', sequence: 4, status: 'conflict', target: 'y', add: 1000 },
    { op_id: 'e', sequence: 5, status: 'conflict', target: 'x', add: 3 },
  ]
  const onX = (op: { target: string }) => op.target === 'x'

  it('keeps unsettled operations for the target in queue order', () => {
    expect(pendingFor(ops, onX).map((op) => op.op_id)).toEqual(['a', 'b', 'e'])
    expect(pendingFor(ops, onX, 'b').map((op) => op.op_id)).toEqual(['a', 'e'])
    expect(pendingFor<(typeof ops)[number]>(null, onX)).toEqual([])
  })

  it('folds pending operations over the acknowledged base, oldest first', () => {
    const order: string[] = []
    const value = overlay(10, ops, onX, (sum, op) => {
      order.push(op.op_id)
      return sum * 10 + op.add
    })
    expect(order).toEqual(['a', 'b', 'e'])
    expect(value).toBe(((10 * 10 + 1) * 10 + 2) * 10 + 3)
  })

  it('keeps arrival order when sequences are missing', () => {
    const rows = [
      { op_id: 'second', status: 'pending' },
      { op_id: 'first', status: 'pending' },
    ]
    expect(pendingFor(rows, () => true).map((op) => op.op_id)).toEqual(['second', 'first'])
  })
})

describe('observations', () => {
  it('belong to one object and survive only an explicit carry', () => {
    const read = observe({ id: 'W-1', tags: [] }, 42)
    expect(observedAt(read)).toBe(42)
    const copied = { ...read, tags: ['T-1'] }
    expect(observedAt(copied)).toBeNull()
    expect(observedAt(carryObservation(read, copied))).toBe(42)
    expect(observedAt(null)).toBeNull()
    expect(observedAt('x')).toBeNull()
    expect(observe(null, 1)).toBeNull()
    const untimed = observe({}, 'never')
    expect(observedAt(untimed)).toBeNull()
  })
})

describe('planAck', () => {
  const created = 1000

  it('lets a sibling revision prove an ACK superseded', () => {
    expect(planAck({ ackRevision: 1, heldRevision: 2, agrees: false, observedAt: 1, operationCreatedAt: created }))
      .toBe('superseded')
  })

  it('never lets a sibling revision prove the value current', () => {
    // Options at revision 1 say "apply", but the detail was read after the op.
    expect(planAck({ ackRevision: 1, heldRevision: 1, observedAt: created + 5, operationCreatedAt: created }))
      .toBe('unproven')
    expect(planAck({ ackRevision: 1, heldRevision: 0, observedAt: created - 5, operationCreatedAt: created }))
      .toBe('apply')
  })

  it('needs no write when the value already agrees', () => {
    expect(planAck({ ackRevision: 3, agrees: true })).toBe('unchanged')
  })
})

describe('adoptFresh', () => {
  it('adopts a server answer while the owner still holds the patched value', async () => {
    const held = { id: 'W' }
    let owner: unknown = held
    const adopted = await adoptFresh(async () => ({ value: { id: 'W', fresh: true }, source: 'server' }),
      () => owner, held, (value) => { owner = value })
    expect(adopted).toBe(true)
    expect(owner).toEqual({ id: 'W', fresh: true })
  })

  it('leaves an owner whose value changed meanwhile', async () => {
    const held = { id: 'W' }
    let owner: unknown = held
    const adopted = await adoptFresh(async () => {
      owner = { id: 'W', newer: true }
      return { value: { id: 'W', fresh: true }, source: 'server' }
    }, () => owner, held, (value) => { owner = value })
    expect(adopted).toBe(false)
    expect(owner).toEqual({ id: 'W', newer: true })
  })

  it('ignores cached, missing and failed reads', async () => {
    const held = {}
    const never = () => { throw new Error('must not adopt') }
    expect(await adoptFresh(async () => ({ value: {}, source: 'cache' }), () => held, held, never)).toBe(false)
    expect(await adoptFresh(async () => ({ value: null, source: 'server' }), () => held, held, never)).toBe(false)
    expect(await adoptFresh(async () => null, () => held, held, never)).toBe(false)
    expect(await adoptFresh(async () => { throw new Error('offline') }, () => held, held, never)).toBe(false)
  })
})

describe('applyOwnerAck', () => {
  type Work = { id: string; tags: string[] }
  function owner(initial: Work | null, fresh: Work | null = null) {
    const state = { value: initial as Work | null, reads: 0, adopted: 0 }
    const api = {
      get: () => state.value,
      set: (value: Work) => { state.value = value },
      agrees: (value: Work) => value.tags.includes('T'),
      patch: (value: Work) => ({ ...value, tags: [...value.tags, 'T'] }),
      read: async () => { state.reads += 1; return { value: fresh, source: 'server' } },
      adopted: () => { state.adopted += 1 },
    }
    return { state, api }
  }
  const flush = () => new Promise((resolve) => setTimeout(resolve, 0))

  it('patches a value read before the operation and keeps its read time', () => {
    const { state, api } = owner(observe({ id: 'W', tags: [] }, 10))
    expect(applyOwnerAck({ ackRevision: 1, operationCreatedAt: 20 }, api)).toBe('apply')
    expect(state.value?.tags).toEqual(['T'])
    expect(observedAt(state.value)).toBe(10)
    expect(state.reads).toBe(0)
  })

  it('skips a superseded ACK and an agreeing value', () => {
    const a = owner(observe({ id: 'W', tags: [] }, 10))
    expect(applyOwnerAck({ ackRevision: 1, heldRevision: 2, operationCreatedAt: 20 }, a.api)).toBe('superseded')
    expect(a.state.value?.tags).toEqual([])
    const b = owner({ id: 'W', tags: ['T'] })
    expect(applyOwnerAck({ ackRevision: 1 }, b.api)).toBe('unchanged')
    expect(owner(null).api.get()).toBeNull()
    expect(applyOwnerAck({ ackRevision: 1 }, owner(null).api)).toBe('unchanged')
  })

  it('patches an unproven value, then lets the server read correct it', async () => {
    const newer = { id: 'W', tags: ['N'] }
    const { state, api } = owner({ id: 'W', tags: [] }, newer)
    expect(applyOwnerAck({ ackRevision: 1, operationCreatedAt: 20 }, api)).toBe('unproven')
    expect(state.value?.tags).toEqual(['T'])
    await flush()
    expect(state.reads).toBe(1)
    expect(state.value).toBe(newer)
    expect(state.adopted).toBe(1)
  })

  it('adopts only the merged part of the re-read, stamped with its read time', async () => {
    type Held = Work & { folder: string }
    const newer = observe({ id: 'W', tags: ['N'], folder: 'server' }, 50)
    const state = { value: observe({ id: 'W', tags: [], folder: 'pending' } as Held, 30) as Held }
    const plan = applyOwnerAck<Held>({ ackRevision: 1, operationCreatedAt: 20 }, {
      get: () => state.value,
      set: (value) => { state.value = value },
      agrees: (value) => value.tags.includes('T'),
      patch: (value) => ({ ...value, tags: [...value.tags, 'T'] }),
      read: async () => ({ value: newer, source: 'server' }),
      merge: (held, fresh) => ({ ...held, tags: fresh.tags }),
    })
    expect(plan).toBe('unproven')
    await flush()
    expect(state.value).toEqual({ id: 'W', tags: ['N'], folder: 'pending' })
    expect(observedAt(state.value)).toBe(50)
  })
})

describe('edit-session bases', () => {
  const shown = { name: 'Group', description: 'old' }

  it('pins the revision read for each field the form shows', () => {
    const pinned = pinEditBase(shown, {
      name: { value: 'Group', revision: 3 },
      description: { value: 'old', revision: 0 },
    })
    expect(pinned).toEqual({ name: { value: 'Group', revision: 3 }, description: { value: 'old', revision: 0 } })
  })

  it('leaves a field unknown when the read already holds a newer value', () => {
    const pinned = pinEditBase(shown, {
      name: { value: 'Group', revision: 3 },
      description: { value: 'from another tab', revision: 1 },
      extra: { value: 'ignored', revision: 9 },
    })
    expect(pinned).toEqual({ name: { value: 'Group', revision: 3 }, description: null })
    expect(pinEditBase(shown, { name: { value: 'Group', revision: -1 } })).toEqual({ name: null, description: null })
    expect(pinEditBase(shown, null)).toBeNull()
  })

  it('pins an unsent edit of this device at the revision it was measured against', () => {
    const read = { name: { value: 'Group', revision: 3 }, description: { value: 'old', revision: 4 } }
    const showsPending = { name: 'Group', description: 'unsent' }
    expect(pinEditBase(showsPending, read, { description: { value: 'unsent', revision: 2 } })).toEqual({
      name: { value: 'Group', revision: 3 },
      description: { value: 'old', revision: 2 },
    })
    expect(pinEditBase(shown, read, { description: { value: 'unsent', revision: 2 } })?.description).toBeNull()
    expect(pinEditBase(shown, read, { description: 'malformed' })?.description).toBeNull()
  })

  it('sends changed fields against the pinned revision, never a newer one', () => {
    const pinned = { name: { value: 'Group', revision: 3 }, description: { value: 'old', revision: 0 } }
    expect(pinnedEdits(shown, { name: 'Group', description: 'mine' }, pinned)).toEqual({
      changes: { description: 'mine' },
      base: pinned,
      unknown: [],
    })
  })

  it('refuses a changed field whose base is unknown, but not an untouched one', () => {
    const pinned = { name: { value: 'Group', revision: 3 }, description: null }
    expect(pinnedEdits(shown, { name: 'Renamed', description: 'old' }, pinned).unknown).toEqual([])
    expect(pinnedEdits(shown, { name: 'Group', description: 'mine' }, pinned).unknown).toEqual(['description'])
    expect(pinnedEdits(shown, {}, pinned).changes).toEqual({})
  })
})
