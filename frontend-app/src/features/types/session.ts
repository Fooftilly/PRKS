import { createVNode } from 'vue'
import {
  presentRouteSurface,
  registerEarlyRoutePresenter,
  registerRouteWindowBridge,
  resetRouteSurfaceForTests,
  type RouteSurfaceOwner,
} from '../../route-surface/lifecycle'
import TypeDetailRoute from './TypeDetailRoute.vue'
import TypesIndexRoute from './TypesIndexRoute.vue'
import { buildTypeDetailProjection, buildTypesIndexProjection, type TypeDetailProjection, type TypesIndexProjection } from './projection'
import type { TypeDetailRouteInstance, TypesIndexRouteInstance } from './route'

export interface TypesIndexPresentInput {
  owner: RouteSurfaceOwner
  host: HTMLElement
  rows: unknown
  generation?: number
  /**
   * True when this owner is the Main shell. Recorded on the route instance.
   * Sidebar publication stays in the legacy router.
   */
  shell?: boolean
}

export interface TypeDetailPresentInput {
  owner: RouteSurfaceOwner
  host: HTMLElement
  docType?: string
  label?: string
  canonicalHash?: string
  rows: unknown
  offlineCached?: boolean
  generation?: number
  shell?: boolean
}

const TYPES_FEATURE = 'types'
const TYPE_DETAIL_FEATURE = 'type-detail'

function isTypesIndexEarlyRequest(
  value: unknown,
): value is Omit<TypesIndexPresentInput, 'host'> & { feature: typeof TYPES_FEATURE } {
  if (!value || typeof value !== 'object') return false
  const record = value as Partial<TypesIndexPresentInput> & { feature?: unknown }
  return record.feature === TYPES_FEATURE && !!record.owner && 'rows' in record && !('docType' in record)
}

function isTypeDetailEarlyRequest(
  value: unknown,
): value is Omit<TypeDetailPresentInput, 'host'> & { feature: typeof TYPE_DETAIL_FEATURE } {
  if (!value || typeof value !== 'object') return false
  const record = value as Partial<TypeDetailPresentInput> & { feature?: unknown }
  return record.feature === TYPE_DETAIL_FEATURE && !!record.owner && 'rows' in record
}

/**
 * Paint one owner's File types index from an already-grouped model.
 * The coordinator owns `prksTypesIndexModel`. Vue does not regroup.
 */
export function presentTypesIndex(input: TypesIndexPresentInput): void {
  if (!input || !input.owner || typeof input.owner !== 'object') return
  const rows = input.rows
  const route: Omit<TypesIndexRouteInstance, 'generation'> & { generation?: number } = {
    name: 'types',
    canonicalHash: '#/types',
    params: {},
    ownsMainShell: input.shell !== false,
    generation: input.generation,
  }
  presentRouteSurface({
    owner: input.owner,
    host: input.host,
    route,
    render: (generation) => {
      const projection: TypesIndexProjection = buildTypesIndexProjection({ rows, generation })
      return createVNode(TypesIndexRoute, { projection })
    },
  })
}

/**
 * Paint one owner's type detail from an already-filtered model.
 * The coordinator owns `prksTypesDetailModel`. Vue does not filter.
 */
export function presentTypeDetail(input: TypeDetailPresentInput): void {
  if (!input || !input.owner || typeof input.owner !== 'object') return
  const docType = typeof input.docType === 'string' && input.docType ? input.docType : 'misc'
  const label = input.label
  const rows = input.rows
  const offlineCached = input.offlineCached === true
  const canonicalHash =
    typeof input.canonicalHash === 'string' && input.canonicalHash
      ? input.canonicalHash
      : `#/types/${encodeURIComponent(docType)}`
  const route: Omit<TypeDetailRouteInstance, 'generation'> & { generation?: number } = {
    name: 'type-detail',
    canonicalHash,
    params: { docType },
    ownsMainShell: input.shell !== false,
    generation: input.generation,
  }
  presentRouteSurface({
    owner: input.owner,
    host: input.host,
    route,
    render: (generation) => {
      const projection: TypeDetailProjection = buildTypeDetailProjection({
        docType,
        label,
        rows,
        offlineCached,
        generation,
      })
      return createVNode(TypeDetailRoute, { projection })
    },
  })
}

export function resetTypesSessionForTests(): void {
  resetRouteSurfaceForTests()
}

export function registerTypesBridge(target: Window = window): void {
  registerRouteWindowBridge(target)
  registerEarlyRoutePresenter(
    TYPES_FEATURE,
    (request, host) => {
      if (!isTypesIndexEarlyRequest(request)) return false
      const { owner, rows, generation, shell } = request
      presentTypesIndex({ owner, host, rows, generation, shell })
      return true
    },
    target,
  )
  registerEarlyRoutePresenter(
    TYPE_DETAIL_FEATURE,
    (request, host) => {
      if (!isTypeDetailEarlyRequest(request)) return false
      const { owner, docType, label, canonicalHash, rows, offlineCached, generation, shell } = request
      presentTypeDetail({
        owner,
        host,
        docType,
        label,
        canonicalHash,
        rows,
        offlineCached,
        generation,
        shell,
      })
      return true
    },
    target,
  )
}
