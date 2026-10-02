import type { PrksRouteInstanceBase } from '../../route-surface/route-instance'

export interface SavedViewsIndexRouteInstance extends PrksRouteInstanceBase {
  readonly name: 'saved-views'
  readonly params: Record<string, never>
}

export interface SavedViewDetailRouteInstance extends PrksRouteInstanceBase {
  readonly name: 'saved-view-detail'
  readonly params: { readonly viewId: string }
}
