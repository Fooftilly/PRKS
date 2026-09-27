/**
 * #234 Pragmatic Drag and Drop PoC (experimental).
 * Not imported by production main.ts / WorkspaceShell.
 */
export { computeEdgeZone, computeReorderIndex, EDGE_BAND, EDGE_ZONE_TO_SPLIT } from './geometry'
export {
  resolveDropIntent,
  dropIntentToCommand,
  pickNestedLeafHit,
  type WorkspaceDropIntent,
  type DragSource,
  type DropHit,
} from './drop-intent'
export { commitDropIntent, browserCommitHandlers } from './commit'
export { createHoverController } from './hover'
export { bindPocAdapter, isPocEnabled, PRKS_DND_POC_FLAG } from './adapter'
export { KEYBOARD_EQUIVALENT_PATHS, keyboardEquivalentSummary } from './a11y'
