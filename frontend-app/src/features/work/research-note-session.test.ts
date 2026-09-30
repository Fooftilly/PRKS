import { nextTick } from 'vue'
import { afterEach, beforeAll, describe, expect, it } from 'vitest'
import tabContextSource from '../../../../frontend/js/tab-context.js?raw'
import worksSource from '../../../../frontend/js/components/works.js?raw'
import workNotesStateSource from '../../../../frontend/js/work-notes-state.js?raw'
import { presentWorkResearchNotes, registerWorkResearchNotesBridge, resetWorkResearchNotesForTests } from './research-note-session'

type NoteEntry = {
  workId: string
  ownerTabId: string
  text: string
  state: string
}

type WorkCtx = {
  tabId: string
  generation: number
  destroyed: boolean
  ui: { workResearchNoteSession: NoteEntry | null; researchNotesHints: unknown }
  root: HTMLElement
  domId: (name: string) => string
  setEntity: (type: string, value: { id: string; text_content?: string; private_notes?: string } | null) => void
  getEntity: (type: string) => { id: string } | null
  setResource: (name: string, value: unknown) => void
  getResource: (name: string) => unknown
  clearResource: (name: string) => void
  query: (selector: string) => Element | null
  beginRoute: (route: { name: string; params: { workId: string } }) => number
}

type NotesWindow = {
  eval: (code: string) => void
  prksMountTabContext: (tabId: string, host: HTMLElement) => void
  prksGetTabContext: (tabId: string) => WorkCtx
  prksDestroyAllTabContexts: () => void
  prksWorkNotesMarkEdit: (notes: { editGeneration?: number; drafting?: boolean; workId?: string }, workId: string, text: string, ctx?: WorkCtx) => number
  prksResearchNotesTextForWork: (workId: string, serverText: string, ctx: WorkCtx) => string
  prksResearchNotesMayPaint: (owner: WorkCtx, workId: string, generation?: number) => boolean
  prksDestroyWorkNotesEditor: (ctx: WorkCtx) => void
  prksResetResearchDraftsForTest: () => void
  prksEnqueueWorkResearchNotesSave: (ctx: WorkCtx, workId: string) => Promise<{ code: string }>
  initEasyMDE: (ctx: WorkCtx, work: { id: string }) => void
  EasyMDE?: new (options: { element: HTMLTextAreaElement }) => {
    codemirror: { getInputField: () => HTMLElement; on: (event: string, handler: () => void) => void }
    value: () => string
  }
  prksSaveWorkNoteDurably: (
    workId: string,
    kind: string,
    text: string,
    observed?: { value: string; revision: number } | null,
  ) => Promise<{ code: string }>
  prksEnsureWorkNotesBase?: (
    ctx: WorkCtx,
    work: { id?: string; text_content?: string; private_notes?: string } | null,
    options?: { publish?: boolean },
  ) => Promise<{ research?: { value: string; revision: number } } | null>
  prksOfflineReadEntity?: (type: string, workId: string) => Promise<{ value: unknown }>
  prksRememberWorkNotesCanonical?: (ctx: WorkCtx, work: { id?: string }) => unknown
  prksRefreshPendingWorkNotes?: () => Promise<unknown>
  prksWorkNoteOperations?: (rows: unknown, workId: string, kind: string) => unknown[]
  prksWorkNoteObserved?: (ctx: WorkCtx, kind: string) => { value: string; revision: number } | null
  prksPendingWorkNoteText?: (workId: string, kind: string, fallback: string) => string
  prksOfflineRuntimeState?: () => string
}

const notesWindow = window as unknown as NotesWindow

function mount(tabId: string): WorkCtx {
  const host = document.createElement('div')
  document.body.appendChild(host)
  notesWindow.prksMountTabContext(tabId, host)
  return notesWindow.prksGetTabContext(tabId)
}

function holdResearchNotesSave(workId = 'work-a') {
  const ctx = mount('main')
  ctx.setEntity('work', { id: workId })
  ctx.root.innerHTML = '<div data-prks-role="editor-status"></div>'
  const status = ctx.root.querySelector('[data-prks-role="editor-status"]') as HTMLElement
  const releases: Array<(value: { code: string }) => void> = []
  notesWindow.prksSaveWorkNoteDurably = async () => new Promise((resolve) => {
    releases.push(resolve)
  })
  const pending = notesWindow.prksEnqueueWorkResearchNotesSave(ctx, workId)
  return {
    ctx,
    status,
    pending,
    releases,
    refresh(nextId = workId) {
      const started = ctx.generation
      ctx.beginRoute({ name: 'work', params: { workId: nextId } })
      ctx.setEntity('work', { id: nextId })
      return started
    },
  }
}

function installRefreshedNotes(held: ReturnType<typeof holdResearchNotesSave>) {
  const started = held.refresh()
  const notes = {
    pendingSave: true,
    drafting: false,
    saveError: false,
    editGeneration: 0,
    saveSequence: 1,
    latestSaveToken: 1,
    latestSaveEditGeneration: 0,
    settledSaveToken: 0,
  }
  held.ctx.setResource('workNotes', notes)
  held.status.innerText = 'Saving...'
  return { started, notes }
}

beforeAll(() => {
  notesWindow.eval(tabContextSource)
  notesWindow.eval(worksSource)
  registerWorkResearchNotesBridge(window)
})

afterEach(() => {
  notesWindow.prksResetResearchDraftsForTest()
  resetWorkResearchNotesForTests()
  notesWindow.prksDestroyAllTabContexts()
  document.body.innerHTML = ''
})

describe('work research notes session', () => {
  it('keeps each owner draft when two panes show the same Work', () => {
    const main = mount('main')
    const side = mount('side')
    main.setEntity('work', { id: 'work-a' })
    side.setEntity('work', { id: 'work-a' })
    notesWindow.prksWorkNotesMarkEdit({ workId: 'work-a' }, 'work-a', 'Main draft', main)
    notesWindow.prksWorkNotesMarkEdit({ workId: 'work-a' }, 'work-a', 'Side draft', side)
    expect(notesWindow.prksResearchNotesTextForWork('work-a', 'server', main)).toBe('Main draft')
    expect(notesWindow.prksResearchNotesTextForWork('work-a', 'server', side)).toBe('Side draft')
    expect(main.ui.workResearchNoteSession?.text).toBe('Main draft')
    expect(side.ui.workResearchNoteSession?.text).toBe('Side draft')
  })

  it('does not paint a Work A save onto the pane that now shows Work B', async () => {
    const ctx = mount('main')
    ctx.setEntity('work', { id: 'work-a' })
    ctx.root.innerHTML = '<div data-prks-role="editor-status"></div>'
    const status = ctx.root.querySelector('[data-prks-role="editor-status"]') as HTMLElement
    let release: (value: { code: string }) => void = () => {}
    const saved: Array<{ workId: string; text: string }> = []
    notesWindow.prksSaveWorkNoteDurably = async (workId: string, kind: string, text: string) => {
      expect(kind).toBe('work-research-note')
      saved.push({ workId, text })
      return new Promise((resolve) => {
        release = resolve
      })
    }
    const pending = notesWindow.prksEnqueueWorkResearchNotesSave(ctx, 'work-a')
    expect(saved).toEqual([{ workId: 'work-a', text: '' }])
    expect(status.innerText).toBe('Saving...')
    ctx.setEntity('work', { id: 'work-b' })
    release({ code: 'saved' })
    await pending
    await nextTick()
    expect(saved).toEqual([{ workId: 'work-a', text: '' }])
    expect(status.innerText).toBe('Saving...')
    expect(notesWindow.prksResearchNotesMayPaint(ctx, 'work-a')).toBe(false)
    expect(notesWindow.prksResearchNotesMayPaint(ctx, 'work-b')).toBe(true)
  })

  it('records a three-argument mark-edit on the TabContext that holds the editor', () => {
    const main = mount('main')
    const side = mount('side')
    main.setEntity('work', { id: 'work-a' })
    side.setEntity('work', { id: 'work-a' })
    const notes = { workId: 'work-a', editGeneration: 0, drafting: false }
    main.setResource('workNotes', notes)
    notesWindow.prksWorkNotesMarkEdit(notes, 'work-a', 'Unscoped edit')
    expect(main.ui.workResearchNoteSession?.text).toBe('Unscoped edit')
    expect(main.ui.workResearchNoteSession?.ownerTabId).toBe(main.tabId)
    expect(side.ui.workResearchNoteSession).toBeNull()
  })

  it('does not paint a stale save onto a newer generation of the same Work', async () => {
    const held = holdResearchNotesSave()
    expect(held.status.innerText).toBe('Saving...')
    const started = held.refresh()
    expect(held.ctx.generation).not.toBe(started)
    held.status.innerText = 'Fresh editor'
    held.releases[0]({ code: 'saved' })
    await held.pending
    await nextTick()
    expect(held.status.innerText).toBe('Fresh editor')
    expect(notesWindow.prksResearchNotesMayPaint(held.ctx, 'work-a', started)).toBe(false)
  })

  it('settles save 1 onto a same-Work refresh that has no newer edit', async () => {
    const prior = {
      refresh: notesWindow.prksRefreshPendingWorkNotes,
      ops: notesWindow.prksWorkNoteOperations,
      offline: notesWindow.prksOfflineRuntimeState,
    }
    const cases = [
      { queued: false, offline: false, status: 'All changes saved' },
      { queued: true, offline: false, status: 'Waiting to sync' },
      { queued: true, offline: true, status: 'Offline · saved locally' },
    ]
    try {
      for (const item of cases) {
        notesWindow.prksResetResearchDraftsForTest()
        notesWindow.prksDestroyAllTabContexts()
        document.body.innerHTML = ''
        notesWindow.prksRefreshPendingWorkNotes = async () => (item.queued ? [{}] : [])
        notesWindow.prksWorkNoteOperations = () => (item.queued ? [{}] : [])
        notesWindow.prksOfflineRuntimeState = () => (item.offline ? 'offline' : 'online')
        const held = holdResearchNotesSave()
        expect(held.status.innerText).toBe('Saving...')
        const { started, notes } = installRefreshedNotes(held)
        held.releases[0]({ code: 'saved' })
        await held.pending
        await new Promise((resolve) => setTimeout(resolve, 0))
        expect(held.ctx.generation).not.toBe(started)
        expect(notes.pendingSave).toBe(false)
        expect(notes.drafting).toBe(false)
        expect(notes.saveError).toBe(false)
        expect(notes.settledSaveToken).toBe(notes.latestSaveToken)
        expect(held.status.innerText).toBe(item.status)
        expect(notesWindow.prksResearchNotesMayPaint(held.ctx, 'work-a', started)).toBe(false)
      }
    } finally {
      notesWindow.prksRefreshPendingWorkNotes = prior.refresh
      notesWindow.prksWorkNoteOperations = prior.ops
      notesWindow.prksOfflineRuntimeState = prior.offline
    }
  })

  it('does not let save 1 settle a newer same-Work save', async () => {
    const held = holdResearchNotesSave()
    expect(held.releases).toHaveLength(1)
    held.refresh()
    const notes = {
      editor: { value: () => 'second draft' },
      pendingSave: false,
      drafting: false,
      saveError: false,
      editGeneration: 0,
      saveSequence: 0,
      latestSaveToken: 0,
      latestSaveEditGeneration: 0,
      settledSaveToken: 0,
    }
    held.ctx.setResource('workNotes', notes)
    notesWindow.prksWorkNotesMarkEdit(notes, 'work-a', 'second draft', held.ctx)
    const second = notesWindow.prksEnqueueWorkResearchNotesSave(held.ctx, 'work-a')
    expect(held.releases).toHaveLength(2)
    expect(held.status.innerText).toBe('Saving...')
    expect(notes.pendingSave).toBe(true)
    const save2Token = notes.latestSaveToken
    const save2Settled = notes.settledSaveToken
    held.releases[0]({ code: 'saved' })
    await held.pending
    expect(notes.pendingSave).toBe(true)
    expect(notes.latestSaveToken).toBe(save2Token)
    expect(notes.settledSaveToken).toBe(save2Settled)
    expect(held.status.innerText).toBe('Saving...')
    expect(notesWindow.prksResearchNotesTextForWork('work-a', 'server', held.ctx)).toBe('second draft')
    held.releases[1]({ code: 'saved' })
    await second
  })

  it('names the interactive CodeMirror input Research Notes', () => {
    const ctx = mount('main')
    ctx.setEntity('work', { id: 'work-a' })
    ctx.root.innerHTML = `
      <div class="work-notes-editor-wrap">
        <textarea data-prks-role="research-notes-editor"></textarea>
      </div>`
    const input = document.createElement('textarea')
    const previous = notesWindow.EasyMDE
    notesWindow.EasyMDE = class {
      codemirror: { getInputField: () => HTMLElement; on: (event: string, handler: () => void) => void }
      constructor() {
        this.codemirror = {
          getInputField: () => input,
          on: () => {},
        }
      }
      value() { return '' }
    }
    try {
      notesWindow.initEasyMDE(ctx, { id: 'work-a' })
      expect(input.getAttribute('aria-label')).toBe('Research Notes')
    } finally {
      notesWindow.EasyMDE = previous
    }
  })

  it('keeps Work B base when a deferred Work A notes-state read completes', async () => {
    const ctx = mount('main')
    ctx.setEntity('work', { id: 'work-a', text_content: 'A body', private_notes: 'A private' })
    ctx.setResource('workNotesCanonical', {
      id: 'work-a',
      text_content: 'A body',
      private_notes: 'A private',
    })
    ctx.root.innerHTML = '<div data-prks-role="editor-status">Ready</div>'
    const status = ctx.root.querySelector('[data-prks-role="editor-status"]') as HTMLElement
    const prior = {
      ensure: notesWindow.prksEnsureWorkNotesBase,
      read: notesWindow.prksOfflineReadEntity,
      save: notesWindow.prksSaveWorkNoteDurably,
      refresh: notesWindow.prksRefreshPendingWorkNotes,
      ops: notesWindow.prksWorkNoteOperations,
      observed: notesWindow.prksWorkNoteObserved,
      remember: notesWindow.prksRememberWorkNotesCanonical,
      pendingText: notesWindow.prksPendingWorkNoteText,
    }
    const saved: Array<{ workId: string; observed: { value: string; revision: number } | null | undefined }> = []
    let releaseRead: (value: { value: unknown }) => void = () => {}
    try {
      notesWindow.eval(workNotesStateSource)
      notesWindow.prksOfflineReadEntity = () => new Promise((resolve) => {
        releaseRead = resolve
      })
      notesWindow.prksSaveWorkNoteDurably = async (workId, _kind, _text, observed) => {
        saved.push({ workId, observed })
        return { code: 'saved' }
      }
      const pending = notesWindow.prksEnqueueWorkResearchNotesSave(ctx, 'work-a')
      expect(status.innerText).toBe('Saving...')
      ctx.beginRoute({ name: 'work', params: { workId: 'work-b' } })
      ctx.setEntity('work', { id: 'work-b', text_content: 'B body', private_notes: 'B private' })
      const baseB = {
        research: { value: 'B body', revision: 8 },
        private: { value: 'B private', revision: 2 },
      }
      ctx.setResource('workNotesCanonical', {
        id: 'work-b',
        text_content: 'B body',
        private_notes: 'B private',
      })
      ctx.setResource('workNotesObserved', baseB)
      status.innerText = 'B ready'
      releaseRead({
        value: {
          work_id: 'work-a',
          research_note_revision: 4,
          private_note_revision: 1,
        },
      })
      await pending
      expect(saved).toEqual([{
        workId: 'work-a',
        observed: { value: 'A body', revision: 4 },
      }])
      expect(ctx.getResource('workNotesObserved')).toEqual(baseB)
      expect(ctx.getResource('workNotesCanonical')).toEqual({
        id: 'work-b',
        text_content: 'B body',
        private_notes: 'B private',
      })
      expect(status.innerText).toBe('B ready')
      expect(notesWindow.prksResearchNotesMayPaint(ctx, 'work-a')).toBe(false)
    } finally {
      notesWindow.prksEnsureWorkNotesBase = prior.ensure
      notesWindow.prksOfflineReadEntity = prior.read
      notesWindow.prksSaveWorkNoteDurably = prior.save
      notesWindow.prksRefreshPendingWorkNotes = prior.refresh
      notesWindow.prksWorkNoteOperations = prior.ops
      notesWindow.prksWorkNoteObserved = prior.observed
      notesWindow.prksRememberWorkNotesCanonical = prior.remember
      notesWindow.prksPendingWorkNoteText = prior.pendingText
    }
  })

  it('keeps an unsaved buffer across a same-Work refresh', () => {
    const ctx = mount('main')
    ctx.setEntity('work', { id: 'work-a' })
    notesWindow.prksWorkNotesMarkEdit({ workId: 'work-a' }, 'work-a', 'Still typing', ctx)
    ctx.beginRoute({ name: 'work', params: { workId: 'work-a' } })
    ctx.setEntity('work', { id: 'work-a' })
    expect(notesWindow.prksResearchNotesTextForWork('work-a', 'server', ctx)).toBe('Still typing')
    expect(ctx.ui.workResearchNoteSession?.text).toBe('Still typing')
  })

  it('drops hint resources when the editor is destroyed', () => {
    const ctx = mount('main')
    ctx.setResource('workNotes', { editor: {} })
    ctx.setResource('wikiTitleMap', { alpha: 'work-a' })
    ctx.setResource('wikiWorkList', [{ id: 'work-a' }])
    ctx.setResource('conceptHintList', [{ id: 'c1' }])
    ctx.setResource('argumentHintList', [{ id: 'a1' }])
    ctx.ui.researchNotesHints = { wiki: true }
    notesWindow.prksDestroyWorkNotesEditor(ctx)
    expect(ctx.getResource('workNotes')).toBeUndefined()
    expect(ctx.getResource('wikiTitleMap')).toBeUndefined()
    expect(ctx.getResource('wikiWorkList')).toBeUndefined()
    expect(ctx.getResource('conceptHintList')).toBeUndefined()
    expect(ctx.getResource('argumentHintList')).toBeUndefined()
    expect(ctx.ui.researchNotesHints).toBeNull()
  })

  it('dismisses this TabContext Research Notes mount when the editor is destroyed', async () => {
    const main = mount('main')
    const side = mount('side')
    main.setEntity('work', { id: 'work-a' })
    side.setEntity('work', { id: 'work-a' })
    for (const ctx of [main, side]) {
      ctx.root.innerHTML = `
        <div data-prks-role="work-research-notes-anchor"></div>`
    }
    expect(presentWorkResearchNotes(main, { id: 'work-a' }, 'Main note')).toBe(true)
    expect(presentWorkResearchNotes(side, { id: 'work-a' }, 'Side note')).toBe(true)
    await nextTick()
    notesWindow.prksDestroyWorkNotesEditor(main)
    await nextTick()
    expect(main.root.querySelector('[data-prks-role="research-notes-editor"]')).toBeNull()
    expect((side.root.querySelector('[data-prks-role="research-notes-editor"]') as HTMLTextAreaElement).value).toBe('Side note')
    expect(presentWorkResearchNotes(main, { id: 'work-a' }, 'Again')).toBe(true)
    await nextTick()
    expect((main.root.querySelector('[data-prks-role="research-notes-editor"]') as HTMLTextAreaElement).value).toBe('Again')
  })

  it('mounts one Research Notes pane and keeps the unsaved text', async () => {
    const ctx = mount('main')
    ctx.setEntity('work', { id: 'work-a' })
    ctx.root.innerHTML = `
      <div class="work-workspace" data-work-id="work-a">
        <div data-prks-role="work-research-notes-anchor">
          <div class="work-notes-pane">
            <textarea data-prks-role="research-notes-editor">shell</textarea>
          </div>
        </div>
      </div>`
    notesWindow.prksWorkNotesMarkEdit({ workId: 'work-a' }, 'work-a', 'Kept buffer', ctx)
    const text = notesWindow.prksResearchNotesTextForWork('work-a', 'server', ctx)
    expect(text).toBe('Kept buffer')
    expect(presentWorkResearchNotes(ctx, { id: 'work-a' }, text)).toBe(true)
    await nextTick()
    const fields = ctx.root.querySelectorAll('[data-prks-role="research-notes-editor"]')
    expect(fields).toHaveLength(1)
    const field = fields[0] as HTMLTextAreaElement
    const notesEditorId = `${ctx.domId('work-notes-editor-region')}-field`
    expect(field.value).toBe('Kept buffer')
    expect(field.id).toBe(notesEditorId)
    expect(ctx.root.querySelector(`label[for="${notesEditorId}"]`)?.textContent).toBe('Research Notes')
    expect(ctx.root.querySelector('.work-notes-title')?.textContent).toBe('Research Notes')
    expect(ctx.root.querySelector('[data-prks-role="work-notes-collapse-btn"]')).toBeInstanceOf(HTMLButtonElement)
    expect(ctx.root.querySelector('[data-prks-role="editor-status"]')).toBeInstanceOf(HTMLElement)
    expect(ctx.root.querySelector('.work-workspace')?.classList.contains('work-workspace--notes-collapsed')).toBe(false)
  })

  it('keeps Main and Secondary panes mounted at the same time', async () => {
    function shell(ctx: WorkCtx) {
      ctx.setEntity('work', { id: 'work-a' })
      ctx.root.innerHTML = `
        <div class="work-workspace" data-work-id="work-a">
          <div data-prks-role="work-research-notes-anchor"></div>
        </div>`
    }
    const main = mount('main')
    const side = mount('side')
    shell(main)
    shell(side)
    expect(presentWorkResearchNotes(main, { id: 'work-a' }, 'Main pane')).toBe(true)
    expect(presentWorkResearchNotes(side, { id: 'work-a' }, 'Side pane')).toBe(true)
    expect(presentWorkResearchNotes(main, { id: 'work-a' }, 'Main pane')).toBe(true)
    await nextTick()
    const mainField = main.root.querySelector('[data-prks-role="research-notes-editor"]') as HTMLTextAreaElement
    const sideField = side.root.querySelector('[data-prks-role="research-notes-editor"]') as HTMLTextAreaElement
    expect(mainField.value).toBe('Main pane')
    expect(sideField.value).toBe('Side pane')
    const mainEditorId = `${main.domId('work-notes-editor-region')}-field`
    const sideEditorId = `${side.domId('work-notes-editor-region')}-field`
    expect(mainField.id).toBe(mainEditorId)
    expect(sideField.id).toBe(sideEditorId)
    expect(mainEditorId).not.toBe(sideEditorId)
    expect(main.root.querySelector(`label[for="${mainEditorId}"]`)?.textContent).toBe('Research Notes')
    expect(side.root.querySelector(`label[for="${sideEditorId}"]`)?.textContent).toBe('Research Notes')
    expect(main.root.querySelectorAll('.work-notes-pane')).toHaveLength(1)
    expect(side.root.querySelectorAll('.work-notes-pane')).toHaveLength(1)
  })
})
