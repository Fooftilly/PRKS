import { nextTick } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { readRouteSurface } from '../../route-surface/lifecycle'
import {
  FOLDER_LIBRARY_RETAIN_SURFACE_KEY,
  dismissFolderLibrary,
  presentFolderLibrary,
  registerFolderLibraryBridge,
  resetFolderLibrarySessionForTests,
} from './session'

afterEach(() => {
  resetFolderLibrarySessionForTests()
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
  delete window.prksVuePresentRoute
  delete window.prksVueDismissFolderLibrary
  delete window.prksFolderLibraryTreeInnerHtml
  delete window.prksFolderLibraryCatalogGlanceParts
  delete window.prksPaintFolderLibraryGlance
  delete window.prksScheduleFolderLibraryGlance
  delete window.prksFolderTreeHasCollapsibleNodes
  delete window.prksFolderTreeAllCollapsed
  delete window.prksBindFolderOfflineState
  delete window.prksOfflineRecentlyAddedFetch
  delete window.prksResolveOfflineRecentlyAdded
})

function host(): HTMLElement {
  const el = document.createElement('div')
  document.body.appendChild(el)
  return el
}

function owner(tabId = 'tab') {
  return { tabId, isCurrent: () => true }
}

function stubFolderLibraryWindow(): void {
  window.prksFolderLibraryTreeInnerHtml = () =>
    '<div class="prks-folder-tree" role="tree"><span data-test="tree">tree</span></div>'
  window.prksFolderLibraryCatalogGlanceParts = () => ['1 folder']
  window.prksPaintFolderLibraryGlance = (el, parts) => {
    el.textContent = (parts as string[]).join(' · ')
  }
  window.prksScheduleFolderLibraryGlance = () => {}
  window.prksFolderTreeHasCollapsibleNodes = () => false
  window.prksFolderTreeAllCollapsed = () => false
  window.prksBindFolderOfflineState = () => {}
  window.prksOfflineRecentlyAddedFetch = async () => ({ source: 'network', value: [] })
  window.prksResolveOfflineRecentlyAdded = () => []
}

describe('Folder Library route bridge', () => {
  it('renders independent Main and Secondary owners', () => {
    stubFolderLibraryWindow()
    const main = owner('main')
    const secondary = owner('side')
    const mainHost = host()
    const secondaryHost = host()
    presentFolderLibrary({
      owner: main,
      host: mainHost,
      folders: [{ id: 'F1', title: 'Main folder', parent_id: null, work_count: 0, child_count: 0 }],
      generation: 2,
      shell: true,
    })
    presentFolderLibrary({
      owner: secondary,
      host: secondaryHost,
      folders: [{ id: 'F2', title: 'Side folder', parent_id: null, work_count: 0, child_count: 0 }],
      generation: 1,
      shell: false,
    })
    expect(mainHost.querySelector('[data-prks-folder-library-view]')).not.toBeNull()
    expect(mainHost.textContent).toContain('Folder Library')
    expect(mainHost.textContent).toContain('tree')
    expect(secondaryHost.textContent).toContain('tree')
    expect(readRouteSurface(main)).toMatchObject({
      name: 'folders',
      canonicalHash: '#/folders',
      ownsMainShell: true,
    })
    expect(readRouteSurface(secondary)?.ownsMainShell).toBe(false)
  })

  it('registers bridges and applies host-local early requests', () => {
    stubFolderLibraryWindow()
    const el = host()
    el.setAttribute('data-prks-vue-route-host', 'true')
    const pane = owner()
    ;(el as HTMLElement & { __prksVueRouteRequest?: object }).__prksVueRouteRequest = {
      feature: 'folder-library',
      owner: pane,
      folders: [{ id: 'F1', title: 'Early', parent_id: null, work_count: 0, child_count: 0 }],
      generation: 1,
      shell: true,
    }
    registerFolderLibraryBridge(window)
    expect(window.prksVuePresentRoute).toBeTypeOf('function')
    expect(el.textContent).toContain('Folder Library')
  })

  function beginRouteCleanups(cleanups: Set<() => void>): void {
    // Mirror tab-context: drain the set before invoking so re-arms during a
    // retain no-op survive for the next leave / failed refresh.
    const fns = Array.from(cleanups)
    cleanups.clear()
    fns.forEach((fn) => fn())
  }

  it('keeps folder filter across production-style retain refresh', async () => {
    stubFolderLibraryWindow()
    const cleanups = new Set<() => void>()
    const pane: {
      tabId: string
      isCurrent: () => boolean
      registerCleanup: (fn: () => void) => () => void
      [FOLDER_LIBRARY_RETAIN_SURFACE_KEY]?: boolean
    } = {
      tabId: 'coord',
      isCurrent: () => true,
      registerCleanup(fn: () => void) {
        cleanups.add(fn)
        return () => {
          cleanups.delete(fn)
        }
      },
    }
    const contentDiv = document.createElement('div')
    document.body.appendChild(contentDiv)
    const routeHost = document.createElement('div')
    routeHost.setAttribute('data-prks-vue-route-host', 'true')
    contentDiv.appendChild(routeHost)
    presentFolderLibrary({
      owner: pane,
      host: routeHost,
      contentRoot: contentDiv,
      folders: [{ id: 'F1', title: 'Alpha', parent_id: null, work_count: 0, child_count: 0 }],
      generation: 1,
    })
    const search = routeHost.querySelector<HTMLInputElement>('#prks-folder-library-search')
    expect(search).not.toBeNull()
    search!.value = 'alp'
    search!.dispatchEvent(new Event('input'))
    await nextTick()
    pane[FOLDER_LIBRARY_RETAIN_SURFACE_KEY] = true
    beginRouteCleanups(cleanups)
    expect(cleanups.size).toBeGreaterThan(0)
    pane[FOLDER_LIBRARY_RETAIN_SURFACE_KEY] = false
    presentFolderLibrary({
      owner: pane,
      host: routeHost,
      contentRoot: contentDiv,
      folders: [
        { id: 'F1', title: 'Alpha', parent_id: null, work_count: 0, child_count: 0 },
        { id: 'F2', title: 'Beta', parent_id: null, work_count: 0, child_count: 0 },
      ],
      generation: 2,
    })
    await nextTick()
    expect(routeHost.querySelector<HTMLInputElement>('#prks-folder-library-search')?.value).toBe('alp')
    beginRouteCleanups(cleanups)
    expect(routeHost.querySelector('[data-prks-folder-library-view]')).toBeNull()
  })

  it('re-armed cleanup dismisses retained surface after failed same-route refresh', async () => {
    stubFolderLibraryWindow()
    const cleanups = new Set<() => void>()
    const pane: {
      tabId: string
      isCurrent: () => boolean
      registerCleanup: (fn: () => void) => () => void
      [FOLDER_LIBRARY_RETAIN_SURFACE_KEY]?: boolean
    } = {
      tabId: 'coord',
      isCurrent: () => true,
      registerCleanup(fn: () => void) {
        cleanups.add(fn)
        return () => {
          cleanups.delete(fn)
        }
      },
    }
    const contentDiv = document.createElement('div')
    document.body.appendChild(contentDiv)
    const routeHost = document.createElement('div')
    routeHost.setAttribute('data-prks-vue-route-host', 'true')
    contentDiv.appendChild(routeHost)
    presentFolderLibrary({
      owner: pane,
      host: routeHost,
      contentRoot: contentDiv,
      folders: [{ id: 'F1', title: 'Alpha', parent_id: null, work_count: 0, child_count: 0 }],
      generation: 1,
    })
    expect(routeHost.querySelector('[data-prks-folder-library-view]')).not.toBeNull()
    // Same-route refresh: retain cleanup runs, then load fails before present.
    pane[FOLDER_LIBRARY_RETAIN_SURFACE_KEY] = true
    beginRouteCleanups(cleanups)
    pane[FOLDER_LIBRARY_RETAIN_SURFACE_KEY] = false
    // No presentFolderLibrary — simulate offline/effective throw.
    // Tab leave / route leave must still tear down the retained Vue tree.
    beginRouteCleanups(cleanups)
    expect(routeHost.querySelector('[data-prks-folder-library-view]')).toBeNull()
  })

  it('unmounts on dismiss without affecting another owner', () => {
    stubFolderLibraryWindow()
    const a = owner('a')
    const b = owner('b')
    const aHost = host()
    const bHost = host()
    presentFolderLibrary({
      owner: a,
      host: aHost,
      folders: [{ id: 'F1', title: 'A', parent_id: null, work_count: 0, child_count: 0 }],
      generation: 1,
    })
    presentFolderLibrary({
      owner: b,
      host: bHost,
      folders: [{ id: 'F2', title: 'B', parent_id: null, work_count: 0, child_count: 0 }],
      generation: 1,
    })
    dismissFolderLibrary(a)
    expect(aHost.querySelector('[data-prks-folder-library-view]')).toBeNull()
    expect(bHost.querySelector('[data-prks-folder-library-view]')).not.toBeNull()
  })
})
