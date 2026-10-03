import { createVNode } from 'vue'
import {
  dismissRouteSurface,
  presentRouteSurface,
  registerEarlyRoutePresenter,
  registerRouteWindowBridge,
  resetRouteSurfaceForTests,
  type RouteSurfaceOwner,
} from '../../route-surface/lifecycle'
import type { ResearchGraphRouteInstance } from './route'
import ResearchGraphRoute from './ResearchGraphRoute.vue'

export interface ResearchGraphChromeIds {
  findId?: string
  resultsId?: string
  filtersPanelId?: string
  legendPanelId?: string
}

/** Options the coordinator uses to mount the Cytoscape runtime after Vue paints. */
export interface ResearchGraphAttach {
  focus?: string
  signal?: AbortSignal
  routeGen?: number
  stale?: () => boolean
  loadSnapshot?: (people: boolean, signal?: AbortSignal) => Promise<unknown>
  onSnapshot?: (result: unknown) => void
}

export interface ResearchGraphPresentInput {
  owner: RouteSurfaceOwner
  host: HTMLElement
  generation?: number
  /** True when this owner is the Main shell. Recorded on the route instance. */
  shell?: boolean
  focus?: string
  includePeople?: boolean
  chrome?: ResearchGraphChromeIds
  /**
   * Present only. The coordinator mounts and destroys the graph instance.
   * Vue does not fetch the projection.
   */
  attach?: ResearchGraphAttach | null
}

const GRAPH_FEATURE = 'research-graph'

function isGraphEarlyRequest(
  value: unknown,
): value is Omit<ResearchGraphPresentInput, 'host'> & { feature: typeof GRAPH_FEATURE } {
  if (!value || typeof value !== 'object') return false
  const record = value as Partial<ResearchGraphPresentInput> & { feature?: unknown }
  return record.feature === GRAPH_FEATURE && !!record.owner
}

function attachGraph(input: ResearchGraphPresentInput): void {
  const attach = input.attach
  if (!attach || typeof window.renderResearchGraph !== 'function') return
  void window.renderResearchGraph(input.host, {
    adoptShell: true,
    ctx: input.owner,
    focus: attach.focus || input.focus || '',
    signal: attach.signal,
    routeGen: attach.routeGen,
    stale: attach.stale,
    loadSnapshot: attach.loadSnapshot,
    onSnapshot: attach.onSnapshot,
  })
}

/**
 * Paint one owner's Research Graph chrome. The coordinator registers the
 * Cytoscape runtime on that owner's resource registry. Vue does not fetch or create it.
 */
export function presentResearchGraph(input: ResearchGraphPresentInput): void {
  if (!input || !input.owner || typeof input.owner !== 'object') return
  const owner = input.owner
  const focus = input.focus || ''
  const includePeople = !!input.includePeople
  const chrome = input.chrome
  const route: Omit<ResearchGraphRouteInstance, 'generation'> & { generation?: number } = {
    name: 'research-graph',
    canonicalHash: focus ? `#/graph?focus=${encodeURIComponent(focus)}` : '#/graph',
    params: focus ? { focus } : {},
    ownsMainShell: input.shell !== false,
    generation: input.generation,
  }
  const painted = presentRouteSurface({
    owner,
    host: input.host,
    route,
    render: () =>
      createVNode(ResearchGraphRoute, {
        owner,
        includePeople,
        chrome,
      }),
  })
  if (!painted) return
  attachGraph(input)
}

export function dismissResearchGraph(owner: object | null | undefined): void {
  dismissRouteSurface(owner)
}

export function resetResearchGraphSessionForTests(): void {
  resetRouteSurfaceForTests()
}

export function registerResearchGraphBridge(target: Window = window): void {
  registerRouteWindowBridge(target)
  target.prksVueDismissResearchGraph = dismissResearchGraph
  registerEarlyRoutePresenter(
    GRAPH_FEATURE,
    (request, host) => {
      if (!isGraphEarlyRequest(request)) return false
      const { owner, generation, shell, focus, includePeople, chrome, attach } = request
      presentResearchGraph({ owner, host, generation, shell, focus, includePeople, chrome, attach })
      return true
    },
    target,
  )
}
