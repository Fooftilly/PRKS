import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import RecentlyAddedPane from './RecentlyAddedPane.vue'
import { folderLibraryIntentsKey } from './intents'
import { resetFolderLibrarySessionForTests } from './session'
import { releaseWorkThumbResources } from './work-thumb-lifecycle'

afterEach(() => {
  resetFolderLibrarySessionForTests()
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
})

const noopIntents = {
  createFolder: () => {},
  openWorkModal: () => {},
  navigateFolder: () => {},
  switchTab: () => {},
  loadRecentlyAdded: async () => ({
    works: [],
    offlineCached: false,
    unavailable: false,
    generation: null,
    pendingGeneration: null,
    reused: false,
  }),
  toggleExpand: () => {},
  toggleExpandAll: () => {},
  bindFolderOfflineState: () => () => {},
  scheduleGlance: () => {},
  subscribeMetadataOverlay: () => () => {},
}

describe('Folder Library preview lifecycle (#170)', () => {
  it('releases preview and lazy thumbs before replacing recently-added card DOM', async () => {
    const hide = vi.fn()
    const releasePreview = vi.fn()
    const releaseLazy = vi.fn()
    const initLazy = vi.fn()
    window.prksHideWorkThumbPreview = hide
    window.prksReleaseWorkThumbPreview = releasePreview
    window.prksReleaseLazyWorkThumbs = releaseLazy
    window.prksInitLazyWorkThumbs = initLazy
    window.prksWorkBrowseCollectionClass = () => 'card-grid'

    const wrapper = mount(RecentlyAddedPane, {
      props: {
        folders: [],
        filterQuery: '',
        works: [{ id: 'W1', title: 'One' }],
        offlineCached: false,
        unavailable: false,
        loading: false,
        generation: 1,
        overlayRevision: 0,
      },
      global: {
        provide: {
          [folderLibraryIntentsKey as symbol]: noopIntents,
        },
      },
    })
    await nextTick()
    expect(initLazy).toHaveBeenCalled()
    hide.mockClear()
    releasePreview.mockClear()
    releaseLazy.mockClear()
    initLazy.mockClear()

    await wrapper.setProps({
      works: [{ id: 'W1', title: 'One' }, { id: 'W2', title: 'Two' }],
      generation: 2,
    })
    await nextTick()
    // Scoped release only — must not call global hide (other panes may own preview).
    expect(hide).not.toHaveBeenCalled()
    expect(releasePreview).toHaveBeenCalled()
    expect(releaseLazy).toHaveBeenCalled()
    expect(initLazy).toHaveBeenCalled()
  })

  it('scoped release does not call global hide', () => {
    const hide = vi.fn()
    const releasePreview = vi.fn()
    const releaseLazy = vi.fn()
    window.prksHideWorkThumbPreview = hide
    window.prksReleaseWorkThumbPreview = releasePreview
    window.prksReleaseLazyWorkThumbs = releaseLazy
    const root = document.createElement('div')
    releaseWorkThumbResources(root)
    expect(hide).not.toHaveBeenCalled()
    expect(releasePreview).toHaveBeenCalledWith(root)
    expect(releaseLazy).toHaveBeenCalledWith(root)
  })

  it('releases resources on unmount', async () => {
    const releasePreview = vi.fn()
    const releaseLazy = vi.fn()
    window.prksReleaseWorkThumbPreview = releasePreview
    window.prksReleaseLazyWorkThumbs = releaseLazy
    window.prksHideWorkThumbPreview = () => {}
    window.prksWorkBrowseCollectionClass = () => 'card-grid'

    const wrapper = mount(RecentlyAddedPane, {
      props: {
        folders: [],
        filterQuery: '',
        works: [{ id: 'W1', title: 'One' }],
        offlineCached: true,
        unavailable: false,
        loading: false,
        generation: 1,
        overlayRevision: 0,
      },
      global: {
        provide: {
          [folderLibraryIntentsKey as symbol]: noopIntents,
        },
      },
    })
    await nextTick()
    wrapper.unmount()
    expect(releasePreview).toHaveBeenCalled()
    expect(releaseLazy).toHaveBeenCalled()
  })

  it('re-inits lazy thumbs after cached paint so observer prune runs (#170)', async () => {
    const initLazy = vi.fn()
    window.prksHideWorkThumbPreview = () => {}
    window.prksReleaseWorkThumbPreview = () => {}
    window.prksReleaseLazyWorkThumbs = () => {}
    window.prksInitLazyWorkThumbs = initLazy
    window.prksWorkBrowseCollectionClass = () => 'card-grid'

    mount(RecentlyAddedPane, {
      props: {
        folders: [],
        filterQuery: '',
        works: [{ id: 'W1', title: 'One' }],
        offlineCached: true,
        unavailable: false,
        loading: false,
        generation: 1,
        overlayRevision: 0,
      },
      global: {
        provide: {
          [folderLibraryIntentsKey as symbol]: noopIntents,
        },
      },
    })
    await nextTick()
    expect(initLazy).toHaveBeenCalled()
  })

  it('keeps the pane empty while the first Recently Added fetch is in flight', async () => {
    window.prksReleaseWorkThumbPreview = () => {}
    window.prksReleaseLazyWorkThumbs = () => {}
    window.prksInitLazyWorkThumbs = () => {}
    window.prksWorkBrowseCollectionClass = () => 'card-grid'

    const wrapper = mount(RecentlyAddedPane, {
      props: {
        folders: [],
        filterQuery: '',
        works: null,
        offlineCached: false,
        unavailable: false,
        loading: true,
        generation: 1,
        overlayRevision: 0,
      },
      global: {
        provide: {
          [folderLibraryIntentsKey as symbol]: noopIntents,
        },
      },
    })
    await nextTick()
    const pane = wrapper.find('#prks-folder-library-recently-added')
    expect(pane.element.children.length).toBe(0)
    expect(pane.text()).toBe('')
  })

  it('repaints when overlayRevision bumps after metadata edits', async () => {
    const releasePreview = vi.fn()
    const initLazy = vi.fn()
    window.prksReleaseWorkThumbPreview = releasePreview
    window.prksReleaseLazyWorkThumbs = () => {}
    window.prksInitLazyWorkThumbs = initLazy
    window.prksWorkBrowseCollectionClass = () => 'card-grid'
    let overlayTitle = 'One'
    window.prksEffectiveProjectionRows = (rows) =>
      rows.map((r) => ({ ...(r as object), title: overlayTitle }))

    const wrapper = mount(RecentlyAddedPane, {
      props: {
        folders: [],
        filterQuery: '',
        works: [{ id: 'W1', title: 'One' }],
        offlineCached: false,
        unavailable: false,
        loading: false,
        generation: 1,
        overlayRevision: 0,
      },
      global: {
        provide: {
          [folderLibraryIntentsKey as symbol]: noopIntents,
        },
      },
    })
    await nextTick()
    releasePreview.mockClear()
    initLazy.mockClear()
    overlayTitle = 'Overlay Title'
    await wrapper.setProps({ overlayRevision: 1 })
    await nextTick()
    expect(releasePreview).toHaveBeenCalled()
    expect(initLazy).toHaveBeenCalled()
    expect(wrapper.find('#prks-folder-library-recently-added').html()).toContain('Overlay Title')
  })
})
