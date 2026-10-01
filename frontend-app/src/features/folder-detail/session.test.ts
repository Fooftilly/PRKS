import { nextTick } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { readRouteSurface } from '../../route-surface/lifecycle'
import {
  dismissFolderDetail,
  presentFolderDetail,
  registerFolderDetailBridge,
  resetFolderDetailSessionForTests,
} from './session'

afterEach(() => {
  resetFolderDetailSessionForTests()
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
  delete window.prksVuePresentFolderDetail
  delete window.prksVueDismissFolderDetail
  delete window.prksWorkCardHtml
  delete window.prksEffectiveFolderDetailWorks
  delete window.prksCommitFolderDetailSurface
  delete window.prksDeleteFolderFromDetail
  delete window.prksOpenNewFolderFromDetail
  delete window.prksFolderDetailSummaryHtml
  delete window.prksFolderDetailNavHtml
  delete window.prksFolderDetailSubfoldersHtml
  delete window.prksReleaseWorkThumbPreview
  delete (window as Window & { prksSyncSidebarActive?: unknown }).prksSyncSidebarActive
})

function host(): HTMLElement {
  const el = document.createElement('div')
  document.body.appendChild(el)
  return el
}

function owner(current = true) {
  let cleanup: (() => void) | null = null
  return {
    tabId: 'tab-1',
    __prksRetainFolderDetailSurface: false,
    isCurrent: () => current,
    getEntity: () => null as { id?: string } | null,
    registerCleanup(fn: () => void) {
      cleanup = fn
    },
    runCleanup() {
      cleanup?.()
    },
  }
}

describe('Folder detail route bridge', () => {
  it('isolates Main and Secondary and does not publish shell state', () => {
    const calls: string[] = []
    ;(window as Window & { prksSyncSidebarActive?: () => void }).prksSyncSidebarActive = () => {
      calls.push('sidebar')
    }
    const commits: Array<{ id: unknown; preserve: boolean }> = []
    window.prksCommitFolderDetailSurface = (_ctx, folder, _container, options) => {
      commits.push({
        id: (folder as { id?: unknown }).id,
        preserve: options?.preserveFolderWorkspace === true,
      })
    }
    window.prksWorkCardHtml = (work) => `<div data-work-id="${String(work.id)}"></div>`
    window.prksEffectiveFolderDetailWorks = () => [{ id: 'overlay' }]
    const main = owner()
    const secondary = owner()
    const mainHost = host()
    const secondaryHost = host()
    presentFolderDetail({
      owner: main,
      host: mainHost,
      folder: { id: 'main', title: 'Main notes', works: [{ id: 'raw' }], children: [] },
      offlineCached: true,
      preserveWorkspace: false,
      generation: 4,
      shell: true,
    })
    presentFolderDetail({
      owner: secondary,
      host: secondaryHost,
      folder: { id: 'side', title: 'Side notes', works: [], children: [{ id: 'c' }] },
      preserveWorkspace: true,
      generation: 1,
      shell: false,
    })
    expect(mainHost.querySelector('.prks-page-title')?.textContent).toContain('Main notes')
    expect(secondaryHost.querySelector('.prks-page-title')?.textContent).toContain('Side notes')
    expect(mainHost.querySelector('[data-work-id="overlay"]')).not.toBeNull()
    expect(mainHost.querySelector('[data-work-id="raw"]')).toBeNull()
    expect(mainHost.querySelector('[data-delete-folder-id]')).toBeNull()
    expect(secondaryHost.querySelector('[data-delete-folder-id]')).toBeNull()
    expect(mainHost.querySelector('[data-prks-folder-detail-tree-host]')).not.toBeNull()
    expect(readRouteSurface(main)).toMatchObject({
      name: 'folder-detail',
      canonicalHash: '#/folders/main',
      ownsMainShell: true,
    })
    expect(readRouteSurface(secondary)).toMatchObject({
      canonicalHash: '#/folders/side',
      ownsMainShell: false,
    })
    expect(commits.map((row) => row.id)).toEqual(['main', 'side'])
    expect(commits[1]?.preserve).toBe(true)
    expect(calls).toEqual([])
  })

  it('keeps a retained hierarchy shell and drops a stale generation', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    window.prksWorkCardHtml = () => ''
    const released: ParentNode[] = []
    window.prksReleaseWorkThumbPreview = (root) => {
      if (root) released.push(root)
    }
    const pane = owner()
    const el = host()
    presentFolderDetail({
      owner: pane,
      host: el,
      folder: { id: 'f1', title: 'First', works: [], children: [] },
      generation: 2,
    })
    expect(el.querySelector('[data-delete-folder-id="f1"]')).not.toBeNull()
    expect(released.length).toBeGreaterThan(0)
    pane.__prksRetainFolderDetailSurface = true
    pane.runCleanup()
    expect(el.querySelector('[data-prks-role="folder-detail"]')).not.toBeNull()
    pane.__prksRetainFolderDetailSurface = false
    pane.runCleanup()
    expect(el.querySelector('[data-prks-role="folder-detail"]')).toBeNull()
    presentFolderDetail({
      owner: pane,
      host: el,
      folder: { id: 'f2', title: 'Second', works: [], children: [] },
      generation: 2,
    })
    await nextTick()
    expect(el.querySelector('[data-delete-folder-id="f2"]')).toBeNull()
    presentFolderDetail({
      owner: pane,
      host: el,
      availability: 'unavailable',
      folder: null,
      folderId: 'f2',
      generation: 3,
    })
    expect(el.querySelector('[data-prks-role="offline-unavailable"]')?.textContent).toContain(
      'not available offline',
    )
    expect(el.querySelector('[data-prks-role="folder-detail"]')).toBeNull()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('sends delete and new-folder through the canonical wrappers', async () => {
    const deleted: string[] = []
    const opened: unknown[] = []
    window.prksDeleteFolderFromDetail = async (id) => {
      deleted.push(id)
    }
    window.prksOpenNewFolderFromDetail = (folder) => {
      opened.push(folder.title)
    }
    window.prksWorkCardHtml = () => ''
    const pane = owner()
    pane.getEntity = () => ({ id: 'f1' })
    const el = host()
    presentFolderDetail({
      owner: pane,
      host: el,
      folder: { id: 'f1', title: 'Empty', description: '', works: [], children: [] },
      generation: 1,
    })
    el.querySelector<HTMLButtonElement>('[data-prks-role="folder-detail-new-folder"]')?.click()
    el.querySelector<HTMLButtonElement>('[data-delete-folder-id]')?.click()
    await nextTick()
    expect(opened).toEqual(['Empty'])
    expect(deleted).toEqual(['f1'])
    expect(el.textContent).toContain('No description provided.')
  })

  it('paints not-found without the hierarchy shell', () => {
    const pane = owner()
    const el = host()
    presentFolderDetail({
      owner: pane,
      host: el,
      availability: 'not-found',
      folder: null,
      folderId: 'missing',
      generation: 1,
    })
    expect(el.querySelector('.prks-inline-message--error')?.textContent).toBe('Folder not found.')
    expect(el.querySelector('[data-prks-role="folder-detail"]')).toBeNull()
    expect(readRouteSurface(pane)?.canonicalHash).toBe('#/folders/missing')
  })

  it('registers the folder-detail bridge on the host that stored the request', () => {
    const el = host()
    const decoy = host()
    el.setAttribute('data-prks-vue-route-host', 'true')
    decoy.setAttribute('data-prks-vue-route-host', 'true')
    const pane = owner()
    ;(el as HTMLElement & { __prksVueRouteRequest?: object }).__prksVueRouteRequest = {
      feature: 'folder-detail',
      owner: pane,
      host: decoy,
      availability: 'ready',
      folder: { id: 'early', title: 'Early', works: [], children: [] },
      generation: 1,
      shell: true,
    }
    window.prksWorkCardHtml = () => ''
    registerFolderDetailBridge(window)
    expect(window.prksVuePresentFolderDetail).toBeTypeOf('function')
    expect((el as HTMLElement & { __prksVueRouteRequest?: unknown }).__prksVueRouteRequest).toBeUndefined()
    expect(el.querySelector('.prks-page-title')?.textContent).toContain('Early')
    expect(decoy.querySelector('[data-prks-folder-detail-view]')).toBeNull()
  })

  it('dismisses one owner and leaves the other mounted', () => {
    window.prksWorkCardHtml = () => ''
    registerFolderDetailBridge(window)
    const main = owner()
    const secondary = owner()
    const mainHost = host()
    const secondaryHost = host()
    window.prksVuePresentFolderDetail?.({
      owner: main,
      host: mainHost,
      folder: { id: 'main', title: 'Main', works: [], children: [] },
      generation: 2,
      shell: true,
    })
    window.prksVuePresentFolderDetail?.({
      owner: secondary,
      host: secondaryHost,
      folder: { id: 'side', title: 'Side', works: [], children: [] },
      generation: 1,
      shell: false,
    })
    dismissFolderDetail(main)
    expect(mainHost.querySelector('[data-prks-folder-detail-view]')).toBeNull()
    expect(secondaryHost.querySelector('.prks-page-title')?.textContent).toContain('Side')
  })
})
