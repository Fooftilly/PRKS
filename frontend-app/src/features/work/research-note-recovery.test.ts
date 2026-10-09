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
import { RUNTIME_SESSION_KEY, UNKNOWN_BASE, type DraftRecord } from '../../lifecycle/editor-recovery/schema'
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
  prksRefreshResearchNotesRecovery: (ctx: Ctx, workId: string) => Promise<unknown>
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
      localStorage: local,
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

/** The tab closes (a final pagehide) and the Work is opened again in a new tab. */
async function closeTabAndOpenAnother(how: 'close' | 'crash' = 'close'): Promise<EditorRecoveryRuntime> {
  if (how === 'close') {
    page!.rt.writers.writeEmergencyNow()
    page!.window.dispatchEvent(new Event('pagehide'))
  }
  page!.rt.dispose()
  // The browser drops a closed or crashed page's locks either way.
  page!.locks.releaseAll()
  win.prksResetResearchDraftsForTest()
  win.prksDestroyAllTabContexts()
  document.body.innerHTML = ''
  await settle()
  // A new tab: its own sessionStorage, so no runtime id and no closed-page list.
  session = browser.sessionStorageWith()
  return startPage()
}

/** Another page of the origin, alive, with its own runtime and writers. */
function otherPage(name: string) {
  const locks = browser.locksFor(name)
  const pageWindow = new EventTarget()
  const rt = recoveryApi.createEditorRecoveryRuntime({
    store: { indexedDB: idb.factory },
    identity: {
      sessionStorage: browser.sessionStorageWith(),
      locks: withoutLocks ? null : locks,
      createChannel: browser.channelFor(name),
      claimWaitMs: 20,
      window: pageWindow,
      localStorage: local,
    },
    writers: { window: null, document: null },
    emergencyStorage: local,
  })
  return { rt, locks, window: pageWindow }
}

type Details = {
  token: string
  current: { text: string; revision: number | null; source: string; queue: string }
  candidates: Array<{ draftId: string; lineage: string; reason: string; action: string | null; body: string | null; status: string; expect: { draftId: string; pageInstanceId: string; generation: number; status: string } }>
}
type Review = {
  details: (ctx: Ctx, workId: string) => Promise<Details | null>
  restore: (ctx: Ctx, workId: string, token: string, expect: unknown) => Promise<{ ok: boolean; code?: string }>
  replace: (ctx: Ctx, workId: string, token: string, expect: unknown, text: string, shown: { text: string; revision: number | null }) => Promise<{ ok: boolean; code?: string }>
  discard: (ctx: Ctx, workId: string, token: string, expect: unknown) => Promise<{ ok: boolean; code?: string }>
  view: (ctx: Ctx) => { drafts: number; incomplete: number; unprotected: string | null } | null
}
function review(): Review {
  const w = window as unknown as Record<string, unknown>
  return {
    details: w.prksResearchNotesRecoveryDetails as Review['details'],
    restore: w.prksResearchNotesRecoveryRestore as Review['restore'],
    replace: w.prksResearchNotesRecoveryReplace as Review['replace'],
    discard: w.prksResearchNotesRecoveryDiscard as Review['discard'],
    view: w.prksResearchNotesRecoveryView as Review['view'],
  }
}

/** Mount as the Work route does: restore, then the editor reads the session text. */
async function openAndRestore(tabId = 'tab-1') {
  const ctx = await openWork(tabId)
  const result = await win.prksRestoreResearchNotesRecovery(ctx, { id: 'w1' })
  const notes = attachEditor(ctx)
  return { ctx, result, notes }
}

beforeAll(() => {
  win.prksSync = sync
  win.prksOfflineReadEntity = async (type: string, workId: string) => ({
    value: type === 'work'
      ? { id: workId, text_content: server.text, private_notes: '' }
      : { work_id: workId, research_note_revision: server.revision, private_note_revision: 0 },
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

  it('restores the draft of a pane that no longer exists into the pane that opens the Work', async () => {
    const side = await openWork('tab-2')
    const entry = type(side, 'Saved note. Side pane')
    await entry.recovery!.flush()
    await reload()
    const main = await openWork('tab-1')
    const result = await win.prksRestoreResearchNotesRecovery(main, { id: 'w1' })
    expect(result).toMatchObject({ restored: true, review: [] })
    expect(win.prksResearchNotesTextForWork('w1', server.text, main)).toBe('Saved note. Side pane')
    expect(sync.rows()).toEqual([])
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
      // Out of time, but not out of reach: the notice still lists the draft, unchecked.
      expect(review().view(stuckMain)).toMatchObject({ drafts: 1 })
      expect(stuckMain.ui.researchNotesRecovery).toMatchObject({ candidates: [{ reason: 'ownership-unknown', action: null }] })
    } finally {
      page!.rt.scanEmergency = scan
      win.prksSetResearchRecoveryRestoreMsForTest(null)
    }
  })

  it('a restore that runs out of time while adopting keeps the unchecked notice', async () => {
    const ctx = await openWork()
    await type(ctx, 'Saved note. Slow adopt').recovery!.flush()
    await reload()
    const fresh = await openWork()
    win.prksSetResearchRecoveryRestoreMsForTest(50)
    const writers = page!.rt.writers
    const adopt = writers.adopt.bind(writers)
    let release!: () => void
    const hung = new Promise<void>((resolve) => (release = resolve))
    let adopting = false
    writers.adopt = async (record, options) => {
      writers.adopt = adopt
      adopting = true
      await hung
      return adopt(record, options)
    }
    try {
      expect(await win.prksRestoreResearchNotesRecovery(fresh, { id: 'w1' })).toBeNull()
      expect(adopting).toBe(true)
      await waitFor(() => !!fresh.ui.researchNotesRecovery, 'unchecked notice')
      // The abandoned run finishes late: it gives the draft back and leaves the notice alone.
      release()
      await settle()
      await new Promise((resolve) => setTimeout(resolve, 20))
      expect(fresh.ui.workResearchNoteSession).toBeNull()
      expect(review().view(fresh)).toMatchObject({ drafts: 1 })
      expect(fresh.ui.researchNotesRecovery).toMatchObject({ candidates: [{ reason: 'ownership-unknown', action: null }] })
    } finally {
      writers.adopt = adopt
      win.prksSetResearchRecoveryRestoreMsForTest(null)
    }
  })

  describe('#488 follow-up: Greptile findings', () => {
    /** Restores with `hook` run inside the adoption, before or after the store adopts. */
    async function restoreAdopting(ctx: Ctx, hook: () => unknown, when: 'before' | 'after' = 'before') {
      const writers = page!.rt.writers
      const adopt = writers.adopt.bind(writers)
      writers.adopt = async (record, options) => {
        writers.adopt = adopt
        if (when === 'before') await hook()
        const writer = await adopt(record, options)
        if (when === 'after') await hook()
        return writer
      }
      try {
        return await win.prksRestoreResearchNotesRecovery(ctx, { id: 'w1' })
      } finally {
        writers.adopt = adopt
      }
    }

    /** This pane queued a body, then the page reloaded: a represented same-pane orphan. */
    async function queuedThenReloaded(): Promise<string> {
      const ctx = await openWork()
      type(ctx, 'Saved note. Queued')
      const saved = await win.prksEnqueueWorkResearchNotesSave(ctx, 'w1')
      await waitFor(async () => (await records())[0]?.pipeline?.state === 'queued', 'queued recorded')
      await reload()
      return saved.opId as string
    }

    function leaveWork(ctx: Ctx) {
      ctx.beginRoute({ name: 'work', params: { workId: 'w2' } })
      ctx.setEntity('work', { id: 'w2', text_content: '', private_notes: '' })
    }

    async function expectKept(body: string) {
      await settle()
      const kept = await records()
      expect(kept).toHaveLength(1)
      expect(await bodyOf(kept[0]!.draftId)).toBe(body)
    }

    /** The draft equals the note it was typed on, then the page reloads. */
    async function revertedThenReloaded() {
      const ctx = await openWork()
      type(ctx, 'Saved note. Edited')
      await type(ctx, 'Saved note.').recovery!.flush()
      await reload()
    }

    it('keeps a draft equal to a cached Work body that the server has since replaced', async () => {
      await revertedThenReloaded()
      // Another device changed the note; this load paints the cached Work, then reaches notes-state online.
      server.text = 'Another device wrote this.'
      server.revision = 6
      workRead.text = 'Saved note.'
      workRead.source = 'cache'
      const fresh = await openWork()
      const result = await win.prksRestoreResearchNotesRecovery(fresh, { id: 'w1' })
      expect(result).toMatchObject({ restored: false, review: [{ reason: 'base-unverified' }] })
      await expectKept('Saved note.')
      expect(sync.rows()).toEqual([])
    })

    it('keeps a draft equal to a server Work body read before another device saved', async () => {
      await revertedThenReloaded()
      // Work GET returns X at r5 from the server; another device saves Y (r6) before notes-state is read.
      workRead.text = 'Saved note.'
      server.text = 'Another device wrote this.'
      server.revision = 6
      const fresh = await openWork()
      const result = await win.prksRestoreResearchNotesRecovery(fresh, { id: 'w1' })
      expect(result).toMatchObject({ restored: false, review: [{ reason: 'base-unverified' }] })
      await expectKept('Saved note.')
      expect(sync.rows()).toEqual([])
    })

    it('does not restore on a base whose body and revision are not one server snapshot', async () => {
      // Both loads join the older server body to the newer revision, so the record's base matches K.
      server.text = 'Another device wrote this.'
      server.revision = 6
      workRead.text = 'Saved note.'
      const ctx = await openWork()
      await type(ctx, 'Saved note. Mine').recovery!.flush()
      await reload()
      const fresh = await openWork()
      const result = await win.prksRestoreResearchNotesRecovery(fresh, { id: 'w1' })
      expect(result).toMatchObject({ restored: false, review: [{ reason: 'base-unverified' }] })
      expect(fresh.ui.workResearchNoteSession).toBeNull()
      expect(sync.rows()).toEqual([])
      await expectKept('Saved note. Mine')
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
      const result = await restoreAdopting(ctx, async () => {
        // The other pane edits, saves, and its row is acknowledged mid-adoption.
        type(side, 'Saved note. Side pane saved')
        const saved = await win.prksEnqueueWorkResearchNotesSave(side, 'w1')
        sync.ack(saved.opId as string, 6)
      })
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
      const result = await restoreAdopting(ctx, () =>
        sync.store.saveWorkNote('w1', 'SET_WORK_RESEARCH_NOTE', 'Saved note. Queued elsewhere', { value: server.text, revision: server.revision }))
      expect(result).toMatchObject({ restored: false, review: [{ reason: 'foreign-queue' }] })
      expect(ctx.ui.workResearchNoteSession).toBeNull()
      expect(sync.rows().map((r) => r.payload.text)).toEqual(['Saved note. Queued elsewhere'])
      // The lineage stays this pane's orphan, with no writer held.
      expect(page!.rt.writers.writers()).toHaveLength(0)
      await expectKept('Saved note. Mine before reload')
    })

    it('does not apply a restored draft after another pane started editing while it was being adopted', async () => {
      const ctx = await typedThenReloaded('Saved note. Mine before reload')
      const side = await openWork('tab-2')
      const result = await restoreAdopting(ctx, () => type(side, 'Saved note. Side pane typing'))
      expect(result).toMatchObject({ restored: false, review: [{ reason: 'dirty-session' }] })
      expect(ctx.ui.workResearchNoteSession).toBeNull()
    })

    it('clears a represented draft whose row acknowledged while it was being adopted', async () => {
      const opId = await queuedThenReloaded()
      const fresh = await openWork()
      expect(await restoreAdopting(fresh, () => sync.ack(opId, 6))).toMatchObject({ restored: false, review: [] })
      await waitFor(async () => (await records()).length === 0, 'represented record cleared')
      await waitFor(() => page!.rt.writers.writers().length === 0, 'adopted writer released')
      // A later mount finds nothing left to report as live elsewhere.
      const again = await openWork('tab-1')
      expect(await win.prksRestoreResearchNotesRecovery(again, { id: 'w1' })).toBeNull()
    })

    it('a stale mount gives back a represented lineage even when its row acknowledged mid-adoption', async () => {
      const opId = await queuedThenReloaded()
      const fresh = await openWork()
      const result = await restoreAdopting(fresh, () => {
        sync.ack(opId, 6)
        leaveWork(fresh)
      })
      expect(result).toMatchObject({ restored: false })
      expect(fresh.ui.workResearchNoteSession).toBeNull()
      await waitFor(async () => (await records()).length === 0, 'represented record cleared')
      await waitFor(() => page!.rt.writers.writers().length === 0, 'adopted writer released')
    })

    it('clears a represented draft acknowledged after a stale mount released its adoption', async () => {
      const opId = await queuedThenReloaded()
      const fresh = await openWork()
      // Adoption completes, then the mount is replaced before the row acknowledges.
      expect(await restoreAdopting(fresh, () => leaveWork(fresh), 'after')).toMatchObject({ restored: false })
      await settle()
      expect(page!.rt.writers.writers()).toHaveLength(0)
      expect(await records()).toHaveLength(1)
      sync.ack(opId, 6)
      await waitFor(async () => (await records()).length === 0, 'represented record cleared')
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

describe('tab-close recovery (slice 3)', () => {
  it('restores the exact text of a closed tab in a new tab, then saves it normally', async () => {
    const ctx = await openWork()
    type(ctx, 'Saved note. Closed within 500 ms')
    // Closed before the idle write: only the emergency entry holds it.
    await closeTabAndOpenAnother()
    const { ctx: fresh, result, notes } = await openAndRestore()
    expect(result).toMatchObject({ restored: true, review: [] })
    expect(notes.editor.value()).toBe('Saved note. Closed within 500 ms')
    expect(fresh.ui.researchNotesRecovery).toBeNull()
    expect(sync.rows()).toEqual([])
    const saved = await win.prksEnqueueWorkResearchNotesSave(fresh, 'w1')
    expect(sync.rows().map((r) => r.payload.text)).toEqual(['Saved note. Closed within 500 ms'])
    sync.ack(saved.opId as string, 6)
    await waitFor(async () => (await records()).length === 0, 'restored record cleared on ack')
  })

  it('restores a closed tab\'s text without Web Locks from the page\'s recorded final pagehide', async () => {
    withoutLocks = true
    startPage()
    const ctx = await openWork()
    await type(ctx, 'Saved note. Closed on the LAN').recovery!.flush()
    await closeTabAndOpenAnother()
    const { result, notes } = await openAndRestore()
    expect(page!.rt.identity.current()!.verified).toBe('channel')
    expect(result).toMatchObject({ restored: true })
    expect(notes.editor.value()).toBe('Saved note. Closed on the LAN')
  })

  it('without Web Locks, a pane already open finds the text of a tab closed before its runtime claim settled', async () => {
    withoutLocks = true
    startPage()
    const { ctx } = await openAndRestore()
    expect(review().view(ctx)).toBeNull()
    // Another tab on the same Work types and closes during its 20 ms claim.
    const other = otherPage('closing-tab')
    void other.rt.start()
    other.rt.writers
      .openWriter({ kind: 'work-research-note', entityType: 'work', entityId: 'w1', paneId: 'tab-9', base: UNKNOWN_BASE })
      .edit(1, 'Saved note. Typed while claiming')
    expect(other.rt.writers.writeEmergencyNow()).toBe('written')
    expect(other.rt.identity.current()).toBeNull()
    other.window.dispatchEvent(new Event('pagehide'))
    other.rt.dispose()
    await settle()
    // The closed-pages notification re-plans the open pane for review; no remount.
    await win.prksRefreshResearchNotesRecovery(ctx, 'w1')
    expect(review().view(ctx)).toMatchObject({ drafts: 1 })
    const details = await review().details(ctx, 'w1')
    expect(details!.candidates).toMatchObject([{ lineage: 'dead-runtime', body: 'Saved note. Typed while claiming' }])
  })

  it('lists every draft of many closed LAN tabs within the restore budget', async () => {
    withoutLocks = true
    startPage()
    const count = 9
    for (let i = 0; i < count; i++) {
      const ctx = await openWork()
      await type(ctx, `Saved note. LAN tab ${i}`).recovery!.flush()
      await closeTabAndOpenAnother()
    }
    win.prksSetResearchRecoveryRestoreMsForTest(2000)
    try {
      const started = Date.now()
      const { ctx, result } = await openAndRestore()
      expect(result).toMatchObject({ restored: false })
      expect(result!.review).toHaveLength(count)
      expect(review().view(ctx)).toMatchObject({ drafts: count })
      // The owner checks run side by side, not one after another.
      expect(Date.now() - started).toBeLessThan(2000)
    } finally {
      win.prksSetResearchRecoveryRestoreMsForTest(null)
    }
  }, 20000)

  it('never adopts a crashed LAN tab\'s draft: silence is not proof, so it is offered for review only', async () => {
    withoutLocks = true
    startPage()
    const ctx = await openWork()
    await type(ctx, 'Saved note.').recovery!.flush()
    await type(ctx, 'Saved note. Crashed').recovery!.flush()
    await closeTabAndOpenAnother('crash')
    const [before] = await records()
    const { ctx: fresh, result, notes } = await openAndRestore()
    expect(result).toMatchObject({ restored: false, review: [{ reason: 'ownership-unknown', lineage: 'unknown', action: null }] })
    expect(notes.editor.value()).toBe('Saved note.')
    expect(review().view(fresh)).toMatchObject({ drafts: 1, incomplete: 0 })
    const details = await review().details(fresh, 'w1')
    expect(details!.candidates).toMatchObject([{ lineage: 'unknown', action: null, body: 'Saved note. Crashed' }])
    // No adoption and no cleanup, whatever is asked.
    expect(await review().restore(fresh, 'w1', details!.token, details!.candidates[0]!.expect)).toMatchObject({ ok: false, code: 'changed' })
    const [after] = await records()
    expect(after!.owner).toEqual(before!.owner)
    expect(sync.rows()).toEqual([])
  })

  it('an explicit Discard removes an unknown owner\'s draft, but not once its owner answers as live', async () => {
    withoutLocks = true
    startPage()
    const ctx = await openWork()
    await type(ctx, 'Saved note. Crashed one').recovery!.flush()
    await closeTabAndOpenAnother('crash')
    const first = await openWork()
    await type(first, 'Saved note. Crashed two').recovery!.flush()
    await closeTabAndOpenAnother('crash')
    const { ctx: fresh } = await openAndRestore()
    const details = await review().details(fresh, 'w1')
    expect(details!.candidates.map((c) => c.lineage)).toEqual(['unknown', 'unknown'])
    const [one, two] = details!.candidates
    // Ownership is checked again at the action: an owner that answers now is live.
    const classify = page!.rt.classify.bind(page!.rt)
    page!.rt.classify = async () => {
      page!.rt.classify = classify
      return 'other-live'
    }
    expect(await review().discard(fresh, 'w1', details!.token, one!.expect)).toMatchObject({ ok: false, code: 'changed' })
    expect(await bodyOf(one!.draftId)).toBe(one!.body)
    // Still unknown: the user's confirmed choice removes exactly that draft.
    expect(await review().discard(fresh, 'w1', details!.token, one!.expect)).toEqual({ ok: true })
    expect((await records()).map((r) => r.draftId)).toEqual([two!.draftId])
    // A stale expectation (the generation it showed) is refused.
    expect(await review().discard(fresh, 'w1', details!.token, { ...two!.expect, generation: two!.expect.generation + 1 }))
      .toMatchObject({ ok: false, code: 'changed' })
    expect(await bodyOf(two!.draftId)).toBe(two!.body)
    expect(sync.rows()).toEqual([])
  })

  it('keeps an unknown owner\'s draft even when it equals the saved note', async () => {
    withoutLocks = true
    startPage()
    const ctx = await openWork()
    await type(ctx, 'Saved note. X').recovery!.flush()
    await type(ctx, 'Saved note.').recovery!.flush()
    await closeTabAndOpenAnother('crash')
    await openAndRestore()
    await settle()
    expect(await records()).toHaveLength(1)
  })

  it('never adopts the draft of an editor that is live in another tab', async () => {
    const other = otherPage('live-tab')
    await other.rt.start()
    const writer = other.rt.writers.openWriter({ kind: 'work-research-note', entityType: 'work', entityId: 'w1', paneId: 'tab-1' })
    writer.edit(1, 'Saved note. Typing in another tab')
    await writer.flush()
    const { ctx, result, notes } = await openAndRestore()
    expect(result).toMatchObject({ restored: false, review: [{ reason: 'live-elsewhere' }] })
    // Not a recovery: no notice, no text offered.
    expect(review().view(ctx)).toBeNull()
    const details = await review().details(ctx, 'w1')
    expect(details!.candidates).toMatchObject([{ lineage: 'other-live', body: null, action: null }])
    expect(await review().discard(ctx, 'w1', details!.token, details!.candidates[0]!.expect)).toMatchObject({ ok: false, code: 'changed' })
    expect(notes.editor.value()).toBe('Saved note.')
    expect((await records())[0]!.owner.pageInstanceId).toBe(other.rt.identity.pageInstanceId)
    other.rt.dispose()
    other.locks.releaseAll()
  })
})

describe('Review (slice 3)', () => {
  async function twoClosedTabs() {
    const ctx = await openWork()
    await type(ctx, 'Saved note. First tab').recovery!.flush()
    await closeTabAndOpenAnother()
    const second = await openWork()
    await type(second, 'Saved note. Second tab').recovery!.flush()
    await closeTabAndOpenAnother()
    return openAndRestore()
  }

  it('shows every draft of two closed tabs and applies none', async () => {
    const { ctx, result, notes } = await twoClosedTabs()
    expect(result!.restored).toBe(false)
    expect(result!.review.map((r) => r.reason)).toEqual(['multiple-drafts', 'multiple-drafts'])
    expect(notes.editor.value()).toBe('Saved note.')
    expect(review().view(ctx)).toMatchObject({ drafts: 2, incomplete: 0, unprotected: null })
    const details = await review().details(ctx, 'w1')
    expect(details!.current).toMatchObject({ text: 'Saved note.', revision: 5, source: 'server', queue: 'none' })
    expect(details!.candidates.map((c) => [c.lineage, c.action, c.body]).sort()).toEqual([
      ['dead-runtime', 'restore', 'Saved note. First tab'],
      ['dead-runtime', 'restore', 'Saved note. Second tab'],
    ])
    expect(sync.rows()).toEqual([])
  })

  it('restores the chosen draft for editing; the other one stays, now only as a comparison', async () => {
    const { ctx, notes } = await twoClosedTabs()
    const details = await review().details(ctx, 'w1')
    const chosen = details!.candidates.find((c) => c.body === 'Saved note. Second tab')!
    expect(await review().restore(ctx, 'w1', details!.token, chosen.expect)).toEqual({ ok: true })
    expect(notes.editor.value()).toBe('Saved note. Second tab')
    // jsdom keeps innerText as a plain property.
    expect((ctx.root.querySelector('[data-prks-role="editor-status"]') as HTMLElement).innerText).toBe('Restored unsaved changes')
    expect(review().view(ctx)).toMatchObject({ drafts: 1 })
    const after = await review().details(ctx, 'w1')
    // The restored draft is this editor's own lineage now: what it shows, not a draft to review.
    expect(after!.candidates).toMatchObject([{ body: 'Saved note. First tab', action: 'reconcile', reason: 'editor-dirty' }])
    expect(after!.candidates.some((c) => c.lineage === 'self-live')).toBe(false)
    expect(sync.rows()).toEqual([])
  })

  describe('with the #490 checks', () => {
    /** Runs `hook` inside the next adoption or claim, before the store takes the record. */
    function during(name: 'adopt' | 'claimReviewed', hook: () => unknown) {
      const target = (name === 'adopt' ? page!.rt.writers : page!.rt) as unknown as Record<string, (...args: unknown[]) => Promise<unknown>>
      const original = target[name]!.bind(target)
      target[name] = async (...args: unknown[]) => {
        target[name] = original
        await hook()
        return original(...args)
      }
    }
    const foreign = () =>
      sync.store.saveWorkNote('w1', 'SET_WORK_RESEARCH_NOTE', 'Saved note. Queued elsewhere', { value: server.text, revision: server.revision })

    it('offers no restore when the note body and revision are not one server snapshot', async () => {
      const { ctx, notes } = await twoClosedTabs()
      // Same revision, other body: the observed base cannot be proven.
      server.text = 'Saved note, changed without a new revision.'
      const details = await review().details(ctx, 'w1')
      expect(details!.current.source).toBe('cache')
      expect(details!.candidates.map((c) => c.action)).toEqual(['reconcile', 'reconcile'])
      expect(await review().restore(ctx, 'w1', details!.token, details!.candidates[0]!.expect)).toMatchObject({ ok: false, code: 'changed' })
      expect(notes.editor.value()).toBe('Saved note.')
      expect(await records()).toHaveLength(2)
      expect(sync.rows()).toEqual([])
    })

    it('does not apply a restore when a row was queued while adopting', async () => {
      const { ctx, notes } = await twoClosedTabs()
      const details = await review().details(ctx, 'w1')
      const target = details!.candidates[0]!
      during('adopt', foreign)
      expect(await review().restore(ctx, 'w1', details!.token, target.expect)).toMatchObject({ ok: false, code: 'changed' })
      expect(notes.editor.value()).toBe('Saved note.')
      expect(ctx.ui.workResearchNoteSession).toBeNull()
      expect(sync.rows().map((r) => r.payload.text)).toEqual(['Saved note. Queued elsewhere'])
      expect(await bodyOf(target.draftId)).toBe(target.body)
    })

    it('does not replace the note when a row was queued while claiming', async () => {
      const { ctx, notes } = await twoClosedTabs()
      const details = await review().details(ctx, 'w1')
      const target = details!.candidates[0]!
      during('claimReviewed', foreign)
      expect(await review().replace(ctx, 'w1', details!.token, target.expect, 'Combined', { text: 'Saved note.', revision: 5 }))
        .toMatchObject({ ok: false, code: 'current-changed' })
      expect(notes.editor.value()).toBe('Saved note.')
      expect(sync.rows().map((r) => r.payload.text)).toEqual(['Saved note. Queued elsewhere'])
      // Claimed but not applied: still listed, with its text.
      expect(await bodyOf(target.draftId)).toBe(target.body)
    })

    it('does not replace a note with no observed base when a row was queued while claiming', async () => {
      const ctx = await openWork()
      await type(ctx, 'Saved note. First tab').recovery!.flush()
      await closeTabAndOpenAnother()
      const second = await openWork()
      await type(second, 'Saved note. Second tab').recovery!.flush()
      await closeTabAndOpenAnother()
      // Notes-state carries no revision: this pane never observes a base.
      server.revision = null as unknown as number
      const { ctx: fresh, notes } = await openAndRestore()
      expect(fresh.getResource('workNotesObserved')).toBeFalsy()
      const details = await review().details(fresh, 'w1')
      const target = details!.candidates[0]!
      expect(target.action).toBe('reconcile')
      during('claimReviewed', foreign)
      expect(await review().replace(fresh, 'w1', details!.token, target.expect, 'Combined', { text: 'Saved note.', revision: null }))
        .toMatchObject({ ok: false, code: 'current-changed' })
      expect(notes.editor.value()).toBe('Saved note.')
      expect(sync.rows().map((r) => r.payload.text)).toEqual(['Saved note. Queued elsewhere'])
      expect(await bodyOf(target.draftId)).toBe(target.body)
    })

    /** Another device saves: the server moves on and this page hears nothing. */
    const remote = () => {
      server.text = 'Saved elsewhere.'
      server.revision = 6
    }

    it('does not apply a restore when another device saved while adopting', async () => {
      const { ctx, notes } = await twoClosedTabs()
      const details = await review().details(ctx, 'w1')
      const target = details!.candidates[0]!
      during('adopt', remote)
      expect(await review().restore(ctx, 'w1', details!.token, target.expect)).toMatchObject({ ok: false, code: 'changed' })
      expect(notes.editor.value()).toBe('Saved note.')
      expect(ctx.ui.workResearchNoteSession).toBeNull()
      expect(sync.rows()).toEqual([])
      expect(await bodyOf(target.draftId)).toBe(target.body)
    })

    it('does not replace the note when another device saved while claiming', async () => {
      const { ctx, notes } = await twoClosedTabs()
      const details = await review().details(ctx, 'w1')
      const target = details!.candidates[0]!
      during('claimReviewed', remote)
      expect(await review().replace(ctx, 'w1', details!.token, target.expect, 'Combined', { text: 'Saved note.', revision: 5 }))
        .toMatchObject({ ok: false, code: 'current-changed' })
      expect(notes.editor.value()).toBe('Saved note.')
      expect(sync.rows()).toEqual([])
      expect(await bodyOf(target.draftId)).toBe(target.body)
    })

    it('keeps an automatic restore for review when another device saved while adopting', async () => {
      const ctx = await openWork()
      await type(ctx, 'Saved note. Closed tab').recovery!.flush()
      await closeTabAndOpenAnother()
      during('adopt', remote)
      const { ctx: fresh, result, notes } = await openAndRestore()
      expect(result).toMatchObject({ restored: false, review: [{ reason: 'base-advanced', action: 'reconcile' }] })
      expect(notes.editor.value()).not.toContain('Closed tab')
      expect(fresh.ui.workResearchNoteSession).toBeNull()
      expect(sync.rows()).toEqual([])
    })

    it('keeps the reviewed draft when the replacement text gets no recovery copy', async () => {
      const { ctx, notes } = await twoClosedTabs()
      const details = await review().details(ctx, 'w1')
      const target = details!.candidates[0]!
      // The claim commits; the replacement's recovery write is refused.
      const claim = page!.rt.claimReviewed.bind(page!.rt)
      page!.rt.claimReviewed = async (...args: Parameters<typeof claim>) => {
        const outcome = await claim(...args)
        // Exactly the replacement's write: a discard after it would succeed.
        idb.failCommits = 1
        return outcome
      }
      expect(await review().replace(ctx, 'w1', details!.token, target.expect, 'Combined', { text: 'Saved note.', revision: 5 })).toEqual({ ok: true })
      expect(notes.editor.value()).toBe('Combined')
      idb.failCommits = 0
      await settle()
      expect(await bodyOf(target.draftId)).toBe(target.body)
    })
  })

  it('never shows a draft\'s text when another tab adopts it while the text is being read', async () => {
    const { ctx } = await twoClosedTabs()
    const [first] = await records()
    const other = otherPage('adopter')
    await other.rt.start()
    let adopted = false
    const store = page!.rt.store
    const getBody = store.getBody.bind(store)
    store.getBody = async (draftId: string) => {
      const row = await getBody(draftId)
      if (!adopted && draftId === first!.draftId) {
        adopted = true
        expect(await other.rt.writers.adopt((await store.get(draftId))!, { paneId: 'tab-9' })).toBeTruthy()
      }
      return row
    }
    try {
      const details = await review().details(ctx, 'w1')
      expect(adopted).toBe(true)
      // Listed as the other tab's, without its text and with nothing to apply.
      expect(details!.candidates.find((c) => c.draftId === first!.draftId)).toMatchObject({ lineage: 'other-live', body: null, action: null })
    } finally {
      store.getBody = getBody
      other.rt.dispose()
      other.locks.releaseAll()
    }
  })

  it('refuses an action decided before the owner changed, and changes nothing', async () => {
    const { ctx, notes } = await twoClosedTabs()
    const details = await review().details(ctx, 'w1')
    const target = details!.candidates[0]!
    // Another tab adopts it while this Review is open.
    const other = otherPage('adopter')
    await other.rt.start()
    const record = (await page!.rt.store.get(target.draftId))!
    expect(await other.rt.writers.adopt(record, { paneId: 'tab-9' })).toBeTruthy()
    for (const act of [
      () => review().restore(ctx, 'w1', details!.token, target.expect),
      () => review().replace(ctx, 'w1', details!.token, target.expect, 'x', { text: 'Saved note.', revision: 5 }),
      () => review().discard(ctx, 'w1', details!.token, target.expect),
    ]) {
      expect(await act()).toMatchObject({ ok: false, code: 'changed' })
    }
    expect(notes.editor.value()).toBe('Saved note.')
    expect((await page!.rt.store.get(target.draftId))!.owner.pageInstanceId).toBe(other.rt.identity.pageInstanceId)
    expect(await bodyOf(target.draftId)).toBe(target.body)
    other.rt.dispose()
    other.locks.releaseAll()
  })

  it('refuses a stale dialog after a Work switch or a new editor session', async () => {
    const { ctx, notes } = await twoClosedTabs()
    const details = await review().details(ctx, 'w1')
    const target = details!.candidates[0]!
    // The pane now shows another Work.
    ctx.setEntity('work', { id: 'w2', text_content: '', private_notes: '' })
    expect(await review().restore(ctx, 'w1', details!.token, target.expect)).toMatchObject({ ok: false, code: 'stale' })
    // Back on the Work, but in a new editor session.
    ctx.setEntity('work', { id: 'w1', text_content: server.text, private_notes: '' })
    attachEditor(ctx)
    expect(await review().discard(ctx, 'w1', details!.token, target.expect)).toMatchObject({ ok: false, code: 'stale' })
    expect(await records()).toHaveLength(2)
    expect(notes.editor.value()).toBe('Saved note.')
  })

  it('reconciles a draft typed on an older note only by an explicit choice against the shown note', async () => {
    const ctx = await openWork()
    await type(ctx, 'Saved note. Mine').recovery!.flush()
    await closeTabAndOpenAnother()
    server.text = 'Another device wrote this.'
    server.revision = 6
    const { ctx: fresh, result, notes } = await openAndRestore()
    expect(result).toMatchObject({ restored: false, review: [{ reason: 'base-advanced', action: 'reconcile' }] })
    expect(notes.editor.value()).toBe('Another device wrote this.')
    const details = await review().details(fresh, 'w1')
    const draft = details!.candidates[0]!
    expect(draft).toMatchObject({ reason: 'base-advanced', action: 'reconcile', body: 'Saved note. Mine' })
    expect(await review().restore(fresh, 'w1', details!.token, draft.expect)).toMatchObject({ ok: false, code: 'changed' })
    // The note moved on again while comparing: nothing is written.
    expect(await review().replace(fresh, 'w1', details!.token, draft.expect, 'Combined', { text: 'Older text', revision: 6 }))
      .toMatchObject({ ok: false, code: 'current-changed' })
    expect(sync.rows()).toEqual([])
    expect(await records()).toHaveLength(1)
    const chosen = 'Another device wrote this. Saved note. Mine'
    expect(await review().replace(fresh, 'w1', details!.token, draft.expect, chosen, { text: details!.current.text, revision: 6 })).toEqual({ ok: true })
    expect(notes.editor.value()).toBe(chosen)
    await waitFor(async () => !(await records()).some((r) => r.draftId === draft.draftId), 'reviewed record removed')
    // The chosen text saves through the ordinary path as this pane's edit.
    const saved = await win.prksEnqueueWorkResearchNotesSave(fresh, 'w1')
    expect(sync.rows()).toMatchObject([{ payload: { text: chosen }, base_revision: 6 }])
    sync.ack(saved.opId as string, 7)
    await waitFor(async () => (await records()).length === 0, 'chosen text cleared on ack')
    expect(review().view(fresh)).toBeNull()
  })

  it('never replaces a foreign queued row on its own', async () => {
    const ctx = await openWork()
    await type(ctx, 'Saved note. Closed tab').recovery!.flush()
    await closeTabAndOpenAnother()
    await sync.store.saveWorkNote('w1', 'SET_WORK_RESEARCH_NOTE', 'Saved note. Foreign', { value: server.text, revision: server.revision })
    const { ctx: fresh, result } = await openAndRestore()
    expect(result).toMatchObject({ restored: false, review: [{ reason: 'foreign-queue', action: 'reconcile' }] })
    expect(sync.rows().map((r) => r.payload.text)).toEqual(['Saved note. Foreign'])
    const details = await review().details(fresh, 'w1')
    expect(details!.current.queue).toBe('queued')
  })

  it('shows an incomplete draft as incomplete, offers copy and discard only, and never enqueues it', async () => {
    const ctx = await openWork()
    await type(ctx, 'Saved note. Older part').recovery!.flush()
    await closeTabAndOpenAnother()
    const [stored] = await records()
    await page!.rt.store.applyEmergencyEntry(
      { v: 1, pageInstanceId: stored!.owner.pageInstanceId, runtimeId: stored!.owner.runtimeId, at: Date.now(), entries: [] },
      { draftId: stored!.draftId, kind: 'work-research-note', entityType: 'work', entityId: 'w1', generation: stored!.generation + 1, committedGeneration: stored!.generation, body: null },
    )
    const { ctx: fresh, result, notes } = await openAndRestore()
    expect(result).toMatchObject({ restored: false, review: [{ reason: 'tail-missing', action: null }] })
    expect(review().view(fresh)).toMatchObject({ drafts: 1, incomplete: 1 })
    const details = await review().details(fresh, 'w1')
    const draft = details!.candidates[0]!
    expect(draft).toMatchObject({ status: 'tail-missing', action: null, body: 'Saved note. Older part' })
    expect(await review().restore(fresh, 'w1', details!.token, draft.expect)).toMatchObject({ ok: false })
    expect(notes.editor.value()).toBe('Saved note.')
    expect(sync.rows()).toEqual([])
    expect(await review().discard(fresh, 'w1', details!.token, draft.expect)).toEqual({ ok: true })
    expect(await records()).toEqual([])
    expect(review().view(fresh)).toBeNull()
  })

  it('discards only the chosen draft; closing Review or hiding the notice keeps every record', async () => {
    const { ctx } = await twoClosedTabs()
    const details = await review().details(ctx, 'w1')
    // Reading details changes nothing.
    expect(await records()).toHaveLength(2)
    const target = details!.candidates[0]!
    expect(await review().discard(ctx, 'w1', details!.token, target.expect)).toEqual({ ok: true })
    const left = await records()
    expect(left.map((r) => r.draftId)).toEqual(details!.candidates.slice(1).map((c) => c.draftId))
    expect(review().view(ctx)).toMatchObject({ drafts: 1 })
  })
})

describe('protection warning (slice 3)', () => {
  it('warns while recovery storage refuses the newest text, and stops once the server holds it', async () => {
    const { ctx } = await openAndRestore()
    idb.failCommits = 1000
    const entry = type(ctx, 'Saved note. Unprotected')
    await entry.recovery!.flush()
    expect(entry.recovery!.state()).toBe('unprotected')
    expect(review().view(ctx)).toMatchObject({ drafts: 0, unprotected: 'quota' })
    expect(page!.rt.writers.leaveGuardActive()).toBe(true)
    const saved = await win.prksEnqueueWorkResearchNotesSave(ctx, 'w1')
    sync.ack(saved.opId as string, 6)
    await waitFor(() => review().view(ctx) === null, 'warning cleared on ack')
    expect(page!.rt.writers.leaveGuardActive()).toBe(false)
  })

  it('repaints the warning away when the text returns to the saved note with nothing to queue', async () => {
    const { ctx } = await openAndRestore()
    const w = win as unknown as Record<string, unknown>
    const painted: unknown[] = []
    w.prksVueUpdateResearchNotesRecovery = (owner: Ctx) => painted.push(review().view(owner))
    try {
      idb.failCommits = 1000
      const entry = type(ctx, 'Saved note. Unprotected')
      await entry.recovery!.flush()
      expect(painted.at(-1)).toMatchObject({ unprotected: 'quota' })
      // A -> B -> A: the save is a no-op, so no queued row acknowledges it.
      type(ctx, 'Saved note.')
      const saved = await win.prksEnqueueWorkResearchNotesSave(ctx, 'w1')
      expect(saved.opId).toBeFalsy()
      await waitFor(() => painted.at(-1) === null, 'warning repainted away')
      expect(page!.rt.writers.leaveGuardActive()).toBe(false)
    } finally {
      delete w.prksVueUpdateResearchNotesRecovery
    }
  })
})
