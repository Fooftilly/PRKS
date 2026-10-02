import { nextTick } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { readRouteSurface, type RouteSurfaceOwner } from '../../route-surface/lifecycle'
import {
  dismissResearchGraph,
  presentResearchGraph,
  registerResearchGraphBridge,
  resetResearchGraphSessionForTests,
} from './session'

afterEach(() => {
  resetResearchGraphSessionForTests()
  document.body.innerHTML = ''
  delete window.prksVuePresentResearchGraph
  delete window.prksVueDismissResearchGraph
  delete window.renderResearchGraph
  delete window.prksReleaseResearchGraph
  delete window.prksIcon
  delete window.prksPageHeaderIconHtml
  delete window.prksRefreshIcons
})

function host(): HTMLElement {
  const el = document.createElement('div')
  document.body.appendChild(el)
  return el
}

function owner(tabId: string): RouteSurfaceOwner & {
  tabId: string
  isCurrent: () => boolean
  lastResolvedRoute: { name: string }
} {
  return {
    tabId,
    isCurrent: () => true,
    lastResolvedRoute: { name: 'research-graph' },
  }
}

describe('Research Graph route bridge', () => {
  it('paints each owner and asks the coordinator to mount that owner only', async () => {
    const mounts: object[] = []
    window.renderResearchGraph = vi.fn(async (_container, options) => {
      mounts.push(options?.ctx as object)
    })
    window.prksIcon = () => '<i></i>'
    window.prksPageHeaderIconHtml = () => '<i data-lucide="share-2"></i>'
    const main = owner('main')
    const secondary = owner('side')
    const mainHost = host()
    const secondaryHost = host()
    presentResearchGraph({
      owner: main,
      host: mainHost,
      generation: 4,
      shell: true,
      focus: 'concept:C-1',
      includePeople: false,
      chrome: { findId: 'find-main' },
      attach: { focus: 'concept:C-1' },
    })
    presentResearchGraph({
      owner: secondary,
      host: secondaryHost,
      generation: 2,
      shell: false,
      focus: 'person:P-1',
      includePeople: true,
      attach: { focus: 'person:P-1' },
    })
    await nextTick()
    expect(mainHost.querySelector('.prks-page-title')?.textContent).toContain('Research Graph')
    expect(mainHost.querySelector('[data-prks-role="graph-canvas"]')).not.toBeNull()
    expect(mainHost.querySelector('[data-prks-role="graph-fit"]')?.textContent).toBe('Fit')
    expect(mainHost.querySelector<HTMLInputElement>('#find-main')).not.toBeNull()
    expect(mainHost.querySelector<HTMLInputElement>('[data-graph-filter="people"]')?.checked).toBe(false)
    expect(secondaryHost.querySelector<HTMLInputElement>('[data-graph-filter="people"]')?.checked).toBe(true)
    expect(secondaryHost.querySelector('#find-main')).toBeNull()
    expect(mounts).toEqual([main, secondary])
    expect(readRouteSurface(main)).toMatchObject({
      name: 'research-graph',
      ownsMainShell: true,
      generation: 4,
    })
    expect(readRouteSurface(secondary)?.ownsMainShell).toBe(false)
    expect(window.renderResearchGraph).toHaveBeenCalledTimes(2)
    const first = vi.mocked(window.renderResearchGraph).mock.calls[0]?.[1]
    expect(first?.adoptShell).toBe(true)
    expect(first?.ctx).toBe(main)
  })

  it('does not mount a stale generation over the current owner', async () => {
    window.renderResearchGraph = vi.fn(async () => undefined)
    const pane = owner('main')
    const el = host()
    presentResearchGraph({ owner: pane, host: el, generation: 3, focus: '', attach: {} })
    presentResearchGraph({ owner: pane, host: el, generation: 2, focus: 'concept:C-1', attach: {} })
    await nextTick()
    expect(window.renderResearchGraph).toHaveBeenCalledTimes(1)
    expect(readRouteSurface(pane)?.generation).toBe(3)
  })

  it('releases only the unmounted owner', async () => {
    const released: object[] = []
    window.renderResearchGraph = vi.fn(async () => undefined)
    window.prksReleaseResearchGraph = (ctx) => {
      released.push(ctx)
    }
    const main = owner('main')
    const secondary = owner('side')
    const mainHost = host()
    const secondaryHost = host()
    presentResearchGraph({ owner: main, host: mainHost, generation: 1, attach: {} })
    presentResearchGraph({ owner: secondary, host: secondaryHost, generation: 1, shell: false, attach: {} })
    dismissResearchGraph(main)
    await nextTick()
    expect(released).toEqual([main])
    expect(mainHost.querySelector('[data-prks-research-graph]')).toBeNull()
    expect(secondaryHost.querySelector('[data-prks-role="graph-canvas"]')).not.toBeNull()
  })

  it('registers the bridge and paints the host that stored the request', () => {
    window.renderResearchGraph = vi.fn(async () => undefined)
    const el = host()
    const decoy = host()
    el.setAttribute('data-prks-vue-route-host', 'true')
    decoy.setAttribute('data-prks-vue-route-host', 'true')
    const pane = owner('main')
    ;(el as HTMLElement & { __prksVueRouteRequest?: object }).__prksVueRouteRequest = {
      feature: 'research-graph',
      owner: pane,
      host: decoy,
      generation: 1,
      shell: true,
      focus: '',
      includePeople: false,
      attach: {},
    }
    registerResearchGraphBridge(window)
    expect(window.prksVuePresentResearchGraph).toBeTypeOf('function')
    expect((el as HTMLElement & { __prksVueRouteRequest?: unknown }).__prksVueRouteRequest).toBeUndefined()
    expect(el.querySelector('[data-prks-research-graph]')).not.toBeNull()
    expect(decoy.querySelector('[data-prks-research-graph]')).toBeNull()
    expect(window.renderResearchGraph).toHaveBeenCalledTimes(1)
  })

  it('refreshes header and legend icons on the painted graph after the markup is written', async () => {
    const refreshed: ParentNode[] = []
    window.prksRefreshIcons = (root) => {
      if (root) refreshed.push(root)
    }
    window.prksIcon = (name) => `<i data-lucide="${name}"></i>`
    window.prksPageHeaderIconHtml = () => '<i data-lucide="share-2"></i>'
    window.renderResearchGraph = vi.fn(async () => undefined)
    const tab = document.createElement('div')
    tab.className = 'prks-tab-root'
    document.body.appendChild(tab)
    const el = document.createElement('div')
    tab.appendChild(el)
    const pane = owner('main')
    presentResearchGraph({ owner: pane, host: el, generation: 1, attach: {} })
    await nextTick()
    const graph = el.querySelector<HTMLElement>('[data-prks-research-graph]')
    const body = graph?.querySelector<HTMLElement>('[data-prks-role="graph-body"]')
    const stage = body?.querySelector('.research-graph__stage')
    expect(el.getAttribute('data-prks-vue-route-host')).toBe('true')
    expect(el.parentElement).toBe(tab)
    expect(graph).not.toBeNull()
    expect(body).not.toBeNull()
    expect(stage).not.toBeNull()
    expect(graph?.parentElement).toBe(el)
    expect(refreshed).toContain(graph)
    const painted = refreshed.find((node) => node === graph)
    expect(painted?.querySelector('.prks-page-title [data-lucide="share-2"]')).not.toBeNull()
    expect(painted?.querySelector('[data-prks-role="graph-legend-panel"] [data-lucide="network"]')).not.toBeNull()
    expect(painted?.querySelector('[data-prks-role="graph-legend-panel"] [data-lucide="user"]')).not.toBeNull()
  })
})
