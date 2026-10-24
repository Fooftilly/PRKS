import type { PrksRouteInstanceBase } from '../../route-surface/route-instance'

export interface ProcessingRouteInstance extends PrksRouteInstanceBase {
  readonly name: 'processing-files'
  readonly params: Record<string, never>
}
