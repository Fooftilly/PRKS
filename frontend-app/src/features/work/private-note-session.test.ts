import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import ownerResourceSource from '../../../../frontend/js/owner-resource.js?raw'
import tabContextSource from '../../../../frontend/js/tab-context.js?raw'
import uiSource from '../../../../frontend/js/ui.js?raw'
import { registerWorkPrivateNotesBridge, resetWorkPrivateNotesForTests } from './private-note-session'

type WorkRecord = {
  id: string
  title: string
  private_notes: string
  status: string
  doc_type: string
}

type NoteSession = {
  workId: string
  draftText: string
  dirty: boolean
  state: string
  retired: boolean
  ownerTabId: string
  ownerGeneration?: number
}

type WorkCtx = {
  tabId: string
  generation: number
  mounted: boolean
  destroyed: boolean
  ui: {
    rightPanelTab: string
    workDetailsMode: string
    workMetaDraft: Record<string, string> | null
    workPrivateNoteSession: NoteSession | null
  }
  setEntity: (type: string, value: WorkRecord) => void
  getEntity: (type: string) => WorkRecord | null
  getResource: (name: string) => {
    dirty: boolean
    textarea: HTMLTextAreaElement
    entityId: string
    key: string
    generation: number
  } | null
  beginRoute: (route?: unknown) => number
  suspend: (host: HTMLElement) => boolean
  resume: (host: HTMLElement) => boolean
  unmount: (reason?: string) => void
  timers: Map<string, unknown>
  resourceTicket: () => unknown
}

type SaveCall = { entityId: string; kind: string; content: string }

type PanelWindow = {
  eval: (code: string) => void
  prksWorkspaceSnapshot: () => { focusedTabId: string; mainTabId: string }
  prksMountTabContext: (tabId: string, host: HTMLElement) => void
  prksGetTabContext: (tabId: string) => WorkCtx
  prksDestroyAllTabContexts: () => void
  initPrksPrivateNotesEditor: (entityType: string, entityId: string, owner: WorkCtx) => void
  prksBindPrivateNotesField: (entityType: string, entityId: string, owner: WorkCtx) => void
  prksFlushPendingPrivateNotes: (owner: WorkCtx) => void
  prksResetPrivateNoteDraftsForTest: () => void
  prksPrivateNotesTextForEntity: (entityType: string, entityId: string, serverText: string) => string
  prksVuePresentWorkPrivateNotes: (owner: WorkCtx, workId: string) => boolean
  prksVueDismissWorkPrivateNotes: () => void
  prksPublishWorkPanelRead: (owner: WorkCtx, work: WorkRecord) => void
  prksSaveWorkNoteDurably: (
    entityId: string,
    kind: string,
    content: string,
  ) => Promise<{ code: string }>
  prksWorkNoteObserved: () => { value: string; revision: number }
  prksRefreshPendingWorkNotes: () => Promise<void>
  prksSync?: { subscribe: (listener: () => void) => () => void }
}

const panelWindow = window as unknown as PanelWindow

function work(id = 'work-a', notes = ''): WorkRecord {
  return { id, title: 'Alpha', private_notes: notes, status: 'Not Started', doc_type: 'article' }
}

function installShell() {
  document.body.innerHTML = `
    <div id="panel-content"></div>
    <div id="host-a"></div>
    <div id="host-b"></div>`
}

function mountPair(focused = 'tab-a') {
  const workspace = { focusedTabId: focused, mainTabId: 'tab-a' }
  panelWindow.prksWorkspaceSnapshot = () => workspace
  const hostA = document.getElementById('host-a')
  const hostB = document.getElementById('host-b')
  if (!hostA || !hostB) throw new Error('hosts missing')
  panelWindow.prksMountTabContext('tab-a', hostA)
  panelWindow.prksMountTabContext('tab-b', hostB)
  return {
    workspace,
    ownerA: panelWindow.prksGetTabContext('tab-a'),
    ownerB: panelWindow.prksGetTabContext('tab-b'),
  }
}

function ownPanel(owner: WorkCtx) {
  const panel = document.getElementById('panel-content')
  if (!panel) throw new Error('panel missing')
  panel.dataset.prksOwnerTabId = owner.tabId
  panel.dataset.prksOwnerGeneration = String(owner.generation)
  return panel
}

async function flushMicrotasks() {
  for (let i = 0; i < 8; i += 1) await Promise.resolve()
}

function advanceGeneration(owner: WorkCtx) {
  owner.beginRoute()
  const panel = document.getElementById('panel-content')
  if (panel) panel.dataset.prksOwnerGeneration = String(owner.generation)
}

function noteHolds(owner: WorkCtx): Record<string, string> | null {
  const ui = owner.ui as WorkCtx['ui'] & { workPrivateNoteHolds?: Record<string, string> | null }
  return ui.workPrivateNoteHolds || null
}

function beginReminderSave(serverNotes: string, nextCode: (saveCount: number) => { code: string } | 'pending') {
  installShell()
  const pair = mountPair()
  pair.ownerA.setEntity('work', work('work-a', serverNotes))
  const notes = notesField(pair.ownerA, 'work-a')
  const saves: SaveCall[] = []
  let releaseSave: (result: { code: string }) => void = () => {}
  const pending = new Promise<{ code: string }>((resolve) => {
    releaseSave = resolve
  })
  panelWindow.prksWorkNoteObserved = () => ({ value: serverNotes, revision: 1 })
  panelWindow.prksRefreshPendingWorkNotes = () => Promise.resolve()
  panelWindow.prksSync = { subscribe: () => () => {} }
  panelWindow.prksSaveWorkNoteDurably = (entityId, kind, content) => {
    saves.push({ entityId: String(entityId), kind: String(kind), content: String(content) })
    const next = nextCode(saves.length)
    return next === 'pending' ? pending : Promise.resolve(next)
  }
  panelWindow.initPrksPrivateNotesEditor('work', 'work-a', pair.ownerA)
  return { ...pair, notes, saves, releaseSave, pending }
}

function notesField(owner: WorkCtx, workId: string) {
  const panel = ownPanel(owner)
  const notes = document.createElement('textarea')
  notes.id = `prks-private-notes-${'work'}-${workId}`
  notes.className = 'prks-private-notes-input'
  const status = document.createElement('p')
  status.id = `prks-private-notes-status-work-${workId}`
  panel.replaceChildren(notes, status)
  return notes
}

beforeAll(() => {
  panelWindow.eval(ownerResourceSource)
  panelWindow.eval(tabContextSource)
  panelWindow.eval(uiSource)
  registerWorkPrivateNotesBridge(window)
})

afterEach(() => {
  vi.useRealTimers()
  resetWorkPrivateNotesForTests()
  panelWindow.prksResetPrivateNoteDraftsForTest()
  panelWindow.prksDestroyAllTabContexts()
  document.body.innerHTML = ''
})

describe('work private notes session', () => {
  it('keeps Main and Secondary drafts apart for the same Work', async () => {
    installShell()
    const { workspace, ownerA, ownerB } = mountPair()
    const recordA = work('work-a', '')
    const recordB = work('work-a', '')
    ownerA.setEntity('work', recordA)
    ownerB.setEntity('work', recordB)
    ownerA.ui.rightPanelTab = 'details'
    ownerB.ui.rightPanelTab = 'details'
    const notesA = notesField(ownerA, 'work-a')
    panelWindow.prksWorkNoteObserved = () => ({ value: '', revision: 1 })
    panelWindow.prksRefreshPendingWorkNotes = () => Promise.resolve()
    panelWindow.initPrksPrivateNotesEditor('work', 'work-a', ownerA)
    notesA.value = 'Remember A'
    notesA.dispatchEvent(new Event('input', { bubbles: true }))
    expect(panelWindow.prksPrivateNotesTextForEntity('work', 'work-a', '')).toBe('Remember A')

    workspace.focusedTabId = 'tab-b'
    expect(panelWindow.prksPrivateNotesTextForEntity('work', 'work-a', '')).toBe('')
    expect(ownerA.ui.workPrivateNoteSession?.draftText).toBe('Remember A')

    const saves: SaveCall[] = []
    let releaseSave: (result: { code: string }) => void = () => {}
    const pending = new Promise<{ code: string }>((resolve) => {
      releaseSave = resolve
    })
    panelWindow.prksSaveWorkNoteDurably = (entityId, kind, content) => {
      saves.push({ entityId: String(entityId), kind: String(kind), content: String(content) })
      return pending
    }
    panelWindow.prksFlushPendingPrivateNotes(ownerA)
    expect(saves).toEqual([{ entityId: 'work-a', kind: 'work-private-note', content: 'Remember A' }])

    const notesB = notesField(ownerB, 'work-a')
    panelWindow.initPrksPrivateNotesEditor('work', 'work-a', ownerB)
    expect(notesB.value).toBe('')
    expect(ownerB.ui.workPrivateNoteSession?.draftText).toBe('')
    notesB.value = 'Remember B'
    notesB.dispatchEvent(new Event('input', { bubbles: true }))
    expect(notesB.value).toBe('Remember B')
    expect(ownerB.ui.workPrivateNoteSession?.draftText).toBe('Remember B')
    expect(ownerA.ui.workPrivateNoteSession?.draftText).toBe('Remember A')
    expect(ownerA.ui.workPrivateNoteSession?.ownerTabId).not.toBe(ownerB.ui.workPrivateNoteSession?.ownerTabId)

    releaseSave({ code: 'saved' })
    await pending
    await new Promise((resolve) => { setTimeout(resolve, 0) })
    expect(saves).toHaveLength(1)
    expect(notesB.value).toBe('Remember B')
    expect(recordB.private_notes).toBe('')
  })

  it('retries scope_busy on the editor that started the save', async () => {
    vi.useFakeTimers()
    const { ownerA, notes, saves } = beginReminderSave('saved', (count) => (
      { code: count === 1 ? 'scope_busy' : 'saved' }
    ))
    notes.value = 'Remember later'
    notes.dispatchEvent(new Event('input', { bubbles: true }))
    panelWindow.prksFlushPendingPrivateNotes(ownerA)
    await flushMicrotasks()
    const editor = ownerA.getResource('privateNotesEditor')
    expect(saves).toEqual([{ entityId: 'work-a', kind: 'work-private-note', content: 'Remember later' }])
    expect(editor?.dirty).toBe(true)
    expect(editor?.key).toContain(ownerA.tabId)
    await vi.advanceTimersByTimeAsync(400)
    await flushMicrotasks()
    expect(saves).toEqual([
      { entityId: 'work-a', kind: 'work-private-note', content: 'Remember later' },
      { entityId: 'work-a', kind: 'work-private-note', content: 'Remember later' },
    ])
  })

  it('retries scope_busy through a same-generation replacement editor', async () => {
    vi.useFakeTimers()
    const { ownerA, notes, saves, releaseSave } = beginReminderSave('saved', (count) => (
      count === 1 ? 'pending' : { code: 'saved' }
    ))
    notes.value = 'Remember later'
    notes.dispatchEvent(new Event('input', { bubbles: true }))
    panelWindow.prksFlushPendingPrivateNotes(ownerA)
    await flushMicrotasks()
    expect(saves).toEqual([{ entityId: 'work-a', kind: 'work-private-note', content: 'Remember later' }])
    const started = ownerA.getResource('privateNotesEditor')
    const replacementNotes = notesField(ownerA, 'work-a')
    panelWindow.initPrksPrivateNotesEditor('work', 'work-a', ownerA)
    const replacement = ownerA.getResource('privateNotesEditor')
    expect(replacement).toBeTruthy()
    expect(replacement).not.toBe(started)
    expect(replacement?.generation).toBe(ownerA.generation)
    expect(replacementNotes.value).toBe('Remember later')
    releaseSave({ code: 'scope_busy' })
    await flushMicrotasks()
    expect(ownerA.ui.workPrivateNoteSession?.dirty).toBe(true)
    await vi.advanceTimersByTimeAsync(400)
    await flushMicrotasks()
    expect(saves).toEqual([
      { entityId: 'work-a', kind: 'work-private-note', content: 'Remember later' },
      { entityId: 'work-a', kind: 'work-private-note', content: 'Remember later' },
    ])
    expect(ownerA.getResource('privateNotesEditor')).toBe(replacement)
    expect(ownerA.ui.workPrivateNoteSession?.dirty).toBe(false)
  })

  it('does not retry scope_busy into a newer generation', async () => {
    vi.useFakeTimers()
    const { ownerA, notes, saves, releaseSave } = beginReminderSave('saved', () => 'pending')
    notes.value = 'Stay on the old generation'
    notes.dispatchEvent(new Event('input', { bubbles: true }))
    panelWindow.prksFlushPendingPrivateNotes(ownerA)
    await flushMicrotasks()
    const startedGeneration = ownerA.generation
    advanceGeneration(ownerA)
    ownerA.setEntity('work', work('work-a', 'saved'))
    const rebound = notesField(ownerA, 'work-a')
    panelWindow.initPrksPrivateNotesEditor('work', 'work-a', ownerA)
    const next = ownerA.getResource('privateNotesEditor')
    expect(next?.generation).toBe(ownerA.generation)
    expect(next?.generation).not.toBe(startedGeneration)
    expect(rebound.value).toBe('Stay on the old generation')
    releaseSave({ code: 'scope_busy' })
    await flushMicrotasks()
    await vi.advanceTimersByTimeAsync(400)
    await flushMicrotasks()
    expect(saves).toHaveLength(1)
    expect(rebound.value).toBe('Stay on the old generation')
    expect(ownerA.ui.workPrivateNoteSession?.draftText).toBe('Stay on the old generation')
    expect(ownerA.ui.workPrivateNoteSession?.ownerGeneration).toBe(ownerA.generation)
  })

  it('does not mark metadata dirty when a reminder commits', async () => {
    installShell()
    const { ownerA } = mountPair()
    ownerA.setEntity('work', work('work-a', 'saved'))
    const notes = notesField(ownerA, 'work-a')
    panelWindow.prksWorkNoteObserved = () => ({ value: 'saved', revision: 1 })
    panelWindow.prksRefreshPendingWorkNotes = () => Promise.resolve()
    panelWindow.prksSaveWorkNoteDurably = () => Promise.resolve({ code: 'saved' })
    panelWindow.initPrksPrivateNotesEditor('work', 'work-a', ownerA)
    notes.value = 'Committed reminder'
    notes.dispatchEvent(new Event('input', { bubbles: true }))
    panelWindow.prksFlushPendingPrivateNotes(ownerA)
    await new Promise((resolve) => { setTimeout(resolve, 0) })
    expect(ownerA.ui.workMetaDraft).toBeNull()
    expect(ownerA.ui.workPrivateNoteSession?.dirty).toBe(false)
    expect(ownerA.ui.workPrivateNoteSession?.state).toBe('committed')
  })

  it('paints one Reminders card for the panel owner', () => {
    installShell()
    const { ownerA, ownerB } = mountPair()
    ownerA.setEntity('work', work('work-a', 'Keep A'))
    ownerB.setEntity('work', work('work-a', 'Other B'))
    const panel = ownPanel(ownerA)
    const anchor = document.createElement('div')
    anchor.dataset.prksRole = 'work-private-notes-anchor'
    anchor.innerHTML = '<textarea id="prks-private-notes-work-work-a" class="prks-private-notes-input">shell</textarea>'
    panel.appendChild(anchor)
    expect(panelWindow.prksVuePresentWorkPrivateNotes(ownerA, 'work-a')).toBe(true)
    const fields = panel.querySelectorAll('#prks-private-notes-work-work-a')
    expect(fields).toHaveLength(1)
    expect((fields[0] as HTMLTextAreaElement).value).toBe('Keep A')
    expect(panel.querySelector('[data-prks-hint-type="notes-private-file"]')?.getAttribute('aria-label')).toBe('About reminders')
    expect(panelWindow.prksVuePresentWorkPrivateNotes(ownerB, 'work-a')).toBe(false)
    expect(panel.querySelectorAll('#prks-private-notes-work-work-a')).toHaveLength(1)
    expect((panel.querySelector('#prks-private-notes-work-work-a') as HTMLTextAreaElement).value).toBe('Keep A')
  })

  it('binds a new Reminders editor when the owner generation changes', async () => {
    installShell()
    const { ownerA } = mountPair()
    ownerA.setEntity('work', work('work-a', ''))
    const panel = ownPanel(ownerA)
    const anchor = document.createElement('div')
    anchor.dataset.prksRole = 'work-private-notes-anchor'
    panel.appendChild(anchor)
    const saves: SaveCall[] = []
    panelWindow.prksWorkNoteObserved = () => ({ value: '', revision: 1 })
    panelWindow.prksRefreshPendingWorkNotes = () => Promise.resolve()
    panelWindow.prksSaveWorkNoteDurably = (entityId, kind, content) => {
      saves.push({ entityId: String(entityId), kind: String(kind), content: String(content) })
      return Promise.resolve({ code: 'saved' })
    }
    expect(panelWindow.prksVuePresentWorkPrivateNotes(ownerA, 'work-a')).toBe(true)
    const first = ownerA.getResource('privateNotesEditor')
    const started = ownerA.generation
    expect(first?.generation).toBe(started)
    const surviving = panel.querySelector('[data-prks-role="work-private-notes-anchor"]')
    advanceGeneration(ownerA)
    ownerA.setEntity('work', work('work-a', ''))
    panel.dataset.prksOwnerTabId = ownerA.tabId
    panel.dataset.prksOwnerGeneration = String(ownerA.generation)
    expect(panel.querySelector('[data-prks-role="work-private-notes-anchor"]')).toBe(surviving)
    expect(panelWindow.prksVuePresentWorkPrivateNotes(ownerA, 'work-a')).toBe(true)
    const second = ownerA.getResource('privateNotesEditor')
    expect(second).toBeTruthy()
    expect(second).not.toBe(first)
    expect(second?.generation).toBe(ownerA.generation)
    expect(second?.generation).not.toBe(started)
    const field = panel.querySelector('#prks-private-notes-work-work-a') as HTMLTextAreaElement
    field.value = 'New generation only'
    field.dispatchEvent(new Event('input', { bubbles: true }))
    panelWindow.prksFlushPendingPrivateNotes(ownerA)
    await flushMicrotasks()
    expect(saves).toEqual([{ entityId: 'work-a', kind: 'work-private-note', content: 'New generation only' }])
    expect(ownerA.getResource('privateNotesEditor')).toBe(second)
  })

  it('unmounts Reminders when the anchor is already detached', () => {
    installShell()
    const { ownerA } = mountPair()
    ownerA.setEntity('work', work('work-a', 'Keep'))
    const panel = ownPanel(ownerA)
    const anchor = document.createElement('div')
    anchor.dataset.prksRole = 'work-private-notes-anchor'
    panel.appendChild(anchor)
    expect(panelWindow.prksVuePresentWorkPrivateNotes(ownerA, 'work-a')).toBe(true)
    expect(anchor.querySelector('textarea')).toBeTruthy()
    panel.removeChild(anchor)
    expect(anchor.isConnected).toBe(false)
    panelWindow.prksVueDismissWorkPrivateNotes()
    panel.appendChild(anchor)
    expect(anchor.querySelector('textarea')).toBeNull()
  })

  it('keeps the Reminders card when people mode publishes the panel', () => {
    installShell()
    const { ownerA } = mountPair()
    const record = work('work-a', 'Keep people')
    ownerA.setEntity('work', record)
    ownerA.ui.workDetailsMode = 'people'
    const panel = ownPanel(ownerA)
    const anchor = document.createElement('div')
    anchor.dataset.prksRole = 'work-private-notes-anchor'
    panel.appendChild(anchor)
    expect(panelWindow.prksVuePresentWorkPrivateNotes(ownerA, 'work-a')).toBe(true)
    const field = panel.querySelector('#prks-private-notes-work-work-a') as HTMLTextAreaElement
    const editor = ownerA.getResource('privateNotesEditor')
    expect(field).toBeTruthy()
    expect(editor?.textarea).toBe(field)
    panelWindow.prksPublishWorkPanelRead(ownerA, record)
    expect(panel.querySelector('#prks-private-notes-work-work-a')).toBe(field)
    expect(field.isConnected).toBe(true)
    expect(ownerA.getResource('privateNotesEditor')?.textarea).toBe(field)
  })

  it('keeps an unsaved draft when leaving replaces the session before the save fails', async () => {
    const { workspace, ownerA, ownerB, notes, saves, releaseSave, pending } = beginReminderSave('server', () => 'pending')
    notes.value = 'Do not drop'
    notes.dispatchEvent(new Event('input', { bubbles: true }))
    panelWindow.prksFlushPendingPrivateNotes(ownerA)
    await flushMicrotasks()
    expect(saves).toHaveLength(1)
    const startedGeneration = ownerA.generation

    advanceGeneration(ownerA)
    ownerA.setEntity('work', work('work-b', ''))
    notesField(ownerA, 'work-b')
    panelWindow.initPrksPrivateNotesEditor('work', 'work-b', ownerA)
    expect(ownerA.ui.workPrivateNoteSession?.workId).toBe('work-b')
    expect(ownerA.ui.workPrivateNoteSession?.draftText).toBe('')

    releaseSave({ code: 'unavailable' })
    await pending
    await flushMicrotasks()

    advanceGeneration(ownerA)
    ownerA.setEntity('work', work('work-a', 'server'))
    const returned = notesField(ownerA, 'work-a')
    panelWindow.initPrksPrivateNotesEditor('work', 'work-a', ownerA)
    expect(returned.value).toBe('Do not drop')
    expect(ownerA.ui.workPrivateNoteSession?.draftText).toBe('Do not drop')
    expect(ownerA.ui.workPrivateNoteSession?.ownerGeneration).toBe(ownerA.generation)
    expect(ownerA.ui.workPrivateNoteSession?.ownerGeneration).not.toBe(startedGeneration)

    workspace.focusedTabId = 'tab-b'
    ownerB.setEntity('work', work('work-a', 'server'))
    const notesB = notesField(ownerB, 'work-a')
    panelWindow.initPrksPrivateNotesEditor('work', 'work-a', ownerB)
    expect(notesB.value).not.toBe('Do not drop')
    expect(ownerB.ui.workPrivateNoteSession?.draftText).not.toBe('Do not drop')
    expect(ownerA.ui.workPrivateNoteSession?.draftText).toBe('Do not drop')
  })

  it('does not flush a reminder again after the in-flight save succeeds', async () => {
    const held = beginReminderSave('server', () => 'pending')
    held.notes.value = 'Already saved'
    held.notes.dispatchEvent(new Event('input', { bubbles: true }))
    panelWindow.prksFlushPendingPrivateNotes(held.ownerA)
    await flushMicrotasks()
    advanceGeneration(held.ownerA)
    held.ownerA.setEntity('work', work('work-b', ''))
    notesField(held.ownerA, 'work-b')
    panelWindow.initPrksPrivateNotesEditor('work', 'work-b', held.ownerA)
    expect(noteHolds(held.ownerA)?.['work-a']).toBe('Already saved')
    held.releaseSave({ code: 'saved' })
    await held.pending
    await flushMicrotasks()
    expect(noteHolds(held.ownerA)?.['work-a']).toBeUndefined()
    advanceGeneration(held.ownerA)
    held.ownerA.setEntity('work', work('work-a', 'server'))
    const returned = notesField(held.ownerA, 'work-a')
    panelWindow.initPrksPrivateNotesEditor('work', 'work-a', held.ownerA)
    expect(returned.value).not.toBe('Already saved')
    expect(held.ownerA.ui.workPrivateNoteSession?.dirty).toBe(false)
    panelWindow.prksFlushPendingPrivateNotes(held.ownerA)
    await flushMicrotasks()
    expect(held.saves).toHaveLength(1)

    resetWorkPrivateNotesForTests()
    panelWindow.prksResetPrivateNoteDraftsForTest()
    panelWindow.prksDestroyAllTabContexts()
    const carried = beginReminderSave('server', () => 'pending')
    carried.notes.value = 'Already saved'
    carried.notes.dispatchEvent(new Event('input', { bubbles: true }))
    panelWindow.prksFlushPendingPrivateNotes(carried.ownerA)
    await flushMicrotasks()
    advanceGeneration(carried.ownerA)
    carried.ownerA.setEntity('work', work('work-a', 'server'))
    const field = notesField(carried.ownerA, 'work-a')
    panelWindow.initPrksPrivateNotesEditor('work', 'work-a', carried.ownerA)
    expect(field.value).toBe('Already saved')
    expect(carried.ownerA.ui.workPrivateNoteSession?.dirty).toBe(true)
    expect(carried.ownerA.ui.workPrivateNoteSession?.state).toBe('drafting')
    carried.releaseSave({ code: 'saved' })
    await carried.pending
    await flushMicrotasks()
    expect(carried.ownerA.ui.workPrivateNoteSession?.dirty).toBe(false)
    expect(carried.ownerA.ui.workPrivateNoteSession?.state).toBe('committed')
    expect(carried.ownerA.ui.workPrivateNoteSession?.draftText).toBe('Already saved')
    expect(carried.ownerA.getResource('privateNotesEditor')?.dirty).toBe(false)
    panelWindow.prksFlushPendingPrivateNotes(carried.ownerA)
    await flushMicrotasks()
    expect(carried.saves).toHaveLength(1)
  })
})

describe('work private notes owner lifetime', () => {
  function bound(serverNotes = '') {
    installShell()
    const pair = mountPair()
    pair.ownerA.setEntity('work', work('work-a', serverNotes))
    const notes = notesField(pair.ownerA, 'work-a')
    const saves: SaveCall[] = []
    panelWindow.prksWorkNoteObserved = () => ({ value: serverNotes, revision: 1 })
    panelWindow.prksRefreshPendingWorkNotes = () => Promise.resolve()
    panelWindow.prksSync = { subscribe: () => () => {} }
    panelWindow.prksSaveWorkNoteDurably = (entityId, kind, content) => {
      saves.push({ entityId: String(entityId), kind: String(kind), content: String(content) })
      return Promise.resolve({ code: 'saved' })
    }
    panelWindow.initPrksPrivateNotesEditor('work', 'work-a', pair.ownerA)
    return { ...pair, notes, saves }
  }

  function parking(): HTMLElement {
    const host = document.createElement('div')
    document.body.appendChild(host)
    return host
  }

  it('warm park disposes the Reminders editor and the rebuilt editor keeps its draft', async () => {
    const { ownerA, notes, saves } = bound()
    const started = ownerA.getResource('privateNotesEditor')
    notes.value = 'Unflushed'
    notes.dispatchEvent(new Event('input', { bubbles: true }))
    expect(ownerA.timers.size).toBe(1)

    expect(ownerA.suspend(parking())).toBe(true)
    expect(ownerA.getResource('privateNotesEditor')).toBeUndefined()
    expect(ownerA.timers.size).toBe(0)
    notes.value = 'Typed into the parked field'
    notes.dispatchEvent(new Event('input', { bubbles: true }))
    expect(ownerA.ui.workPrivateNoteSession?.draftText).toBe('Unflushed')
    expect(ownerA.ui.workPrivateNoteSession?.dirty).toBe(true)

    expect(ownerA.resume(parking())).toBe(true)
    const field = notesField(ownerA, 'work-a')
    panelWindow.initPrksPrivateNotesEditor('work', 'work-a', ownerA)
    const rebuilt = ownerA.getResource('privateNotesEditor')
    expect(rebuilt).toBeTruthy()
    expect(rebuilt).not.toBe(started)
    expect(field.value).toBe('Unflushed')
    expect(rebuilt?.dirty).toBe(true)
    panelWindow.prksFlushPendingPrivateNotes(ownerA)
    await flushMicrotasks()
    expect(saves).toEqual([{ entityId: 'work-a', kind: 'work-private-note', content: 'Unflushed' }])
  })

  it('cold park disposes the Reminders editor and its debounce once', async () => {
    const { ownerA, notes, saves } = bound()
    notes.value = 'Gone with the route'
    notes.dispatchEvent(new Event('input', { bubbles: true }))
    ownerA.unmount('park')
    expect(ownerA.getResource('privateNotesEditor')).toBeUndefined()
    notes.dispatchEvent(new Event('blur'))
    await flushMicrotasks()
    expect(saves).toEqual([])
  })

  it('binds nothing for a warm-parked owner', () => {
    installShell()
    const { ownerA } = mountPair()
    ownerA.setEntity('work', work('work-a', ''))
    const field = notesField(ownerA, 'work-a')
    expect(ownerA.suspend(parking())).toBe(true)
    panelWindow.prksBindPrivateNotesField('work', 'work-a', ownerA)
    expect(ownerA.getResource('privateNotesEditor')).toBeUndefined()
    expect(field.dataset.prksNotesBound).toBeUndefined()
  })

  it('keeps Main and Secondary Reminders sessions apart across a warm park', () => {
    const { workspace, ownerA, ownerB } = bound()
    ownerB.setEntity('work', work('work-a', ''))
    workspace.focusedTabId = 'tab-b'
    notesField(ownerB, 'work-a')
    panelWindow.initPrksPrivateNotesEditor('work', 'work-a', ownerB)
    const sideEditor = ownerB.getResource('privateNotesEditor')
    expect(sideEditor).toBeTruthy()
    expect(ownerA.suspend(parking())).toBe(true)
    expect(ownerA.getResource('privateNotesEditor')).toBeUndefined()
    expect(ownerB.getResource('privateNotesEditor')).toBe(sideEditor)
  })

  it('does not paint a stale same-generation editor failure into the replacement', async () => {
    const { ownerA, notes } = bound('saved')
    let rejectSave: (reason?: unknown) => void = () => {}
    panelWindow.prksSaveWorkNoteDurably = () => new Promise((_resolve, reject) => {
      rejectSave = reject
    })
    notes.value = 'Will fail'
    notes.dispatchEvent(new Event('input', { bubbles: true }))
    panelWindow.prksFlushPendingPrivateNotes(ownerA)
    await flushMicrotasks()
    const started = ownerA.getResource('privateNotesEditor')
    const panel = document.getElementById('panel-content') as HTMLElement
    const status = panel.querySelector('#prks-private-notes-status-work-work-a') as HTMLElement
    const replacementField = document.createElement('textarea')
    replacementField.id = 'prks-private-notes-work-work-a'
    panel.replaceChild(replacementField, notes)
    panelWindow.initPrksPrivateNotesEditor('work', 'work-a', ownerA)
    const replacement = ownerA.getResource('privateNotesEditor')
    expect(replacement).not.toBe(started)
    status.textContent = 'Replacement status'
    rejectSave(new Error('store refused'))
    await flushMicrotasks()
    expect(status.textContent).toBe('Replacement status')
    expect(ownerA.getResource('privateNotesEditor')).toBe(replacement)
  })

  it('flushes a draft left dirty after the editor was parked away', async () => {
    const { ownerA, notes, saves } = bound('saved')
    let release: (result: { code: string }) => void = () => {}
    panelWindow.prksSaveWorkNoteDurably = (entityId, kind, content) => {
      saves.push({ entityId: String(entityId), kind: String(kind), content: String(content) })
      if (saves.length > 1) return Promise.resolve({ code: 'saved' })
      return new Promise((resolve) => { release = resolve })
    }
    notes.value = 'Busy while parking'
    notes.dispatchEvent(new Event('input', { bubbles: true }))
    panelWindow.prksFlushPendingPrivateNotes(ownerA)
    await flushMicrotasks()
    expect(ownerA.suspend(parking())).toBe(true)
    expect(ownerA.getResource('privateNotesEditor')).toBeUndefined()
    release({ code: 'scope_busy' })
    await flushMicrotasks()
    expect(ownerA.ui.workPrivateNoteSession?.dirty).toBe(true)
    panelWindow.prksFlushPendingPrivateNotes(ownerA)
    await flushMicrotasks()
    expect(saves).toEqual([
      { entityId: 'work-a', kind: 'work-private-note', content: 'Busy while parking' },
      { entityId: 'work-a', kind: 'work-private-note', content: 'Busy while parking' },
    ])
    expect(ownerA.ui.workPrivateNoteSession?.dirty).toBe(false)
    expect(ownerA.ui.workPrivateNoteSession?.state).toBe('committed')
    panelWindow.prksFlushPendingPrivateNotes(ownerA)
    await flushMicrotasks()
    expect(saves).toHaveLength(2)
  })

  it('retries a scope_busy Reminders save while the owner is warm-parked', async () => {
    vi.useFakeTimers()
    const { ownerA, notes, saves } = bound('saved')
    let release: (result: { code: string }) => void = () => {}
    panelWindow.prksSaveWorkNoteDurably = (entityId, kind, content) => {
      saves.push({ entityId: String(entityId), kind: String(kind), content: String(content) })
      if (saves.length > 1) return Promise.resolve({ code: 'saved' })
      return new Promise((resolve) => { release = resolve })
    }
    notes.value = 'Retry while parked'
    notes.dispatchEvent(new Event('input', { bubbles: true }))
    panelWindow.prksFlushPendingPrivateNotes(ownerA)
    await flushMicrotasks()
    expect(ownerA.suspend(parking())).toBe(true)
    release({ code: 'scope_busy' })
    await flushMicrotasks()
    expect(ownerA.ui.workPrivateNoteSession?.dirty).toBe(true)
    await vi.advanceTimersByTimeAsync(400)
    await flushMicrotasks()
    expect(saves).toEqual([
      { entityId: 'work-a', kind: 'work-private-note', content: 'Retry while parked' },
      { entityId: 'work-a', kind: 'work-private-note', content: 'Retry while parked' },
    ])
    expect(ownerA.ui.workPrivateNoteSession?.dirty).toBe(false)
    expect(ownerA.getResource('privateNotesEditor')).toBeUndefined()
  })

  function busyRetryTimers(owner: WorkCtx): string[] {
    return [...owner.timers.keys()].filter((key) => key.startsWith('privateNotesBusyRetry:'))
  }

  function countingSync() {
    const listeners = new Set<() => void>()
    panelWindow.prksSync = {
      subscribe(listener: () => void) {
        listeners.add(listener)
        return () => { listeners.delete(listener) }
      },
    }
    return listeners
  }

  // Dirty draft, warm park disposes the editor, the session saver flushes it,
  // and the save settles scope_busy: the retry is scheduled for no live editor.
  async function busyRetryFromSessionSaver() {
    const pair = bound('saved')
    const listeners = countingSync()
    const saves: SaveCall[] = []
    let release: (result: { code: string }) => void = () => {}
    panelWindow.prksSaveWorkNoteDurably = (entityId, kind, content) => {
      saves.push({ entityId: String(entityId), kind: String(kind), content: String(content) })
      if (saves.length > 1) return Promise.resolve({ code: 'saved' })
      return new Promise((resolve) => { release = resolve })
    }
    pair.notes.value = 'Busy from the session'
    pair.notes.dispatchEvent(new Event('input', { bubbles: true }))
    expect(pair.ownerA.suspend(parking())).toBe(true)
    expect(pair.ownerA.getResource('privateNotesEditor')).toBeUndefined()
    panelWindow.prksFlushPendingPrivateNotes(pair.ownerA)
    await flushMicrotasks()
    expect(saves).toHaveLength(1)
    release({ code: 'scope_busy' })
    await flushMicrotasks()
    expect(listeners.size).toBe(1)
    expect(busyRetryTimers(pair.ownerA)).toHaveLength(1)
    return { ...pair, listeners, saves }
  }

  it('stops the session-saver busy retry on cold route without a sync event', async () => {
    vi.useFakeTimers()
    const { ownerA, listeners, saves } = await busyRetryFromSessionSaver()
    ownerA.unmount('park')
    expect(listeners.size).toBe(0)
    expect(busyRetryTimers(ownerA)).toEqual([])
    await vi.advanceTimersByTimeAsync(1000)
    expect(saves).toHaveLength(1)
  })

  it('stops the session-saver busy retry when the owner is destroyed', async () => {
    vi.useFakeTimers()
    const { ownerA, listeners, saves } = await busyRetryFromSessionSaver()
    panelWindow.prksDestroyAllTabContexts()
    expect(ownerA.destroyed).toBe(true)
    expect(listeners.size).toBe(0)
    expect(busyRetryTimers(ownerA)).toEqual([])
    await vi.advanceTimersByTimeAsync(1000)
    expect(saves).toHaveLength(1)
  })

  it('stops the busy retry listener when its timer fires', async () => {
    vi.useFakeTimers()
    const { ownerA, listeners, saves } = await busyRetryFromSessionSaver()
    await vi.advanceTimersByTimeAsync(400)
    await flushMicrotasks()
    expect(listeners.size).toBe(0)
    expect(busyRetryTimers(ownerA)).toEqual([])
    expect(saves).toHaveLength(2)
    expect(ownerA.ui.workPrivateNoteSession?.dirty).toBe(false)
  })
})

