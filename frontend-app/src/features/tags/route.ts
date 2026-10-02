import type { PrksRouteInstanceBase } from '../../route-surface/route-instance'

export interface TagsRouteInstance extends PrksRouteInstanceBase {
  readonly name: 'tags'
  readonly params: Record<string, never>
}
