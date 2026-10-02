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
  delete window.prksProcessingQuickCreatePerson
  delete window.prksProcessingQuickCreateFolder
  delete window.prksProcessingSave
  delete window.prksProcessingImport
  delete window.__prksProcessingPeople
})

async function flush(): Promise<void> {
  await nextTick()
  await new Promise((resolve) => setTimeout(resolve, 0))
  await nextTick()
}

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

  it('keeps painted people when quick-create returns an empty or short global catalogue', async () => {
    window.__prksProcessingPeople = []
    window.prksProcessingQuickCreatePerson = async () => {
      window.__prksProcessingPeople = [{ id: 'new', name: 'New Person' }]
      return { id: 'new', name: 'New Person', people: [{ id: 'new', name: 'New Person' }] }
    }
    const el = host()
    presentProcessing({
      owner: owner(2),
      host: el,
      files: [file],
      people: [
        { id: 'ada', first_name: 'Ada', last_name: 'Lovelace' },
        { id: 'grace', first_name: 'Grace', last_name: 'Hopper' },
      ],
      folders: [],
      roleTypes: ['Author'],
      domPrefix: 'prks-pf-main',
      generation: 2,
    })
    await nextTick()
    const input = el.querySelector<HTMLInputElement>('[aria-label="Search person"]')
    input!.value = 'New Person'
    input!.dispatchEvent(new Event('input'))
    await nextTick()
    el.querySelector('.result-item--create')?.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    await flush()
    input!.value = ''
    input!.dispatchEvent(new Event('input'))
    await nextTick()
    const names = [...el.querySelectorAll('.result-item--person-pick .result-item__primary')].map((node) => node.textContent)
    expect(names).toEqual(['Ada Lovelace', 'Grace Hopper', 'New Person'])
    expect(window.__prksProcessingPeople).toEqual([{ id: 'new', name: 'New Person' }])
  })

  it('keeps painted folders when the folder read after create is only the new folder', async () => {
    window.prksProcessingQuickCreateFolder = async () => ({
      ok: true,
      id: 'created',
      title: 'Created',
      folders: [{ id: 'created', title: 'Created' }],
      foldersFailed: false,
    })
    const el = host()
    presentProcessing({
      owner: owner(2),
      host: el,
      files: [file],
      people: [],
      folders: [
        { id: 'lib', title: 'Library' },
        { id: 'arc', title: 'Archive' },
      ],
      roleTypes: ['Author'],
      domPrefix: 'prks-pf-main',
      generation: 2,
    })
    await nextTick()
    const input = el.querySelector<HTMLInputElement>('[aria-label="Search folder"]')
    input!.value = 'Created'
    input!.dispatchEvent(new Event('input'))
    el.querySelector<HTMLButtonElement>('[aria-label="Create new folder"]')?.click()
    await flush()
    input!.value = ''
    input!.dispatchEvent(new Event('input'))
    await nextTick()
    const folderList = input!.closest('.tag-add-shell')?.querySelector('.combobox-results')
    const titles = [...(folderList?.querySelectorAll('.result-item') || [])]
      .map((node) => node.textContent?.trim())
      .filter((title) => title && title !== 'No folders found')
    expect(titles).toEqual(['Library', 'Archive', 'Created'])
  })

  it('keeps the inbox when a refresh rejects, then clears the busy state', async () => {
    window.prksReloadProcessingFiles = async () => {
      throw new Error('Could not refresh files for processing.')
    }
    const el = host()
    presentProcessing({
      owner: owner(2),
      host: el,
      files: [file],
      people: [],
      folders: [],
      roleTypes: ['Author'],
      domPrefix: 'prks-pf-main',
      generation: 2,
    })
    await nextTick()
    el.querySelector<HTMLButtonElement>('#prks-processing-refresh')?.click()
    await flush()
    expect(el.querySelector('[data-processing-id="pdf-1"]')).not.toBeNull()
    expect(el.querySelector('[data-prks-processing-refresh-error]')?.textContent).toContain(
      'Could not refresh files for processing.',
    )
    const refresh = el.querySelector<HTMLButtonElement>('#prks-processing-refresh')
    expect(refresh?.getAttribute('aria-busy')).toBeNull()
    expect(refresh?.textContent).toContain('Refresh folder scan')
  })

  it('stays quiet when a refresh rejects for a stale owner', async () => {
    let current = true
    const pane = {
      tabId: 'main',
      isCurrent: () => current,
      route: { name: 'processing-files' },
    }
    window.prksReloadProcessingFiles = async () => {
      current = false
      throw new Error('Could not refresh files for processing.')
    }
    const el = host()
    presentProcessing({
      owner: pane,
      host: el,
      files: [file],
      people: [],
      folders: [],
      roleTypes: ['Author'],
      domPrefix: 'prks-pf-main',
      generation: 2,
    })
    await nextTick()
    el.querySelector<HTMLButtonElement>('#prks-processing-refresh')?.click()
    await flush()
    expect(el.querySelector('[data-prks-processing-refresh-error]')).toBeNull()
    expect(el.querySelector('[data-processing-id="pdf-1"]')).not.toBeNull()
    expect(el.querySelector('#prks-processing-refresh')?.getAttribute('aria-busy')).toBeNull()
  })

  it('clears import busy and shows the reload error beside the import action', async () => {
    window.prksProcessingSave = async () => ({})
    window.prksProcessingImport = async () => ({})
    window.prksReloadProcessingFiles = async () => {
      throw new Error('Could not refresh files for processing.')
    }
    const el = host()
    presentProcessing({
      owner: owner(2),
      host: el,
      files: [file],
      people: [],
      folders: [],
      roleTypes: ['Author'],
      domPrefix: 'prks-pf-main',
      generation: 2,
    })
    await nextTick()
    const button = [...el.querySelectorAll('button')].find((node) => node.textContent?.includes('Import to library'))
    button?.click()
    await flush()
    expect(el.querySelector('[data-processing-id="pdf-1"]')).not.toBeNull()
    expect(el.querySelector('.prks-processing-card__message')?.textContent).toContain(
      'Could not refresh files for processing.',
    )
    const after = [...el.querySelectorAll('button')].find((node) => node.textContent?.includes('Import to library'))
    expect(after?.getAttribute('aria-busy')).toBeNull()
  })

  it('registers the early presenter', () => {
    registerProcessingBridge(window)
    expect(typeof window.prksVuePresentProcessing).toBe('function')
    expect(typeof window.prksVueDismissProcessing).toBe('function')
  })
})
