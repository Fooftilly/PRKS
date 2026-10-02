import { createVNode } from 'vue'
import {
  dismissRouteSurface,
  presentRouteSurface,
  registerEarlyRoutePresenter,
  resetRouteSurfaceForTests,
  type RouteSurfaceOwner,
} from '../../route-surface/lifecycle'
import { closeTagsAliasModals, closeTagsMergeModals } from './closers'
import { browserTagsIntents, type TagsIntentOwner } from './intents'
import { buildTagsProjection, type TagsProjection, type TagsResume } from './projection'
import type { TagsRouteInstance } from './route'
import TagsRoute from './TagsRoute.vue'

export interface TagsPresentInput {
  owner: RouteSurfaceOwner & TagsIntentOwner
  host: HTMLElement
  tags: unknown
  generation?: number
  /**
   * True when this owner is the Main shell. Recorded on the route instance.
   * The Tags sidebar is static copy; Vue does not publish it.
   */
  shell?: boolean
  /** Reopen one tag's alias dialog after this owner reloads. */
  resume?: TagsResume | null
}

const TAGS_FEATURE = 'tags'

function isTagsEarlyRequest(
  value: unknown,
): value is Omit<TagsPresentInput, 'host'> & { feature: typeof TAGS_FEATURE } {
  if (!value || typeof value !== 'object') return false
  const record = value as Partial<TagsPresentInput> & { feature?: unknown }
  return record.feature === TAGS_FEATURE && !!record.owner && 'tags' in record
}

/**
 * Paint one owner's Tags vocabulary page. The coordinator has already loaded
 * `fetchTags({ used: true })`. Vue does not fetch it.
 */
export function presentTags(input: TagsPresentInput): void {
  if (!input || !input.owner || typeof input.owner !== 'object') return
  const owner = input.owner
  const tags = input.tags
  const resume = input.resume
  const route: Omit<TagsRouteInstance, 'generation'> & { generation?: number } = {
    name: 'tags',
    canonicalHash: '#/tags',
    params: {},
    ownsMainShell: input.shell !== false,
    generation: input.generation,
  }
  presentRouteSurface({
    owner,
    host: input.host,
    route,
    render: (generation) => {
      const projection: TagsProjection = buildTagsProjection({ tags, generation, resume })
      return createVNode(TagsRoute, {
        projection,
        intents: browserTagsIntents(owner, generation),
      })
    },
  })
}

export function dismissTags(owner: object | null | undefined): void {
  dismissRouteSurface(owner)
}

export function resetTagsSessionForTests(): void {
  resetRouteSurfaceForTests()
}

export function registerTagsBridge(target: Window = window): void {
  target.prksVuePresentTags = presentTags
  target.prksVueDismissTags = dismissTags
  target.prksVueCloseTagsAliasModal = closeTagsAliasModals
  target.prksVueCloseTagsMergeModal = closeTagsMergeModals
  registerEarlyRoutePresenter(
    TAGS_FEATURE,
    (request, host) => {
      if (!isTagsEarlyRequest(request)) return false
      const { owner, tags, generation, shell, resume } = request
      presentTags({ owner, host, tags, generation, shell, resume })
      return true
    },
    target,
  )
}
