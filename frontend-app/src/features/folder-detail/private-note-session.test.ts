/**
 * #534 PR B: Folder Reminders sessions per pane, with an observed
 * acknowledged base. `ui.js` sessions and `folder-state.js` bases run for real
 * over a fake durable queue with the local store's Folder field rules.
 */
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import folderStateSource from '../../../../frontend/js/folder-state.js?raw'
import ownerResourceSource from '../../../../frontend/js/owner-resource.js?raw'
import tabContextSource from '../../../../frontend/js/tab-context.js?raw'
import uiSource from '../../../../frontend/js/ui.js?raw'
import { createFolderQueue, type FolderServer } from './test-support/folder-note-queue'

type Folder = { id: string; title: string; private_notes: string }

type Session = {
  folderId: string
  entityId: string
  draftText: string
  dirty: boolean
  state: string
  retired: boolean
  ownerTabId: string
  ownerGeneration: number
  editGeneration: number
  ownQueued: { opId: string; generation: number; text: string } | null
  editBase: { value: string | null; revision: number | null; source: string; start: string | null } | null
}

type Observed = { folderId: string; value: string; revision: number; source: string }

type Ctx = {
  tabId: string
  generation: number
  destroyed: boolean
  ui: {
    rightPanelTab: string
    folderPrivateNoteSession: Session | null
    folderPrivateNoteHolds?: Record<string, string> | null
  }
  setEntity: (type: string, value: Folder | null) => void
  getEntity: (type: string) => Folder | null
  getResource: (name: string) => unknown
  clearResource: (name: string) => void
  beginRoute: (route?: unknown) => number
}

type W = {
  eval: (code: string) => void
  prksWorkspaceSnapshot: () => { focusedTabId: string; mainTabId: string }
  prksMountTabContext: (tabId: string, host: HTMLElement) => void
  prksGetTabContext: (tabId: string) => Ctx
  prksDestroyAllTabContexts: () => void
  initPrksPrivateNotesEditor: (entityType: string, entityId: string, owner: Ctx) => void
  prksFlushPendingPrivateNotes: (owner: Ctx) => void
  prksResetPrivateNoteDraftsForTest: () => void
  prksPrivateNotesTextForEntity: (entityType: string, entityId: string, serverText: string) => string
  prksRememberFolderNotesCanonical: (owner: Ctx, folder: Folder, source: string) => void
  prksEnsureFolderNotesBase: (owner: Ctx, folder: Folder, options?: unknown) => Promise<Observed | null>
  prksFolderNoteObserved: (owner: Ctx, folderId?: string) => Observed | null
  prksRefreshPendingFolderNotes: () => Promise<unknown>
  prksBindFolderPrivateNotesSync: (owner: Ctx) => void
  prksOfflineReadEntity: (kind: string, id: string, url: string, options?: unknown) => Promise<unknown>
  prksOfflineInvalidateEntity: () => Promise<boolean>
  prksDurableOperationsOrNone: () => Promise<unknown[]>
  prksSync?: unknown
}

const w = window as unknown as W
const FA = 'F-A'
const FB = 'F-B'

let server: FolderServer
let queue: ReturnType<typeof createFolderQueue>
/* Where reads come from: the server, the offline cache, or nowhere. */
let reads: { state: 'server' | 'cache' | 'unavailable'; body: 'server' | 'cache' }

function folder(id: string, notes?: string): Folder {
  return { id, title: id, private_notes: notes ?? server[id]?.private_notes ?? '' }
}

function stateOf(id: string) {
  const revision = server[id]?.revision ?? 0
  return {
    folder_id: id,
    fields: {
      title: { revision: 0 }, description: { revision: 0 },
      private_notes: { revision }, parent_id: { revision: 0 },
    },
  }
}

/* When each cached copy was taken; a server read is taken now. */
let clock = 100
let cachedAt = { state: 10, body: 20 }
/* Runs between the revision read and the body read that follows it. */
let betweenReads: (() => void) | null = null

function installEnvironment() {
  w.prksOfflineReadEntity = async (kind: string, id: string) => {
    if (kind === 'folder-state') {
      if (reads.state === 'unavailable') return { value: null, source: 'unavailable', cachedAt: null }
      const read = { value: stateOf(id), source: reads.state, cachedAt: reads.state === 'server' ? ++clock : cachedAt.state }
      const hook = betweenReads
      betweenReads = null
      if (hook) hook()
      return read
    }
    return { value: folder(id), source: reads.body, cachedAt: reads.body === 'server' ? ++clock : cachedAt.body }
  }
  w.prksOfflineInvalidateEntity = async () => true
  w.prksDurableOperationsOrNone = async () => queue.store.listOperations()
  w.prksSync = queue
}

function installShell() {
  document.body.innerHTML = `
    <div id="panel-content"></div>
    <div id="host-a"></div>
    <div id="host-b"></div>`
}

function mountPair(focused = 'tab-a') {
  const workspace = { focusedTabId: focused, mainTabId: 'tab-a' }
  w.prksWorkspaceSnapshot = () => workspace
  w.prksMountTabContext('tab-a', document.getElementById('host-a')!)
  w.prksMountTabContext('tab-b', document.getElementById('host-b')!)
  return { workspace, ownerA: w.prksGetTabContext('tab-a'), ownerB: w.prksGetTabContext('tab-b') }
}

/* The Folder route for `id` in this pane: a new generation, its detail read and base. */
async function openFolder(owner: Ctx, id: string, source: 'server' | 'cache' = 'server') {
  owner.beginRoute()
  const read = folder(id)
  owner.setEntity('folder', { ...read })
  w.prksRememberFolderNotesCanonical(owner, read, source)
  await w.prksEnsureFolderNotesBase(owner, read)
  w.prksBindFolderPrivateNotesSync(owner)
}

/* The Reminders card of `id`, painted into the shared right panel for this pane. */
function mountCard(owner: Ctx, id: string) {
  const panel = document.getElementById('panel-content')!
  panel.dataset.prksOwnerTabId = owner.tabId
  panel.dataset.prksOwnerGeneration = String(owner.generation)
  const field = document.createElement('textarea')
  field.id = `prks-private-notes-folder-${id}`
  const status = document.createElement('p')
  status.id = `prks-private-notes-status-folder-${id}`
  panel.replaceChildren(field, status)
  w.initPrksPrivateNotesEditor('folder', id, owner)
  return { field, status }
}

function type(field: HTMLTextAreaElement, text: string) {
  field.value = text
  field.dispatchEvent(new Event('input', { bubbles: true }))
}

async function settle() {
  for (let i = 0; i < 6; i += 1) await new Promise((resolve) => setTimeout(resolve, 0))
}

const session = (owner: Ctx) => owner.ui.folderPrivateNoteSession
const noteRows = () => queue.rows().filter((r) => r.payload.field === 'private_notes')

beforeAll(() => {
  w.eval(ownerResourceSource)
  w.eval(tabContextSource)
  w.eval(folderStateSource)
  w.eval(uiSource)
})

beforeEach(async () => {
  server = {
    [FA]: { private_notes: 'Server A', revision: 4 },
    [FB]: { private_notes: 'Server B', revision: 9 },
  }
  queue = createFolderQueue(server)
  reads = { state: 'server', body: 'server' }
  cachedAt = { state: 10, body: 20 }
  betweenReads = null
  installEnvironment()
  await w.prksRefreshPendingFolderNotes()
})

afterEach(() => {
  vi.useRealTimers()
  w.prksResetPrivateNoteDraftsForTest()
  w.prksDestroyAllTabContexts()
  document.body.innerHTML = ''
})

describe('Folder Reminders observed base', () => {
  it('joins the read body with the private_notes field revision, and says where both came from', async () => {
    installShell()
    const { ownerA } = mountPair()
    await openFolder(ownerA, FA)
    expect(w.prksFolderNoteObserved(ownerA, FA)).toEqual({ folderId: FA, value: 'Server A', revision: 4, source: 'server' })
    expect(w.prksFolderNoteObserved(ownerA, FB)).toBeNull()

    reads = { state: 'cache', body: 'server' }
    await openFolder(ownerA, FA)
    expect(w.prksFolderNoteObserved(ownerA, FA)?.source).toBe('cache')
    reads = { state: 'server', body: 'server' }
    await openFolder(ownerA, FA, 'cache')
    expect(w.prksFolderNoteObserved(ownerA, FA)?.source).toBe('cache')
  })

  it('has no base when the revision is unknown, and never guesses one', async () => {
    installShell()
    const { ownerA } = mountPair()
    reads = { state: 'unavailable', body: 'server' }
    await openFolder(ownerA, FA)
    expect(w.prksFolderNoteObserved(ownerA, FA)).toBeNull()
  })

  it('has no base when the server changed after the pane read the body, and queues nothing over it', async () => {
    installShell()
    const { ownerA } = mountPair()
    ownerA.beginRoute()
    const read = folder(FA)
    ownerA.setEntity('folder', { ...read })
    w.prksRememberFolderNotesCanonical(ownerA, read, 'server')
    /* Another device saves before this page reads the field revision. */
    server[FA] = { private_notes: 'Saved on another device', revision: 5 }
    expect(await w.prksEnsureFolderNotesBase(ownerA, read)).toBeNull()
    expect(w.prksFolderNoteObserved(ownerA, FA)).toBeNull()
    const { field, status } = mountCard(ownerA, FA)
    expect(field.value).toBe('Server A')
    type(field, 'Server A, edited')
    w.prksFlushPendingPrivateNotes(ownerA)
    await settle()
    expect(noteRows()).toEqual([])
    expect(status.textContent).toBe('Reminders cannot be saved yet — open this folder while connected once')
    expect(server[FA]!.private_notes).toBe('Saved on another device')
  })

  it('has no base when the server changes between the revision and the body read', async () => {
    installShell()
    const { ownerA } = mountPair()
    betweenReads = () => { server[FA] = { private_notes: 'Saved on another device', revision: 5 } }
    await openFolder(ownerA, FA)
    expect(w.prksFolderNoteObserved(ownerA, FA)).toBeNull()
  })

  it.each([
    ['a server revision with a cached body taken before it', { state: 'server', body: 'cache' }, { state: 10, body: 20 }, null],
    ['a cached revision with a cached body taken before it', { state: 'cache', body: 'cache' }, { state: 30, body: 20 }, null],
    ['a cached revision with a cached body taken after it', { state: 'cache', body: 'cache' }, { state: 10, body: 20 }, 'cache'],
    ['a cached revision with a server body read after it', { state: 'cache', body: 'server' }, { state: 10, body: 20 }, 'cache'],
  ] as const)('with %s, the base is %s', async (_how, from, times, expected) => {
    installShell()
    const { ownerA } = mountPair()
    reads = { ...from }
    cachedAt = { ...times }
    await openFolder(ownerA, FA, from.body)
    const observed = w.prksFolderNoteObserved(ownerA, FA)
    if (expected === null) expect(observed).toBeNull()
    else expect(observed).toEqual({ folderId: FA, value: 'Server A', revision: 4, source: expected })
  })

  it('a pending creation is revision 0 by construction', async () => {
    installShell()
    const { ownerA } = mountPair()
    ownerA.beginRoute()
    const created = { id: 'F-NEW', title: 'New', private_notes: '' }
    ownerA.setEntity('folder', created)
    w.prksRememberFolderNotesCanonical(ownerA, created, 'pending-create')
    await w.prksEnsureFolderNotesBase(ownerA, created, { pendingCreate: true })
    expect(w.prksFolderNoteObserved(ownerA, 'F-NEW')).toEqual({ folderId: 'F-NEW', value: '', revision: 0, source: 'pending-create' })
  })

  it('an acknowledgement advances the base to what the server stores; an older read never moves it back', async () => {
    installShell()
    const { ownerA } = mountPair()
    await openFolder(ownerA, FA)
    const { field } = mountCard(ownerA, FA)
    type(field, '  Call the printer \n')
    w.prksFlushPendingPrivateNotes(ownerA)
    await settle()
    const [row] = noteRows()
    queue.ack(row.op_id, 5)
    expect(w.prksFolderNoteObserved(ownerA, FA)).toEqual({ folderId: FA, value: 'Call the printer', revision: 5, source: 'server' })
    expect(ownerA.getEntity('folder')?.private_notes).toBe('Call the printer')

    /* A read that started before the acknowledgement answers with revision 4. */
    server[FA] = { private_notes: 'Server A', revision: 4 }
    await w.prksEnsureFolderNotesBase(ownerA, folder(FA, 'Server A'))
    expect(w.prksFolderNoteObserved(ownerA, FA)?.revision).toBe(5)
  })
})

describe('Folder Reminders sessions', () => {
  it('saves after the 850 ms debounce through the Folder field contract, and says so', async () => {
    vi.useFakeTimers()
    installShell()
    const { ownerA } = mountPair()
    await openFolder(ownerA, FA)
    const { field, status } = mountCard(ownerA, FA)
    expect(field.value).toBe('Server A')
    type(field, 'Buy toner')
    expect(status.textContent).toBe('Drafting…')
    await vi.advanceTimersByTimeAsync(849)
    expect(queue.saves).toHaveLength(0)
    await vi.advanceTimersByTimeAsync(1)
    expect(queue.saves).toEqual([{
      folderId: FA,
      changes: { private_notes: 'Buy toner' },
      base: { private_notes: { value: 'Server A', revision: 4 } },
    }])
    await vi.advanceTimersByTimeAsync(0)
    expect(status.textContent).toBe('Saved')
    const [row] = noteRows()
    expect(session(ownerA)?.ownQueued).toEqual({ opId: row.op_id, generation: 1, text: 'Buy toner' })
    expect(session(ownerA)?.state).toBe('committed')
    expect(session(ownerA)?.dirty).toBe(false)
    expect(queue.rows().every((r) => r.payload.field === 'private_notes')).toBe(true)
  })

  it('a blur saves at once', async () => {
    installShell()
    const { ownerA } = mountPair()
    await openFolder(ownerA, FA)
    const { field } = mountCard(ownerA, FA)
    type(field, 'Now')
    field.dispatchEvent(new Event('blur'))
    await settle()
    expect(noteRows().map((r) => r.payload.value)).toEqual(['Now'])
  })

  it('Folder A -> B -> A keeps unsaved text in the pane that typed it', async () => {
    installShell()
    const { ownerA } = mountPair()
    await openFolder(ownerA, FA)
    const a = mountCard(ownerA, FA)
    queue.failNext(1)
    type(a.field, 'A, not yet saved')
    /* Leaving flushes; the write fails after the pane moved on. */
    w.prksFlushPendingPrivateNotes(ownerA)
    await openFolder(ownerA, FB)
    const b = mountCard(ownerA, FB)
    expect(b.field.value).toBe('Server B')
    expect(session(ownerA)?.folderId).toBe(FB)
    await settle()
    expect(ownerA.ui.folderPrivateNoteHolds?.[FA]).toBe('A, not yet saved')

    await openFolder(ownerA, FA)
    const back = mountCard(ownerA, FA)
    expect(back.field.value).toBe('A, not yet saved')
    expect(session(ownerA)?.dirty).toBe(true)
    expect(session(ownerA)?.state).toBe('drafting')
    expect(ownerA.ui.folderPrivateNoteHolds?.[FA]).toBeUndefined()
    w.prksFlushPendingPrivateNotes(ownerA)
    await settle()
    expect(noteRows().map((r) => [r.entity_id, r.payload.value, r.base_revision])).toEqual([[FA, 'A, not yet saved', 4]])
  })

  it('a save that settles after the pane moved on drops the copy it no longer needs', async () => {
    installShell()
    const { ownerA } = mountPair()
    await openFolder(ownerA, FA)
    const a = mountCard(ownerA, FA)
    type(a.field, 'Saved on the way out')
    w.prksFlushPendingPrivateNotes(ownerA)
    await openFolder(ownerA, FB)
    mountCard(ownerA, FB)
    await settle()
    expect(ownerA.ui.folderPrivateNoteHolds?.[FA]).toBeUndefined()
    expect(noteRows().map((r) => r.payload.value)).toEqual(['Saved on the way out'])
    expect(queue.saves[0].base).toEqual({ private_notes: { value: 'Server A', revision: 4 } })
  })

  it('switching panes during an in-flight save keeps each pane its own text', async () => {
    installShell()
    const { workspace, ownerA, ownerB } = mountPair()
    await openFolder(ownerA, FA)
    await openFolder(ownerB, FA)
    const a = mountCard(ownerA, FA)
    type(a.field, 'Typed in Main')
    w.prksFlushPendingPrivateNotes(ownerA)
    /* The right panel moves to the Secondary pane while that save is in flight. */
    ownerA.clearResource('privateNotesEditor')
    workspace.focusedTabId = 'tab-b'
    mountCard(ownerB, FA)
    expect(session(ownerB)?.state).toBe('committed')
    expect(session(ownerB)?.dirty).toBe(false)
    await settle()
    expect(session(ownerA)?.state).toBe('committed')
    expect(session(ownerA)?.ownQueued?.text).toBe('Typed in Main')
    expect(session(ownerB)?.ownQueued).toBeNull()
    /* Repainted, the Secondary pane shows the queued text, as its own clean state. */
    const b = mountCard(ownerB, FA)
    expect(b.field.value).toBe('Typed in Main')
    type(b.field, 'Typed in Secondary')
    expect(session(ownerA)?.draftText).toBe('Typed in Main')
    expect(session(ownerB)?.draftText).toBe('Typed in Secondary')
  })

  it('two panes editing the same Folder keep separate sessions; only its own row is a pane\'s predecessor', async () => {
    installShell()
    const { workspace, ownerA, ownerB } = mountPair()
    await openFolder(ownerA, FA)
    await openFolder(ownerB, FA)
    const a = mountCard(ownerA, FA)
    type(a.field, 'From Main')
    w.prksFlushPendingPrivateNotes(ownerA)
    await settle()
    const [mainRow] = noteRows()
    expect(session(ownerA)?.ownQueued?.opId).toBe(mainRow.op_id)

    ownerA.clearResource('privateNotesEditor')
    workspace.focusedTabId = 'tab-b'
    const b = mountCard(ownerB, FA)
    type(b.field, 'From Secondary')
    w.prksFlushPendingPrivateNotes(ownerB)
    await settle()
    const [secondaryRow] = noteRows()
    expect(secondaryRow.payload.value).toBe('From Secondary')
    expect(session(ownerB)?.ownQueued?.opId).toBe(secondaryRow.op_id)
    expect(session(ownerA)?.ownQueued?.opId).toBe(mainRow.op_id)

    queue.ack(secondaryRow.op_id, 5)
    expect(session(ownerB)?.ownQueued).toBeNull()
    expect(session(ownerA)?.ownQueued?.opId).toBe(mainRow.op_id)
    expect(session(ownerA)?.draftText).toBe('From Main')
    /* Both panes show the Folder, so both learn what the server now holds. */
    expect(w.prksFolderNoteObserved(ownerA, FA)?.value).toBe('From Secondary')
    expect(w.prksFolderNoteObserved(ownerB, FA)?.value).toBe('From Secondary')
  })

  it('an older acknowledgement never clears a newer edit', async () => {
    vi.useFakeTimers()
    installShell()
    const { ownerA } = mountPair()
    await openFolder(ownerA, FA)
    const { field, status } = mountCard(ownerA, FA)
    type(field, 'Generation one')
    await vi.advanceTimersByTimeAsync(850)
    const [older] = noteRows()
    queue.attempt(older.op_id)

    type(field, 'Generation two')
    await vi.advanceTimersByTimeAsync(850)
    expect(status.textContent).toBe('Still syncing — wait or resolve the conflict in Diagnostics')
    expect(session(ownerA)?.dirty).toBe(true)
    expect(session(ownerA)?.ownQueued?.opId).toBe(older.op_id)

    /* The acknowledgement is the busy retry's cue: the newer edit is saved
     * at once, on the acknowledged revision. */
    queue.ack(older.op_id, 5)
    expect(session(ownerA)?.draftText).toBe('Generation two')
    expect(field.value).toBe('Generation two')
    expect(w.prksFolderNoteObserved(ownerA, FA)).toMatchObject({ value: 'Generation one', revision: 5 })
    await vi.advanceTimersByTimeAsync(0)
    const [newer] = noteRows()
    expect(newer.payload.value).toBe('Generation two')
    expect(newer.base_revision).toBe(5)
    expect(session(ownerA)?.ownQueued).toEqual({ opId: newer.op_id, generation: 2, text: 'Generation two' })

    /* A late duplicate of the older acknowledgement changes nothing. */
    queue.replayAck(older, 5)
    expect(session(ownerA)?.ownQueued?.opId).toBe(newer.op_id)
    expect(w.prksFolderNoteObserved(ownerA, FA)?.revision).toBe(5)
  })

  it('a foreign queued row is shown but never taken for this pane\'s own', async () => {
    vi.useFakeTimers()
    installShell()
    const { ownerA } = mountPair()
    await openFolder(ownerA, FA)
    const foreign = queue.foreign(FA, 'private_notes', 'From another tab', 4)
    queue.foreign(FA, 'title', 'Renamed elsewhere', 0)
    await w.prksRefreshPendingFolderNotes()
    const { field } = mountCard(ownerA, FA)
    expect(field.value).toBe('From another tab')
    expect(session(ownerA)?.ownQueued).toBeNull()

    queue.attempt(foreign.op_id)
    type(field, 'Mine')
    await vi.advanceTimersByTimeAsync(850)
    expect(session(ownerA)?.dirty).toBe(true)
    expect(session(ownerA)?.ownQueued).toBeNull()
    queue.ack(foreign.op_id, 5)
    expect(session(ownerA)?.ownQueued).toBeNull()
    expect(w.prksFolderNoteObserved(ownerA, FA)).toMatchObject({ value: 'From another tab', revision: 5 })
    await vi.advanceTimersByTimeAsync(400)
    const mine = noteRows()
    expect(mine.map((r) => [r.payload.value, r.base_revision])).toEqual([['Mine', 5]])
    expect(queue.rows().find((r) => r.payload.field === 'title')?.payload.value).toBe('Renamed elsewhere')
  })

  it('without a known base nothing is queued, and the pane says why', async () => {
    installShell()
    const { ownerA } = mountPair()
    reads = { state: 'unavailable', body: 'server' }
    await openFolder(ownerA, FA)
    const { field, status } = mountCard(ownerA, FA)
    type(field, 'Offline first visit')
    w.prksFlushPendingPrivateNotes(ownerA)
    await settle()
    expect(queue.saves).toHaveLength(0)
    expect(status.textContent).toBe('Reminders cannot be saved yet — open this folder while connected once')
    expect(session(ownerA)?.state).toBe('error')
    expect(session(ownerA)?.draftText).toBe('Offline first visit')
  })

  it('a cached base still saves, against the cached revision', async () => {
    installShell()
    const { ownerA } = mountPair()
    reads = { state: 'cache', body: 'cache' }
    await openFolder(ownerA, FA, 'cache')
    const { field } = mountCard(ownerA, FA)
    type(field, 'Offline edit')
    w.prksFlushPendingPrivateNotes(ownerA)
    await settle()
    expect(noteRows().map((r) => [r.payload.value, r.base_revision])).toEqual([['Offline edit', 4]])
  })

  it('a row that needs resolution keeps the text unsaved without retrying on a timer', async () => {
    vi.useFakeTimers()
    installShell()
    const { ownerA } = mountPair()
    await openFolder(ownerA, FA)
    const { field, status } = mountCard(ownerA, FA)
    type(field, 'First')
    await vi.advanceTimersByTimeAsync(850)
    const [row] = noteRows()
    queue.conflict(row.op_id)
    type(field, 'Second')
    await vi.advanceTimersByTimeAsync(850)
    expect(status.textContent).toBe('Still syncing — wait or resolve the conflict in Diagnostics')
    expect(session(ownerA)?.state).toBe('error')
    expect(session(ownerA)?.dirty).toBe(true)
    const saves = queue.saves.length
    await vi.advanceTimersByTimeAsync(5000)
    expect(queue.saves).toHaveLength(saves)
  })

  it('a failed write is reported and kept', async () => {
    installShell()
    const { ownerA } = mountPair()
    await openFolder(ownerA, FA)
    const { field, status } = mountCard(ownerA, FA)
    queue.failNext(1)
    type(field, 'Refused')
    w.prksFlushPendingPrivateNotes(ownerA)
    await settle()
    expect(status.textContent).toBe('Could not save')
    expect(session(ownerA)?.state).toBe('error')
    expect(field.value).toBe('Refused')
  })

  it('returning to the acknowledged text leaves nothing queued', async () => {
    installShell()
    const { ownerA } = mountPair()
    await openFolder(ownerA, FA)
    const { field, status } = mountCard(ownerA, FA)
    type(field, 'Changed')
    w.prksFlushPendingPrivateNotes(ownerA)
    await settle()
    type(field, 'Server A')
    w.prksFlushPendingPrivateNotes(ownerA)
    await settle()
    expect(noteRows()).toHaveLength(0)
    expect(status.textContent).toBe('Saved')
    expect(session(ownerA)?.ownQueued).toBeNull()
  })

  it('never drops unsaved text, however many Folders a pane visits while saves fail', async () => {
    installShell()
    const { ownerA } = mountPair()
    const ids = Array.from({ length: 40 }, (_, i) => 'F-' + i)
    for (const id of ids) server[id] = { private_notes: '', revision: 1 }
    queue.failNext(1000)
    for (const id of ids) {
      await openFolder(ownerA, id)
      const { field } = mountCard(ownerA, id)
      type(field, 'unsaved ' + id)
      /* Leaving flushes; every write is refused. */
      w.prksFlushPendingPrivateNotes(ownerA)
      await settle()
    }
    await openFolder(ownerA, FA)
    mountCard(ownerA, FA)
    expect(Object.keys(ownerA.ui.folderPrivateNoteHolds || {})).toEqual(ids)
    expect(noteRows()).toEqual([])
    /* The first Folder typed in still has its text, and saves it once writes work again. */
    queue.failNext(0)
    await openFolder(ownerA, 'F-0')
    const first = mountCard(ownerA, 'F-0')
    expect(first.field.value).toBe('unsaved F-0')
    expect(session(ownerA)?.dirty).toBe(true)
    w.prksFlushPendingPrivateNotes(ownerA)
    await settle()
    expect(noteRows().map((r) => [r.entity_id, r.payload.value])).toEqual([['F-0', 'unsaved F-0']])
    expect(ownerA.ui.folderPrivateNoteHolds?.['F-0']).toBeUndefined()
    expect(Object.keys(ownerA.ui.folderPrivateNoteHolds || {})).toHaveLength(39)
  })

  it('does not paint one pane\'s draft into another pane', async () => {
    installShell()
    const { workspace, ownerA, ownerB } = mountPair()
    await openFolder(ownerA, FA)
    await openFolder(ownerB, FA)
    const a = mountCard(ownerA, FA)
    type(a.field, 'Main only')
    expect(w.prksPrivateNotesTextForEntity('folder', FA, 'Server A')).toBe('Main only')
    workspace.focusedTabId = 'tab-b'
    expect(w.prksPrivateNotesTextForEntity('folder', FA, 'Server A')).toBe('Server A')
  })
})

describe('Folder Reminders edit base', () => {
  it('another pane\'s acknowledgement never moves the base of text this pane is still typing', async () => {
    installShell()
    const { workspace, ownerA, ownerB } = mountPair()
    await openFolder(ownerA, FA)
    await openFolder(ownerB, FA)
    /* Main types inside the debounce; nothing is saved yet. */
    const a = mountCard(ownerA, FA)
    type(a.field, 'A edit')
    expect(session(ownerA)?.editBase).toEqual({ value: 'Server A', revision: 4, source: 'server', start: 'Server A' })

    ownerA.clearResource('privateNotesEditor')
    workspace.focusedTabId = 'tab-b'
    const b = mountCard(ownerB, FA)
    type(b.field, 'B edit')
    w.prksFlushPendingPrivateNotes(ownerB)
    await settle()
    const [bRow] = noteRows()
    queue.ack(bRow.op_id, 5)
    expect(server[FA]).toMatchObject({ private_notes: 'B edit', revision: 5 })
    /* Main's pane learns what the server holds; its unsaved text keeps its base. */
    expect(w.prksFolderNoteObserved(ownerA, FA)).toMatchObject({ value: 'B edit', revision: 5 })
    expect(session(ownerA)?.editBase).toEqual({ value: 'Server A', revision: 4, source: 'server', start: 'Server A' })
    expect(session(ownerA)?.draftText).toBe('A edit')

    w.prksFlushPendingPrivateNotes(ownerA)
    await settle()
    /* Typed on revision 4, so the server answers with a conflict, never an overwrite. */
    expect(queue.saves.at(-1)).toEqual({
      folderId: FA,
      changes: { private_notes: 'A edit' },
      base: { private_notes: { value: 'Server A', revision: 4 } },
    })
    expect(noteRows().map((r) => [r.payload.value, r.base_revision])).toEqual([['A edit', 4]])
  })

  it('a held draft keeps the base it was typed on when the Folder changes while it is held', async () => {
    installShell()
    const { ownerA } = mountPair()
    await openFolder(ownerA, FA)
    const a = mountCard(ownerA, FA)
    queue.failNext(1)
    type(a.field, 'A, typed on 4')
    w.prksFlushPendingPrivateNotes(ownerA)
    await openFolder(ownerA, FB)
    mountCard(ownerA, FB)
    await settle()
    expect(ownerA.ui.folderPrivateNoteHolds?.[FA]).toBe('A, typed on 4')

    /* Another device saves the Folder while the text is held. */
    server[FA] = { private_notes: 'Saved on another device', revision: 5 }
    await openFolder(ownerA, FA)
    expect(w.prksFolderNoteObserved(ownerA, FA)).toMatchObject({ value: 'Saved on another device', revision: 5 })
    const back = mountCard(ownerA, FA)
    expect(back.field.value).toBe('A, typed on 4')
    expect(session(ownerA)?.editBase).toEqual({ value: 'Server A', revision: 4, source: 'server', start: 'Server A' })
    w.prksFlushPendingPrivateNotes(ownerA)
    await settle()
    expect(noteRows().map((r) => [r.payload.value, r.base_revision])).toEqual([['A, typed on 4', 4]])
  })

  it('a clean field shows another tab\'s acknowledged text, and the next edit is typed on it', async () => {
    installShell()
    const { ownerA } = mountPair()
    await openFolder(ownerA, FA)
    const { field } = mountCard(ownerA, FA)
    expect(field.value).toBe('Server A')
    const foreign = queue.foreign(FA, 'private_notes', 'From another tab', 4)
    queue.ack(foreign.op_id, 5)
    expect(field.value).toBe('From another tab')
    type(field, 'From another tab, and mine')
    expect(session(ownerA)?.editBase).toEqual({ value: 'From another tab', revision: 5, source: 'server', start: 'From another tab' })
    w.prksFlushPendingPrivateNotes(ownerA)
    await settle()
    expect(noteRows().map((r) => [r.payload.value, r.base_revision])).toEqual([['From another tab, and mine', 5]])
  })

  it('text typed before any base is known never takes a base that shows other text', async () => {
    installShell()
    const { ownerA } = mountPair()
    reads = { state: 'unavailable', body: 'server' }
    await openFolder(ownerA, FA)
    const { field, status } = mountCard(ownerA, FA)
    type(field, 'Typed offline')
    expect(session(ownerA)?.editBase).toEqual({ value: null, revision: null, source: 'unknown', start: 'Server A' })
    w.prksFlushPendingPrivateNotes(ownerA)
    await settle()
    expect(status.textContent).toBe('Reminders cannot be saved yet — open this folder while connected once')

    /* Another tab's save is acknowledged: the pane now knows a base, but not the one this text was typed on. */
    const foreign = queue.foreign(FA, 'private_notes', 'Elsewhere', 4)
    queue.ack(foreign.op_id, 5)
    expect(w.prksFolderNoteObserved(ownerA, FA)).toMatchObject({ value: 'Elsewhere', revision: 5 })
    type(field, 'Typed offline, still')
    w.prksFlushPendingPrivateNotes(ownerA)
    await settle()
    expect(noteRows()).toEqual([])
    expect(status.textContent).toBe('Reminders changed elsewhere — copy your text, then reopen this folder')
    expect(session(ownerA)?.draftText).toBe('Typed offline, still')
    expect(server[FA]!.private_notes).toBe('Elsewhere')
  })

  it('text typed before the base is read saves on it once it is, when it shows the same text', async () => {
    installShell()
    const { ownerA } = mountPair()
    reads = { state: 'unavailable', body: 'server' }
    await openFolder(ownerA, FA)
    const { field } = mountCard(ownerA, FA)
    type(field, 'Typed early')
    reads = { state: 'server', body: 'server' }
    await w.prksEnsureFolderNotesBase(ownerA, folder(FA))
    w.prksFlushPendingPrivateNotes(ownerA)
    await settle()
    expect(noteRows().map((r) => [r.payload.value, r.base_revision])).toEqual([['Typed early', 4]])
    expect(session(ownerA)?.editBase).toEqual({ value: 'Server A', revision: 4, source: 'server', start: 'Server A' })
  })
})
