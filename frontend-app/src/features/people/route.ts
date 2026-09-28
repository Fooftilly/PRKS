import type { PrksRouteInstanceBase } from '../../route-surface/route-instance'

export interface PeopleIndexRouteInstance extends PrksRouteInstanceBase {
  readonly name: 'people'
  readonly params: { readonly role?: string }
}

export interface PersonDetailRouteInstance extends PrksRouteInstanceBase {
  readonly name: 'person'
  readonly params: { readonly personId: string }
}
