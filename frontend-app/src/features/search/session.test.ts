import { nextTick } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { readRouteSurface } from '../../route-surface/lifecycle'
import { dismissSearch, presentSearch, registerSearchBridge, resetSearchSessionForTests } from './session'

afterEach(() => {
  resetSearchSessionForTests()
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
  delete window.prksVuePresentRoute
  delete window.prksVueDismissSearch
  delete window.prksWorkCardHtml
  delete window.prksAbstractExcerpt
  delete window.prksReleaseWorkThumbPreview
  delete window.prksReleaseLazyWorkThumbs
  delete window.prksInitLazyWorkThumbs
  delete window.prksScopeLineHtml
  delete window.prksNavigate
  delete window.prksOpenSavedViewModalFromCurrentSearch
})

function host(): HTMLElement {
  const el = document.createElement('div')
  document.body.appendChild(el)
  return el
}

function owner(tabId: string) {
  const state = { generation: 0, route: { name: 'search' } as { name: string } }
  return {
    tabId,
    state,
    isCurrent: (generation: number) => generation === state.generation,
    get lastResolvedRoute() {
      return state.route
    },
  }
}

function cards(): void {
  window.prksWorkCardHtml = (work, options) =>
    `<div class="work-card" data-work-id="${String(work.id)}" data-sub="${options.subtitle || ''}"></div>`
}

describe('Search route bridge', () => {
  it('paints already-effective rows per owner without fetching or publishing shell state', () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    cards()
    window.prksAbstractExcerpt = (value) => String(value).slice(0, 3)
    window.prksScopeLineHtml = (o) => `<p class="prks-scope-line">${o.total} ${o.label}</p>`
    const main = owner('main')
    const other = owner('other')
    main.state.generation = 5
    other.state.generation = 1
    const mainHost = host()
    const otherHost = host()
    presentSearch({
      owner: main,
      host: mainHost,
      request: { q: 'adorno', author: '', publisher: '', tag: '', any: '' },
      canonicalHash: '#/search?q=adorno',
      rows: [{ id: 'w1', abstract: 'abcdef', status: 'Completed' }],
      generation: 5,
      shell: true,
    })
    presentSearch({
      owner: other,
      host: otherHost,
      request: { tag: 'T' },
      canonicalHash: '#/search?tag=T',
      rows: [],
      generation: 1,
      shell: false,
    })
    expect(mainHost.querySelector('.prks-page-title')?.textContent).toBe('Search results for “adorno”')
    expect(mainHost.querySelector('[data-work-id="w1"]')?.getAttribute('data-sub')).toBe('abc…')
    expect(mainHost.querySelector('.prks-scope-line')?.textContent).toBe('1 result')
    expect((mainHost.querySelector('#search-q-input') as HTMLInputElement).value).toBe('adorno')
    expect(mainHost.querySelector('#prks-save-view-btn')).not.toBeNull()
    expect(otherHost.querySelector('.prks-page-title')?.textContent).toBe('Files tagged “T”')
    expect(otherHost.querySelector('.search-advanced')).toBeNull()
    expect(otherHost.querySelector('.prks-inline-message')?.textContent).toBe('No files have this tag yet.')
    expect(otherHost.querySelector('[data-work-id="w1"]')).toBeNull()
    expect(readRouteSurface(main)).toMatchObject({ name: 'search', canonicalHash: '#/search?q=adorno', ownsMainShell: true })
    expect(readRouteSurface(other)?.ownsMainShell).toBe(false)
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('renders the all-fields form and submits through the owning tab', async () => {
    cards()
    const navigate = vi.fn()
    window.prksNavigate = navigate
    const pane = owner('main')
    pane.state.generation = 2
    const el = host()
    presentSearch({ owner: pane, host: el, request: { q: 'x', any: 'yes' }, rows: [], generation: 2 })
    expect(el.querySelector('#search-q-input')).toBeNull()
    const input = el.querySelector('#search-any-input') as HTMLInputElement
    expect(input.value).toBe('x')
    input.value = 'critical'
    input.dispatchEvent(new Event('input'))
    await nextTick()
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }))
    expect(navigate).toHaveBeenCalledWith('#/search?any=1&q=critical', { tabId: 'main' })
  })

  it('ignores a stale generation and a stale submit', async () => {
    cards()
    const navigate = vi.fn()
    window.prksNavigate = navigate
    const pane = owner('main')
    pane.state.generation = 3
    const el = host()
    presentSearch({ owner: pane, host: el, request: { q: 'a' }, rows: [{ id: 'a' }], generation: 3 })
    presentSearch({ owner: pane, host: el, request: { q: 'b' }, rows: [{ id: 'b' }], generation: 2 })
    await nextTick()
    expect(el.querySelector('[data-work-id="a"]')).not.toBeNull()
    expect(el.querySelector('[data-work-id="b"]')).toBeNull()
    pane.state.generation = 4
    ;(el.querySelector('#search-run-btn') as HTMLButtonElement).click()
    expect(navigate).not.toHaveBeenCalled()
  })

  it('releases scoped thumb resources before a rewrite and on dismiss', async () => {
    const released: string[] = []
    window.prksReleaseLazyWorkThumbs = (root) => {
      released.push((root as HTMLElement).querySelector('.work-card')?.getAttribute('data-work-id') || 'empty')
    }
    cards()
    const pane = owner('main')
    pane.state.generation = 1
    const el = host()
    presentSearch({ owner: pane, host: el, request: { q: 'a' }, rows: [{ id: 'a' }], generation: 1 })
    presentSearch({ owner: pane, host: el, request: { q: 'a' }, rows: [{ id: 'b' }], generation: 2 })
    await nextTick()
    expect(released).toEqual(['empty', 'a'])
    dismissSearch(pane)
    expect(released).toEqual(['empty', 'a', 'b'])
    expect(el.querySelector('[data-prks-search-view]')).toBeNull()
  })

  it('registers the bridge and paints the host that stored the early request', () => {
    cards()
    const el = host()
    const decoy = host()
    el.setAttribute('data-prks-vue-route-host', 'true')
    decoy.setAttribute('data-prks-vue-route-host', 'true')
    ;(el as HTMLElement & { __prksVueRouteRequest?: object }).__prksVueRouteRequest = {
      feature: 'search',
      owner: owner('main'),
      host: decoy,
      request: { q: 'early' },
      canonicalHash: '#/search?q=early',
      rows: [{ id: 'early' }],
      generation: 1,
    }
    registerSearchBridge(window)
    expect(window.prksVuePresentRoute).toBeTypeOf('function')
    expect(el.querySelector('[data-work-id="early"]')).not.toBeNull()
    expect(decoy.querySelector('[data-work-id="early"]')).toBeNull()
  })
})
