import { nextTick } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { readRouteSurface } from '../../route-surface/lifecycle'
import {
  dismissProcessing,
  presentProcessing,
  registerProcessingBridge,
  resetProcessingSessionForTests,
} from './session'

afterEach(() => {
  resetProcessingSessionForTests()
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
  delete window.prksVuePresentProcessing
  delete window.prksVueDismissProcessing
  delete window.prksProcessingAttachResources
  delete window.prksProcessingReleaseResources
  delete window.prksProcessingSetPreview
  delete window.prksReloadProcessingFiles
})

function host(): HTMLElement {
  const el = document.createElement('div')
  document.body.appendChild(el)
  return el
}

function owner(generation: number) {
  return {
    tabId: 'main',
    isCurrent: (value: number) => value === generation,
    route: { name: 'processing-files' },
  }
}

const file = {
  id: 'pdf-1',
  filename: 'notes.pdf',
  rel_path: 'inbox/notes.pdf',
  status: 'pending',
  exists: true,
}

describe('Processing Files route bridge', () => {
  it('paints the inbox for each owner and does not fetch', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    const attach = vi.fn()
    window.prksProcessingAttachResources = attach
    const main = owner(2)
    const secondary = { ...owner(1), tabId: 'other', isCurrent: (value: number) => value === 1 }
    const mainHost = host()
    const secondaryHost = host()
    presentProcessing({
      owner: main,
      host: mainHost,
      files: [file, { id: 'pdf-2', filename: 'b.pdf', status: 'pending', exists: true }],
      people: [],
      folders: [],
      roleTypes: ['Author'],
      domPrefix: 'prks-pf-main',
      generation: 2,
      shell: true,
    })
    presentProcessing({
      owner: secondary,
      host: secondaryHost,
      files: [],
      people: [],
      folders: [],
      roleTypes: ['Author'],
      domPrefix: 'prks-pf-other',
      generation: 1,
      shell: false,
    })
    await nextTick()
    expect(mainHost.querySelector('.prks-page-title')?.textContent).toBe('Files for Processing')
    expect(mainHost.querySelector('#prks-processing-refresh')).not.toBeNull()
    expect(mainHost.querySelectorAll('[data-processing-id]').length).toBe(2)
    expect(mainHost.innerHTML).not.toContain('style="display: contents"')
    expect(mainHost.querySelectorAll('.work-html-slot').length).toBeGreaterThan(0)
    expect(secondaryHost.textContent).toContain('No PDF files waiting for processing.')
    expect(secondaryHost.querySelector('[data-processing-id]')).toBeNull()
    expect(fetchMock).not.toHaveBeenCalled()
    expect(attach).toHaveBeenCalledWith(main, expect.any(HTMLElement))
    expect(readRouteSurface(main)?.name).toBe('processing-files')
    expect(readRouteSurface(secondary)?.canonicalHash).toBe('#/processing-files')
  })

  it('rejects an older generation and releases the preview with the owner', async () => {
    const release = vi.fn()
    window.prksProcessingReleaseResources = release
    window.prksProcessingAttachResources = vi.fn()
    const current = owner(4)
    const pane = host()
    presentProcessing({
      owner: current,
      host: pane,
      files: [file],
      people: [],
      folders: [],
      roleTypes: [],
      domPrefix: 'prks-pf-main',
      generation: 4,
    })
    presentProcessing({
      owner: current,
      host: pane,
      files: [],
      people: [],
      folders: [],
      roleTypes: [],
      domPrefix: 'prks-pf-main',
      generation: 3,
    })
    await nextTick()
    expect(pane.querySelector('[data-processing-id="pdf-1"]')).not.toBeNull()
    dismissProcessing(current)
    expect(release).toHaveBeenCalledWith(current)
    expect(pane.querySelector('[data-prks-processing-page]')).toBeNull()
  })

  it('registers the early presenter', () => {
    registerProcessingBridge(window)
    expect(typeof window.prksVuePresentProcessing).toBe('function')
    expect(typeof window.prksVueDismissProcessing).toBe('function')
  })
})
