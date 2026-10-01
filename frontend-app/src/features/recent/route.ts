import type { PrksRouteInstanceBase } from '../../route-surface/route-instance'

export interface RecentRouteInstance extends PrksRouteInstanceBase {
  readonly name: 'recent'
  readonly params: Record<string, never>
}
