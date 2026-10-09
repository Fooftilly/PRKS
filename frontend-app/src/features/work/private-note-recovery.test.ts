/**
 * #474: Work Reminders sessions in `ui.js` on the shared Work note recovery
 * adapter (`work-note-recovery.js`) and a real editor-recovery runtime on the
 * fake IndexedDB. A "reload" disposes the page's runtime and sessions and
 * starts a new page on the same IndexedDB, sessionStorage and localStorage.
 */
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest'
import ownerResourceSource from '../../../../frontend/js/owner-resource.js?raw'
import tabContextSource from '../../../../frontend/js/tab-context.js?raw'
import uiSource from '../../../../frontend/js/ui.js?raw'
import workNotesStateSource from '../../../../frontend/js/work-notes-state.js?raw'
import workLifecycleSource from '../../../../frontend/js/work-lifecycle-state.js?raw'
import workNoteRecoverySource from '../../../../frontend/js/work-note-recovery.js?raw'
import type { EditorRecoveryRuntime } from '../../lifecycle/editor-recovery/runtime'
import { EMERGENCY_KEY_PREFIX, RUNTIME_SESSION_KEY, type DraftBase, type DraftRecord } from '../../lifecycle/editor-recovery/schema'
import { createFakeBrowser } from '../../lifecycle/editor-recovery/test-support/fake-env'
import { createFakeIdb, settle } from '../../lifecycle/editor-recovery/test-support/fake-idb'
import { createNoteQueue, memoryStorage, startRecoveryPage, type RecoveryPage } from './test-support/work-note-harness'

type Session = {
  key: string
  workId: string
  draftText: string
  state: string
  dirty: boolean
  editGeneration: number
  recovery: { draftId(): string | null; flush(): Promise<void>; state(): string } | null
  recoveryHeir?: Session
  recoveryQueued?: { opId: string } | null
}

type Editor = { dirty: boolean; textarea: HTMLTextAreaElement; statusEl: HTMLElement | null }

type Ctx = {
  tabId: string
  generation: number
  mounted: boolean
  destroyed: boolean
  ui: {
    workPrivateNoteSession: Session | null
    privateNotesRecovery?: { candidates: Array<{ reason: string; lineage: string }> } | null
    rightPanelTab: string
    workDetailsMode: string
  }
  setEntity: (type: string, value: unknown) => void
  getEntity: (type: string) => { id: string; private_notes: string } | null
  getResource: (name: string) => unknown
  timers: Map<string, unknown>
}

type Details = {
  token: string
  current: { text: string; revision: number | null; source: string; queue: string }
  candidates: Array<{ draftId: string; lineage: string; reason: string; action: string | null; body: string | null; expect: { draftId: string; pageInstanceId: string; generation: number; status: string } }>
}

type W = Record<string, unknown> & {
  eval: (code: string) => void
  prksWorkspaceSnapshot: () => { focusedTabId: string; mainTabId: string }
  prksMountTabContext: (tabId: string, host: HTMLElement) => void
  prksGetTabContext: (tabId: string) => Ctx
  prksDestroyAllTabContexts: () => void
  prksRefreshFocusedRightPanel: () => void
  initPrksPrivateNotesEditor: (entityType: string, entityId: string, owner: Ctx) => void
  prksFlushPendingPrivateNotes: (owner: Ctx) => void
  prksResetPrivateNoteDraftsForTest: () => void
  prksEnsureWorkPrivateNoteSession: (ctx: Ctx, workId: string, text: string) => Session | null
  prksEnsureWorkNotesBase: (ctx: Ctx, work: object) => Promise<unknown>
  prksRememberWorkNotesCanonical: (ctx: Ctx, work: object, source?: string) => unknown
  prksBindWorkNotesSync: (ctx: Ctx) => void
  prksRefreshPendingWorkNotes: () => Promise<unknown>
  prksRestoreWorkPrivateNoteRecovery: (ctx: Ctx, work: object) => Promise<{ restored: boolean; review: Array<{ reason: string; lineage?: string; action?: string | null }> } | null>
  prksWorkPrivateNotesRecoveryView: (ctx: Ctx) => { drafts: number; incomplete: number; unprotected: string | null } | null
  prksWorkPrivateNotesRecoveryDetails: (ctx: Ctx, workId: string) => Promise<Details | null>
  prksWorkPrivateNotesRecoveryRestore: (ctx: Ctx, workId: string, token: string, expect: unknown) => Promise<{ ok: boolean; code?: string }>
  prksWorkPrivateNotesRecoveryReplace: (ctx: Ctx, workId: string, token: string, expect: unknown, text: string, shown: { text: string; revision: number | null }) => Promise<{ ok: boolean; code?: string }>
  prksWorkPrivateNotesRecoveryDiscard: (ctx: Ctx, workId: string, token: string, expect: unknown) => Promise<{ ok: boolean; code?: string }>
  prksDeleteWorkDurably: (workId: string) => Promise<{ op_id: string } | null>
  prksRetryDeletedWorkRecovery: () => Promise<string[]>
  prksCleanupDeletedWorkRecovery: (workId: string) => Promise<{ removed: string[]; unknown: string[] } | null>
  prksRequest?: (url: string) => Promise<{ status: number; json(): Promise<unknown> }>
  indexedDB?: { databases(): Promise<Array<{ name: string }>> }
}
const win = window as unknown as W
const KIND = 'work-private-note'
const OP = 'SET_WORK_PRIVATE_NOTE'

const server = { text: 'Saved reminder.', revision: 5 }
const sync = createNoteQueue(server)

/* ---- one page: runtime, identity, TabContext and the right panel ---- */
const browser = createFakeBrowser()
let idb = createFakeIdb()
let session = browser.sessionStorageWith({ [RUNTIME_SESSION_KEY]: 'r-' + 'a'.repeat(32) })
let local = memoryStorage()
let page: RecoveryPage | null = null
let pageSeq = 0
/** The LAN/HTTP deployment: no Web Locks, so a closed tab is recognized from its closed-page record. */
let withoutLocks = false
let focused = 'tab-1'

function startPage() {
  page = startRecoveryPage({ browser, name: 'page-' + ++pageSeq, idb, session, local, withoutLocks })
  return page.rt
}

function work() {
  return { id: 'w1', title: 'Alpha', text_content: '', private_notes: server.text, status: 'Not Started', doc_type: 'article' }
}

/** The right panel shows Work w1's Reminders field for the focused pane. */
function bindField(ctx: Ctx): HTMLTextAreaElement {
  const panel = document.getElementById('panel-content')!
  panel.replaceChildren()
  delete panel.dataset.prksOwnerTabId
  delete panel.dataset.prksOwnerGeneration
  const ta = document.createElement('textarea')
  ta.id = 'prks-private-notes-work-w1'
  const status = document.createElement('p')
  status.id = 'prks-private-notes-status-work-w1'
  panel.append(ta, status)
  win.initPrksPrivateNotesEditor('work', 'w1', ctx)
  return ta
}

/** Opens Work w1 in a pane as the Work route does: the field is painted, then notes-state and restore. */
async function openWork(tabId = 'tab-1'): Promise<{ ctx: Ctx; ta: HTMLTextAreaElement | null }> {
  let host = document.getElementById('host-' + tabId)
  if (!host) {
    host = document.createElement('div')
    host.id = 'host-' + tabId
    document.body.appendChild(host)
  }
  win.prksMountTabContext(tabId, host)
  const ctx = win.prksGetTabContext(tabId)
  ctx.setEntity('work', work())
  ctx.ui.rightPanelTab = 'details'
  ctx.ui.workDetailsMode = 'metadata'
  await page!.rt.start()
  await win.prksRefreshPendingWorkNotes()
  const ta = focused === tabId ? bindField(ctx) : null
  win.prksRememberWorkNotesCanonical(ctx, work(), 'server')
  await win.prksEnsureWorkNotesBase(ctx, work())
  win.prksBindWorkNotesSync(ctx)
  return { ctx, ta }
}

async function openAndRestore(tabId = 'tab-1') {
  const opened = await openWork(tabId)
  const result = await win.prksRestoreWorkPrivateNoteRecovery(opened.ctx, work())
  return { ...opened, result }
}

function type(ctx: Ctx, ta: HTMLTextAreaElement, text: string): Session {
  ta.value = text
  ta.dispatchEvent(new Event('input', { bubbles: true }))
  return ctx.ui.workPrivateNoteSession as Session
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

/** Waits until `text` is queued and the recovery record names its row, as a sync round trip would. */
async function queuedAs(text: string): Promise<string> {
  await waitFor(() => sync.rows().some((r) => r.payload.text === text), 'queued: ' + text)
  const opId = sync.rows().find((r) => r.payload.text === text)!.op_id
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
async function reload(): Promise<EditorRecoveryRuntime> {
  page!.rt.writers.writeEmergencyNow()
  page!.window.dispatchEvent(new Event('pagehide'))
  page!.rt.dispose()
  page!.locks.releaseAll()
  resetPage()
  sync.reset()
  await settle()
  return startPage()
}

/** The tab closes (or crashes) and the Work is opened again in a new tab. */
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

beforeAll(() => {
  win.prksSync = sync
  win.prksWorkspaceSnapshot = () => ({ focusedTabId: focused, mainTabId: 'tab-1' })
  win.prksOfflineReadEntity = async (type: string, workId: string) => ({
    value: type === 'work'
      ? { ...work(), id: workId }
      : { work_id: workId, research_note_revision: 0, private_note_revision: server.revision },
    source: 'server',
    cachedAt: null,
  })
  win.prksOfflineInvalidateEntity = async () => undefined
  win.eval(ownerResourceSource)
  win.eval(tabContextSource)
  win.eval(workNotesStateSource)
  win.eval(workNoteRecoverySource)
  win.eval(uiSource)
  win.eval(workLifecycleSource)
})

beforeEach(() => {
  withoutLocks = false
  focused = 'tab-1'
  idb = createFakeIdb()
  session = browser.sessionStorageWith({ [RUNTIME_SESSION_KEY]: 'r-' + 'a'.repeat(32) })
  local = memoryStorage()
  sync.reset()
  server.text = 'Saved reminder.'
  server.revision = 5
  resetPage()
  startPage()
})

afterEach(async () => {
  page!.rt.dispose()
  page!.locks.releaseAll()
  resetPage()
  await settle()
})

describe('Work Reminders edits reach the recovery writer', () => {
  it('records every edit before the save debounce, typed on the observed server base', async () => {
    const { ctx, ta } = await openWork()
    const s = type(ctx, ta!, 'Saved reminder. One')
    expect(s.recovery).toBeTruthy()
    await s.recovery!.flush()
    const [record] = await records()
    expect(record).toMatchObject({
      kind: KIND,
      entityKey: 'work-private-note:w1',
      generation: s.editGeneration,
      base: { revision: 5, length: 'Saved reminder.'.length, source: 'server' },
      owner: { paneId: 'tab-1' },
    })
    expect(await bodyOf(record!.draftId)).toBe('Saved reminder. One')
    // Nothing queued yet: the 850 ms debounce has not fired.
    expect(sync.rows()).toEqual([])
    expect(editorOf(ctx)!.statusEl!.textContent).not.toContain('Saved')
  })

  it('records the queued row, never lets the acknowledgement of A clear newer B, and clears on B\'s', async () => {
    const { ctx, ta } = await openWork()
    type(ctx, ta!, 'Saved reminder. A')
    win.prksFlushPendingPrivateNotes(ctx)
    await waitFor(() => sync.rows().length === 1, 'A queued')
    await waitFor(async () => (await records())[0]?.pipeline?.queuedOpId === 'op-1', 'queued pipeline')
    const s = type(ctx, ta!, 'Saved reminder. A then B')
    await s.recovery!.flush()
    sync.attempt('op-1')
    sync.ack('op-1', 6)
    await settle()
    const [kept] = await records()
    expect(await bodyOf(kept!.draftId)).toBe('Saved reminder. A then B')
    win.prksFlushPendingPrivateNotes(ctx)
    sync.ack(await queuedAs('Saved reminder. A then B'), 7)
    await waitFor(async () => (await records()).length === 0, 'record cleared on exact ack')
  })

  it('keeps the newest text recoverable through scope_busy and saves it after the sent row settles', async () => {
    const { ctx, ta } = await openWork()
    type(ctx, ta!, 'Saved reminder. Sent')
    win.prksFlushPendingPrivateNotes(ctx)
    await waitFor(() => sync.rows().length === 1, 'first row queued')
    sync.attempt('op-1')
    const s = type(ctx, ta!, 'Saved reminder. Sent and newer')
    win.prksFlushPendingPrivateNotes(ctx)
    await waitFor(() => s.state === 'drafting' && s.dirty && editorOf(ctx)!.statusEl!.textContent!.includes('Still syncing'), 'scope_busy kept the draft')
    await s.recovery!.flush()
    const [record] = await records()
    expect(await bodyOf(record!.draftId)).toBe('Saved reminder. Sent and newer')
    sync.ack('op-1', 6)
    sync.ack(await queuedAs('Saved reminder. Sent and newer'), 7)
    await waitFor(async () => (await records()).length === 0, 'cleared once the newer text is acknowledged')
  })

  it('keeps the record when the save fails', async () => {
    const { ctx, ta } = await openWork()
    const s = type(ctx, ta!, 'Saved reminder. Refused')
    sync.failNext(1)
    win.prksFlushPendingPrivateNotes(ctx)
    await waitFor(() => s.state === 'error', 'save failed')
    await waitFor(async () => (await records())[0]?.pipeline?.state === 'error', 'error pipeline')
    expect(await bodyOf((await records())[0]!.draftId)).toBe('Saved reminder. Refused')
  })

  it('keeps one lineage across a Work switch and back, and clears it when the carried text saves', async () => {
    const { ctx, ta } = await openWork()
    const first = type(ctx, ta!, 'Saved reminder. Before switch')
    await first.recovery!.flush()
    const draftId = first.recovery!.draftId()
    // The pane shows another Work, then comes back to w1.
    win.prksEnsureWorkPrivateNoteSession(ctx, 'w2', '')
    expect(ctx.ui.workPrivateNoteSession!.workId).toBe('w2')
    const back = win.prksEnsureWorkPrivateNoteSession(ctx, 'w1', server.text)!
    expect(back.draftText).toBe('Saved reminder. Before switch')
    expect(back.recovery!.draftId()).toBe(draftId)
    expect(first.recovery).toBeNull()
    const ta2 = bindField(ctx)
    expect(ta2.value).toBe('Saved reminder. Before switch')
    const s = type(ctx, ta2, 'Saved reminder. Before switch, after')
    await s.recovery!.flush()
    expect((await records()).map((r) => r.draftId)).toEqual([draftId])
    win.prksFlushPendingPrivateNotes(ctx)
    sync.ack(await queuedAs('Saved reminder. Before switch, after'), 6)
    await waitFor(async () => (await records()).length === 0, 'cleared')
  })

  /** A edit -> its save starts -> B -> A, before that save settles. */
  async function switchAwayAndBackDuringSave(text: string) {
    const { ctx, ta } = await openWork()
    type(ctx, ta!, text)
    win.prksFlushPendingPrivateNotes(ctx)
    win.prksEnsureWorkPrivateNoteSession(ctx, 'w2', '')
    const back = win.prksEnsureWorkPrivateNoteSession(ctx, 'w1', server.text)!
    expect(back.draftText).toBe(text)
    const field = bindField(ctx)
    expect(field.value).toBe(text)
    return { ctx, back, field }
  }

  it('settles a carried session that saw no new edit when the save it carried lands, with no duplicate save on leave', async () => {
    const { ctx, back, field } = await switchAwayAndBackDuringSave('Saved reminder. In flight')
    await waitFor(() => !back.dirty && back.state === 'committed', 'carried session settled by the save it carried')
    expect(editorOf(ctx)!.dirty).toBe(false)
    expect(field.value).toBe('Saved reminder. In flight')
    const opId = await queuedAs('Saved reminder. In flight')
    // Leaving the Work enqueues nothing more.
    win.prksFlushPendingPrivateNotes(ctx)
    win.prksEnsureWorkPrivateNoteSession(ctx, 'w2', '')
    await settle()
    expect(sync.rows().map((r) => r.op_id)).toEqual([opId])
    sync.ack(opId, 6)
    await waitFor(async () => (await records()).length === 0, 'cleared on acknowledgement')
  })

  it('keeps a carried session dirty and protected when it was edited after returning, and saves the newer text', async () => {
    const { ctx, back, field } = await switchAwayAndBackDuringSave('Saved reminder. In flight')
    const s = type(ctx, field, 'Saved reminder. In flight, then more')
    expect(s).toBe(back)
    await s.recovery!.flush()
    const opId = await queuedAs('Saved reminder. In flight')
    await settle()
    expect(back.dirty).toBe(true)
    expect(back.state).toBe('drafting')
    expect(editorOf(ctx)!.dirty).toBe(true)
    const [kept] = await records()
    expect(await bodyOf(kept!.draftId)).toBe('Saved reminder. In flight, then more')
    sync.ack(opId, 6)
    await settle()
    expect(await records()).toHaveLength(1)
    win.prksFlushPendingPrivateNotes(ctx)
    sync.ack(await queuedAs('Saved reminder. In flight, then more'), 7)
    await waitFor(async () => (await records()).length === 0, 'cleared once the newer text is acknowledged')
  })

  it('keeps the lineage when the right panel moves to another pane and back', async () => {
    const { ctx, ta } = await openWork()
    const s = type(ctx, ta!, 'Saved reminder. Unfocused')
    await s.recovery!.flush()
    const draftId = s.recovery!.draftId()
    focused = 'tab-2'
    const { ctx: other } = await openWork('tab-2')
    bindField(other)
    expect(ta!.isConnected).toBe(false)
    focused = 'tab-1'
    const ta2 = bindField(ctx)
    expect(ta2.value).toBe('Saved reminder. Unfocused')
    const again = type(ctx, ta2, 'Saved reminder. Unfocused again')
    expect(again.recovery!.draftId()).toBe(draftId)
  })
})

describe('Work Reminders restore', () => {
  it('restores text typed within the debounce after a reload, paints the field and saves it', async () => {
    const { ctx, ta } = await openWork()
    await type(ctx, ta!, 'Saved reminder. Typed then reloaded').recovery!.flush()
    await reload()
    const { ctx: fresh, ta: field, result } = await openAndRestore()
    expect(result).toMatchObject({ restored: true })
    expect(field!.value).toBe('Saved reminder. Typed then reloaded')
    expect(editorOf(fresh)!.statusEl!.textContent).toBe('Restored unsaved changes')
    await waitFor(() => sync.rows().length === 1, 'restored text saved through the ordinary path')
    expect(sync.rows()[0]!.payload.text).toBe('Saved reminder. Typed then reloaded')
    sync.ack(await queuedAs('Saved reminder. Typed then reloaded'), 6)
    await waitFor(async () => (await records()).length === 0, 'cleared on acknowledgement')
  })

  it.each([
    ['with Web Locks', false],
    ['without Web Locks, from the recorded final pagehide', true],
  ])('restores a closed tab\'s text in a new tab %s', async (_how, lan) => {
    withoutLocks = lan
    startPage()
    const { ctx, ta } = await openWork()
    await type(ctx, ta!, 'Saved reminder. Closed tab').recovery!.flush()
    await closeTabAndOpenAnother()
    const { ta: field, result } = await openAndRestore()
    expect(result).toMatchObject({ restored: true })
    expect(field!.value).toBe('Saved reminder. Closed tab')
  })

  it('offers a crashed LAN tab\'s draft for review only, and lets Discard remove it', async () => {
    withoutLocks = true
    startPage()
    const { ctx, ta } = await openWork()
    await type(ctx, ta!, 'Saved reminder. Crashed').recovery!.flush()
    await closeTabAndOpenAnother('crash')
    const { ctx: fresh, ta: field, result } = await openAndRestore()
    expect(result).toMatchObject({ restored: false, review: [{ reason: 'ownership-unknown', lineage: 'unknown', action: null }] })
    expect(field!.value).toBe('Saved reminder.')
    expect(win.prksWorkPrivateNotesRecoveryView(fresh)).toMatchObject({ drafts: 1, incomplete: 0 })
    const details = (await win.prksWorkPrivateNotesRecoveryDetails(fresh, 'w1'))!
    expect(details.candidates).toMatchObject([{ lineage: 'unknown', action: null, body: 'Saved reminder. Crashed' }])
    expect(await win.prksWorkPrivateNotesRecoveryRestore(fresh, 'w1', details.token, details.candidates[0]!.expect))
      .toMatchObject({ ok: false, code: 'changed' })
    expect(await win.prksWorkPrivateNotesRecoveryDiscard(fresh, 'w1', details.token, details.candidates[0]!.expect)).toEqual({ ok: true })
    expect(await records()).toEqual([])
    await waitFor(() => win.prksWorkPrivateNotesRecoveryView(fresh) === null, 'notice gone')
    expect(sync.rows()).toEqual([])
  })

  it('never restores over a note the server changed since, and Replace applies the chosen text', async () => {
    const { ctx, ta } = await openWork()
    await type(ctx, ta!, 'Saved reminder. Mine').recovery!.flush()
    await reload()
    server.text = 'Changed on another device.'
    server.revision = 6
    const { ctx: fresh, ta: field, result } = await openAndRestore()
    expect(result).toMatchObject({ restored: false, review: [{ reason: 'base-advanced', action: 'reconcile' }] })
    expect(field!.value).toBe('Changed on another device.')
    expect(sync.rows()).toEqual([])
    const details = (await win.prksWorkPrivateNotesRecoveryDetails(fresh, 'w1'))!
    const target = details.candidates[0]!
    expect(target).toMatchObject({ action: 'reconcile', body: 'Saved reminder. Mine' })
    expect(details.current).toMatchObject({ text: 'Changed on another device.', revision: 6, source: 'server' })
    const chosen = 'Changed on another device. Mine'
    expect(await win.prksWorkPrivateNotesRecoveryReplace(fresh, 'w1', details.token, target.expect, chosen, { text: details.current.text, revision: 6 }))
      .toEqual({ ok: true })
    expect(field!.value).toBe(chosen)
    await waitFor(async () => !(await records()).some((r) => r.draftId === target.draftId), 'reviewed draft removed')
    await waitFor(() => sync.rows().some((r) => r.payload.text === chosen), 'chosen text saved')
  })

  it('keeps the draft for review when the server changes while adopting', async () => {
    const { ctx, ta } = await openWork()
    await type(ctx, ta!, 'Saved reminder. Mine').recovery!.flush()
    await reload()
    during('adopt', () => {
      server.text = 'Saved elsewhere.'
      server.revision = 6
    })
    const { ta: field, result } = await openAndRestore()
    expect(result).toMatchObject({ restored: false, review: [{ reason: 'base-advanced', action: 'reconcile' }] })
    expect(field!.value).toBe('Saved reminder.')
    expect(sync.rows()).toEqual([])
    expect(await records()).toHaveLength(1)
  })

  it('lists the earlier draft for review when the user types while the restore is adopting it', async () => {
    const { ctx, ta } = await openWork()
    await type(ctx, ta!, 'Saved reminder. Earlier').recovery!.flush()
    await reload()
    const { ctx: fresh, ta: field } = await openWork()
    during('adopt', () => {
      type(fresh, field!, 'Saved reminder. Typed now')
    })
    const result = await win.prksRestoreWorkPrivateNoteRecovery(fresh, work())
    expect(result).toMatchObject({ restored: false })
    expect(result!.review).toHaveLength(1)
    expect(field!.value).toBe('Saved reminder. Typed now')
    expect(win.prksWorkPrivateNotesRecoveryView(fresh)).toMatchObject({ drafts: 1 })
    const bodies = await Promise.all((await records()).map((r) => bodyOf(r.draftId)))
    expect(bodies).toContain('Saved reminder. Earlier')
  })

  it('does not replace the note when a row was queued while claiming', async () => {
    const { ctx, ta } = await openWork()
    await type(ctx, ta!, 'Saved reminder. Mine').recovery!.flush()
    await reload()
    server.text = 'Changed on another device.'
    server.revision = 6
    const { ctx: fresh, ta: field } = await openAndRestore()
    const details = (await win.prksWorkPrivateNotesRecoveryDetails(fresh, 'w1'))!
    const target = details.candidates[0]!
    during('claimReviewed', () => sync.foreign(OP, 'w1', 'Queued elsewhere.'))
    expect(await win.prksWorkPrivateNotesRecoveryReplace(fresh, 'w1', details.token, target.expect, 'Combined', { text: details.current.text, revision: 6 }))
      .toMatchObject({ ok: false, code: 'current-changed' })
    expect(field!.value).toBe('Changed on another device.')
    expect(await bodyOf(target.draftId)).toBe(target.body)
  })

  it('reviews instead of restoring while a foreign row is queued', async () => {
    const { ctx, ta } = await openWork()
    await type(ctx, ta!, 'Saved reminder. Mine').recovery!.flush()
    await reload()
    sync.foreign(OP, 'w1', 'Queued elsewhere.')
    const { ta: field, result } = await openAndRestore()
    expect(result).toMatchObject({ restored: false, review: [{ reason: 'foreign-queue', action: 'reconcile' }] })
    expect(field!.value).not.toBe('Saved reminder. Mine')
  })

  it('restores behind its own queued predecessor and saves after it settles', async () => {
    const { ctx, ta } = await openWork()
    type(ctx, ta!, 'Saved reminder. Queued')
    win.prksFlushPendingPrivateNotes(ctx)
    await waitFor(() => sync.rows().length === 1, 'predecessor queued')
    await waitFor(async () => (await records())[0]?.pipeline?.queuedOpId === 'op-1', 'queued pipeline')
    sync.attempt('op-1')
    await type(ctx, ta!, 'Saved reminder. Queued and newer').recovery!.flush()
    // Reload keeps the queue: the predecessor is still sending.
    const kept = sync.rows().map((r) => ({ ...r }))
    await reload()
    kept.forEach((r) => sync.rows().push(r))
    const { ta: field, result } = await openAndRestore()
    expect(result).toMatchObject({ restored: true })
    expect(field!.value).toBe('Saved reminder. Queued and newer')
    sync.ack('op-1', 6)
    await waitFor(() => sync.rows().some((r) => r.payload.text === 'Saved reminder. Queued and newer'), 'newer text saved after its predecessor')
  })

  it('does not restore into a field that already has unsaved text', async () => {
    const { ctx, ta } = await openWork()
    await type(ctx, ta!, 'Saved reminder. Old draft').recovery!.flush()
    await reload()
    const { ctx: fresh, ta: field } = await openWork()
    type(fresh, field!, 'Saved reminder. Typing now')
    const result = await win.prksRestoreWorkPrivateNoteRecovery(fresh, work())
    expect(result!.restored).toBe(false)
    expect(field!.value).toBe('Saved reminder. Typing now')
  })

  it('never offers a Research Notes draft as Reminders', async () => {
    await page!.rt.start()
    const writer = page!.rt.writers.openWriter({ kind: 'work-research-note', entityType: 'work', entityId: 'w1', paneId: 'tab-9' })
    writer.edit(1, 'A research draft')
    await writer.flush()
    await writer.release()
    const { ctx, result } = await openAndRestore()
    expect(result).toBeNull()
    expect(win.prksWorkPrivateNotesRecoveryView(ctx)).toBeNull()
  })
})

describe('Work Reminders protection warning', () => {
  it('warns and keeps the leave guard while recovery storage refuses the text, until the server holds it', async () => {
    const { ctx, ta } = await openWork()
    idb.failCommits = 1000
    const s = type(ctx, ta!, 'Saved reminder. Unprotected')
    await s.recovery!.flush()
    expect(s.recovery!.state()).toBe('unprotected')
    expect(win.prksWorkPrivateNotesRecoveryView(ctx)).toMatchObject({ drafts: 0, unprotected: 'quota' })
    expect(page!.rt.writers.leaveGuardActive()).toBe(true)
    win.prksFlushPendingPrivateNotes(ctx)
    await waitFor(() => sync.rows().length === 1 && s.recoveryQueued?.opId === 'op-1', 'queued')
    // Queued is not saved: the guard stays until the acknowledgement.
    expect(page!.rt.writers.leaveGuardActive()).toBe(true)
    sync.ack('op-1', 6)
    await waitFor(() => win.prksWorkPrivateNotesRecoveryView(ctx) === null, 'warning cleared on ack')
    expect(page!.rt.writers.leaveGuardActive()).toBe(false)
  })
})

describe('Work delete and recovery drafts (#533)', () => {
  /** Typed on a note read from the server for that Work. */
  const SERVER_BASE: DraftBase = { revision: 0, length: 0, fingerprint: '0'.repeat(32), source: 'server' }

  /** A Research Notes draft of w1 that no editor holds any more. */
  async function researchDraft(text: string, base = SERVER_BASE): Promise<string> {
    await page!.rt.start()
    const writer = page!.rt.writers.openWriter({ kind: 'work-research-note', entityType: 'work', entityId: 'w1', paneId: 'tab-9', base })
    writer.edit(1, text)
    await writer.flush()
    await writer.release()
    return writer.draftId()!
  }

  /** Reminders typed and queued, then the pane closes; the research draft beside it. */
  async function draftsOfBothKinds() {
    const { ctx, ta } = await openWork()
    type(ctx, ta!, 'Saved reminder. Unsaved')
    win.prksFlushPendingPrivateNotes(ctx)
    await queuedAs('Saved reminder. Unsaved')
    await researchDraft('Unsaved research')
    win.prksDestroyAllTabContexts()
    await settle()
    expect((await records()).map((r) => r.kind).sort()).toEqual(['work-private-note', 'work-research-note'])
  }

  const DELETED_WORK_PREFIX = 'prks.workRecoveryCleanup.v1.'
  const deleteOp = () => sync.rows().find((r) => r.operation === 'DELETE_WORK')!.op_id

  let responses: Record<string, { status: number; body: unknown }> = {}
  const probed: string[] = []
  beforeEach(() => {
    responses = {}
    probed.length = 0
    for (const id of markedEntries().map((entry) => entry.id)) window.localStorage.removeItem(DELETED_WORK_PREFIX + id)
    // Cleanup runs only where recovery storage exists; this page's is the fake one.
    win.indexedDB = { databases: async () => [{ name: 'prks-editor-recovery-v1' }] }
    win.prksRequest = async (url: string) => {
      probed.push(url)
      const answer = responses[url] || { status: 200, body: { work_id: 'w1', research_note_revision: 0, private_note_revision: 5 } }
      return { status: answer.status, json: async () => answer.body }
    }
  })
  afterEach(() => {
    delete win.prksRequest
    delete win.indexedDB
  })
  /** Marked Works in retry order: the least recently tried first, then the earliest marked. */
  function markedEntries(): Array<{ id: string; marked: number; tried: number }> {
    const entries = []
    for (let i = 0; i < window.localStorage.length; i++) {
      const key = window.localStorage.key(i)!
      if (!key.startsWith(DELETED_WORK_PREFIX)) continue
      entries.push({ id: key.slice(DELETED_WORK_PREFIX.length), ...JSON.parse(window.localStorage.getItem(key)!) })
    }
    return entries.sort((a, b) => a.tried - b.tried || a.marked - b.marked || a.id.localeCompare(b.id))
  }
  const marked = () => markedEntries().map((entry) => entry.id)
  const mark = (id = 'w1', marked = 1) =>
    window.localStorage.setItem(DELETED_WORK_PREFIX + id, JSON.stringify({ marked, tried: 0 }))
  const goneOnServer = (workId = 'w1') => {
    responses['/api/works/' + workId + '/notes-state'] = { status: 404, body: { error: 'Work not found' } }
  }

  it('keeps every draft while the delete is only requested, and removes both kinds once it is acknowledged', async () => {
    await draftsOfBothKinds()
    await win.prksDeleteWorkDurably('w1')
    // The request cancelled the never-sent Reminders row: the draft is now its only copy.
    expect(sync.rows().map((r) => r.operation)).toEqual(['DELETE_WORK'])
    await settle()
    expect(await records()).toHaveLength(2)
    sync.ackDelete(deleteOp())
    await waitFor(async () => (await records()).length === 0, 'both kinds removed on the acknowledgement')
    expect(await page!.rt.store.listAll()).toEqual([])
    // Only a later load's retry, finding nothing left, clears the mark.
    expect(marked()).toEqual(['w1'])
    goneOnServer()
    expect(await win.prksRetryDeletedWorkRecovery()).toEqual(['w1'])
    expect(marked()).toEqual([])
  })

  it('keeps the mark after the acknowledgement while another tab may hold an edit not yet stored', async () => {
    await draftsOfBothKinds()
    await win.prksDeleteWorkDurably('w1')
    sync.ackDelete(deleteOp())
    await waitFor(async () => (await records()).length === 0, 'both kinds removed on the acknowledgement')
    // The other tab's debounce lands its first generation after the cleanup looked.
    const other = startRecoveryPage({ browser, name: 'other-tab', idb, session: browser.sessionStorageWith(), local, withoutLocks: false, background: true })
    await other.rt.start()
    const late = other.rt.writers.openWriter({ kind: KIND, entityType: 'work', entityId: 'w1', paneId: 'tab-1', base: SERVER_BASE })
    late.edit(1, 'Typed in the other tab just before the acknowledgement')
    await late.flush()
    expect(marked()).toEqual(['w1'])
    // That tab closes; a later load removes the draft.
    other.rt.dispose()
    other.locks.releaseAll()
    await settle()
    goneOnServer()
    expect(await win.prksRetryDeletedWorkRecovery()).toEqual(['w1'])
    expect(await records()).toEqual([])
    expect(marked()).toEqual([])
  })

  it('keeps the drafts when the delete conflicts, and the surviving Work restores its Reminders', async () => {
    await draftsOfBothKinds()
    await win.prksDeleteWorkDurably('w1')
    sync.conflict(deleteOp())
    await settle()
    expect(await records()).toHaveLength(2)
    // Resolved by discarding the delete: the Work is still there and its draft comes back.
    sync.reset()
    await reload()
    const { ta, result } = await openAndRestore()
    expect(result).toMatchObject({ restored: true })
    expect(ta!.value).toBe('Saved reminder. Unsaved')
  })

  it('never removes a draft a live editor owns, and cleans it once that tab is gone and the server says so', async () => {
    const other = startRecoveryPage({ browser, name: 'other-tab', idb, session: browser.sessionStorageWith(), local, withoutLocks: false, background: true })
    await other.rt.start()
    const live = other.rt.writers.openWriter({ kind: KIND, entityType: 'work', entityId: 'w1', paneId: 'tab-1', base: SERVER_BASE })
    live.edit(1, 'Still typing in the other tab')
    await live.flush()
    await researchDraft('Unsaved research')
    await win.prksDeleteWorkDurably('w1')
    sync.ackDelete(deleteOp())
    await waitFor(async () => (await records()).length === 1, 'the orphan removed')
    const [kept] = await records()
    expect(kept!.draftId).toBe(live.draftId())
    expect(marked()).toEqual(['w1'])
    // The other tab closes; a later load retries the marked Work.
    other.rt.dispose()
    other.locks.releaseAll()
    await settle()
    goneOnServer()
    expect(await win.prksRetryDeletedWorkRecovery()).toEqual(['w1'])
    expect(await records()).toEqual([])
    expect(marked()).toEqual([])
  })

  it('keeps a crashed LAN tab\'s draft after the delete: a frozen tab still editing it looks the same', async () => {
    withoutLocks = true
    startPage()
    const { ctx, ta } = await openWork()
    await type(ctx, ta!, 'Saved reminder. Crashed').recovery!.flush()
    await closeTabAndOpenAnother('crash')
    await page!.rt.start()
    const [record] = await records()
    expect(await page!.rt.classify(record!)).toBe('unknown')
    await win.prksDeleteWorkDurably('w1')
    sync.ackDelete(deleteOp())
    expect(await win.prksCleanupDeletedWorkRecovery('w1')).toMatchObject({ removed: [], unknown: [record!.draftId] })
    goneOnServer()
    // However often it is retried, the draft and the mark both stay: one probe per load.
    for (let load = 1; load <= 6; load++) {
      probed.length = 0
      expect(await win.prksRetryDeletedWorkRecovery()).toEqual(['w1'])
      expect(probed).toEqual(['/api/works/w1/notes-state'])
      expect(marked()).toEqual(['w1'])
    }
    expect((await records()).map((r) => r.draftId)).toEqual([record!.draftId])
  })

  it('keeps the mark through any number of retries the server does not answer, and cleans on the first "Work not found"', async () => {
    await researchDraft('Waiting for the server')
    mark()
    responses['/api/works/w1/notes-state'] = { status: 503, body: {} }
    for (let load = 1; load <= 5; load++) {
      expect(await win.prksRetryDeletedWorkRecovery()).toEqual([])
      expect(marked()).toEqual(['w1'])
    }
    expect(await records()).toHaveLength(1)
    goneOnServer()
    expect(await win.prksRetryDeletedWorkRecovery()).toEqual(['w1'])
    expect(await records()).toEqual([])
    expect(marked()).toEqual([])
  })

  it('keeps the mark while another tab holds the draft through many retries, and cleans once that tab is gone', async () => {
    const other = startRecoveryPage({ browser, name: 'other-tab', idb, session: browser.sessionStorageWith(), local, withoutLocks: false, background: true })
    await other.rt.start()
    const live = other.rt.writers.openWriter({ kind: KIND, entityType: 'work', entityId: 'w1', paneId: 'tab-1', base: SERVER_BASE })
    live.edit(1, 'Still open in the other tab')
    await live.flush()
    mark()
    goneOnServer()
    for (let load = 1; load <= 5; load++) {
      expect(await win.prksRetryDeletedWorkRecovery()).toEqual(['w1'])
      expect(marked()).toEqual(['w1'])
      expect(await records()).toHaveLength(1)
    }
    other.rt.dispose()
    other.locks.releaseAll()
    await settle()
    expect(await win.prksRetryDeletedWorkRecovery()).toEqual(['w1'])
    expect(await records()).toEqual([])
    expect(marked()).toEqual([])
  })

  it('never removes drafts of a Work this device did not see deleted, whatever the server answers', async () => {
    // Same origin, another storage root: the new library has no w1, but the drafts belong to the old one.
    await researchDraft('Typed against the previous library')
    goneOnServer()
    expect(await win.prksRetryDeletedWorkRecovery()).toEqual([])
    expect(probed).toEqual([])
    expect(await records()).toHaveLength(1)
  })

  it('retries an acknowledged delete whose cleanup a shutdown interrupted, once the server also says the Work is gone', async () => {
    await draftsOfBothKinds()
    await win.prksDeleteWorkDurably('w1')
    const cleanup = page!.rt.cleanupDeletedEntity
    page!.rt.cleanupDeletedEntity = async () => {
      page!.rt.cleanupDeletedEntity = cleanup
      throw new Error('the browser shut down')
    }
    sync.ackDelete(deleteOp())
    await waitFor(() => page!.rt.cleanupDeletedEntity === cleanup, 'cleanup attempted')
    await settle()
    expect(await records()).toHaveLength(2)
    expect(marked()).toEqual(['w1'])
    // Another 404, or no answer, proves nothing: the mark stays.
    responses['/api/works/w1/notes-state'] = { status: 404, body: { error: 'Not found' } }
    expect(await win.prksRetryDeletedWorkRecovery()).toEqual([])
    expect(await records()).toHaveLength(2)
    goneOnServer()
    expect(await win.prksRetryDeletedWorkRecovery()).toEqual(['w1'])
    expect(await records()).toEqual([])
    expect(marked()).toEqual([])
  })

  it('keeps the drafts of a marked Work the server holds again, and forgets the mark', async () => {
    await researchDraft('Typed after a restore')
    mark()
    expect(await win.prksRetryDeletedWorkRecovery()).toEqual([])
    expect(await records()).toHaveLength(1)
    expect(marked()).toEqual([])
  })

  it('retries a marked Work whose draft is held only in a closed tab\'s emergency key, with no recovery database yet', async () => {
    // The tab closed before its first IndexedDB commit: no database, only the key.
    win.indexedDB = { databases: async () => [] }
    const pageInstanceId = 'p-closed-early'
    const key = JSON.stringify({
      v: 1, pageInstanceId, runtimeId: null, at: 1,
      entries: [{
        draftId: 'd-early', kind: KIND, entityType: 'work', entityId: 'w1', generation: 1, committedGeneration: 0,
        body: 'Saved reminder. Closed early',
        lineage: { createdAt: 1, owner: { runtimeId: null, pageInstanceId, paneId: 'tab-1' }, base: SERVER_BASE },
      }],
    })
    // The page reads keys from its own localStorage before it builds a runtime; the runtime here reads `local`.
    local.setItem(EMERGENCY_KEY_PREFIX + pageInstanceId, key)
    window.localStorage.setItem(EMERGENCY_KEY_PREFIX + pageInstanceId, key)
    mark()
    goneOnServer()
    expect(await win.prksRetryDeletedWorkRecovery()).toEqual(['w1'])
    expect((await records()).filter((r) => r.status !== 'discarded')).toEqual([])
    expect([...local.map.keys()].filter((k) => k.startsWith(EMERGENCY_KEY_PREFIX))).toEqual([])
    expect(marked()).toEqual([])
    window.localStorage.removeItem(EMERGENCY_KEY_PREFIX + pageInstanceId)
  })

  /** Emergency keys this code cannot parse: the ones a later read or a newer bundle may recover keep the mark. */
  const unreadableKeys = [
    { name: 'of a newer schema', raw: JSON.stringify({ v: 99, pageInstanceId: 'p-odd', entries: [] }), keeps: true },
    { name: 'whose read fails', raw: JSON.stringify({ v: 1, pageInstanceId: 'p-odd', entries: [] }), failRead: true, keeps: true },
    {
      name: 'holding a draft kind a newer bundle added',
      raw: JSON.stringify({
        v: 1, pageInstanceId: 'p-odd', runtimeId: null, at: 1,
        entries: [{
          draftId: 'd-new-kind', kind: 'folder-reminder', entityType: 'work', entityId: 'w1', generation: 1, committedGeneration: 0,
          body: 'Typed in a newer bundle', lineage: { createdAt: 1, owner: { runtimeId: null, pageInstanceId: 'p-odd', paneId: 'tab-1' }, base: SERVER_BASE },
        }],
      }),
      keeps: true,
    },
    {
      name: 'holding a broken entry of a known kind',
      raw: JSON.stringify({ v: 1, pageInstanceId: 'p-odd', runtimeId: null, at: 1, entries: [{ draftId: 'd-broken', kind: KIND }] }),
      keeps: false,
    },
    { name: 'of malformed JSON', raw: '{"v": 1, "entr', keeps: false },
    { name: 'of an older schema', raw: JSON.stringify({ v: 0, pageInstanceId: 'p-odd', entries: [] }), keeps: false },
    { name: 'naming another page', raw: JSON.stringify({ v: 1, pageInstanceId: 'p-other', entries: [] }), keeps: false },
  ]
  for (const database of [false, true]) {
    for (const odd of unreadableKeys) {
      it(`${odd.keeps ? 'keeps' : 'does not keep'} the mark for an emergency key ${odd.name}, ${database ? 'with' : 'without'} a recovery database`, async () => {
        if (!database) win.indexedDB = { databases: async () => [] }
        const key = EMERGENCY_KEY_PREFIX + 'p-odd'
        local.setItem(key, odd.raw)
        window.localStorage.setItem(key, odd.raw)
        const failing = { now: !!odd.failRead }
        // The page's own localStorage (jsdom: patched on the prototype) and the runtime's.
        const readers = [local, Storage.prototype] as Array<{ getItem(k: string): string | null }>
        const originals = readers.map((storage) => storage.getItem)
        readers.forEach((storage, i) => {
          storage.getItem = function (this: unknown, k: string) {
            if (failing.now && k === key) throw new Error('storage is busy')
            return originals[i]!.call(this, k)
          }
        })
        try {
          mark()
          goneOnServer()
          expect(await win.prksRetryDeletedWorkRecovery()).toEqual(['w1'])
          expect(marked()).toEqual(odd.keeps ? ['w1'] : [])
          // Never removed or rewritten for this.
          failing.now = false
          expect(local.getItem(key)).toBe(odd.raw)
          if (odd.keeps) {
            // Once a later read or a newer bundle has merged it, the next retry finishes.
            local.removeItem(key)
            window.localStorage.removeItem(key)
            expect(await win.prksRetryDeletedWorkRecovery()).toEqual(['w1'])
            expect(marked()).toEqual([])
          }
        } finally {
          readers.forEach((storage, i) => { storage.getItem = originals[i]! })
          local.removeItem(key)
          window.localStorage.removeItem(key)
        }
      })
    }
  }

  it('builds no recovery runtime for a confirmed delete when there is no database and no emergency key', async () => {
    win.indexedDB = { databases: async () => [] }
    const api = win.prksEditorRecovery as { runtime(): unknown }
    const runtime = api.runtime
    let built = 0
    api.runtime = () => {
      built += 1
      return runtime()
    }
    try {
      mark()
      goneOnServer()
      expect(await win.prksRetryDeletedWorkRecovery()).toEqual(['w1'])
      expect(built).toBe(0)
      expect(marked()).toEqual([])
    } finally {
      api.runtime = runtime
    }
  })

  it('keeps the mark while a record of the Work this code cannot read remains', async () => {
    await researchDraft('Written by a newer PRKS')
    const cleanup = page!.rt.cleanupDeletedEntity
    page!.rt.cleanupDeletedEntity = async () =>
      ({ removed: [], live: [], unknown: [], changed: [], unsupported: ['d-newer'], suppressed: [] })
    try {
      mark()
      goneOnServer()
      expect(await win.prksRetryDeletedWorkRecovery()).toEqual(['w1'])
      expect(marked()).toEqual(['w1'])
    } finally {
      page!.rt.cleanupDeletedEntity = cleanup
    }
  })

  it('gives every mark its turn: ones that do not clear move behind the rest', async () => {
    await researchDraft('Interrupted cleanup')
    // Eight marks ahead of w1 whose server never answers, so none of them clears.
    const stuck = Array.from({ length: 8 }, (_, i) => 'stuck-' + i)
    for (const id of stuck) responses['/api/works/' + id + '/notes-state'] = { status: 503, body: {} }
    stuck.forEach((id, i) => mark(id, i + 1))
    mark('w1', 9)
    goneOnServer()
    expect(await win.prksRetryDeletedWorkRecovery()).toEqual([])
    expect(marked()).toEqual(['w1'].concat(stuck))
    expect(await win.prksRetryDeletedWorkRecovery()).toEqual(['w1'])
    expect(await records()).toEqual([])
    // The seven tried again went behind the one that waited.
    expect(marked()).toEqual(['stuck-7'].concat(stuck.slice(0, 7)))
  })

  it('keeps every mark, however many there are, and removes no draft for any of them', async () => {
    await draftsOfBothKinds()
    for (let i = 0; i < 64; i++) mark('other-' + i)
    await win.prksDeleteWorkDurably('w1')
    const cleanup = page!.rt.cleanupDeletedEntity
    page!.rt.cleanupDeletedEntity = async () => {
      page!.rt.cleanupDeletedEntity = cleanup
      throw new Error('the browser shut down')
    }
    sync.ackDelete(deleteOp())
    await waitFor(() => page!.rt.cleanupDeletedEntity === cleanup, 'cleanup attempted')
    await settle()
    const ids = marked()
    expect(ids).toHaveLength(65)
    expect(ids).toContain('w1')
    expect(await records()).toHaveLength(2)
  })

  it('never loses a mark another tab writes or brings back one it clears while a retry runs', async () => {
    mark('w1', 1)
    mark('w3', 2)
    responses['/api/works/w3/notes-state'] = { status: 503, body: {} }
    const request = win.prksRequest
    win.prksRequest = async () => {
      // Another tab: confirms the delete of w2 and finishes cleaning w3.
      mark('w2', 3)
      window.localStorage.removeItem(DELETED_WORK_PREFIX + 'w3')
      win.prksRequest = request
      return { status: 503, json: async () => ({}) }
    }
    expect(await win.prksRetryDeletedWorkRecovery()).toEqual([])
    expect(marked().sort()).toEqual(['w1', 'w2'])
  })

  it('keeps the mark while a pane still shows the Work, even one nobody has typed in yet', async () => {
    await openWork()
    mark()
    goneOnServer()
    expect(await win.prksRetryDeletedWorkRecovery()).toEqual(['w1'])
    expect(marked()).toEqual(['w1'])
    win.prksDestroyAllTabContexts()
    await settle()
    expect(await win.prksRetryDeletedWorkRecovery()).toEqual(['w1'])
    expect(marked()).toEqual([])
  })

  it('keeps the mark while a pane still showing the Work holds an edit not yet in storage', async () => {
    const { ctx, ta } = await openWork()
    type(ctx, ta!, 'Saved reminder. Typed within the debounce')
    expect(await records()).toEqual([])
    mark()
    await win.prksCleanupDeletedWorkRecovery('w1')
    expect(marked()).toEqual(['w1'])
    // The pane closes; its draft is then an orphan a later load removes.
    win.prksDestroyAllTabContexts()
    await settle()
    goneOnServer()
    expect(await win.prksRetryDeletedWorkRecovery()).toEqual(['w1'])
    expect((await records()).filter((r) => r.status !== 'discarded')).toEqual([])
    expect(marked()).toEqual([])
  })

  async function foldAway() {
    const store = sync.store as unknown as { deleteWork(id: string): Promise<unknown> }
    const original = store.deleteWork
    store.deleteWork = async () => null
    try {
      expect(await win.prksDeleteWorkDurably('w1')).toBeNull()
    } finally {
      store.deleteWork = original
    }
  }

  it('removes the drafts, even of the pane that deletes it, when a Work whose creation never left this device folds away', async () => {
    // The deleting pane still shows the Work and its Reminders session, as Delete File in its own Details does.
    const { ctx, ta } = await openWork()
    await type(ctx, ta!, 'Saved reminder. Typed in a new Work').recovery!.flush()
    await researchDraft('Typed in a new Work')
    expect(await records()).toHaveLength(2)
    await foldAway()
    await waitFor(async () => (await records()).length === 0, 'folded creation cleans up')
    // The mark stays for a later load: another tab may hold an edit not yet in storage.
    expect(marked()).toEqual(['w1'])
    // The deleting pane navigates away; a later load clears the mark.
    win.prksDestroyAllTabContexts()
    await settle()
    goneOnServer()
    expect(await win.prksRetryDeletedWorkRecovery()).toEqual(['w1'])
    expect(marked()).toEqual([])
  })

  it('releases an edit not yet in storage before it decides the folded Work has no drafts', async () => {
    // Nothing reached IndexedDB yet: the database appears only once a writer commits.
    let gated = false
    win.indexedDB = {
      databases: async () => {
        gated = true
        return (await page!.rt.store.listAll()).length ? [{ name: 'prks-editor-recovery-v1' }] : []
      },
    }
    const { ctx, ta } = await openWork()
    type(ctx, ta!, 'Saved reminder. Typed within the debounce')
    expect(await records()).toEqual([])
    await foldAway()
    await waitFor(() => gated, 'folded creation cleanup checked for recovery storage')
    // Whatever writer is left settles its edit; none may land a draft of the deleted Work.
    await waitFor(() => page!.rt.writers.writers().every((w) => w.state() !== 'pending'), 'writers idle')
    await settle()
    expect((await records()).filter((r) => r.status !== 'discarded')).toEqual([])
  })
})
