/**
 * #534 PR C: Folder Reminders on the shared note recovery adapter
 * (`work-note-recovery.js`, kind `folder-private-note`) and a real
 * editor-recovery runtime on the fake IndexedDB. The base is the pane's
 * observed `private_notes` Folder field (value, field revision, source); a
 * "reload" disposes the page's runtime and sessions and starts a new page on
 * the same IndexedDB, sessionStorage and localStorage.
 */
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest'
import folderStateSource from '../../../../frontend/js/folder-state.js?raw'
import ownerResourceSource from '../../../../frontend/js/owner-resource.js?raw'
import tabContextSource from '../../../../frontend/js/tab-context.js?raw'
import uiSource from '../../../../frontend/js/ui.js?raw'
import workNotesStateSource from '../../../../frontend/js/work-notes-state.js?raw'
import workNoteRecoverySource from '../../../../frontend/js/work-note-recovery.js?raw'
import type { EditorRecoveryRuntime } from '../../lifecycle/editor-recovery/runtime'
import { RUNTIME_SESSION_KEY, type DraftRecord } from '../../lifecycle/editor-recovery/schema'
import { createFakeBrowser } from '../../lifecycle/editor-recovery/test-support/fake-env'
import { createFakeIdb, settle } from '../../lifecycle/editor-recovery/test-support/fake-idb'
import { memoryStorage, startRecoveryPage, type RecoveryPage } from '../work/test-support/work-note-harness'
import { createFolderQueue, type FolderRow, type FolderServer } from './test-support/folder-note-queue'

type Session = {
  key: string
  folderId: string
  draftText: string
  state: string
  dirty: boolean
  editGeneration: number
  recovery: { draftId(): string | null; flush(): Promise<void>; state(): string } | null
  recoveryQueued?: { opId: string } | null
  ownQueued?: { opId: string; text: string } | null
}

type Editor = { dirty: boolean; textarea: HTMLTextAreaElement; statusEl: HTMLElement | null }

type Folder = { id: string; title: string; private_notes: string }

type Ctx = {
  tabId: string
  generation: number
  destroyed: boolean
  ui: {
    folderPrivateNoteSession: Session | null
    rightPanelTab: string
  }
  setEntity: (type: string, value: unknown) => void
  getEntity: (type: string) => Folder | null
  getResource: (name: string) => unknown
}

type Candidate = {
  draftId: string
  lineage: string
  reason: string
  action: string | null
  body: string | null
  expect: { draftId: string; pageInstanceId: string; generation: number; status: string }
}

type Details = {
  token: string
  current: { text: string; revision: number | null; source: string; queue: string }
  candidates: Candidate[]
}

type Result = { ok: boolean; code?: string }

type W = Record<string, unknown> & {
  eval: (code: string) => void
  prksWorkspaceSnapshot: () => { focusedTabId: string; mainTabId: string }
  prksMountTabContext: (tabId: string, host: HTMLElement) => void
  prksGetTabContext: (tabId: string) => Ctx
  prksDestroyAllTabContexts: () => void
  initPrksPrivateNotesEditor: (entityType: string, entityId: string, owner: Ctx) => void
  prksFlushPendingPrivateNotes: (owner: Ctx) => void
  prksResetPrivateNoteDraftsForTest: () => void
  prksRememberFolderNotesCanonical: (owner: Ctx, folder: Folder, source: string) => void
  prksEnsureFolderNotesBase: (owner: Ctx, folder: Folder) => Promise<unknown>
  prksRefreshPendingFolderNotes: () => Promise<unknown>
  prksBindFolderPrivateNotesSync: (owner: Ctx) => void
  prksFolderNoteObserved: (owner: Ctx, folderId?: string) => { value: string; revision: number } | null
  prksRestoreFolderPrivateNoteRecovery: (ctx: Ctx, folder: Folder) => Promise<{ restored: boolean; review: Array<{ reason: string; lineage?: string; action?: string | null }> } | null>
  prksFolderPrivateNotesRecoveryView: (ctx: Ctx) => { drafts: number; incomplete: number; unprotected: string | null } | null
  prksFolderPrivateNotesRecoveryDetails: (ctx: Ctx, id: string) => Promise<Details | null>
  prksFolderPrivateNotesRecoveryRestore: (ctx: Ctx, id: string, token: string, expect: unknown) => Promise<Result>
  prksFolderPrivateNotesRecoveryReplace: (ctx: Ctx, id: string, token: string, expect: unknown, text: string, shown: { text: string; revision: number | null }) => Promise<Result>
  prksFolderPrivateNotesRecoveryDiscard: (ctx: Ctx, id: string, token: string, expect: unknown) => Promise<Result>
  prksWorkPrivateNotesRecoveryView: (ctx: Ctx) => unknown
}
const win = window as unknown as W
const KIND = 'folder-private-note'
const FA = 'F-A'
const SAVED = 'Saved reminder.'

const server: FolderServer = {}
const sync = createFolderQueue(server)

const browser = createFakeBrowser()
let idb = createFakeIdb()
let session = browser.sessionStorageWith({ [RUNTIME_SESSION_KEY]: 'r-' + 'a'.repeat(32) })
let local = memoryStorage()
let page: RecoveryPage | null = null
let pageSeq = 0
let withoutLocks = false
let focused = 'tab-1'

function startPage() {
  page = startRecoveryPage({ browser, name: 'page-' + ++pageSeq, idb, session, local, withoutLocks })
  return page.rt
}

function folder(id = FA): Folder {
  return { id, title: 'Folder ' + id, private_notes: server[id]?.private_notes ?? '' }
}

/** The right panel shows Folder `id`'s Reminders card for this pane. */
function bindField(ctx: Ctx, id = FA): HTMLTextAreaElement {
  const panel = document.getElementById('panel-content')!
  panel.dataset.prksOwnerTabId = ctx.tabId
  panel.dataset.prksOwnerGeneration = String(ctx.generation)
  const ta = document.createElement('textarea')
  ta.id = 'prks-private-notes-folder-' + id
  ta.value = folder(id).private_notes
  const status = document.createElement('p')
  status.id = 'prks-private-notes-status-folder-' + id
  panel.replaceChildren(ta, status)
  win.initPrksPrivateNotesEditor('folder', id, ctx)
  return ta
}

/** Opens Folder `id` in a pane as the Folder route does: entity, card, base, then restore. */
async function openFolder(tabId = 'tab-1', id = FA): Promise<{ ctx: Ctx; ta: HTMLTextAreaElement | null }> {
  let host = document.getElementById('host-' + tabId)
  if (!host) {
    host = document.createElement('div')
    host.id = 'host-' + tabId
    document.body.appendChild(host)
  }
  win.prksMountTabContext(tabId, host)
  const ctx = win.prksGetTabContext(tabId)
  ctx.setEntity('folder', folder(id))
  ctx.ui.rightPanelTab = 'details'
  await page!.rt.start()
  await win.prksRefreshPendingFolderNotes()
  win.prksRememberFolderNotesCanonical(ctx, folder(id), 'server')
  await win.prksEnsureFolderNotesBase(ctx, folder(id))
  const ta = focused === tabId ? bindField(ctx, id) : null
  win.prksBindFolderPrivateNotesSync(ctx)
  return { ctx, ta }
}

async function openAndRestore(tabId = 'tab-1') {
  const opened = await openFolder(tabId)
  const result = await win.prksRestoreFolderPrivateNoteRecovery(opened.ctx, folder())
  return { ...opened, result }
}

function type(ctx: Ctx, ta: HTMLTextAreaElement, text: string): Session {
  ta.value = text
  ta.dispatchEvent(new Event('input', { bubbles: true }))
  return ctx.ui.folderPrivateNoteSession as Session
}

function editorOf(ctx: Ctx): Editor | null {
  return ctx.getResource('privateNotesEditor') as Editor | null
}

async function waitFor(check: () => boolean | Promise<boolean>, what: string) {
  for (let i = 0; i < 400; i++) {
    if (await check()) return
    await new Promise((resolve) => setTimeout(resolve, 5))
  }
  throw new Error('timed out: ' + what)
}

async function records(): Promise<DraftRecord[]> {
  return (await page!.rt.store.listAll()).filter((r) => r.status !== 'discarded')
}

const noteRows = () => sync.rows().filter((r) => r.payload.field === 'private_notes')

/** Waits until `text` is queued and the recovery record names its row. */
async function queuedAs(text: string): Promise<string> {
  await waitFor(() => noteRows().some((r) => r.payload.value === text), 'queued: ' + text)
  const opId = noteRows().find((r) => r.payload.value === text)!.op_id
  await waitFor(async () => (await records()).some((r) => r.pipeline?.queuedOpId === opId), 'recorded: ' + opId)
  return opId
}

async function bodyOf(draftId: string): Promise<string | null> {
  const row = await page!.rt.store.getBody(draftId)
  return row ? row.body : null
}

function resetPage() {
  win.prksResetPrivateNoteDraftsForTest()
  win.prksDestroyAllTabContexts()
  document.body.innerHTML = '<div id="right-panel"><div class="tabs"></div><div id="panel-content"></div></div>'
}

/** Unload within the save debounce: what the page committed or held stays; its timers and locks go. */
async function reload(keepQueue = false): Promise<EditorRecoveryRuntime> {
  page!.rt.writers.writeEmergencyNow()
  page!.window.dispatchEvent(new Event('pagehide'))
  page!.rt.dispose()
  page!.locks.releaseAll()
  const kept: FolderRow[] = keepQueue ? sync.rows().map((r) => ({ ...r, payload: { ...r.payload } })) : []
  resetPage()
  sync.reset()
  kept.forEach((r) => sync.rows().push(r))
  await settle()
  return startPage()
}

/** The tab closes (or crashes) and the Folder is opened again in a new tab. */
async function closeTabAndOpenAnother(how: 'close' | 'crash' = 'close'): Promise<EditorRecoveryRuntime> {
  if (how === 'close') {
    page!.rt.writers.writeEmergencyNow()
    page!.window.dispatchEvent(new Event('pagehide'))
  }
  page!.rt.dispose()
  page!.locks.releaseAll()
  resetPage()
  await settle()
  session = browser.sessionStorageWith()
  return startPage()
}

function during(name: 'adopt' | 'claimReviewed', hook: () => unknown) {
  const target = (name === 'adopt' ? page!.rt.writers : page!.rt) as unknown as Record<string, (...args: unknown[]) => Promise<unknown>>
  const original = target[name]!.bind(target)
  target[name] = async (...args: unknown[]) => {
    target[name] = original
    await hook()
    return original(...args)
  }
}

function stateOf(id: string) {
  return {
    folder_id: id,
    fields: {
      title: { revision: 0 }, description: { revision: 0 },
      private_notes: { revision: server[id]?.revision ?? 0 }, parent_id: { revision: 0 },
    },
  }
}

beforeAll(() => {
  win.prksSync = sync
  win.prksWorkspaceSnapshot = () => ({ focusedTabId: focused, mainTabId: 'tab-1' })
  let clock = 0
  win.prksOfflineReadEntity = async (kind: string, id: string) => ({
    value: kind === 'folder-state' ? stateOf(id) : folder(id),
    source: 'server',
    cachedAt: ++clock,
  })
  win.prksOfflineInvalidateEntity = async () => undefined
  win.eval(ownerResourceSource)
  win.eval(tabContextSource)
  win.eval(workNotesStateSource)
  win.eval(folderStateSource)
  win.eval(workNoteRecoverySource)
  win.eval(uiSource)
})

beforeEach(async () => {
  withoutLocks = false
  focused = 'tab-1'
  idb = createFakeIdb()
  session = browser.sessionStorageWith({ [RUNTIME_SESSION_KEY]: 'r-' + 'a'.repeat(32) })
  local = memoryStorage()
  sync.reset()
  server[FA] = { private_notes: SAVED, revision: 5 }
  server['F-B'] = { private_notes: '', revision: 2 }
  resetPage()
  startPage()
  await win.prksRefreshPendingFolderNotes()
})

afterEach(async () => {
  page!.rt.dispose()
  page!.locks.releaseAll()
  resetPage()
  await settle()
})

describe('Folder Reminders edits reach the recovery writer', () => {
  it('records every edit before the save debounce, typed on the observed Folder field base', async () => {
    const { ctx, ta } = await openFolder()
    const s = type(ctx, ta!, 'Saved reminder. One')
    expect(s.recovery).toBeTruthy()
    await s.recovery!.flush()
    const [record] = await records()
    expect(record).toMatchObject({
      kind: KIND,
      entityKey: 'folder-private-note:' + FA,
      generation: s.editGeneration,
      base: { revision: 5, length: SAVED.length, source: 'server' },
      owner: { paneId: 'tab-1' },
    })
    expect(await bodyOf(record!.draftId)).toBe('Saved reminder. One')
    expect(sync.rows()).toEqual([])
  })

  it('never lets the acknowledgement of A clear newer B, and clears on B\'s exact acknowledgement', async () => {
    const { ctx, ta } = await openFolder()
    type(ctx, ta!, 'Saved reminder. A')
    win.prksFlushPendingPrivateNotes(ctx)
    const a = await queuedAs('Saved reminder. A')
    const s = type(ctx, ta!, 'Saved reminder. A then B')
    await s.recovery!.flush()
    sync.attempt(a)
    sync.ack(a, 6)
    await settle()
    const [kept] = await records()
    expect(await bodyOf(kept!.draftId)).toBe('Saved reminder. A then B')
    win.prksFlushPendingPrivateNotes(ctx)
    sync.ack(await queuedAs('Saved reminder. A then B'), 7)
    await waitFor(async () => (await records()).length === 0, 'record cleared on exact ack')
  })

  it('clears on the acknowledgement of text the server stores trimmed', async () => {
    const { ctx, ta } = await openFolder()
    type(ctx, ta!, 'Saved reminder. Spaced  ')
    win.prksFlushPendingPrivateNotes(ctx)
    sync.ack(await queuedAs('Saved reminder. Spaced  '), 6)
    expect(server[FA]!.private_notes).toBe('Saved reminder. Spaced')
    await waitFor(async () => (await records()).length === 0, 'record cleared')
  })

  it('ignores a late duplicate acknowledgement of an earlier row', async () => {
    const { ctx, ta } = await openFolder()
    type(ctx, ta!, 'Saved reminder. First')
    win.prksFlushPendingPrivateNotes(ctx)
    const first = await queuedAs('Saved reminder. First')
    const row = { ...noteRows()[0]!, payload: { ...noteRows()[0]!.payload } }
    sync.ack(first, 6)
    await waitFor(async () => (await records()).length === 0, 'first cleared')
    const s = type(ctx, ta!, 'Saved reminder. Second')
    await s.recovery!.flush()
    sync.replayAck(row, 6)
    await settle()
    const [kept] = await records()
    expect(await bodyOf(kept!.draftId)).toBe('Saved reminder. Second')
  })

  it('keeps the newest text recoverable through scope_busy and saves it after the sent row settles', async () => {
    const { ctx, ta } = await openFolder()
    type(ctx, ta!, 'Saved reminder. Sent')
    win.prksFlushPendingPrivateNotes(ctx)
    const sent = await queuedAs('Saved reminder. Sent')
    sync.attempt(sent)
    const s = type(ctx, ta!, 'Saved reminder. Sent and newer')
    win.prksFlushPendingPrivateNotes(ctx)
    await waitFor(() => s.dirty && editorOf(ctx)!.statusEl!.textContent!.includes('Still syncing'), 'scope_busy kept the draft')
    await s.recovery!.flush()
    const [record] = await records()
    expect(await bodyOf(record!.draftId)).toBe('Saved reminder. Sent and newer')
    sync.ack(sent, 6)
    sync.ack(await queuedAs('Saved reminder. Sent and newer'), 7)
    await waitFor(async () => (await records()).length === 0, 'cleared once the newer text is acknowledged')
  })

  it('keeps the record when the save fails, and when the row conflicts', async () => {
    const { ctx, ta } = await openFolder()
    const s = type(ctx, ta!, 'Saved reminder. Refused')
    sync.failNext(1)
    win.prksFlushPendingPrivateNotes(ctx)
    await waitFor(() => s.state === 'error', 'save failed')
    await waitFor(async () => (await records())[0]?.pipeline?.state === 'error', 'error pipeline')
    expect(await bodyOf((await records())[0]!.draftId)).toBe('Saved reminder. Refused')
    // The next edit saves again; the server then refuses that row.
    type(ctx, ta!, 'Saved reminder. Refused again')
    win.prksFlushPendingPrivateNotes(ctx)
    const opId = await queuedAs('Saved reminder. Refused again')
    sync.conflict(opId)
    await waitFor(async () => (await records())[0]?.pipeline?.state === 'conflict', 'conflict pipeline')
    expect(await bodyOf((await records())[0]!.draftId)).toBe('Saved reminder. Refused again')
  })

  it('keeps one lineage across a Folder switch and back (A, B, A)', async () => {
    const { ctx, ta } = await openFolder()
    const first = type(ctx, ta!, 'Saved reminder. Before switch')
    await first.recovery!.flush()
    const draftId = first.recovery!.draftId()
    ctx.setEntity('folder', folder('F-B'))
    bindField(ctx, 'F-B')
    ctx.setEntity('folder', folder())
    const ta2 = bindField(ctx)
    expect(ta2.value).toBe('Saved reminder. Before switch')
    const back = type(ctx, ta2, 'Saved reminder. Before switch, after')
    expect(back.recovery!.draftId()).toBe(draftId)
    await back.recovery!.flush()
    expect((await records()).map((r) => r.draftId)).toEqual([draftId])
  })
})

describe('Folder Reminders restore', () => {
  it('restores text typed within the debounce after a reload, paints the field and saves it', async () => {
    const { ctx, ta } = await openFolder()
    await type(ctx, ta!, 'Saved reminder. Typed then reloaded').recovery!.flush()
    await reload()
    const { ctx: fresh, ta: field, result } = await openAndRestore()
    expect(result).toMatchObject({ restored: true })
    expect(field!.value).toBe('Saved reminder. Typed then reloaded')
    expect(editorOf(fresh)!.statusEl!.textContent).toBe('Restored unsaved changes')
    const opId = await queuedAs('Saved reminder. Typed then reloaded')
    // The restored save is measured against the Folder field revision it was typed on.
    expect(noteRows()[0]!.base_revision).toBe(5)
    sync.ack(opId, 6)
    await waitFor(async () => (await records()).length === 0, 'cleared on acknowledgement')
  })

  it('saves restored text on the base it was typed on, even after another tab\'s save lands first', async () => {
    const { ctx, ta } = await openFolder()
    await type(ctx, ta!, 'Saved reminder. Typed on 5').recovery!.flush()
    await reload()
    const { ctx: fresh, ta: field, result } = await openAndRestore()
    expect(result).toMatchObject({ restored: true })
    expect(field!.value).toBe('Saved reminder. Typed on 5')
    /* Before the restored text's save, another tab's Reminders reach the server. */
    const foreign = sync.foreign(FA, 'private_notes', 'From another tab', 5)
    sync.ack(foreign.op_id, 6)
    expect(win.prksFolderNoteObserved(fresh, FA)).toMatchObject({ value: 'From another tab', revision: 6 })
    expect(field!.value).toBe('Saved reminder. Typed on 5')
    await queuedAs('Saved reminder. Typed on 5')
    /* Revision 5, so the server answers with a conflict, never an overwrite. */
    expect(noteRows().map((r) => [r.payload.value, r.base_revision])).toEqual([['Saved reminder. Typed on 5', 5]])
  })

  it.each([
    ['with Web Locks', false],
    ['without Web Locks, from the recorded final pagehide', true],
  ])('restores a closed tab\'s text in a new tab %s', async (_how, lan) => {
    withoutLocks = lan
    startPage()
    const { ctx, ta } = await openFolder()
    await type(ctx, ta!, 'Saved reminder. Closed tab').recovery!.flush()
    await closeTabAndOpenAnother()
    const { ta: field, result } = await openAndRestore()
    expect(result).toMatchObject({ restored: true })
    expect(field!.value).toBe('Saved reminder. Closed tab')
  })

  it('offers a crashed LAN tab\'s draft for review only, and lets Discard remove it', async () => {
    withoutLocks = true
    startPage()
    const { ctx, ta } = await openFolder()
    await type(ctx, ta!, 'Saved reminder. Crashed').recovery!.flush()
    await closeTabAndOpenAnother('crash')
    const { ctx: fresh, ta: field, result } = await openAndRestore()
    expect(result).toMatchObject({ restored: false, review: [{ reason: 'ownership-unknown', lineage: 'unknown', action: null }] })
    expect(field!.value).toBe(SAVED)
    expect(win.prksFolderPrivateNotesRecoveryView(fresh)).toMatchObject({ drafts: 1, incomplete: 0 })
    // The Work Reminders notice of this pane never shows a Folder draft.
    expect(win.prksWorkPrivateNotesRecoveryView(fresh)).toBeNull()
    const details = (await win.prksFolderPrivateNotesRecoveryDetails(fresh, FA))!
    expect(details.candidates).toMatchObject([{ lineage: 'unknown', action: null, body: 'Saved reminder. Crashed' }])
    expect(await win.prksFolderPrivateNotesRecoveryRestore(fresh, FA, details.token, details.candidates[0]!.expect))
      .toMatchObject({ ok: false, code: 'changed' })
    expect(await win.prksFolderPrivateNotesRecoveryDiscard(fresh, FA, details.token, details.candidates[0]!.expect)).toEqual({ ok: true })
    expect(await records()).toEqual([])
    await waitFor(() => win.prksFolderPrivateNotesRecoveryView(fresh) === null, 'notice gone')
    expect(sync.rows()).toEqual([])
    // The tombstone keeps a discarded draft from coming back on the next visit.
    const again = await win.prksRestoreFolderPrivateNoteRecovery(fresh, folder())
    expect(again).toBeNull()
  })

  it('never restores over Reminders the server changed since, and Replace applies the chosen text', async () => {
    const { ctx, ta } = await openFolder()
    await type(ctx, ta!, 'Saved reminder. Mine').recovery!.flush()
    await reload()
    server[FA] = { private_notes: 'Changed on another device.', revision: 6 }
    const { ctx: fresh, ta: field, result } = await openAndRestore()
    expect(result).toMatchObject({ restored: false, review: [{ reason: 'base-advanced', action: 'reconcile' }] })
    expect(field!.value).toBe('Changed on another device.')
    expect(sync.rows()).toEqual([])
    const details = (await win.prksFolderPrivateNotesRecoveryDetails(fresh, FA))!
    const target = details.candidates[0]!
    expect(target).toMatchObject({ action: 'reconcile', body: 'Saved reminder. Mine' })
    expect(details.current).toMatchObject({ text: 'Changed on another device.', revision: 6, source: 'server' })
    const chosen = 'Changed on another device. Mine'
    expect(await win.prksFolderPrivateNotesRecoveryReplace(fresh, FA, details.token, target.expect, chosen, { text: details.current.text, revision: 6 }))
      .toEqual({ ok: true })
    expect(field!.value).toBe(chosen)
    await waitFor(async () => !(await records()).some((r) => r.draftId === target.draftId), 'reviewed draft removed')
    await waitFor(() => noteRows().some((r) => r.payload.value === chosen && r.base_revision === 6), 'chosen text saved on revision 6')
  })

  it('keeps the draft for review when the server changes while adopting', async () => {
    const { ctx, ta } = await openFolder()
    await type(ctx, ta!, 'Saved reminder. Mine').recovery!.flush()
    await reload()
    during('adopt', () => {
      server[FA] = { private_notes: 'Saved elsewhere.', revision: 6 }
    })
    const { ta: field, result } = await openAndRestore()
    expect(result).toMatchObject({ restored: false, review: [{ reason: 'base-advanced', action: 'reconcile' }] })
    expect(field!.value).toBe(SAVED)
    expect(sync.rows()).toEqual([])
    expect(await records()).toHaveLength(1)
  })

  it('reviews instead of restoring while a foreign row is queued, and does not replace when one appears while claiming', async () => {
    const { ctx, ta } = await openFolder()
    await type(ctx, ta!, 'Saved reminder. Mine').recovery!.flush()
    await reload()
    const foreign = sync.foreign(FA, 'private_notes', 'Queued elsewhere.', 5)
    const { ctx: fresh, ta: field, result } = await openAndRestore()
    expect(result).toMatchObject({ restored: false, review: [{ reason: 'foreign-queue', action: 'reconcile' }] })
    expect(field!.value).not.toBe('Saved reminder. Mine')
    expect(noteRows().map((r) => r.op_id)).toEqual([foreign.op_id])
    const details = (await win.prksFolderPrivateNotesRecoveryDetails(fresh, FA))!
    const target = details.candidates[0]!
    during('claimReviewed', () => sync.foreign(FA, 'private_notes', 'Queued again elsewhere.', 5))
    expect(await win.prksFolderPrivateNotesRecoveryReplace(fresh, FA, details.token, target.expect, 'Combined', { text: details.current.text, revision: details.current.revision }))
      .toMatchObject({ ok: false, code: 'current-changed' })
    expect(await bodyOf(target.draftId)).toBe(target.body)
  })

  it('ignores queued rows of other Folder fields and other Folders', async () => {
    const { ctx, ta } = await openFolder()
    await type(ctx, ta!, 'Saved reminder. Mine').recovery!.flush()
    await reload()
    sync.foreign(FA, 'title', 'Renamed', 0)
    sync.foreign('F-B', 'private_notes', 'Another Folder.', 2)
    const { ta: field, result } = await openAndRestore()
    expect(result).toMatchObject({ restored: true })
    expect(field!.value).toBe('Saved reminder. Mine')
  })

  it('restores behind its own queued predecessor and saves after it settles', async () => {
    const { ctx, ta } = await openFolder()
    type(ctx, ta!, 'Saved reminder. Queued')
    win.prksFlushPendingPrivateNotes(ctx)
    const first = await queuedAs('Saved reminder. Queued')
    sync.attempt(first)
    await type(ctx, ta!, 'Saved reminder. Queued and newer').recovery!.flush()
    await reload(true)
    const { ctx: fresh, ta: field, result } = await openAndRestore()
    expect(result).toMatchObject({ restored: true })
    expect(field!.value).toBe('Saved reminder. Queued and newer')
    expect(fresh.ui.folderPrivateNoteSession!.ownQueued).toMatchObject({ opId: first, text: 'Saved reminder. Queued' })
    sync.ack(first, 6)
    await waitFor(() => noteRows().some((r) => r.payload.value === 'Saved reminder. Queued and newer'), 'newer text saved after its predecessor')
  })

  it('restored text replaces its own never-sent predecessor, and only that row', async () => {
    const { ctx, ta } = await openFolder()
    type(ctx, ta!, 'Saved reminder. Queued')
    win.prksFlushPendingPrivateNotes(ctx)
    const first = await queuedAs('Saved reminder. Queued')
    await type(ctx, ta!, 'Saved reminder. Queued and newer').recovery!.flush()
    await reload(true)
    const { ctx: fresh, result } = await openAndRestore()
    expect(result).toMatchObject({ restored: true })
    expect(fresh.ui.folderPrivateNoteSession!.ownQueued).toMatchObject({ opId: first })
    win.prksFlushPendingPrivateNotes(fresh)
    await waitFor(() => noteRows().some((r) => r.payload.value === 'Saved reminder. Queued and newer'), 'restored text coalesced over its own row')
    expect(noteRows().map((r) => r.payload.value)).toEqual(['Saved reminder. Queued and newer'])
  })

  it('does not restore into a field that already has unsaved text', async () => {
    const { ctx, ta } = await openFolder()
    await type(ctx, ta!, 'Saved reminder. Old draft').recovery!.flush()
    await reload()
    const { ctx: fresh, ta: field } = await openFolder()
    type(fresh, field!, 'Saved reminder. Typing now')
    const result = await win.prksRestoreFolderPrivateNoteRecovery(fresh, folder())
    expect(result!.restored).toBe(false)
    expect(field!.value).toBe('Saved reminder. Typing now')
  })

  it('reports another pane\'s unsaved Reminders of the same Folder instead of restoring', async () => {
    const { ctx, ta } = await openFolder()
    await type(ctx, ta!, 'Saved reminder. Old draft').recovery!.flush()
    await reload()
    focused = 'tab-2'
    const { ctx: other, ta: otherField } = await openFolder('tab-2')
    type(other, otherField!, 'Saved reminder. Other pane typing')
    focused = 'tab-1'
    const { ta: field, result } = await openAndRestore('tab-1')
    expect(result!.restored).toBe(false)
    expect(result!.review.map((c) => c.reason)).toContain('dirty-session')
    expect(field!.value).toBe(SAVED)
  })

  it.each([
    ['another open tab', false],
    ['a duplicate of that tab, which copied its session id', true],
  ])('never adopts Reminders a live tab is still writing, opened from %s', async (_how, duplicated) => {
    // The first tab is still open and typing in Folder A's Reminders.
    page!.rt.dispose()
    page!.locks.releaseAll()
    const copied = 'r-' + 'b'.repeat(32)
    const first = startRecoveryPage({ browser, name: 'first-tab', idb, session: browser.sessionStorageWith({ [RUNTIME_SESSION_KEY]: copied }), local, withoutLocks: false, background: true })
    await first.rt.start()
    const api = win.prksEditorRecovery as { fingerprintText(text: string): string }
    const live = first.rt.writers.openWriter({
      kind: KIND, entityType: 'folder', entityId: FA, paneId: 'tab-1',
      base: { revision: 5, length: SAVED.length, fingerprint: api.fingerprintText(SAVED), source: 'server' },
    })
    live.edit(1, 'Saved reminder. Still typing in the first tab')
    await live.flush()
    session = duplicated ? browser.sessionStorageWith({ [RUNTIME_SESSION_KEY]: copied }) : browser.sessionStorageWith()
    startPage()
    const { ctx, ta, result } = await openAndRestore()
    expect(result!.restored).toBe(false)
    expect(result!.review).toMatchObject([{ reason: 'live-elsewhere', lineage: 'other-live', action: null }])
    expect(ta!.value).toBe(SAVED)
    expect(sync.rows()).toEqual([])
    // Review cannot take it either while that tab still owns it.
    const details = (await win.prksFolderPrivateNotesRecoveryDetails(ctx, FA))!
    expect(await win.prksFolderPrivateNotesRecoveryRestore(ctx, FA, details.token, details.candidates[0]!.expect))
      .toMatchObject({ ok: false })
    expect(ta!.value).toBe(SAVED)
    expect(await bodyOf(live.draftId()!)).toBe('Saved reminder. Still typing in the first tab')
    first.rt.dispose()
    first.locks.releaseAll()
  })

  it('verifies a base only when no write lands while its body is read', async () => {
    const verify = win.prksVerifyFolderNoteBase as (id: string, base: unknown) => Promise<boolean>
    const base = { value: SAVED, revision: 6, source: 'server' }
    server[FA] = { private_notes: SAVED, revision: 6 }
    expect(await verify(FA, base)).toBe(true)
    // The body is read at revision 5; a write of other text lands as revision 6 right after.
    server[FA] = { private_notes: SAVED, revision: 5 }
    const read = win.prksOfflineReadEntity as (kind: string, id: string, ...rest: unknown[]) => Promise<unknown>
    win.prksOfflineReadEntity = async (kind: string, id: string, ...rest: unknown[]) => {
      const out = await read(kind, id, ...rest)
      if (kind === 'folder') server[FA] = { private_notes: 'Written on another device', revision: 6 }
      return out
    }
    try {
      expect(await verify(FA, base)).toBe(false)
    } finally {
      win.prksOfflineReadEntity = read
    }
  })

  it('never offers a Work Reminders draft with the same id as Folder Reminders, nor the reverse', async () => {
    await page!.rt.start()
    const writer = page!.rt.writers.openWriter({ kind: 'work-private-note', entityType: 'work', entityId: FA, paneId: 'tab-9' })
    writer.edit(1, 'A Work reminder')
    await writer.flush()
    await writer.release()
    const { ctx, result } = await openAndRestore()
    expect(result).toBeNull()
    expect(win.prksFolderPrivateNotesRecoveryView(ctx)).toBeNull()
  })
})

describe('Folder Reminders protection warning', () => {
  it('warns and keeps the leave guard while recovery storage refuses the text, until the server holds it', async () => {
    const { ctx, ta } = await openFolder()
    idb.failCommits = 1000
    const s = type(ctx, ta!, 'Saved reminder. Unprotected')
    await s.recovery!.flush()
    expect(s.recovery!.state()).toBe('unprotected')
    expect(win.prksFolderPrivateNotesRecoveryView(ctx)).toMatchObject({ drafts: 0, unprotected: 'quota' })
    expect(page!.rt.writers.leaveGuardActive()).toBe(true)
    win.prksFlushPendingPrivateNotes(ctx)
    await waitFor(() => noteRows().length === 1 && s.recoveryQueued?.opId === noteRows()[0]!.op_id, 'queued')
    // Queued is not saved: the guard stays until the acknowledgement.
    expect(page!.rt.writers.leaveGuardActive()).toBe(true)
    sync.ack(noteRows()[0]!.op_id, 6)
    await waitFor(() => win.prksFolderPrivateNotesRecoveryView(ctx) === null, 'warning cleared on ack')
    expect(page!.rt.writers.leaveGuardActive()).toBe(false)
  })
})
