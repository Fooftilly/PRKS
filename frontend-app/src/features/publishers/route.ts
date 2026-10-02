import type { PrksRouteInstanceBase } from '../../route-surface/route-instance'

export interface PublishersRouteInstance extends PrksRouteInstanceBase {
  readonly name: 'publishers'
  readonly params: Record<string, never>
}
