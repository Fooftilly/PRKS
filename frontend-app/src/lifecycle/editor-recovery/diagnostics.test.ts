import { describe, expect, it } from 'vitest'
import { createRecoveryDiagnostics, DELETED_WORK_MARK_PREFIX, OLD_DRAFT_MS, RECOVERY_WARN_BYTES, type DiagnosticsDraft, type DiscardTarget } from './diagnostics'
import { createEditorRecoveryRuntime, type EditorRecoveryRuntime } from './runtime'
import { BODIES_STORE, DRAFTS_STORE, EMERGENCY_KEY_PREFIX, RECOVERY_DB_NAME, SUPERSEDED_KEY_PREFIX, UNKNOWN_BASE, type DraftEntityType, type DraftKind } from './schema'
import { createFakeBrowser, memoryStorage } from './test-support/fake-env'
import { createFakeIdb, createManualScheduler, settle, type FakeIdbControls } from './test-support/fake-idb'

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
      identity: { sessionStorage: browser.sessionStorageWith(), locks: withoutLocks ? null : locks, createChannel, claimWaitMs: 20, localStorage: local },
      writers: { scheduler: createManualScheduler(), window: null, document: null },
      emergencyStorage: local,
    })
    return { rt, locks }
  }
  return { idb, local, page }
}

type Page = ReturnType<ReturnType<typeof setup>['page']>

/** A page that wrote `text` and crashed: no pagehide, but its page lock is released (proven gone). */
async function crashedDraft(p: Page, text: string, kind: DraftKind = 'work-research-note', entityId = 'w1', entityType: DraftEntityType = 'work') {
  await p.rt.start()
  const w = p.rt.writers.openWriter({ kind, entityType, entityId, paneId: 'tab-1', base: UNKNOWN_BASE })
  w.edit(1, text)
  await w.flush()
  p.rt.identity.dispose()
  p.locks.releaseAll()
  await settle()
  return w.draftId()!
}

async function putRows(idb: FakeIdbControls, store: string, rows: Array<Record<string, unknown>>): Promise<void> {
  const db = await new Promise<IDBDatabase>((resolve) => {
    const req = idb.factory.open(RECOVERY_DB_NAME, 1)
    req.onsuccess = () => resolve(req.result)
  })
  await new Promise<void>((resolve) => {
    const tx = db.transaction([store], 'readwrite')
    for (const row of rows) tx.objectStore(store).put(row)
    tx.oncomplete = () => resolve()
  })
}

async function deleteRows(idb: FakeIdbControls, store: string, keys: string[]): Promise<void> {
  const db = await new Promise<IDBDatabase>((resolve) => {
    const req = idb.factory.open(RECOVERY_DB_NAME, 1)
    req.onsuccess = () => resolve(req.result)
  })
  await new Promise<void>((resolve) => {
    const tx = db.transaction([store], 'readwrite')
    for (const key of keys) tx.objectStore(store).delete(key)
    tx.oncomplete = () => resolve()
  })
}

async function bodies(rt: EditorRecoveryRuntime): Promise<string[]> {
  const rows = (await rt.store.listAll()).filter((r) => r.status !== 'discarded')
  return Promise.all(rows.map(async (r) => (await rt.store.getBody(r.draftId))?.body ?? '<none>'))
}

describe('editor recovery diagnostics (#466 slice 5)', () => {
  it('lists drafts of every kind with their identity, and reads their text without changing anything', async () => {
    const { page, idb, local } = setup()
    const notes = await crashedDraft(page('a'), 'research text')
    const reminders = await crashedDraft(page('b'), 'work reminder', 'work-private-note')
    const folder = await crashedDraft(page('c'), 'folder reminder', 'folder-private-note', 'f1', 'folder')
    const viewer = page('viewer').rt
    const diag = createRecoveryDiagnostics(viewer, { storage: local })
    const before = JSON.stringify(idb.rows(RECOVERY_DB_NAME, DRAFTS_STORE))
    const inv = await diag.inventory()
    expect(inv.storage).toBe('ok')
    expect(inv.drafts.map((d) => [d.draftId, d.kind, d.entityType, d.entityId, d.lineage, d.status]).sort()).toEqual(
      [
        [notes, 'work-research-note', 'work', 'w1', 'dead-runtime', 'active'],
        [reminders, 'work-private-note', 'work', 'w1', 'dead-runtime', 'active'],
        [folder, 'folder-private-note', 'folder', 'f1', 'dead-runtime', 'active'],
      ].sort(),
    )
    expect(inv.totalChars).toBe('research text'.length + 'work reminder'.length + 'folder reminder'.length)
    expect(inv.overBudget).toBe(false)
    const d = inv.drafts.find((x) => x.draftId === folder)!
    expect(await diag.readText({ source: 'record', expect: d.expect })).toEqual({ ok: true, text: 'folder reminder' })
    expect(JSON.stringify(idb.rows(RECOVERY_DB_NAME, DRAFTS_STORE))).toBe(before)
    expect(idb.rows('prks-local-v1', 'operations')).toEqual([])
  })

  it('never offers the text of a draft an open editor holds, and never discards it', async () => {
    const { page, local } = setup()
    const live = page('live').rt
    await live.start()
    const w = live.writers.openWriter({ kind: 'work-research-note', entityType: 'work', entityId: 'w1', paneId: 'tab-1', base: UNKNOWN_BASE })
    w.edit(1, 'typing')
    await w.flush()
    // Diagnostics in the same page sees its own editor's lineage as held by another session.
    const diag = createRecoveryDiagnostics(live, { storage: local })
    const [d] = (await diag.inventory()).drafts
    expect(d!.lineage).toBe('other-live')
    expect(d!.old).toBe(false)
    expect(await diag.readText({ source: 'record', expect: d!.expect })).toEqual({ ok: false, code: 'live' })
    expect(await diag.discard({ ...d!.expect, kind: d!.kind, entityType: d!.entityType, entityId: d!.entityId, allowUnknownOwner: true })).toBe('live')
    // And another tab sees it as live elsewhere.
    const other = createRecoveryDiagnostics(page('other').rt, { storage: local })
    expect((await other.inventory()).drafts[0]!.lineage).toBe('other-live')
    expect(await bodies(live)).toEqual(['typing'])
  })

  it('discards a draft whose owner is unknown only after the user accepted that risk', async () => {
    const { page, local } = setup()
    const frozen = { now: false }
    const tab = page('frozen', frozen)
    await tab.rt.start()
    const w = tab.rt.writers.openWriter({ kind: 'work-private-note', entityType: 'work', entityId: 'w1', paneId: 'tab-1', base: UNKNOWN_BASE })
    w.edit(1, 'frozen text')
    await w.flush()
    frozen.now = true
    const diag = createRecoveryDiagnostics(page('viewer').rt, { storage: local })
    const [d] = (await diag.inventory()).drafts
    expect(d!.lineage).toBe('unknown')
    const target = { ...d!.expect, kind: d!.kind, entityType: d!.entityType, entityId: d!.entityId }
    expect(await diag.readText({ source: 'record', expect: d!.expect })).toEqual({ ok: true, text: 'frozen text' })
    expect(await diag.discard({ ...target, allowUnknownOwner: false })).toBe('unknown-owner')
    expect(await bodies(tab.rt)).toEqual(['frozen text'])
    expect(await diag.discard({ ...target, allowUnknownOwner: true })).toBe('deleted')
    expect(await bodies(tab.rt)).toEqual([])
  })

  it('refuses a discard or read decided on a listing that is no longer true', async () => {
    const { page, local } = setup()
    const draftId = await crashedDraft(page('gone'), 'first')
    const viewer = page('viewer').rt
    const diag = createRecoveryDiagnostics(viewer, { storage: local })
    const [d] = (await diag.inventory()).drafts
    // Another tab adopts it and types more after the list was read.
    const adopted = await viewer.claimReviewed(d!.expect, 'tab-9')
    expect(adopted.outcome).toBe('ok')
    const target = { ...d!.expect, kind: d!.kind, entityType: d!.entityType, entityId: d!.entityId, allowUnknownOwner: true }
    expect(await diag.discard(target)).toBe('changed')
    expect(await diag.readText({ source: 'record', expect: d!.expect })).toEqual({ ok: false, code: 'changed' })
    expect(await viewer.store.get(draftId)).not.toBeNull()
    // A wrong kind or entity never matches either.
    const [again] = (await diag.inventory()).drafts
    expect(await diag.discard({ ...again!.expect, kind: 'work-private-note', entityType: 'work', entityId: 'w1', allowUnknownOwner: true })).toBe('changed')
    expect(await diag.discard({ ...again!.expect, kind: again!.kind, entityType: 'work', entityId: 'w1', allowUnknownOwner: false })).toBe('deleted')
  })

  it('lists records it cannot read, keeps them untouched, and still offers their text', async () => {
    const { page, idb, local } = setup()
    const draftId = await crashedDraft(page('gone'), 'newer schema text')
    const viewer = page('viewer').rt
    const [record] = await viewer.store.listAll()
    // A v1 row that lost its status, entity and timestamps is damaged too, never a discardable draft.
    const partial = { v: 1, draftId: 'd-partial', kind: 'work-private-note', generation: 2, owner: { pageInstanceId: 'p-x' } }
    await putRows(idb, DRAFTS_STORE, [{ ...record!, v: 99 }, { draftId: 'd-garbled', kind: 7 }, partial])
    await putRows(idb, BODIES_STORE, [{ draftId: 'd-garbled', generation: 1, body: 'damaged but readable' }])
    const diag = createRecoveryDiagnostics(viewer, { storage: local })
    const inv = await diag.inventory()
    expect(inv.drafts).toEqual([])
    // Unreadable copies count towards the size; one that states none makes the total a lower bound.
    expect(inv.totalChars).toBe('newer schema text'.length)
    expect(inv.sizeIncomplete).toBe(true)
    expect(inv.unreadable).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ draftId, reason: 'newer-version', kind: 'work-research-note', entityId: 'w1' }),
        expect.objectContaining({ draftId: 'd-garbled', reason: 'malformed', kind: null }),
        expect.objectContaining({ draftId: 'd-partial', reason: 'malformed', kind: 'work-private-note', entityId: null }),
      ]),
    )
    const sameUndefined = { draftId: 'd-partial', pageInstanceId: 'p-x', generation: 2, status: undefined, kind: 'work-private-note', entityType: undefined, entityId: undefined }
    expect(await diag.discard(sameUndefined as unknown as DiscardTarget)).toBe('changed')
    const snapshot = (id: string) => inv.unreadable.find((u) => u.draftId === id)!.snapshot!
    expect(await diag.readText({ source: 'unreadable-record', draftId, snapshot: snapshot(draftId) })).toEqual({ ok: true, text: 'newer schema text' })
    expect(await diag.readText({ source: 'unreadable-record', draftId: 'd-garbled', snapshot: snapshot('d-garbled') })).toEqual({ ok: true, text: 'damaged but readable' })
    expect(await diag.readText({ source: 'unreadable-record', draftId: 'd-missing', snapshot: 'null' })).toEqual({ ok: false, code: 'missing' })
    // A row JSON cannot represent still gets an exact identity.
    await putRows(idb, DRAFTS_STORE, [{ v: 99, draftId: 'd-big', n: 1n }])
    await putRows(idb, BODIES_STORE, [{ draftId: 'd-big', generation: 1, body: 'big text' }])
    const odd = await diag.inventory()
    const big = odd.unreadable.find((u) => u.draftId === 'd-big')!
    expect(await diag.readText({ source: 'unreadable-record', draftId: 'd-big', snapshot: big.snapshot! })).toEqual({ ok: true, text: 'big text' })
    await putRows(idb, DRAFTS_STORE, [{ v: 99, draftId: 'd-big', n: 2n }])
    expect(await diag.readText({ source: 'unreadable-record', draftId: 'd-big', snapshot: big.snapshot! })).toEqual({ ok: false, code: 'changed' })
    // Array holes and extra properties count too.
    const sparse: unknown[] = [1]
    sparse[2] = 3
    const filled: unknown[] = [1, undefined, 3]
    const tagged: unknown[] = Object.assign([1, 2], { note: 'x' })
    await putRows(idb, DRAFTS_STORE, [{ v: 99, draftId: 'd-arr', a: sparse }])
    const arr = (await diag.inventory()).unreadable.find((u) => u.draftId === 'd-arr')!.snapshot
    await putRows(idb, DRAFTS_STORE, [{ v: 99, draftId: 'd-arr', a: filled }])
    expect((await diag.inventory()).unreadable.find((u) => u.draftId === 'd-arr')!.snapshot).not.toBe(arr)
    await putRows(idb, DRAFTS_STORE, [{ v: 99, draftId: 'd-arr', a: [1, 2] }])
    const plain = (await diag.inventory()).unreadable.find((u) => u.draftId === 'd-arr')!.snapshot
    await putRows(idb, DRAFTS_STORE, [{ v: 99, draftId: 'd-arr', a: tagged }])
    expect((await diag.inventory()).unreadable.find((u) => u.draftId === 'd-arr')!.snapshot).not.toBe(plain)
    // A typed-array view's buffer identity is not represented: no snapshot, so no text from it.
    await putRows(idb, DRAFTS_STORE, [{ v: 99, draftId: 'd-arr', a: new Uint8Array(new ArrayBuffer(2), 1, 1) }])
    expect((await diag.inventory()).unreadable.find((u) => u.draftId === 'd-arr')!.snapshot).toBeNull()
    // A newer-version editor rewrote the row since it was listed: its text is not shown from the old listing.
    await putRows(idb, DRAFTS_STORE, [{ ...record!, v: 99, generation: record!.generation + 1 }])
    expect(await diag.readText({ source: 'unreadable-record', draftId, snapshot: snapshot(draftId) })).toEqual({ ok: false, code: 'changed' })
    expect((await viewer.store.get(draftId))?.v).toBe(99)
    expect(idb.rows(RECOVERY_DB_NAME, DRAFTS_STORE)).toHaveLength(5)
  })

  it('keeps a record missing only its base or entity key copy-only, never discardable', async () => {
    const { page, idb, local } = setup()
    const first = await crashedDraft(page('gone-1'), 'lost its base')
    const second = await crashedDraft(page('gone-2'), 'lost its entity key', 'work-private-note', 'w2')
    const viewer = page('viewer').rt
    const rows = await viewer.store.listAll()
    const noBase: Record<string, unknown> = { ...rows.find((r) => r.draftId === first)! }
    delete noBase.base
    const noKey: Record<string, unknown> = { ...rows.find((r) => r.draftId === second)! }
    delete noKey.entityKey
    await putRows(idb, DRAFTS_STORE, [noBase, noKey])
    const diag = createRecoveryDiagnostics(viewer, { storage: local })
    const inv = await diag.inventory()
    expect(inv.drafts).toEqual([])
    expect(inv.unreadable.map((u) => [u.draftId, u.reason]).sort()).toEqual([[first, 'malformed'], [second, 'malformed']].sort())
    for (const [row, text] of [[noBase, 'lost its base'], [noKey, 'lost its entity key']] as const) {
      const target = {
        draftId: row.draftId,
        pageInstanceId: (row.owner as { pageInstanceId: string }).pageInstanceId,
        generation: row.generation,
        status: row.status,
        kind: row.kind,
        entityType: row.entityType,
        entityId: row.entityId,
        allowUnknownOwner: true,
      } as DiscardTarget
      expect(await diag.discard(target)).toBe('changed')
      expect(await diag.readText({ source: 'record', expect: target })).toEqual({ ok: false, code: 'changed' })
      const listed = inv.unreadable.find((u) => u.draftId === target.draftId)!
      expect(await diag.readText({ source: 'unreadable-record', draftId: target.draftId, snapshot: listed.snapshot! })).toEqual({ ok: true, text })
    }
    expect(await bodies(viewer)).toEqual(expect.arrayContaining(['lost its base', 'lost its entity key']))
    expect(idb.rows(RECOVERY_DB_NAME, DRAFTS_STORE)).toHaveLength(2)
  })

  it('lists text only an emergency key holds, without merging a live page, and lists the page\'s own key as open', async () => {
    const { page, local } = setup()
    const live = page('live').rt
    await live.start()
    const w = live.writers.openWriter({ kind: 'work-research-note', entityType: 'work', entityId: 'w1', paneId: 'tab-1', base: UNKNOWN_BASE })
    w.edit(1, 'hidden tab text')
    expect(live.writers.writeEmergencyNow()).toBe('written')
    const viewer = page('viewer').rt
    // Started first: a later scan merges only pages proven gone, so these keys stay keys.
    await viewer.start()
    // A key written by a newer PRKS, a damaged one, and one whose newest text was not captured.
    local.setItem(EMERGENCY_KEY_PREFIX + 'p-newer', JSON.stringify({ v: 99, pageInstanceId: 'p-newer', entries: [] }))
    local.setItem(EMERGENCY_KEY_PREFIX + 'p-broken', '{"v": 1, "entr')
    local.setItem(
      EMERGENCY_KEY_PREFIX + 'p-tail',
      JSON.stringify({
        v: 1, pageInstanceId: 'p-tail', runtimeId: null, at: 5,
        entries: [{ draftId: 'd-tail', kind: 'work-private-note', entityType: 'work', entityId: 'w2', generation: 3, committedGeneration: 2, body: null }],
      }),
    )
    const diag = createRecoveryDiagnostics(viewer, { storage: local })
    const inv = await diag.inventory()
    const held = inv.emergency.find((e) => e.draftId === w.draftId())!
    expect(held).toMatchObject({ lineage: 'other-live', bodyLength: 'hidden tab text'.length, kind: 'work-research-note', entityId: 'w1' })
    // Its editor is open: the text is read there, not from the key.
    expect(await diag.readText({ source: 'emergency', key: held.key, draftId: held.draftId, generation: held.generation })).toEqual({ ok: false, code: 'live' })
    expect(inv.emergency.find((e) => e.draftId === 'd-tail')).toMatchObject({ bodyLength: null, entityId: 'w2' })
    // A kept copy that states no size leaves the total a lower bound.
    expect(inv.sizeIncomplete).toBe(true)
    expect(inv.unreadableEmergency.map((u) => [u.pageInstanceId, u.reason]).sort()).toEqual([['p-broken', 'malformed'], ['p-newer', 'newer-version']])
    const broken = { source: 'unreadable-emergency' as const, key: EMERGENCY_KEY_PREFIX + 'p-broken', raw: '{"v": 1, "entr' }
    expect(inv.unreadableEmergency.find((u) => u.pageInstanceId === 'p-broken')!.raw).toBe(broken.raw)
    expect(await diag.readText(broken)).toEqual({ ok: true, text: '{"v": 1, "entr' })
    // Rewritten since it was listed: it is listed again before anything is shown.
    local.setItem(broken.key, '{"v": 1, "entries": []')
    expect(await diag.readText(broken)).toEqual({ ok: false, code: 'changed' })
    local.setItem(broken.key, broken.raw)
    // Nothing was listed for a key that could not be read: there is nothing to check its value against.
    expect(await diag.readText({ ...broken, raw: null })).toEqual({ ok: false, code: 'failed' })
    // Nothing was merged or removed: the live page still owns its key.
    expect(await viewer.store.get(w.draftId()!)).toBeNull()
    expect(local.getItem(EMERGENCY_KEY_PREFIX + 'p-broken')).toBe('{"v": 1, "entr')
    // The page lists its own key too while no record holds the text yet (recovery storage refused or behind),
    // as open here: the text is read in its editor.
    const ownDiag = createRecoveryDiagnostics(live, { storage: local })
    const own = (await ownDiag.inventory()).emergency.find((e) => e.draftId === w.draftId())!
    expect(['self-live', 'other-live']).toContain(own.lineage)
    expect(await ownDiag.readText({ source: 'emergency', key: own.key, draftId: own.draftId, generation: own.generation })).toEqual({ ok: false, code: 'live' })
  })

  it('refuses an emergency copy\'s text once its page answers for it again', async () => {
    const { page, idb, local } = setup()
    const frozen = { now: true }
    const tab = page('frozen', frozen)
    await tab.rt.start()
    const w = tab.rt.writers.openWriter({ kind: 'work-research-note', entityType: 'work', entityId: 'w1', paneId: 'tab-1', base: UNKNOWN_BASE })
    w.edit(1, 'typed while frozen')
    expect(tab.rt.writers.writeEmergencyNow()).toBe('written')
    const viewer = page('viewer').rt
    await viewer.start()
    const diag = createRecoveryDiagnostics(viewer, { storage: local })
    const [held] = (await diag.inventory()).emergency
    expect(held!.lineage).toBe('unknown')
    const target = { source: 'emergency' as const, key: held!.key, draftId: held!.draftId, generation: held!.generation }
    expect(await diag.readText(target)).toEqual({ ok: true, text: 'typed while frozen' })
    // A damaged row for the same lineage, without its text, never hides the key's copy.
    await putRows(idb, DRAFTS_STORE, [{ v: 1, draftId: held!.draftId, kind: 'work-research-note', generation: held!.generation, owner: { pageInstanceId: 'p-x' } }])
    const again = await diag.inventory()
    expect(again.emergency.map((e) => e.draftId)).toEqual([held!.draftId])
    expect(again.unreadable.map((u) => u.draftId)).toEqual([held!.draftId])
    // Nor does a damaged row that claims to be discarded while its body is still stored.
    await putRows(idb, DRAFTS_STORE, [{ v: 1, draftId: held!.draftId, kind: 'work-research-note', generation: held!.generation, owner: { pageInstanceId: 'p-x' }, status: 'discarded' }])
    await putRows(idb, BODIES_STORE, [{ draftId: held!.draftId, generation: held!.generation, body: 'kept under a false tombstone' }])
    const falseStone = await diag.inventory()
    expect(falseStone.emergency.map((e) => e.draftId)).toEqual([held!.draftId])
    expect(falseStone.unreadable.map((u) => u.draftId)).toEqual([held!.draftId])
    // A real tombstone (the store's shape, no body) still answers for the key, as the runtime's merge does.
    await deleteRows(idb, BODIES_STORE, [held!.draftId])
    await putRows(idb, DRAFTS_STORE, [{
      v: 1, draftId: held!.draftId, kind: 'work-research-note', entityType: 'work', entityId: 'w1', entityKey: 'work-research-note:w1',
      owner: { runtimeId: null, pageInstanceId: 'p-x', paneId: '', claimedAt: 1 }, generation: held!.generation, bodyLength: 0,
      base: UNKNOWN_BASE, pipeline: null, status: 'discarded', createdAt: 1, updatedAt: 1,
    }])
    const stone = await diag.inventory()
    expect(stone.emergency).toEqual([])
    expect(stone.unreadable).toEqual([])
    await deleteRows(idb, DRAFTS_STORE, [held!.draftId])
    // The page rewrites its key while it is asked about: the text read before is stale.
    const rewritten = local.getItem(held!.key)!.replace('typed while frozen', 'typed as it hid')
    const probing = createRecoveryDiagnostics(
      {
        ...viewer,
        classify: async (record) => {
          local.setItem(held!.key, rewritten)
          return viewer.classify(record)
        },
      },
      { storage: local },
    )
    expect(await probing.readText(target)).toEqual({ ok: false, code: 'changed' })
    expect(await diag.readText(target)).toEqual({ ok: true, text: 'typed as it hid' })
    // The tab resumes: its editor holds newer text than the key.
    frozen.now = false
    expect(await diag.readText(target)).toEqual({ ok: false, code: 'live' })
  })

  it('merges a closed page\'s emergency text into a record before listing it', async () => {
    const { page, local } = setup()
    const p = page('closing')
    await p.rt.start()
    const w = p.rt.writers.openWriter({ kind: 'work-research-note', entityType: 'work', entityId: 'w1', paneId: 'tab-1', base: UNKNOWN_BASE })
    w.edit(1, 'never reached storage')
    expect(p.rt.writers.writeEmergencyNow()).toBe('written')
    p.rt.identity.dispose()
    p.locks.releaseAll()
    await settle()
    const diag = createRecoveryDiagnostics(page('viewer').rt, { storage: local })
    const inv = await diag.inventory()
    expect(inv.emergency).toEqual([])
    expect(inv.drafts).toEqual([expect.objectContaining({ draftId: w.draftId(), lineage: 'dead-runtime' })])
    expect(await diag.readText({ source: 'record', expect: inv.drafts[0]!.expect })).toEqual({ ok: true, text: 'never reached storage' })
  })

  it('flags old, superseded and locally deleted drafts, warns over the budget, and removes nothing for it', async () => {
    const { page, idb, local } = setup()
    const draftId = await crashedDraft(page('gone'), 'ancient')
    const viewer = page('viewer').rt
    const [record] = await viewer.store.listAll()
    await putRows(idb, DRAFTS_STORE, [{ ...record!, updatedAt: 1, bodyLength: RECOVERY_WARN_BYTES }])
    local.setItem(SUPERSEDED_KEY_PREFIX + draftId, JSON.stringify({ pageInstanceId: record!.owner.pageInstanceId, generation: record!.generation }))
    local.setItem(DELETED_WORK_MARK_PREFIX + 'w1', JSON.stringify({ marked: 1, tried: 0 }))
    const diag = createRecoveryDiagnostics(viewer, { storage: local, now: () => 1 + OLD_DRAFT_MS + 1 })
    const inv = await diag.inventory()
    expect(inv.drafts[0]).toMatchObject<Partial<DiagnosticsDraft>>({ old: true, superseded: true, deletedHere: true })
    expect(inv.overBudget).toBe(true)
    await diag.inventory()
    expect(await bodies(viewer)).toEqual(['ancient'])
  })

  it('still lists emergency copies when recovery storage cannot be opened', async () => {
    const { page, idb, local } = setup()
    local.setItem(
      EMERGENCY_KEY_PREFIX + 'p-x',
      JSON.stringify({
        v: 1, pageInstanceId: 'p-x', runtimeId: null, at: 5,
        entries: [{ draftId: 'd-x', kind: 'work-research-note', entityType: 'work', entityId: 'w1', generation: 1, committedGeneration: 0, body: 'kept in localStorage',
          lineage: { createdAt: 1, owner: { runtimeId: null, pageInstanceId: 'p-x', paneId: 'tab-1' }, base: UNKNOWN_BASE } }],
      }),
    )
    idb.openFails = true
    const diag = createRecoveryDiagnostics(page('viewer').rt, { storage: local })
    const inv = await diag.inventory()
    expect(inv.storage).not.toBe('ok')
    expect(inv.emergency).toEqual([expect.objectContaining({ draftId: 'd-x', bodyLength: 'kept in localStorage'.length })])
    expect(await diag.readText({ source: 'emergency', key: EMERGENCY_KEY_PREFIX + 'p-x', draftId: 'd-x', generation: 1 })).toEqual({ ok: true, text: 'kept in localStorage' })
    expect(local.getItem(EMERGENCY_KEY_PREFIX + 'p-x')).not.toBeNull()
  })

  it('reports local storage it cannot enumerate instead of failing', async () => {
    const { page } = setup()
    const refusing = {
      get length(): number {
        throw new Error('SecurityError')
      },
      key: () => {
        throw new Error('SecurityError')
      },
      getItem: () => {
        throw new Error('SecurityError')
      },
      setItem: () => {
        throw new Error('SecurityError')
      },
      removeItem: () => undefined,
    }
    const inv = await createRecoveryDiagnostics(page('viewer').rt, { storage: refusing }).inventory()
    expect(inv).toMatchObject({ storage: 'ok', emergencyStorage: 'unavailable', drafts: [], emergency: [] })
  })

  it('without Web Locks, keeps a crashed tab\'s draft as unknown and reviewable', async () => {
    const { page, local } = setup(true)
    const tab = page('crashed')
    await tab.rt.start()
    const w = tab.rt.writers.openWriter({ kind: 'work-research-note', entityType: 'work', entityId: 'w1', paneId: 'tab-1', base: UNKNOWN_BASE })
    w.edit(1, 'lan crash')
    await w.flush()
    // Crashed: no pagehide record, nothing answers.
    tab.rt.identity.dispose()
    await settle()
    const diag = createRecoveryDiagnostics(page('viewer').rt, { storage: local })
    const [d] = (await diag.inventory()).drafts
    expect(d!.lineage).toBe('unknown')
    expect(await diag.readText({ source: 'record', expect: d!.expect })).toEqual({ ok: true, text: 'lan crash' })
  })
})
