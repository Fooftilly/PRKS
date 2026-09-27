import { nextTick } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { readRouteSurface } from '../../route-surface/lifecycle'
import {
  dismissFolderLibrary,
  FOLDER_LIBRARY_RETAIN_SURFACE_KEY,
  presentFolderLibrary,
  registerFolderLibraryBridge,
  resetFolderLibrarySessionForTests,
} from './session'

afterEach(() => {
  resetFolderLibrarySessionForTests()
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
  delete window.prksVuePresentFolderLibrary
  delete window.prksVueDismissFolderLibrary
  delete window.prksFolderLibraryTreeInnerHtml
  delete window.prksFolderTreeHasCollapsibleNodes
  delete window.prksFolderLibraryExpandToggleLabel
  delete window.prksFolderLibraryExpandToggleInnerHtml
  delete window.prksFolderTreeAllCollapsed
  delete window.prksPageSummaryHtml
  delete window.prksTagSearchIconHtml
  delete window.prksWorkBrowseModeToggleHtml
  delete window.prksWorkBrowseCollectionClass
  delete window.prksBindWorkBrowseMode
  delete window.prksRefreshIcons
  delete window.prksReleaseWorkThumbPreview
  delete window.prksReleaseLazyWorkThumbs
  delete window.prksInitLazyWorkThumbs
  delete window.prksCollectFolderLibraryGlanceExtras
  window.__prksFolderDashboardState = null
})

function host(): HTMLElement {
  const el = document.createElement('div')
  document.body.appendChild(el)
  return el
}

function owner(tabId = 'tab') {
  return { tabId, isCurrent: () => true, root: null as HTMLElement | null }
}

function stubTreeApis(): void {
  window.prksFolderLibraryTreeInnerHtml = () =>
    '<div class="prks-folder-tree" role="tree"><div data-folder-id="F1">Alpha</div></div>'
  window.prksFolderTreeHasCollapsibleNodes = () => false
  window.prksFolderLibraryExpandToggleLabel = () => 'Expand all'
  window.prksFolderLibraryExpandToggleInnerHtml = () => '<span class="ribbon-btn__icon">▾</span>'
  window.prksFolderTreeAllCollapsed = () => true
  window.prksPageSummaryHtml = () => '<div class="prks-page-summary"></div>'
  window.prksTagSearchIconHtml = () => ''
  window.prksWorkBrowseModeToggleHtml = () => ''
  window.prksWorkBrowseCollectionClass = () => 'prks-folder-library__grid card-grid'
  window.prksBindWorkBrowseMode = () => {}
  window.prksRefreshIcons = () => {}
  window.prksCollectFolderLibraryGlanceExtras = async () => []
}

describe('Folder Library route bridge', () => {
  it('renders the library shell without fetch', () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    stubTreeApis()
    const pane = owner('main')
    const el = host()
    pane.root = el
    presentFolderLibrary({
      owner: pane,
      host: el,
      folders: [{ id: 'F1', title: 'Alpha', parent_id: null, child_count: 0, work_count: 1 }],
      generation: 2,
      shell: true,
    })
    expect(el.querySelector('[data-prks-folder-library-view]')).not.toBeNull()
    expect(el.querySelector('.prks-page-title')?.textContent).toContain('Folder Library')
    expect(el.querySelector('#prks-folder-library-search')).not.toBeNull()
    expect(readRouteSurface(pane)).toMatchObject({
      name: 'folders',
      canonicalHash: '#/folders',
      ownsMainShell: true,
    })
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('does not repaint a dismissed generation', async () => {
    stubTreeApis()
    const pane = owner()
    const el = host()
    presentFolderLibrary({
      owner: pane,
      host: el,
      folders: [{ id: 'a', title: 'A', parent_id: null, child_count: 0, work_count: 0 }],
      generation: 2,
    })
    dismissFolderLibrary(pane)
    expect(el.querySelector('[data-prks-folder-library-view]')).toBeNull()
    presentFolderLibrary({
      owner: pane,
      host: el,
      folders: [{ id: 'b', title: 'B', parent_id: null, child_count: 0, work_count: 0 }],
      generation: 2,
    })
    await nextTick()
    expect(el.textContent).not.toContain('B')
    presentFolderLibrary({
      owner: pane,
      host: el,
      folders: [{ id: 'b', title: 'B', parent_id: null, child_count: 0, work_count: 0 }],
      generation: 3,
    })
    expect(el.querySelector('[data-prks-folder-library-view]')).not.toBeNull()
  })

  it('registers bridges and applies host-local early requests', () => {
    stubTreeApis()
    const el = host()
    const decoy = host()
    el.setAttribute('data-prks-vue-route-host', 'true')
    decoy.setAttribute('data-prks-vue-route-host', 'true')
    const pane = owner()
    ;(el as HTMLElement & { __prksVueRouteRequest?: object }).__prksVueRouteRequest = {
      feature: 'folders',
      owner: pane,
      host: decoy,
      folders: [{ id: 'early', title: 'Early', parent_id: null, child_count: 0, work_count: 0 }],
      generation: 1,
      shell: true,
    }
    registerFolderLibraryBridge(window)
    expect(window.prksVuePresentFolderLibrary).toBeTypeOf('function')
    expect(el.querySelector('[data-prks-folder-library-view]')).not.toBeNull()
    expect(decoy.querySelector('[data-prks-folder-library-view]')).toBeNull()
  })

  it('keeps local tab/filter across retain + same-host refresh', async () => {
    stubTreeApis()
    const cleanups = new Set<() => void>()
    const pane: {
      tabId: string
      isCurrent: () => boolean
      root: HTMLElement | null
      registerCleanup: (fn: () => void) => () => void
      [FOLDER_LIBRARY_RETAIN_SURFACE_KEY]?: boolean
    } = {
      tabId: 'coord',
      isCurrent: () => true,
      root: null,
      registerCleanup(fn: () => void) {
        cleanups.add(fn)
        return () => {
          cleanups.delete(fn)
        }
      },
    }
    const contentDiv = document.createElement('div')
    document.body.appendChild(contentDiv)
    pane.root = contentDiv
    const routeHost = document.createElement('div')
    routeHost.setAttribute('data-prks-vue-route-host', 'true')
    contentDiv.appendChild(routeHost)
    presentFolderLibrary({
      owner: pane,
      host: routeHost,
      folders: [{ id: 'F1', title: 'Alpha', parent_id: null, child_count: 0, work_count: 0 }],
      generation: 1,
    })
    const search = routeHost.querySelector<HTMLInputElement>('#prks-folder-library-search')
    expect(search).not.toBeNull()
    search!.value = 'alp'
    search!.dispatchEvent(new Event('input'))
    await nextTick()

    pane[FOLDER_LIBRARY_RETAIN_SURFACE_KEY] = true
    const beginRouteCleanups = Array.from(cleanups)
    cleanups.clear()
    beginRouteCleanups.forEach((fn) => fn())
    pane[FOLDER_LIBRARY_RETAIN_SURFACE_KEY] = false

    expect(routeHost.querySelector('[data-prks-folder-library-view]')).not.toBeNull()
    presentFolderLibrary({
      owner: pane,
      host: routeHost,
      folders: [
        { id: 'F1', title: 'Alpha', parent_id: null, child_count: 0, work_count: 0 },
        { id: 'F2', title: 'Beta', parent_id: null, child_count: 0, work_count: 0 },
      ],
      generation: 2,
    })
    await nextTick()
    expect(routeHost.querySelector<HTMLInputElement>('#prks-folder-library-search')?.value).toBe(
      'alp',
    )

    const leaveCleanups = Array.from(cleanups)
    cleanups.clear()
    leaveCleanups.forEach((fn) => fn())
    expect(routeHost.querySelector('[data-prks-folder-library-view]')).toBeNull()
  })

  it('releases browse resources on dismiss and on Folders↔Recently added switch', async () => {
    stubTreeApis()
    const releases: string[] = []
    window.prksReleaseWorkThumbPreview = (root) => {
      releases.push(`preview:${root ? 'root' : 'null'}`)
    }
    window.prksReleaseLazyWorkThumbs = (root) => {
      releases.push(`thumbs:${root ? 'root' : 'null'}`)
    }
    window.prksInitLazyWorkThumbs = () => {
      releases.push('init')
    }
    window.prksWorkCardHtml = (work) => `<div data-work-id="${String(work.id)}"></div>`
    window.prksOfflineDomainGeneration = () => 1
    window.prksPendingWorkMetadataGeneration = () => 1
    window.prksRefreshPendingWorkMetadata = async () => {}
    window.prksOfflineRecentlyAddedFetch = async () => ({ source: 'network' })
    window.prksResolveOfflineRecentlyAdded = () => [
      { id: 'W1', title: 'Work', created_at: '2026-09-05T00:00:00.000Z' },
    ]

    const pane = owner('lifecycle')
    const el = host()
    pane.root = el
    presentFolderLibrary({
      owner: pane,
      host: el,
      folders: [{ id: 'F1', title: 'Alpha', parent_id: null, child_count: 0, work_count: 0 }],
      generation: 1,
    })
    await nextTick()
    const recentTab = el.querySelector<HTMLButtonElement>(
      '.prks-folder-library__tab-btn[data-tab="recently-added"]',
    )
    expect(recentTab).not.toBeNull()
    recentTab!.click()
    await nextTick()
    await new Promise((r) => setTimeout(r, 0))
    expect(el.querySelector('#prks-folder-library-recently-added [data-work-id="W1"]')).not.toBeNull()
    expect(releases.some((x) => x.startsWith('preview:'))).toBe(true)
    expect(releases.some((x) => x.startsWith('thumbs:'))).toBe(true)

    const foldersTab = el.querySelector<HTMLButtonElement>(
      '.prks-folder-library__tab-btn[data-tab="folders"]',
    )
    const beforeSwitch = releases.length
    foldersTab!.click()
    await nextTick()
    expect(releases.length).toBeGreaterThan(beforeSwitch)

    dismissFolderLibrary(pane)
    expect(el.querySelector('[data-prks-folder-library-view]')).toBeNull()
    expect(releases.filter((x) => x.startsWith('preview:')).length).toBeGreaterThanOrEqual(2)
  })

  it('paints unavailable without fetch and leaves sibling banners alone', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    stubTreeApis()
    const pane = owner('banner')
    const contentDiv = document.createElement('div')
    document.body.appendChild(contentDiv)
    const banner = document.createElement('div')
    banner.setAttribute('data-prks-role', 'offline-provenance-banner')
    contentDiv.appendChild(banner)
    const routeHost = document.createElement('div')
    routeHost.setAttribute('data-prks-vue-route-host', 'true')
    contentDiv.appendChild(routeHost)
    presentFolderLibrary({
      owner: pane,
      host: routeHost,
      availability: 'unavailable',
      generation: 1,
    })
    await nextTick()
    expect(routeHost.querySelector('[data-prks-role="offline-unavailable"]')).not.toBeNull()
    expect(contentDiv.querySelectorAll('[data-prks-role="offline-provenance-banner"]').length).toBe(
      1,
    )
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('unmounts on dismiss without affecting another owner', () => {
    stubTreeApis()
    const a = owner('a')
    const b = owner('b')
    const aHost = host()
    const bHost = host()
    presentFolderLibrary({
      owner: a,
      host: aHost,
      folders: [{ id: '1', title: 'Alpha', parent_id: null, child_count: 0, work_count: 0 }],
      generation: 1,
    })
    presentFolderLibrary({
      owner: b,
      host: bHost,
      folders: [{ id: '2', title: 'Beta', parent_id: null, child_count: 0, work_count: 0 }],
      generation: 1,
    })
    dismissFolderLibrary(a)
    expect(aHost.querySelector('[data-prks-folder-library-view]')).toBeNull()
    expect(bHost.querySelector('[data-prks-folder-library-view]')).not.toBeNull()
  })
})
