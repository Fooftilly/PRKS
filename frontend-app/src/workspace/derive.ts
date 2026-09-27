import { MAX_SECONDARY_LEAVES } from './constants'
import { collectLeafTabIds, containsTab, leafCount } from './tree'
import type { WorkspacePresentation, WorkspaceState, WorkspaceTab } from './types'

export type LogicalTabRole = 'main' | 'secondary' | 'parked'

export function visualTiled(state: WorkspaceState, presentation: WorkspacePresentation): boolean {
  return state.mode === 'tiled' && !presentation.narrowFallback && !!state.secondaryTree
}

export function findTab(state: WorkspaceState, tabId: string | null | undefined): WorkspaceTab | null {
  if (!tabId) return null
  for (let i = 0; i < state.tabs.length; i++) {
    if (state.tabs[i].id === tabId) return state.tabs[i]
  }
  return null
}

/**
 * Main, a logical Secondary leaf, or a parked tab.
 * A hidden split keeps its leaves as `secondary` even while they are unmounted.
 */
export function logicalTabRole(state: WorkspaceState, tabId: string): LogicalTabRole | null {
  if (!findTab(state, tabId)) return null
  if (state.mainTabId === tabId) return 'main'
  if (containsTab(state.secondaryTree, tabId)) return 'secondary'
  return 'parked'
}

/** On screen. Hidden-split leaves and narrow-fallback leaves are logical tabs, not visible panes. */
export function isTabVisible(
  state: WorkspaceState,
  tabId: string,
  presentation: WorkspacePresentation,
): boolean {
  if (!findTab(state, tabId)) return false
  if (tabId === state.mainTabId) return true
  return visualTiled(state, presentation) && containsTab(state.secondaryTree, tabId)
}

/** Main first, then depth-first Secondary leaves that are actually on screen. */
export function visibleTabIds(state: WorkspaceState, presentation: WorkspacePresentation): string[] {
  const ids: string[] = []
  if (state.mainTabId && findTab(state, state.mainTabId)) ids.push(state.mainTabId)
  if (!visualTiled(state, presentation) || !state.secondaryTree) return ids
  const leaves = collectLeafTabIds(state.secondaryTree)
  for (let i = 0; i < leaves.length; i++) {
    if (leaves[i] !== state.mainTabId) ids.push(leaves[i])
  }
  return ids
}

export function parkedTabIds(state: WorkspaceState): string[] {
  const out: string[] = []
  for (let i = 0; i < state.tabs.length; i++) {
    if (logicalTabRole(state, state.tabs[i].id) === 'parked') out.push(state.tabs[i].id)
  }
  return out
}

/** Secondary leaves that stay in the tree while the split is hidden or the canvas is narrow. */
export function hiddenSecondaryTabIds(state: WorkspaceState, presentation: WorkspacePresentation): string[] {
  if (visualTiled(state, presentation) || !state.secondaryTree) return []
  const leaves = collectLeafTabIds(state.secondaryTree)
  const out: string[] = []
  for (let i = 0; i < leaves.length; i++) {
    if (leaves[i] !== state.mainTabId) out.push(leaves[i])
  }
  return out
}

export function secondaryLeafCapReached(tree: WorkspaceState['secondaryTree']): boolean {
  return leafCount(tree) >= MAX_SECONDARY_LEAVES
}
