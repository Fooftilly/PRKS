import { afterEach, describe, expect, it } from 'vitest'
import { BODIES_STORE, DRAFTS_STORE, RECOVERY_DB_NAME, UNKNOWN_BASE, type DraftOwner, type DraftPipeline, type DraftRecord } from './schema'
import { createRecoveryStore, forkedDraftId, type CreateLineage, type RecoveryStore } from './store'
import { createFakeIdb, type FakeIdbControls } from './test-support/fake-idb'

const owner = (pageInstanceId: string, runtimeId = 'r-1'): DraftOwner => ({ runtimeId, pageInstanceId, paneId: 'tab-1', claimedAt: 1 })
const lineage = (pageInstanceId: string): CreateLineage => ({
  kind: 'work-research-note',
  entityType: 'work',
  entityId: 'w1',
  owner: owner(pageInstanceId),
  base: { revision: 3, length: 4, fingerprint: 'f'.repeat(32), source: 'server' },
})

function setup(extra: Parameters<typeof createRecoveryStore>[0] = {}): { idb: FakeIdbControls; store: RecoveryStore } {
  const idb = createFakeIdb()
  return { idb, store: createRecoveryStore({ indexedDB: idb.factory, now: () => 1000, ...extra }) }
}

async function seed(store: RecoveryStore, draftId = 'd-a', page = 'p-a', generation = 1, body = 'body one'): Promise<void> {
  expect(await store.writeGeneration({ draftId, pageInstanceId: page, generation, body, create: lineage(page) })).toBe('ok')
}

describe('recovery store', () => {
  const originalTx = (globalThis as { IDBTransaction?: unknown }).IDBTransaction
  afterEach(() => {
    ;(globalThis as { IDBTransaction?: unknown }).IDBTransaction = originalTx
  })

  it('uses its own database, never prks-local-v1', async () => {
    const { idb, store } = setup()
    await seed(store)
    expect(RECOVERY_DB_NAME).toBe('prks-editor-recovery-v1')
    expect(idb.rows(RECOVERY_DB_NAME, DRAFTS_STORE)).toHaveLength(1)
    expect(idb.rows('prks-local-v1', 'operations')).toEqual([])
  })

  it('writes metadata and body together with matching generations', async () => {
    const { store } = setup()
    await seed(store)
    expect(await store.writeGeneration({ draftId: 'd-a', pageInstanceId: 'p-a', generation: 2, body: 'body two' })).toBe('ok')
    const record = (await store.get('d-a')) as DraftRecord
    expect(record).toMatchObject({ v: 1, generation: 2, bodyLength: 8, entityKey: 'work-research-note:w1', status: 'active' })
    expect(await store.getBody('d-a')).toEqual({ draftId: 'd-a', generation: 2, body: 'body two' })
  })

  it('never leaves metadata and body diverged when the commit aborts', async () => {
    const { idb, store } = setup()
    await seed(store)
    idb.failCommits = 1
    await expect(store.writeGeneration({ draftId: 'd-a', pageInstanceId: 'p-a', generation: 2, body: 'lost' })).rejects.toMatchObject({
      code: 'quota',
    })
    expect((await store.get('d-a'))?.generation).toBe(1)
    expect(await store.getBody('d-a')).toMatchObject({ generation: 1, body: 'body one' })
  })

  it('keeps generations monotonic and lets only the owner page write', async () => {
    const { store } = setup()
    await seed(store, 'd-a', 'p-a', 5)
    expect(await store.writeGeneration({ draftId: 'd-a', pageInstanceId: 'p-a', generation: 5, body: 'same gen' })).toBe('stale')
    expect(await store.writeGeneration({ draftId: 'd-a', pageInstanceId: 'p-a', generation: 4, body: 'older' })).toBe('stale')
    expect(await store.writeGeneration({ draftId: 'd-a', pageInstanceId: 'p-b', generation: 9, body: 'intruder' })).toBe('not-owner')
    expect(await store.writeGeneration({ draftId: 'd-missing', pageInstanceId: 'p-a', generation: 9, body: 'x' })).toBe('missing')
    expect(await store.getBody('d-a')).toMatchObject({ generation: 5, body: 'body one' })
  })

  it('updates base and pipeline in place for the owner page only, never the body', async () => {
    const { store } = setup()
    await seed(store, 'd-a', 'p-a', 4, 'kept body')
    const pipeline: DraftPipeline = {
      state: 'blocked',
      queuedOpId: null,
      queuedGeneration: 0,
      blockedBase: { revision: 3, length: 4, fingerprint: 'f'.repeat(32) },
      ownQueued: { opId: 'op-1', textLength: 2, textFingerprint: 'e'.repeat(32), base: { revision: 3, length: 4, fingerprint: 'f'.repeat(32) } },
    }
    const base = { revision: 9, length: 2, fingerprint: 'c'.repeat(32), source: 'server' as const }
    expect(await store.updateLineage({ draftId: 'd-a', pageInstanceId: 'p-other', pipeline })).toBe('not-owner')
    expect((await store.get('d-a'))?.pipeline).toBeNull()
    expect(await store.updateLineage({ draftId: 'd-a', pageInstanceId: 'p-a', base, pipeline })).toBe('ok')
    const record = (await store.get('d-a')) as DraftRecord
    expect(record).toMatchObject({ generation: 4, base, pipeline })
    expect(await store.getBody('d-a')).toEqual({ draftId: 'd-a', generation: 4, body: 'kept body' })
    // The next body write keeps the stored pipeline unless it brings its own.
    expect(await store.writeGeneration({ draftId: 'd-a', pageInstanceId: 'p-a', generation: 5, body: 'next' })).toBe('ok')
    expect((await store.get('d-a'))?.pipeline).toEqual(pipeline)
    expect(await store.writeGeneration({ draftId: 'd-a', pageInstanceId: 'p-a', generation: 6, body: 'n', pipeline: null })).toBe('ok')
    expect((await store.get('d-a'))?.pipeline).toBeNull()
    expect(await store.updateLineage({ draftId: 'd-missing', pageInstanceId: 'p-a', base })).toBe('missing')
    await store.discard('d-a', { kind: 'work-research-note', entityType: 'work', entityId: 'w1', generation: 6, pageInstanceId: 'p-a' })
    expect(await store.updateLineage({ draftId: 'd-a', pageInstanceId: 'p-a', base })).toBe('missing')
  })

  it('adopts by compare-and-set: of two simultaneous adopters exactly one wins', async () => {
    const { store } = setup()
    await seed(store, 'd-a', 'p-dead', 3)
    const [one, two] = await Promise.all([
      store.adopt('d-a', 'p-dead', owner('p-one', 'r-one')),
      store.adopt('d-a', 'p-dead', owner('p-two', 'r-two')),
    ])
    expect([one.outcome, two.outcome].sort()).toEqual(['conflict', 'ok'])
    const winner = one.outcome === 'ok' ? 'p-one' : 'p-two'
    expect((await store.get('d-a'))?.owner.pageInstanceId).toBe(winner)
    // The old owner can no longer write the adopted lineage.
    expect(await store.writeGeneration({ draftId: 'd-a', pageInstanceId: 'p-dead', generation: 4, body: 'late' })).toBe('not-owner')
  })

  it('clears on acknowledgement only for the exact generation and body', async () => {
    const { store } = setup()
    await seed(store, 'd-a', 'p-a', 2, 'acked body')
    expect(await store.deleteIfAcknowledged('d-a', 1, 'acked body')).toBe('kept')
    expect(await store.deleteIfAcknowledged('d-a', 2, 'acked bodY')).toBe('kept')
    expect(await store.deleteIfAcknowledged('d-a', 2, 'acked body')).toBe('deleted')
    expect(await store.get('d-a')).toBeNull()
    expect(await store.getBody('d-a')).toBeNull()
  })

  it('keeps a different body when an injected fingerprint collides', async () => {
    const { store } = setup({ fingerprint: () => '0'.repeat(32) })
    await seed(store, 'd-a', 'p-a', 2, 'draft text A')
    expect(await store.deleteIfAcknowledged('d-a', 2, 'draft text B')).toBe('kept')
    expect(await store.deleteIfEqual('d-a', 'draft text B')).toBe('kept')
    expect(await store.deleteIfEqual('d-a', 'draft text A')).toBe('deleted')
  })

  it('never deletes or rewrites a record from a newer schema', async () => {
    const { idb, store } = setup()
    await seed(store)
    // Simulate a newer PRKS having rewritten the record.
    const newer = createRecoveryStore({ indexedDB: idb.factory })
    await newer.writeGeneration({ draftId: 'd-new', pageInstanceId: 'p-a', generation: 1, body: 'b', create: lineage('p-a') })
    const raw = idb.rows(RECOVERY_DB_NAME, DRAFTS_STORE).find((r) => r.draftId === 'd-new') as Record<string, unknown>
    const db = await new Promise<IDBDatabase>((resolve) => {
      const req = idb.factory.open(RECOVERY_DB_NAME, 1)
      req.onsuccess = () => resolve(req.result)
    })
    await new Promise<void>((resolve) => {
      const tx = db.transaction([DRAFTS_STORE, BODIES_STORE], 'readwrite')
      tx.objectStore(DRAFTS_STORE).put({ ...raw, v: 2 })
      tx.oncomplete = () => resolve()
    })
    expect(await store.writeGeneration({ draftId: 'd-new', pageInstanceId: 'p-a', generation: 2, body: 'x' })).toBe('unsupported')
    expect(await store.discard('d-new')).toBe('unsupported')
    expect(await store.deleteIfEqual('d-new', 'b')).toBe('unsupported')
    expect(await store.deleteIfAcknowledged('d-new', 1, 'b')).toBe('unsupported')
    expect((await store.get('d-new'))?.v).toBe(2)
  })

  it('has no age-based deletion', async () => {
    let clock = 0
    const idb = createFakeIdb()
    const store = createRecoveryStore({ indexedDB: idb.factory, now: () => clock })
    await store.writeGeneration({ draftId: 'd-old', pageInstanceId: 'p-a', generation: 1, body: 'old', create: lineage('p-a') })
    clock = 10 * 365 * 24 * 3600 * 1000
    await store.writeGeneration({ draftId: 'd-new', pageInstanceId: 'p-b', generation: 1, body: 'new', create: lineage('p-b') })
    await store.listAll()
    expect((await store.listAll()).map((r) => r.draftId).sort()).toEqual(['d-new', 'd-old'])
  })

  it('lists lineages by entity without loading bodies', async () => {
    const { store } = setup()
    await seed(store, 'd-a', 'p-a')
    await seed(store, 'd-b', 'p-b')
    await store.writeGeneration({
      draftId: 'd-c',
      pageInstanceId: 'p-c',
      generation: 1,
      body: 'x',
      create: { ...lineage('p-c'), entityId: 'w2' },
    })
    const rows = await store.listByEntity('work-research-note', 'w1')
    expect(rows.map((r) => r.draftId).sort()).toEqual(['d-a', 'd-b'])
    expect(rows.every((r) => !('body' in r))).toBe(true)
  })

  describe('durability', () => {
    it('requests relaxed durability when the prototype exposes it', async () => {
      ;(globalThis as { IDBTransaction?: unknown }).IDBTransaction = class {
        get durability() {
          return 'default'
        }
      }
      const { idb, store } = setup()
      await seed(store)
      const writes = idb.log.filter((e) => e.mode === 'readwrite')
      expect(writes.length).toBeGreaterThan(0)
      expect(writes.every((e) => (e.options as { durability?: string })?.durability === 'relaxed')).toBe(true)
      expect(store.lastDurability()).toBe('relaxed')
    })

    it('falls back to a plain transaction when the prototype has no durability', async () => {
      ;(globalThis as { IDBTransaction?: unknown }).IDBTransaction = class {}
      const { idb, store } = setup()
      await seed(store)
      expect(idb.log.filter((e) => e.mode === 'readwrite').every((e) => e.options === undefined)).toBe(true)
      expect(store.lastDurability()).toBe('default')
    })

    it('falls back when transaction() rejects the options argument', async () => {
      const { idb, store } = setup({ durability: 'relaxed' })
      idb.rejectOptions = true
      await seed(store)
      expect(await store.getBody('d-a')).toMatchObject({ body: 'body one' })
      expect(store.lastDurability()).toBe('default')
    })
  })

  it('reports an unavailable database and retries the open later', async () => {
    const { idb, store } = setup()
    idb.openFails = true
    await expect(store.listAll()).rejects.toMatchObject({ code: 'unavailable' })
    idb.openFails = false
    expect(await store.listAll()).toEqual([])
  })

  it('works against the repository fake IndexedDB used by the classic selftests', async () => {
    // CommonJS selftest helper; loaded through Node's require (no Node typings in this package).
    const nodeModule = 'node:module'
    const { createRequire } = (await import(/* @vite-ignore */ nodeModule)) as {
      createRequire: (url: string) => (id: string) => unknown
    }
    const require = createRequire(import.meta.url)
    const { createFakeIndexedDBFactory } = require('../../../../tests/browser/lib/fake_indexeddb.js') as {
      createFakeIndexedDBFactory: () => IDBFactory
    }
    const store = createRecoveryStore({ indexedDB: createFakeIndexedDBFactory() })
    expect(await store.writeGeneration({ draftId: 'd-r', pageInstanceId: 'p-a', generation: 1, body: 'repo fake', create: lineage('p-a') })).toBe(
      'ok',
    )
    expect(await store.getBody('d-r')).toMatchObject({ generation: 1, body: 'repo fake' })
    expect(await store.deleteIfAcknowledged('d-r', 1, 'repo fake')).toBe('deleted')
  })

  describe('emergency merge', () => {
    const payload = (page: string) => ({ v: 1, pageInstanceId: page, runtimeId: 'r-dead', at: 50, entries: [] })

    it('writes a newer tail into the dead page lineage, keeping its owner', async () => {
      const { store } = setup()
      await seed(store, 'd-a', 'p-dead', 3)
      const entry = { draftId: 'd-a', kind: 'work-research-note' as const, entityType: 'work' as const, entityId: 'w1', generation: 4, committedGeneration: 3, body: 'tail' }
      expect(await store.applyEmergencyEntry(payload('p-dead'), entry)).toBe('written')
      expect(await store.getBody('d-a')).toMatchObject({ generation: 4, body: 'tail' })
      expect((await store.get('d-a'))?.owner.pageInstanceId).toBe('p-dead')
      expect(await store.applyEmergencyEntry(payload('p-dead'), entry)).toBe('noop')
    })

    it('takes the base and pane from the entry for a same-owner tail', async () => {
      const { store } = setup()
      await seed(store, 'd-a', 'p-dead', 3)
      const newerBase = { revision: 42, length: 9, fingerprint: 'c'.repeat(32), source: 'server' as const }
      const entry = {
        draftId: 'd-a',
        kind: 'work-research-note' as const,
        entityType: 'work' as const,
        entityId: 'w1',
        generation: 4,
        committedGeneration: 3,
        body: 'tail on the newer base',
        lineage: { createdAt: 1, owner: { runtimeId: 'r-dead', pageInstanceId: 'p-dead', paneId: 'tab-9' }, base: newerBase },
      }
      expect(await store.applyEmergencyEntry(payload('p-dead'), entry)).toBe('written')
      const record = await store.get('d-a')
      expect(record?.base).toEqual(newerBase)
      expect(record?.owner).toMatchObject({ pageInstanceId: 'p-dead', paneId: 'tab-9' })
    })

    it('marks tail-missing when the body could not be held', async () => {
      const { store } = setup()
      await seed(store, 'd-a', 'p-dead', 3)
      const entry = { draftId: 'd-a', kind: 'work-research-note' as const, entityType: 'work' as const, entityId: 'w1', generation: 4, committedGeneration: 3, body: null }
      expect(await store.applyEmergencyEntry(payload('p-dead'), entry)).toBe('tail-missing')
      expect((await store.get('d-a'))?.status).toBe('tail-missing')
      expect(await store.getBody('d-a')).toMatchObject({ generation: 3 })
    })

    it('creates a first-generation lineage from the entry, never inventing a base', async () => {
      const { store } = setup()
      const entry = {
        draftId: 'd-first',
        kind: 'work-research-note' as const,
        entityType: 'work' as const,
        entityId: 'w1',
        generation: 1,
        committedGeneration: 0,
        body: 'first words',
        lineage: { createdAt: 7, owner: { runtimeId: null, pageInstanceId: 'p-dead', paneId: 'tab-2' }, base: { ...UNKNOWN_BASE } },
      }
      expect(await store.applyEmergencyEntry(payload('p-dead'), entry)).toBe('created')
      expect(await store.get('d-first')).toMatchObject({ createdAt: 7, base: UNKNOWN_BASE, owner: { pageInstanceId: 'p-dead', paneId: 'tab-2' } })
    })

    it('forks the tail into its own lineage when the record was adopted, idempotently', async () => {
      const { store } = setup()
      await seed(store, 'd-a', 'p-dead', 3)
      await store.adopt('d-a', 'p-dead', owner('p-live'))
      const entry = { draftId: 'd-a', kind: 'work-research-note' as const, entityType: 'work' as const, entityId: 'w1', generation: 4, committedGeneration: 3, body: 'tail' }
      expect(await store.applyEmergencyEntry(payload('p-dead'), entry)).toBe('forked')
      expect(await store.applyEmergencyEntry(payload('p-dead'), entry)).toBe('noop')
      expect(await store.getBody('d-a')).toMatchObject({ generation: 3, body: 'body one' })
      expect(await store.getBody(forkedDraftId('d-a', 4))).toMatchObject({ body: 'tail' })
    })
  })
})
