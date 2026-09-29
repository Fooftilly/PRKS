import type { PrksRouteInstanceBase } from '../../route-surface/route-instance'

export interface PersonGroupsIndexRouteInstance extends PrksRouteInstanceBase {
  readonly name: 'people-groups'
  readonly params: Record<string, never>
}

export interface PersonGroupDetailRouteInstance extends PrksRouteInstanceBase {
  readonly name: 'person-group-detail'
  readonly params: { readonly groupId: string }
}
