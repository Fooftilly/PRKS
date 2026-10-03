/**
 * Fields every mounted route instance shares.
 *
 * The legacy PRKS router remains canonical (`prksParseRoute`, canonical hashes,
 * TabContext, Main/Secondary ownership, workspace tab routing). Feature modules
 * narrow `name` and add `params`. `PrksRouteInstance` in `./routes` is the
 * discriminated union of those features. Do not copy the legacy route table
 * into TypeScript.
 */
export interface PrksRouteInstanceBase {
  readonly name: string
  readonly canonicalHash: string
  readonly ownsMainShell: boolean
  readonly generation: number
}
