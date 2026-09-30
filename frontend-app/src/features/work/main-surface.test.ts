import { nextTick } from 'vue'
import { afterEach, beforeAll, describe, expect, it } from 'vitest'
import tabContextSource from '../../../../frontend/js/tab-context.js?raw'
import videoSource from '../../../../frontend/js/components/works-video.js?raw'
import {
  presentWorkMainSurface,
  resetWorkMainSurfaceForTests,
  type WorkMainSurfaceModel,
} from './main-surface'
import {
  dismissWorkResearchNotes,
  presentWorkResearchNotes,
  resetWorkResearchNotesForTests,
} from './research-note-session'

type WorkCtx = {
  tabId: string
  generation: number
  destroyed: boolean
  ui: object
  root: HTMLElement
  domId: (name: string) => string
  setEntity: (type: string, value: { id: string } | null) => void
  getEntity: (type: string) => { id: string } | null
  isCurrent: (generation: number) => boolean
  getResource: (name: string) => unknown
  setResource: (name: string, value: unknown, disposer?: () => void) => unknown
  beginRoute: (route: { name: string; params: { workId: string } }) => number
}

type SurfaceWindow = {
  eval: (code: string) => void
  prksMountTabContext: (tabId: string, host: HTMLElement) => void
  prksGetTabContext: (tabId: string) => WorkCtx
  prksDestroyAllTabContexts: () => void
  prksDestroyTabContext: (tabId: string) => void
  renderVideoViewerPane: (work: { source_url?: string; provider?: string; provider_id?: string }) => string
}

const surfaceWindow = window as unknown as SurfaceWindow

function mount(tabId: string): WorkCtx {
  const host = document.createElement('div')
  document.body.appendChild(host)
  surfaceWindow.prksMountTabContext(tabId, host)
  return surfaceWindow.prksGetTabContext(tabId)
}

function surface(ctx: WorkCtx, over: Partial<WorkMainSurfaceModel> = {}): WorkMainSurfaceModel {
  return {
    workId: 'work-a',
    generation: ctx.generation,
    kind: 'empty',
    hasFile: false,
    showHeader: true,
    title: 'Lecture',
    docTypeHtml: '<span class="doc-type-badge">Video</span>',
    relSummaryHtml: '<p class="prks-rel-summary">Folder</p>',
    viewerHtml: '',
    editorRegionId: ctx.domId('work-notes-editor-region'),
    ...over,
  }
}

beforeAll(() => {
  surfaceWindow.eval(tabContextSource)
  surfaceWindow.eval(videoSource)
})

afterEach(() => {
  resetWorkMainSurfaceForTests()
  resetWorkResearchNotesForTests()
  surfaceWindow.prksDestroyAllTabContexts()
  document.body.innerHTML = ''
})

describe('work main surface', () => {
  it('inserts the video pane from renderVideoViewerPane and keeps the header', () => {
    const ctx = mount('main')
    ctx.setEntity('work', { id: 'work-a' })
    const viewerHtml = surfaceWindow.renderVideoViewerPane({
      source_url: 'https://www.youtube.com/watch?v=abcdefghijk',
      provider: 'youtube',
      provider_id: 'abcdefghijk',
    })
    expect(presentWorkMainSurface(ctx, surface(ctx, {
      kind: 'video',
      title: 'A & B',
      viewerHtml,
    }))).toBe(true)
    const frame = ctx.root.querySelector('iframe')
    expect(frame?.getAttribute('src')).toContain('abcdefghijk')
    expect(frame?.closest('.work-pdf-pane')).toBeTruthy()
    expect(ctx.root.querySelector('.work-viewer-host')).toBeInstanceOf(HTMLElement)
    expect(ctx.root.querySelector('.page-header--work-title')?.textContent).toBe('A & B')
    expect(ctx.root.querySelector('[data-prks-role="work-header-doc-type-slot"] .doc-type-badge')?.textContent).toBe('Video')
    expect(ctx.root.querySelector('.prks-rel-summary')?.textContent).toBe('Folder')
    expect(ctx.root.querySelector('[data-prks-role="work-research-notes-anchor"]')).toBeInstanceOf(HTMLElement)
    expect(ctx.root.querySelector('.work-split-handle')).toBeInstanceOf(HTMLElement)
  })

  it('replaces the route loading placeholder and keeps the PDF host on the next paint', () => {
    const ctx = mount('main')
    ctx.setEntity('work', { id: 'work-a' })
    ctx.root.innerHTML = [
      '<div class="prks-page-header page-header"><h2 class="prks-page-title">File</h2></div>',
      '<div class="prks-route-loading" role="status"><p class="meta-row">Loading view...</p></div>',
    ].join('')
    ctx.root.setAttribute('aria-busy', 'true')
    expect(presentWorkMainSurface(ctx, surface(ctx, {
      kind: 'pdf',
      hasFile: true,
      showHeader: false,
    }))).toBe(true)
    expect(ctx.root.querySelector('.prks-route-loading')).toBeNull()
    expect(ctx.root.getAttribute('aria-busy')).toBeNull()
    const host = ctx.root.querySelector('[data-prks-role="pdf-viewer"]')
    expect(host).toBeInstanceOf(HTMLElement)
    expect(presentWorkMainSurface(ctx, surface(ctx, {
      kind: 'pdf',
      hasFile: true,
      showHeader: false,
    }))).toBe(true)
    expect(ctx.root.querySelector('[data-prks-role="pdf-viewer"]')).toBe(host)
    expect(ctx.root.querySelectorAll('.prks-route-loading')).toHaveLength(0)
  })

  it('exposes an empty PDF host and omits the page header', () => {
    const ctx = mount('main')
    ctx.setEntity('work', { id: 'work-a' })
    expect(presentWorkMainSurface(ctx, surface(ctx, {
      kind: 'pdf',
      hasFile: true,
      showHeader: false,
    }))).toBe(true)
    expect(ctx.root.querySelector('[data-prks-role="pdf-viewer"]')).toBeInstanceOf(HTMLElement)
    expect(ctx.root.querySelector('iframe')).toBeNull()
    expect(ctx.root.querySelector('.page-header--work')).toBeNull()
    expect(ctx.root.querySelector('.work-pdf-empty')).toBeNull()
  })

  it('names a PDF with no file and a Work with no source', () => {
    const pdf = mount('pdf')
    pdf.setEntity('work', { id: 'work-a' })
    presentWorkMainSurface(pdf, surface(pdf, { kind: 'pdf', hasFile: false, showHeader: true }))
    expect(pdf.root.querySelector('[data-prks-role="pdf-viewer"]')).toBeNull()
    expect(pdf.root.querySelector('.work-pdf-empty')?.textContent).toBe('No PDF file attached.')

    const empty = mount('empty')
    empty.setEntity('work', { id: 'work-a' })
    presentWorkMainSurface(empty, surface(empty, { kind: 'empty', title: 'Loose note' }))
    expect(empty.root.querySelector('.work-pdf-empty')?.textContent).toBe('No file attached.')
    expect(empty.root.querySelector('.page-header--work-title')?.textContent).toBe('Loose note')
  })

  it('keeps Main and Secondary shells mounted at the same time', () => {
    const main = mount('main')
    const side = mount('side')
    main.setEntity('work', { id: 'work-a' })
    side.setEntity('work', { id: 'work-b' })
    const mainHtml = surfaceWindow.renderVideoViewerPane({
      provider: 'youtube',
      provider_id: 'mainvideo01',
      source_url: '',
    })
    const sideHtml = '<div class="work-pdf-pane work-pdf-pane--empty"><p class="work-pdf-empty">No file attached.</p></div>'
    expect(presentWorkMainSurface(main, surface(main, { kind: 'video', viewerHtml: mainHtml, title: 'Main' }))).toBe(true)
    expect(presentWorkMainSurface(side, surface(side, {
      workId: 'work-b',
      kind: 'empty',
      title: 'Side',
      viewerHtml: sideHtml,
    }))).toBe(true)
    expect(main.root.querySelector('iframe')?.getAttribute('src')).toContain('mainvideo01')
    expect(side.root.querySelector('iframe')).toBeNull()
    expect(side.root.querySelector('.page-header--work-title')?.textContent).toBe('Side')
    expect(main.root.querySelector('.page-header--work-title')?.textContent).toBe('Main')
  })

  it('does not paint after the owner generation changes', () => {
    const ctx = mount('main')
    ctx.setEntity('work', { id: 'work-a' })
    const html = surfaceWindow.renderVideoViewerPane({
      provider: 'youtube',
      provider_id: 'keptvideo01',
      source_url: '',
    })
    expect(presentWorkMainSurface(ctx, surface(ctx, { kind: 'video', viewerHtml: html }))).toBe(true)
    const opened = ctx.generation
    ctx.beginRoute({ name: 'work', params: { workId: 'work-b' } })
    ctx.setEntity('work', { id: 'work-b' })
    expect(presentWorkMainSurface(ctx, surface(ctx, {
      workId: 'work-b',
      generation: opened,
      kind: 'empty',
      title: 'Replaced',
    }))).toBe(false)
    expect(ctx.root.querySelector('.page-header--work-title')).toBeNull()
    expect(ctx.root.querySelector('iframe')).toBeNull()
  })

  it('does not paint for a different Work than the one installed', () => {
    const ctx = mount('main')
    ctx.setEntity('work', { id: 'work-a' })
    expect(presentWorkMainSurface(ctx, surface(ctx, { workId: 'work-b' }))).toBe(false)
    expect(ctx.root.querySelector('.work-workspace')).toBeNull()
  })

  it('leaves a Research Notes anchor the notes pane can take over', async () => {
    const ctx = mount('main')
    ctx.setEntity('work', { id: 'work-a' })
    expect(presentWorkMainSurface(ctx, surface(ctx, { kind: 'empty' }))).toBe(true)
    const handle = ctx.root.querySelector('.work-split-handle')
    expect(handle?.getAttribute('role')).toBe('separator')
    expect(handle?.getAttribute('tabindex')).toBe('0')
    expect(handle?.hasAttribute('aria-orientation')).toBe(false)
    expect(handle?.hasAttribute('aria-valuemin')).toBe(false)
    expect(handle?.hasAttribute('aria-valuemax')).toBe(false)
    expect(handle?.hasAttribute('aria-valuenow')).toBe(false)
    const shellField = ctx.root.querySelector('[data-prks-role="research-notes-editor"]') as HTMLTextAreaElement
    const notesEditorId = `${ctx.domId('work-notes-editor-region')}-field`
    expect(shellField.id).toBe(notesEditorId)
    expect(ctx.root.querySelector(`label[for="${notesEditorId}"]`)?.textContent).toBe('Research Notes')
    expect(presentWorkResearchNotes(ctx, { id: 'work-a' }, 'Kept buffer')).toBe(true)
    await nextTick()
    const field = ctx.root.querySelector('[data-prks-role="research-notes-editor"]') as HTMLTextAreaElement
    expect(field.value).toBe('Kept buffer')
    expect(field.closest('[data-prks-role="work-research-notes-anchor"]')).toBeTruthy()
    expect(ctx.root.querySelectorAll('.work-notes-pane')).toHaveLength(1)
  })

  it('disposes Research Notes and the PDF runtime before the shell on route teardown and tab destroy', async () => {
    async function openPair(mainId: string, sideId: string) {
      const main = mount(mainId)
      const side = mount(sideId)
      main.setEntity('work', { id: 'work-a' })
      side.setEntity('work', { id: 'work-b' })
      const paint = (ctx: WorkCtx, workId: string) => {
        expect(presentWorkMainSurface(ctx, surface(ctx, {
          workId,
          kind: 'pdf',
          hasFile: true,
          showHeader: false,
        }))).toBe(true)
        expect(presentWorkResearchNotes(ctx, { id: workId }, `${workId} notes`)).toBe(true)
      }
      paint(main, 'work-a')
      paint(side, 'work-b')
      await nextTick()
      return { main, side }
    }

    function watchChildren(ctx: WorkCtx, log: string[]) {
      const host = ctx.root.querySelector('[data-prks-role="pdf-viewer"]') as HTMLElement
      const anchor = ctx.root.querySelector('[data-prks-role="work-research-notes-anchor"]') as HTMLElement
      const runtime = {
        destroy() {
          log.push(host.isConnected ? 'pdf-connected' : 'pdf-detached')
        },
      }
      ctx.setResource('pdf', runtime, () => runtime.destroy())
      const notes = {
        destroy() {
          log.push('easymde')
        },
      }
      ctx.setResource('workNotes', notes, () => {
        log.push(anchor.isConnected ? 'notes-connected' : 'notes-detached')
        notes.destroy()
        dismissWorkResearchNotes(ctx)
        log.push(anchor.querySelector('.work-notes-pane') ? 'notes-vue-mounted' : 'notes-vue-unmounted')
        log.push(ctx.root.querySelector('.work-workspace') ? 'shell-present' : 'shell-gone')
        log.push(host.isConnected ? 'pdf-host-connected' : 'pdf-host-detached')
      })
      return { host }
    }

    async function expectMainTeardownLeavesSide(trigger: 'route' | 'destroy') {
      const mainId = trigger === 'route' ? 'main-route' : 'main-destroy'
      const sideId = trigger === 'route' ? 'side-route' : 'side-destroy'
      const { main, side } = await openPair(mainId, sideId)
      const mainLog: string[] = []
      const sideLog: string[] = []
      const { host } = watchChildren(main, mainLog)
      watchChildren(side, sideLog)
      const sideHost = side.root.querySelector('[data-prks-role="pdf-viewer"]')
      const sideNotes = side.root.querySelector('[data-prks-role="research-notes-editor"]') as HTMLTextAreaElement
      const mainRoot = main.root
      expect(main.getResource('workMainSurface')).toBeUndefined()
      if (trigger === 'route') {
        main.beginRoute({ name: 'work', params: { workId: 'work-b' } })
      } else {
        surfaceWindow.prksDestroyTabContext(mainId)
      }
      expect(mainLog).toEqual([
        'pdf-connected',
        'notes-connected',
        'easymde',
        'notes-vue-unmounted',
        'shell-present',
        'pdf-host-connected',
      ])
      expect(sideLog).toEqual([])
      expect(mainRoot.querySelector('.work-workspace')).toBeNull()
      expect(host.isConnected).toBe(false)
      expect(side.root.querySelector('.work-workspace')).toBe(sideHost?.closest('.work-workspace'))
      expect(sideHost?.isConnected).toBe(true)
      expect(sideNotes.isConnected).toBe(true)
      expect(sideNotes.value).toBe('work-b notes')
    }

    await expectMainTeardownLeavesSide('route')
    await expectMainTeardownLeavesSide('destroy')
  })
})
