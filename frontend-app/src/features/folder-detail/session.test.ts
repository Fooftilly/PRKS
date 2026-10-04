import { nextTick } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { dismissRouteSurface, readRouteSurface } from '../../route-surface/lifecycle'
import {
  presentFolderDetail,
  registerFolderDetailBridge,
  resetFolderDetailSessionForTests,
} from './session'

afterEach(() => {
  resetFolderDetailSessionForTests()
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
  delete window.prksVuePresentRoute
  delete window.prksVueDismissRoute
  delete window.prksEffectiveFolderDetailWorks
  delete window.prksCommitFolderDetailSurface
  delete window.prksDeleteFolderFromDetail
  delete window.prksOpenNewFolderFromDetail
  delete window.prksFolderDetailSummaryHtml
  delete window.prksFolderDetailNavHtml
  delete window.prksMountFolderHierarchyNav
  delete window.prksFolderDetailSubfoldersHtml
  delete window.prksReleaseWorkThumbPreview
  delete window.prksReleaseLazyWorkThumbs
  delete (window as Window & { __prksWorkThumbPreviewSource?: unknown }).__prksWorkThumbPreviewSource
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
    // First Vue mount has nothing previous to release. Leave/rewrite coverage
    // is the next test: "releases preview and lazy thumbs before leave unmount".
    expect(released.length).toBe(0)
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

  it('releases preview and lazy thumbs before leave unmount detaches them', () => {
    const preview = window as Window & { __prksWorkThumbPreviewSource?: Element | null }
    window.prksReleaseWorkThumbPreview = (root) => {
      const src = preview.__prksWorkThumbPreviewSource
      if (!src || !root || typeof root.contains !== 'function' || !root.contains(src)) return
      preview.__prksWorkThumbPreviewSource = null
    }
    const lazyRoots: ParentNode[] = []
    window.prksReleaseLazyWorkThumbs = (root) => {
      if (root?.querySelector?.('img[data-prks-thumb-lazy]')) lazyRoots.push(root)
    }
    const pane = owner()
    const el = host()
    presentFolderDetail({
      owner: pane,
      host: el,
      folder: { id: 'f1', title: 'First', works: [{ id: 'w1', file_path: '/api/pdfs/w1.pdf' }], children: [] },
      generation: 1,
    })
    const thumb = el.querySelector('.work-card__thumb')
    expect(thumb).not.toBeNull()
    preview.__prksWorkThumbPreviewSource = thumb
    pane.runCleanup()
    expect(preview.__prksWorkThumbPreviewSource).toBeNull()
    expect(lazyRoots.length).toBeGreaterThan(0)
    expect(el.querySelector('[data-prks-role="folder-detail"]')).toBeNull()
    expect(el.querySelector('img[data-prks-thumb-lazy]')).toBeNull()
  })

  it('dismisses preview when a retained shell paints another folder', async () => {
    const preview = window as Window & { __prksWorkThumbPreviewSource?: Element | null }
    window.prksReleaseWorkThumbPreview = (root) => {
      const src = preview.__prksWorkThumbPreviewSource
      if (!src) return
      if (root && typeof root.contains === 'function' && root.contains(src)) {
        preview.__prksWorkThumbPreviewSource = null
        return
      }
      if (src instanceof Node && !src.isConnected) preview.__prksWorkThumbPreviewSource = null
    }
    window.prksHideWorkThumbPreview = () => {
      preview.__prksWorkThumbPreviewSource = null
    }
    window.prksReleaseLazyWorkThumbs = () => {}
    const pane = owner()
    const el = host()
    presentFolderDetail({
      owner: pane,
      host: el,
      folder: { id: 'f1', title: 'First', works: [{ id: 'w1', file_path: '/api/pdfs/w1.pdf' }], children: [] },
      generation: 1,
    })
    const thumb = el.querySelector('.work-card__thumb')
    expect(thumb).not.toBeNull()
    preview.__prksWorkThumbPreviewSource = thumb
    presentFolderDetail({
      owner: pane,
      host: el,
      folder: { id: 'f2', title: 'Second', works: [{ id: 'w2', file_path: '/api/pdfs/w2.pdf' }], children: [] },
      preserveWorkspace: true,
      generation: 2,
    })
    await nextTick()
    expect(preview.__prksWorkThumbPreviewSource).toBeNull()
    expect(el.querySelector('[data-work-id="w2"]')).not.toBeNull()
    expect(el.querySelector('[data-work-id="w1"]')).toBeNull()
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

  it('rebinds hierarchy nav when its projection changes inside the same generation', async () => {
    let nav = '<nav class="prks-folder-nav" data-prks-role="folder-hierarchy-nav">one</nav>'
    window.prksFolderDetailNavHtml = () => nav
    const commits: string[] = []
    const mounts: string[] = []
    window.prksCommitFolderDetailSurface = () => {
      commits.push('commit')
    }
    window.prksMountFolderHierarchyNav = (_ctx, _folder, container) => {
      mounts.push(container?.querySelector?.('[data-prks-role="folder-hierarchy-nav"]')?.textContent || '')
    }
    const pane = owner()
    const el = host()
    presentFolderDetail({
      owner: pane,
      host: el,
      folder: { id: 'f1', title: 'First', works: [], children: [] },
      generation: 4,
    })
    expect(commits).toEqual(['commit'])
    expect(mounts).toEqual([])
    expect(el.querySelector('[data-prks-role="folder-hierarchy-nav"]')?.textContent).toBe('one')
    nav = '<nav class="prks-folder-nav" data-prks-role="folder-hierarchy-nav">two</nav>'
    presentFolderDetail({
      owner: pane,
      host: el,
      folder: { id: 'f1', title: 'First', works: [], children: [] },
      preserveWorkspace: false,
      generation: 4,
    })
    await nextTick()
    expect(el.querySelector('[data-prks-role="folder-hierarchy-nav"]')?.textContent).toBe('two')
    expect(mounts).toEqual(['two'])
    expect(commits).toEqual(['commit'])
    nav = '<nav class="prks-folder-nav" data-prks-role="folder-hierarchy-nav">three</nav>'
    presentFolderDetail({
      owner: pane,
      host: el,
      folder: { id: 'f1', title: 'Renamed', works: [], children: [] },
      generation: 5,
    })
    await nextTick()
    expect(commits).toEqual(['commit', 'commit'])
    expect(mounts).toEqual(['two'])
    expect(el.querySelector('[data-prks-role="folder-hierarchy-nav"]')?.textContent).toBe('three')
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
    registerFolderDetailBridge(window)
    expect(window.prksVuePresentRoute).toBeTypeOf('function')
    expect((el as HTMLElement & { __prksVueRouteRequest?: unknown }).__prksVueRouteRequest).toBeUndefined()
    expect(el.querySelector('.prks-page-title')?.textContent).toContain('Early')
    expect(decoy.querySelector('[data-prks-folder-detail-view]')).toBeNull()
  })

  it('dismisses one owner and leaves the other mounted', () => {
    registerFolderDetailBridge(window)
    const main = owner()
    const secondary = owner()
    const mainHost = host()
    const secondaryHost = host()
    window.prksVuePresentRoute?.({
      feature: 'folder-detail',
      owner: main,
      host: mainHost,
      folder: { id: 'main', title: 'Main', works: [], children: [] },
      generation: 2,
      shell: true,
    })
    window.prksVuePresentRoute?.({
      feature: 'folder-detail',
      owner: secondary,
      host: secondaryHost,
      folder: { id: 'side', title: 'Side', works: [], children: [] },
      generation: 1,
      shell: false,
    })
    dismissRouteSurface(main)
    expect(mainHost.querySelector('[data-prks-folder-detail-view]')).toBeNull()
    expect(secondaryHost.querySelector('.prks-page-title')?.textContent).toContain('Side')
  })
})
