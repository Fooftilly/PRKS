import { nextTick } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { readRouteSurface } from '../../route-surface/lifecycle'
import { presentSearch } from '../search/session'
import { buildSavedViewDetailProjection } from './projection'
import {
  dismissSavedViews,
  presentSavedViewDetail,
  registerSavedViewsBridge,
  resetSavedViewsSessionForTests,
} from './session'

afterEach(() => {
  resetSavedViewsSessionForTests()
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
  delete window.prksVuePresentSavedViewDetail
  delete window.prksVueDismissSavedViews
  delete window.prksWorkCardHtml
  delete window.prksAbstractExcerpt
  delete window.prksReleaseLazyWorkThumbs
  delete window.prksOpenSavedViewModal
  delete window.prksDeleteSavedViewFromDetail
})

const VIEW = { id: 'SV 1', name: 'Critical theory', search: { mode: 'all', q: 'x', tag: '', author: '', publisher: '' } }

function host(): HTMLElement {
  const el = document.createElement('div')
  document.body.appendChild(el)
  return el
}

function owner(tabId: string) {
  const state = { generation: 0, entityId: null as string | null, routeName: 'saved-view-detail' }
  return {
    tabId,
    state,
    isCurrent: (generation: number) => generation === state.generation,
    getEntity: (type: string) => (type === 'savedView' && state.entityId ? { id: state.entityId } : null),
    get lastResolvedRoute() {
      return { name: state.routeName }
    },
  }
}

function cards(): void {
  window.prksWorkCardHtml = (work, options) =>
    `<div class="work-card" data-work-id="${String(work.id)}" data-sub="${options.subtitle || ''}"></div>`
}

describe('saved view detail projection', () => {
  it('is not-found without a record and never carries rows then', () => {
    const missing = buildSavedViewDetailProjection({ availability: 'ready', view: null, viewId: 'SV-9', rows: [{ id: 'w' }], generation: 1 })
    expect(missing.availability).toBe('not-found')
    expect(missing.viewId).toBe('SV-9')
    expect(missing.results.rows).toEqual([])
    const ready = buildSavedViewDetailProjection({
      availability: 'ready',
      view: VIEW,
      searchHash: 'javascript:alert(1)',
      rows: [{ id: 'w' }],
      generation: 1,
    })
    expect(ready.view?.search.q).toBe('x')
    expect(ready.searchHash).toBe('#/search')
  })
})

describe('Saved View detail route bridge', () => {
  it('paints the view and the same result cards Search paints for the same rows', () => {
    cards()
    window.prksAbstractExcerpt = (value) => String(value).slice(0, 2)
    const rows = [{ id: 'w1', abstract: 'abcd', status: 'Completed' }]
    const viewOwner = owner('main')
    viewOwner.state.generation = 2
    viewOwner.state.entityId = 'SV 1'
    const viewHost = host()
    presentSavedViewDetail({
      owner: viewOwner,
      host: viewHost,
      availability: 'ready',
      view: VIEW,
      searchHash: '#/search?any=1&q=x',
      rows,
      generation: 2,
    })
    const searchHost = host()
    presentSearch({ owner: owner('other'), host: searchHost, request: { q: 'x', any: '1' }, rows, generation: 1 })
    expect(viewHost.querySelector('.saved-view-detail__kicker')?.textContent).toBe('Saved View')
    expect(viewHost.querySelector('.prks-page-title')?.textContent).toBe('Critical theory')
    expect(viewHost.querySelector('a[href="#/search?any=1&q=x"]')?.textContent).toBe('Open as Search')
    expect(viewHost.querySelector('#prks-saved-view-delete')?.getAttribute('data-sv-delete')).toBe('SV 1')
    expect(viewHost.querySelector('[data-prks-search-results]')?.innerHTML).toBe(
      searchHost.querySelector('[data-prks-search-results]')?.innerHTML,
    )
    expect(viewHost.querySelector('[data-work-id="w1"]')?.getAttribute('data-sub')).toBe('ab…')
    expect(readRouteSurface(viewOwner)).toMatchObject({ name: 'saved-view-detail', canonicalHash: '#/views/SV%201' })
  })

  it('routes Edit and Delete through owner-checked intents', async () => {
    cards()
    const open = vi.fn()
    const del = vi.fn(async () => {})
    window.prksOpenSavedViewModal = open
    window.prksDeleteSavedViewFromDetail = del
    const pane = owner('main')
    pane.state.generation = 4
    pane.state.entityId = 'SV 1'
    const el = host()
    presentSavedViewDetail({ owner: pane, host: el, availability: 'ready', view: VIEW, rows: [], generation: 4 })
    ;(el.querySelector('#prks-saved-view-edit') as HTMLButtonElement).click()
    ;(el.querySelector('#prks-saved-view-delete') as HTMLButtonElement).click()
    await nextTick()
    expect(open).toHaveBeenCalledWith({ viewId: 'SV 1', name: 'Critical theory', definition: VIEW.search })
    expect(del).toHaveBeenCalledWith('SV 1', expect.any(Function), 'main')
    pane.state.generation = 5
    ;(el.querySelector('#prks-saved-view-edit') as HTMLButtonElement).click()
    ;(el.querySelector('#prks-saved-view-delete') as HTMLButtonElement).click()
    expect(open).toHaveBeenCalledTimes(1)
    expect(del).toHaveBeenCalledTimes(1)
  })

  it('paints not-found, ignores a stale generation, and keeps owners apart', async () => {
    cards()
    const main = owner('main')
    const other = owner('other')
    const mainHost = host()
    const otherHost = host()
    presentSavedViewDetail({ owner: main, host: mainHost, availability: 'not-found', viewId: 'gone', generation: 3 })
    presentSavedViewDetail({ owner: other, host: otherHost, availability: 'ready', view: VIEW, rows: [{ id: 'w2' }], generation: 1 })
    expect(mainHost.querySelector('.prks-page-title')?.textContent).toBe('Saved View not found.')
    expect(mainHost.querySelector('a[href="#/views"]')).not.toBeNull()
    expect(mainHost.querySelector('[data-work-id="w2"]')).toBeNull()
    presentSavedViewDetail({ owner: main, host: mainHost, availability: 'ready', view: VIEW, rows: [{ id: 'late' }], generation: 2 })
    await nextTick()
    expect(mainHost.querySelector('[data-work-id="late"]')).toBeNull()
    dismissSavedViews(main)
    expect(mainHost.innerHTML).toBe('')
    expect(otherHost.querySelector('[data-work-id="w2"]')).not.toBeNull()
  })

  it('registers the bridge and paints the host that stored the early request', () => {
    cards()
    const el = host()
    el.setAttribute('data-prks-vue-route-host', 'true')
    ;(el as HTMLElement & { __prksVueRouteRequest?: object }).__prksVueRouteRequest = {
      feature: 'saved-view-detail',
      owner: owner('main'),
      availability: 'ready',
      view: VIEW,
      rows: [{ id: 'early' }],
      generation: 1,
    }
    registerSavedViewsBridge(window)
    expect(window.prksVuePresentSavedViewDetail).toBeTypeOf('function')
    expect(el.querySelector('[data-work-id="early"]')).not.toBeNull()
  })
})
