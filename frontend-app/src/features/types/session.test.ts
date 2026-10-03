import { nextTick } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { dismissRouteSurface, readRouteSurface } from '../../route-surface/lifecycle'
import {
  presentTypeDetail,
  presentTypesIndex,
  registerTypesBridge,
  resetTypesSessionForTests,
} from './session'

afterEach(() => {
  resetTypesSessionForTests()
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
  delete window.prksVuePresentRoute
  delete window.prksVueDismissRoute
  delete window.prksWorkCardHtml
  delete window.prksDocTypeBadgeHtml
  delete window.prksIcon
  delete window.prksWorkBrowseModeToggleHtml
  delete window.prksWorkBrowseCollectionClass
  delete window.prksBindWorkBrowseMode
  delete window.prksReleaseWorkThumbPreview
  delete window.prksReleaseLazyWorkThumbs
  delete window.prksInitLazyWorkThumbs
  delete window.prksRefreshIcons
  delete (window as Window & { __prksWorkThumbPreviewSource?: unknown }).__prksWorkThumbPreviewSource
  delete (window as Window & { prksSyncSidebarActive?: unknown }).prksSyncSidebarActive
})

function host(): HTMLElement {
  const el = document.createElement('div')
  document.body.appendChild(el)
  return el
}

function owner() {
  return {}
}

function stubBadge(): void {
  window.prksDocTypeBadgeHtml = (docType) => `<span class="doc-type-badge" data-type="${docType}">${docType}</span>`
  window.prksIcon = () => '<i data-lucide="chevron-right"></i>'
}

describe('File types route bridge', () => {
  it('renders grouped index rows for each owner and does not publish shell state', () => {
    const calls: string[] = []
    ;(window as Window & { prksSyncSidebarActive?: () => void }).prksSyncSidebarActive = () => {
      calls.push('sidebar')
    }
    stubBadge()
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    const main = owner()
    const secondary = owner()
    const mainHost = host()
    const secondaryHost = host()
    presentTypesIndex({
      owner: main,
      host: mainHost,
      rows: [
        { value: 'book', label: 'Book', count: 2 },
        { value: 'article', label: 'Article', count: 1 },
      ],
      generation: 4,
      shell: true,
    })
    presentTypesIndex({
      owner: secondary,
      host: secondaryHost,
      rows: [{ value: 'misc', label: 'Misc', count: 3 }],
      generation: 1,
      shell: false,
    })
    expect(mainHost.querySelector('.prks-page-title')?.textContent).toBe('File types')
    expect(mainHost.querySelector('[data-prks-route="#/types/book"]')?.textContent).toContain('2 files')
    expect(mainHost.querySelector('[data-type="book"]')).not.toBeNull()
    expect(mainHost.querySelector('[data-prks-route="#/types/misc"]')).toBeNull()
    expect(secondaryHost.querySelector('[data-prks-route="#/types/misc"]')?.textContent).toContain('3 files')
    expect(secondaryHost.querySelector('[data-prks-route="#/types/book"]')).toBeNull()
    expect(readRouteSurface(main)).toMatchObject({
      name: 'types',
      canonicalHash: '#/types',
      ownsMainShell: true,
    })
    expect(readRouteSurface(secondary)?.ownsMainShell).toBe(false)
    expect(calls).toEqual([])
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('paints the empty index and ignores a stale generation', async () => {
    stubBadge()
    const pane = owner()
    const el = host()
    presentTypesIndex({ owner: pane, host: el, rows: [], generation: 2 })
    expect(el.querySelector('.types-page__empty')?.textContent).toContain('No files in library yet')
    dismissRouteSurface(pane)
    presentTypesIndex({
      owner: pane,
      host: el,
      rows: [{ value: 'book', label: 'Book', count: 1 }],
      generation: 2,
    })
    await nextTick()
    expect(el.querySelector('[data-prks-route="#/types/book"]')).toBeNull()
    presentTypesIndex({
      owner: pane,
      host: el,
      rows: [{ value: 'book', label: 'Book', count: 1 }],
      generation: 3,
    })
    expect(el.querySelector('[data-prks-route="#/types/book"]')).not.toBeNull()
    expect(el.querySelector('.types-page__list-stats')?.textContent).toBe('1 file')
  })

  it('registers the types bridge and paints the host that stored the request', () => {
    stubBadge()
    const el = host()
    const decoy = host()
    el.setAttribute('data-prks-vue-route-host', 'true')
    decoy.setAttribute('data-prks-vue-route-host', 'true')
    const pane = owner()
    ;(el as HTMLElement & { __prksVueRouteRequest?: object }).__prksVueRouteRequest = {
      feature: 'types',
      owner: pane,
      host: decoy,
      rows: [{ value: 'online', label: 'Online', count: 4 }],
      generation: 1,
      shell: true,
    }
    registerTypesBridge(window)
    expect(window.prksVuePresentRoute).toBeTypeOf('function')
    expect(window.prksVueDismissRoute).toBeTypeOf('function')
    expect((el as HTMLElement & { __prksVueRouteRequest?: unknown }).__prksVueRouteRequest).toBeUndefined()
    expect(el.querySelector('[data-prks-route="#/types/online"]')).not.toBeNull()
    expect(decoy.querySelector('[data-prks-types-index]')).toBeNull()
    expect(readRouteSurface(pane)?.name).toBe('types')
  })

  it('dismisses one owner and leaves the other mounted', () => {
    stubBadge()
    window.prksWorkCardHtml = (work, options) =>
      `<div data-work-id="${String(work.id)}" data-hide="${options.hideDocTypeBadge ? '1' : '0'}" data-thumb="${options.suppressThumbnail ? 'off' : 'on'}"></div>`
    registerTypesBridge(window)
    const main = owner()
    const secondary = owner()
    const mainHost = host()
    const secondaryHost = host()
    window.prksVuePresentRoute?.({
      feature: 'types',
      owner: main,
      host: mainHost,
      rows: [{ value: 'book', label: 'Book', count: 1 }],
      generation: 2,
      shell: true,
    })
    window.prksVuePresentRoute?.({
      feature: 'type-detail',
      owner: secondary,
      host: secondaryHost,
      docType: 'article',
      label: 'Article',
      canonicalHash: '#/types/article',
      rows: [{ id: 'side', title: 'Side' }],
      offlineCached: false,
      generation: 1,
      shell: false,
    })
    window.prksVueDismissRoute?.(main)
    expect(mainHost.querySelector('[data-prks-types-index]')).toBeNull()
    expect(secondaryHost.querySelector('[data-work-id="side"]')).not.toBeNull()
    expect(readRouteSurface(secondary)).toMatchObject({
      name: 'type-detail',
      canonicalHash: '#/types/article',
      ownsMainShell: false,
    })
  })

  it('paints type detail cards without refetching and suppresses cached thumbnails', () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    stubBadge()
    window.prksWorkBrowseCollectionClass = (extra) => `card-grid ${extra || ''}`.trim()
    window.prksWorkBrowseModeToggleHtml = (id) => `<div data-mode="${id || ''}"></div>`
    window.prksWorkCardHtml = (work, options) =>
      `<div data-work-id="${String(work.id)}" data-hide="${options.hideDocTypeBadge ? '1' : '0'}" data-thumb="${options.suppressThumbnail ? 'off' : 'on'}"></div>`
    const pane = owner()
    const el = host()
    presentTypeDetail({
      owner: pane,
      host: el,
      docType: 'book',
      label: 'Book',
      canonicalHash: '#/types/book',
      rows: [{ id: 'a', title: 'Ada' }],
      offlineCached: true,
      generation: 5,
      shell: true,
    })
    expect(el.querySelector('.prks-page-title')?.textContent).toBe('Files')
    expect(el.querySelector('[data-type="book"]')).not.toBeNull()
    expect(el.querySelector('[data-mode="prks-work-browse-mode-types"]')).not.toBeNull()
    expect(el.querySelector('.types-page__detail-grid')).not.toBeNull()
    const card = el.querySelector('[data-work-id="a"]')
    expect(card?.getAttribute('data-hide')).toBe('1')
    expect(card?.getAttribute('data-thumb')).toBe('off')
    expect(fetchMock).not.toHaveBeenCalled()
    expect(readRouteSurface(pane)?.canonicalHash).toBe('#/types/book')
  })

  it('releases preview and lazy thumbs while they are still under the type detail root', async () => {
    stubBadge()
    window.prksWorkCardHtml = (work, options) => {
      const thumb = options?.suppressThumbnail
        ? ''
        : `<img data-prks-thumb-lazy data-for="${String(work.id)}">`
      return `<div data-work-id="${String(work.id)}">${thumb}</div>`
    }
    const preview = window as Window & { __prksWorkThumbPreviewSource?: Element | null }
    const releaseLog: Array<{ id: string | null; connected: boolean; underDetail: boolean }> = []
    window.prksReleaseWorkThumbPreview = (root) => {
      const src = preview.__prksWorkThumbPreviewSource
      if (!src || !root || typeof root.contains !== 'function' || !root.contains(src)) return
      preview.__prksWorkThumbPreviewSource = null
    }
    window.prksReleaseLazyWorkThumbs = (root) => {
      const img = root?.querySelector?.('img[data-prks-thumb-lazy]') ?? null
      if (!img || !root || typeof root.contains !== 'function' || !root.contains(img)) return
      releaseLog.push({
        id: img.getAttribute('data-for'),
        connected: img.isConnected,
        underDetail: !!img.closest('[data-prks-types-detail]'),
      })
    }
    const inits: string[] = []
    window.prksInitLazyWorkThumbs = (root) => {
      inits.push(root?.querySelector?.('[data-work-id]')?.getAttribute('data-work-id') || '')
    }
    const pane = owner()
    const el = host()
    presentTypeDetail({
      owner: pane,
      host: el,
      docType: 'article',
      label: 'Article',
      rows: [{ id: 'a' }],
      offlineCached: false,
      generation: 1,
    })
    const first = el.querySelector('img[data-prks-thumb-lazy]')
    expect(first?.getAttribute('data-for')).toBe('a')
    expect(inits).toEqual(['a'])
    preview.__prksWorkThumbPreviewSource = first
    presentTypeDetail({
      owner: pane,
      host: el,
      docType: 'article',
      label: 'Article',
      rows: [{ id: 'b' }],
      offlineCached: false,
      generation: 1,
    })
    await nextTick()
    expect(releaseLog).toContainEqual({ id: 'a', connected: true, underDetail: true })
    expect(preview.__prksWorkThumbPreviewSource).toBeNull()
    expect(el.querySelector('[data-work-id="b"]')).not.toBeNull()
    expect(el.querySelector('[data-work-id="a"]')).toBeNull()
    const live = el.querySelector('img[data-prks-thumb-lazy]')
    preview.__prksWorkThumbPreviewSource = live
    dismissRouteSurface(pane)
    expect(releaseLog).toContainEqual({ id: 'b', connected: true, underDetail: true })
    expect(preview.__prksWorkThumbPreviewSource).toBeNull()
    presentTypeDetail({
      owner: pane,
      host: el,
      docType: 'article',
      label: 'Article',
      rows: [{ id: 'c' }],
      offlineCached: true,
      generation: 2,
    })
    expect(inits).toEqual(['a', 'b'])
    expect(el.querySelector('[data-work-id="c"]')?.querySelector('img')).toBeNull()
    expect(el.querySelector('.types-page__empty')).toBeNull()
  })

  it('paints the empty type detail collection', () => {
    stubBadge()
    const pane = owner()
    const el = host()
    presentTypeDetail({
      owner: pane,
      host: el,
      docType: 'misc',
      label: 'Misc',
      rows: [],
      generation: 1,
    })
    expect(el.querySelector('.types-page__empty')?.textContent).toBe('No files in this type yet.')
  })
})
