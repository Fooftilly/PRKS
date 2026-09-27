/**
 * Pure typed WorkspaceDropIntent resolver (#234).
 *
 * Sensors / hit geometry feed this module; it never mutates WorkspaceState.
 * Confirmed drops map intents to WorkspaceCommand values for the coordinator.
 */
import type { WorkspaceCommand } from '../workspace/commands'
import {
  EDGE_ZONE_TO_SPLIT,
  computeEdgeZone,
  computeReorderIndex,
  type EdgeZone,
  type RectLike,
  type TabRect,
} from './geometry'

export type DragSourceKind = 'tab' | 'pane'

export interface DragSource {
  readonly kind: DragSourceKind
  readonly tabId: string
}

export type WorkspaceDropIntent =
  | { readonly kind: 'tab-reorder'; readonly beforeTabId: string | null; readonly index: number }
  | { readonly kind: 'park' }
  | { readonly kind: 'secondary-empty'; readonly valid: true }
  | {
      readonly kind: 'secondary-edge'
      readonly tabId: string
      readonly axis: 'left-right' | 'top-bottom'
      readonly placement: 'first' | 'second'
      readonly zone: EdgeZone
      readonly valid: boolean
      readonly reason: 'cap' | 'route' | null
    }

export interface DropHitStrip {
  readonly kind: 'strip'
  readonly x: number
  /** Ordered rects of every OTHER tab (source already excluded). */
  readonly otherTabRects: readonly TabRect[]
}

export interface DropHitLeaf {
  readonly kind: 'leaf'
  readonly tabId: string
  readonly rect: RectLike
  readonly x: number
  readonly y: number
}

export interface DropHitEmptySecondary {
  readonly kind: 'empty-secondary'
  readonly canvasRect: RectLike
  readonly x: number
  readonly y: number
}

export type DropHit = DropHitStrip | DropHitLeaf | DropHitEmptySecondary | null

export interface ResolveDropIntentInput {
  readonly source: DragSource
  readonly mainTabId: string | null
  /** Visible Secondary leaf tab ids (depth-first). */
  readonly secondaryLeafTabIds: readonly string[]
  readonly hasSecondaryTree: boolean
  readonly narrowFallback: boolean
  readonly canAddSecondaryLeaf: boolean
  readonly sourceRouteSupportsTile: boolean
  readonly hit: DropHit
}

export function resolveDropIntent(input: ResolveDropIntentInput): WorkspaceDropIntent | null {
  const { source, hit } = input
  if (!hit) return null

  if (hit.kind === 'strip') {
    if (source.kind === 'pane') return { kind: 'park' }
    const idx = computeReorderIndex(hit.otherTabRects, hit.x)
    return {
      kind: 'tab-reorder',
      beforeTabId: hit.otherTabRects[idx] ? hit.otherTabRects[idx].id : null,
      index: idx,
    }
  }

  if (input.narrowFallback) return null
  if (source.tabId === input.mainTabId) return null

  if (hit.kind === 'leaf') {
    if (hit.tabId === source.tabId) return null
    const zone = computeEdgeZone(hit.rect, hit.x, hit.y)
    if (!zone) return null
    const map = EDGE_ZONE_TO_SPLIT[zone]
    const isMove = input.secondaryLeafTabIds.includes(source.tabId)
    const capped = !isMove && !input.canAddSecondaryLeaf
    const ineligibleRoute = !isMove && !input.sourceRouteSupportsTile
    return {
      kind: 'secondary-edge',
      tabId: hit.tabId,
      axis: map.axis,
      placement: map.placement,
      zone,
      valid: !capped && !ineligibleRoute,
      reason: capped ? 'cap' : ineligibleRoute ? 'route' : null,
    }
  }

  if (hit.kind === 'empty-secondary') {
    if (input.hasSecondaryTree) return null
    if (source.kind !== 'tab' || !input.sourceRouteSupportsTile) return null
    const rect = hit.canvasRect
    const zoneStart = rect.left + rect.width * 0.6
    const right = rect.left + rect.width
    const bottom = rect.top + rect.height
    if (hit.x >= zoneStart && hit.x <= right && hit.y >= rect.top && hit.y <= bottom) {
      return { kind: 'secondary-empty', valid: true }
    }
    return null
  }

  return null
}

/** Map a confirmed drop intent to a WorkspaceCommand. Hover never calls this. */
export function dropIntentToCommand(
  source: DragSource,
  intent: WorkspaceDropIntent | null,
  secondaryLeafTabIds: readonly string[],
): WorkspaceCommand | null {
  if (!intent) return null
  if (intent.kind === 'tab-reorder') {
    return { type: 'reorder-tab', tabId: source.tabId, beforeTabId: intent.beforeTabId }
  }
  if (intent.kind === 'park') {
    if (source.kind !== 'pane') return null
    return { type: 'hide-leaf', tabId: source.tabId }
  }
  if (intent.kind === 'secondary-empty') {
    // Coordinator path uses prksWorkspaceTileTab; no discrete WorkspaceCommand today.
    return null
  }
  if (intent.kind === 'secondary-edge') {
    if (!intent.valid) return null
    const isMove = secondaryLeafTabIds.includes(source.tabId)
    if (isMove) {
      return {
        type: 'move-pane',
        sourceTabId: source.tabId,
        targetTabId: intent.tabId,
        axis: intent.axis,
        placement: intent.placement,
      }
    }
    return {
      type: 'split-leaf',
      targetTabId: intent.tabId,
      newTabId: source.tabId,
      axis: intent.axis,
      placement: intent.placement,
    }
  }
  return null
}

/** Nested target determinism: first matching leaf in caller order wins; center is null. */
export function pickNestedLeafHit(
  leaves: readonly { readonly tabId: string; readonly rect: RectLike }[],
  x: number,
  y: number,
  sourceTabId: string,
): DropHitLeaf | null {
  for (const leaf of leaves) {
    if (leaf.tabId === sourceTabId) continue
    const r = leaf.rect
    if (x < r.left || x > r.left + r.width || y < r.top || y > r.top + r.height) continue
    return { kind: 'leaf', tabId: leaf.tabId, rect: r, x, y }
  }
  return null
}
