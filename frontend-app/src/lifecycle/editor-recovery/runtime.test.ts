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
