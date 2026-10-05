import type { PrksRouteInstanceBase } from '../../route-surface/route-instance'

/** One mounted Work detail (`#/works/:workId`). PDF runtime stays on the owner registry. */
export interface WorkDetailRouteInstance extends PrksRouteInstanceBase {
  readonly name: 'work'
  readonly params: { readonly workId: string }
}
