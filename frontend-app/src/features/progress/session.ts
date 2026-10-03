import { createVNode } from 'vue'
import {
  dismissRouteSurface,
  presentRouteSurface,
  registerEarlyRoutePresenter,
  registerRouteWindowBridge,
  resetRouteSurfaceForTests,
  type RouteSurfaceOwner,
} from '../../route-surface/lifecycle'
import ProgressView from './ProgressView.vue'
import type { ProgressRouteInstance } from './route'
import { acceptEffectiveRows } from './rows'
import type { ProgressSnapshot } from './state'
import { canonicalProgressStatus, progressCanonicalHash } from './status'

export interface ProgressPresentInput {
  owner: RouteSurfaceOwner
  host: HTMLElement
  status: string | null | undefined
  rows: unknown
  offlineCached?: boolean
  generation?: number
  /**
   * True when this owner is the Main shell. Recorded on the route instance.
   * Sidebar publication stays in the legacy router.
   */
  shell?: boolean
}

const PROGRESS_FEATURE = 'progress'

function isProgressEarlyRequest(
  value: unknown,
): value is Omit<ProgressPresentInput, 'host'> & { feature: typeof PROGRESS_FEATURE } {
  if (!value || typeof value !== 'object') return false
  const record = value as Partial<ProgressPresentInput> & { feature?: unknown }
  return record.feature === PROGRESS_FEATURE && !!record.owner && 'status' in record && 'rows' in record
}

/**
 * Paint one owner's Progress surface from an already-effective browse snapshot.
 * Owner session bookkeeping lives in the shared route-surface lifecycle.
 */
export function presentProgress(input: ProgressPresentInput): void {
  if (!input || !input.owner || typeof input.owner !== 'object') return
  const status = canonicalProgressStatus(input.status)
  const offlineCached = input.offlineCached === true
  const rows = input.rows
  const route: Omit<ProgressRouteInstance, 'generation'> & { generation?: number } = {
    name: 'progress',
    canonicalHash: progressCanonicalHash(status),
    params: { status },
    ownsMainShell: input.shell !== false,
    generation: input.generation,
  }
  presentRouteSurface({
    owner: input.owner,
    host: input.host,
    route,
    render: (generation) => {
      const snapshot: ProgressSnapshot = {
        status,
        rows: acceptEffectiveRows(rows),
        offlineCached,
        generation,
      }
      return createVNode(ProgressView, { snapshot })
    },
  })
}

/** Drop the Vue Progress tree owned by this pane. Other owners stay mounted. */
export function dismissProgress(owner: object | null | undefined): void {
  dismissRouteSurface(owner)
}

export function resetProgressSessionForTests(): void {
  resetRouteSurfaceForTests()
}

export function registerProgressBridge(target: Window = window): void {
  registerRouteWindowBridge(target)
  target.prksVueDismissProgress = dismissProgress
  registerEarlyRoutePresenter(
    PROGRESS_FEATURE,
    (request, host) => {
      if (!isProgressEarlyRequest(request)) return false
      const { owner, status, rows, offlineCached, generation, shell } = request
      presentProgress({ owner, host, status, rows, offlineCached, generation, shell })
      return true
    },
    target,
  )
}
