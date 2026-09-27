import { createVNode } from 'vue'
import {
  dismissRouteSurface,
  presentRouteSurface,
  registerEarlyRoutePresenter,
  resetRouteSurfaceForTests,
  type RouteSurfaceOwner,
} from '../../route-surface/lifecycle'
import ConceptDetailRoute from './ConceptDetailRoute.vue'
import ConceptIntentsProvider from './ConceptIntentsProvider.vue'
import ConceptsIndexRoute from './ConceptsIndexRoute.vue'
import type { ConceptIntentOwner } from './intents'
import {
  buildConceptDetailProjection,
  buildConceptIndexProjection,
  type ConceptDetailProjection,
  type ConceptIndexProjection,
} from './projection'
import type { ConceptDetailRouteInstance, ConceptsIndexRouteInstance } from './route'
import type { ConceptDetailAvailability, ConceptIndexAvailability } from './types'

const CONCEPTS_FEATURE = 'concepts'
const CONCEPT_DETAIL_FEATURE = 'concept-detail'

/** Set by the route coordinator before beginRoute when staying on Concepts. */
export const CONCEPTS_RETAIN_SURFACE_KEY = '__prksRetainConceptsSurface'
const CONCEPTS_CLEANUP_ARMED_KEY = '__prksConceptsCleanupArmed'

type ConceptsOwner = RouteSurfaceOwner &
  ConceptIntentOwner & {
    [CONCEPTS_RETAIN_SURFACE_KEY]?: boolean
    [CONCEPTS_CLEANUP_ARMED_KEY]?: boolean
  }

/**
 * Concepts dismisses on leave/destroy, not on every beginRoute.
 * Retained refreshes set CONCEPTS_RETAIN_SURFACE_KEY so cleanup is a no-op;
 * the next present re-arms because beginRoute clears the cleanup set.
 */
function armConceptsOwnerCleanup(owner: ConceptsOwner): void {
  if (owner[CONCEPTS_CLEANUP_ARMED_KEY] || typeof owner.registerCleanup !== 'function') return
  owner[CONCEPTS_CLEANUP_ARMED_KEY] = true
  owner.registerCleanup(() => {
    owner[CONCEPTS_CLEANUP_ARMED_KEY] = false
    if (owner[CONCEPTS_RETAIN_SURFACE_KEY]) return
    dismissConcepts(owner)
  })
}

export interface ConceptsIndexPresentInput {
  owner: ConceptsOwner
  host: HTMLElement
  availability?: ConceptIndexAvailability
  items?: unknown
  generation?: number
  shell?: boolean
}

export interface ConceptDetailPresentInput {
  owner: ConceptsOwner
  host: HTMLElement
  availability?: ConceptDetailAvailability
  concept?: unknown
  conceptId?: string
  generation?: number
  shell?: boolean
}

/**
 * Paint one owner's Concepts index from an already-effective projection.
 * Owner session bookkeeping lives in the shared route-surface lifecycle.
 */
export function presentConceptsIndex(input: ConceptsIndexPresentInput): void {
  if (!input || !input.owner || typeof input.owner !== 'object') return
  const availability: ConceptIndexAvailability =
    input.availability === 'unavailable' ? 'unavailable' : 'ready'
  const items = input.items
  const route: Omit<ConceptsIndexRouteInstance, 'generation'> & { generation?: number } = {
    name: 'concepts',
    canonicalHash: '#/concepts',
    params: {},
    ownsMainShell: input.shell !== false,
    generation: input.generation,
  }
  presentRouteSurface({
    owner: input.owner,
    host: input.host,
    route,
    // Coordinator retains the host across Concepts→Concepts beginRoute; dismiss
    // is explicit on leave (and via retain-aware owner cleanup on destroy).
    armBeginRouteCleanup: false,
    render: (generation) => {
      const projection: ConceptIndexProjection = buildConceptIndexProjection({
        availability,
        items,
        generation,
      })
      // Stable provider component type so same-host repaints keep local Vue state
      // (e.g. searchQuery) instead of remounting a fresh anonymous definition.
      return createVNode(
        ConceptIntentsProvider,
        { owner: input.owner, generation },
        { default: () => createVNode(ConceptsIndexRoute, { projection }) },
      )
    },
  })
  armConceptsOwnerCleanup(input.owner)
}

/** Paint one owner's Concept detail from an already-effective projection. */
export function presentConceptDetail(input: ConceptDetailPresentInput): void {
  if (!input || !input.owner || typeof input.owner !== 'object') return
  const availability: ConceptDetailAvailability =
    input.availability === 'unavailable' || input.availability === 'not-found'
      ? input.availability
      : 'ready'
  const concept = input.concept
  const conceptId =
    typeof input.conceptId === 'string' && input.conceptId
      ? input.conceptId
      : typeof concept === 'object' && concept && 'id' in concept
        ? String((concept as { id?: unknown }).id || '')
        : ''
  const route: Omit<ConceptDetailRouteInstance, 'generation'> & { generation?: number } = {
    name: 'concept-detail',
    canonicalHash: conceptId ? `#/concepts/${encodeURIComponent(conceptId)}` : '#/concepts',
    params: { conceptId },
    ownsMainShell: input.shell !== false,
    generation: input.generation,
  }
  presentRouteSurface({
    owner: input.owner,
    host: input.host,
    route,
    armBeginRouteCleanup: false,
    render: (generation) => {
      const projection: ConceptDetailProjection = buildConceptDetailProjection({
        availability,
        concept,
        generation,
      })
      return createVNode(
        ConceptIntentsProvider,
        { owner: input.owner, generation },
        { default: () => createVNode(ConceptDetailRoute, { projection }) },
      )
    },
  })
  armConceptsOwnerCleanup(input.owner)
}

/** Drop the Vue Concepts tree owned by this pane. Other owners stay mounted. */
export function dismissConcepts(owner: object | null | undefined): void {
  dismissRouteSurface(owner)
}

export function resetConceptsSessionForTests(): void {
  resetRouteSurfaceForTests()
}

function isConceptsIndexEarlyRequest(
  value: unknown,
): value is Omit<ConceptsIndexPresentInput, 'host'> & { feature: typeof CONCEPTS_FEATURE } {
  if (!value || typeof value !== 'object') return false
  const record = value as Partial<ConceptsIndexPresentInput> & { feature?: unknown }
  return record.feature === CONCEPTS_FEATURE && !!record.owner
}

function isConceptDetailEarlyRequest(
  value: unknown,
): value is Omit<ConceptDetailPresentInput, 'host'> & { feature: typeof CONCEPT_DETAIL_FEATURE } {
  if (!value || typeof value !== 'object') return false
  const record = value as Partial<ConceptDetailPresentInput> & { feature?: unknown }
  return record.feature === CONCEPT_DETAIL_FEATURE && !!record.owner
}

export function registerConceptsBridge(target: Window = window): void {
  target.prksVuePresentConceptsIndex = presentConceptsIndex
  target.prksVuePresentConceptDetail = presentConceptDetail
  target.prksVueDismissConcepts = dismissConcepts
  registerEarlyRoutePresenter(
    CONCEPTS_FEATURE,
    (request, host) => {
      if (!isConceptsIndexEarlyRequest(request)) return false
      const { owner, availability, items, generation, shell } = request
      presentConceptsIndex({ owner, host, availability, items, generation, shell })
      return true
    },
    target,
  )
  registerEarlyRoutePresenter(
    CONCEPT_DETAIL_FEATURE,
    (request, host) => {
      if (!isConceptDetailEarlyRequest(request)) return false
      const { owner, availability, concept, conceptId, generation, shell } = request
      presentConceptDetail({
        owner,
        host,
        availability,
        concept,
        conceptId,
        generation,
        shell,
      })
      return true
    },
    target,
  )
}
