import type { PrksRouteInstanceBase } from '../../route-surface/route-instance'

export interface SearchRouteInstance extends PrksRouteInstanceBase {
  readonly name: 'search'
  readonly params: { readonly q: string; readonly tag: string; readonly author: string; readonly publisher: string; readonly any: boolean }
}
