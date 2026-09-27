import { mount, flushPromises } from '@vue/test-utils'
import { nextTick } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import FolderLibraryRoute from './FolderLibraryRoute.vue'
import { folderLibraryIntentsKey } from './intents'
import { buildFolderLibraryProjection } from './projection'
import { resetFolderLibrarySessionForTests } from './session'
import { FOLDER_LIBRARY_FILTER_KEY, FOLDER_LIBRARY_FILES_FILTER_KEY } from './types'

afterEach(() => {
  resetFolderLibrarySessionForTests()
  document.body.innerHTML = ''
  sessionStorage.clear()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

beforeEach(() => {
  window.prksFolderLibraryTreeInnerHtml = () => '<div class="prks-folder-tree"></div>'
  window.prksFolderLibraryCatalogGlanceParts = () => []
  window.prksPaintFolderLibraryGlance = () => {}
  window.prksScheduleFolderLibraryGlance = () => {}
  window.prksFolderTreeHasCollapsibleNodes = () => false
  window.prksFolderTreeAllCollapsed = () => true
  window.prksFolderLibraryExpandToggleLabel = () => 'Expand all'
  window.prksFolderLibraryExpandToggleInnerHtml = () => '<span></span>'
  window.prksBindFolderOfflineState = () => {}
  window.prksTagSearchIconHtml = () => ''
  window.prksWorkBrowseModeToggleHtml = () => ''
  window.prksBindWorkBrowseMode = () => {}
  window.prksReleaseWorkThumbPreview = () => {}
  window.prksReleaseLazyWorkThumbs = () => {}
  window.prksInitLazyWorkThumbs = () => {}
  window.prksRefreshIcons = () => {}
})

function projection(generation = 1) {
  return buildFolderLibraryProjection({
    availability: 'ready',
    folders: [{ id: 'F1', title: 'Alpha', parent_id: null, work_count: 0, child_count: 0 }],
    generation,
  })
}

describe('Folder Library route lifecycle hardening', () => {
  function mountRoute(opts: {
    contentRoot: HTMLElement
    intents: Record<string, unknown>
    generation?: number
  }) {
    return mount(FolderLibraryRoute, {
      attachTo: opts.contentRoot,
      props: { projection: projection(opts.generation ?? 1), contentRoot: opts.contentRoot },
      global: {
        provide: {
          [folderLibraryIntentsKey as symbol]: opts.intents,
        },
      },
    })
  }

  const baseIntents = {
    createFolder: () => {},
    openWorkModal: () => {},
    navigateFolder: () => {},
    switchTab: () => {},
    toggleExpand: () => {},
    toggleExpandAll: () => {},
    bindFolderOfflineState: () => () => {},
    scheduleGlance: () => {},
    subscribeMetadataOverlay: () => () => {},
  }

  it('resets loading and marks unavailable when Recently Added load rejects', async () => {
    sessionStorage.setItem('prks-folder-library-tab', 'recently-added')
    const load = vi.fn(async () => {
      throw new Error('offline boom')
    })
    const contentRoot = document.createElement('div')
    document.body.appendChild(contentRoot)
    const wrapper = mountRoute({
      contentRoot,
      intents: { ...baseIntents, loadRecentlyAdded: load },
    })
    await flushPromises()
    expect(load).toHaveBeenCalled()
    expect(wrapper.find('#prks-folder-library-recently-added').html()).toContain(
      'not available offline',
    )
    expect(window.__prksFolderDashboardState?.recentlyAddedLoading).toBe(false)
    wrapper.unmount()
  })

  it('invalidates pending debounced folder filter persist on clear', async () => {
    vi.useFakeTimers()
    const contentRoot = document.createElement('div')
    document.body.appendChild(contentRoot)
    const wrapper = mountRoute({
      contentRoot,
      intents: {
        ...baseIntents,
        loadRecentlyAdded: async () => ({
          works: [],
          offlineCached: false,
          unavailable: false,
          generation: null,
          pendingGeneration: null,
          reused: false,
        }),
      },
    })
    await nextTick()
    const input = wrapper.get('#prks-folder-library-search')
    await input.setValue('typed')
    await wrapper.get('#prks-folder-library-search-clear').trigger('click')
    expect(sessionStorage.getItem(FOLDER_LIBRARY_FILTER_KEY)).toBe('')
    await vi.advanceTimersByTimeAsync(200)
    expect(sessionStorage.getItem(FOLDER_LIBRARY_FILTER_KEY)).toBe('')
    wrapper.unmount()
  })

  it('invalidates pending debounced files filter persist on clear', async () => {
    vi.useFakeTimers()
    sessionStorage.setItem('prks-folder-library-tab', 'recently-added')
    const contentRoot = document.createElement('div')
    document.body.appendChild(contentRoot)
    const wrapper = mountRoute({
      contentRoot,
      intents: {
        ...baseIntents,
        loadRecentlyAdded: async () => ({
          works: [{ id: 'W1', title: 'One' }],
          offlineCached: false,
          unavailable: false,
          generation: 1,
          pendingGeneration: null,
          reused: false,
        }),
      },
    })
    await flushPromises()
    const input = wrapper.get('#prks-folder-library-files-search')
    await input.setValue('typed')
    await wrapper.get('#prks-folder-library-files-search-clear').trigger('click')
    expect(sessionStorage.getItem(FOLDER_LIBRARY_FILES_FILTER_KEY)).toBe('')
    await vi.advanceTimersByTimeAsync(200)
    expect(sessionStorage.getItem(FOLDER_LIBRARY_FILES_FILTER_KEY)).toBe('')
    wrapper.unmount()
  })
})
