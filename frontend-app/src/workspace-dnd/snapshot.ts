/**
 * Build a WorkspaceDndSnapshot from a shell projection (#256).
 * Pure derivation except optional coordinator pane-cap probe.
 */
import { MAX_SECONDARY_LEAVES } from '../workspace/constants'
import { collectLeafTabIds } from '../workspace-shell/walk'
import type { WorkspaceProjection } from '../workspace-shell/types'
import type { WorkspaceDndSnapshot } from './adapter'

export function snapshotFromProjection(
  projection: WorkspaceProjection | null | undefined,
): WorkspaceDndSnapshot | null {
  if (!projection) return null
  const secondaryLeafTabIds = collectLeafTabIds(projection.state.secondaryTree)
  const canAdd =
    typeof window.prksWorkspaceCanAddSecondaryLeaf === 'function'
      ? window.prksWorkspaceCanAddSecondaryLeaf()
      : secondaryLeafTabIds.length < MAX_SECONDARY_LEAVES
  return {
    mainTabId: projection.state.mainTabId,
    secondaryLeafTabIds,
    hasSecondaryTree: projection.state.secondaryTree != null,
    narrowFallback: projection.narrowFallback,
    canAddSecondaryLeaf: canAdd,
    tabs: projection.state.tabs.map((tab) => ({
      id: tab.id,
      route: tab.route,
      title: tab.title,
      icon: tab.icon,
    })),
  }
}
