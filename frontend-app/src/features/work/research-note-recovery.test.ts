/**
 * #466 slice 2: Research Notes sessions in `works.js` / `work-notes-state.js`
 * against a real editor-recovery runtime on the fake IndexedDB. A "reload"
 * disposes the page's runtime and session map and starts a new page on the
 * same IndexedDB, sessionStorage and localStorage.
 */
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest'
import ownerResourceSource from '../../../../frontend/js/owner-resource.js?raw'
import tabContextSource from '../../../../frontend/js/tab-context.js?raw'
import worksSource from '../../../../frontend/js/components/works.js?raw'
import workNotesStateSource from '../../../../frontend/js/work-notes-state.js?raw'
import * as recoveryApi from '../../lifecycle/editor-recovery-entry'
import type { EmergencyStorage } from '../../lifecycle/editor-recovery/emergency'
import type { EditorRecoveryRuntime } from '../../lifecycle/editor-recovery/runtime'
import { RUNTIME_SESSION_KEY, type DraftRecord } from '../../lifecycle/editor-recovery/schema'
import { createFakeBrowser } from '../../lifecycle/editor-recovery/test-support/fake-env'
import { createFakeIdb, settle } from '../../lifecycle/editor-recovery/test-support/fake-idb'

type Row = {
  op_id: string
  operation: string
  entity_type: string
  entity_id: string
  payload: { text: string }
  base_revision: number
  status: string
  attempt_count: number
}

type Entry = {
  text: string
  state: string
  editGeneration: number
  recovery: { draftId(): string | null; flush(): Promise<void>; state(): string } | null
  blockedBase?: { value: string; revision: number } | null
  ownQueuedText?: string
}

type Ctx = {
  tabId: string
  generation: number
  ui: { workResearchNoteSession: Entry | null; researchNotesRecovery?: { status: string; candidates: Array<{ reason: string; draftId: string }> } | null }
  root: HTMLElement
  setEntity: (type: string, value: unknown) => void
  getEntity: (type: string) => { id: string } | null
  getResource: (name: string) => unknown
  setResource: (name: string, value: unknown) => void
  resourceTicket: () => unknown
  registerResource: (ticket: unknown, registration: { kind: string; value: unknown; suspendable: boolean; dispose: () => void }) => string
  beginRoute: (route: { name: string; params: { workId: string } }) => number
}

type W = Record<string, unknown> & {
  eval: (code: string) => void
  prksMountTabContext: (tabId: string, host: HTMLElement) => void
  prksGetTabContext: (tabId: string) => Ctx
  prksDestroyAllTabContexts: () => void
  prksWorkNotesMarkEdit: (notes: object, workId: string, text: string, ctx: Ctx) => number
  prksEnqueueWorkResearchNotesSave: (ctx: Ctx, workId: string) => Promise<{ code: string; opId?: string | null }>
  prksResearchNotesTextForWork: (workId: string, serverText: string, ctx: Ctx) => string
  prksResetResearchDraftsForTest: () => void
  prksEnsureWorkNotesBase: (ctx: Ctx, work: object) => Promise<unknown>
  prksRememberWorkNotesCanonical: (ctx: Ctx, work: object, source?: string) => unknown
  prksBindWorkNotesSync: (ctx: Ctx) => void
  prksSetResearchRecoveryRestoreMsForTest: (ms: number | null) => void
  prksRestoreResearchNotesRecovery: (ctx: Ctx, work: object) => Promise<{ restored: boolean; review: Array<{ reason: string }> } | null>
}
const win = window as unknown as W

/* ---- a minimal durable queue with the store's coalescing and scope_busy rules ---- */
const sync = (() => {
  let rows: Row[] = []
  let seq = 0
  const listeners = new Set<(event: unknown) => void>()
  const busy = (msg: string) => Object.assign(new Error(msg), { prksLocalStoreCode: 'scope_busy' })
  const store = {
    async saveWorkNote(workId: string, operation: string, text: string, observed: { value: string; revision: number }) {
      const active = rows.filter((r) => r.entity_id === workId && r.operation === operation)
      if (active.length > 1) throw busy('two rows')
      const existing = active[0]
      if (existing) {
        if (existing.status !== 'pending' || existing.attempt_count > 0) throw busy('attempted')
        if (existing.payload.text === text) return existing
        rows = rows.filter((r) => r !== existing)
      }
      if (text === observed.value) return null
      const row: Row = {
        op_id: 'op-' + ++seq,
        operation,
        entity_type: 'work',
        entity_id: workId,
        payload: { text },
        base_revision: observed.revision,
        status: 'pending',
        attempt_count: 0,
      }
      rows.push(row)
      return row
    },
    async listOperations() {
      return rows.map((r) => ({ ...r, payload: { ...r.payload } }))
    },
  }
  const emit = (event: unknown) => listeners.forEach((fn) => fn(event))
  return {
    store,
    subscribe(fn: (event: unknown) => void) {
      listeners.add(fn)
      return () => listeners.delete(fn)
    },
    changed() {
      emit({})
    },
    rows: () => rows,
    reset() {
      rows = []
      seq = 0
    },
    attempt(opId: string) {
      const row = rows.find((r) => r.op_id === opId)!
      row.attempt_count = 1
    },
    ack(opId: string, revision: number) {
      const row = rows.find((r) => r.op_id === opId)!
      rows = rows.filter((r) => r !== row)
      server.text = row.payload.text
      server.revision = revision
      emit({ acknowledged: { code: 'ACKNOWLEDGED', server_revision: revision }, operation: row.operation, op: row })
    },
  }
})()

const server = { text: 'Saved note.', revision: 5, source: 'server' as 'server' | 'cache' }
/** What the Work detail read returned: the server's body, or an older cached one. */
const workRead = { text: null as string | null, source: 'server' as 'server' | 'cache' }

/* ---- one page: runtime, identity and TabContext ---- */
const browser = createFakeBrowser()
let idb = createFakeIdb()
let session = browser.sessionStorageWith({ [RUNTIME_SESSION_KEY]: 'r-' + 'a'.repeat(32) })
let local = memoryStorage()
let page: { rt: EditorRecoveryRuntime; locks: { releaseAll(): void }; window: EventTarget } | null = null
let pageSeq = 0
/** The LAN/HTTP deployment: no Web Locks, so a reload is recognized from the closed-page record. */
let withoutLocks = false

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

function startPage() {
  const name = 'page-' + ++pageSeq
  const locks = browser.locksFor(name)
  const pageWindow = new EventTarget()
  const rt = recoveryApi.createEditorRecoveryRuntime({
    store: { indexedDB: idb.factory },
    identity: {
      sessionStorage: session,
      locks: withoutLocks ? null : locks,
      createChannel: browser.channelFor(name),
      claimWaitMs: 20,
      window: pageWindow,
    },
    writers: { window: null, document: null },
    emergencyStorage: local,
  })
  page = { rt, locks, window: pageWindow }
  win.prksEditorRecovery = { ...recoveryApi, runtime: () => rt }
  return rt
}

function mount(tabId = 'tab-1', workId = 'w1'): Ctx {
  const host = document.createElement('div')
  document.body.appendChild(host)
  win.prksMountTabContext(tabId, host)
  const ctx = win.prksGetTabContext(tabId)
  ctx.root.innerHTML = '<div data-prks-role="editor-status"></div>'
  ctx.setEntity('work', { id: workId, text_content: server.text, private_notes: '' })
  return ctx
}

/** The pane's editor: what the save path reads. Built with the session text, as initEasyMDE is. */
function attachEditor(ctx: Ctx, workId = 'w1') {
  let text = win.prksResearchNotesTextForWork(workId, server.text, ctx)
  const notes = { workId, editGeneration: 0, editor: { value: (next?: string) => (next === undefined ? text : void (text = next)) } }
  ctx.registerResource(ctx.resourceTicket(), { kind: 'workNotes', value: notes, suspendable: true, dispose() {} })
  return notes
}

async function openWork(tabId = 'tab-1', workId = 'w1'): Promise<Ctx> {
  const ctx = mount(tabId, workId)
  // The mount path starts the runtime (restore) before the editor exists.
  await page!.rt.start()
  await (window as unknown as { prksRefreshPendingWorkNotes: () => Promise<unknown> }).prksRefreshPendingWorkNotes()
  // As the Work route does: the Work body and where it was read from, then notes-state.
  const body = workRead.text === null ? server.text : workRead.text
  win.prksRememberWorkNotesCanonical(ctx, { id: workId, text_content: body, private_notes: '' }, workRead.source)
  await win.prksEnsureWorkNotesBase(ctx, { id: workId, text_content: body, private_notes: '' })
  return ctx
}

function type(ctx: Ctx, text: string, workId = 'w1'): Entry {
  const notes = (ctx.getResource('workNotes') as ReturnType<typeof attachEditor> | null) || attachEditor(ctx, workId)
  notes.editor.value(text)
  win.prksWorkNotesMarkEdit(notes, workId, text, ctx)
  return ctx.ui.workResearchNoteSession as Entry
}

async function waitFor(check: () => boolean | Promise<boolean>, what: string) {
  for (let i = 0; i < 200; i++) {
    if (await check()) return
    await new Promise((resolve) => setTimeout(resolve, 5))
  }
  throw new Error('timed out: ' + what)
}

async function records(): Promise<DraftRecord[]> {
  return (await page!.rt.store.listAll()).filter((r) => r.status !== 'discarded')
}

async function bodyOf(draftId: string): Promise<string | null> {
  const row = await page!.rt.store.getBody(draftId)
  return row ? row.body : null
}

/** Unload: what the page committed or held stays; its writers, timers and locks go. */
async function reload(): Promise<EditorRecoveryRuntime> {
  page!.rt.writers.writeEmergencyNow()
  page!.window.dispatchEvent(new Event('pagehide'))
  page!.rt.dispose()
  page!.locks.releaseAll()
  win.prksResetResearchDraftsForTest()
  win.prksDestroyAllTabContexts()
  document.body.innerHTML = ''
  await settle()
  return startPage()
}

beforeAll(() => {
  win.prksSync = sync
  win.prksOfflineReadEntity = async (_type: string, workId: string) => ({
    value: { work_id: workId, research_note_revision: server.revision, private_note_revision: 0 },
    source: server.source,
    cachedAt: null,
  })
  win.prksOfflineInvalidateEntity = async () => undefined
  win.eval(ownerResourceSource)
  win.eval(tabContextSource)
  win.eval(workNotesStateSource)
  win.eval(worksSource)
})

beforeEach(() => {
  withoutLocks = false
  idb = createFakeIdb()
  session = browser.sessionStorageWith({ [RUNTIME_SESSION_KEY]: 'r-' + 'a'.repeat(32) })
  local = memoryStorage()
  sync.reset()
  server.text = 'Saved note.'
  server.revision = 5
  server.source = 'server'
  workRead.text = null
  workRead.source = 'server'
  startPage()
})

afterEach(async () => {
  page!.rt.dispose()
  page!.locks.releaseAll()
  win.prksResetResearchDraftsForTest()
  win.prksDestroyAllTabContexts()
  document.body.innerHTML = ''
  await settle()
})

describe('Research Notes edits reach the recovery writer', () => {
  it('records every edit as the session\'s lineage, typed on the observed server base', async () => {
    const ctx = await openWork()
    type(ctx, 'Saved note. One')
    const entry = type(ctx, 'Saved note. One two')
    expect(entry.recovery).toBeTruthy()
    await entry.recovery!.flush()
    const [record] = await records()
    expect(record).toMatchObject({
      generation: entry.editGeneration,
      entityKey: 'work-research-note:w1',
      base: { revision: 5, length: 'Saved note.'.length, source: 'server' },
      pipeline: { state: 'drafting' },
      owner: { paneId: 'tab-1' },
    })
    expect(record!.base.fingerprint).toBe(recoveryApi.fingerprintText('Saved note.'))
    expect(await bodyOf(record!.draftId)).toBe('Saved note. One two')
    // Recovery storage alone never reads as saved.
    expect(ctx.root.querySelector('[data-prks-role="editor-status"]')!.textContent).not.toContain('saved')
    expect(sync.rows()).toEqual([])
  })

  it('keeps two panes on one Work in separate lineages', async () => {
    const main = await openWork('tab-1')
    const side = await openWork('tab-2')
    const a = type(main, 'Main text')
    const b = type(side, 'Side text')
    await a.recovery!.flush()
    await b.recovery!.flush()
    const rows = await records()
    expect(rows).toHaveLength(2)
    expect(new Set(rows.map((r) => r.draftId)).size).toBe(2)
    expect(await bodyOf(a.recovery!.draftId()!)).toBe('Main text')
    expect(await bodyOf(b.recovery!.draftId()!)).toBe('Side text')
  })

  it('records the queued row and clears the record on that exact acknowledgement', async () => {
    const ctx = await openWork()
    const entry = type(ctx, 'Saved note. A')
    const result = await win.prksEnqueueWorkResearchNotesSave(ctx, 'w1')
    expect(result).toMatchObject({ code: 'saved', opId: 'op-1' })
    await waitFor(async () => (await records())[0]?.pipeline?.state === 'queued', 'queued pipeline')
    const [record] = await records()
    expect(record!.pipeline).toMatchObject({
      queuedOpId: 'op-1',
      queuedGeneration: entry.editGeneration,
      ownQueued: { opId: 'op-1', textLength: 'Saved note. A'.length, textFingerprint: recoveryApi.fingerprintText('Saved note. A') },
    })
    sync.ack('op-1', 6)
    await waitFor(async () => (await records()).length === 0, 'record cleared on ack')
  })

  it('never lets the acknowledgement of A clear newer text B', async () => {
    const ctx = await openWork()
    type(ctx, 'Saved note. A')
    await win.prksEnqueueWorkResearchNotesSave(ctx, 'w1')
    const entry = type(ctx, 'Saved note. A then B')
    await entry.recovery!.flush()
    sync.ack('op-1', 6)
    await settle()
    await new Promise((resolve) => setTimeout(resolve, 20))
    const rows = await records()
    expect(rows).toHaveLength(1)
    expect(await bodyOf(rows[0]!.draftId)).toBe('Saved note. A then B')
    // B's base is now its own acknowledged predecessor A.
    expect(rows[0]!.base).toMatchObject({ revision: 6, length: 'Saved note. A'.length, fingerprint: recoveryApi.fingerprintText('Saved note. A') })
  })

  it('records a scope_busy body as blocked with the base it was refused against', async () => {
    const ctx = await openWork()
    type(ctx, 'Saved note. A')
    await win.prksEnqueueWorkResearchNotesSave(ctx, 'w1')
    sync.attempt('op-1')
    const entry = type(ctx, 'Saved note. A then B')
    const result = await win.prksEnqueueWorkResearchNotesSave(ctx, 'w1')
    expect(result.code).toBe('scope_busy')
    await waitFor(async () => (await records())[0]?.pipeline?.state === 'blocked', 'blocked pipeline')
    const [record] = await records()
    expect(entry.state).toBe('blocked')
    expect(record!.pipeline).toMatchObject({
      blockedBase: { revision: 5, length: 'Saved note.'.length, fingerprint: recoveryApi.fingerprintText('Saved note.') },
      ownQueued: { opId: 'op-1' },
    })
    expect(await bodyOf(record!.draftId)).toBe('Saved note. A then B')
  })

  it('clears a draft that a save proves equal to the acknowledged note (A -> B -> A)', async () => {
    const ctx = await openWork()
    type(ctx, 'Saved note. B')
    const entry = type(ctx, 'Saved note.')
    await entry.recovery!.flush()
    expect(await records()).toHaveLength(1)
    const result = await win.prksEnqueueWorkResearchNotesSave(ctx, 'w1')
    expect(result).toMatchObject({ code: 'saved', opId: null })
    await waitFor(async () => (await records()).length === 0, 'equal draft cleared')
  })

  it('records a conflicted row of this session as conflict, and keeps the record', async () => {
    const ctx = await openWork()
    type(ctx, 'Saved note. A')
    await win.prksEnqueueWorkResearchNotesSave(ctx, 'w1')
    await waitFor(async () => (await records())[0]?.pipeline?.state === 'queued', 'queued pipeline')
    sync.rows()[0]!.status = 'conflict'
    sync.changed()
    await waitFor(async () => (await records())[0]?.pipeline?.state === 'conflict', 'conflict pipeline')
    expect(await bodyOf((await records())[0]!.draftId)).toBe('Saved note. A')
  })

  it('keeps the lineage when the session is dropped and only finishes its last write', async () => {
    const ctx = await openWork()
    const entry = type(ctx, 'Unsaved words')
    const writer = entry.recovery!
    win.prksResetResearchDraftsForTest()
    await waitFor(async () => (await records()).length === 1, 'released writer finished its write')
    expect(await bodyOf(writer.draftId()!)).toBe('Unsaved words')
    expect(page!.rt.writers.writers()).toHaveLength(0)
    void ctx
  })
})

describe('same-pane restore after reload', () => {
  async function typedThenReloaded(text: string) {
    const ctx = await openWork()
    const entry = type(ctx, text)
    await entry.recovery!.flush()
    await reload()
    return openWork()
  }

  it('restores the exact newest body when the server note is unchanged, then saves it normally', async () => {
    const ctx = await typedThenReloaded('Saved note. Typed before reload')
    const result = await win.prksRestoreResearchNotesRecovery(ctx, { id: 'w1' })
    expect(result).toMatchObject({ restored: true, review: [] })
    expect(win.prksResearchNotesTextForWork('w1', server.text, ctx)).toBe('Saved note. Typed before reload')
    const entry = ctx.ui.workResearchNoteSession!
    expect(entry.state).toBe('drafting')
    expect(sync.rows()).toEqual([])
    attachEditor(ctx)
    // Owned by this page now; the ordinary save path queues it and the ack clears it.
    const saved = await win.prksEnqueueWorkResearchNotesSave(ctx, 'w1')
    expect(saved.code).toBe('saved')
    expect(sync.rows().map((r) => r.payload.text)).toEqual(['Saved note. Typed before reload'])
    sync.ack(saved.opId as string, 6)
    await waitFor(async () => (await records()).length === 0, 'restored record cleared on ack')
  })

  it('restores without Web Locks (LAN over HTTP) from this tab\'s closed-page record', async () => {
    withoutLocks = true
    startPage()
    const ctx = await typedThenReloaded('Saved note. On the LAN')
    expect(page!.rt.identity.current()!.verified).toBe('channel')
    expect(await win.prksRestoreResearchNotesRecovery(ctx, { id: 'w1' })).toMatchObject({ restored: true })
    expect(win.prksResearchNotesTextForWork('w1', server.text, ctx)).toBe('Saved note. On the LAN')
  })

  it('restores text that only reached the emergency entry', async () => {
    const ctx = await openWork()
    type(ctx, 'Saved note. Never committed')
    await reload()
    const fresh = await openWork()
    expect(await win.prksRestoreResearchNotesRecovery(fresh, { id: 'w1' })).toMatchObject({ restored: true })
    expect(win.prksResearchNotesTextForWork('w1', server.text, fresh)).toBe('Saved note. Never committed')
    void ctx
  })

  it('keeps the draft and changes nothing when the server note changed elsewhere', async () => {
    const ctx = await openWork()
    const entry = type(ctx, 'Saved note. Mine')
    await entry.recovery!.flush()
    await reload()
    server.text = 'Another device wrote this.'
    server.revision = 6
    const fresh = await openWork()
    const result = await win.prksRestoreResearchNotesRecovery(fresh, { id: 'w1' })
    expect(result).toMatchObject({ restored: false, review: [{ reason: 'base-advanced' }] })
    expect(fresh.ui.researchNotesRecovery).toMatchObject({ status: 'needs-review', candidates: [{ reason: 'base-advanced' }] })
    expect(win.prksResearchNotesTextForWork('w1', server.text, fresh)).toBe('Another device wrote this.')
    expect(sync.rows()).toEqual([])
    const [record] = await records()
    expect(await bodyOf(record!.draftId)).toBe('Saved note. Mine')
  })

  it('keeps the draft without applying it when the base cannot be verified with the server', async () => {
    const ctx = await typedThenReloaded('Saved note. Offline')
    void ctx
    server.source = 'cache'
    const fresh = await openWork('tab-1')
    const result = await win.prksRestoreResearchNotesRecovery(fresh, { id: 'w1' })
    expect(result).toMatchObject({ restored: false, review: [{ reason: 'base-unverified' }] })
    expect(sync.rows()).toEqual([])
    expect(await records()).toHaveLength(1)
  })

  it('keeps the draft without applying it when the queue cannot be read', async () => {
    const ctx = await typedThenReloaded('Saved note. Queue unknown')
    // A foreign, unattempted row the restored body must never replace.
    await sync.store.saveWorkNote('w1', 'SET_WORK_RESEARCH_NOTE', 'Saved note. Foreign A', { value: server.text, revision: server.revision })
    const list = sync.store.listOperations
    sync.store.listOperations = async () => {
      throw new Error('queue unavailable')
    }
    try {
      const result = await win.prksRestoreResearchNotesRecovery(ctx, { id: 'w1' })
      expect(result).toMatchObject({ restored: false, review: [{ reason: 'queue-unknown' }] })
    } finally {
      sync.store.listOperations = list
    }
    expect(ctx.ui.workResearchNoteSession).toBeNull()
    expect(await records()).toHaveLength(1)
    // Storage works again: the foreign row is intact and nothing was enqueued.
    expect(sync.rows().map((r) => r.payload.text)).toEqual(['Saved note. Foreign A'])
  })

  it('keeps a draft whose newer tail never reached storage, even when its stored body equals the note', async () => {
    const ctx = await openWork()
    // Generation 1 equals the server note; a newer, oversized generation only left a bodyless tail.
    await type(ctx, 'Saved note.').recovery!.flush()
    await reload()
    const [stored] = await records()
    expect(await bodyOf(stored!.draftId)).toBe('Saved note.')
    const outcome = await page!.rt.store.applyEmergencyEntry(
      { v: 1, pageInstanceId: stored!.owner.pageInstanceId, runtimeId: stored!.owner.runtimeId, at: Date.now(), entries: [] },
      { draftId: stored!.draftId, kind: 'work-research-note', entityType: 'work', entityId: 'w1', generation: stored!.generation + 1, committedGeneration: stored!.generation, body: null },
    )
    expect(outcome).toBe('tail-missing')
    const fresh = await openWork()
    const result = await win.prksRestoreResearchNotesRecovery(fresh, { id: 'w1' })
    expect(sync.rows()).toEqual([])
    expect(result).toMatchObject({ restored: false, review: [{ reason: 'tail-missing' }] })
    await settle()
    expect(await records()).toHaveLength(1)
  })

  it('does not restore another pane\'s draft into this pane', async () => {
    const side = await openWork('tab-2')
    const entry = type(side, 'Saved note. Side pane')
    await entry.recovery!.flush()
    await reload()
    const main = await openWork('tab-1')
    const result = await win.prksRestoreResearchNotesRecovery(main, { id: 'w1' })
    expect(result).toMatchObject({ restored: false, review: [{ reason: 'other-source' }] })
    expect(win.prksResearchNotesTextForWork('w1', server.text, main)).toBe('Saved note.')
  })

  it('does not restore while two drafts exist for the Work', async () => {
    const main = await openWork('tab-1')
    const side = await openWork('tab-2')
    await type(main, 'Main words').recovery!.flush()
    await type(side, 'Side words').recovery!.flush()
    await reload()
    const fresh = await openWork('tab-1')
    const result = await win.prksRestoreResearchNotesRecovery(fresh, { id: 'w1' })
    expect(result!.restored).toBe(false)
    expect(result!.review.map((r) => r.reason)).toEqual(['multiple-drafts', 'multiple-drafts'])
    expect(await records()).toHaveLength(2)
  })

  it('clears a draft exactly equal to the server note', async () => {
    const ctx = await openWork()
    type(ctx, 'Saved note. A')
    const entry = type(ctx, 'Saved note. A done')
    await entry.recovery!.flush()
    await reload()
    server.text = 'Saved note. A done'
    server.revision = 6
    const fresh = await openWork()
    const result = await win.prksRestoreResearchNotesRecovery(fresh, { id: 'w1' })
    expect(result).toMatchObject({ restored: false, review: [] })
    await waitFor(async () => (await records()).length === 0, 'equal draft cleared')
  })

  it('a stale mount restores nothing and leaves the lineage for the next mount', async () => {
    const ctx = await typedThenReloaded('Saved note. Stale mount')
    const pending = win.prksRestoreResearchNotesRecovery(ctx, { id: 'w1' })
    ctx.beginRoute({ name: 'work', params: { workId: 'w2' } })
    ctx.setEntity('work', { id: 'w2', text_content: '', private_notes: '' })
    expect(await pending).toBeNull()
    expect(ctx.ui.workResearchNoteSession).toBeNull()
    await settle()
    expect(page!.rt.writers.writers()).toHaveLength(0)
    const again = await openWork('tab-1')
    expect(await win.prksRestoreResearchNotesRecovery(again, { id: 'w1' })).toMatchObject({ restored: true })
    expect(win.prksResearchNotesTextForWork('w1', server.text, again)).toBe('Saved note. Stale mount')
  })

  it('leaves a body already queued for its row\'s acknowledgement, and the pane continues that lineage', async () => {
    const ctx = await openWork()
    type(ctx, 'Saved note. Queued')
    const saved = await win.prksEnqueueWorkResearchNotesSave(ctx, 'w1')
    await waitFor(async () => (await records())[0]?.pipeline?.state === 'queued', 'queued recorded')
    await reload()
    const fresh = await openWork()
    const result = await win.prksRestoreResearchNotesRecovery(fresh, { id: 'w1' })
    expect(result).toMatchObject({ restored: false, review: [] })
    expect(fresh.ui.workResearchNoteSession).toBeNull()
    expect(await records()).toHaveLength(1)
    // The ack of the queued row clears exactly that generation.
    sync.ack(saved.opId as string, 6)
    await waitFor(async () => (await records()).length === 0, 'represented record cleared on its ack')
  })

  it('never clears a queued body whose owner may still be live when its row acknowledges', async () => {
    const ctx = await openWork()
    type(ctx, 'Saved note. Queued by A')
    const saved = await win.prksEnqueueWorkResearchNotesSave(ctx, 'w1')
    await waitFor(async () => (await records())[0]?.pipeline?.state === 'queued', 'queued recorded')
    await reload()
    // Page A is frozen: its lineage cannot be classified.
    const classify = page!.rt.classify.bind(page!.rt)
    page!.rt.classify = async () => 'unknown'
    let result
    const fresh = await openWork()
    try {
      result = await win.prksRestoreResearchNotesRecovery(fresh, { id: 'w1' })
    } finally {
      page!.rt.classify = classify
    }
    expect(result).toMatchObject({ restored: false, review: [{ reason: 'ownership-unknown' }] })
    sync.ack(saved.opId as string, 6)
    await settle()
    expect(await records()).toHaveLength(1)
  })

  it('never clears a represented body that another page adopted before its row acknowledged', async () => {
    const side = await openWork('tab-2')
    type(side, 'Saved note. Queued in side pane')
    const saved = await win.prksEnqueueWorkResearchNotesSave(side, 'w1')
    await waitFor(async () => (await records())[0]?.pipeline?.state === 'queued', 'queued recorded')
    await reload()
    const main = await openWork('tab-1')
    // Another pane's lineage: watched for its ack, not adopted here.
    expect(await win.prksRestoreResearchNotesRecovery(main, { id: 'w1' })).toMatchObject({ restored: false, review: [] })
    const [record] = await records()
    const adopted = await page!.rt.store.adopt(record!.draftId, record!.owner.pageInstanceId, {
      runtimeId: 'r-other',
      pageInstanceId: 'p-adopter',
      paneId: 'tab-9',
      claimedAt: Date.now(),
    })
    expect(adopted.outcome).toBe('ok')
    sync.ack(saved.opId as string, 6)
    await settle()
    const [kept] = await records()
    expect(kept && kept.owner.pageInstanceId).toBe('p-adopter')
  })

  it('an acknowledgement of the represented row does not clear text typed after the restore', async () => {
    const ctx = await openWork()
    type(ctx, 'Saved note. Queued')
    const saved = await win.prksEnqueueWorkResearchNotesSave(ctx, 'w1')
    await waitFor(async () => (await records())[0]?.pipeline?.state === 'queued', 'queued recorded')
    const [before] = await records()
    await reload()
    const fresh = await openWork()
    await win.prksRestoreResearchNotesRecovery(fresh, { id: 'w1' })
    const entry = type(fresh, 'Saved note. Queued, then more')
    await entry.recovery!.flush()
    const after = await records()
    expect(after.map((r) => r.draftId)).toEqual([before!.draftId])
    sync.ack(saved.opId as string, 6)
    await settle()
    const [kept] = await records()
    expect(kept && (await bodyOf(kept.draftId))).toBe('Saved note. Queued, then more')
  })

  it('a timed-out restore does not hold up the next Work\'s restore, and paints nothing when it finishes late', async () => {
    const main = await openWork('tab-1', 'w1')
    const side = await openWork('tab-2', 'w2')
    await type(main, 'Saved note. Stuck W1', 'w1').recovery!.flush()
    await type(side, 'Saved note. Ready W2', 'w2').recovery!.flush()
    await reload()
    const stuckMain = await openWork('tab-1', 'w1')
    const readySide = await openWork('tab-2', 'w2')
    const [w1Before] = (await records()).filter((r) => r.entityId === 'w1')
    win.prksSetResearchRecoveryRestoreMsForTest(50)
    const scan = page!.rt.scanEmergency.bind(page!.rt)
    let release!: () => void
    const hung = new Promise<void>((resolve) => (release = resolve))
    page!.rt.scanEmergency = async () => {
      page!.rt.scanEmergency = scan
      await hung
      return scan()
    }
    try {
      // Stuck in storage past the timeout: abandoned, the editor opens without recovery.
      expect(await win.prksRestoreResearchNotesRecovery(stuckMain, { id: 'w1' })).toBeNull()
      // The next restore is not queued behind the stuck one.
      expect(await win.prksRestoreResearchNotesRecovery(readySide, { id: 'w2' })).toMatchObject({ restored: true })
      expect(win.prksResearchNotesTextForWork('w2', server.text, readySide)).toBe('Saved note. Ready W2')
      // The stuck run finishing late neither paints nor adopts.
      release()
      await settle()
      await new Promise((resolve) => setTimeout(resolve, 20))
      expect(stuckMain.ui.workResearchNoteSession).toBeNull()
      expect(win.prksResearchNotesTextForWork('w1', server.text, stuckMain)).toBe('Saved note.')
      const [w1After] = (await records()).filter((r) => r.entityId === 'w1')
      expect(w1After!.owner.pageInstanceId).toBe(w1Before!.owner.pageInstanceId)
    } finally {
      page!.rt.scanEmergency = scan
      win.prksSetResearchRecoveryRestoreMsForTest(null)
    }
  })

  describe('#488 follow-up: Greptile findings', () => {
    it('keeps a draft equal to a cached Work body that the server has since replaced', async () => {
      const ctx = await openWork()
      type(ctx, 'Saved note. Edited')
      // Back to the note it was typed on: equal to the body the cache holds.
      await type(ctx, 'Saved note.').recovery!.flush()
      await reload()
      // Another device changed the note; this load paints the cached Work, then reaches notes-state online.
      server.text = 'Another device wrote this.'
      server.revision = 6
      workRead.text = 'Saved note.'
      workRead.source = 'cache'
      const fresh = await openWork()
      const result = await win.prksRestoreResearchNotesRecovery(fresh, { id: 'w1' })
      expect(result).toMatchObject({ restored: false, review: [{ reason: 'base-unverified' }] })
      await settle()
      const [record] = await records()
      expect(record && (await bodyOf(record.draftId))).toBe('Saved note.')
      expect(sync.rows()).toEqual([])
    })

    it('treats a cached Work body as verified once its acknowledgement arrives', async () => {
      workRead.source = 'cache'
      const ctx = await openWork()
      win.prksBindWorkNotesSync(ctx)
      type(ctx, 'Saved note. A')
      const saved = await win.prksEnqueueWorkResearchNotesSave(ctx, 'w1')
      sync.ack(saved.opId as string, 6)
      expect(ctx.getResource('workNotesObserved')).toMatchObject({ research: { value: 'Saved note. A', revision: 6, source: 'server' } })
    })

    it('does not apply a restored draft after another pane saved while it was being adopted', async () => {
      const ctx = await typedThenReloaded('Saved note. Mine before reload')
      win.prksBindWorkNotesSync(ctx)
      const side = await openWork('tab-2')
      const adopt = page!.rt.writers.adopt.bind(page!.rt.writers)
      page!.rt.writers.adopt = async (record, options) => {
        page!.rt.writers.adopt = adopt
        // The other pane edits, saves, and its row is acknowledged mid-adoption.
        type(side, 'Saved note. Side pane saved')
        const saved = await win.prksEnqueueWorkResearchNotesSave(side, 'w1')
        sync.ack(saved.opId as string, 6)
        return adopt(record, options)
      }
      let result
      try {
        result = await win.prksRestoreResearchNotesRecovery(ctx, { id: 'w1' })
      } finally {
        page!.rt.writers.adopt = adopt
      }
      expect(result).toMatchObject({ restored: false, review: [{ reason: 'base-advanced' }] })
      expect(ctx.ui.workResearchNoteSession).toBeNull()
      expect(server.text).toBe('Saved note. Side pane saved')
      expect(sync.rows()).toEqual([])
      await settle()
      const mine = (await records()).filter((r) => r.owner.paneId === 'tab-1')
      expect(mine).toHaveLength(1)
      expect(await bodyOf(mine[0]!.draftId)).toBe('Saved note. Mine before reload')
    })

    it('does not apply a restored draft after a row was queued while it was being adopted', async () => {
      const ctx = await typedThenReloaded('Saved note. Mine before reload')
      const adopt = page!.rt.writers.adopt.bind(page!.rt.writers)
      page!.rt.writers.adopt = async (record, options) => {
        page!.rt.writers.adopt = adopt
        await sync.store.saveWorkNote('w1', 'SET_WORK_RESEARCH_NOTE', 'Saved note. Queued elsewhere', { value: server.text, revision: server.revision })
        return adopt(record, options)
      }
      let result
      try {
        result = await win.prksRestoreResearchNotesRecovery(ctx, { id: 'w1' })
      } finally {
        page!.rt.writers.adopt = adopt
      }
      expect(result).toMatchObject({ restored: false, review: [{ reason: 'foreign-queue' }] })
      expect(ctx.ui.workResearchNoteSession).toBeNull()
      expect(sync.rows().map((r) => r.payload.text)).toEqual(['Saved note. Queued elsewhere'])
      // The lineage stays this pane's orphan: the next mount sees it again.
      await settle()
      expect(page!.rt.writers.writers()).toHaveLength(0)
      const [record] = await records()
      expect(await bodyOf(record!.draftId)).toBe('Saved note. Mine before reload')
    })

    it('does not apply a restored draft after another pane started editing while it was being adopted', async () => {
      const ctx = await typedThenReloaded('Saved note. Mine before reload')
      const side = await openWork('tab-2')
      const adopt = page!.rt.writers.adopt.bind(page!.rt.writers)
      page!.rt.writers.adopt = async (record, options) => {
        page!.rt.writers.adopt = adopt
        type(side, 'Saved note. Side pane typing')
        return adopt(record, options)
      }
      let result
      try {
        result = await win.prksRestoreResearchNotesRecovery(ctx, { id: 'w1' })
      } finally {
        page!.rt.writers.adopt = adopt
      }
      expect(result).toMatchObject({ restored: false, review: [{ reason: 'dirty-session' }] })
      expect(ctx.ui.workResearchNoteSession).toBeNull()
    })

    it('clears a represented draft whose row acknowledged while it was being adopted', async () => {
      const ctx = await openWork()
      type(ctx, 'Saved note. Queued')
      const saved = await win.prksEnqueueWorkResearchNotesSave(ctx, 'w1')
      await waitFor(async () => (await records())[0]?.pipeline?.state === 'queued', 'queued recorded')
      await reload()
      const fresh = await openWork()
      const adopt = page!.rt.writers.adopt.bind(page!.rt.writers)
      page!.rt.writers.adopt = async (record, options) => {
        page!.rt.writers.adopt = adopt
        sync.ack(saved.opId as string, 6)
        return adopt(record, options)
      }
      try {
        expect(await win.prksRestoreResearchNotesRecovery(fresh, { id: 'w1' })).toMatchObject({ restored: false, review: [] })
      } finally {
        page!.rt.writers.adopt = adopt
      }
      await waitFor(async () => (await records()).length === 0, 'represented record cleared')
      await waitFor(() => page!.rt.writers.writers().length === 0, 'adopted writer released')
      // A later mount finds nothing left to report as live elsewhere.
      const again = await openWork('tab-1')
      expect(await win.prksRestoreResearchNotesRecovery(again, { id: 'w1' })).toBeNull()
    })

    it('a stale mount gives back a represented lineage even when its row acknowledged mid-adoption', async () => {
      const ctx = await openWork()
      type(ctx, 'Saved note. Queued')
      const saved = await win.prksEnqueueWorkResearchNotesSave(ctx, 'w1')
      await waitFor(async () => (await records())[0]?.pipeline?.state === 'queued', 'queued recorded')
      await reload()
      const fresh = await openWork()
      const adopt = page!.rt.writers.adopt.bind(page!.rt.writers)
      page!.rt.writers.adopt = async (record, options) => {
        page!.rt.writers.adopt = adopt
        sync.ack(saved.opId as string, 6)
        fresh.beginRoute({ name: 'work', params: { workId: 'w2' } })
        fresh.setEntity('work', { id: 'w2', text_content: '', private_notes: '' })
        return adopt(record, options)
      }
      try {
        expect(await win.prksRestoreResearchNotesRecovery(fresh, { id: 'w1' })).toMatchObject({ restored: false })
        expect(fresh.ui.workResearchNoteSession).toBeNull()
      } finally {
        page!.rt.writers.adopt = adopt
      }
      await waitFor(async () => (await records()).length === 0, 'represented record cleared')
      await waitFor(() => page!.rt.writers.writers().length === 0, 'adopted writer released')
    })
  })

  describe('#475 blocked body', () => {
    async function blockedThenReloaded() {
      const ctx = await openWork()
      type(ctx, 'Saved note. A')
      await win.prksEnqueueWorkResearchNotesSave(ctx, 'w1')
      sync.attempt('op-1')
      type(ctx, 'Saved note. A then B')
      await win.prksEnqueueWorkResearchNotesSave(ctx, 'w1')
      await waitFor(async () => (await records())[0]?.pipeline?.state === 'blocked', 'blocked recorded')
      await reload()
    }

    it('survives reload as blocked behind its own unsettled predecessor', async () => {
      await blockedThenReloaded()
      const fresh = await openWork()
      const result = await win.prksRestoreResearchNotesRecovery(fresh, { id: 'w1' })
      expect(result).toMatchObject({ restored: true })
      const entry = fresh.ui.workResearchNoteSession!
      expect(entry.state).toBe('blocked')
      expect(entry.text).toBe('Saved note. A then B')
      expect(entry.ownQueuedText).toBe('Saved note. A')
      expect(entry.blockedBase).toEqual({ value: 'Saved note.', revision: 5 })
      expect(sync.rows().map((r) => r.payload.text)).toEqual(['Saved note. A'])
    })

    it('keeps a blocked body that reverts to the server note behind its own predecessor', async () => {
      const ctx = await openWork()
      type(ctx, 'Saved note. A')
      await win.prksEnqueueWorkResearchNotesSave(ctx, 'w1')
      sync.attempt('op-1')
      type(ctx, 'Saved note.')
      await win.prksEnqueueWorkResearchNotesSave(ctx, 'w1')
      await waitFor(async () => (await records())[0]?.pipeline?.state === 'blocked', 'blocked recorded')
      await reload()
      const fresh = await openWork()
      const result = await win.prksRestoreResearchNotesRecovery(fresh, { id: 'w1' })
      expect(result).toMatchObject({ restored: true })
      expect(fresh.ui.workResearchNoteSession).toMatchObject({ state: 'blocked', text: 'Saved note.' })
      const [record] = await records()
      expect(await bodyOf(record!.draftId)).toBe('Saved note.')
      sync.ack('op-1', 6)
      await settle()
      // A is acknowledged; the newer intent (back to the old note) is still recoverable and unsaved.
      expect(server.text).toBe('Saved note. A')
      const [kept] = await records()
      expect(kept && (await bodyOf(kept.draftId))).toBe('Saved note.')
    })

    it('resumes on its own acknowledged predecessor', async () => {
      await blockedThenReloaded()
      sync.ack('op-1', 6)
      const fresh = await openWork()
      expect(await win.prksRestoreResearchNotesRecovery(fresh, { id: 'w1' })).toMatchObject({ restored: true })
      expect(fresh.ui.workResearchNoteSession).toMatchObject({ state: 'drafting', text: 'Saved note. A then B' })
    })

    it('is not rebased over a foreign edit', async () => {
      await blockedThenReloaded()
      sync.ack('op-1', 6)
      server.text = 'Saved note. A, then someone else'
      server.revision = 7
      const fresh = await openWork()
      const result = await win.prksRestoreResearchNotesRecovery(fresh, { id: 'w1' })
      expect(result).toMatchObject({ restored: false, review: [{ reason: 'base-advanced' }] })
      expect(sync.rows()).toEqual([])
    })
  })
})
