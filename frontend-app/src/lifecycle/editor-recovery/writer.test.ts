import { describe, expect, it } from 'vitest'
import type { EmergencyStorage } from './emergency'
import { RECOVERY_DB_NAME, BODIES_STORE, LARGE_BODY_CHARS, emergencyKeyOf, type DraftBase, type EmergencyPayload } from './schema'
import { createRecoveryStore, type RecoveryStore, type WriteGenerationInput, type WriteOutcome } from './store'
import { createFakeIdb, createManualScheduler, settle } from './test-support/fake-idb'
import { createWriterRegistry, type DraftWriter, type WriterEvent, type WriterRegistry } from './writer'

function listenerTarget(visibilityState = 'visible') {
  const listeners = new Map<string, Set<(e: Event) => void>>()
  return {
    visibilityState,
    addEventListener(type: string, fn: (e: Event) => void) {
      if (!listeners.has(type)) listeners.set(type, new Set())
      listeners.get(type)!.add(fn)
    },
    removeEventListener(type: string, fn: (e: Event) => void) {
      listeners.get(type)?.delete(fn)
    },
    count: (type: string) => listeners.get(type)?.size ?? 0,
    fire(type: string, event: Partial<Event> = {}) {
      for (const fn of [...(listeners.get(type) ?? [])]) fn({ type, preventDefault() {}, ...event } as Event)
    },
  }
}

function memoryStorage(): EmergencyStorage & { map: Map<string, string> } {
  const map = new Map<string, string>()
  return {
    map,
    get length() {
      return map.size
    },
    key: (i: number) => [...map.keys()][i] ?? null,
    getItem: (k: string) => map.get(k) ?? null,
    setItem: (k: string, v: string) => void map.set(k, v),
    removeItem: (k: string) => void map.delete(k),
  }
}

const BASE: DraftBase = { revision: 7, length: 3, fingerprint: 'a'.repeat(32), source: 'server' }

function setup(options: { gate?: boolean; storage?: EmergencyStorage | null } = {}) {
  const idb = createFakeIdb()
  const realStore = createRecoveryStore({ indexedDB: idb.factory })
  const calls: WriteGenerationInput[] = []
  const gates: Array<() => void> = []
  const store: RecoveryStore = {
    ...realStore,
    writeGeneration(input) {
      calls.push(input)
      if (!options.gate) return realStore.writeGeneration(input)
      return new Promise<WriteOutcome>((resolve, reject) => {
        gates.push(() => realStore.writeGeneration(input).then(resolve, reject))
      })
    },
  }
  const scheduler = createManualScheduler()
  const win = listenerTarget()
  const doc = listenerTarget('hidden')
  const storage = memoryStorage()
  const events: WriterEvent[] = []
  const registry = createWriterRegistry({
    store,
    identity: { pageInstanceId: 'p-me', current: () => ({ runtimeId: 'r-me', verified: 'lock' }), setLineageResponder() {} },
    scheduler,
    now: scheduler.now,
    window: win,
    document: doc,
    emergencyStorage: options.storage === undefined ? storage : options.storage,
    onEvent: (e) => events.push(e),
  })
  const open = (sessionKey = 's-1', entityId = 'w1') =>
    registry.openWriter({ kind: 'work-research-note', entityType: 'work', entityId, paneId: 'tab-1', sessionKey, base: BASE })
  const releaseGate = async () => {
    const gate = gates.shift()
    if (gate) gate()
    await settle()
  }
  return { idb, store: realStore, calls, scheduler, win, doc, storage, events, registry, open, releaseGate }
}

/** INV-DRAFT-1: every pending generation is committed, held by the emergency plan, or leave-guarded. */
function expectProtectedOrDisclosed(registry: WriterRegistry): void {
  for (const w of registry.writers()) {
    if (w.state() === 'pending' || w.state() === 'unprotected') {
      expect(w.heldByEmergency() || (w.needsLeaveGuard() && registry.leaveGuardActive())).toBe(true)
    }
    if (w.state() === 'unprotected') expect(registry.leaveGuardActive()).toBe(true)
  }
}

describe('coalescing writer', () => {
  it('writes an ordinary body after 300 ms idle, only the newest generation', async () => {
    const t = setup()
    const w = t.open()
    w.edit(1, 'a')
    w.edit(2, 'ab')
    w.edit(3, 'abc')
    expect(w.state()).toBe('pending')
    expectProtectedOrDisclosed(t.registry)
    t.scheduler.advance(299)
    await settle()
    expect(t.calls).toHaveLength(0)
    t.scheduler.advance(1)
    await settle()
    expect(t.calls.map((c) => c.generation)).toEqual([3])
    expect(w.state()).toBe('protected')
    expect(await t.store.getBody(w.draftId() as string)).toMatchObject({ generation: 3, body: 'abc' })
  })

  it('writes at most 1 s after the first unrecorded change while typing continues', async () => {
    const t = setup()
    const w = t.open()
    for (let g = 1; g <= 6; g++) {
      w.edit(g, 'x'.repeat(g))
      t.scheduler.advance(200)
    }
    await settle()
    expect(t.calls.map((c) => c.generation)).toEqual([5])
  })

  it('keeps one write in flight and writes only the newest pending generation next', async () => {
    const t = setup({ gate: true })
    const w = t.open()
    w.edit(1, 'one')
    void w.flush()
    w.edit(2, 'two')
    w.edit(3, 'three')
    w.edit(4, 'four')
    w.edit(3, 'stale three')
    w.edit(4, 'equal four')
    t.scheduler.advance(300)
    expect(t.calls.map((c) => c.generation)).toEqual([1])
    await t.releaseGate()
    expect(t.calls.map((c) => c.generation)).toEqual([1, 4])
    await t.releaseGate()
    expect(w.state()).toBe('protected')
    expect(await t.store.getBody(w.draftId() as string)).toMatchObject({ generation: 4, body: 'four' })
  })

  it('starts a large write on the next task and guards it until it commits', async () => {
    const t = setup({ gate: true })
    const w = t.open()
    const large = 'L'.repeat(LARGE_BODY_CHARS + 1)
    w.edit(1, large)
    // Armed synchronously, before any write and before any unload event.
    expect(t.registry.leaveGuardActive()).toBe(true)
    expect(t.win.count('beforeunload')).toBe(1)
    expect(w.heldByEmergency()).toBe(false)
    expectProtectedOrDisclosed(t.registry)
    t.scheduler.advance(0)
    expect(t.calls.map((c) => c.generation)).toEqual([1])
    w.edit(2, large + 'M')
    t.scheduler.advance(0)
    expect(t.calls).toHaveLength(1)
    await t.releaseGate()
    expect(t.calls.map((c) => c.generation)).toEqual([1, 2])
    expect(t.registry.leaveGuardActive()).toBe(true)
    await t.releaseGate()
    expect(w.state()).toBe('protected')
    expect(t.registry.leaveGuardActive()).toBe(false)
    expect(t.win.count('beforeunload')).toBe(0)
  })

  it('registers unload listeners only while something is pending', async () => {
    const t = setup()
    const w = t.open()
    expect(t.win.count('pagehide') + t.doc.count('visibilitychange') + t.win.count('beforeunload')).toBe(0)
    w.edit(1, 'a')
    expect(t.win.count('pagehide')).toBe(1)
    expect(t.doc.count('visibilitychange')).toBe(1)
    expect(t.win.count('beforeunload')).toBe(0)
    t.scheduler.advance(300)
    await settle()
    expect(t.win.count('pagehide') + t.doc.count('visibilitychange') + t.win.count('beforeunload')).toBe(0)
  })
})

describe('emergency entry', () => {
  it('holds a pending first generation with its lineage, and removes the key after commit', async () => {
    const t = setup()
    const w = t.open()
    w.edit(1, 'first words')
    t.doc.fire('visibilitychange')
    const raw = t.storage.map.get(emergencyKeyOf('p-me'))
    const payload = JSON.parse(raw as string) as EmergencyPayload
    expect(payload).toMatchObject({ v: 1, pageInstanceId: 'p-me', runtimeId: 'r-me' })
    expect(payload.entries[0]).toMatchObject({
      draftId: w.draftId(),
      generation: 1,
      committedGeneration: 0,
      body: 'first words',
      lineage: { owner: { pageInstanceId: 'p-me', runtimeId: 'r-me', paneId: 'tab-1' }, base: BASE },
    })
    t.scheduler.advance(300)
    await settle()
    expect(t.storage.map.size).toBe(0)
    w.edit(2, 'first words and more')
    t.win.fire('pagehide')
    const next = JSON.parse(t.storage.map.get(emergencyKeyOf('p-me')) as string) as EmergencyPayload
    expect(next.entries[0]).toMatchObject({ generation: 2, committedGeneration: 1 })
    expect(next.entries[0]!.lineage).toBeUndefined()
  })

  it('guards writers that the page budget cannot hold', () => {
    const t = setup()
    const writers: DraftWriter[] = []
    for (let i = 0; i < 5; i++) {
      const w = t.open('s-' + i, 'w' + i)
      w.edit(1, String(i).repeat(250_000))
      writers.push(w)
    }
    expect(writers.filter((w) => w.heldByEmergency())).toHaveLength(4)
    expect(t.registry.leaveGuardActive()).toBe(true)
    expectProtectedOrDisclosed(t.registry)
    t.registry.writeEmergencyNow()
    const payload = JSON.parse(t.storage.map.get(emergencyKeyOf('p-me')) as string) as EmergencyPayload
    expect(payload.entries.filter((e) => e.body === null)).toHaveLength(1)
  })
})

describe('emergency storage that cannot keep a body', () => {
  function throwingStorage(limit: number): EmergencyStorage & { map: Map<string, string> } {
    const inner = memoryStorage()
    return {
      ...inner,
      setItem(k: string, v: string) {
        if (v.length > limit) throw new DOMException('full', 'QuotaExceededError')
        inner.setItem(k, v)
      },
    }
  }

  it('guards a pending body when there is no localStorage', () => {
    const t = setup({ storage: null })
    const w = t.open()
    w.edit(1, 'first words')
    expect(w.heldByEmergency()).toBe(false)
    expect(t.registry.leaveGuardActive()).toBe(true)
    expect(t.registry.writeEmergencyNow()).toBe('unavailable')
    expect(t.registry.leaveGuardActive()).toBe(true)
    expectProtectedOrDisclosed(t.registry)
  })

  it('guards a pending body when localStorage is blocked', () => {
    const t = setup({ storage: throwingStorage(-1) })
    const w = t.open()
    w.edit(1, 'first words')
    expect(w.heldByEmergency()).toBe(false)
    expect(t.registry.leaveGuardActive()).toBe(true)
    expect(t.registry.writeEmergencyNow()).toBe('failed')
    expect(t.registry.leaveGuardActive()).toBe(true)
  })

  it('arms the guard once an emergency write had to drop bodies, and lifts it after a full write', async () => {
    const storage = throwingStorage(1500)
    const t = setup({ storage })
    const w = t.open()
    w.edit(1, 'short')
    expect(w.heldByEmergency()).toBe(true)
    expect(t.registry.leaveGuardActive()).toBe(false)
    w.edit(2, 'y'.repeat(3000))
    expect(t.registry.writeEmergencyNow()).toBe('written-without-bodies')
    expect(w.heldByEmergency()).toBe(false)
    expect(t.registry.leaveGuardActive()).toBe(true)
    expectProtectedOrDisclosed(t.registry)
    w.edit(3, 'short again')
    expect(t.registry.leaveGuardActive()).toBe(true)
    expect(t.registry.writeEmergencyNow()).toBe('written')
    expect(w.heldByEmergency()).toBe(true)
    expect(t.registry.leaveGuardActive()).toBe(false)
    t.scheduler.advance(300)
    await settle()
    expect(w.state()).toBe('protected')
    expect(t.registry.leaveGuardActive()).toBe(false)
  })
})

describe('ownership and failure', () => {
  it('moves to a fresh lineage when another page adopted its lineage', async () => {
    const t = setup()
    const w = t.open()
    w.edit(1, 'mine')
    await w.flush()
    const first = w.draftId() as string
    const adopted = await t.store.adopt(first, 'p-me', { runtimeId: 'r-x', pageInstanceId: 'p-other', paneId: 'tab-1', claimedAt: 0 })
    expect(adopted.outcome).toBe('ok')
    w.edit(2, 'mine and newer')
    await w.flush()
    await settle()
    expect(w.draftId()).not.toBe(first)
    expect(t.events.find((e) => e.type === 'ownership-lost')).toMatchObject({ oldDraftId: first, newDraftId: w.draftId(), reason: 'not-owner' })
    expect(await t.store.getBody(first)).toMatchObject({ generation: 1, body: 'mine' })
    expect(await t.store.getBody(w.draftId() as string)).toMatchObject({ generation: 2, body: 'mine and newer' })
    expect((await t.store.get(w.draftId() as string))?.base).toEqual(BASE)
  })

  it('reports unprotected on a quota failure, guards, and retries with backoff', async () => {
    const t = setup()
    const w = t.open()
    t.idb.failCommits = 2
    w.edit(1, 'text')
    t.scheduler.advance(300)
    await settle()
    expect(w.state()).toBe('unprotected')
    expect(t.events.find((e) => e.type === 'unprotected')).toMatchObject({ code: 'quota' })
    expect(t.registry.leaveGuardActive()).toBe(true)
    expectProtectedOrDisclosed(t.registry)
    t.scheduler.advance(2000)
    await settle()
    expect(w.state()).toBe('unprotected')
    t.scheduler.advance(3999)
    await settle()
    expect(w.state()).toBe('unprotected')
    t.scheduler.advance(1)
    await settle()
    expect(w.state()).toBe('protected')
    expect(t.registry.leaveGuardActive()).toBe(false)
    expect(t.idb.rows(RECOVERY_DB_NAME, BODIES_STORE)).toHaveLength(1)
  })

  it('release finishes the last write instead of cancelling it and leaves the lineage', async () => {
    const t = setup()
    const w = t.open()
    w.edit(1, 'typed just before release')
    await w.release()
    expect(await t.store.getBody(w.draftId() as string)).toMatchObject({ body: 'typed just before release' })
    expect(t.registry.writers()).toHaveLength(0)
    expect(t.registry.ownerOf(w.draftId() as string)).toBeNull()
    expect(t.scheduler.pending()).toBe(0)
    expect(t.win.count('pagehide') + t.doc.count('visibilitychange') + t.win.count('beforeunload')).toBe(0)
  })
})

describe('release after a failed write', () => {
  it('stays registered, guarded and retrying until the last generation commits', async () => {
    const t = setup()
    const w = t.open()
    t.idb.failCommits = 1
    w.edit(1, 'typed before a failing release')
    await w.release()
    expect(w.state()).toBe('unprotected')
    expect(t.registry.writers()).toHaveLength(1)
    expect(t.registry.leaveGuardActive()).toBe(true)
    w.edit(2, 'ignored after release')
    t.scheduler.advance(2000)
    await settle()
    expect(t.registry.writers()).toHaveLength(0)
    expect(t.registry.leaveGuardActive()).toBe(false)
    expect(await t.store.getBody(w.draftId() as string)).toMatchObject({ generation: 1, body: 'typed before a failing release' })
  })
})

describe('acknowledgement', () => {
  it('clears only the exact acknowledged generation and body', async () => {
    const t = setup()
    const w = t.open()
    w.edit(1, 'saved body')
    await w.flush()
    const id = w.draftId() as string
    expect(await w.acknowledged(1, 'saved bodY')).toBe('kept')
    expect(await w.acknowledged(1, 'saved body')).toBe('deleted')
    expect(await t.store.get(id)).toBeNull()
    expect(w.state()).toBe('clean')
    expect(w.draftId()).toBeNull()
  })

  it('keeps a newer committed generation when an older one is acknowledged', async () => {
    const t = setup()
    const w = t.open()
    w.edit(1, 'one')
    await w.flush()
    w.edit(2, 'one two')
    await w.flush()
    expect(await w.acknowledged(1, 'one')).toBe('kept')
    expect(await t.store.getBody(w.draftId() as string)).toMatchObject({ generation: 2, body: 'one two' })
  })

  it('moves a newer pending generation to a fresh lineage when the committed one is acknowledged', async () => {
    const t = setup()
    const w = t.open()
    w.edit(1, 'one')
    await w.flush()
    const first = w.draftId() as string
    w.edit(2, 'one two')
    expect(await w.acknowledged(1, 'one')).toBe('deleted')
    expect(w.state()).toBe('pending')
    expect(w.draftId()).not.toBe(first)
    expectProtectedOrDisclosed(t.registry)
    await w.flush()
    expect(await t.store.getBody(w.draftId() as string)).toMatchObject({ generation: 2, body: 'one two' })
  })

  it('writes the next edit after a clear to a fresh lineage', async () => {
    const t = setup()
    const w = t.open()
    w.edit(1, 'one')
    await w.flush()
    const first = w.draftId()
    await w.acknowledged(1, 'one')
    w.edit(2, 'one two')
    await w.flush()
    expect(w.draftId()).not.toBe(first)
    expect(t.events.some((e) => e.type === 'ownership-lost')).toBe(false)
  })
})

describe('adoption', () => {
  it('adopts an orphan by compare-and-set and continues its generations', async () => {
    const t = setup()
    await t.store.writeGeneration({
      draftId: 'd-orphan',
      pageInstanceId: 'p-before-reload',
      generation: 9,
      body: 'orphan',
      create: {
        kind: 'work-research-note',
        entityType: 'work',
        entityId: 'w1',
        owner: { runtimeId: 'r-me', pageInstanceId: 'p-before-reload', paneId: 'tab-2', claimedAt: 0 },
        base: BASE,
      },
    })
    const record = (await t.store.get('d-orphan'))!
    const [a, b] = await Promise.all([
      t.registry.adopt(record, { paneId: 'tab-1', sessionKey: 's-a' }),
      t.registry.adopt(record, { paneId: 'tab-3', sessionKey: 's-b' }),
    ])
    const winner = (a || b) as DraftWriter
    expect([a, b].filter(Boolean)).toHaveLength(1)
    expect(t.registry.ownerOf('d-orphan')).toBe(winner.sessionKey)
    winner.edit(9, 'ignored, not newer')
    winner.edit(10, 'orphan continued')
    await winner.flush()
    expect(await t.store.getBody('d-orphan')).toMatchObject({ generation: 10, body: 'orphan continued' })
  })
})
