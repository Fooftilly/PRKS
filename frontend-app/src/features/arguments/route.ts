import type { PrksRouteInstanceBase } from '../../route-surface/route-instance'
import type { ArgumentKindFilter } from './types'

/** Arguments index narrowing of the shared route instance. */
export interface ArgumentsIndexRouteInstance extends PrksRouteInstanceBase {
  readonly name: 'arguments'
  readonly params: { readonly kind: ArgumentKindFilter }
}

/** Argument detail narrowing of the shared route instance. */
export interface ArgumentDetailRouteInstance extends PrksRouteInstanceBase {
  readonly name: 'argument-detail'
  readonly params: { readonly argumentId: string }
}
