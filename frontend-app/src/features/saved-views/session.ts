import { createVNode } from 'vue'
import {
  presentRouteSurface,
  registerEarlyRoutePresenter,
  registerRouteWindowBridge,
  resetRouteSurfaceForTests,
  type RouteSurfaceOwner,
} from '../../route-surface/lifecycle'
import SavedViewDetailRoute from './SavedViewDetailRoute.vue'
import SavedViewsIndexRoute from './SavedViewsIndexRoute.vue'
import { browserSavedViewIntents, type SavedViewIntentOwner } from './intents'
import { buildSavedViewDetailProjection, buildSavedViewIndexProjection } from './projection'
import type { SavedViewDetailRouteInstance, SavedViewsIndexRouteInstance } from './route'
import type { SavedViewDetailAvailability } from './types'

export interface SavedViewDetailPresentInput {
  owner: RouteSurfaceOwner & SavedViewIntentOwner
  host: HTMLElement
  availability?: SavedViewDetailAvailability
  view?: unknown
  viewId?: string
  searchHash?: string
  /** Already-effective rows from `prksEffectiveSearchResults`. */
  rows?: unknown
  generation?: number
  shell?: boolean
}

export interface SavedViewsIndexPresentInput {
  owner: RouteSurfaceOwner & SavedViewIntentOwner
  host: HTMLElement
  views?: unknown
  generation?: number
  shell?: boolean
}

const SAVED_VIEWS_INDEX_FEATURE = 'saved-views'
const SAVED_VIEW_DETAIL_FEATURE = 'saved-view-detail'

function isSavedViewsIndexEarlyRequest(
  value: unknown,
): value is Omit<SavedViewsIndexPresentInput, 'host'> & { feature: typeof SAVED_VIEWS_INDEX_FEATURE } {
  if (!value || typeof value !== 'object') return false
  const record = value as Partial<SavedViewsIndexPresentInput> & { feature?: unknown }
  return record.feature === SAVED_VIEWS_INDEX_FEATURE && !!record.owner && 'views' in record
}

/**
 * Paint one owner's Saved Views index. The coordinator has already loaded the
 * list. Vue does not fetch it. Summaries come from the search query codec.
 */
export function presentSavedViewsIndex(input: SavedViewsIndexPresentInput): void {
  if (!input || !input.owner || typeof input.owner !== 'object') return
  const owner = input.owner
  const views = input.views
  const route: Omit<SavedViewsIndexRouteInstance, 'generation'> & { generation?: number } = {
    name: 'saved-views',
    canonicalHash: '#/views',
    params: {},
    ownsMainShell: input.shell !== false,
    generation: input.generation,
  }
  presentRouteSurface({
    owner,
    host: input.host,
    route,
    render: (generation) =>
      createVNode(SavedViewsIndexRoute, {
        projection: buildSavedViewIndexProjection({ views, generation }),
        intents: browserSavedViewIntents(owner, generation),
      }),
  })
}

function isSavedViewDetailEarlyRequest(
  value: unknown,
): value is Omit<SavedViewDetailPresentInput, 'host'> & { feature: typeof SAVED_VIEW_DETAIL_FEATURE } {
  if (!value || typeof value !== 'object') return false
  const record = value as Partial<SavedViewDetailPresentInput> & { feature?: unknown }
  return record.feature === SAVED_VIEW_DETAIL_FEATURE && !!record.owner
}

/**
 * Paint one owner's Saved View detail. The coordinator loads the record,
 * maps its definition through the query codec, and runs the same
 * `prksEffectiveSearchResults` read as Search. Results are never stored.
 */
export function presentSavedViewDetail(input: SavedViewDetailPresentInput): void {
  if (!input || !input.owner || typeof input.owner !== 'object') return
  const owner = input.owner
  const { availability, view, searchHash, rows } = input
  const viewId = String(input.viewId || (view as { id?: unknown } | null)?.id || '')
  const route: Omit<SavedViewDetailRouteInstance, 'generation'> & { generation?: number } = {
    name: 'saved-view-detail',
    canonicalHash: viewId ? `#/views/${encodeURIComponent(viewId)}` : '#/views',
    params: { viewId },
    ownsMainShell: input.shell !== false,
    generation: input.generation,
  }
  presentRouteSurface({
    owner,
    host: input.host,
    route,
    render: (generation) =>
      createVNode(SavedViewDetailRoute, {
        projection: buildSavedViewDetailProjection({ availability, view, viewId, searchHash, rows, generation }),
        intents: browserSavedViewIntents(owner, generation),
      }),
  })
}

export function resetSavedViewsSessionForTests(): void {
  resetRouteSurfaceForTests()
}

export function registerSavedViewsBridge(target: Window = window): void {
  registerRouteWindowBridge(target)
  registerEarlyRoutePresenter(
    SAVED_VIEWS_INDEX_FEATURE,
    (request, host) => {
      if (!isSavedViewsIndexEarlyRequest(request)) return false
      const { owner, views, generation, shell } = request
      presentSavedViewsIndex({ owner, host, views, generation, shell })
      return true
    },
    target,
  )
  registerEarlyRoutePresenter(
    SAVED_VIEW_DETAIL_FEATURE,
    (request, host) => {
      if (!isSavedViewDetailEarlyRequest(request)) return false
      const { owner, availability, view, viewId, searchHash, rows, generation, shell } = request
      presentSavedViewDetail({ owner, host, availability, view, viewId, searchHash, rows, generation, shell })
      return true
    },
    target,
  )
}
