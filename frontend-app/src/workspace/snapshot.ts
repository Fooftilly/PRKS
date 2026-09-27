import type { WorkspacePaneNode, WorkspaceState, WorkspaceTab } from './types'
import { asTabId } from './types'

function copyTree(node: WorkspacePaneNode | null): WorkspacePaneNode | null {
  if (!node) return null
  if (node.type === 'leaf') {
    return node.tabId ? { type: 'leaf', tabId: asTabId(String(node.tabId)) } : null
  }
  if (node.type === 'split') {
    const first = copyTree(node.first)
    const second = copyTree(node.second)
    if (!first || !second) return null
    return {
      type: 'split',
      id: node.id,
      axis: node.axis,
      ratio: node.ratio,
      first,
      second,
    }
  }
  return null
}

function copyTab(tab: WorkspaceTab): WorkspaceTab {
  return {
    id: tab.id,
    route: tab.route,
    title: tab.title,
    icon: tab.icon,
    history: tab.history.slice(),
    historyIndex: tab.historyIndex,
  }
}

/**
 * External `prksWorkspaceSnapshot()` body, without ephemeral `titleRouteGen`.
 * The coordinator attaches that field from the live tab when it publishes the
 * snapshot. The copy does not alias live history arrays or tree nodes.
 */
export function workspaceSnapshot(state: WorkspaceState): WorkspaceState {
  return {
    version: state.version,
    mode: state.mode,
    mainTabId: state.mainTabId,
    focusedTabId: state.focusedTabId,
    secondaryTree: copyTree(state.secondaryTree),
    tabs: state.tabs.map(copyTab),
    mainSplitRatio: state.mainSplitRatio,
  }
}
