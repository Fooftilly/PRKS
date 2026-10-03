import { createVNode } from 'vue'
import {
  dismissRouteSurface,
  presentRouteSurface,
  registerEarlyRoutePresenter,
  registerRouteWindowBridge,
  resetRouteSurfaceForTests,
  type RouteSurfaceOwner,
} from '../../route-surface/lifecycle'
import PeopleIndexRoute from './PeopleIndexRoute.vue'
import PeopleIntentsProvider from './PeopleIntentsProvider.vue'
import PersonDetailRoute from './PersonDetailRoute.vue'
import type { PeopleIntentOwner } from './intents'
import {
  buildPeopleIndexProjection,
  buildPersonDetailProjection,
  type PeopleIndexProjection,
  type PersonDetailProjection,
} from './projection'
import type { PeopleIndexRouteInstance, PersonDetailRouteInstance } from './route'
import type { PeopleIndexAvailability, PersonDetailAvailability } from './types'

const PEOPLE_FEATURE = 'people'
const PERSON_DETAIL_FEATURE = 'person'

/** Set by the route coordinator before beginRoute when staying on People. */
export const PEOPLE_RETAIN_SURFACE_KEY = '__prksRetainPeopleSurface'
const PEOPLE_CLEANUP_ARMED_KEY = '__prksPeopleCleanupArmed'

type PeopleOwner = RouteSurfaceOwner &
  PeopleIntentOwner & {
    [PEOPLE_RETAIN_SURFACE_KEY]?: boolean
    [PEOPLE_CLEANUP_ARMED_KEY]?: boolean
  }

/**
 * People dismisses on leave/destroy, not on every beginRoute.
 * A retained refresh sets PEOPLE_RETAIN_SURFACE_KEY so this cleanup does
 * not unmount, then re-arms immediately: beginRoute already drained the set,
 * and a failed refresh never reaches present to register another callback.
 */
function armPeopleOwnerCleanup(owner: PeopleOwner): void {
  if (owner[PEOPLE_CLEANUP_ARMED_KEY] || typeof owner.registerCleanup !== 'function') return
  owner[PEOPLE_CLEANUP_ARMED_KEY] = true
  owner.registerCleanup(() => {
    owner[PEOPLE_CLEANUP_ARMED_KEY] = false
    if (owner[PEOPLE_RETAIN_SURFACE_KEY]) {
      armPeopleOwnerCleanup(owner)
      return
    }
    dismissRouteSurface(owner)
  })
}

export interface PeopleIndexPresentInput {
  owner: PeopleOwner
  host: HTMLElement
  availability?: PeopleIndexAvailability
  items?: unknown
  roleFilter?: string
  unknownRole?: boolean
  generation?: number
  shell?: boolean
}

export interface PersonDetailPresentInput {
  owner: PeopleOwner
  host: HTMLElement
  availability?: PersonDetailAvailability
  person?: unknown
  personId?: string
  editing?: boolean
  editorActive?: boolean
  worksEditing?: boolean
  offlineCached?: boolean
  generation?: number
  shell?: boolean
}

function focusedOwner(owner: PeopleOwner): boolean {
  const check = window.prksTabContextIsFocused
  if (typeof check !== 'function') return true
  return check(owner)
}

export function presentPeopleIndex(input: PeopleIndexPresentInput): void {
  if (!input || !input.owner || typeof input.owner !== 'object') return
  const availability: PeopleIndexAvailability = input.unknownRole
    ? 'unknown-role'
    : input.availability === 'unavailable'
      ? 'unavailable'
      : 'ready'
  const items = input.items
  const roleFilter = typeof input.roleFilter === 'string' ? input.roleFilter : ''
  const unknownRole = input.unknownRole === true
  const hash = roleFilter ? `#/people/role/${encodeURIComponent(roleFilter)}` : '#/people'
  const route: Omit<PeopleIndexRouteInstance, 'generation'> & { generation?: number } = {
    name: 'people',
    canonicalHash: hash,
    params: roleFilter ? { role: roleFilter } : {},
    ownsMainShell: input.shell !== false,
    generation: input.generation,
  }
  presentRouteSurface({
    owner: input.owner,
    host: input.host,
    route,
    armBeginRouteCleanup: false,
    render: (generation) => {
      const projection: PeopleIndexProjection = buildPeopleIndexProjection({
        availability,
        items,
        roleFilter,
        unknownRole,
        generation,
      })
      return createVNode(
        PeopleIntentsProvider,
        { owner: input.owner, generation },
        { default: () => createVNode(PeopleIndexRoute, { projection }) },
      )
    },
  })
  armPeopleOwnerCleanup(input.owner)
}

export function presentPersonDetail(input: PersonDetailPresentInput): void {
  if (!input || !input.owner || typeof input.owner !== 'object') return
  const availability: PersonDetailAvailability =
    input.availability === 'unavailable' || input.availability === 'not-found'
      ? input.availability
      : 'ready'
  const person = input.person
  const personId =
    typeof input.personId === 'string' && input.personId
      ? input.personId
      : typeof person === 'object' && person && 'id' in person
        ? String((person as { id?: unknown }).id || '')
        : ''
  const editing = input.editing === true
  const editorActive = input.editorActive === false ? false : focusedOwner(input.owner)
  const worksEditing = input.worksEditing === true
  const offlineCached = input.offlineCached === true
  const route: Omit<PersonDetailRouteInstance, 'generation'> & { generation?: number } = {
    name: 'person',
    canonicalHash: personId ? `#/people/${encodeURIComponent(personId)}` : '#/people',
    params: { personId },
    ownsMainShell: input.shell !== false,
    generation: input.generation,
  }
  presentRouteSurface({
    owner: input.owner,
    host: input.host,
    route,
    armBeginRouteCleanup: false,
    render: (generation) => {
      const projection: PersonDetailProjection = buildPersonDetailProjection({
        availability,
        person,
        personId,
        editing,
        editorActive: editing ? editorActive : false,
        worksEditing,
        offlineCached,
        generation,
      })
      return createVNode(
        PeopleIntentsProvider,
        { owner: input.owner, generation },
        { default: () => createVNode(PersonDetailRoute, { projection }) },
      )
    },
  })
  armPeopleOwnerCleanup(input.owner)
}

export function resetPeopleSessionForTests(): void {
  resetRouteSurfaceForTests()
}

function isIndexEarlyRequest(
  value: unknown,
): value is Omit<PeopleIndexPresentInput, 'host'> & { feature: typeof PEOPLE_FEATURE } {
  if (!value || typeof value !== 'object') return false
  const record = value as Partial<PeopleIndexPresentInput> & { feature?: unknown }
  return record.feature === PEOPLE_FEATURE && !!record.owner
}

function isDetailEarlyRequest(
  value: unknown,
): value is Omit<PersonDetailPresentInput, 'host'> & { feature: typeof PERSON_DETAIL_FEATURE } {
  if (!value || typeof value !== 'object') return false
  const record = value as Partial<PersonDetailPresentInput> & { feature?: unknown }
  return record.feature === PERSON_DETAIL_FEATURE && !!record.owner
}

export function registerPeopleBridge(target: Window = window): void {
  registerRouteWindowBridge(target)
  registerEarlyRoutePresenter(
    PEOPLE_FEATURE,
    (request, host) => {
      if (!isIndexEarlyRequest(request)) return false
      const { owner, availability, items, roleFilter, unknownRole, generation, shell } = request
      presentPeopleIndex({ owner, host, availability, items, roleFilter, unknownRole, generation, shell })
      return true
    },
    target,
  )
  registerEarlyRoutePresenter(
    PERSON_DETAIL_FEATURE,
    (request, host) => {
      if (!isDetailEarlyRequest(request)) return false
      const {
        owner,
        availability,
        person,
        personId,
        editing,
        editorActive,
        worksEditing,
        offlineCached,
        generation,
        shell,
      } = request
      presentPersonDetail({
        owner,
        host,
        availability,
        person,
        personId,
        editing,
        editorActive,
        worksEditing,
        offlineCached,
        generation,
        shell,
      })
      return true
    },
    target,
  )
}
