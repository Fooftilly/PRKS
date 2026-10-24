import { createVNode } from 'vue'
import {
  presentRouteSurface,
  registerEarlyRoutePresenter,
  registerRouteWindowBridge,
  resetRouteSurfaceForTests,
  type RouteSurfaceOwner,
} from '../../route-surface/lifecycle'
import { closePublishersAliasModals } from './closers'
import { browserPublishersIntents, type PublishersIntentOwner } from './intents'
import type { PublishersRouteInstance } from './route'
import PublishersRoute from './PublishersRoute.vue'

export interface PublishersPresentInput {
  owner: RouteSurfaceOwner & PublishersIntentOwner
  host: HTMLElement
  generation?: number
  /**
   * True when this owner is the Main shell. Recorded on the route instance.
   * The Publishers sidebar is static copy; Vue does not publish it.
   */
  shell?: boolean
}

const PUBLISHERS_FEATURE = 'publishers'

function isPublishersEarlyRequest(
  value: unknown,
): value is Omit<PublishersPresentInput, 'host'> & { feature: typeof PUBLISHERS_FEATURE } {
  if (!value || typeof value !== 'object') return false
  const record = value as Partial<PublishersPresentInput> & { feature?: unknown }
  return record.feature === PUBLISHERS_FEATURE && !!record.owner
}

/**
 * Paint one owner's Publishers page. The page reads and writes Publishers
 * through the typed client and the shared QueryClient; the coordinator only
 * keeps the offline gate in front of it.
 */
export function presentPublishers(input: PublishersPresentInput): void {
  if (!input || !input.owner || typeof input.owner !== 'object') return
  const owner = input.owner
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
    render: (generation) =>
      createVNode(PublishersRoute, {
        intents: browserPublishersIntents(owner, generation),
      }),
  })
}

export function resetPublishersSessionForTests(): void {
  resetRouteSurfaceForTests()
}

export function registerPublishersBridge(target: Window = window): void {
  registerRouteWindowBridge(target)
  target.prksVueClosePublishersAliasModal = closePublishersAliasModals
  registerEarlyRoutePresenter(
    PUBLISHERS_FEATURE,
    (request, host) => {
      if (!isPublishersEarlyRequest(request)) return false
      const { owner, generation, shell } = request
      presentPublishers({ owner, host, generation, shell })
      return true
    },
    target,
  )
}
