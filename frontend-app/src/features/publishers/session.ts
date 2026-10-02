import { createVNode } from 'vue'
import {
  dismissRouteSurface,
  presentRouteSurface,
  registerEarlyRoutePresenter,
  resetRouteSurfaceForTests,
  type RouteSurfaceOwner,
} from '../../route-surface/lifecycle'
import { closePublishersAliasModals } from './closers'
import { browserPublishersIntents, type PublishersIntentOwner } from './intents'
import {
  buildPublishersProjection,
  type PublishersProjection,
  type PublishersResume,
} from './projection'
import type { PublishersRouteInstance } from './route'
import PublishersRoute from './PublishersRoute.vue'

export interface PublishersPresentInput {
  owner: RouteSurfaceOwner & PublishersIntentOwner
  host: HTMLElement
  publishers: unknown
  generation?: number
  /**
   * True when this owner is the Main shell. Recorded on the route instance.
   * The Publishers sidebar is static copy; Vue does not publish it.
   */
  shell?: boolean
  /** Reopen one publisher's alias dialog after this owner reloads. */
  resume?: PublishersResume | null
}

const PUBLISHERS_FEATURE = 'publishers'

function isPublishersEarlyRequest(
  value: unknown,
): value is Omit<PublishersPresentInput, 'host'> & { feature: typeof PUBLISHERS_FEATURE } {
  if (!value || typeof value !== 'object') return false
  const record = value as Partial<PublishersPresentInput> & { feature?: unknown }
  return record.feature === PUBLISHERS_FEATURE && !!record.owner && 'publishers' in record
}

/**
 * Paint one owner's Publishers page. The coordinator has already loaded
 * `fetchPublishersInUse`. Vue does not fetch it.
 */
export function presentPublishers(input: PublishersPresentInput): void {
  if (!input || !input.owner || typeof input.owner !== 'object') return
  const owner = input.owner
  const publishers = input.publishers
  const resume = input.resume
  const route: Omit<PublishersRouteInstance, 'generation'> & { generation?: number } = {
    name: 'publishers',
    canonicalHash: '#/publishers',
    params: {},
    ownsMainShell: input.shell !== false,
    generation: input.generation,
  }
  presentRouteSurface({
    owner,
    host: input.host,
    route,
    render: (generation) => {
      const projection: PublishersProjection = buildPublishersProjection({ publishers, generation, resume })
      return createVNode(PublishersRoute, {
        projection,
        intents: browserPublishersIntents(owner, generation),
      })
    },
  })
}

export function dismissPublishers(owner: object | null | undefined): void {
  dismissRouteSurface(owner)
}

export function resetPublishersSessionForTests(): void {
  resetRouteSurfaceForTests()
}

export function registerPublishersBridge(target: Window = window): void {
  target.prksVuePresentPublishers = presentPublishers
  target.prksVueDismissPublishers = dismissPublishers
  target.prksVueClosePublishersAliasModal = closePublishersAliasModals
  registerEarlyRoutePresenter(
    PUBLISHERS_FEATURE,
    (request, host) => {
      if (!isPublishersEarlyRequest(request)) return false
      const { owner, publishers, generation, shell, resume } = request
      presentPublishers({ owner, host, publishers, generation, shell, resume })
      return true
    },
    target,
  )
}
