/**
 * Minimum identity the shared Vue surface records for one mounted owner.
 *
 * The legacy PRKS router remains canonical (`prksParseRoute`, canonical hashes,
 * TabContext, Main/Secondary ownership, workspace tab routing). This is the
 * typed bridge into Vue, not a second parser. Feature modules narrow `name`
 * and add params (Progress does). A future discriminated union should import
 * those feature types. Do not copy the legacy route table into TypeScript.
 */
export interface PrksRouteInstanceBase {
  readonly name: string
  readonly canonicalHash: string
  readonly ownsMainShell: boolean
  readonly generation: number
}

/** Present input. Generation is filled from this owner when the caller omits it. */
export type RouteInstanceInput = Omit<PrksRouteInstanceBase, 'generation'> & {
  readonly generation?: number
}
