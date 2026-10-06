/**
 * Work detail route-surface presenter.
 * The shell is Vue. PDF, Research Notes, and the right panel mount from
 * `attach` after this owner's generation is accepted.
 */
import { createVNode, render } from 'vue'
import {
  presentRouteSurface,
  registerEarlyRoutePresenter,
  registerRouteWindowBridge,
  resetRouteSurfaceForTests,
  type RouteSurfaceOwner,
} from '../../route-surface/lifecycle'
import { mountWorkDetail, type WorkDetailMountRequest } from './detail-lifecycle'
import type { WorkMainSurfaceModel } from './main-surface'
import type { WorkDetailRouteInstance } from './route'
import WorkDetailRoute from './WorkDetailRoute.vue'

export interface WorkDetailPresentOwner extends RouteSurfaceOwner {
  tabId?: unknown
  destroyed?: boolean
  ui?: object | null
  root?: ParentNode | null
  generation?: number
  isCurrent?: (generation: number) => boolean
  getEntity?: (type: string) => { id?: unknown } | null
}

export interface WorkDetailPresentInput {
  owner: WorkDetailPresentOwner
  host: HTMLElement
  availability: 'ready' | 'not-found'
  workId: string
  generation?: number
  /** True when this owner is the Main shell. Recorded on the route instance. */
  shell?: boolean
  surface: WorkMainSurfaceModel | null
  /** Runs only after this generation's shell is in the host. */
  attach?: (() => void) | null
}

const WORK_FEATURE = 'work'

function clearRouteLoading(host: HTMLElement): void {
  if (!host.querySelector(':scope > .prks-route-loading')) return
  render(null, host)
  host.replaceChildren()
  host.removeAttribute('aria-busy')
}

function isWorkEarlyRequest(
  value: unknown,
): value is Omit<WorkDetailPresentInput, 'host'> & { feature: typeof WORK_FEATURE } {
  if (!value || typeof value !== 'object') return false
  const record = value as Partial<WorkDetailPresentInput> & { feature?: unknown }
  return record.feature === WORK_FEATURE && !!record.owner && typeof record.workId === 'string'
}

/**
 * Paint one owner's Work shell. A stale generation or a different installed
 * Work paints nothing and does not call `attach`.
 */
export function presentWorkDetail(input: WorkDetailPresentInput): boolean {
  const owner = input.owner
  if (!owner || owner.destroyed || !owner.ui || !input.host) return false
  const workId = String(input.workId || '')
  if (!workId && input.availability === 'ready') return false
  if (
    typeof input.generation === 'number' &&
    typeof owner.isCurrent === 'function' &&
    !owner.isCurrent(input.generation)
  ) {
    return false
  }
  if (input.availability === 'ready') {
    const surface = input.surface
    if (!surface || surface.workId !== workId) return false
    const live = owner.getEntity ? owner.getEntity('work') : null
    if (!live || String(live.id ?? '') !== workId) return false
  }
  clearRouteLoading(input.host)
  const canonicalHash = '#/works/' + encodeURIComponent(workId)
  const route: Omit<WorkDetailRouteInstance, 'generation'> & { generation?: number } = {
    name: 'work',
    canonicalHash,
    params: { workId },
    ownsMainShell: input.shell !== false,
    generation: input.generation,
  }
  const surface = input.surface
  const availability = input.availability
  const painted = presentRouteSurface({
    owner,
    host: input.host,
    route,
    render: () => createVNode(WorkDetailRoute, { availability, surface }),
  })
  if (!painted) return false
  if (availability === 'ready' && typeof input.attach === 'function') input.attach()
  return true
}

export function resetWorkDetailSessionForTests(): void {
  resetRouteSurfaceForTests()
}

export function registerWorkDetailBridge(target: Window = window): void {
  registerRouteWindowBridge(target)
  registerEarlyRoutePresenter(
    WORK_FEATURE,
    (request, host) => {
      if (!isWorkEarlyRequest(request)) return false
      const { owner, availability, workId, generation, shell, surface, attach } = request
      presentWorkDetail({
        owner,
        host,
        availability: availability === 'not-found' ? 'not-found' : 'ready',
        workId,
        generation,
        shell,
        surface: surface ?? null,
        attach: attach ?? null,
      })
      return true
    },
    target,
  )
  const bridge = target as Window & {
    prksMountWorkDetail?: (
      ctx: WorkDetailPresentOwner,
      contentDiv: HTMLElement,
      work: Record<string, unknown> | null,
      request?: WorkDetailMountRequest,
    ) => Promise<boolean>
  }
  bridge.prksMountWorkDetail = (ctx, contentDiv, work, request) =>
    mountWorkDetail(ctx, contentDiv, work, request)
}
