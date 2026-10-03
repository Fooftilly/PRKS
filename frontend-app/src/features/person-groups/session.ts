import { createVNode } from 'vue'
import {
  dismissRouteSurface,
  presentRouteSurface,
  registerEarlyRoutePresenter,
  registerRouteWindowBridge,
  resetRouteSurfaceForTests,
  type RouteSurfaceOwner,
} from '../../route-surface/lifecycle'
import PersonGroupDetailRoute from './PersonGroupDetailRoute.vue'
import PersonGroupsIndexRoute from './PersonGroupsIndexRoute.vue'
import PersonGroupsIntentsProvider from './PersonGroupsIntentsProvider.vue'
import type { PersonGroupIntentOwner } from './intents'
import {
  buildPersonGroupDetailProjection,
  buildPersonGroupsIndexProjection,
  type PersonGroupDetailProjection,
  type PersonGroupsIndexProjection,
} from './projection'
import type { PersonGroupDetailRouteInstance, PersonGroupsIndexRouteInstance } from './route'
import type { PersonGroupsAvailability } from './types'

const GROUPS_FEATURE = 'person-groups'
const GROUP_DETAIL_FEATURE = 'person-group-detail'

/** Set by the route coordinator before beginRoute when staying on Person Groups. */
export const PERSON_GROUPS_RETAIN_SURFACE_KEY = '__prksRetainPersonGroupsSurface'
const PERSON_GROUPS_CLEANUP_ARMED_KEY = '__prksPersonGroupsCleanupArmed'

type PersonGroupOwner = RouteSurfaceOwner &
  PersonGroupIntentOwner & {
    [PERSON_GROUPS_RETAIN_SURFACE_KEY]?: boolean
    [PERSON_GROUPS_CLEANUP_ARMED_KEY]?: boolean
  }

/**
 * Person Groups dismisses on leave/destroy, not on every beginRoute.
 * A retained refresh sets PERSON_GROUPS_RETAIN_SURFACE_KEY so this cleanup
 * does not unmount, then re-arms immediately: beginRoute already drained the
 * set, and a failed refresh never reaches present to register another callback.
 */
function armPersonGroupsOwnerCleanup(owner: PersonGroupOwner): void {
  if (owner[PERSON_GROUPS_CLEANUP_ARMED_KEY] || typeof owner.registerCleanup !== 'function') return
  owner[PERSON_GROUPS_CLEANUP_ARMED_KEY] = true
  owner.registerCleanup(() => {
    owner[PERSON_GROUPS_CLEANUP_ARMED_KEY] = false
    if (owner[PERSON_GROUPS_RETAIN_SURFACE_KEY]) {
      armPersonGroupsOwnerCleanup(owner)
      return
    }
    dismissPersonGroups(owner)
  })
}

export interface PersonGroupsIndexPresentInput {
  owner: PersonGroupOwner
  host: HTMLElement
  availability?: string
  items?: unknown
  generation?: number
  shell?: boolean
}

export interface PersonGroupDetailPresentInput {
  owner: PersonGroupOwner
  host: HTMLElement
  availability?: string
  group?: unknown
  groupId?: string
  editing?: boolean
  membersEditing?: boolean
  editorActive?: boolean
  generation?: number
  shell?: boolean
}

function focusedOwner(owner: PersonGroupOwner): boolean {
  const check = window.prksTabContextIsFocused
  if (typeof check !== 'function') return true
  return check(owner)
}

export function presentPersonGroupsIndex(input: PersonGroupsIndexPresentInput): void {
  if (!input || !input.owner || typeof input.owner !== 'object') return
  const availability: PersonGroupsAvailability = input.availability === 'unavailable' ? 'unavailable' : 'ready'
  const items = input.items
  const route: Omit<PersonGroupsIndexRouteInstance, 'generation'> & { generation?: number } = {
    name: 'people-groups',
    canonicalHash: '#/people/groups',
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
      const projection: PersonGroupsIndexProjection = buildPersonGroupsIndexProjection({
        availability,
        items,
        generation,
      })
      return createVNode(
        PersonGroupsIntentsProvider,
        { owner: input.owner, generation },
        { default: () => createVNode(PersonGroupsIndexRoute, { projection }) },
      )
    },
  })
  armPersonGroupsOwnerCleanup(input.owner)
}

export function presentPersonGroupDetail(input: PersonGroupDetailPresentInput): void {
  if (!input || !input.owner || typeof input.owner !== 'object') return
  const availability: PersonGroupsAvailability =
    input.availability === 'unavailable' || input.availability === 'not-found' ? input.availability : 'ready'
  const group = input.group
  const groupId =
    typeof input.groupId === 'string' && input.groupId
      ? input.groupId
      : typeof group === 'object' && group && 'id' in group
        ? String((group as { id?: unknown }).id || '')
        : ''
  const editing = input.editing === true
  const membersEditing = input.membersEditing === true && !editing
  const editorActive = input.editorActive === false ? false : focusedOwner(input.owner)
  const route: Omit<PersonGroupDetailRouteInstance, 'generation'> & { generation?: number } = {
    name: 'person-group-detail',
    canonicalHash: groupId ? `#/people/groups/${encodeURIComponent(groupId)}` : '#/people/groups',
    params: { groupId },
    ownsMainShell: input.shell !== false,
    generation: input.generation,
  }
  presentRouteSurface({
    owner: input.owner,
    host: input.host,
    route,
    armBeginRouteCleanup: false,
    render: (generation) => {
      const projection: PersonGroupDetailProjection = buildPersonGroupDetailProjection({
        availability,
        group,
        groupId,
        editing,
        membersEditing,
        editorActive: editing ? editorActive : false,
        generation,
      })
      return createVNode(
        PersonGroupsIntentsProvider,
        { owner: input.owner, generation },
        { default: () => createVNode(PersonGroupDetailRoute, { projection }) },
      )
    },
  })
  armPersonGroupsOwnerCleanup(input.owner)
}

export function dismissPersonGroups(owner: object | null | undefined): void {
  dismissRouteSurface(owner)
}

export function resetPersonGroupsSessionForTests(): void {
  resetRouteSurfaceForTests()
}

function isIndexEarlyRequest(
  value: unknown,
): value is Omit<PersonGroupsIndexPresentInput, 'host'> & { feature: typeof GROUPS_FEATURE } {
  if (!value || typeof value !== 'object') return false
  const record = value as Partial<PersonGroupsIndexPresentInput> & { feature?: unknown }
  return record.feature === GROUPS_FEATURE && !!record.owner
}

function isDetailEarlyRequest(
  value: unknown,
): value is Omit<PersonGroupDetailPresentInput, 'host'> & { feature: typeof GROUP_DETAIL_FEATURE } {
  if (!value || typeof value !== 'object') return false
  const record = value as Partial<PersonGroupDetailPresentInput> & { feature?: unknown }
  return record.feature === GROUP_DETAIL_FEATURE && !!record.owner
}

export function registerPersonGroupsBridge(target: Window = window): void {
  registerRouteWindowBridge(target)
  target.prksVueDismissPersonGroups = dismissPersonGroups
  registerEarlyRoutePresenter(
    GROUPS_FEATURE,
    (request, host) => {
      if (!isIndexEarlyRequest(request)) return false
      const { owner, availability, items, generation, shell } = request
      presentPersonGroupsIndex({ owner, host, availability, items, generation, shell })
      return true
    },
    target,
  )
  registerEarlyRoutePresenter(
    GROUP_DETAIL_FEATURE,
    (request, host) => {
      if (!isDetailEarlyRequest(request)) return false
      const { owner, availability, group, groupId, editing, membersEditing, editorActive, generation, shell } =
        request
      presentPersonGroupDetail({
        owner,
        host,
        availability,
        group,
        groupId,
        editing,
        membersEditing,
        editorActive,
        generation,
        shell,
      })
      return true
    },
    target,
  )
}
