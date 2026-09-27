/**
 * Canonical in-memory workspace state.
 *
 * One model. `workspace-tabs.js` holds the single live object and commits
 * the results of these pure transforms. There is no Pinia store, no second
 * tab list, and no second Secondary tree. A future Vue workspace renderer
 * reads this same state; it does not own another copy.
 *
 * Persistable (#58 later, Workspace Persistence v1 today): tab id, route
 * hash, title, icon, mode, mainTabId, secondary topology (axis, ratio, leaf
 * tab ids), and mainSplitRatio. Persistence drops history, historyIndex,
 * focusedTabId, split-node ids, and everything below.
 *
 * Canonical in memory but not persisted in v1: per-tab history/historyIndex,
 * focusedTabId, and split-node ids (stable per runtime for DOM reuse; fresh
 * ids are assigned on restore).
 *
 * Ephemeral runtime — never put these in WorkspaceState or serialize them:
 * TabContexts, warm-park hosts, DOM nodes, route generations (`titleRouteGen`),
 * Vue trees, AbortControllers, PDF instances, editors, drafts, effective
 * (constrained) ratios, `narrowFallback`, and drag hover/preview (#234).
 * Drag commits only through canonical commands after the pointer drops.
 *
 * Route identity is a canonical hash string. Feature Vue trees stay on the
 * #251 route-surface, one per TabContext. This module does not import Vue.
 */

declare const tabIdBrand: unique symbol
declare const splitIdBrand: unique symbol

/** Logical tab id (`tab-1`, …). Not a split-node id. */
export type TabId = string & { readonly [tabIdBrand]: true }

/** In-memory split-node id (`split-1`, …). Not persisted. Not a tab id. */
export type SplitId = string & { readonly [splitIdBrand]: true }

export type WorkspaceMode = 'stacked' | 'tiled'

export type SplitAxis = 'left-right' | 'top-bottom'

export function asTabId(value: string): TabId {
  return String(value) as TabId
}

export function asSplitId(value: string): SplitId {
  return String(value) as SplitId
}

/**
 * Logical tab. `titleRouteGen` is intentionally absent: it is the ephemeral
 * route generation for a resolved title, reattached only by the snapshot
 * adapter so `prksWorkspaceSnapshot()` keeps its existing external shape.
 */
export interface WorkspaceTab {
  readonly id: TabId
  /** Canonical hash (`#/...`). Not a Vue route record. */
  readonly route: string
  readonly title: string
  readonly icon: string
  readonly history: readonly string[]
  readonly historyIndex: number
}

/** Bare Secondary leaf. The common single-Secondary case is this node, not a split. */
export interface WorkspaceLeaf {
  readonly type: 'leaf'
  readonly tabId: TabId
}

/**
 * Recursive Secondary split. `ratio` is `first / usable size of this split`,
 * never the root Main/Secondary ratio (`mainSplitRatio`).
 */
export interface WorkspaceSplit {
  readonly type: 'split'
  readonly id: SplitId
  readonly axis: SplitAxis
  readonly ratio: number
  readonly first: WorkspacePaneNode
  readonly second: WorkspacePaneNode
}

export type WorkspacePaneNode = WorkspaceLeaf | WorkspaceSplit

export interface WorkspaceState {
  readonly version: number
  readonly mode: WorkspaceMode
  readonly mainTabId: TabId | null
  readonly focusedTabId: TabId | null
  /** `null` (no Secondary), a leaf, or a split. Hidden split keeps the tree while `mode` is `stacked`. */
  readonly secondaryTree: WorkspacePaneNode | null
  readonly tabs: readonly WorkspaceTab[]
  /** Root Main width / usable Main-Secondary width. Default 0.58. Not a nested split ratio. */
  readonly mainSplitRatio: number
}

/**
 * Not stored. Passed into pure decisions that depend on responsive presentation.
 * `narrowFallback` is never persisted and never written by drag preview.
 */
export interface WorkspacePresentation {
  readonly narrowFallback: boolean
}

/** Documented non-model. Not a value the workspace store keeps. */
export interface WorkspaceEphemeralRuntime {
  readonly narrowFallback: boolean
  readonly titleRouteGenByTabId: Readonly<Record<string, number | null>>
}
