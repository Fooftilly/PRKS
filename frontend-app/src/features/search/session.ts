import { createVNode } from 'vue'
import {
  dismissRouteSurface,
  presentRouteSurface,
  registerEarlyRoutePresenter,
  resetRouteSurfaceForTests,
  type RouteSurfaceOwner,
} from '../../route-surface/lifecycle'
import SearchRoute from './SearchRoute.vue'
import { browserSearchIntents, type SearchIntentOwner } from './intents'
import { acceptSearchRequest, buildSearchRouteProjection } from './projection'
import type { SearchRouteInstance } from './route'

export interface SearchPresentInput {
  owner: RouteSurfaceOwner & SearchIntentOwner
  host: HTMLElement
  /** Route params as parsed by `prksParseRoute`. */
  request: unknown
  canonicalHash?: string
  /** Already-effective rows from `prksEffectiveSearchResults`. */
  rows: unknown
  generation?: number
  shell?: boolean
}

const SEARCH_FEATURE = 'search'

function isSearchEarlyRequest(
  value: unknown,
): value is Omit<SearchPresentInput, 'host'> & { feature: typeof SEARCH_FEATURE } {
  if (!value || typeof value !== 'object') return false
  const record = value as Partial<SearchPresentInput> & { feature?: unknown }
  return record.feature === SEARCH_FEATURE && !!record.owner && 'rows' in record
}

/**
 * Paint one owner's Search surface. The coordinator runs `fetchSearch`,
 * hydrates pending Work state, and applies `prksEffectiveWorksSync` before
 * this is called. Vue does not fetch, read the durable queue, or cache results.
 */
export function presentSearch(input: SearchPresentInput): void {
  if (!input || !input.owner || typeof input.owner !== 'object') return
  const request = acceptSearchRequest(input.request)
  const canonicalHash = input.canonicalHash || '#/search'
  const rows = input.rows
  const owner = input.owner
  const route: Omit<SearchRouteInstance, 'generation'> & { generation?: number } = {
    name: 'search',
    canonicalHash,
    params: request,
    ownsMainShell: input.shell !== false,
    generation: input.generation,
  }
  presentRouteSurface({
    owner,
    host: input.host,
    route,
    render: (generation) =>
      createVNode(SearchRoute, {
        projection: buildSearchRouteProjection({ request, canonicalHash, rows, generation }),
        intents: browserSearchIntents(owner, generation, canonicalHash),
      }),
  })
}

export function dismissSearch(owner: object | null | undefined): void {
  dismissRouteSurface(owner)
}

export function resetSearchSessionForTests(): void {
  resetRouteSurfaceForTests()
}

export function registerSearchBridge(target: Window = window): void {
  target.prksVuePresentSearch = presentSearch
  target.prksVueDismissSearch = dismissSearch
  registerEarlyRoutePresenter(
    SEARCH_FEATURE,
    (request, host) => {
      if (!isSearchEarlyRequest(request)) return false
      const { owner, request: params, canonicalHash, rows, generation, shell } = request
      presentSearch({ owner, host, request: params, canonicalHash, rows, generation, shell })
      return true
    },
    target,
  )
}
