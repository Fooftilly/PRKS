import { nextTick } from 'vue'
import { afterEach, beforeAll, describe, expect, it } from 'vitest'
import tabContextSource from '../../../../frontend/js/tab-context.js?raw'
import videoSource from '../../../../frontend/js/components/works-video.js?raw'
import {
  presentWorkMainSurface,
  resetWorkMainSurfaceForTests,
  type WorkMainSurfaceModel,
} from './main-surface'
import { presentWorkResearchNotes, resetWorkResearchNotesForTests } from './research-note-session'

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
    expect(presentWorkResearchNotes(ctx, { id: 'work-a' }, 'Kept buffer')).toBe(true)
    await nextTick()
    const field = ctx.root.querySelector('[data-prks-role="research-notes-editor"]') as HTMLTextAreaElement
    expect(field.value).toBe('Kept buffer')
    expect(field.closest('[data-prks-role="work-research-notes-anchor"]')).toBeTruthy()
    expect(ctx.root.querySelectorAll('.work-notes-pane')).toHaveLength(1)
  })
})
