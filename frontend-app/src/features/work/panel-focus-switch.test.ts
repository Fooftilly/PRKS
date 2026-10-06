import { afterEach, beforeAll, describe, expect, it } from 'vitest'
import ownerResourceSource from '../../../../frontend/js/owner-resource.js?raw'
import tabContextSource from '../../../../frontend/js/tab-context.js?raw'
import uiSource from '../../../../frontend/js/ui.js?raw'
import { registerWorkMetadataEditorBridge, resetWorkMetadataEditorForTests } from './metadata-session'

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
  ui: {
    rightPanelTab: string
    workDetailsMode: string
    workMetaDraft: Record<string, string> | null
    workMetaDraftWorkId: string | null
  }
  route: { name: string; hash: string; canonicalHash: string; params: { workId: string } }
  lastResolvedRoute: WorkCtx['route']
  setEntity: (type: string, value: WorkRecord) => void
  getEntity: (type: string) => WorkRecord | null
  getResource: (name: string) => { dirty: boolean; textarea: HTMLTextAreaElement; entityId: string } | null
}

type SaveCall = { entityId: string; kind: string; content: string }

type PanelWindow = {
  eval: (code: string) => void
  prksWorkspaceSnapshot: () => { focusedTabId: string; mainTabId: string }
  prksMountTabContext: (tabId: string, host: HTMLElement) => void
  prksGetTabContext: (tabId: string) => WorkCtx
  prksDestroyAllTabContexts: () => void
  prksRefreshFocusedRightPanel: () => void
  initPrksPrivateNotesEditor: (entityType: string, entityId: string, owner: WorkCtx) => void
  prksResetPrivateNoteDraftsForTest: () => void
  prksSaveWorkNoteDurably: (entityId: string, kind: string, content: string) => Promise<{ code: string }>
  prksWorkNoteObserved: () => { value: string; revision: number }
  prksRefreshPendingWorkNotes: () => Promise<void>
}

const panelWindow = window as unknown as PanelWindow

beforeAll(() => {
  panelWindow.eval(ownerResourceSource)
  panelWindow.eval(tabContextSource)
  panelWindow.eval(uiSource)
  registerWorkMetadataEditorBridge(window)
})

afterEach(() => {
  resetWorkMetadataEditorForTests()
  panelWindow.prksResetPrivateNoteDraftsForTest()
  panelWindow.prksDestroyAllTabContexts()
  document.body.innerHTML = ''
})

describe('work panel focus switch', () => {
  it('keeps the unfocused draft and private-note save off the newly focused Work', async () => {
    document.body.innerHTML = `
      <div id="right-panel">
        <div class="tabs"><button class="tab-btn" data-target="details">Details</button></div>
        <div id="panel-content"></div>
      </div>
      <div id="host-a"></div>
      <div id="host-b"></div>`
    const workspace = { focusedTabId: 'tab-a', mainTabId: 'tab-a' }
    panelWindow.prksWorkspaceSnapshot = () => workspace
    const hostA = document.getElementById('host-a')
    const hostB = document.getElementById('host-b')
    if (!hostA || !hostB) throw new Error('hosts missing')
    panelWindow.prksMountTabContext('tab-a', hostA)
    panelWindow.prksMountTabContext('tab-b', hostB)
    const ownerA = panelWindow.prksGetTabContext('tab-a')
    const ownerB = panelWindow.prksGetTabContext('tab-b')
    const workA: WorkRecord = {
      id: 'work-a',
      title: 'Alpha',
      private_notes: 'saved-a',
      status: 'Not Started',
      doc_type: 'article',
    }
    const workB: WorkRecord = {
      id: 'work-b',
      title: 'Beta',
      private_notes: 'saved-b',
      status: 'Not Started',
      doc_type: 'article',
    }
    ownerA.setEntity('work', workA)
    ownerB.setEntity('work', workB)
    const routeFor = (id: string) => ({
      name: 'work',
      hash: `#/works/${id}`,
      canonicalHash: `#/works/${id}`,
      params: { workId: id },
    })
    ownerA.route = routeFor('work-a')
    ownerA.lastResolvedRoute = ownerA.route
    ownerB.route = routeFor('work-b')
    ownerB.lastResolvedRoute = ownerB.route
    ownerA.ui.rightPanelTab = 'details'
    ownerB.ui.rightPanelTab = 'details'
    ownerA.ui.workDetailsMode = 'metadata'
    ownerA.ui.workMetaDraftWorkId = 'work-a'
    ownerA.ui.workMetaDraft = {
      title: 'Alpha',
      status: '',
      doc_type: 'article',
      year: '',
      published_date: '',
      publisher: '',
      location: '',
      edition: '',
      journal: '',
      volume: '',
      issue: '',
      pages: '',
      isbn: '',
      doi: '',
      source_url: '',
      abstract: '',
      thumb_page: '',
      author_text: '',
    }

    panelWindow.prksRefreshFocusedRightPanel()
    const title = document.getElementById('meta-title')
    if (!(title instanceof HTMLInputElement)) throw new Error('metadata title missing')
    title.value = 'Alpha unsaved'
    const panel = document.getElementById('panel-content')
    if (!panel) throw new Error('panel missing')
    const notes = document.createElement('textarea')
    notes.id = 'prks-private-notes-work-work-a'
    notes.className = 'prks-private-notes-input'
    notes.value = 'Remember the first file'
    panel.appendChild(notes)
    const status = document.createElement('p')
    status.id = 'prks-private-notes-status-work-work-a'
    panel.appendChild(status)
    panelWindow.initPrksPrivateNotesEditor('work', 'work-a', ownerA)
    const editor = ownerA.getResource('privateNotesEditor')
    if (!editor) throw new Error('notes editor missing')
    editor.textarea.value = 'Remember the first file'
    editor.dirty = true

    let releaseSave: (result: { code: string }) => void = () => {}
    const saveResult = new Promise<{ code: string }>((resolve) => {
      releaseSave = resolve
    })
    const saves: SaveCall[] = []
    panelWindow.prksWorkNoteObserved = () => ({ value: 'saved-a', revision: 1 })
    panelWindow.prksRefreshPendingWorkNotes = () => Promise.resolve()
    panelWindow.prksSaveWorkNoteDurably = (entityId, kind, content) => {
      saves.push({ entityId: String(entityId), kind: String(kind), content: String(content) })
      return saveResult
    }

    workspace.focusedTabId = 'tab-b'
    panelWindow.prksRefreshFocusedRightPanel()
    releaseSave({ code: 'saved' })
    await saveResult
    await new Promise((resolve) => {
      setTimeout(resolve, 0)
    })

    expect(ownerA.ui.workMetaDraftWorkId).toBe('work-a')
    expect(ownerA.ui.workMetaDraft?.title).toBe('Alpha unsaved')
    expect(workA.title).toBe('Alpha')
    expect(workA.private_notes).toBe('saved-a')
    expect(saves).toEqual([
      { entityId: 'work-a', kind: 'work-private-note', content: 'Remember the first file' },
    ])
    expect(workB.private_notes).toBe('saved-b')
    expect(workB.title).toBe('Beta')
    expect(ownerB.ui.workMetaDraft).toBeNull()

    const focused = document.getElementById('panel-content')
    if (!focused) throw new Error('focused panel missing')
    expect(focused.dataset.prksOwnerTabId).toBe('tab-b')
    expect(focused.dataset.prksOwnerGeneration).toBe(String(ownerB.generation))
    expect(focused.querySelector('.card-title')?.textContent).toBe('Beta')
    expect(focused.querySelector('#prks-private-notes-work-work-a')).toBeNull()
    const focusedNotes = focused.querySelector('#prks-private-notes-work-work-b')
    expect(focusedNotes).toBeInstanceOf(HTMLTextAreaElement)
    expect((focusedNotes as HTMLTextAreaElement).value).toBe('saved-b')
    expect(focused.textContent).not.toContain('Remember the first file')
    expect(focused.textContent).not.toContain('Alpha unsaved')
    const readRequest = (focused as HTMLElement & { __prksWorkPanelReadRequest?: { workId?: string } })
      .__prksWorkPanelReadRequest
    expect(readRequest?.workId).toBe('work-b')
  })
})
