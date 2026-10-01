import type { PrksRouteInstanceBase } from '../../route-surface/route-instance'

export interface SavedViewDetailRouteInstance extends PrksRouteInstanceBase {
  readonly name: 'saved-view-detail'
  readonly params: { readonly viewId: string }
}
