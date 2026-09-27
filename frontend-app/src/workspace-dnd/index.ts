/**
 * Workspace drag-and-drop (#256): Pragmatic adapter + pure drop-intent resolver.
 */
export { computeEdgeZone, computeReorderIndex, EDGE_BAND, EDGE_ZONE_TO_SPLIT } from './geometry'
export type { EdgeZone, RectLike, TabRect } from './geometry'
export {
  resolveDropIntent,
  dropIntentToCommand,
  pickNestedLeafHit,
  type WorkspaceDropIntent,
  type DragSource,
  type DropHit,
} from './drop-intent'
export { commitDropIntent, browserCommitHandlers } from './commit'
export type { WorkspaceDndCommitHandlers } from './commit'
export { createHoverController } from './hover'
export type { HoverController } from './hover'
export {
  bindWorkspaceDnd,
  resolveIntentAtPoint,
  hitFromPointer,
  buildDragPreviewElement,
} from './adapter'
export type {
  WorkspaceDndSnapshot,
  BindWorkspaceDndOptions,
  WorkspaceDndSession,
} from './adapter'
export { KEYBOARD_EQUIVALENT_PATHS, keyboardEquivalentSummary } from './a11y'
export { snapshotFromProjection } from './snapshot'
