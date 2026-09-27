import { createVNode } from 'vue'
import {
  dismissRouteSurface,
  presentRouteSurface,
  registerEarlyRoutePresenter,
  resetRouteSurfaceForTests,
  type RouteSurfaceOwner,
} from '../../route-surface/lifecycle'
import type { PositionIntentOwner } from './intents'
import PositionDetailRoute from './PositionDetailRoute.vue'
import PositionIntentsProvider from './PositionIntentsProvider.vue'
import PositionsIndexRoute from './PositionsIndexRoute.vue'
import {
  buildPositionDetailProjection,
  buildPositionIndexProjection,
  type PositionDetailProjection,
  type PositionIndexProjection,
} from './projection'
import type { PositionDetailRouteInstance, PositionsIndexRouteInstance } from './route'
import type { PositionDetailAvailability, PositionIndexAvailability } from './types'

const POSITIONS_FEATURE = 'positions'
const POSITION_DETAIL_FEATURE = 'position-detail'

/** Set by the route coordinator before beginRoute when staying on Positions. */
export const POSITIONS_RETAIN_SURFACE_KEY = '__prksRetainPositionsSurface'
const POSITIONS_CLEANUP_ARMED_KEY = '__prksPositionsCleanupArmed'

type PositionsOwner = RouteSurfaceOwner &
  PositionIntentOwner & {
    [POSITIONS_RETAIN_SURFACE_KEY]?: boolean
    [POSITIONS_CLEANUP_ARMED_KEY]?: boolean
  }

/**
 * Positions dismisses on leave/destroy, not on every beginRoute.
 * A retained refresh sets POSITIONS_RETAIN_SURFACE_KEY so this cleanup does
 * not unmount, then re-arms immediately: beginRoute already drained the set,
 * and a failed refresh never reaches present to register another callback.
 */
function armPositionsOwnerCleanup(owner: PositionsOwner): void {
  if (owner[POSITIONS_CLEANUP_ARMED_KEY] || typeof owner.registerCleanup !== 'function') return
  owner[POSITIONS_CLEANUP_ARMED_KEY] = true
  owner.registerCleanup(() => {
    owner[POSITIONS_CLEANUP_ARMED_KEY] = false
    if (owner[POSITIONS_RETAIN_SURFACE_KEY]) {
      armPositionsOwnerCleanup(owner)
      return
    }
    dismissPositions(owner)
  })
}

export interface PositionsIndexPresentInput {
  owner: PositionsOwner
  host: HTMLElement
  availability?: PositionIndexAvailability
  items?: unknown
  generation?: number
  shell?: boolean
}

export interface PositionDetailPresentInput {
  owner: PositionsOwner
  host: HTMLElement
  availability?: PositionDetailAvailability
  position?: unknown
  positionId?: string
  generation?: number
  shell?: boolean
}

/**
 * Paint one owner's Positions index from an already-effective projection.
 * Owner session bookkeeping lives in the shared route-surface lifecycle.
 */
export function presentPositionsIndex(input: PositionsIndexPresentInput): void {
  if (!input || !input.owner || typeof input.owner !== 'object') return
  const availability: PositionIndexAvailability =
    input.availability === 'unavailable' ? 'unavailable' : 'ready'
  const items = input.items
  const route: Omit<PositionsIndexRouteInstance, 'generation'> & { generation?: number } = {
    name: 'positions',
    canonicalHash: '#/positions',
    params: {},
    ownsMainShell: input.shell !== false,
    generation: input.generation,
  }
  presentRouteSurface({
    owner: input.owner,
    host: input.host,
    route,
    armBeginRouteCleanup: false,
    render: (generation) => {
      const projection: PositionIndexProjection = buildPositionIndexProjection({
        availability,
        items,
        generation,
      })
      return createVNode(
        PositionIntentsProvider,
        { owner: input.owner, generation },
        { default: () => createVNode(PositionsIndexRoute, { projection }) },
      )
    },
  })
  armPositionsOwnerCleanup(input.owner)
}

/** Paint one owner's Position detail from an already-effective projection. */
export function presentPositionDetail(input: PositionDetailPresentInput): void {
  if (!input || !input.owner || typeof input.owner !== 'object') return
  const availability: PositionDetailAvailability =
    input.availability === 'unavailable' || input.availability === 'not-found'
      ? input.availability
      : 'ready'
  const position = input.position
  const positionId =
    typeof input.positionId === 'string' && input.positionId
      ? input.positionId
      : typeof position === 'object' && position && 'id' in position
        ? String((position as { id?: unknown }).id || '')
        : ''
  const route: Omit<PositionDetailRouteInstance, 'generation'> & { generation?: number } = {
    name: 'position-detail',
    canonicalHash: positionId ? `#/positions/${encodeURIComponent(positionId)}` : '#/positions',
    params: { positionId },
    ownsMainShell: input.shell !== false,
    generation: input.generation,
  }
  presentRouteSurface({
    owner: input.owner,
    host: input.host,
    route,
    armBeginRouteCleanup: false,
    render: (generation) => {
      const projection: PositionDetailProjection = buildPositionDetailProjection({
        availability,
        position,
        generation,
      })
      return createVNode(
        PositionIntentsProvider,
        { owner: input.owner, generation },
        { default: () => createVNode(PositionDetailRoute, { projection }) },
      )
    },
  })
  armPositionsOwnerCleanup(input.owner)
}

/** Drop the Vue Positions tree owned by this pane. Other owners stay mounted. */
export function dismissPositions(owner: object | null | undefined): void {
  dismissRouteSurface(owner)
}

export function resetPositionsSessionForTests(): void {
  resetRouteSurfaceForTests()
}

function isPositionsIndexEarlyRequest(
  value: unknown,
): value is Omit<PositionsIndexPresentInput, 'host'> & { feature: typeof POSITIONS_FEATURE } {
  if (!value || typeof value !== 'object') return false
  const record = value as Partial<PositionsIndexPresentInput> & { feature?: unknown }
  return record.feature === POSITIONS_FEATURE && !!record.owner
}

function isPositionDetailEarlyRequest(
  value: unknown,
): value is Omit<PositionDetailPresentInput, 'host'> & { feature: typeof POSITION_DETAIL_FEATURE } {
  if (!value || typeof value !== 'object') return false
  const record = value as Partial<PositionDetailPresentInput> & { feature?: unknown }
  return record.feature === POSITION_DETAIL_FEATURE && !!record.owner
}

export function registerPositionsBridge(target: Window = window): void {
  target.prksVuePresentPositionsIndex = presentPositionsIndex
  target.prksVuePresentPositionDetail = presentPositionDetail
  target.prksVueDismissPositions = dismissPositions
  registerEarlyRoutePresenter(
    POSITIONS_FEATURE,
    (request, host) => {
      if (!isPositionsIndexEarlyRequest(request)) return false
      const { owner, availability, items, generation, shell } = request
      presentPositionsIndex({ owner, host, availability, items, generation, shell })
      return true
    },
    target,
  )
  registerEarlyRoutePresenter(
    POSITION_DETAIL_FEATURE,
    (request, host) => {
      if (!isPositionDetailEarlyRequest(request)) return false
      const { owner, availability, position, positionId, generation, shell } = request
      presentPositionDetail({
        owner,
        host,
        availability,
        position,
        positionId,
        generation,
        shell,
      })
      return true
    },
    target,
  )
}
