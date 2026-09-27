import { nextTick } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { readRouteSurface } from '../../route-surface/lifecycle'
import {
  CONCEPTS_RETAIN_SURFACE_KEY,
  dismissConcepts,
  presentConceptDetail,
  presentConceptsIndex,
  registerConceptsBridge,
  resetConceptsSessionForTests,
} from './session'

afterEach(() => {
  resetConceptsSessionForTests()
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
  delete window.prksVuePresentConceptsIndex
  delete window.prksVuePresentConceptDetail
  delete window.prksVueDismissConcepts
  delete window.prksCreateConceptFlow
  delete window.prksResearchMarkdownHtml
  delete window.prksPageHeaderIconHtml
  delete window.prksIcon
  delete window.prksPaintScopeHost
  delete window.prksRefreshIcons
  delete window.prksRelSummaryHtml
  delete window.prksResearchSectionHeadHtml
  delete window.prksResearchIndexRowHtml
})

function host(): HTMLElement {
  const el = document.createElement('div')
  document.body.appendChild(el)
  return el
}

function owner(tabId = 'tab') {
  return { tabId, isCurrent: () => true }
}

describe('Concepts route bridge', () => {
  it('renders independent Main and Secondary index owners', () => {
    window.prksPageHeaderIconHtml = () => ''
    window.prksIcon = () => ''
    window.prksPaintScopeHost = () => {}
    window.prksRefreshIcons = () => {}
    const main = owner('main')
    const secondary = owner('side')
    const mainHost = host()
    const secondaryHost = host()
    presentConceptsIndex({
      owner: main,
      host: mainHost,
      items: [{ id: 'C1', name: 'Main Concept' }],
      generation: 4,
      shell: true,
    })
    presentConceptsIndex({
      owner: secondary,
      host: secondaryHost,
      items: [{ id: 'C2', name: 'Side Concept' }],
      generation: 1,
      shell: false,
    })
    expect(mainHost.querySelector('.prks-page-title')?.textContent).toContain('Concepts')
    expect(mainHost.textContent).toContain('Main Concept')
    expect(secondaryHost.textContent).toContain('Side Concept')
    expect(mainHost.textContent).not.toContain('Side Concept')
    expect(readRouteSurface(main)).toMatchObject({
      name: 'concepts',
      canonicalHash: '#/concepts',
      ownsMainShell: true,
    })
    expect(readRouteSurface(secondary)?.ownsMainShell).toBe(false)
  })

  it('does not fetch and does not repaint a dismissed generation', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    window.prksPageHeaderIconHtml = () => ''
    window.prksIcon = () => ''
    window.prksPaintScopeHost = () => {}
    window.prksRefreshIcons = () => {}
    const pane = owner()
    const el = host()
    presentConceptsIndex({
      owner: pane,
      host: el,
      items: [{ id: 'a', name: 'A' }],
      generation: 2,
    })
    expect(fetchMock).not.toHaveBeenCalled()
    dismissConcepts(pane)
    expect(el.querySelector('[data-prks-concepts-index-view]')).toBeNull()
    presentConceptsIndex({
      owner: pane,
      host: el,
      items: [{ id: 'b', name: 'B' }],
      generation: 2,
    })
    await nextTick()
    expect(el.textContent).not.toContain('B')
    presentConceptsIndex({
      owner: pane,
      host: el,
      items: [{ id: 'b', name: 'B' }],
      generation: 3,
    })
    expect(el.textContent).toContain('B')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('paints detail and unavailable / not-found states without fetch', () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    window.prksResearchMarkdownHtml = (text) => `<p>${text || ''}</p>`
    window.prksRelSummaryHtml = () => '<p class="prks-rel-summary"></p>'
    window.prksResearchSectionHeadHtml = (title, opts) => {
      const o = opts || {}
      const action = o.actionId
        ? `<button type="button" id="${o.actionId}" data-prks-role="${o.actionRole || ''}">${
            o.actionLabel || 'Edit'
          }</button>`
        : ''
      const count =
        o.count != null ? `<span class="research-entity__section-count">${o.count}</span>` : ''
      return (
        `<div class="research-entity__section-head"><h3 id="${o.headingId || ''}">${title}</h3>` +
        (action || count
          ? `<div class="research-entity__section-head-actions">${count}${action}</div>`
          : '') +
        `</div>` +
        (o.sub ? `<p class="research-entity__section-sub meta-row">${o.sub}</p>` : '')
      )
    }
    window.prksRefreshIcons = () => {}
    const pane = owner()
    const el = host()
    presentConceptDetail({
      owner: pane,
      host: el,
      concept: {
        id: 'C9',
        name: 'Nine',
        description: 'Def',
        aliases: ['n'],
        parents: [],
        children: [],
        mentions: [],
        mention_count: 0,
      },
      generation: 1,
    })
    expect(el.querySelector('#prks-concept-edit-def')).not.toBeNull()
    expect(el.querySelector('#prks-concept-view-graph')).not.toBeNull()
    expect(el.querySelector('#prks-concept-delete')?.className).toContain('prks-btn--quiet-danger')
    expect(el.querySelector('.research-entity__section-head')).not.toBeNull()
    dismissConcepts(pane)
    presentConceptDetail({
      owner: pane,
      host: el,
      availability: 'not-found',
      generation: 2,
    })
    expect(el.textContent).toContain('Concept not found')
    presentConceptDetail({
      owner: pane,
      host: el,
      availability: 'unavailable',
      generation: 3,
    })
    expect(el.querySelector('[data-prks-role="offline-unavailable"]')).not.toBeNull()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('registers bridges and applies host-local early requests', () => {
    window.prksPageHeaderIconHtml = () => ''
    window.prksIcon = () => ''
    window.prksPaintScopeHost = () => {}
    window.prksRefreshIcons = () => {}
    const el = host()
    const decoy = host()
    el.setAttribute('data-prks-vue-route-host', 'true')
    decoy.setAttribute('data-prks-vue-route-host', 'true')
    const pane = owner()
    ;(el as HTMLElement & { __prksVueRouteRequest?: object }).__prksVueRouteRequest = {
      feature: 'concepts',
      owner: pane,
      host: decoy,
      items: [{ id: 'early', name: 'Early' }],
      generation: 1,
      shell: true,
    }
    registerConceptsBridge(window)
    expect(window.prksVuePresentConceptsIndex).toBeTypeOf('function')
    expect(window.prksVuePresentConceptDetail).toBeTypeOf('function')
    expect(el.textContent).toContain('Early')
    expect(decoy.textContent).not.toContain('Early')
  })

  it('repaints the relation summary when parents/mentions change in place', async () => {
    window.prksResearchMarkdownHtml = (text) => `<p>${text || ''}</p>`
    window.prksResearchSectionHeadHtml = () => '<div class="research-entity__section-head"></div>'
    window.prksRefreshIcons = () => {}
    const summaries: string[] = []
    window.prksRelSummaryHtml = (opts) => {
      const text = (opts.parts || []).filter(Boolean).join(' · ')
      summaries.push(text)
      return `<p class="prks-rel-summary" data-summary="${text}"></p>`
    }
    const pane = owner()
    const el = host()
    presentConceptDetail({
      owner: pane,
      host: el,
      concept: {
        id: 'C9',
        name: 'Nine',
        description: 'Def',
        aliases: [],
        parents: [],
        children: [],
        mentions: [],
        mention_count: 0,
      },
      generation: 7,
    })
    expect(el.querySelector('[data-summary]')?.getAttribute('data-summary') || '').toBe('')
    presentConceptDetail({
      owner: pane,
      host: el,
      concept: {
        id: 'C9',
        name: 'Nine',
        description: 'Def',
        aliases: [],
        parents: [{ id: 'P1', name: 'Parent' }],
        children: [],
        mentions: [],
        mention_count: 3,
      },
      generation: 7,
    })
    await nextTick()
    expect(el.querySelector('[data-summary]')?.getAttribute('data-summary')).toContain('1 parent')
    expect(el.querySelector('[data-summary]')?.getAttribute('data-summary')).toContain(
      '3 note mentions',
    )
    expect(summaries.some((s) => s.includes('1 parent') && s.includes('3 note mentions'))).toBe(
      true,
    )
  })

  it('keeps index searchQuery across same-generation in-place projection updates', async () => {
    window.prksPageHeaderIconHtml = () => ''
    window.prksIcon = () => ''
    window.prksPaintScopeHost = () => {}
    window.prksRefreshIcons = () => {}
    const pane = owner('stable')
    const el = host()
    presentConceptsIndex({
      owner: pane,
      host: el,
      items: [
        { id: 'C1', name: 'Alpha', aliases: [], parents: [], subconcept_count: 0, mention_count: 0 },
        { id: 'C2', name: 'Beta', aliases: [], parents: [], subconcept_count: 0, mention_count: 0 },
      ],
      generation: 1,
    })
    const search = el.querySelector<HTMLInputElement>('#prks-concept-search')
    expect(search).not.toBeNull()
    search!.value = 'alp'
    search!.dispatchEvent(new Event('input'))
    await nextTick()
    expect(search!.value).toBe('alp')
    presentConceptsIndex({
      owner: pane,
      host: el,
      items: [
        { id: 'C1', name: 'Alpha', aliases: [], parents: [], subconcept_count: 0, mention_count: 0 },
        { id: 'C2', name: 'Beta', aliases: [], parents: [], subconcept_count: 0, mention_count: 0 },
        { id: 'C3', name: 'Gamma', aliases: [], parents: [], subconcept_count: 0, mention_count: 0 },
      ],
      generation: 1,
    })
    await nextTick()
    const searchAfter = el.querySelector<HTMLInputElement>('#prks-concept-search')
    expect(searchAfter?.value).toBe('alp')
    expect(el.textContent).toContain('Alpha')
    expect(el.textContent).not.toContain('Beta')
  })

  it('keeps searchQuery across production-style retain + same-host Concepts refresh', async () => {
    // Mirrors prksRenderTabRoute: beginRoute runs owner cleanups with the
    // retain flag set, then presents again into the same host with a new
    // generation — without wiping contentDiv / minting a replacement host.
    window.prksPageHeaderIconHtml = () => ''
    window.prksIcon = () => ''
    window.prksPaintScopeHost = () => {}
    window.prksRefreshIcons = () => {}
    const cleanups = new Set<() => void>()
    const pane: {
      tabId: string
      isCurrent: () => boolean
      registerCleanup: (fn: () => void) => () => void
      [CONCEPTS_RETAIN_SURFACE_KEY]?: boolean
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
      { id: 'C1', name: 'Alpha', aliases: [], parents: [], subconcept_count: 0, mention_count: 0 },
      { id: 'C2', name: 'Beta', aliases: [], parents: [], subconcept_count: 0, mention_count: 0 },
    ]
    presentConceptsIndex({
      owner: pane,
      host: routeHost,
      items,
      generation: 1,
    })
    const search = routeHost.querySelector<HTMLInputElement>('#prks-concept-search')
    expect(search).not.toBeNull()
    search!.value = 'alp'
    search!.dispatchEvent(new Event('input'))
    await nextTick()
    expect(search!.value).toBe('alp')
    expect(routeHost.querySelector('[data-prks-concepts-index-view]')).not.toBeNull()

    pane[CONCEPTS_RETAIN_SURFACE_KEY] = true
    const beginRouteCleanups = Array.from(cleanups)
    cleanups.clear()
    beginRouteCleanups.forEach((fn) => fn())
    pane[CONCEPTS_RETAIN_SURFACE_KEY] = false

    expect(routeHost.querySelector('[data-prks-concepts-index-view]')).not.toBeNull()
    expect(routeHost.querySelector<HTMLInputElement>('#prks-concept-search')?.value).toBe('alp')
    // Production reuses :scope > [data-prks-vue-route-host] — same node.
    const reused =
      contentDiv.querySelector(':scope > [data-prks-vue-route-host]') === routeHost
    expect(reused).toBe(true)
    presentConceptsIndex({
      owner: pane,
      host: routeHost,
      items: [
        ...items,
        { id: 'C3', name: 'Gamma', aliases: [], parents: [], subconcept_count: 0, mention_count: 0 },
      ],
      generation: 2,
    })
    await nextTick()
    expect(routeHost.querySelector<HTMLInputElement>('#prks-concept-search')?.value).toBe('alp')
    expect(routeHost.textContent).toContain('Alpha')
    expect(routeHost.textContent).not.toContain('Beta')

    // Leave Concepts: cleanup without retain tears the tree down.
    const leaveCleanups = Array.from(cleanups)
    cleanups.clear()
    leaveCleanups.forEach((fn) => fn())
    expect(routeHost.querySelector('[data-prks-concepts-index-view]')).toBeNull()
  })

  it('presenter leaves contentDiv sibling provenance banners alone', async () => {
    // Banner clear is owned by app.js prksOfflinePrependBanner on unavailable /
    // not-found (see coordinator E2E). Vue present must not wipe contentDiv.
    window.prksPageHeaderIconHtml = () => ''
    window.prksIcon = () => ''
    window.prksPaintScopeHost = () => {}
    window.prksRefreshIcons = () => {}
    const pane = owner('banner-sibling')
    const contentDiv = document.createElement('div')
    document.body.appendChild(contentDiv)
    const banner = document.createElement('div')
    banner.setAttribute('data-prks-role', 'offline-provenance-banner')
    banner.textContent = 'Offline · cached earlier'
    contentDiv.appendChild(banner)
    const routeHost = document.createElement('div')
    routeHost.setAttribute('data-prks-vue-route-host', 'true')
    contentDiv.appendChild(routeHost)
    presentConceptDetail({
      owner: pane,
      host: routeHost,
      availability: 'unavailable',
      conceptId: 'missing',
      generation: 1,
    })
    await nextTick()
    expect(contentDiv.querySelectorAll('[data-prks-role="offline-provenance-banner"]').length).toBe(1)
    expect(routeHost.querySelector('[data-prks-role="offline-unavailable"]')).not.toBeNull()
  })

  it('unmounts on dismiss without affecting another owner', () => {
    window.prksPageHeaderIconHtml = () => ''
    window.prksIcon = () => ''
    window.prksPaintScopeHost = () => {}
    window.prksRefreshIcons = () => {}
    const a = owner('a')
    const b = owner('b')
    const aHost = host()
    const bHost = host()
    presentConceptsIndex({
      owner: a,
      host: aHost,
      items: [{ id: '1', name: 'Alpha' }],
      generation: 1,
    })
    presentConceptsIndex({
      owner: b,
      host: bHost,
      items: [{ id: '2', name: 'Beta' }],
      generation: 1,
    })
    dismissConcepts(a)
    expect(aHost.querySelector('[data-prks-concepts-index-view]')).toBeNull()
    expect(bHost.textContent).toContain('Beta')
  })
})
