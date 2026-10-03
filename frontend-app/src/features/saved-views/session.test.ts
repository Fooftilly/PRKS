import { flushPromises } from '@vue/test-utils'
import { nextTick } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { readRouteSurface } from '../../route-surface/lifecycle'
import { presentSearch } from '../search/session'
import { buildSavedViewDetailProjection, buildSavedViewIndexProjection } from './projection'
import {
  dismissSavedViews,
  presentSavedViewDetail,
  presentSavedViewsIndex,
  registerSavedViewsBridge,
  resetSavedViewsSessionForTests,
} from './session'

afterEach(() => {
  resetSavedViewsSessionForTests()
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
  delete window.prksVuePresentRoute
  delete window.prksVueDismissSavedViews
  delete window.prksSearchSummaryText
  delete window.prksDeleteSavedViewFromIndex
  delete window.fetchSavedView
  delete window.prksOpenCommandPalette
  delete window.prksPageHeaderIconHtml
  delete window.prksIcon
  delete window.prksRefreshIcons
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

describe('saved views index projection', () => {
  it('keeps named views, skips blank ids, and asks the codec for the summary', () => {
    window.prksSearchSummaryText = (definition) => {
      const search = definition as { q?: string }
      return search.q ? `q:${search.q}` : 'Any file'
    }
    const projection = buildSavedViewIndexProjection({
      views: [VIEW, { id: '  ', name: 'blank' }, null, { name: 'missing id' }],
      generation: 4,
    })
    expect(projection.generation).toBe(4)
    expect(projection.rows).toEqual([
      { id: 'SV 1', name: 'Critical theory', summary: 'q:x', href: '#/views/SV%201' },
    ])
    delete window.prksSearchSummaryText
    expect(buildSavedViewIndexProjection({ views: [VIEW], generation: 1 }).rows[0]?.summary).toBe('')
  })
})

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

describe('Saved Views index route bridge', () => {
  it('paints rows, routes Edit and Delete through index intents, and ignores a stale owner', async () => {
    window.prksSearchSummaryText = () => 'Any file'
    window.prksPageHeaderIconHtml = () => ''
    window.prksIcon = () => ''
    const open = vi.fn()
    const del = vi.fn(async () => {})
    window.fetchSavedView = async () => VIEW
    window.prksOpenSavedViewModal = open
    window.prksDeleteSavedViewFromIndex = del
    const pane = owner('main')
    pane.state.generation = 3
    pane.state.routeName = 'saved-views'
    const el = host()
    presentSavedViewsIndex({ owner: pane, host: el, views: [VIEW], generation: 3 })
    expect(el.querySelector('.prks-page-title')?.textContent?.replace(/\s+/g, ' ').trim()).toBe('Saved Views')
    expect(el.querySelector('.saved-views-page__summary')?.textContent).toBe('Any file')
    expect(el.querySelector('a.saved-views-page__list-main')?.getAttribute('href')).toBe('#/views/SV%201')
    expect(el.querySelector('a.prks-btn')?.textContent).toBe('Open')
    const actions = el.querySelectorAll('.saved-views-page__row-actions button')
    ;(actions[0] as HTMLButtonElement).click()
    await flushPromises()
    expect(open).toHaveBeenCalledWith({ viewId: 'SV 1', name: 'Critical theory', definition: VIEW.search })
    ;(actions[1] as HTMLButtonElement).click()
    await flushPromises()
    expect(del).toHaveBeenCalledWith('SV 1', expect.any(Function), 'main')
    pane.state.generation = 4
    ;(actions[0] as HTMLButtonElement).click()
    await flushPromises()
    ;(actions[1] as HTMLButtonElement).click()
    await flushPromises()
    expect(open).toHaveBeenCalledTimes(1)
    expect(del).toHaveBeenCalledTimes(1)
    expect(readRouteSurface(pane)).toMatchObject({ name: 'saved-views', canonicalHash: '#/views' })
  })

  it('keeps an index delete busy until resolve, reject, or cancel, and ignores a second click', async () => {
    window.prksSearchSummaryText = () => 'Any file'
    window.prksPageHeaderIconHtml = () => ''
    window.prksIcon = () => ''
    let release: (value?: void) => void = () => {}
    let rejectDelete: (error: Error) => void = () => {}
    const del = vi.fn(
      () =>
        new Promise<void>((resolve, reject) => {
          release = resolve
          rejectDelete = reject
        }),
    )
    window.prksDeleteSavedViewFromIndex = del
    const main = owner('main')
    const other = owner('other')
    main.state.generation = 3
    other.state.generation = 1
    main.state.routeName = 'saved-views'
    other.state.routeName = 'saved-views'
    const mainHost = host()
    const otherHost = host()
    const views = [VIEW, { ...VIEW, id: 'SV 2', name: 'Later' }]
    presentSavedViewsIndex({ owner: main, host: mainHost, views, generation: 3 })
    presentSavedViewsIndex({ owner: other, host: otherHost, views, generation: 1 })
    const button = (root: HTMLElement, id: string) =>
      root.querySelector(`[data-sv-index-delete="${id}"]`) as HTMLButtonElement
    const clicked = () => button(mainHost, 'SV 1')
    const sibling = () => button(mainHost, 'SV 2')
    const otherButton = () => button(otherHost, 'SV 1')

    expect(clicked().classList.contains('prks-btn--danger')).toBe(true)
    expect(clicked().classList.contains('prks-btn--sm')).toBe(true)
    expect(clicked().textContent?.trim()).toBe('Delete')
    expect(mainHost.innerHTML).not.toContain('style="display: contents"')
    expect(mainHost.querySelectorAll('.work-html-slot').length).toBeGreaterThan(0)

    clicked().click()
    await nextTick()
    expect(clicked().disabled).toBe(true)
    expect(clicked().getAttribute('aria-busy')).toBe('true')
    expect(clicked().textContent).toBe('Deleting…')
    clicked().click()
    sibling().click()
    expect(del).toHaveBeenCalledTimes(1)
    expect(del).toHaveBeenCalledWith('SV 1', expect.any(Function), 'main')
    expect(sibling().disabled).toBe(true)
    expect(sibling().getAttribute('aria-busy')).toBeNull()
    expect(sibling().textContent?.trim()).toBe('Delete')
    expect(otherButton().disabled).toBe(false)
    expect(otherButton().getAttribute('aria-busy')).toBeNull()
    expect(otherButton().textContent?.trim()).toBe('Delete')

    release()
    await flushPromises()
    expect(clicked().disabled).toBe(false)
    expect(clicked().getAttribute('aria-busy')).toBeNull()
    expect(clicked().textContent?.trim()).toBe('Delete')
    expect(sibling().disabled).toBe(false)

    clicked().click()
    await nextTick()
    expect(del).toHaveBeenCalledTimes(2)
    expect(clicked().getAttribute('aria-busy')).toBe('true')
    rejectDelete(new Error('delete failed'))
    await flushPromises()
    expect(clicked().disabled).toBe(false)
    expect(clicked().getAttribute('aria-busy')).toBeNull()
    expect(clicked().textContent?.trim()).toBe('Delete')

    clicked().click()
    await nextTick()
    expect(del).toHaveBeenCalledTimes(3)
    expect(clicked().textContent).toBe('Deleting…')
    release()
    await flushPromises()
    expect(clicked().disabled).toBe(false)
    expect(clicked().getAttribute('aria-busy')).toBeNull()
    expect(clicked().textContent?.trim()).toBe('Delete')
    expect(otherButton().disabled).toBe(false)
  })

  it('starts one fetch for two delayed Edit clicks, and Edit and Delete cannot overlap', async () => {
    window.prksSearchSummaryText = () => 'Any file'
    window.prksPageHeaderIconHtml = () => ''
    window.prksIcon = () => ''
    let releaseFetch: (value: typeof VIEW) => void = () => {}
    const fetchView = vi.fn(
      () =>
        new Promise<typeof VIEW>((resolve) => {
          releaseFetch = resolve
        }),
    )
    const open = vi.fn()
    window.fetchSavedView = fetchView
    window.prksOpenSavedViewModal = open
    let releaseDelete: (value?: void) => void = () => {}
    const del = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          releaseDelete = resolve
        }),
    )
    window.prksDeleteSavedViewFromIndex = del
    const main = owner('main')
    const other = owner('other')
    main.state.generation = 3
    other.state.generation = 1
    main.state.routeName = 'saved-views'
    other.state.routeName = 'saved-views'
    const mainHost = host()
    const otherHost = host()
    presentSavedViewsIndex({ owner: main, host: mainHost, views: [VIEW], generation: 3 })
    presentSavedViewsIndex({ owner: other, host: otherHost, views: [VIEW], generation: 1 })
    const editButton = (root: HTMLElement) =>
      root.querySelector('[data-sv-index-edit="SV 1"]') as HTMLButtonElement
    const deleteButton = (root: HTMLElement) =>
      root.querySelector('[data-sv-index-delete="SV 1"]') as HTMLButtonElement

    editButton(mainHost).click()
    await nextTick()
    expect(editButton(mainHost).disabled).toBe(true)
    expect(editButton(mainHost).getAttribute('aria-busy')).toBe('true')
    expect(editButton(mainHost).textContent).toBe('Opening…')
    editButton(mainHost).click()
    expect(fetchView).toHaveBeenCalledTimes(1)
    expect(deleteButton(mainHost).disabled).toBe(true)
    deleteButton(mainHost).click()
    expect(del).not.toHaveBeenCalled()
    expect(editButton(otherHost).disabled).toBe(false)
    expect(deleteButton(otherHost).disabled).toBe(false)

    releaseFetch(VIEW)
    await flushPromises()
    expect(open).toHaveBeenCalledTimes(1)
    expect(editButton(mainHost).disabled).toBe(false)
    expect(editButton(mainHost).textContent?.trim()).toBe('Edit')
    expect(deleteButton(mainHost).disabled).toBe(false)

    deleteButton(mainHost).click()
    await nextTick()
    expect(deleteButton(mainHost).disabled).toBe(true)
    expect(deleteButton(mainHost).textContent).toBe('Deleting…')
    expect(editButton(mainHost).disabled).toBe(true)
    editButton(mainHost).click()
    expect(fetchView).toHaveBeenCalledTimes(1)
    expect(editButton(otherHost).disabled).toBe(false)
    releaseDelete()
    await flushPromises()
    expect(editButton(mainHost).disabled).toBe(false)
    expect(deleteButton(mainHost).disabled).toBe(false)
    expect(del).toHaveBeenCalledTimes(1)
  })

  it('shows a failed Edit or Delete on the owning row and stays quiet for cancel or stale', async () => {
    window.prksSearchSummaryText = () => 'Any file'
    window.prksPageHeaderIconHtml = () => ''
    window.prksIcon = () => ''
    const open = vi.fn()
    window.prksOpenSavedViewModal = open
    let rejectFetch: (err: Error) => void = () => {}
    window.fetchSavedView = vi.fn(
      () =>
        new Promise<typeof VIEW>((_resolve, reject) => {
          rejectFetch = reject
        }),
    )
    let deleteResult: { ok: boolean; reason: string; message?: string } = {
      ok: false,
      reason: 'cancelled',
    }
    const del = vi.fn(async () => deleteResult)
    window.prksDeleteSavedViewFromIndex = del
    const main = owner('main')
    const other = owner('other')
    main.state.generation = 3
    other.state.generation = 1
    main.state.routeName = 'saved-views'
    other.state.routeName = 'saved-views'
    const mainHost = host()
    const otherHost = host()
    const views = [VIEW, { ...VIEW, id: 'SV 2', name: 'Later' }]
    presentSavedViewsIndex({ owner: main, host: mainHost, views, generation: 3 })
    presentSavedViewsIndex({ owner: other, host: otherHost, views: [VIEW], generation: 1 })
    const editButton = (root: HTMLElement, id: string) =>
      root.querySelector(`[data-sv-index-edit="${id}"]`) as HTMLButtonElement
    const deleteButton = (root: HTMLElement, id: string) =>
      root.querySelector(`[data-sv-index-delete="${id}"]`) as HTMLButtonElement
    const rowError = (root: HTMLElement, id: string) => root.querySelector(`[data-sv-index-error="${id}"]`)

    editButton(mainHost, 'SV 1').click()
    await nextTick()
    expect(editButton(mainHost, 'SV 1').textContent).toBe('Opening…')
    rejectFetch(new Error('Could not open Saved View.'))
    await flushPromises()
    expect(open).not.toHaveBeenCalled()
    expect(editButton(mainHost, 'SV 1').disabled).toBe(false)
    expect(editButton(mainHost, 'SV 1').getAttribute('aria-busy')).toBeNull()
    expect(editButton(mainHost, 'SV 1').textContent?.trim()).toBe('Edit')
    expect(rowError(mainHost, 'SV 1')?.textContent?.trim()).toBe('Could not open Saved View.')
    expect(rowError(mainHost, 'SV 2')).toBeNull()
    expect(otherHost.querySelector('[data-sv-index-error]')).toBeNull()

    editButton(mainHost, 'SV 2').click()
    await nextTick()
    main.state.generation = 4
    rejectFetch(new Error('late failure'))
    await flushPromises()
    expect(mainHost.textContent).not.toContain('late failure')
    expect(rowError(mainHost, 'SV 2')).toBeNull()
    expect(rowError(mainHost, 'SV 1')?.textContent?.trim()).toBe('Could not open Saved View.')
    expect(editButton(mainHost, 'SV 2').disabled).toBe(false)
    expect(editButton(otherHost, 'SV 1').disabled).toBe(false)

    main.state.generation = 3
    deleteButton(mainHost, 'SV 1').click()
    await flushPromises()
    expect(del).toHaveBeenCalledTimes(1)
    expect(rowError(mainHost, 'SV 1')?.textContent?.trim()).toBe('Could not open Saved View.')
    expect(deleteButton(mainHost, 'SV 1').disabled).toBe(false)
    expect(deleteButton(mainHost, 'SV 1').textContent?.trim()).toBe('Delete')

    deleteResult = { ok: false, reason: 'failed', message: 'Could not delete Saved View.' }
    deleteButton(mainHost, 'SV 2').click()
    await flushPromises()
    expect(rowError(mainHost, 'SV 2')?.textContent?.trim()).toBe('Could not delete Saved View.')
    expect(rowError(mainHost, 'SV 1')?.textContent?.trim()).toBe('Could not open Saved View.')
    expect(deleteButton(mainHost, 'SV 2').disabled).toBe(false)
    expect(deleteButton(mainHost, 'SV 2').classList.contains('prks-btn--danger')).toBe(true)
    expect(otherHost.querySelector('[data-sv-index-error]')).toBeNull()
  })

  it('stays quiet when a delete fails after the owning index goes stale', async () => {
    window.prksSearchSummaryText = () => 'Any file'
    window.prksPageHeaderIconHtml = () => ''
    window.prksIcon = () => ''
    let releaseDelete: (value: { ok: boolean; reason: string; message: string }) => void = () => {}
    window.prksDeleteSavedViewFromIndex = () =>
      new Promise((resolve) => {
        releaseDelete = resolve
      })
    const main = owner('main')
    const other = owner('other')
    main.state.generation = 3
    other.state.generation = 1
    main.state.routeName = 'saved-views'
    other.state.routeName = 'saved-views'
    const mainHost = host()
    const otherHost = host()
    presentSavedViewsIndex({ owner: main, host: mainHost, views: [VIEW], generation: 3 })
    presentSavedViewsIndex({ owner: other, host: otherHost, views: [VIEW], generation: 1 })
    const deleteButton = (root: HTMLElement) =>
      root.querySelector('[data-sv-index-delete="SV 1"]') as HTMLButtonElement

    deleteButton(mainHost).click()
    await nextTick()
    expect(deleteButton(mainHost).textContent).toBe('Deleting…')
    expect(deleteButton(mainHost).classList.contains('prks-btn--danger')).toBe(true)
    main.state.generation = 4
    releaseDelete({ ok: false, reason: 'failed', message: 'late failure' })
    await flushPromises()
    expect(mainHost.textContent).not.toContain('late failure')
    expect(mainHost.querySelector('[data-sv-index-error]')).toBeNull()
    expect(deleteButton(mainHost).disabled).toBe(false)
    expect(deleteButton(mainHost).textContent?.trim()).toBe('Delete')
    expect(deleteButton(otherHost).disabled).toBe(false)
    expect(otherHost.querySelector('[data-sv-index-error]')).toBeNull()
  })

  it('paints the empty index, keeps owners apart, and drops a stale generation', async () => {
    const palette = vi.fn()
    window.prksOpenCommandPalette = palette
    const main = owner('main')
    const other = owner('other')
    main.state.generation = 2
    other.state.generation = 1
    main.state.routeName = 'saved-views'
    other.state.routeName = 'saved-views'
    const mainHost = host()
    const otherHost = host()
    presentSavedViewsIndex({ owner: main, host: mainHost, views: [], generation: 2 })
    presentSavedViewsIndex({ owner: other, host: otherHost, views: [VIEW], generation: 1 })
    expect(mainHost.querySelector('.saved-views-page__empty')?.textContent).toBe('No Saved Views yet.')
    expect(mainHost.textContent).toContain('Run a search and choose “Save View” to keep it here.')
    expect(mainHost.querySelector('.saved-views-page__list-item')).toBeNull()
    ;(mainHost.querySelector('#prks-saved-views-empty-search') as HTMLButtonElement).click()
    expect(palette).toHaveBeenCalledTimes(1)
    expect(otherHost.querySelector('.saved-views-page__list-item')).not.toBeNull()
    presentSavedViewsIndex({ owner: main, host: mainHost, views: [VIEW], generation: 1 })
    await nextTick()
    expect(mainHost.querySelector('.saved-views-page__list-item')).toBeNull()
    dismissSavedViews(main)
    expect(mainHost.innerHTML).toBe('')
    expect(otherHost.querySelector('.saved-views-page__list-item')).not.toBeNull()
  })

  it('registers the index bridge and paints an early host', () => {
    window.prksSearchSummaryText = () => 'tag:T'
    const el = host()
    el.setAttribute('data-prks-vue-route-host', 'true')
    ;(el as HTMLElement & { __prksVueRouteRequest?: object }).__prksVueRouteRequest = {
      feature: 'saved-views',
      owner: owner('main'),
      views: [VIEW],
      generation: 1,
    }
    registerSavedViewsBridge(window)
    expect(window.prksVuePresentRoute).toBeTypeOf('function')
    expect(el.querySelector('.saved-views-page__summary')?.textContent).toBe('tag:T')
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

  it('keeps detail delete busy until confirm, cancel, or failure, and ignores a second click', async () => {
    cards()
    let release: (value?: void) => void = () => {}
    let rejectDelete: (error: Error) => void = () => {}
    const del = vi.fn(
      () =>
        new Promise<void>((resolve, reject) => {
          release = resolve
          rejectDelete = reject
        }),
    )
    window.prksDeleteSavedViewFromDetail = del
    const main = owner('main')
    const other = owner('other')
    main.state.generation = 2
    other.state.generation = 1
    main.state.entityId = 'SV 1'
    other.state.entityId = 'SV 1'
    const mainHost = host()
    const otherHost = host()
    presentSavedViewDetail({ owner: main, host: mainHost, availability: 'ready', view: VIEW, rows: [], generation: 2 })
    presentSavedViewDetail({ owner: other, host: otherHost, availability: 'ready', view: VIEW, rows: [], generation: 1 })
    const button = () => mainHost.querySelector('#prks-saved-view-delete') as HTMLButtonElement
    const otherButton = () => otherHost.querySelector('#prks-saved-view-delete') as HTMLButtonElement
    expect(button().classList.contains('prks-btn--danger')).toBe(true)
    expect(button().textContent?.trim()).toBe('Delete Saved View')

    button().click()
    await nextTick()
    expect(button().disabled).toBe(true)
    expect(button().getAttribute('aria-busy')).toBe('true')
    expect(button().textContent).toBe('Deleting…')
    button().click()
    expect(del).toHaveBeenCalledTimes(1)
    expect(otherButton().disabled).toBe(false)
    expect(otherButton().getAttribute('aria-busy')).toBeNull()

    release()
    await flushPromises()
    expect(button().disabled).toBe(false)
    expect(button().getAttribute('aria-busy')).toBeNull()
    expect(button().textContent?.trim()).toBe('Delete Saved View')

    button().click()
    await nextTick()
    expect(del).toHaveBeenCalledTimes(2)
    expect(button().getAttribute('aria-busy')).toBe('true')
    rejectDelete(new Error('delete failed'))
    await flushPromises()
    expect(button().disabled).toBe(false)
    expect(button().getAttribute('aria-busy')).toBeNull()
    expect(button().textContent?.trim()).toBe('Delete Saved View')
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
    expect(window.prksVuePresentRoute).toBeTypeOf('function')
    expect(el.querySelector('[data-work-id="early"]')).not.toBeNull()
  })
})
