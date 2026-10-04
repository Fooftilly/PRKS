import { nextTick } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { dismissRouteSurface, readRouteSurface } from '../../route-surface/lifecycle'
import { presentRecent, registerRecentBridge, resetRecentSessionForTests } from './session'

afterEach(() => {
  resetRecentSessionForTests()
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
  delete window.prksVuePresentRoute
  delete window.prksVueDismissRoute
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
    expect(mainHost.querySelector('[data-work-id="main"] .work-card__thumb--empty')).not.toBeNull()
    expect(mainHost.querySelector('[data-work-id="main"] .work-card__context')?.textContent).toContain('Last opened: ')
    expect(secondaryHost.querySelector('[data-work-id="side"] .work-card__thumb--empty')).not.toBeNull()
    expect(secondaryHost.querySelector('[data-work-id="side"] .work-card__context')?.textContent).toBe('Last opened: Unknown')
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
    const pane = owner()
    const el = host()
    presentRecent({ owner: pane, host: el, rows: [], generation: 2 })
    expect(el.querySelector('.prks-inline-message')?.textContent).toBe('No recently opened documents found.')
    expect(fetchMock).not.toHaveBeenCalled()
    dismissRouteSurface(pane)
    presentRecent({
      owner: pane,
      host: el,
      rows: [{ id: 'b', file_path: '/api/pdfs/b.pdf' }],
      generation: 2,
    })
    await nextTick()
    expect(el.querySelector('[data-work-id="b"]')).toBeNull()
    presentRecent({
      owner: pane,
      host: el,
      rows: [{ id: 'b', file_path: '/api/pdfs/b.pdf' }],
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
    registerRecentBridge(window)
    expect(window.prksVuePresentRoute).toBeTypeOf('function')
    expect(window.prksVueDismissRoute).toBeTypeOf('function')
    expect((el as HTMLElement & { __prksVueRouteRequest?: unknown }).__prksVueRouteRequest).toBeUndefined()
    expect(el.querySelector('[data-work-id="early"]')).not.toBeNull()
    expect(decoy.querySelector('[data-prks-recent-view]')).toBeNull()
    expect(readRouteSurface(pane)?.name).toBe('recent')
  })

  it('dismisses one owner and leaves the other mounted', () => {
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
    window.prksVueDismissRoute?.(main)
    expect(mainHost.querySelector('[data-prks-recent-view]')).toBeNull()
    expect(secondaryHost.querySelector('[data-work-id="side"]')).not.toBeNull()
  })

  it('releases preview and lazy thumbs while they are still under the Recent root', async () => {
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
        id: img.closest('[data-work-id]')?.getAttribute('data-work-id') ?? img.getAttribute('data-for'),
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
      rows: [{ id: 'a', file_path: '/api/pdfs/a.pdf' }],
      offlineCached: false,
      generation: 1,
    })
    const first = el.querySelector('img[data-prks-thumb-lazy]')
    expect(first?.closest('[data-work-id]')?.getAttribute('data-work-id')).toBe('a')
    expect(inits).toEqual(['a'])
    preview.__prksWorkThumbPreviewSource = first
    presentRecent({
      owner: pane,
      host: el,
      rows: [{ id: 'b', file_path: '/api/pdfs/b.pdf' }],
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
    dismissRouteSurface(pane)
    expect(releaseLog).toContainEqual({ id: 'b', connected: true, underRecent: true })
    expect(preview.__prksWorkThumbPreviewSource).toBeNull()
    expect(el.querySelector('[data-prks-recent-view]')).toBeNull()
    presentRecent({
      owner: pane,
      host: el,
      rows: [{ id: 'c', file_path: '/api/pdfs/c.pdf' }],
      offlineCached: true,
      generation: 2,
    })
    expect(inits).toEqual(['a', 'b'])
    expect(el.querySelector('[data-work-id="c"]')?.querySelector('img')).toBeNull()
  })
})
