import { nextTick } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { readRouteSurface } from '../../route-surface/lifecycle'
import {
  dismissPositions,
  POSITIONS_RETAIN_SURFACE_KEY,
  presentPositionDetail,
  presentPositionsIndex,
  registerPositionsBridge,
  resetPositionsSessionForTests,
} from './session'

afterEach(() => {
  resetPositionsSessionForTests()
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
  delete window.prksVuePresentRoute
  delete window.prksVueDismissPositions
  delete window.prksPageHeaderIconHtml
  delete window.prksIcon
  delete window.prksPaintScopeHost
  delete window.prksRefreshIcons
  delete window.prksRelSummaryHtml
  delete window.prksResearchSectionHeadHtml
  delete window.prksResearchIndexRowHtml
  delete window.prksResearchMarkdownHtml
  delete window.prksEscapeHtml
})

function host(): HTMLElement {
  const el = document.createElement('div')
  document.body.appendChild(el)
  return el
}

function owner(tabId = 'tab') {
  return { tabId, isCurrent: () => true }
}

function paintHelpers(): void {
  window.prksPageHeaderIconHtml = () => ''
  window.prksIcon = () => '<svg data-icon="flag"></svg>'
  window.prksPaintScopeHost = () => {}
  window.prksRefreshIcons = () => {}
  window.prksEscapeHtml = (value) =>
    String(value ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
  window.prksResearchMarkdownHtml = (text) => `<p>${text || ''}</p>`
  window.prksRelSummaryHtml = ({ parts }) =>
    `<p class="prks-rel-summary">${(parts || []).filter(Boolean).join(' · ')}</p>`
  window.prksResearchSectionHeadHtml = (title, opts) => {
    const count =
      opts?.count != null ? `<span class="research-entity__section-count">${opts.count}</span>` : ''
    return `<div class="research-entity__section-head"><h3 id="${opts?.headingId || ''}">${title}</h3>${count}</div>`
  }
  window.prksResearchIndexRowHtml = (opts) =>
    `<a class="prks-list-row prks-research-row" href="${opts.href}"><span class="prks-research-row__title">${opts.title}</span></a>`
}

describe('Positions route bridge', () => {
  it('renders independent Main and Secondary index owners', () => {
    paintHelpers()
    const main = owner('main')
    const secondary = owner('side')
    const mainHost = host()
    const secondaryHost = host()
    presentPositionsIndex({
      owner: main,
      host: mainHost,
      items: [{ id: 'P1', name: 'Main Position', description: '' }],
      generation: 4,
      shell: true,
    })
    presentPositionsIndex({
      owner: secondary,
      host: secondaryHost,
      items: [{ id: 'P2', name: 'Side Position', description: '' }],
      generation: 1,
      shell: false,
    })
    const mainTitle = mainHost.querySelector('.prks-page-title')?.textContent || ''
    expect(mainTitle).toContain('Positions')
    expect(mainHost.textContent).toContain('Main Position')
    expect(secondaryHost.textContent?.includes('Side Position')).toBe(true)
    expect(mainHost.textContent?.includes('Side Position')).toBe(false)
    const mainSurface = readRouteSurface(main)
    expect(mainSurface?.name).toBe('positions')
    expect(mainSurface?.canonicalHash).toBe('#/positions')
    expect(mainSurface?.ownsMainShell).toBe(true)
    expect(readRouteSurface(secondary)?.ownsMainShell).toBe(false)
  })

  it('paints ready, empty, and unavailable index states without fetch', () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    paintHelpers()
    const pane = owner()
    const el = host()
    presentPositionsIndex({
      owner: pane,
      host: el,
      items: [],
      generation: 1,
    })
    expect(el.textContent).toContain('No Positions yet.')
    expect(el.querySelector('#prks-position-new-empty')).not.toBeNull()
    presentPositionsIndex({
      owner: pane,
      host: el,
      availability: 'unavailable',
      items: [{ id: 'stale', name: 'Should not show' }],
      generation: 2,
    })
    expect(el.querySelector('[data-prks-role="offline-unavailable"]')).not.toBeNull()
    expect(el.textContent).not.toContain('No Positions yet.')
    expect(el.textContent).not.toContain('Should not show')
    expect(el.querySelector('#prks-position-new')).toBeNull()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('does not repaint a dismissed generation and drops the tree on unmount', async () => {
    paintHelpers()
    const pane = owner()
    const el = host()
    presentPositionsIndex({
      owner: pane,
      host: el,
      items: [{ id: 'a', name: 'A', description: '' }],
      generation: 2,
    })
    dismissPositions(pane)
    expect(el.querySelector('[data-prks-positions-index-view]')).toBeNull()
    presentPositionsIndex({
      owner: pane,
      host: el,
      items: [{ id: 'b', name: 'B', description: '' }],
      generation: 2,
    })
    await nextTick()
    expect(el.textContent).not.toContain('B')
    presentPositionsIndex({
      owner: pane,
      host: el,
      items: [{ id: 'b', name: 'B', description: '' }],
      generation: 3,
    })
    expect(el.textContent).toContain('B')
  })

  it('paints detail ready, not-found, and unavailable without fetch', () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    paintHelpers()
    const pane = owner()
    const el = host()
    presentPositionDetail({
      owner: pane,
      host: el,
      position: {
        id: 'P9',
        name: 'Nine',
        description: 'A definition',
        arguments: [
          { id: 'A1', name: 'Pending argument name', kind: 'argument', verdict_label: 'Supports' },
        ],
      },
      positionId: 'P9',
      generation: 1,
    })
    expect(el.querySelector('#prks-position-view-graph')).not.toBeNull()
    expect(el.textContent).toContain('Nine')
    expect(el.textContent).toContain('Pending argument name')
    expect(el.querySelector('[data-prks-role="position-argument-link"]')).not.toBeNull()
    expect(el.querySelector('.research-md')?.textContent).toContain('A definition')
    expect(readRouteSurface(pane)?.canonicalHash).toBe('#/positions/P9')
    expect(fetchMock).not.toHaveBeenCalled()

    presentPositionDetail({
      owner: pane,
      host: el,
      availability: 'not-found',
      positionId: 'missing',
      generation: 2,
    })
    expect(el.textContent).toContain('Position not found')
    expect(el.querySelector('a[href="#/positions"]')).not.toBeNull()

    presentPositionDetail({
      owner: pane,
      host: el,
      availability: 'unavailable',
      position: { id: 'P9', name: 'Nine' },
      positionId: 'P9',
      generation: 3,
    })
    expect(el.querySelector('[data-prks-role="offline-unavailable"]')).not.toBeNull()
    expect(el.textContent).not.toContain('Nine')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('shows a pending local Position that has no arguments snapshot', () => {
    paintHelpers()
    const pane = owner('local')
    const el = host()
    presentPositionDetail({
      owner: pane,
      host: el,
      position: { id: 'P-local', name: 'Unsent claim', description: '' },
      positionId: 'P-local',
      generation: 1,
    })
    expect(el.textContent).toContain('Unsent claim')
    expect(el.textContent).toContain('No description yet.')
    expect(el.textContent).toContain('No Arguments or Stances target this Position yet.')
    expect(readRouteSurface(pane)?.canonicalHash).toBe('#/positions/P-local')
  })

  it('refreshes an effective rename and argument-name overlay without keeping the previous name', async () => {
    paintHelpers()
    const pane = owner('detail')
    const el = host()
    presentPositionDetail({
      owner: pane,
      host: el,
      position: {
        id: 'P1',
        name: 'Original title',
        description: 'before',
        arguments: [{ id: 'A1', name: 'Server argument', kind: 'stance', verdict_label: 'Old' }],
      },
      generation: 1,
    })
    expect(el.textContent).toContain('Original title')
    presentPositionDetail({
      owner: pane,
      host: el,
      position: {
        id: 'P1',
        name: 'Renamed title',
        description: 'after the edit',
        arguments: [{ id: 'A1', name: 'Pending argument name', kind: 'stance', verdict_label: 'New' }],
      },
      generation: 2,
    })
    await nextTick()
    expect(el.textContent).toContain('Renamed title')
    expect(el.textContent).not.toContain('Original title')
    expect(el.textContent).toContain('Pending argument name')
    expect(el.textContent).not.toContain('Server argument')
    expect(el.querySelector('.research-md')?.innerHTML).toContain('after the edit')
  })

  it('keeps index search across a retained same-route refresh and clears on leave', async () => {
    paintHelpers()
    const cleanups = new Set<() => void>()
    const pane: {
      tabId: string
      isCurrent: () => boolean
      registerCleanup: (fn: () => void) => () => void
      [POSITIONS_RETAIN_SURFACE_KEY]?: boolean
    } = {
      tabId: 'coord',
      isCurrent: () => true,
      registerCleanup(fn: () => void) {
        cleanups.add(fn)
        return () => {
          cleanups.delete(fn)
        }
      },
    }
    const contentDiv = document.createElement('div')
    document.body.appendChild(contentDiv)
    const routeHost = document.createElement('div')
    routeHost.setAttribute('data-prks-vue-route-host', 'true')
    contentDiv.appendChild(routeHost)
    const items = [
      { id: 'P1', name: 'Alpha', description: 'one' },
      { id: 'P2', name: 'Beta', description: 'two' },
    ]
    presentPositionsIndex({ owner: pane, host: routeHost, items, generation: 1 })
    const search = routeHost.querySelector<HTMLInputElement>('#prks-position-search')
    expect(search).not.toBeNull()
    search!.value = 'alp'
    search!.dispatchEvent(new Event('input'))
    await nextTick()
    expect(routeHost.textContent).toContain('Alpha')
    expect(routeHost.textContent).not.toContain('Beta')

    pane[POSITIONS_RETAIN_SURFACE_KEY] = true
    const beginRouteCleanups = Array.from(cleanups)
    cleanups.clear()
    beginRouteCleanups.forEach((fn) => fn())
    pane[POSITIONS_RETAIN_SURFACE_KEY] = false
    expect(routeHost.querySelector('[data-prks-positions-index-view]')).not.toBeNull()

    presentPositionsIndex({
      owner: pane,
      host: routeHost,
      items: [...items, { id: 'P3', name: 'Gamma', description: 'three' }],
      generation: 2,
    })
    await nextTick()
    expect(routeHost.querySelector<HTMLInputElement>('#prks-position-search')?.value).toBe('alp')
    expect(routeHost.textContent).toContain('Alpha')
    expect(routeHost.textContent).not.toContain('Beta')

    const leaveCleanups = Array.from(cleanups)
    cleanups.clear()
    leaveCleanups.forEach((fn) => fn())
    expect(routeHost.querySelector('[data-prks-positions-index-view]')).toBeNull()
  })

  it('unmounts a failed retained refresh before the retry view and on later leave', async () => {
    paintHelpers()
    const cleanups = new Set<() => void>()
    const pane: {
      tabId: string
      isCurrent: () => boolean
      registerCleanup: (fn: () => void) => () => void
      [POSITIONS_RETAIN_SURFACE_KEY]?: boolean
    } = {
      tabId: 'coord',
      isCurrent: () => true,
      registerCleanup(fn: () => void) {
        cleanups.add(fn)
        return () => {
          cleanups.delete(fn)
        }
      },
    }
    const contentDiv = document.createElement('div')
    document.body.appendChild(contentDiv)
    const routeHost = document.createElement('div')
    routeHost.setAttribute('data-prks-vue-route-host', 'true')
    contentDiv.appendChild(routeHost)
    presentPositionsIndex({
      owner: pane,
      host: routeHost,
      items: [{ id: 'P1', name: 'Kept', description: 'still here' }],
      generation: 1,
    })
    expect(routeHost.querySelector('[data-prks-positions-index-view]')).not.toBeNull()

    pane[POSITIONS_RETAIN_SURFACE_KEY] = true
    const drained = Array.from(cleanups)
    cleanups.clear()
    drained.forEach((fn) => fn())
    pane[POSITIONS_RETAIN_SURFACE_KEY] = false
    expect(cleanups.size).toBe(1)
    expect(routeHost.textContent).toContain('Kept')

    dismissPositions(pane)
    expect(routeHost.querySelector('[data-prks-positions-index-view]')).toBeNull()
    expect(readRouteSurface(pane)?.mounted).toBe(false)
    contentDiv.innerHTML = '<p><button type="button" id="prks-route-retry">Retry</button></p>'
    expect(contentDiv.querySelector('#prks-route-retry')).not.toBeNull()
    expect(contentDiv.querySelector('[data-prks-positions-index-view]')).toBeNull()

    const leave = Array.from(cleanups)
    cleanups.clear()
    leave.forEach((fn) => fn())
    expect(readRouteSurface(pane)?.mounted).toBe(false)
    expect(document.body.querySelector('[data-prks-positions-index-view]')).toBeNull()
  })

  it('registers bridges and applies host-local early requests', () => {
    paintHelpers()
    const el = host()
    const decoy = host()
    el.setAttribute('data-prks-vue-route-host', 'true')
    decoy.setAttribute('data-prks-vue-route-host', 'true')
    const pane = owner('early')
    ;(el as HTMLElement & { __prksVueRouteRequest?: object }).__prksVueRouteRequest = {
      feature: 'positions',
      owner: pane,
      host: decoy,
      items: [{ id: 'P1', name: 'Early', description: '' }],
      generation: 1,
      shell: true,
    }
    registerPositionsBridge(window)
    expect(window.prksVuePresentRoute).toBeTypeOf('function')
    expect(el.textContent).toContain('Early')
    expect(decoy.textContent).not.toContain('Early')
    expect(readRouteSurface(pane)?.canonicalHash).toBe('#/positions')
  })

  it('unmounts one owner without affecting the other', () => {
    paintHelpers()
    const a = owner('a')
    const b = owner('b')
    const aHost = host()
    const bHost = host()
    presentPositionsIndex({
      owner: a,
      host: aHost,
      items: [{ id: '1', name: 'Alpha', description: '' }],
      generation: 1,
    })
    presentPositionsIndex({
      owner: b,
      host: bHost,
      items: [{ id: '2', name: 'Beta', description: '' }],
      generation: 1,
    })
    dismissPositions(a)
    expect(aHost.querySelector('[data-prks-positions-index-view]')).toBeNull()
    expect(bHost.textContent).toContain('Beta')
  })
})
