import type { PrksRouteName } from '../routing/route-model'

/**
 * Fields every mounted route instance shares.
 *
 * `name` is a registry name from the typed route model (`../routing/route-model`),
 * which owns the hash parser, canonical hashes, and the route registry.
 * TabContext, Main/Secondary ownership, and workspace tab routing stay with
 * the classic runtime. Feature modules narrow `name` and add `params`.
 * `PrksRouteInstance` in `./routes` is the discriminated union of those
 * features.
 */
export interface PrksRouteInstanceBase {
  readonly name: PrksRouteName
  readonly canonicalHash: string
  readonly ownsMainShell: boolean
  readonly generation: number
}
