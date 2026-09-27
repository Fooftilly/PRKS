import type { PrksRouteInstanceBase } from '../../route-surface/route-instance'

/** Positions index narrowing of the shared route instance. */
export interface PositionsIndexRouteInstance extends PrksRouteInstanceBase {
  readonly name: 'positions'
  readonly params: Record<string, never>
}

/** Position detail narrowing of the shared route instance. */
export interface PositionDetailRouteInstance extends PrksRouteInstanceBase {
  readonly name: 'position-detail'
  readonly params: { readonly positionId: string }
}
