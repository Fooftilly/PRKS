import { nextTick } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { readRouteSurface } from '../../route-surface/lifecycle'
import {
  ARGUMENTS_RETAIN_SURFACE_KEY,
  dismissArguments,
  presentArgumentDetail,
  presentArgumentsIndex,
  registerArgumentsBridge,
  resetArgumentsSessionForTests,
} from './session'

afterEach(() => {
  resetArgumentsSessionForTests()
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
  delete window.prksVuePresentArgumentsIndex
  delete window.prksVuePresentArgumentDetail
  delete window.prksVueDismissArguments
  delete window.prksPageHeaderIconHtml
  delete window.prksIcon
  delete window.prksPaintScopeHost
  delete window.prksRefreshIcons
  delete window.prksResearchSectionHeadHtml
  delete window.prksResearchIndexRowHtml
  delete window.prksResearchMarkdownHtml
  delete window.prksEscapeHtml
  delete window.prksPrepareArgumentEdit
  delete window.prksCommitArgumentEditorDraft
  delete window.prksTabContextOwnsEntityRoute
  delete window.prksNavigate
  delete window.prksAlertDialog
})

function host(): HTMLElement {
  const el = document.createElement('div')
  document.body.appendChild(el)
  return el
}

function owner(tabId = 'tab') {
  return {
    tabId,
    isCurrent: () => true,
    ui: { argumentEditing: false },
    lastResolvedRoute: { name: 'argument-detail' as const },
    getEntity: () => ({ id: 'A1' }),
  }
}

function paintHelpers(): void {
  window.prksPageHeaderIconHtml = () => ''
  window.prksIcon = () => '<svg data-icon="messages-square"></svg>'
  window.prksPaintScopeHost = () => {}
  window.prksRefreshIcons = () => {}
  window.prksEscapeHtml = (value) =>
    String(value ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
  window.prksResearchMarkdownHtml = (text) => `<p>${text || ''}</p>`
  window.prksResearchSectionHeadHtml = (title, opts) => {
    const count =
      opts?.count != null ? `<span class="research-entity__section-count">${opts.count}</span>` : ''
    return `<div class="research-entity__section-head"><h3 id="${opts?.headingId || ''}">${title}</h3>${count}</div>`
  }
  window.prksResearchIndexRowHtml = (opts) =>
    `<a class="prks-list-row prks-research-row" href="${opts.href}"><span class="prks-research-row__title">${opts.title}</span></a>`
}

const rows = [
  {
    id: 'A1',
    name: 'Alienation',
    kind: 'stance',
    main_text: 'Labor becomes a commodity',
    response_count: 1,
    targets: [],
    sources: [],
  },
  {
    id: 'A2',
    name: 'Unrelated Argument',
    kind: 'argument',
    main_text: 'A separate claim',
    response_count: 0,
    targets: [],
    sources: [],
  },
]

describe('Arguments route bridge', () => {
  it('renders independent Main and Secondary index owners', () => {
    paintHelpers()
    const mainHost = host()
    const secondaryHost = host()
    presentArgumentsIndex({
      owner: owner('main'),
      host: mainHost,
      items: [rows[0]],
      generation: 4,
      shell: true,
    })
    presentArgumentsIndex({
      owner: owner('side'),
      host: secondaryHost,
      kind: 'argument',
      items: [rows[1]],
      generation: 1,
      shell: false,
    })
    expect(mainHost.textContent).toContain('Alienation')
    expect(mainHost.textContent).not.toContain('Unrelated Argument')
    expect(secondaryHost.textContent).toContain('Unrelated Argument')
    const main = { tabId: 'main', isCurrent: () => true }
    const side = { tabId: 'side', isCurrent: () => true }
    presentArgumentsIndex({ owner: main, host: mainHost, items: [rows[0]], generation: 5, shell: true })
    presentArgumentsIndex({
      owner: side,
      host: secondaryHost,
      kind: 'argument',
      items: [rows[1]],
      generation: 2,
      shell: false,
    })
    expect(readRouteSurface(main)?.canonicalHash).toBe('#/arguments')
    expect(readRouteSurface(main)?.ownsMainShell).toBe(true)
    expect(readRouteSurface(side)?.ownsMainShell).toBe(false)
    expect(readRouteSurface(side)?.canonicalHash).toBe('#/arguments?kind=argument')
  })

  it('filters kind in the projection and searches name and main text locally', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    paintHelpers()
    const pane = owner()
    const el = host()
    presentArgumentsIndex({ owner: pane, host: el, kind: 'stance', items: rows, generation: 1 })
    expect(el.querySelector('[data-arg-filter="stance"]')?.classList.contains('is-active')).toBe(true)
    expect(el.textContent).toContain('Alienation')
    expect(el.textContent).toContain('Unrelated Argument')
    const search = el.querySelector<HTMLInputElement>('#prks-argument-search')
    expect(search).not.toBeNull()
    search!.value = 'commodity'
    search!.dispatchEvent(new Event('input'))
    await nextTick()
    expect(el.textContent).toContain('Alienation')
    expect(el.textContent).not.toContain('Unrelated Argument')
    search!.value = 'separate claim'
    search!.dispatchEvent(new Event('input'))
    await nextTick()
    expect(el.textContent).toContain('Unrelated Argument')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('paints cached empty and unavailable index states without fetch', () => {
    paintHelpers()
    const pane = owner()
    const el = host()
    presentArgumentsIndex({ owner: pane, host: el, kind: 'argument', items: [], generation: 1 })
    expect(el.textContent).toContain('No Arguments yet.')
    expect(el.querySelector('#prks-argument-new-empty')).not.toBeNull()
    expect(el.querySelector('#prks-stance-new-empty')).toBeNull()
    presentArgumentsIndex({
      owner: pane,
      host: el,
      availability: 'unavailable',
      items: rows,
      generation: 2,
    })
    expect(el.querySelector('[data-prks-role="offline-unavailable"]')).not.toBeNull()
    expect(el.textContent).toContain('This list has not been cached on this device.')
    expect(el.textContent).not.toContain('Alienation')
    expect(el.querySelector('#prks-argument-new')).toBeNull()
  })

  it('paints detail overlays, unavailable, and not-found without fetch', () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    paintHelpers()
    const pane = owner()
    const el = host()
    presentArgumentDetail({
      owner: pane,
      host: el,
      argument: {
        id: 'A1',
        name: 'Pending rename',
        kind: 'argument',
        main_text: 'after the edit',
        targets: [
          { type: 'position', id: 'P1', name: 'Pending position', verdict_id: 'supports', verdict_label: 'Supports' },
          { type: 'argument', id: 'A2', name: 'Pending argument', kind: 'argument', verdict_id: 'opposes' },
        ],
        sources: [{ work_id: 'W1', work_title: 'Renamed work', pages: '4', authors: [] }],
        responses: [{ id: 'A3', name: 'Pending response', kind: 'argument', verdict_label: 'Opposes' }],
        mentions: [{ work_id: 'W2', title: 'Mention title' }],
      },
      argumentId: 'A1',
      generation: 1,
    })
    expect(el.querySelector('#prks-arg-view-graph')).not.toBeNull()
    expect(el.textContent).toContain('Pending position')
    expect(el.textContent).toContain('Pending argument')
    expect(el.textContent).toContain('Pending response')
    expect(el.textContent).toContain('Renamed work')
    expect(el.textContent).toContain('Mention title')
    expect(el.querySelector('.research-entity__section-count')?.textContent).toBe('2')
    expect(readRouteSurface(pane)?.canonicalHash).toBe('#/arguments/A1')
    expect(fetchMock).not.toHaveBeenCalled()

    presentArgumentDetail({
      owner: pane,
      host: el,
      availability: 'not-found',
      argumentId: 'missing',
      generation: 2,
    })
    expect(el.textContent).toContain('Argument not found')

    presentArgumentDetail({
      owner: pane,
      host: el,
      availability: 'unavailable',
      argument: { id: 'A1', name: 'Pending rename' },
      argumentId: 'A1',
      generation: 3,
    })
    expect(el.textContent).toContain('Argument or Stance not available offline')
    expect(el.textContent).toContain('This item is not available offline.')
    expect(el.textContent).not.toContain('Pending rename')
  })

  it('enters edit, cancels without saving, and leaves edit after a retained generation change', async () => {
    paintHelpers()
    window.prksPrepareArgumentEdit = vi.fn(async () => {})
    window.prksCommitArgumentEditorDraft = vi.fn(async () => ({ id: 'A1' }))
    window.prksTabContextOwnsEntityRoute = () => true
    const pane = owner()
    const el = host()
    presentArgumentDetail({
      owner: pane,
      host: el,
      argument: {
        id: 'A1',
        name: 'Base',
        kind: 'argument',
        main_text: 'Body',
        targets: [],
        sources: [],
        responses: [],
        mentions: [],
        verdicts: [{ id: 'supports', label: 'Supports' }],
      },
      argumentId: 'A1',
      generation: 1,
    })
    el.querySelector<HTMLButtonElement>('#prks-arg-edit')?.click()
    await nextTick()
    await Promise.resolve()
    await nextTick()
    expect(el.querySelector('#prks-arg-form')).not.toBeNull()
    expect(pane.ui.argumentEditing).toBe(true)
    el.querySelector<HTMLButtonElement>('#prks-arg-cancel')?.click()
    await nextTick()
    expect(el.querySelector('#prks-arg-form')).toBeNull()
    expect(pane.ui.argumentEditing).toBe(false)
    expect(window.prksCommitArgumentEditorDraft).not.toHaveBeenCalled()

    el.querySelector<HTMLButtonElement>('#prks-arg-edit')?.click()
    await nextTick()
    await Promise.resolve()
    await nextTick()
    expect(el.querySelector('#prks-arg-form')).not.toBeNull()
    presentArgumentDetail({
      owner: pane,
      host: el,
      argument: {
        id: 'A1',
        name: 'Base',
        kind: 'argument',
        main_text: 'Body',
        targets: [],
        sources: [],
        responses: [],
        mentions: [],
      },
      argumentId: 'A1',
      generation: 2,
    })
    await nextTick()
    expect(el.querySelector('#prks-arg-form')).toBeNull()
    expect(el.querySelector('#prks-arg-edit')).not.toBeNull()
  })

  it('keeps index search across a retained same-route refresh and clears on leave', async () => {
    paintHelpers()
    const cleanups = new Set<() => void>()
    const pane: {
      tabId: string
      isCurrent: () => boolean
      registerCleanup: (fn: () => void) => () => void
      [ARGUMENTS_RETAIN_SURFACE_KEY]?: boolean
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
    const routeHost = host()
    presentArgumentsIndex({ owner: pane, host: routeHost, items: rows, generation: 1 })
    const search = routeHost.querySelector<HTMLInputElement>('#prks-argument-search')
    search!.value = 'alien'
    search!.dispatchEvent(new Event('input'))
    await nextTick()
    expect(routeHost.textContent).toContain('Alienation')
    expect(routeHost.textContent).not.toContain('Unrelated Argument')

    pane[ARGUMENTS_RETAIN_SURFACE_KEY] = true
    const drained = Array.from(cleanups)
    cleanups.clear()
    drained.forEach((fn) => fn())
    pane[ARGUMENTS_RETAIN_SURFACE_KEY] = false
    expect(routeHost.querySelector('[data-prks-arguments-index-view]')).not.toBeNull()

    presentArgumentsIndex({ owner: pane, host: routeHost, items: rows, generation: 2 })
    await nextTick()
    expect(routeHost.querySelector<HTMLInputElement>('#prks-argument-search')?.value).toBe('alien')
    expect(routeHost.textContent).not.toContain('Unrelated Argument')

    const leave = Array.from(cleanups)
    cleanups.clear()
    leave.forEach((fn) => fn())
    expect(routeHost.querySelector('[data-prks-arguments-index-view]')).toBeNull()
  })

  it('unmounts a failed retained refresh before the retry view and on later leave', async () => {
    paintHelpers()
    const cleanups = new Set<() => void>()
    const pane: {
      tabId: string
      isCurrent: () => boolean
      registerCleanup: (fn: () => void) => () => void
      [ARGUMENTS_RETAIN_SURFACE_KEY]?: boolean
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
    presentArgumentsIndex({ owner: pane, host: routeHost, items: rows, generation: 1 })
    pane[ARGUMENTS_RETAIN_SURFACE_KEY] = true
    const drained = Array.from(cleanups)
    cleanups.clear()
    drained.forEach((fn) => fn())
    pane[ARGUMENTS_RETAIN_SURFACE_KEY] = false
    expect(cleanups.size).toBe(1)

    dismissArguments(pane)
    expect(routeHost.querySelector('[data-prks-arguments-index-view]')).toBeNull()
    contentDiv.innerHTML = '<p><button type="button" id="prks-route-retry">Retry</button></p>'
    expect(contentDiv.querySelector('[data-prks-arguments-index-view]')).toBeNull()
    const leave = Array.from(cleanups)
    cleanups.clear()
    leave.forEach((fn) => fn())
    expect(document.body.querySelector('[data-prks-arguments-index-view]')).toBeNull()
  })

  it('registers bridges and unmounts one owner without affecting the other', () => {
    paintHelpers()
    const el = host()
    const decoy = host()
    el.setAttribute('data-prks-vue-route-host', 'true')
    decoy.setAttribute('data-prks-vue-route-host', 'true')
    const pane = owner('early')
    ;(el as HTMLElement & { __prksVueRouteRequest?: object }).__prksVueRouteRequest = {
      feature: 'arguments',
      owner: pane,
      host: decoy,
      items: rows,
      kind: 'all',
      generation: 1,
      shell: true,
    }
    registerArgumentsBridge(window)
    expect(el.textContent).toContain('Alienation')
    expect(decoy.textContent).not.toContain('Alienation')

    const other = owner('other')
    const otherHost = host()
    presentArgumentsIndex({ owner: other, host: otherHost, items: [rows[1]], generation: 1 })
    dismissArguments(pane)
    expect(el.querySelector('[data-prks-arguments-index-view]')).toBeNull()
    expect(otherHost.textContent).toContain('Unrelated Argument')
  })
})
