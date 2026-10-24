import type { PrksRouteInstanceBase } from '../../route-surface/route-instance'

export interface TypesIndexRouteInstance extends PrksRouteInstanceBase {
  readonly name: 'types'
  readonly params: Record<string, never>
}

export interface TypeDetailRouteInstance extends PrksRouteInstanceBase {
  readonly name: 'type-detail'
  readonly params: { readonly docType: string }
}
