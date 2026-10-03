import { createVNode } from 'vue'
import {
  dismissRouteSurface,
  presentRouteSurface,
  registerEarlyRoutePresenter,
  registerRouteWindowBridge,
  resetRouteSurfaceForTests,
  type RouteSurfaceOwner,
} from '../../route-surface/lifecycle'
import ArgumentDetailRoute from './ArgumentDetailRoute.vue'
import ArgumentIntentsProvider from './ArgumentIntentsProvider.vue'
import ArgumentsIndexRoute from './ArgumentsIndexRoute.vue'
import type { ArgumentIntentOwner } from './intents'
import { argumentIndexHash, normalizeArgumentKindFilter } from './match'
import {
  buildArgumentDetailProjection,
  buildArgumentIndexProjection,
  type ArgumentDetailProjection,
  type ArgumentIndexProjection,
} from './projection'
import type { ArgumentDetailRouteInstance, ArgumentsIndexRouteInstance } from './route'
import type { ArgumentDetailAvailability, ArgumentIndexAvailability } from './types'

const ARGUMENTS_FEATURE = 'arguments'
const ARGUMENT_DETAIL_FEATURE = 'argument-detail'

/** Set by the route coordinator before beginRoute when staying on Arguments. */
export const ARGUMENTS_RETAIN_SURFACE_KEY = '__prksRetainArgumentsSurface'
const ARGUMENTS_CLEANUP_ARMED_KEY = '__prksArgumentsCleanupArmed'

type ArgumentsOwner = RouteSurfaceOwner &
  ArgumentIntentOwner & {
    [ARGUMENTS_RETAIN_SURFACE_KEY]?: boolean
    [ARGUMENTS_CLEANUP_ARMED_KEY]?: boolean
  }

/**
 * Arguments dismisses on leave/destroy, not on every beginRoute.
 * A retained refresh sets ARGUMENTS_RETAIN_SURFACE_KEY so this cleanup does
 * not unmount, then re-arms immediately: beginRoute already drained the set,
 * and a failed refresh never reaches present to register another callback.
 */
function armArgumentsOwnerCleanup(owner: ArgumentsOwner): void {
  if (owner[ARGUMENTS_CLEANUP_ARMED_KEY] || typeof owner.registerCleanup !== 'function') return
  owner[ARGUMENTS_CLEANUP_ARMED_KEY] = true
  owner.registerCleanup(() => {
    owner[ARGUMENTS_CLEANUP_ARMED_KEY] = false
    if (owner[ARGUMENTS_RETAIN_SURFACE_KEY]) {
      armArgumentsOwnerCleanup(owner)
      return
    }
    dismissArguments(owner)
  })
}

export interface ArgumentsIndexPresentInput {
  owner: ArgumentsOwner
  host: HTMLElement
  availability?: ArgumentIndexAvailability
  kind?: string
  items?: unknown
  generation?: number
  shell?: boolean
}

export interface ArgumentDetailPresentInput {
  owner: ArgumentsOwner
  host: HTMLElement
  availability?: ArgumentDetailAvailability
  argument?: unknown
  argumentId?: string
  generation?: number
  shell?: boolean
}

/**
 * Paint one owner's Arguments index from an already-effective projection.
 * Owner session bookkeeping lives in the shared route-surface lifecycle.
 */
export function presentArgumentsIndex(input: ArgumentsIndexPresentInput): void {
  if (!input || !input.owner || typeof input.owner !== 'object') return
  const availability: ArgumentIndexAvailability =
    input.availability === 'unavailable' ? 'unavailable' : 'ready'
  const items = input.items
  const kind = normalizeArgumentKindFilter(input.kind)
  const route: Omit<ArgumentsIndexRouteInstance, 'generation'> & { generation?: number } = {
    name: 'arguments',
    canonicalHash: argumentIndexHash(kind),
    params: { kind },
    ownsMainShell: input.shell !== false,
    generation: input.generation,
  }
  presentRouteSurface({
    owner: input.owner,
    host: input.host,
    route,
    armBeginRouteCleanup: false,
    render: (generation) => {
      const projection: ArgumentIndexProjection = buildArgumentIndexProjection({
        availability,
        kind,
        items,
        generation,
      })
      return createVNode(
        ArgumentIntentsProvider,
        { owner: input.owner, generation },
        { default: () => createVNode(ArgumentsIndexRoute, { projection }) },
      )
    },
  })
  armArgumentsOwnerCleanup(input.owner)
}

/** Paint one owner's Argument detail from an already-effective projection. */
export function presentArgumentDetail(input: ArgumentDetailPresentInput): void {
  if (!input || !input.owner || typeof input.owner !== 'object') return
  const availability: ArgumentDetailAvailability =
    input.availability === 'unavailable' || input.availability === 'not-found'
      ? input.availability
      : 'ready'
  const argument = input.argument
  const argumentId =
    typeof input.argumentId === 'string' && input.argumentId
      ? input.argumentId
      : typeof argument === 'object' && argument && 'id' in argument
        ? String((argument as { id?: unknown }).id || '')
        : ''
  const route: Omit<ArgumentDetailRouteInstance, 'generation'> & { generation?: number } = {
    name: 'argument-detail',
    canonicalHash: argumentId ? `#/arguments/${encodeURIComponent(argumentId)}` : '#/arguments',
    params: { argumentId },
    ownsMainShell: input.shell !== false,
    generation: input.generation,
  }
  presentRouteSurface({
    owner: input.owner,
    host: input.host,
    route,
    armBeginRouteCleanup: false,
    render: (generation) => {
      const projection: ArgumentDetailProjection = buildArgumentDetailProjection({
        availability,
        argument,
        generation,
      })
      return createVNode(
        ArgumentIntentsProvider,
        { owner: input.owner, generation },
        { default: () => createVNode(ArgumentDetailRoute, { projection }) },
      )
    },
  })
  armArgumentsOwnerCleanup(input.owner)
}

/** Drop the Vue Arguments tree owned by this pane. Other owners stay mounted. */
export function dismissArguments(owner: object | null | undefined): void {
  dismissRouteSurface(owner)
}

export function resetArgumentsSessionForTests(): void {
  resetRouteSurfaceForTests()
}

function isArgumentsIndexEarlyRequest(
  value: unknown,
): value is Omit<ArgumentsIndexPresentInput, 'host'> & { feature: typeof ARGUMENTS_FEATURE } {
  if (!value || typeof value !== 'object') return false
  const record = value as Partial<ArgumentsIndexPresentInput> & { feature?: unknown }
  return record.feature === ARGUMENTS_FEATURE && !!record.owner
}

function isArgumentDetailEarlyRequest(
  value: unknown,
): value is Omit<ArgumentDetailPresentInput, 'host'> & { feature: typeof ARGUMENT_DETAIL_FEATURE } {
  if (!value || typeof value !== 'object') return false
  const record = value as Partial<ArgumentDetailPresentInput> & { feature?: unknown }
  return record.feature === ARGUMENT_DETAIL_FEATURE && !!record.owner
}

export function registerArgumentsBridge(target: Window = window): void {
  registerRouteWindowBridge(target)
  target.prksVueDismissArguments = dismissArguments
  registerEarlyRoutePresenter(
    ARGUMENTS_FEATURE,
    (request, host) => {
      if (!isArgumentsIndexEarlyRequest(request)) return false
      const { owner, availability, kind, items, generation, shell } = request
      presentArgumentsIndex({ owner, host, availability, kind, items, generation, shell })
      return true
    },
    target,
  )
  registerEarlyRoutePresenter(
    ARGUMENT_DETAIL_FEATURE,
    (request, host) => {
      if (!isArgumentDetailEarlyRequest(request)) return false
      const { owner, availability, argument, argumentId, generation, shell } = request
      presentArgumentDetail({
        owner,
        host,
        availability,
        argument,
        argumentId,
        generation,
        shell,
      })
      return true
    },
    target,
  )
}
