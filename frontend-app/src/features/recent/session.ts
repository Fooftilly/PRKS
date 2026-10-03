import { createVNode } from 'vue'
import {
  dismissRouteSurface,
  presentRouteSurface,
  registerEarlyRoutePresenter,
  registerRouteWindowBridge,
  resetRouteSurfaceForTests,
  type RouteSurfaceOwner,
} from '../../route-surface/lifecycle'
import RecentRoute from './RecentRoute.vue'
import { buildRecentProjection, type RecentProjection } from './projection'
import type { RecentRouteInstance } from './route'

export interface RecentPresentInput {
  owner: RouteSurfaceOwner
  host: HTMLElement
  rows: unknown
  offlineCached?: boolean
  generation?: number
  /**
   * True when this owner is the Main shell. Recorded on the route instance.
   * Sidebar publication stays in the legacy router.
   */
  shell?: boolean
}

const RECENT_FEATURE = 'recent'

function isRecentEarlyRequest(
  value: unknown,
): value is Omit<RecentPresentInput, 'host'> & { feature: typeof RECENT_FEATURE } {
  if (!value || typeof value !== 'object') return false
  const record = value as Partial<RecentPresentInput> & { feature?: unknown }
  return record.feature === RECENT_FEATURE && !!record.owner && 'rows' in record
}

/**
 * Paint one owner's Recent surface from an already-effective browse snapshot.
 * The coordinator owns `prksEffectiveRecent`. Vue does not read the durable queue.
 */
export function presentRecent(input: RecentPresentInput): void {
  if (!input || !input.owner || typeof input.owner !== 'object') return
  const offlineCached = input.offlineCached === true
  const rows = input.rows
  const route: Omit<RecentRouteInstance, 'generation'> & { generation?: number } = {
    name: 'recent',
    canonicalHash: '#/recent',
    params: {},
    ownsMainShell: input.shell !== false,
    generation: input.generation,
  }
  presentRouteSurface({
    owner: input.owner,
    host: input.host,
    route,
    render: (generation) => {
      const projection: RecentProjection = buildRecentProjection({
        rows,
        offlineCached,
        generation,
      })
      return createVNode(RecentRoute, { projection })
    },
  })
}

export function dismissRecent(owner: object | null | undefined): void {
  dismissRouteSurface(owner)
}

export function resetRecentSessionForTests(): void {
  resetRouteSurfaceForTests()
}

export function registerRecentBridge(target: Window = window): void {
  registerRouteWindowBridge(target)
  target.prksVueDismissRecent = dismissRecent
  registerEarlyRoutePresenter(
    RECENT_FEATURE,
    (request, host) => {
      if (!isRecentEarlyRequest(request)) return false
      const { owner, rows, offlineCached, generation, shell } = request
      presentRecent({ owner, host, rows, offlineCached, generation, shell })
      return true
    },
    target,
  )
}
