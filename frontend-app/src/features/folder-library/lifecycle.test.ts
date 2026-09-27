import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import RecentlyAddedPane from './RecentlyAddedPane.vue'
import { folderLibraryIntentsKey } from './intents'
import { resetFolderLibrarySessionForTests } from './session'

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
  loadRecentlyAdded: async () => ({ works: [], offlineCached: false, unavailable: false }),
  toggleExpand: () => {},
  toggleExpandAll: () => {},
  bindFolderOfflineState: () => () => {},
  scheduleGlance: () => {},
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
    window.prksWorkCardHtml = () => '<article class="work-card">card</article>'
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
    expect(hide).toHaveBeenCalled()
    expect(releasePreview).toHaveBeenCalled()
    expect(releaseLazy).toHaveBeenCalled()
    expect(initLazy).toHaveBeenCalled()
  })

  it('releases resources on unmount', async () => {
    const releasePreview = vi.fn()
    const releaseLazy = vi.fn()
    window.prksReleaseWorkThumbPreview = releasePreview
    window.prksReleaseLazyWorkThumbs = releaseLazy
    window.prksHideWorkThumbPreview = () => {}
    window.prksWorkCardHtml = () => '<article class="work-card">card</article>'
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
    window.prksWorkCardHtml = () => '<article class="work-card">card</article>'
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
})
