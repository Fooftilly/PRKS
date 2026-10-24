import { afterEach, beforeAll, describe, expect, it } from 'vitest'
import ownerResourceSource from '../../../../frontend/js/owner-resource.js?raw'
import tabContextSource from '../../../../frontend/js/tab-context.js?raw'
import uiSource from '../../../../frontend/js/ui.js?raw'

type WorkRecord = {
  id: string
  title: string
  private_notes: string
  status: string
  doc_type: string
}

type WorkCtx = {
  tabId: string
  generation: number
  mounted: boolean
  destroyed: boolean
  ui: { rightPanelTab: string; workDetailsMode: string }
  setEntity: (type: string, value: WorkRecord) => void
  getResource: (name: string) => { dirty: boolean; textarea: HTMLTextAreaElement } | null
}

type PanelWindow = {
  eval: (code: string) => void
  prksWorkspaceSnapshot: () => { focusedTabId: string; mainTabId: string }
  prksMountTabContext: (tabId: string, host: HTMLElement) => void
  prksGetTabContext: (tabId: string) => WorkCtx
  prksDestroyAllTabContexts: () => void
  prksRefreshFocusedRightPanel: () => void
  initPrksPrivateNotesEditor: (entityType: string, entityId: string, owner: WorkCtx) => void
  prksFlushPendingPrivateNotes: (owner: WorkCtx) => void
  prksResetPrivateNoteDraftsForTest: () => void
  prksPrivateNotesTextForEntity: (entityType: string, entityId: string, serverText: string) => string
  prksSaveWorkNoteDurably: (
    entityId: string,
    kind: string,
    content: string,
    observed: { value: string; revision: number } | null,
  ) => Promise<{ code: string }>
  prksWorkNoteObserved: () => { value: string; revision: number }
  prksRefreshPendingWorkNotes: () => Promise<void>
  prksPendingWorkNoteText: (workId: string, kind: string, fallback: string) => string
}

const panelWindow = window as unknown as PanelWindow

beforeAll(() => {
  panelWindow.eval(ownerResourceSource)
  panelWindow.eval(tabContextSource)
  panelWindow.eval(uiSource)
})

afterEach(() => {
  panelWindow.prksResetPrivateNoteDraftsForTest()
  panelWindow.prksDestroyAllTabContexts()
  document.body.innerHTML = ''
})

describe('work private-note draft', () => {
  it('keeps a committed reminder after the pending overlay clears', async () => {
    document.body.innerHTML = `
      <div id="right-panel">
        <div class="tabs"><button class="tab-btn" data-target="details">Details</button></div>
        <div id="panel-content"></div>
      </div>
      <div id="host-a"></div>`
    panelWindow.prksWorkspaceSnapshot = () => ({ focusedTabId: 'tab-a', mainTabId: 'tab-a' })
    const host = document.getElementById('host-a')
    if (!host) throw new Error('host missing')
    panelWindow.prksMountTabContext('tab-a', host)
    const owner = panelWindow.prksGetTabContext('tab-a')
    const work: WorkRecord = {
      id: 'work-a',
      title: 'Alpha',
      private_notes: 'saved-a',
      status: 'Not Started',
      doc_type: 'article',
    }
    owner.setEntity('work', work)
    owner.ui.rightPanelTab = 'details'
    owner.ui.workDetailsMode = 'metadata'
    panelWindow.prksRefreshFocusedRightPanel()

    const panel = document.getElementById('panel-content')
    if (!panel) throw new Error('panel missing')
    const notes = document.createElement('textarea')
    notes.id = 'prks-private-notes-work-work-a'
    notes.className = 'prks-private-notes-input'
    panel.appendChild(notes)
    const status = document.createElement('p')
    status.id = 'prks-private-notes-status-work-work-a'
    panel.appendChild(status)
    panelWindow.initPrksPrivateNotesEditor('work', 'work-a', owner)

    const drafted = 'PRIVATE-PARK-NEW'
    notes.value = drafted
    notes.dispatchEvent(new Event('input', { bubbles: true }))
    const editor = owner.getResource('privateNotesEditor')
    if (!editor || !editor.dirty) throw new Error('reminder draft did not become dirty')

    let releaseSave: (result: { code: string }) => void = () => {}
    const saveResult = new Promise<{ code: string }>((resolve) => {
      releaseSave = resolve
    })
    panelWindow.prksWorkNoteObserved = () => ({ value: 'saved-a', revision: 1 })
    panelWindow.prksRefreshPendingWorkNotes = () => Promise.resolve()
    panelWindow.prksSaveWorkNoteDurably = () => saveResult
    panelWindow.prksFlushPendingPrivateNotes(owner)
    releaseSave({ code: 'saved' })
    await saveResult
    await new Promise((resolve) => {
      setTimeout(resolve, 0)
    })

    let pending: string | null = drafted
    panelWindow.prksPendingWorkNoteText = (_workId, _kind, fallback) => pending ?? fallback
    expect(panelWindow.prksPrivateNotesTextForEntity('work', 'work-a', '')).toBe(drafted)
    pending = null
    expect(panelWindow.prksPrivateNotesTextForEntity('work', 'work-a', 'saved-a')).toBe(drafted)
    expect(work.private_notes).toBe('saved-a')
    expect(panelWindow.prksPrivateNotesTextForEntity('work', 'work-a', drafted)).toBe(drafted)
    expect(panelWindow.prksPrivateNotesTextForEntity('work', 'work-a', 'saved-a')).toBe('saved-a')
  })
})
