import type { PrksRouteInstanceBase } from '../../route-surface/route-instance'
import type { ProgressStatus } from './status'

/**
 * Progress narrowing of the shared route instance.
 * Status parsing stays aligned with the legacy router; this type does not parse hashes.
 */
export interface ProgressRouteInstance extends PrksRouteInstanceBase {
  readonly name: 'progress'
  readonly params: { readonly status: ProgressStatus }
}
