import { nextTick } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { SavedView } from '../../api/saved-views'
import { resetPrksQueryClientForTests } from '../../query/client'
import { dismissRouteSurface, readRouteSurface } from '../../route-surface/lifecycle'
import { presentSearch } from '../search/session'
import { buildSavedViewDetailProjection, savedViewIndexRows } from './projection'
import {
  presentSavedViewDetail,
  presentSavedViewsIndex,
  registerSavedViewsBridge,
  resetSavedViewsSessionForTests,
} from './session'

afterEach(() => {
  resetSavedViewsSessionForTests()
  resetPrksQueryClientForTests()
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
  delete window.prksVuePresentRoute
  delete window.prksVueDismissRoute
  delete window.prksSavedViewRecords
  delete window.prksOpenCommandPalette
  delete window.prksPageHeaderIconHtml
  delete window.prksIcon
  delete window.prksRefreshIcons
  delete window.prksAbstractExcerpt
  delete window.prksReleaseLazyWorkThumbs
  delete window.prksOpenSavedViewModal
  delete window.prksConfirmDestructive
  delete window.prksNavigate
})

async function flush(): Promise<void> {
  for (let i = 0; i < 4; i += 1) {
    await nextTick()
    await new Promise((resolve) => setTimeout(resolve, 0))
  }
  await nextTick()
}

const VIEW: SavedView = {
  id: 'SV 1',
  name: 'Critical theory',
  search: { mode: 'all', q: 'x', tag: '', author: '', publisher: '' },
  created_at: '2026-10-03 10:00:00',
  updated_at: '2026-10-03 10:00:00',
}
const LATER: SavedView = { ...VIEW, id: 'SV 2', name: 'Later' }

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

function indexOwner(tabId: string, generation: number) {
  const pane = owner(tabId)
  pane.state.generation = generation
  pane.state.routeName = 'saved-views'
  return pane
}

function cards(): void {
}

function chrome(): void {
  window.prksPageHeaderIconHtml = () => ''
  window.prksIcon = () => ''
}

type Reply = { status: number; body: unknown }

/**
 * An in-memory Saved Views server behind `fetch`. `refuse` answers the next
 * matching request with an error; `hold` keeps it pending until released.
 */
function fakeServer(initial: SavedView[]) {
  const views = initial.map((view) => ({ ...view, search: { ...view.search } }))
  const requests: string[] = []
  const refusals = new Map<string, Reply>()
  const holds = new Map<string, Promise<void>>()

  function handle(method: string, path: string): Reply {
    if (method === 'GET' && path === '/api/saved-views') return { status: 200, body: views }
    const match = /^\/api\/saved-views\/([^/]+)$/.exec(path)
    const id = match ? decodeURIComponent(match[1]) : ''
    const index = views.findIndex((view) => view.id === id)
    if (!match || index < 0) return { status: 404, body: { error: 'Saved View not found.' } }
    if (method === 'GET') return { status: 200, body: views[index] }
    if (method === 'DELETE') {
      views.splice(index, 1)
      return { status: 200, body: { status: 'deleted' } }
    }
    return { status: 405, body: { error: 'Method not allowed' } }
  }

  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init: RequestInit = {}) => {
      const method = init.method ?? 'GET'
      const key = `${method} ${new URL(url, location.origin).pathname}`
      requests.push(key)
      const held = holds.get(key)
      if (held) {
        holds.delete(key)
        await held
      }
      const refused = refusals.get(key)
      if (refused) refusals.delete(key)
      const reply = refused ?? handle(method, new URL(url, location.origin).pathname)
      return new Response(JSON.stringify(reply.body), { status: reply.status })
    }),
  )

  return {
    requests,
    refuse(key: string, reply: Reply) {
      refusals.set(key, reply)
    },
    hold(key: string): () => void {
      let release: () => void = () => {}
      holds.set(
        key,
        new Promise<void>((resolve) => {
          release = resolve
        }),
      )
      return () => release()
    },
    count: (key: string) => requests.filter((request) => request === key).length,
  }
}

function rowIds(el: HTMLElement): (string | null)[] {
  return [...el.querySelectorAll('[data-sv-index-edit]')].map((node) => node.getAttribute('data-sv-index-edit'))
}

describe('saved views projections', () => {
  it('builds index rows in server order and summarizes with the canonical codec', () => {
    expect(savedViewIndexRows([VIEW, { ...LATER, name: '' }])).toEqual([
      { id: 'SV 1', name: 'Critical theory', summary: 'All: x', href: '#/views/SV%201' },
      { id: 'SV 2', name: 'Saved View', summary: 'All: x', href: '#/views/SV%202' },
    ])
  })

  it('is not-found without a record, keeps a read failure apart, and never carries rows then', () => {
    const missing = buildSavedViewDetailProjection({ availability: 'ready', view: null, viewId: 'SV-9', rows: [{ id: 'w' }], generation: 1 })
    expect(missing.availability).toBe('not-found')
    expect(missing.viewId).toBe('SV-9')
    expect(missing.results.rows).toEqual([])
    const failed = buildSavedViewDetailProjection({ availability: 'error', viewId: 'SV-9', rows: [{ id: 'w' }], generation: 1 })
    expect(failed.availability).toBe('error')
    expect(failed.results.rows).toEqual([])
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

describe('Saved Views index route', () => {
  it('shares one list read between panes and paints rows after loading', async () => {
    chrome()
    const server = fakeServer([VIEW, LATER])
    const release = server.hold('GET /api/saved-views')
    const main = host()
    const other = host()
    presentSavedViewsIndex({ owner: indexOwner('main', 3), host: main, generation: 3 })
    presentSavedViewsIndex({ owner: indexOwner('other', 1), host: other, generation: 1 })
    await nextTick()
    expect(main.querySelector('[data-saved-views-loading]')).not.toBeNull()
    expect(main.querySelector('.saved-views-page__empty')).toBeNull()
    release()
    await flush()
    expect(server.count('GET /api/saved-views')).toBe(1)
    expect(main.querySelector('[data-saved-views-loading]')).toBeNull()
    expect(main.querySelector('.prks-page-title')?.textContent?.replace(/\s+/g, ' ').trim()).toBe('Saved Views')
    expect(main.querySelector('.saved-views-page__summary')?.textContent).toBe('All: x')
    expect(main.querySelector('a.saved-views-page__list-main')?.getAttribute('href')).toBe('#/views/SV%201')
    expect(main.querySelector('a.prks-btn')?.textContent).toBe('Open')
    expect(rowIds(other)).toEqual(['SV 1', 'SV 2'])
  })

  it('opens Edit from a fresh record read and refreshes every pane after Delete', async () => {
    chrome()
    const server = fakeServer([VIEW, LATER])
    const open = vi.fn()
    const confirm = vi.fn(async () => true)
    window.prksOpenSavedViewModal = open
    window.prksConfirmDestructive = confirm
    window.prksNavigate = vi.fn()
    const pane = indexOwner('main', 3)
    const main = host()
    const other = host()
    presentSavedViewsIndex({ owner: pane, host: main, generation: 3 })
    presentSavedViewsIndex({ owner: indexOwner('other', 1), host: other, generation: 1 })
    await flush()

    main.querySelector<HTMLButtonElement>('[data-sv-index-edit="SV 1"]')?.click()
    await flush()
    expect(server.requests).toContain('GET /api/saved-views/SV%201')
    expect(open).toHaveBeenCalledWith({ viewId: 'SV 1', name: 'Critical theory', definition: VIEW.search })

    main.querySelector<HTMLButtonElement>('[data-sv-index-delete="SV 1"]')?.click()
    await flush()
    expect(confirm).toHaveBeenCalledTimes(1)
    expect(server.requests).toContain('DELETE /api/saved-views/SV%201')
    expect(rowIds(main)).toEqual(['SV 2'])
    expect(rowIds(other)).toEqual(['SV 2'])
    expect(window.prksNavigate).not.toHaveBeenCalled()
    expect(readRouteSurface(pane)).toMatchObject({ name: 'saved-views', canonicalHash: '#/views' })

    pane.state.generation = 4
    main.querySelector<HTMLButtonElement>('[data-sv-index-edit="SV 2"]')?.click()
    main.querySelector<HTMLButtonElement>('[data-sv-index-delete="SV 2"]')?.click()
    await flush()
    expect(open).toHaveBeenCalledTimes(1)
    expect(confirm).toHaveBeenCalledTimes(1)
  })

  it('keeps an index delete busy until the server answers, and Edit and Delete cannot overlap', async () => {
    chrome()
    const server = fakeServer([VIEW, LATER])
    window.prksConfirmDestructive = async () => true
    window.prksOpenSavedViewModal = vi.fn()
    const main = host()
    const other = host()
    presentSavedViewsIndex({ owner: indexOwner('main', 3), host: main, generation: 3 })
    presentSavedViewsIndex({ owner: indexOwner('other', 1), host: other, generation: 1 })
    await flush()
    const button = (root: HTMLElement, kind: 'edit' | 'delete', id: string) =>
      root.querySelector(`[data-sv-index-${kind}="${id}"]`) as HTMLButtonElement

    expect(button(main, 'delete', 'SV 1').classList.contains('prks-btn--danger')).toBe(true)
    expect(button(main, 'delete', 'SV 1').classList.contains('prks-btn--sm')).toBe(true)
    expect(main.innerHTML).not.toContain('style="display: contents"')

    const releaseDelete = server.hold('DELETE /api/saved-views/SV%201')
    button(main, 'delete', 'SV 1').click()
    await flush()
    expect(button(main, 'delete', 'SV 1').disabled).toBe(true)
    expect(button(main, 'delete', 'SV 1').getAttribute('aria-busy')).toBe('true')
    expect(button(main, 'delete', 'SV 1').textContent).toBe('Deleting…')
    button(main, 'delete', 'SV 1').click()
    button(main, 'delete', 'SV 2').click()
    button(main, 'edit', 'SV 2').click()
    expect(button(main, 'delete', 'SV 2').disabled).toBe(true)
    expect(button(main, 'delete', 'SV 2').getAttribute('aria-busy')).toBeNull()
    expect(button(other, 'delete', 'SV 1').disabled).toBe(false)
    releaseDelete()
    await flush()
    expect(server.count('DELETE /api/saved-views/SV%201')).toBe(1)
    expect(server.count('DELETE /api/saved-views/SV%202')).toBe(0)
    expect(server.count('GET /api/saved-views/SV%202')).toBe(0)
    expect(rowIds(main)).toEqual(['SV 2'])
    expect(button(main, 'delete', 'SV 2').disabled).toBe(false)

    const releaseRead = server.hold('GET /api/saved-views/SV%202')
    button(main, 'edit', 'SV 2').click()
    await flush()
    expect(button(main, 'edit', 'SV 2').textContent).toBe('Opening…')
    expect(button(main, 'delete', 'SV 2').disabled).toBe(true)
    button(main, 'edit', 'SV 2').click()
    releaseRead()
    await flush()
    expect(server.count('GET /api/saved-views/SV%202')).toBe(1)
    expect(window.prksOpenSavedViewModal).toHaveBeenCalledTimes(1)
    expect(button(main, 'edit', 'SV 2').textContent?.trim()).toBe('Edit')
  })

  it('shows a failed Edit or Delete on its row, stays quiet for cancel, and refreshes after a failed delete', async () => {
    chrome()
    const server = fakeServer([VIEW, LATER])
    let answer = false
    window.prksConfirmDestructive = async () => answer
    window.prksOpenSavedViewModal = vi.fn()
    const main = host()
    const other = host()
    presentSavedViewsIndex({ owner: indexOwner('main', 3), host: main, generation: 3 })
    presentSavedViewsIndex({ owner: indexOwner('other', 1), host: other, generation: 1 })
    await flush()
    const rowError = (id: string) => main.querySelector(`[data-sv-index-error="${id}"]`)

    server.refuse('GET /api/saved-views/SV%201', { status: 500, body: null })
    main.querySelector<HTMLButtonElement>('[data-sv-index-edit="SV 1"]')?.click()
    await flush()
    expect(rowError('SV 1')?.textContent?.trim()).toBe('Could not open Saved View.')
    expect(window.prksOpenSavedViewModal).not.toHaveBeenCalled()

    main.querySelector<HTMLButtonElement>('[data-sv-index-delete="SV 2"]')?.click()
    await flush()
    expect(server.count('DELETE /api/saved-views/SV%202')).toBe(0)
    expect(rowError('SV 2')).toBeNull()

    answer = true
    const lists = server.count('GET /api/saved-views')
    server.refuse('DELETE /api/saved-views/SV%202', { status: 404, body: { error: 'Saved View not found.' } })
    main.querySelector<HTMLButtonElement>('[data-sv-index-delete="SV 2"]')?.click()
    await flush()
    expect(rowError('SV 2')?.textContent?.trim()).toBe('Saved View not found.')
    expect(rowError('SV 1')?.textContent?.trim()).toBe('Could not open Saved View.')
    expect(server.count('GET /api/saved-views')).toBe(lists + 1)
    expect(other.querySelector('[data-sv-index-error]')).toBeNull()
  })

  it('stays quiet when a delete fails after the owning index goes stale', async () => {
    chrome()
    const server = fakeServer([VIEW])
    window.prksConfirmDestructive = async () => true
    const pane = indexOwner('main', 3)
    const main = host()
    presentSavedViewsIndex({ owner: pane, host: main, generation: 3 })
    await flush()
    server.refuse('DELETE /api/saved-views/SV%201', { status: 500, body: { error: 'late failure' } })
    const release = server.hold('DELETE /api/saved-views/SV%201')
    main.querySelector<HTMLButtonElement>('[data-sv-index-delete="SV 1"]')?.click()
    await flush()
    pane.state.generation = 4
    release()
    await flush()
    expect(main.textContent).not.toContain('late failure')
    expect(main.querySelector('[data-sv-index-error]')).toBeNull()
  })

  it('paints the empty index, a first-load error with retry, and keeps rows when a refetch fails', async () => {
    chrome()
    const palette = vi.fn()
    window.prksOpenCommandPalette = palette
    const server = fakeServer([])
    server.refuse('GET /api/saved-views', { status: 500, body: null })
    const main = host()
    presentSavedViewsIndex({ owner: indexOwner('main', 2), host: main, generation: 2 })
    await flush()
    expect(main.querySelector('[data-saved-views-load-error]')?.textContent).toContain('Could not load Saved Views.')
    expect(main.querySelector('.saved-views-page__empty')).toBeNull()
    main.querySelector<HTMLButtonElement>('[data-saved-views-load-error] button')?.click()
    await flush()
    expect(main.querySelector('[data-saved-views-load-error]')).toBeNull()
    expect(main.querySelector('.saved-views-page__empty')?.textContent).toBe('No Saved Views yet.')
    expect(main.textContent).toContain('Run a search and choose “Save View” to keep it here.')
    ;(main.querySelector('#prks-saved-views-empty-search') as HTMLButtonElement).click()
    expect(palette).toHaveBeenCalledTimes(1)

    resetSavedViewsSessionForTests()
    document.body.innerHTML = ''
    const full = fakeServer([VIEW])
    const first = host()
    const firstOwner = indexOwner('main', 1)
    presentSavedViewsIndex({ owner: firstOwner, host: first, generation: 1 })
    await flush()
    dismissRouteSurface(firstOwner)
    full.refuse('GET /api/saved-views', { status: 500, body: null })
    const second = host()
    presentSavedViewsIndex({ owner: indexOwner('main', 2), host: second, generation: 2 })
    await flush()
    expect(full.count('GET /api/saved-views')).toBe(2)
    expect(rowIds(second)).toEqual(['SV 1'])
    expect(second.querySelector('[data-saved-views-refresh-error]')?.textContent?.trim()).toBe(
      'Could not refresh Saved Views.',
    )
  })

  it('does not claim an empty index when refreshing an empty list fails', async () => {
    chrome()
    const server = fakeServer([])
    const first = host()
    const firstOwner = indexOwner('main', 1)
    presentSavedViewsIndex({ owner: firstOwner, host: first, generation: 1 })
    await flush()
    expect(first.querySelector('.saved-views-page__empty')).not.toBeNull()
    dismissRouteSurface(firstOwner)
    server.refuse('GET /api/saved-views', { status: 500, body: null })
    const second = host()
    presentSavedViewsIndex({ owner: indexOwner('main', 2), host: second, generation: 2 })
    await flush()
    expect(second.querySelector('[data-saved-views-refresh-error]')).not.toBeNull()
    expect(second.querySelector('.saved-views-page__empty')).toBeNull()
    expect(second.querySelector('#prks-saved-views-empty-search')).toBeNull()
  })

  it('refreshes icons on mount even when the first read fails, and again when rows arrive', async () => {
    chrome()
    const refresh = vi.fn()
    window.prksRefreshIcons = refresh
    const server = fakeServer([VIEW])
    server.refuse('GET /api/saved-views', { status: 500, body: null })
    const main = host()
    presentSavedViewsIndex({ owner: indexOwner('main', 1), host: main, generation: 1 })
    await flush()
    expect(main.querySelector('[data-saved-views-load-error]')).not.toBeNull()
    expect(refresh).toHaveBeenCalledWith(main.querySelector('.saved-views-page'))
    const afterMount = refresh.mock.calls.length
    main.querySelector<HTMLButtonElement>('[data-saved-views-load-error] button')?.click()
    await flush()
    expect(rowIds(main)).toEqual(['SV 1'])
    expect(refresh.mock.calls.length).toBeGreaterThan(afterMount)
    expect(refresh.mock.calls.every(([root]) => root === main.querySelector('.saved-views-page'))).toBe(true)
  })

  it('drops a stale generation and keeps owners apart', async () => {
    chrome()
    fakeServer([VIEW])
    const main = owner('main')
    main.state.generation = 2
    main.state.routeName = 'saved-views'
    const mainHost = host()
    const otherHost = host()
    presentSavedViewsIndex({ owner: main, host: mainHost, generation: 2 })
    presentSavedViewsIndex({ owner: indexOwner('other', 1), host: otherHost, generation: 1 })
    await flush()
    presentSavedViewsIndex({ owner: main, host: mainHost, generation: 1 })
    await flush()
    expect(rowIds(mainHost)).toEqual(['SV 1'])
    dismissRouteSurface(main)
    expect(mainHost.innerHTML).toBe('')
    expect(rowIds(otherHost)).toEqual(['SV 1'])
  })

  it('registers the records bridge and paints an early host', async () => {
    chrome()
    fakeServer([VIEW])
    const el = host()
    el.setAttribute('data-prks-vue-route-host', 'true')
    ;(el as HTMLElement & { __prksVueRouteRequest?: object }).__prksVueRouteRequest = {
      feature: 'saved-views',
      owner: indexOwner('main', 1),
      generation: 1,
    }
    registerSavedViewsBridge(window)
    expect(window.prksVuePresentRoute).toBeTypeOf('function')
    expect(window.prksSavedViewRecords?.get).toBeTypeOf('function')
    await flush()
    expect(el.querySelector('.saved-views-page__summary')?.textContent).toBe('All: x')
  })
})

describe('Saved View detail route', () => {
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
    expect(viewHost.querySelector('[data-prks-search-results] .project-card--work-card')?.getAttribute('data-work-id')).toBe(
      searchHost.querySelector('[data-prks-search-results] .project-card--work-card')?.getAttribute('data-work-id'),
    )
    expect(viewHost.querySelector('[data-work-id="w1"] .work-card__context')?.textContent).toBe('ab…')
    expect(readRouteSurface(viewOwner)).toMatchObject({ name: 'saved-view-detail', canonicalHash: '#/views/SV%201' })
  })

  it('edits through the shared modal and deletes, then sends only this owner back to the index', async () => {
    cards()
    const server = fakeServer([VIEW])
    const open = vi.fn()
    const navigate = vi.fn()
    window.prksOpenSavedViewModal = open
    window.prksConfirmDestructive = async () => true
    window.prksNavigate = navigate
    const pane = owner('main')
    pane.state.generation = 4
    pane.state.entityId = 'SV 1'
    const el = host()
    presentSavedViewDetail({ owner: pane, host: el, availability: 'ready', view: VIEW, rows: [], generation: 4 })
    ;(el.querySelector('#prks-saved-view-edit') as HTMLButtonElement).click()
    expect(open).toHaveBeenCalledWith({ viewId: 'SV 1', name: 'Critical theory', definition: VIEW.search })
    ;(el.querySelector('#prks-saved-view-delete') as HTMLButtonElement).click()
    await flush()
    expect(server.count('DELETE /api/saved-views/SV%201')).toBe(1)
    expect(navigate).toHaveBeenCalledWith('#/views', { replace: true, tabId: 'main' })
    pane.state.generation = 5
    ;(el.querySelector('#prks-saved-view-edit') as HTMLButtonElement).click()
    ;(el.querySelector('#prks-saved-view-delete') as HTMLButtonElement).click()
    await flush()
    expect(open).toHaveBeenCalledTimes(1)
    expect(server.count('DELETE /api/saved-views/SV%201')).toBe(1)
  })

  it('keeps detail delete busy until the server answers and shows a failure inline', async () => {
    cards()
    const server = fakeServer([VIEW])
    window.prksConfirmDestructive = async () => true
    const navigate = vi.fn()
    window.prksNavigate = navigate
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
    expect(button().classList.contains('prks-btn--danger')).toBe(true)
    expect(button().textContent?.trim()).toBe('Delete Saved View')

    server.refuse('DELETE /api/saved-views/SV%201', { status: 500, body: null })
    const release = server.hold('DELETE /api/saved-views/SV%201')
    button().click()
    await flush()
    expect(button().disabled).toBe(true)
    expect(button().getAttribute('aria-busy')).toBe('true')
    expect(button().textContent).toBe('Deleting…')
    button().click()
    expect((otherHost.querySelector('#prks-saved-view-delete') as HTMLButtonElement).disabled).toBe(false)
    release()
    await flush()
    expect(server.count('DELETE /api/saved-views/SV%201')).toBe(1)
    expect(button().disabled).toBe(false)
    expect(button().getAttribute('aria-busy')).toBeNull()
    expect(mainHost.querySelector('[data-sv-delete-error]')?.textContent?.trim()).toBe('Could not delete Saved View.')
    expect(otherHost.querySelector('[data-sv-delete-error]')).toBeNull()
    expect(navigate).not.toHaveBeenCalled()
  })

  it('paints not-found and a read failure, ignores a stale generation, and keeps owners apart', async () => {
    cards()
    const main = owner('main')
    const other = owner('other')
    const mainHost = host()
    const otherHost = host()
    const failedHost = host()
    presentSavedViewDetail({ owner: main, host: mainHost, availability: 'not-found', viewId: 'gone', generation: 3 })
    presentSavedViewDetail({ owner: other, host: otherHost, availability: 'ready', view: VIEW, rows: [{ id: 'w2' }], generation: 1 })
    presentSavedViewDetail({ owner: owner('third'), host: failedHost, availability: 'error', viewId: 'SV 1', generation: 1 })
    expect(mainHost.querySelector('.prks-page-title')?.textContent).toBe('Saved View not found.')
    expect(mainHost.querySelector('a[href="#/views"]')).not.toBeNull()
    expect(failedHost.querySelector('[data-prks-saved-view-load-error] .prks-page-title')?.textContent).toBe(
      'Could not load Saved View.',
    )
    expect(failedHost.querySelector('[data-prks-saved-view-not-found]')).toBeNull()
    expect(mainHost.querySelector('[data-work-id="w2"]')).toBeNull()
    presentSavedViewDetail({ owner: main, host: mainHost, availability: 'ready', view: VIEW, rows: [{ id: 'late' }], generation: 2 })
    await nextTick()
    expect(mainHost.querySelector('[data-work-id="late"]')).toBeNull()
    dismissRouteSurface(main)
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
