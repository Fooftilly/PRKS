import { describe, expect, it, vi } from 'vitest'
import type { EmergencyStorage } from './emergency'
import { RECOVERY_DB_NAME, BODIES_STORE, LARGE_BODY_CHARS, emergencyKeyOf, reservationKeyOf, type DraftBase, type DraftPipeline, type EmergencyPayload } from './schema'
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

function setup(options: { gate?: boolean; storage?: EmergencyStorage | null; page?: string; settled?: () => boolean } = {}) {
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
    identity: {
      pageInstanceId: options.page ?? 'p-me',
      current: () => (!options.settled || options.settled() ? { runtimeId: 'r-me', verified: 'lock' } : null),
      setLineageResponder() {},
    },
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
    // A committed lineage still carries its base, for a fork after adoption.
    expect(next.entries[0]!.lineage).toMatchObject({ base: BASE })
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

  it('sizes the capacity proof by the serialized body, so an escape-heavy note stays guarded', () => {
    const storage = throwingStorage(4000)
    const t = setup({ storage })
    const w = t.open()
    // 1000 chars that serialize to 6000: fits by length, not once escaped.
    w.edit(1, '\u0001'.repeat(1000))
    expect(w.heldByEmergency()).toBe(false)
    expect(t.registry.leaveGuardActive()).toBe(true)
    w.edit(2, 'x'.repeat(1000))
    expect(w.heldByEmergency()).toBe(true)
    expect(t.registry.leaveGuardActive()).toBe(false)
  })

  it('reserves afresh for a new burst after the previous one ended', async () => {
    const inner = memoryStorage()
    let limit = 10_000
    const storage: EmergencyStorage & { map: Map<string, string> } = {
      ...inner,
      setItem(k: string, v: string) {
        if (v.length > limit) throw new DOMException('full', 'QuotaExceededError')
        inner.setItem(k, v)
      },
    }
    const t = setup({ storage })
    const w = t.open()
    w.edit(1, 'x'.repeat(1000))
    expect(w.heldByEmergency()).toBe(true)
    await w.flush()
    expect(t.registry.emergencyListenersActive()).toBe(false)
    limit = 1500
    t.win.fire('storage', { key: 'other-app-key', oldValue: null, newValue: 'y'.repeat(9000) } as unknown as Partial<Event>)
    w.edit(2, 'x'.repeat(1000))
    expect(w.heldByEmergency()).toBe(false)
    expect(t.registry.leaveGuardActive()).toBe(true)
  })

  it('overwrites the key with no entries when removing it is refused', async () => {
    const storage = memoryStorage()
    const t = setup({ storage: { ...storage, removeItem: () => { throw new DOMException('blocked', 'SecurityError') } } })
    const w = t.open()
    w.edit(1, 'first words')
    t.doc.fire('visibilitychange')
    await w.discard()
    const left = JSON.parse(storage.map.get(emergencyKeyOf('p-me')) as string) as EmergencyPayload
    expect(left.entries).toEqual([])
  })

  it('arms the guard once an emergency write had to drop bodies, and lifts it after a full write', async () => {
    const storage = throwingStorage(1500)
    const t = setup({ storage })
    const w = t.open()
    w.edit(1, 'short')
    expect(w.heldByEmergency()).toBe(true)
    expect(t.registry.leaveGuardActive()).toBe(false)
    w.edit(2, 'y'.repeat(3000))
    // The probe already showed the payload will not fit: guarded before any unload.
    expect(w.heldByEmergency()).toBe(false)
    expect(t.registry.leaveGuardActive()).toBe(true)
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

describe('quota reservation', () => {
  /** One origin's localStorage: a quota shared by every value and every page. */
  function quotaStorage(quota: number): EmergencyStorage & { map: Map<string, string> } {
    const map = new Map<string, string>()
    return {
      map,
      get length() {
        return map.size
      },
      key: (i: number) => [...map.keys()][i] ?? null,
      getItem: (k: string) => map.get(k) ?? null,
      setItem(k: string, v: string) {
        let used = v.length
        for (const [key, value] of map) if (key !== k) used += value.length
        if (used > quota) throw new DOMException('full', 'QuotaExceededError')
        map.set(k, v)
      },
      removeItem: (k: string) => void map.delete(k),
    }
  }
  const free = (storage: { map: Map<string, string> }, quota: number) =>
    quota - [...storage.map.values()].reduce((n, v) => n + v.length, 0)

  it('keeps the reserved quota away from another page, which stays guarded instead', () => {
    // Room for one page's reservation, not for two.
    const storage = quotaStorage(6000)
    const a = setup({ storage, page: 'p-a' })
    const b = setup({ storage, page: 'p-b' })
    const wa = a.open()
    wa.edit(1, 'a'.repeat(1000))
    expect(wa.heldByEmergency()).toBe(true)
    expect(a.registry.leaveGuardActive()).toBe(false)
    const wb = b.open()
    wb.edit(1, 'b'.repeat(1000))
    expect(wb.heldByEmergency()).toBe(false)
    expect(b.registry.leaveGuardActive()).toBe(true)
    // Both close, the guarded page first: whatever it manages to write, the
    // held page's payload still fits.
    b.registry.writeEmergencyNow()
    expect(a.registry.writeEmergencyNow()).toBe('written')
    const left = JSON.parse(storage.map.get(emergencyKeyOf('p-a')) as string) as EmergencyPayload
    expect(left.entries[0]!.body).toBe('a'.repeat(1000))
  })

  it('still writes the whole payload after this page fills every byte the reservation left', () => {
    const quota = 200_000
    const storage = quotaStorage(quota)
    const t = setup({ storage })
    const w = t.open()
    w.edit(1, 'x'.repeat(1000))
    expect(w.heldByEmergency()).toBe(true)
    // Same-page workspace persistence, far larger than any fixed headroom, takes all the rest.
    storage.setItem('prks.workspace.v1', 'w'.repeat(free(storage, quota)))
    expect(free(storage, quota)).toBe(0)
    expect(() => storage.setItem('other', 'y')).toThrow()
    expect(t.registry.writeEmergencyNow()).toBe('written')
    const left = JSON.parse(storage.map.get(emergencyKeyOf('p-me')) as string) as EmergencyPayload
    expect(left.entries[0]!.body).toBe('x'.repeat(1000))
  })

  it('reserves with a two-byte filler and frees the reservation once nothing is pending', async () => {
    const t = setup()
    const w = t.open()
    w.edit(1, 'x'.repeat(1000))
    const reservation = t.storage.map.get(reservationKeyOf('p-me')) as string
    expect(reservation.length).toBeGreaterThan(2000)
    expect(/^r-me\n\u0101+$/.test(reservation)).toBe(true)
    // Grows geometrically, not on every keystroke.
    const spy = vi.spyOn(t.storage, 'setItem')
    w.edit(2, 'x'.repeat(1001))
    expect(spy).not.toHaveBeenCalled()
    await w.flush()
    expect(t.storage.map.has(reservationKeyOf('p-me'))).toBe(false)
  })

  it('does not reserve again after the pagehide write, and re-plans when the page is shown again', () => {
    const t = setup()
    const w = t.open()
    w.edit(1, 'closing with this')
    expect(t.storage.map.has(reservationKeyOf('p-me'))).toBe(true)
    t.win.fire('pagehide')
    expect(t.storage.map.has(reservationKeyOf('p-me'))).toBe(false)
    const left = JSON.parse(t.storage.map.get(emergencyKeyOf('p-me')) as string) as EmergencyPayload
    expect(left.entries[0]!.body).toBe('closing with this')
    // Restored from the back/forward cache: it reserves again.
    t.win.fire('pageshow')
    expect(t.storage.map.has(reservationKeyOf('p-me'))).toBe(true)
    expect(w.heldByEmergency()).toBe(true)
  })

  it('writes its reservation again when the page comes back from hidden or frozen', () => {
    const t = setup()
    const w = t.open()
    w.edit(1, 'typed before the tab was frozen')
    t.doc.fire('visibilitychange')
    // While frozen, a page that claimed this runtime took it for closed.
    t.storage.map.delete(reservationKeyOf('p-me'))
    t.doc.fire('resume')
    expect(t.storage.map.has(reservationKeyOf('p-me'))).toBe(true)
    t.storage.map.delete(reservationKeyOf('p-me'))
    ;(t.doc as { visibilityState: string }).visibilityState = 'visible'
    t.doc.fire('visibilitychange')
    expect(t.storage.map.has(reservationKeyOf('p-me'))).toBe(true)
    expect(w.heldByEmergency()).toBe(true)
  })

  it('takes bounded slack beyond a large payload', () => {
    const t = setup()
    const w = t.open()
    w.edit(1, 'x'.repeat(200_000))
    const reserved = (t.storage.map.get(reservationKeyOf('p-me')) as string).length
    expect(reserved).toBeGreaterThan(200_000)
    expect(reserved).toBeLessThanOrEqual('r-me\n'.length + 200_000 + 2 + 1024 + 64 * 1024)
  })

  it('does not reserve, and guards, until the runtime claim has settled', () => {
    let settled = false
    const t = setup({ settled: () => settled })
    const w = t.open()
    w.edit(1, 'before the claim')
    expect(w.heldByEmergency()).toBe(false)
    expect(t.registry.leaveGuardActive()).toBe(true)
    expect(t.storage.map.has(reservationKeyOf('p-me'))).toBe(false)
    settled = true
    w.edit(2, 'after the claim')
    expect(w.heldByEmergency()).toBe(true)
    expect(t.registry.leaveGuardActive()).toBe(false)
  })
})

describe('discard when the emergency key cannot be cleared', () => {
  function stuckStorage() {
    const inner = memoryStorage()
    const state = { blocked: false }
    const storage: EmergencyStorage & { map: Map<string, string> } = {
      ...inner,
      setItem(k: string, v: string) {
        if (state.blocked) throw new DOMException('blocked', 'SecurityError')
        inner.setItem(k, v)
      },
      removeItem(k: string) {
        if (state.blocked) throw new DOMException('blocked', 'SecurityError')
        inner.removeItem(k)
      },
    }
    return { storage, state }
  }

  it('leaves a tombstone that is never a candidate, and clears it once the key is rewritten', async () => {
    const { storage, state } = stuckStorage()
    const t = setup({ storage })
    const w = t.open()
    w.edit(1, 'discard me')
    expect(t.registry.writeEmergencyNow()).toBe('written')
    const draftId = w.draftId() as string
    state.blocked = true
    await w.discard()
    expect(storage.map.has(emergencyKeyOf('p-me'))).toBe(true)
    expect(await t.store.listByEntity('work-research-note', 'w1')).toEqual([])
    expect(await t.store.getBody(draftId)).toBeNull()
    expect(await t.store.get(draftId)).toMatchObject({ status: 'discarded', bodyLength: 0 })
    expect(await t.store.adopt(draftId, 'p-me', { runtimeId: 'r-x', pageInstanceId: 'p-x', paneId: 'tab-1', claimedAt: 0 })).toEqual({ outcome: 'missing' })

    state.blocked = false
    const other = t.open('s-2', 'w2')
    other.edit(1, 'another note')
    await settle()
    expect(JSON.parse(storage.map.get(emergencyKeyOf('p-me')) as string).entries.map((e: { draftId: string }) => e.draftId)).toEqual([other.draftId()])
    expect(await t.store.get(draftId)).toBeNull()
  })

  it('tombstones an acknowledged first generation whose stale key could not be cleared', async () => {
    const { storage, state } = stuckStorage()
    const t = setup({ storage })
    const w = t.open()
    w.edit(1, 'saved words')
    expect(t.registry.writeEmergencyNow()).toBe('written')
    const draftId = w.draftId() as string
    state.blocked = true
    await w.flush()
    expect(await w.acknowledged(1, 'saved words')).toBe('deleted')
    expect(JSON.parse(storage.map.get(emergencyKeyOf('p-me')) as string).entries[0].committedGeneration).toBe(0)
    expect(await t.store.get(draftId)).toMatchObject({ status: 'discarded' })
    const payload = JSON.parse(storage.map.get(emergencyKeyOf('p-me')) as string) as EmergencyPayload
    expect(await t.store.applyEmergencyEntry(payload, payload.entries[0]!)).toBe('suppressed')
    expect(await t.store.listByEntity('work-research-note', 'w1')).toEqual([])
  })

  it('discards without a tombstone when the key could be cleared', async () => {
    const t = setup()
    const w = t.open()
    w.edit(1, 'discard me')
    t.registry.writeEmergencyNow()
    const draftId = w.draftId() as string
    await w.discard()
    expect(t.storage.map.size).toBe(0)
    expect(await t.store.get(draftId)).toBeNull()
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
  it('ends the warning and the guard when the server acknowledges a generation storage refused', async () => {
    const t = setup()
    const w = t.open()
    w.edit(1, 'stored')
    await w.flush()
    const id = w.draftId() as string
    t.idb.failCommits = 100
    w.edit(2, 'stored, then refused')
    await w.flush()
    expect(w.state()).toBe('unprotected')
    expect(t.registry.leaveGuardActive()).toBe(true)
    // Another body is not this generation: still unprotected.
    await expect(w.acknowledged(2, 'stored, then refuseD')).rejects.toThrow()
    expect(w.state()).toBe('unprotected')
    t.idb.failCommits = 0
    expect(await w.acknowledged(2, 'stored, then refused')).toBe('deleted')
    expect(w.state()).toBe('clean')
    expect(t.registry.leaveGuardActive()).toBe(false)
    // The superseded older generation of the same lineage goes with it.
    expect(await t.store.get(id)).toBeNull()
    expect(t.scheduler.pending()).toBe(0)
  })

  it('retries removing the superseded generation in the background when storage still refuses', async () => {
    const t = setup()
    const w = t.open()
    w.edit(1, 'stored')
    await w.flush()
    const id = w.draftId() as string
    t.idb.failCommits = 100
    w.edit(2, 'stored, then refused')
    await w.flush()
    expect(await w.acknowledged(2, 'stored, then refused')).toBe('deleted')
    expect(w.state()).toBe('clean')
    expect(t.registry.leaveGuardActive()).toBe(false)
    // Still refused: the older record stays for now, with a cleanup retry.
    expect(t.scheduler.pending()).toBe(1)
    t.idb.failCommits = 0
    t.scheduler.advance(60_000)
    await settle()
    expect(await t.store.get(id)).toBeNull()
    expect(t.scheduler.pending()).toBe(0)
    expect(t.registry.leaveGuardActive()).toBe(false)
  })

  it('ends the warning when recovery storage cannot be opened at all', async () => {
    const t = setup()
    const w = t.open()
    t.idb.failCommits = 100
    w.edit(1, 'never stored')
    await w.flush()
    expect(w.state()).toBe('unprotected')
    expect(await w.acknowledged(1, 'never stored')).toBe('deleted')
    expect(w.state()).toBe('clean')
    expect(t.registry.leaveGuardActive()).toBe(false)
  })

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

  it('writes a newer generation whose timer fired while the acknowledgement deleted its lineage', async () => {
    const t = setup()
    const w = t.open()
    w.edit(5, 'five')
    await w.flush()
    const first = w.draftId() as string
    w.edit(6, 'six')
    const ack = w.acknowledged(5, 'five')
    t.scheduler.advance(300)
    expect(await ack).toBe('deleted')
    await settle()
    expect(w.draftId()).not.toBe(first)
    expect(w.state()).toBe('protected')
    expect(await t.store.getBody(w.draftId() as string)).toMatchObject({ generation: 6, body: 'six' })
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

describe('lineage metadata', () => {
  const PIPELINE: DraftPipeline = { state: 'queued', queuedOpId: 'op-7', queuedGeneration: 1, blockedBase: null, ownQueued: null }
  const NEWER: DraftBase = { revision: 8, length: 5, fingerprint: 'b'.repeat(32), source: 'server' }

  it('carries base and pipeline with the next write while a body is pending', async () => {
    const t = setup()
    const w = t.open()
    w.setPipeline(PIPELINE)
    w.edit(1, 'one')
    await w.flush()
    expect(t.calls).toHaveLength(1)
    expect(t.calls[0]).toMatchObject({ generation: 1, base: BASE, pipeline: PIPELINE })
    expect(await t.store.get(w.draftId() as string)).toMatchObject({ base: BASE, pipeline: PIPELINE })
  })

  it('updates a stored lineage in place when nothing is pending, without a body write', async () => {
    const t = setup()
    const w = t.open()
    w.edit(1, 'one')
    await w.flush()
    w.setBase(NEWER)
    w.setPipeline(PIPELINE)
    await settle()
    expect(t.calls).toHaveLength(1)
    expect(await t.store.get(w.draftId() as string)).toMatchObject({ generation: 1, base: NEWER, pipeline: PIPELINE })
    expect(await t.store.getBody(w.draftId() as string)).toMatchObject({ generation: 1, body: 'one' })
  })

  it('never races a body write: a metadata change during a write lands after it', async () => {
    const t = setup({ gate: true })
    const w = t.open()
    w.edit(1, 'one')
    void w.flush()
    await settle()
    w.setPipeline(PIPELINE)
    await t.releaseGate()
    await settle()
    expect(t.calls).toHaveLength(1)
    expect(await t.store.get(w.draftId() as string)).toMatchObject({ generation: 1, pipeline: PIPELINE })
  })

  it('writes nothing for a writer that has no stored lineage yet', async () => {
    const t = setup()
    const w = t.open()
    w.setPipeline(PIPELINE)
    await settle()
    expect(t.calls).toHaveLength(0)
    expect(await t.store.listAll()).toEqual([])
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

  it('refuses a second adoption of a draft this page released', async () => {
    const t = setup()
    const w = t.open()
    w.edit(1, 'mine')
    await w.release()
    const record = (await t.store.get(w.draftId() as string))!
    expect(record.owner.pageInstanceId).toBe('p-me')
    const [a, b] = await Promise.all([
      t.registry.adopt(record, { paneId: 'tab-1', sessionKey: 's-a' }),
      t.registry.adopt(record, { paneId: 'tab-2', sessionKey: 's-b' }),
    ])
    expect([a, b].filter(Boolean)).toHaveLength(1)
    expect(await t.registry.adopt(record, { paneId: 'tab-3', sessionKey: 's-c' })).toBeNull()
    expect(t.registry.writers().filter((x) => x.draftId() === record.draftId)).toHaveLength(1)
  })
})

describe('discard', () => {
  it('moves an edit made while a write is in flight to a fresh lineage', async () => {
    const t = setup({ gate: true })
    const w = t.open()
    w.edit(1, 'to be discarded')
    void w.flush()
    const old = w.draftId() as string
    const discarding = w.discard()
    w.edit(2, 'kept words')
    expect(w.draftId()).not.toBe(old)
    expectProtectedOrDisclosed(t.registry)
    await t.releaseGate()
    await discarding
    expect(w.state()).toBe('pending')
    t.scheduler.advance(300)
    await t.releaseGate()
    expect(w.state()).toBe('protected')
    expect(await t.store.getBody(w.draftId() as string)).toMatchObject({ generation: 2, body: 'kept words' })
    expect(await t.store.get(old)).toBeNull()
  })
})

describe('emergency key refresh', () => {
  it('drops the entry of a discarded draft while another stays pending', async () => {
    const t = setup()
    const a = t.open('s-a', 'w1')
    const b = t.open('s-b', 'w2')
    a.edit(1, 'a text')
    b.edit(1, 'b text')
    t.doc.fire('visibilitychange')
    const before = JSON.parse(t.storage.map.get(emergencyKeyOf('p-me')) as string) as EmergencyPayload
    expect(before.entries).toHaveLength(2)
    const aId = a.draftId() as string
    await a.flush()
    await a.discard()
    const after = JSON.parse(t.storage.map.get(emergencyKeyOf('p-me')) as string) as EmergencyPayload
    expect(after.entries.map((e) => e.draftId)).toEqual([b.draftId()])
    expect(after.entries.some((e) => e.draftId === aId)).toBe(false)
  })
})
