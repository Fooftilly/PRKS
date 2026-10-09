import { describe, expect, it } from 'vitest'
import type { EmergencyStorage } from './emergency'
import { createEditorRecoveryRuntime, type EditorRecoveryRuntime } from './runtime'
import { CLOSED_PAGE_KEY_PREFIX, DRAFTS_STORE, EMERGENCY_KEY_PREFIX, RECOVERY_DB_NAME, RUNTIME_SESSION_KEY, UNKNOWN_BASE, reservationKeyOf, type DraftKind } from './schema'
import type { DraftWriter } from './writer'
import { createFakeBrowser, memoryStorage } from './test-support/fake-env'
import { createFakeIdb, createManualScheduler, settle } from './test-support/fake-idb'

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

  it('without Web Locks, picks up a tab that closed during its claim once its final pagehide is recorded', async () => {
    const browser = createFakeBrowser()
    const idb = createFakeIdb()
    const local = memoryStorage()
    const make = (name: string) =>
      createEditorRecoveryRuntime({
        store: { indexedDB: idb.factory },
        identity: { sessionStorage: browser.sessionStorageWith(), locks: null, createChannel: browser.channelFor(name), claimWaitMs: 20, localStorage: local },
        writers: { scheduler: createManualScheduler(), window: null, document: null },
        emergencyStorage: local,
      })
    const b = make('b')
    await b.start()
    const a = make('a')
    // Typed and closed before its runtime claim settled: the payload carries no runtime id.
    void a.start()
    a.writers.openWriter({ kind: 'work-research-note', entityType: 'work', entityId: 'w1', paneId: 'tab-1', base: UNKNOWN_BASE }).edit(1, 'closed while claiming')
    expect(a.writers.writeEmergencyNow()).toBe('written')
    const key = EMERGENCY_KEY_PREFIX + a.identity.pageInstanceId
    expect(JSON.parse(local.map.get(key)!).runtimeId).toBeNull()
    a.identity.dispose()
    await settle()
    // Silence alone is not proof: the key stays.
    expect(await b.scanEmergency()).toEqual([])
    expect(local.map.has(key)).toBe(true)
    local.setItem(CLOSED_PAGE_KEY_PREFIX + a.identity.pageInstanceId, '1')
    const merged = await b.scanEmergency()
    expect(merged.map((m) => [m.key, m.outcomes, m.removed])).toEqual([[key, ['created'], true]])
    const [row] = await b.store.listByEntity('work-research-note', 'w1')
    expect((await b.store.getBody(row!.draftId))?.body).toBe('closed while claiming')
  })

  it('without Web Locks, never takes a page of this tab frozen in the back/forward cache for an orphan', async () => {
    const browser = createFakeBrowser()
    const idb = createFakeIdb()
    const local = memoryStorage()
    const session = browser.sessionStorageWith()
    const events = () => {
      const target = new EventTarget()
      return {
        target,
        fire: (type: string, persisted: boolean) => target.dispatchEvent(Object.assign(new Event(type), { persisted })),
      }
    }
    const make = (name: string, win: EventTarget) =>
      createEditorRecoveryRuntime({
        store: { indexedDB: idb.factory },
        identity: { sessionStorage: session, locks: null, createChannel: browser.channelFor(name), claimWaitMs: 20, localStorage: local, window: win },
        writers: { scheduler: createManualScheduler(), window: null, document: null },
        emergencyStorage: local,
      })
    const aWin = events()
    const a = make('a', aWin.target)
    await a.start()
    const w = a.writers.openWriter({ kind: 'work-research-note', entityType: 'work', entityId: 'w1', paneId: 'tab-1', base: UNKNOWN_BASE })
    w.edit(1, 'still in the cache')
    await w.flush()
    const [record] = await a.store.listByEntity('work-research-note', 'w1')
    // Into the cache, then frozen: it answers nothing.
    aWin.fire('pagehide', true)
    a.identity.dispose()
    await settle()
    // The same tab opens PRKS again: it inherits the runtime id and verifies it by silence.
    const b = make('b', events().target)
    const { claim } = await b.start()
    expect(claim).toMatchObject({ runtimeId: record!.owner.runtimeId, verified: 'channel' })
    expect(await b.classify(record!)).toBe('unknown')
    expect(b.identity.wasPageClosed(record!.owner.pageInstanceId)).toBe(false)
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

describe('cleanupDeletedEntity (#533)', () => {
  const W1 = { entityType: 'work' as const, entityId: 'w1' }
  const KINDS = ['work-research-note', 'work-private-note'] as const

  function setup(withoutLocks = false) {
    const browser = createFakeBrowser()
    const idb = createFakeIdb()
    const local = memoryStorage()
    const page = (name: string, frozen?: { now: boolean }) => {
      const locks = browser.locksFor(name)
      const open = browser.channelFor(name)
      // A frozen page keeps its page lock but hears nothing, so it answers nothing.
      const createChannel = (channelName: string) => {
        const channel = open(channelName)
        if (!frozen) return channel
        let handler: ((event: { data: unknown }) => void) | null = null
        channel.onmessage = (event) => {
          if (!frozen.now) handler?.(event)
        }
        return {
          get onmessage() {
            return handler
          },
          set onmessage(next) {
            handler = next
          },
          postMessage: (message: unknown) => channel.postMessage(message),
          close: () => channel.close(),
        }
      }
      const rt = createEditorRecoveryRuntime({
        store: { indexedDB: idb.factory },
        identity: {
          sessionStorage: browser.sessionStorageWith(),
          locks: withoutLocks ? null : locks,
          createChannel,
          claimWaitMs: 20,
          localStorage: local,
        },
        writers: { scheduler: createManualScheduler(), window: null, document: null },
        emergencyStorage: local,
      })
      return { rt, locks }
    }
    return { idb, local, page }
  }

  /** A page that wrote `text` for w1 and crashed: no pagehide, but its page lock is released (proven gone). */
  async function crashedDraft(make: (name: string) => { rt: EditorRecoveryRuntime; locks: { releaseAll(): void } }, name: string, text: string, kind: DraftKind = 'work-private-note') {
    const p = make(name)
    await p.rt.start()
    const w = p.rt.writers.openWriter({ kind, entityType: 'work', entityId: 'w1', paneId: 'tab-1', base: UNKNOWN_BASE })
    w.edit(1, text)
    await w.flush()
    p.rt.identity.dispose()
    p.locks.releaseAll()
    await settle()
    return { page: p, writer: w }
  }

  async function bodies(rt: EditorRecoveryRuntime): Promise<Array<[string, string | null]>> {
    const rows = (await rt.store.listAll()).filter((r) => r.status !== 'discarded')
    return Promise.all(rows.map(async (r) => [r.entityKey, (await rt.store.getBody(r.draftId))?.body ?? null] as [string, string | null]))
  }

  it('rejects a cleanup that read a lineage before another page adopted it, and keeps it for that live editor', async () => {
    const { page } = setup()
    await crashedDraft(page, 'gone', 'orphaned text')
    const cleaner = page('cleaner').rt
    const adopter = page('adopter').rt
    await adopter.start()
    const [record] = await adopter.store.listAll()
    let adopted: DraftWriter | null = null
    const discard = cleaner.store.discard
    cleaner.store.discard = async (...args) => {
      cleaner.store.discard = discard
      adopted = await adopter.writers.adopt(record!, { paneId: 'tab-4' })
      return discard(...args)
    }
    const report = await cleaner.cleanupDeletedEntity(W1, KINDS)
    expect(adopted).not.toBeNull()
    expect(report).toEqual({ removed: [], live: [], unknown: [], changed: [record!.draftId], unsupported: [], suppressed: [] })
    expect(await bodies(cleaner)).toEqual([['work-private-note:w1', 'orphaned text']])
    // Retried: the adopter is now a live editor, so its lineage stays.
    expect(await cleaner.cleanupDeletedEntity(W1, KINDS)).toMatchObject({ removed: [], live: [record!.draftId] })
    expect(await bodies(cleaner)).toHaveLength(1)
  })

  it('never removes a newer generation written after the cleanup read the older one; a retry reads it again', async () => {
    const { page } = setup()
    const { page: gone } = await crashedDraft(page, 'gone', 'older text')
    const cleaner = page('cleaner').rt
    await cleaner.start()
    const [record] = await cleaner.store.listAll()
    const discard = cleaner.store.discard
    cleaner.store.discard = async (...args) => {
      cleaner.store.discard = discard
      // The dead page's tail lands first, as a merge of its emergency entry would.
      const owner = record!.owner
      expect(
        await cleaner.store.applyEmergencyEntry(
          { v: 1, pageInstanceId: owner.pageInstanceId, runtimeId: owner.runtimeId, at: 1, entries: [] },
          { draftId: record!.draftId, kind: 'work-private-note', entityType: 'work', entityId: 'w1', generation: 2, committedGeneration: 1, body: 'newer text' },
        ),
      ).toBe('written')
      return discard(...args)
    }
    expect(await cleaner.cleanupDeletedEntity(W1, KINDS)).toMatchObject({ removed: [], changed: [record!.draftId] })
    expect(await bodies(cleaner)).toEqual([['work-private-note:w1', 'newer text']])
    expect(gone.rt.identity.pageInstanceId).toBe(record!.owner.pageInstanceId)
    expect(await cleaner.cleanupDeletedEntity(W1, KINDS)).toMatchObject({ removed: [record!.draftId], changed: [] })
    expect(await bodies(cleaner)).toEqual([])
  })

  it('keeps a stale emergency key from bringing back a removed lineage, or creating one that never reached storage', async () => {
    const { page, local } = setup()
    // Already running when the other page crashes, so its start() merged nothing of that page.
    const cleaner = page('cleaner').rt
    await cleaner.start()
    // Its page lock is gone: proven closed.
    const { writer } = await crashedDraft(page, 'crashed', 'committed')
    const crashedPage = (await cleaner.store.get(writer.draftId()!))!.owner.pageInstanceId
    // Keys whose pages never settled a runtime id: no scan of a running page merges them.
    const key = (pageInstanceId: string, entries: unknown[]) =>
      local.setItem(EMERGENCY_KEY_PREFIX + pageInstanceId, JSON.stringify({ v: 1, pageInstanceId, runtimeId: null, at: 1, entries }))
    const entry = (draftId: string, generation: number, committedGeneration: number, body: string, kind: DraftKind, pageInstanceId: string) => ({
      draftId, kind, entityType: 'work', entityId: 'w1', generation, committedGeneration, body,
      lineage: { createdAt: 1, owner: { runtimeId: null, pageInstanceId, paneId: 'tab-1' }, base: UNKNOWN_BASE },
    })
    // Generation 2 of the stored lineage, held only in the crashed page's key.
    key(crashedPage, [entry(writer.draftId()!, 2, 1, 'typed after the commit', 'work-private-note', crashedPage)])
    // A lineage whose first generation never reached IndexedDB.
    key('p-lost', [entry('d-lost', 1, 0, 'only in localStorage', 'work-research-note', 'p-lost')])

    const report = await cleaner.cleanupDeletedEntity(W1, KINDS)
    expect(report).toEqual({ removed: [writer.draftId()], live: [], unknown: [], changed: [], unsupported: [], suppressed: ['d-lost'] })
    expect(await bodies(cleaner)).toEqual([])
    // Each stays a tombstone while a key still lists it.
    expect(await cleaner.store.get(writer.draftId()!)).toMatchObject({ status: 'discarded' })
    expect(await cleaner.store.get('d-lost')).toMatchObject({ status: 'discarded' })
    // A later load takes the silent pages for dead and merges their keys: nothing comes back.
    const next = page('next').rt
    const { merged } = await next.start()
    expect(merged.flatMap((m) => m.outcomes)).toEqual(['suppressed', 'suppressed'])
    expect(await bodies(next)).toEqual([])
    expect([...local.map.keys()].filter((k) => k.startsWith(EMERGENCY_KEY_PREFIX))).toEqual([])
    // With the keys gone, so are the tombstones.
    expect(await next.store.listAll()).toEqual([])
  })

  it('reports an emergency key it cannot read and leaves it in place', async () => {
    const { page, local } = setup()
    const cleaner = page('cleaner').rt
    await cleaner.start()
    const key = EMERGENCY_KEY_PREFIX + 'p-newer'
    const newer = JSON.stringify({ v: 99, pageInstanceId: 'p-newer', entries: [] })
    local.setItem(key, newer)
    expect(await cleaner.cleanupDeletedEntity(W1, KINDS)).toEqual({ removed: [], live: [], unknown: [], changed: [], unsupported: [key], suppressed: [] })
    expect(local.getItem(key)).toBe(newer)
  })

  it('reports an emergency-only lineage that another tab merged after the listing, so it is not taken as cleaned', async () => {
    const { page, local } = setup()
    const cleaner = page('cleaner').rt
    await cleaner.start()
    const payload = { v: 1, pageInstanceId: 'p-lost', runtimeId: null, at: 1, entries: [] }
    const lost = {
      draftId: 'd-lost', kind: 'work-research-note' as DraftKind, entityType: 'work' as const, entityId: 'w1', generation: 1, committedGeneration: 0, body: 'only in localStorage',
      lineage: { createdAt: 1, owner: { runtimeId: null, pageInstanceId: 'p-lost', paneId: 'tab-1' }, base: UNKNOWN_BASE },
    }
    local.setItem(EMERGENCY_KEY_PREFIX + 'p-lost', JSON.stringify({ ...payload, entries: [lost] }))
    const tombstone = cleaner.store.tombstoneIfAbsent
    cleaner.store.tombstoneIfAbsent = async (...args) => {
      // Another tab merges the key between this cleanup's listing and its tombstone.
      expect(await cleaner.store.applyEmergencyEntry({ ...payload, entries: [lost] }, lost)).toBe('created')
      cleaner.store.tombstoneIfAbsent = tombstone
      return tombstone(...args)
    }
    expect(await cleaner.cleanupDeletedEntity(W1, KINDS)).toEqual({ removed: [], live: [], unknown: [], changed: ['d-lost'], unsupported: [], suppressed: [] })
    expect(await cleaner.cleanupDeletedEntity(W1, KINDS)).toMatchObject({ removed: ['d-lost'], changed: [] })
  })

  it('keeps a frozen page\'s committed lineage: its page lock is held though it answers nothing', async () => {
    const { page } = setup()
    const frozen = { now: false }
    const live = page('frozen', frozen)
    await live.rt.start()
    const w = live.rt.writers.openWriter({ kind: 'work-private-note', entityType: 'work', entityId: 'w1', paneId: 'tab-1', base: UNKNOWN_BASE })
    w.edit(1, 'committed before the freeze')
    await w.flush()
    expect(live.rt.writers.leaveGuardActive()).toBe(false)
    frozen.now = true
    const cleaner = page('cleaner').rt
    expect(await cleaner.classify((await cleaner.store.get(w.draftId()!))!)).toBe('unknown')
    expect(await cleaner.cleanupDeletedEntity(W1, KINDS)).toEqual({ removed: [], live: [], unknown: [w.draftId()], changed: [], unsupported: [], suppressed: [] })
    expect(await bodies(cleaner)).toEqual([['work-private-note:w1', 'committed before the freeze']])
    // Resumed: the writer still owns its lineage and keeps it recoverable.
    frozen.now = false
    const events: string[] = []
    live.rt.onWriterEvent((event) => events.push(event.type))
    w.edit(2, 'typed after resuming')
    await w.flush()
    expect(events).not.toContain('ownership-lost')
    expect(await bodies(cleaner)).toEqual([['work-private-note:w1', 'typed after resuming']])
    // Once its page is proven gone, the next cleanup removes it.
    await w.release()
    live.rt.identity.dispose()
    live.locks.releaseAll()
    await settle()
    expect(await cleaner.cleanupDeletedEntity(W1, KINDS)).toMatchObject({ removed: [w.draftId()], unknown: [] })
    expect(await bodies(cleaner)).toEqual([])
  })

  it('keeps a record of the entity it cannot read, untouched, and reports it', async () => {
    const { page, idb } = setup()
    await crashedDraft(page, 'gone', 'written by a newer PRKS')
    const cleaner = page('cleaner').rt
    const [record] = await cleaner.store.listAll()
    // A newer PRKS rewrote it with a schema this code does not know.
    const db = await new Promise<IDBDatabase>((resolve) => {
      const req = idb.factory.open(RECOVERY_DB_NAME, 1)
      req.onsuccess = () => resolve(req.result)
    })
    await new Promise<void>((resolve) => {
      const tx = db.transaction([DRAFTS_STORE], 'readwrite')
      tx.objectStore(DRAFTS_STORE).put({ ...record!, v: 99 })
      tx.objectStore(DRAFTS_STORE).put({ draftId: 'd-garbled', v: 99 })
      tx.oncomplete = () => resolve()
    })
    const report = await cleaner.cleanupDeletedEntity(W1, KINDS)
    expect(report).toMatchObject({ removed: [], unsupported: [record!.draftId, 'd-garbled'] })
    expect((await cleaner.store.get(record!.draftId))?.v).toBe(99)
    // Another Work's newer record is none of this cleanup's business.
    expect((await cleaner.cleanupDeletedEntity({ entityType: 'work', entityId: 'w2' }, KINDS)).unsupported).toEqual(['d-garbled'])
  })

  it('leaves a live page\'s first generation in its emergency key alone', async () => {
    const { page } = setup()
    const live = page('live')
    await live.rt.start()
    const w = live.rt.writers.openWriter({ kind: 'work-private-note', entityType: 'work', entityId: 'w1', paneId: 'tab-1', base: UNKNOWN_BASE })
    w.edit(1, 'typing now')
    expect(live.rt.writers.writeEmergencyNow()).toBe('written')
    const cleaner = page('cleaner').rt
    expect(await cleaner.cleanupDeletedEntity(W1, KINDS)).toEqual({ removed: [], live: [w.draftId()], unknown: [], changed: [], unsupported: [], suppressed: [] })
    expect(await cleaner.store.get(w.draftId()!)).toBeNull()
    await w.flush()
    expect(await bodies(cleaner)).toEqual([['work-private-note:w1', 'typing now']])
  })

  it('reports a record a newer schema rewrote between the listing and its removal as unsupported', async () => {
    const { page } = setup()
    await crashedDraft(page, 'a', 'reminder of w1')
    const cleaner = page('cleaner').rt
    const draftId = (await cleaner.store.listAll())[0]!.draftId
    cleaner.store.discard = async () => 'unsupported'
    expect(await cleaner.cleanupDeletedEntity(W1, KINDS)).toEqual({ removed: [], live: [], unknown: [], changed: [], unsupported: [draftId], suppressed: [] })
  })

  it('finishes an interrupted cleanup on retry, is idempotent, and never touches another Work or kind', async () => {
    const { page, idb } = setup()
    await crashedDraft(page, 'a', 'reminder of w1')
    await crashedDraft(page, 'b', 'research of w1', 'work-research-note')
    const other = page('other')
    await other.rt.start()
    for (const [kind, id] of [['work-private-note', 'w2'], ['folder-private-note', 'w1']] as const) {
      const w = other.rt.writers.openWriter({ kind, entityType: kind === 'folder-private-note' ? 'folder' : 'work', entityId: id, paneId: 'tab-1', base: UNKNOWN_BASE })
      w.edit(1, kind + ' ' + id)
      await w.release()
    }
    other.rt.identity.dispose()
    other.locks.releaseAll()
    await settle()
    const cleaner = page('cleaner').rt
    let calls = 0
    const discard = cleaner.store.discard
    cleaner.store.discard = (...args) => {
      // The second removal fails to commit, as a browser shutting down mid-cleanup would leave it.
      if (++calls === 2) idb.failCommits = 1
      return discard(...args)
    }
    await expect(cleaner.cleanupDeletedEntity(W1, KINDS)).rejects.toThrow()
    expect(await bodies(cleaner)).toHaveLength(3)
    const retried = await cleaner.cleanupDeletedEntity(W1, KINDS)
    expect(retried.removed).toHaveLength(1)
    expect(await cleaner.cleanupDeletedEntity(W1, KINDS)).toEqual({ removed: [], live: [], unknown: [], changed: [], unsupported: [], suppressed: [] })
    expect((await bodies(cleaner)).sort()).toEqual([
      ['folder-private-note:w1', 'folder-private-note w1'],
      ['work-private-note:w2', 'work-private-note w2'],
    ])
  })
})
