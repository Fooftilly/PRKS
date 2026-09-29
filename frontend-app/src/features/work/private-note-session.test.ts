import { afterEach, beforeAll, describe, expect, it } from 'vitest'
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
  getResource: (name: string) => { dirty: boolean; textarea: HTMLTextAreaElement; entityId: string; key: string } | null
}

type SaveCall = { entityId: string; kind: string; content: string }

type PanelWindow = {
  eval: (code: string) => void
  prksWorkspaceSnapshot: () => { focusedTabId: string; mainTabId: string }
  prksMountTabContext: (tabId: string, host: HTMLElement) => void
  prksGetTabContext: (tabId: string) => WorkCtx
  prksDestroyAllTabContexts: () => void
  initPrksPrivateNotesEditor: (entityType: string, entityId: string, owner: WorkCtx) => void
  prksFlushPendingPrivateNotes: (owner: WorkCtx) => void
  prksResetPrivateNoteDraftsForTest: () => void
  prksPrivateNotesTextForEntity: (entityType: string, entityId: string, serverText: string) => string
  prksVuePresentWorkPrivateNotes: (owner: WorkCtx, workId: string) => boolean
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
  panelWindow.eval(tabContextSource)
  panelWindow.eval(uiSource)
  registerWorkPrivateNotesBridge(window)
})

afterEach(() => {
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
    installShell()
    const { ownerA } = mountPair()
    ownerA.setEntity('work', work('work-a', 'saved'))
    const notes = notesField(ownerA, 'work-a')
    const saves: SaveCall[] = []
    panelWindow.prksWorkNoteObserved = () => ({ value: 'saved', revision: 1 })
    panelWindow.prksRefreshPendingWorkNotes = () => Promise.resolve()
    panelWindow.prksSync = { subscribe: () => () => {} }
    panelWindow.prksSaveWorkNoteDurably = (entityId, kind, content) => {
      saves.push({ entityId: String(entityId), kind: String(kind), content: String(content) })
      return Promise.resolve({ code: saves.length === 1 ? 'scope_busy' : 'saved' })
    }
    panelWindow.initPrksPrivateNotesEditor('work', 'work-a', ownerA)
    notes.value = 'Remember later'
    notes.dispatchEvent(new Event('input', { bubbles: true }))
    panelWindow.prksFlushPendingPrivateNotes(ownerA)
    await new Promise((resolve) => { setTimeout(resolve, 20) })
    const editor = ownerA.getResource('privateNotesEditor')
    expect(saves).toEqual([{ entityId: 'work-a', kind: 'work-private-note', content: 'Remember later' }])
    expect(editor?.dirty).toBe(true)
    expect(editor?.key).toContain(ownerA.tabId)
    await new Promise((resolve) => { setTimeout(resolve, 450) })
    expect(saves).toEqual([
      { entityId: 'work-a', kind: 'work-private-note', content: 'Remember later' },
      { entityId: 'work-a', kind: 'work-private-note', content: 'Remember later' },
    ])
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
})
