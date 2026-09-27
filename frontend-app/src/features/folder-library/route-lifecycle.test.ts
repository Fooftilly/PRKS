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

function projection(
  generation = 1,
  folders: Array<{
    id: string
    title: string
    parent_id: string | null
    work_count: number
    child_count: number
  }> = [{ id: 'F1', title: 'Alpha', parent_id: null, work_count: 0, child_count: 0 }],
) {
  return buildFolderLibraryProjection({
    availability: 'ready',
    folders,
    generation,
  })
}

describe('Folder Library route lifecycle hardening', () => {
  function mountRoute(opts: {
    contentRoot: HTMLElement
    intents: Record<string, unknown>
    generation?: number
    folders?: Array<{
      id: string
      title: string
      parent_id: string | null
      work_count: number
      child_count: number
    }>
  }) {
    return mount(FolderLibraryRoute, {
      attachTo: opts.contentRoot,
      props: {
        projection: projection(opts.generation ?? 1, opts.folders),
        contentRoot: opts.contentRoot,
      },
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

  it('same-tab Recently Added switch still awaits load via bridge', async () => {
    sessionStorage.setItem('prks-folder-library-tab', 'recently-added')
    window.prksWorkCardHtml = (w) =>
      `<article data-work-id="${String((w as { id?: string }).id || '')}">${String((w as { title?: string }).title || '')}</article>`
    window.prksWorkBrowseCollectionClass = () => 'card-grid'
    let loads = 0
    const secondGate = (() => {
      let release!: () => void
      const promise = new Promise<void>((resolve) => {
        release = () => resolve()
      })
      return { promise, release: () => release() }
    })()
    const load = vi.fn(async () => {
      loads += 1
      if (loads > 1) await secondGate.promise
      return {
        works: [{ id: 'W1', title: loads === 1 ? 'First' : 'Second' }],
        offlineCached: false,
        unavailable: false,
        generation: loads,
        pendingGeneration: null,
        reused: false,
      }
    })
    const contentRoot = document.createElement('div')
    document.body.appendChild(contentRoot)
    const wrapper = mountRoute({
      contentRoot,
      intents: { ...baseIntents, loadRecentlyAdded: load },
    })
    await flushPromises()
    expect(load).toHaveBeenCalledTimes(1)
    const switchTab = window.__prksFolderDashboardState?.switchTab
    expect(typeof switchTab).toBe('function')
    const switchPromise = switchTab?.('recently-added')
    expect(switchPromise).toBeInstanceOf(Promise)
    let settled = false
    void switchPromise?.then(() => {
      settled = true
    })
    await flushPromises()
    expect(load).toHaveBeenCalledTimes(2)
    expect(settled).toBe(false)
    expect(wrapper.find('#prks-folder-library-recently-added').html()).not.toContain('Second')
    secondGate.release()
    await switchPromise
    expect(settled).toBe(true)
    expect(wrapper.find('#prks-folder-library-recently-added').html()).toContain('Second')
    wrapper.unmount()
  })

  it('two owners keep independent glance rows after both schedules settle', async () => {
    const owners = new Map<object, { folders: unknown[]; recentlyAddedWorks: unknown[] | null }>()
    window.prksPublishFolderDashboardState = (state) => {
      if (state?.container) owners.set(state.container, state as never)
      window.__prksFolderDashboardState = state
    }
    window.prksUnpublishFolderDashboardState = (container) => {
      owners.delete(container)
      if (window.__prksFolderDashboardState?.container === container) {
        const next = owners.values().next()
        window.__prksFolderDashboardState = next.done ? undefined : (next.value as never)
      }
    }
    window.prksFolderLibraryCatalogGlanceParts = (folders) => {
      const list = Array.isArray(folders) ? folders : []
      return list.length ? [`${list.length} folders`] : []
    }
    window.prksPaintFolderLibraryGlance = (host, parts) => {
      const texts = (Array.isArray(parts) ? parts : [])
        .map((p) => (typeof p === 'string' ? p : String((p as { text?: string })?.text || '')))
        .filter(Boolean)
      host.textContent = texts.join(' · ')
    }

    const settled: Array<{ rootId: string; folders: unknown[]; ra: unknown[] | null }> = []
    const scheduleGlance = vi.fn(async (root: HTMLElement | null, options?: {
      folders?: readonly unknown[]
      recentlyAddedWorks?: unknown[] | null
    }) => {
      const host = root?.querySelector(
        '[data-prks-role="folder-library-glance-host"]',
      ) as HTMLElement | null
      if (!host || !root) return
      const token = `${Date.now()}-${Math.random()}`
      host.dataset.glanceToken = token
      await new Promise((r) => setTimeout(r, 15))
      if (!host.isConnected || host.dataset.glanceToken !== token) return
      const folders = options?.folders ? [...options.folders] : []
      const ra = options && 'recentlyAddedWorks' in (options || {})
        ? (options.recentlyAddedWorks ?? null)
        : null
      const parts = [
        ...(window.prksFolderLibraryCatalogGlanceParts?.(folders) || []),
        Array.isArray(ra) && ra.length ? `${ra.length} recently added` : null,
      ].filter(Boolean)
      window.prksPaintFolderLibraryGlance?.(host, parts)
      settled.push({ rootId: root.id, folders, ra })
    })

    const main = document.createElement('div')
    main.id = 'main-folders'
    const secondary = document.createElement('div')
    secondary.id = 'secondary-folders'
    document.body.append(main, secondary)

    const mainWrapper = mountRoute({
      contentRoot: main,
      folders: [
        { id: 'FA', title: 'MainOnly', parent_id: null, work_count: 0, child_count: 0 },
      ],
      intents: {
        ...baseIntents,
        scheduleGlance,
        loadRecentlyAdded: async () => ({
          works: [{ id: 'WM', title: 'MainWork' }],
          offlineCached: false,
          unavailable: false,
          generation: 1,
          pendingGeneration: null,
          reused: false,
        }),
      },
    })
    const secondaryWrapper = mountRoute({
      contentRoot: secondary,
      folders: [
        { id: 'FB', title: 'SecondaryA', parent_id: null, work_count: 0, child_count: 0 },
        { id: 'FC', title: 'SecondaryB', parent_id: null, work_count: 0, child_count: 0 },
      ],
      intents: {
        ...baseIntents,
        scheduleGlance,
        loadRecentlyAdded: async () => ({
          works: [
            { id: 'WS1', title: 'Sec1' },
            { id: 'WS2', title: 'Sec2' },
          ],
          offlineCached: false,
          unavailable: false,
          generation: 1,
          pendingGeneration: null,
          reused: false,
        }),
      },
    })
    await flushPromises()
    await vi.waitFor(() => expect(settled.length).toBeGreaterThanOrEqual(2))

    const mainHost = main.querySelector('[data-prks-role="folder-library-glance-host"]')
    const secondaryHost = secondary.querySelector(
      '[data-prks-role="folder-library-glance-host"]',
    )
    expect(mainHost?.textContent).toContain('1 folders')
    expect(secondaryHost?.textContent).toContain('2 folders')
    expect(mainHost?.textContent).not.toContain('2 folders')
    expect(secondaryHost?.textContent).not.toContain('1 folders')

    secondaryWrapper.unmount()
    expect(window.__prksFolderDashboardState?.container).toBe(main)
    expect(owners.has(main)).toBe(true)
    expect(owners.has(secondary)).toBe(false)

    mainWrapper.unmount()
    expect(window.__prksFolderDashboardState).toBeUndefined()
  })
})
