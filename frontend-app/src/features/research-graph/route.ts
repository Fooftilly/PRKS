import type { PrksRouteInstanceBase } from '../../route-surface/route-instance'

export interface ResearchGraphRouteInstance extends PrksRouteInstanceBase {
  readonly name: 'research-graph'
  readonly params: { readonly focus?: string }
}
