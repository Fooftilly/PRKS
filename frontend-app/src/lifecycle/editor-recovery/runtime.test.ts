import { describe, expect, it } from 'vitest'
import type { EmergencyStorage } from './emergency'
import { createEditorRecoveryRuntime } from './runtime'
import { EMERGENCY_KEY_PREFIX, RUNTIME_SESSION_KEY, UNKNOWN_BASE, reservationKeyOf } from './schema'
import { createFakeBrowser } from './test-support/fake-env'
import { createFakeIdb, createManualScheduler, settle } from './test-support/fake-idb'

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

const COPIED = 'r-' + 'c'.repeat(32)

describe('editor recovery runtime', () => {
  it('without Web Locks, a busy original writes its reservation again after a duplicate removed it', async () => {
    const browser = createFakeBrowser()
    const idb = createFakeIdb()
    const local = memoryStorage()
    // The original tab's channel, held back during a long task.
    let busy = false
    const held: Array<() => void> = []
    const busyChannel = (name: string) => {
      const real = browser.channelFor('original')(name)
      const proxy = {
        onmessage: null as ((event: { data: unknown }) => void) | null,
        postMessage: (message: unknown) => real.postMessage(message),
        close: () => real.close(),
      }
      real.onmessage = (event) => {
        const deliver = () => proxy.onmessage?.(event)
        if (busy) held.push(deliver)
        else deliver()
      }
      return proxy
    }
    const original = createEditorRecoveryRuntime({
      store: { indexedDB: idb.factory },
      identity: { sessionStorage: browser.sessionStorageWith({ [RUNTIME_SESSION_KEY]: COPIED }), locks: null, createChannel: busyChannel, claimWaitMs: 20 },
      writers: { scheduler: createManualScheduler(), window: null, document: null },
      emergencyStorage: local,
    })
    await original.start()
    const w = original.writers.openWriter({ kind: 'work-research-note', entityType: 'work', entityId: 'w1', paneId: 'tab-1', base: UNKNOWN_BASE })
    w.edit(1, 'still typing here')
    const key = reservationKeyOf(original.identity.pageInstanceId)
    expect(local.map.has(key)).toBe(true)
    busy = true
    // Duplicate tab: the copied id goes unanswered, so it reads as a reload of this tab.
    const duplicate = createEditorRecoveryRuntime({
      store: { indexedDB: idb.factory },
      identity: { sessionStorage: browser.sessionStorageWith({ [RUNTIME_SESSION_KEY]: COPIED }), locks: null, createChannel: browser.channelFor('duplicate'), claimWaitMs: 20 },
      writers: { window: null, document: null },
      emergencyStorage: local,
    })
    expect((await duplicate.start()).claim.verified).toBe('channel')
    expect(local.map.has(key)).toBe(false)
    // The long task ends: the queued claim and removal notice arrive in order.
    busy = false
    held.splice(0).forEach((deliver) => deliver())
    await new Promise((resolve) => setTimeout(resolve, 10))
    expect(local.map.has(key)).toBe(true)
    expect(w.heldByEmergency()).toBe(true)
    expect(duplicate.identity.current()!.verified).toBe('unverified')
    // Later scans never use the runtime tag.
    await duplicate.scanEmergency()
    expect(local.map.has(key)).toBe(true)
  })

  it('without Web Locks, frees a crashed page\'s reservation when its tab reloads and claims the same runtime', async () => {
    const browser = createFakeBrowser()
    const idb = createFakeIdb()
    const local = memoryStorage()
    const session = browser.sessionStorageWith({ [RUNTIME_SESSION_KEY]: COPIED })
    const load = (name: string) =>
      createEditorRecoveryRuntime({
        store: { indexedDB: idb.factory },
        identity: { sessionStorage: session, locks: null, createChannel: browser.channelFor(name), claimWaitMs: 20 },
        writers: { scheduler: createManualScheduler(), window: null, document: null },
        emergencyStorage: local,
      })
    const crashed = load('crashed')
    expect((await crashed.start()).claim).toEqual({ runtimeId: COPIED, verified: 'channel' })
    crashed.writers.openWriter({ kind: 'work-research-note', entityType: 'work', entityId: 'w1', paneId: 'tab-1', base: UNKNOWN_BASE }).edit(1, 'typed')
    const leaked = reservationKeyOf(crashed.identity.pageInstanceId)
    expect(local.map.has(leaked)).toBe(true)
    // An unrelated tab cannot tell a crash from a freeze, so it leaves the reservation alone.
    const other = createEditorRecoveryRuntime({
      store: { indexedDB: idb.factory },
      identity: { sessionStorage: browser.sessionStorageWith(), locks: null, createChannel: browser.channelFor('other'), claimWaitMs: 20 },
      writers: { window: null, document: null },
      emergencyStorage: local,
    })
    // Renderer crash: no pagehide, the channel goes silent, localStorage stays.
    crashed.identity.dispose()
    await other.start()
    expect(local.map.has(leaked)).toBe(true)
    // The same tab reloads and verifiably claims the runtime again.
    const reloaded = load('reloaded')
    expect((await reloaded.start()).claim.runtimeId).toBe(COPIED)
    expect(local.map.has(leaked)).toBe(false)
    // A re-scan never uses the tag: by then a silent claim proves even less.
    local.setItem(leaked, 'r-' + 'c'.repeat(32) + '\n' + 'ā'.repeat(10))
    await reloaded.scanEmergency()
    expect(local.map.has(leaked)).toBe(true)
  })

  it('recovers both duplicated tabs that started simultaneously with one copied runtime id', async () => {
    const browser = createFakeBrowser()
    const idb = createFakeIdb()
    const local = memoryStorage()
    const pages = ['a', 'b'].map((name) => {
      const scheduler = createManualScheduler()
      const locks = browser.locksFor(name)
      const rt = createEditorRecoveryRuntime({
        store: { indexedDB: idb.factory },
        identity: {
          sessionStorage: browser.sessionStorageWith({ [RUNTIME_SESSION_KEY]: COPIED }),
          locks,
          createChannel: browser.channelFor(name),
          claimWaitMs: 20,
        },
        writers: { scheduler, window: null, document: null },
        emergencyStorage: local,
      })
      return { rt, scheduler, locks }
    })
    const starts = pages.map((p) => p.rt.start())
    // Both type before their runtime claims settle.
    const writers = pages.map((p, i) => {
      const w = p.rt.writers.openWriter({ kind: 'work-research-note', entityType: 'work', entityId: 'w1', paneId: 'tab-1', base: UNKNOWN_BASE })
      w.edit(1, 'typed in tab ' + i)
      return w
    })
    pages.forEach((p) => p.rt.writers.writeEmergencyNow())
    const keys = [...local.map.keys()].filter((k) => k.startsWith(EMERGENCY_KEY_PREFIX))
    expect(keys.sort()).toEqual(pages.map((p) => EMERGENCY_KEY_PREFIX + p.rt.identity.pageInstanceId).sort())
    const claims = await Promise.all(starts)
    expect(claims[0]!.claim.runtimeId).not.toBe(claims[1]!.claim.runtimeId)
    expect(writers[0]!.draftId()).not.toBe(writers[1]!.draftId())

    // Both pages die before any IndexedDB write: locks and channels go away, localStorage stays.
    pages.forEach((p) => {
      p.locks.releaseAll()
      p.rt.identity.dispose()
    })
    await settle()
    const next = createEditorRecoveryRuntime({
      store: { indexedDB: idb.factory },
      identity: { sessionStorage: browser.sessionStorageWith(), locks: browser.locksFor('next'), createChannel: browser.channelFor('next'), claimWaitMs: 20 },
      writers: { window: null, document: null },
      emergencyStorage: local,
    })
    const { merged } = await next.start()
    expect(merged.flatMap((m) => m.outcomes).sort()).toEqual(['created', 'created'])
    const rows = await next.store.listByEntity('work-research-note', 'w1')
    expect(rows).toHaveLength(2)
    const bodies = await Promise.all(rows.map((r) => next.store.getBody(r.draftId)))
    expect(bodies.map((b) => b?.body).sort()).toEqual(['typed in tab 0', 'typed in tab 1'])
    expect(local.map.size).toBe(0)
    // Each recovered lineage keeps its own page as owner and is adoptable by the next page.
    for (const row of rows) expect(await next.classify(row)).toBe('dead-runtime')
  })

  it('classifies a previous load of this tab as a same-runtime orphan after reload', async () => {
    const browser = createFakeBrowser()
    const idb = createFakeIdb()
    const session = browser.sessionStorageWith()
    const make = (name: string) =>
      createEditorRecoveryRuntime({
        store: { indexedDB: idb.factory },
        identity: { sessionStorage: session, locks: browser.locksFor(name), createChannel: browser.channelFor(name), claimWaitMs: 20 },
        writers: { window: null, document: null },
        emergencyStorage: memoryStorage(),
      })
    const before = make('before')
    await before.start()
    const w = before.writers.openWriter({ kind: 'work-research-note', entityType: 'work', entityId: 'w1', paneId: 'tab-2', base: UNKNOWN_BASE })
    w.edit(1, 'before reload')
    await w.flush()
    before.dispose()
    await settle()
    const after = make('after')
    const { claim } = await after.start()
    const [record] = await after.store.listByEntity('work-research-note', 'w1')
    expect(record!.owner.runtimeId).toBe(claim.runtimeId)
    expect(await after.classify(record!)).toBe('same-runtime-orphan')
    const adopted = await after.writers.adopt(record!, { paneId: 'tab-7' })
    expect(adopted).not.toBeNull()
    expect(await after.classify(record!, adopted!.sessionKey)).toBe('self-live')
  })

  it('picks up a tab that closes after this page started, without a reload', async () => {
    const browser = createFakeBrowser()
    const idb = createFakeIdb()
    const local = memoryStorage()
    const make = (name: string) => {
      const locks = browser.locksFor(name)
      const rt = createEditorRecoveryRuntime({
        store: { indexedDB: idb.factory },
        identity: { sessionStorage: browser.sessionStorageWith(), locks, createChannel: browser.channelFor(name), claimWaitMs: 20 },
        writers: { scheduler: createManualScheduler(), window: null, document: null },
        emergencyStorage: local,
      })
      return { rt, locks }
    }
    const b = make('b')
    expect((await b.rt.start()).merged).toEqual([])
    const a = make('a')
    await a.rt.start()
    a.rt.writers.openWriter({ kind: 'work-research-note', entityType: 'work', entityId: 'w1', paneId: 'tab-1', base: UNKNOWN_BASE }).edit(1, 'closed later')
    expect(a.rt.writers.writeEmergencyNow()).toBe('written')
    const key = EMERGENCY_KEY_PREFIX + a.rt.identity.pageInstanceId

    // A is still alive: its key is not consumed.
    const [whileAlive, again] = await Promise.all([b.rt.scanEmergency(), b.rt.scanEmergency()])
    expect([whileAlive, again]).toEqual([[], []])
    expect(local.map.has(key)).toBe(true)
    expect(await b.rt.store.listByEntity('work-research-note', 'w1')).toEqual([])

    a.locks.releaseAll()
    a.rt.identity.dispose()
    await settle()
    const merged = await b.rt.scanEmergency()
    expect(merged.map((m) => [m.key, m.outcomes, m.removed])).toEqual([[key, ['created'], true]])
    const [row] = await b.rt.store.listByEntity('work-research-note', 'w1')
    expect((await b.rt.store.getBody(row!.draftId))?.body).toBe('closed later')
    expect(local.map.has(key)).toBe(false)
    expect(await b.rt.scanEmergency()).toEqual([])
  })

  it('scanEmergency starts the runtime when nothing has yet', async () => {
    const browser = createFakeBrowser()
    const rt = createEditorRecoveryRuntime({
      store: { indexedDB: createFakeIdb().factory },
      identity: { sessionStorage: browser.sessionStorageWith(), locks: browser.locksFor('x'), createChannel: browser.channelFor('x'), claimWaitMs: 20 },
      writers: { window: null, document: null },
      emergencyStorage: memoryStorage(),
    })
    expect(await rt.scanEmergency()).toEqual([])
    expect((await rt.start()).claim.runtimeId).toBeTruthy()
  })

  it('never recreates a draft discarded while its emergency key could not be cleared', async () => {
    const browser = createFakeBrowser()
    const idb = createFakeIdb()
    const inner = memoryStorage()
    let blocked = false
    const local: EmergencyStorage & { map: Map<string, string> } = {
      ...inner,
      get length() {
        return inner.map.size
      },
      setItem(k: string, v: string) {
        if (blocked) throw new DOMException('blocked', 'SecurityError')
        inner.setItem(k, v)
      },
      removeItem(k: string) {
        if (blocked) throw new DOMException('blocked', 'SecurityError')
        inner.removeItem(k)
      },
    }
    const make = (name: string) => {
      const locks = browser.locksFor(name)
      const rt = createEditorRecoveryRuntime({
        store: { indexedDB: idb.factory },
        identity: { sessionStorage: browser.sessionStorageWith(), locks, createChannel: browser.channelFor(name), claimWaitMs: 20 },
        writers: { scheduler: createManualScheduler(), window: null, document: null },
        emergencyStorage: local,
      })
      return { rt, locks }
    }
    const a = make('a')
    await a.rt.start()
    const w = a.rt.writers.openWriter({ kind: 'work-research-note', entityType: 'work', entityId: 'w1', paneId: 'tab-1', base: UNKNOWN_BASE })
    // First generation, only in the emergency entry.
    w.edit(1, 'discarded words')
    expect(a.rt.writers.writeEmergencyNow()).toBe('written')
    blocked = true
    await w.discard()
    const key = EMERGENCY_KEY_PREFIX + a.rt.identity.pageInstanceId
    expect(local.map.has(key)).toBe(true)
    a.locks.releaseAll()
    a.rt.identity.dispose()
    await settle()

    blocked = false
    const b = make('b')
    const { merged } = await b.rt.start()
    expect(merged.map((m) => [m.key, m.outcomes, m.removed])).toEqual([[key, ['suppressed'], true]])
    expect(await b.rt.store.listByEntity('work-research-note', 'w1')).toEqual([])
    // The key is gone, so the tombstone went with it.
    expect(await b.rt.store.listAll()).toEqual([])
    expect(local.map.has(key)).toBe(false)
    expect(await b.rt.scanEmergency()).toEqual([])
  })
})

describe('review actions', () => {
  function runtimeOn(idb: ReturnType<typeof createFakeIdb>, local: EmergencyStorage, name: string) {
    const browser = createFakeBrowser()
    return createEditorRecoveryRuntime({
      store: { indexedDB: idb.factory },
      identity: { sessionStorage: browser.sessionStorageWith(), locks: null, createChannel: browser.channelFor(name), claimWaitMs: 5, localStorage: local },
      writers: { window: null, document: null },
      emergencyStorage: local,
    })
  }

  it('leaves a tombstone when a stale emergency key still lists the discarded draft, so a merge never brings it back', async () => {
    const idb = createFakeIdb()
    const local = memoryStorage()
    const rt = runtimeOn(idb, local, 'reviewer')
    await rt.start()
    const draftId = 'd-' + 'e'.repeat(32)
    const lineage = { createdAt: 1, owner: { runtimeId: null, pageInstanceId: 'p-gone', paneId: 'tab-1' }, base: UNKNOWN_BASE }
    expect(await rt.store.writeGeneration({
      draftId, pageInstanceId: 'p-gone', generation: 2, body: 'reviewed text',
      create: { kind: 'work-research-note', entityType: 'work', entityId: 'w1', owner: { ...lineage.owner, claimedAt: 1 }, base: UNKNOWN_BASE },
    })).toBe('ok')
    // The gone page's key could not be removed and still lists the lineage at generation 1.
    const payload = { v: 1, pageInstanceId: 'p-gone', runtimeId: null, at: 1, entries: [
      { draftId, kind: 'work-research-note' as const, entityType: 'work' as const, entityId: 'w1', generation: 1, committedGeneration: 0, body: 'older text', lineage },
    ] }
    local.setItem(EMERGENCY_KEY_PREFIX + 'p-gone', JSON.stringify(payload))
    const reviewed = { draftId, pageInstanceId: 'p-gone', generation: 2, status: 'active' as const, kind: 'work-research-note' as const, entityType: 'work' as const, entityId: 'w1' }
    // A stale review (older generation) removes nothing.
    expect(await rt.discardReviewed({ ...reviewed, generation: 1 })).toBe('kept')
    expect(await rt.discardReviewed(reviewed)).toBe('deleted')
    expect(await rt.store.get(draftId)).toMatchObject({ status: 'discarded' })
    expect(await rt.store.getBody(draftId)).toBeNull()
    expect(await rt.store.applyEmergencyEntry(payload, payload.entries[0]!)).toBe('suppressed')
    expect((await rt.store.listByEntity('work-research-note', 'w1')).filter((r) => r.status !== 'discarded')).toEqual([])
    rt.dispose()
  })

  it('claims a reviewed record by compare-and-set on its owner and generation', async () => {
    const idb = createFakeIdb()
    const local = memoryStorage()
    const rt = runtimeOn(idb, local, 'claimer')
    await rt.start()
    const draftId = 'd-' + 'f'.repeat(32)
    await rt.store.writeGeneration({
      draftId, pageInstanceId: 'p-gone', generation: 3, body: 'text',
      create: { kind: 'work-research-note', entityType: 'work', entityId: 'w1', owner: { runtimeId: null, pageInstanceId: 'p-gone', paneId: 'tab-1', claimedAt: 1 }, base: UNKNOWN_BASE },
    })
    const reviewed = { draftId, pageInstanceId: 'p-gone', generation: 3, status: 'active' as const }
    expect((await rt.claimReviewed({ ...reviewed, generation: 2 }, 'tab-1')).outcome).toBe('conflict')
    expect((await rt.claimReviewed({ ...reviewed, status: 'tail-missing' }, 'tab-1')).outcome).toBe('conflict')
    expect((await rt.claimReviewed(reviewed, 'tab-1')).outcome).toBe('ok')
    expect((await rt.store.get(draftId))!.owner.pageInstanceId).toBe(rt.identity.pageInstanceId)
    // A second claim from the same review is now stale.
    expect((await rt.claimReviewed(reviewed, 'tab-1')).outcome).toBe('conflict')
    rt.dispose()
  })

  it('fans writer protection events out to every listener until it unsubscribes', async () => {
    const idb = createFakeIdb()
    const rt = runtimeOn(idb, memoryStorage(), 'events')
    await rt.start()
    const seen: string[] = []
    const stop = rt.onWriterEvent((event) => seen.push(event.type))
    rt.onWriterEvent(() => {
      throw new Error('a broken listener')
    })
    idb.failCommits = 5
    const w = rt.writers.openWriter({ kind: 'work-research-note', entityType: 'work', entityId: 'w1', paneId: 'tab-1', base: UNKNOWN_BASE })
    w.edit(1, 'text')
    await w.flush()
    expect(seen).toEqual(['unprotected'])
    stop()
    idb.failCommits = 0
    await w.flush()
    expect(seen).toEqual(['unprotected'])
    rt.dispose()
    await settle()
  })
})
