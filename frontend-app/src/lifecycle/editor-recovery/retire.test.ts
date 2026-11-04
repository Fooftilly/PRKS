import { describe, expect, it } from 'vitest'
import { createRecoveryDiagnostics } from './diagnostics'
import { createEditorRecoveryRuntime } from './runtime'
import { UNKNOWN_BASE, emergencyKeyOf } from './schema'
import { createRecoveryStore, RecoveryStoreError } from './store'
import { createFakeBrowser, memoryStorage, TEST_NAMES } from './test-support/fake-env'
import { createFakeIdb, createManualScheduler, settle } from './test-support/fake-idb'

function load(idb: ReturnType<typeof createFakeIdb>, local: ReturnType<typeof memoryStorage>, page: string) {
  const browser = createFakeBrowser()
  return createEditorRecoveryRuntime({
    names: TEST_NAMES,
    store: { indexedDB: idb.factory },
    identity: { sessionStorage: browser.sessionStorageWith(), locks: null, createChannel: browser.channelFor(page), claimWaitMs: 5 },
    writers: { scheduler: createManualScheduler(), window: null, document: null },
    emergencyStorage: local,
  })
}

async function refusedCode(promise: Promise<unknown>): Promise<string> {
  try {
    await promise
    return 'resolved'
  } catch (error) {
    return error instanceof RecoveryStoreError ? error.code : 'other'
  }
}

describe('recovery retires with its browser scope (#629)', () => {
  it('refuses every store operation once retired, also an open still in flight, and removes nothing', async () => {
    const idb = createFakeIdb()
    const store = createRecoveryStore({ indexedDB: idb.factory, name: TEST_NAMES.dbName })
    const lineage = { kind: 'work-research-note' as const, entityType: 'work' as const, entityId: 'w1',
      owner: { runtimeId: null, pageInstanceId: 'p-a', paneId: 'tab-1', claimedAt: 1 }, base: UNKNOWN_BASE }
    expect(await store.writeGeneration({ draftId: 'd-a', pageInstanceId: 'p-a', generation: 1, body: 'Text of A', create: lineage })).toBe('ok')
    store.close()
    // The open below starts before the retirement and completes after it.
    const inFlight = store.listAll()
    store.retire()
    expect(store.isRetired()).toBe(true)
    expect(await refusedCode(inFlight)).toBe('retired')
    expect(await refusedCode(store.getBody('d-a'))).toBe('retired')
    expect(await refusedCode(store.writeGeneration({ draftId: 'd-a', pageInstanceId: 'p-a', generation: 2, body: 'later' }))).toBe('retired')
    expect(await refusedCode(store.discard('d-a'))).toBe('retired')
    // The account signs in again: its draft is there.
    const again = createRecoveryStore({ indexedDB: idb.factory, name: TEST_NAMES.dbName })
    expect(await again.getBody('d-a')).toEqual({ draftId: 'd-a', generation: 1, body: 'Text of A' })
  })

  it('keeps the last typed text in its own scope, then refuses through the runtime, its handles and diagnostics', async () => {
    const idb = createFakeIdb()
    const local = memoryStorage()
    const rt = load(idb, local, 'a')
    await rt.start()
    const writer = rt.writers.openWriter({ kind: 'work-research-note', entityType: 'work', entityId: 'w1', paneId: 'tab-1', base: UNKNOWN_BASE })
    writer.edit(1, 'Typed before sign-out')
    const store = rt.store
    const storage = rt.emergencyStorage!
    const diag = createRecoveryDiagnostics(rt)
    const key = emergencyKeyOf(TEST_NAMES, rt.identity.pageInstanceId)

    rt.retire()
    expect(rt.isRetired()).toBe(true)
    // Written to this account's emergency key on the way out, never deleted.
    expect(local.map.get(key)).toContain('Typed before sign-out')
    // Handles taken before the retirement refuse too.
    expect(await refusedCode(store.listAll())).toBe('retired')
    expect(() => storage.getItem(key)).toThrow()
    expect(() => storage.length).toThrow()
    writer.edit(2, 'Typed after sign-out')
    await settle()
    expect(local.map.get(key)).not.toContain('Typed after sign-out')
    const inventory = await diag.inventory()
    expect(inventory.drafts).toEqual([])
    expect(inventory.emergency).toEqual([])
    expect(JSON.stringify(inventory)).not.toContain('Typed before sign-out')
    expect(rt.writers.leaveGuardActive()).toBe(false)
    expect(rt.writers.emergencyListenersActive()).toBe(false)

    // The same account on a new page merges and reads it.
    const next = load(idb, local, 'b')
    await next.start()
    const rows = await next.store.listAll()
    expect(rows).toHaveLength(1)
    expect((await next.store.getBody(rows[0]!.draftId))!.body).toBe('Typed before sign-out')
    next.dispose()
  })
})
