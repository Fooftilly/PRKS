import { nextTick } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { readRouteSurface } from '../../route-surface/lifecycle'
import { dismissRecent, presentRecent, registerRecentBridge, resetRecentSessionForTests } from './session'

afterEach(() => {
  resetRecentSessionForTests()
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
  delete window.prksVuePresentRoute
  delete window.prksVueDismissRecent
  delete window.prksWorkCardHtml
  delete window.prksWorkBrowseModeToggleHtml
  delete window.prksPageHeaderIconHtml
  delete window.prksReleaseWorkThumbPreview
  delete window.prksReleaseLazyWorkThumbs
  delete window.prksInitLazyWorkThumbs
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

describe('Recent route bridge', () => {
  it('renders effective rows for each owner and does not publish shell state', () => {
    const calls: string[] = []
    ;(window as Window & { prksSyncSidebarActive?: () => void }).prksSyncSidebarActive = () => {
      calls.push('sidebar')
    }
    window.prksWorkCardHtml = (work, options) =>
      `<div data-work-id="${String(work.id)}" data-sub="${options.subtitle || ''}" data-thumb="${options.suppressThumbnail ? 'off' : 'on'}"></div>`
    const main = owner()
    const secondary = owner()
    const mainHost = host()
    const secondaryHost = host()
    presentRecent({
      owner: main,
      host: mainHost,
      rows: [{ id: 'main', last_opened_at: '2020-01-02T00:00:00.000Z' }],
      offlineCached: true,
      generation: 4,
      shell: true,
    })
    presentRecent({
      owner: secondary,
      host: secondaryHost,
      rows: [{ id: 'side', last_opened_at: '' }],
      offlineCached: false,
      generation: 1,
      shell: false,
    })
    expect(mainHost.querySelector('.prks-page-title')?.textContent).toContain('Recently Opened')
    expect(mainHost.querySelector('[data-work-id="main"]')?.getAttribute('data-thumb')).toBe('off')
    expect(mainHost.querySelector('[data-work-id="main"]')?.getAttribute('data-sub')).toContain('Last opened: ')
    expect(secondaryHost.querySelector('[data-work-id="side"]')?.getAttribute('data-thumb')).toBe('on')
    expect(secondaryHost.querySelector('[data-work-id="side"]')?.getAttribute('data-sub')).toBe('Last opened: Unknown')
    expect(mainHost.querySelector('[data-work-id="side"]')).toBeNull()
    expect(readRouteSurface(main)).toMatchObject({
      name: 'recent',
      canonicalHash: '#/recent',
      ownsMainShell: true,
    })
    expect(readRouteSurface(secondary)?.ownsMainShell).toBe(false)
    expect(calls).toEqual([])
  })

  it('paints the empty collection and ignores a stale generation', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    window.prksWorkCardHtml = (work) => `<div data-work-id="${String(work.id)}"></div>`
    const pane = owner()
    const el = host()
    presentRecent({ owner: pane, host: el, rows: [], generation: 2 })
    expect(el.querySelector('.prks-inline-message')?.textContent).toBe('No recently opened documents found.')
    expect(fetchMock).not.toHaveBeenCalled()
    dismissRecent(pane)
    presentRecent({
      owner: pane,
      host: el,
      rows: [{ id: 'b' }],
      generation: 2,
    })
    await nextTick()
    expect(el.querySelector('[data-work-id="b"]')).toBeNull()
    presentRecent({
      owner: pane,
      host: el,
      rows: [{ id: 'b' }],
      generation: 3,
    })
    expect(el.querySelector('[data-work-id="b"]')).not.toBeNull()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('registers the recent bridge and paints the host that stored the request', () => {
    const el = host()
    const decoy = host()
    el.setAttribute('data-prks-vue-route-host', 'true')
    decoy.setAttribute('data-prks-vue-route-host', 'true')
    const pane = owner()
    ;(el as HTMLElement & { __prksVueRouteRequest?: object }).__prksVueRouteRequest = {
      feature: 'recent',
      owner: pane,
      host: decoy,
      rows: [{ id: 'early', last_opened_at: '' }],
      offlineCached: false,
      generation: 1,
      shell: true,
    }
    window.prksWorkCardHtml = (work) => `<div data-work-id="${String(work.id)}"></div>`
    registerRecentBridge(window)
    expect(window.prksVuePresentRoute).toBeTypeOf('function')
    expect(window.prksVueDismissRecent).toBeTypeOf('function')
    expect((el as HTMLElement & { __prksVueRouteRequest?: unknown }).__prksVueRouteRequest).toBeUndefined()
    expect(el.querySelector('[data-work-id="early"]')).not.toBeNull()
    expect(decoy.querySelector('[data-prks-recent-view]')).toBeNull()
    expect(readRouteSurface(pane)?.name).toBe('recent')
  })

  it('dismisses one owner and leaves the other mounted', () => {
    window.prksWorkCardHtml = (work) => `<div data-work-id="${String(work.id)}"></div>`
    registerRecentBridge(window)
    const main = owner()
    const secondary = owner()
    const mainHost = host()
    const secondaryHost = host()
    window.prksVuePresentRoute?.({
      feature: 'recent',
      owner: main,
      host: mainHost,
      rows: [{ id: 'main' }],
      generation: 2,
      shell: true,
    })
    window.prksVuePresentRoute?.({
      feature: 'recent',
      owner: secondary,
      host: secondaryHost,
      rows: [{ id: 'side' }],
      generation: 1,
      shell: false,
    })
    window.prksVueDismissRecent?.(main)
    expect(mainHost.querySelector('[data-prks-recent-view]')).toBeNull()
    expect(secondaryHost.querySelector('[data-work-id="side"]')).not.toBeNull()
  })

  it('releases preview and lazy thumbs while they are still under the Recent root', async () => {
    window.prksWorkCardHtml = (work, options) => {
      const thumb = options?.suppressThumbnail
        ? ''
        : `<img data-prks-thumb-lazy data-for="${String(work.id)}">`
      return `<div data-work-id="${String(work.id)}">${thumb}</div>`
    }
    const preview = window as Window & { __prksWorkThumbPreviewSource?: Element | null }
    const releaseLog: Array<{ id: string | null; connected: boolean; underRecent: boolean }> = []
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
        underRecent: !!img.closest('[data-prks-recent-view]'),
      })
    }
    const inits: string[] = []
    window.prksInitLazyWorkThumbs = (root) => {
      inits.push(root?.querySelector?.('[data-work-id]')?.getAttribute('data-work-id') || '')
    }
    const pane = owner()
    const el = host()
    presentRecent({
      owner: pane,
      host: el,
      rows: [{ id: 'a' }],
      offlineCached: false,
      generation: 1,
    })
    const first = el.querySelector('img[data-prks-thumb-lazy]')
    expect(first?.getAttribute('data-for')).toBe('a')
    expect(inits).toEqual(['a'])
    preview.__prksWorkThumbPreviewSource = first
    presentRecent({
      owner: pane,
      host: el,
      rows: [{ id: 'b' }],
      offlineCached: false,
      generation: 1,
    })
    await nextTick()
    expect(releaseLog).toContainEqual({ id: 'a', connected: true, underRecent: true })
    expect(preview.__prksWorkThumbPreviewSource).toBeNull()
    expect(el.querySelector('[data-work-id="b"]')).not.toBeNull()
    expect(el.querySelector('[data-work-id="a"]')).toBeNull()
    expect(inits).toEqual(['a', 'b'])
    const live = el.querySelector('img[data-prks-thumb-lazy]')
    preview.__prksWorkThumbPreviewSource = live
    dismissRecent(pane)
    expect(releaseLog).toContainEqual({ id: 'b', connected: true, underRecent: true })
    expect(preview.__prksWorkThumbPreviewSource).toBeNull()
    expect(el.querySelector('[data-prks-recent-view]')).toBeNull()
    presentRecent({
      owner: pane,
      host: el,
      rows: [{ id: 'c' }],
      offlineCached: true,
      generation: 2,
    })
    expect(inits).toEqual(['a', 'b'])
    expect(el.querySelector('[data-work-id="c"]')?.querySelector('img')).toBeNull()
  })
})
