import type { ProjectionNode, ProjectionSplit, WorkspaceProjection } from './types'

export function collectLeafTabIds(node: ProjectionNode | null | undefined): string[] {
  if (!node) return []
  if (node.type === 'leaf') return [node.tabId]
  return collectLeafTabIds(node.first).concat(collectLeafTabIds(node.second))
}

export function collectSplitIds(node: ProjectionNode | null | undefined): string[] {
  if (!node || node.type !== 'split') return []
  return [node.id].concat(collectSplitIds(node.first), collectSplitIds(node.second))
}

export function visiblePaneTabIds(projection: WorkspaceProjection | null | undefined): string[] {
  if (!projection?.state.mainTabId) return []
  const ids = [projection.state.mainTabId]
  if (projection.visualTiled) ids.push(...collectLeafTabIds(projection.state.secondaryTree))
  return ids
}

export function isSplit(node: ProjectionNode): node is ProjectionSplit {
  return node.type === 'split'
}
