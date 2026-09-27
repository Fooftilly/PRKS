import { MAX_SECONDARY_LEAVES } from './constants'
import { findTab } from './derive'
import { collectLeafTabIds, containsTab, validateTree } from './tree'
import type { WorkspaceState } from './types'

export interface WorkspaceValidation {
  ok: boolean
  errors: string[]
}

function push(errors: string[], message: string) {
  errors.push(message)
}

/**
 * Structural invariants of one canonical workspace. Does not inspect DOM,
 * TabContexts, or responsive presentation. A stacked workspace may still hold
 * a Secondary tree (Hide split). Tiled mode requires that tree.
 */
export function validateWorkspace(state: WorkspaceState): WorkspaceValidation {
  const errors: string[] = []
  if (!state || typeof state !== 'object') {
    return { ok: false, errors: ['workspace state missing'] }
  }
  if (state.mode !== 'stacked' && state.mode !== 'tiled') {
    push(errors, 'invalid mode ' + String(state.mode))
  }
  if (typeof state.mainSplitRatio !== 'number' || !Number.isFinite(state.mainSplitRatio) || state.mainSplitRatio < 0 || state.mainSplitRatio > 1) {
    push(errors, 'invalid mainSplitRatio ' + String(state.mainSplitRatio))
  }

  const seenTabs: Record<string, true> = Object.create(null) as Record<string, true>
  for (let i = 0; i < state.tabs.length; i++) {
    const tab = state.tabs[i]
    if (!tab || !tab.id) {
      push(errors, 'tab missing id at ' + i)
      continue
    }
    if (seenTabs[tab.id]) push(errors, 'duplicate tab id ' + tab.id)
    seenTabs[tab.id] = true
  }

  if (state.mainTabId != null && !findTab(state, state.mainTabId)) {
    push(errors, 'mainTabId missing ' + state.mainTabId)
  }
  if (state.focusedTabId != null && !findTab(state, state.focusedTabId)) {
    push(errors, 'focusedTabId missing ' + state.focusedTabId)
  }
  if (state.mainTabId != null && state.focusedTabId == null) {
    push(errors, 'focusedTabId missing while main is set')
  }
  if (state.mode === 'stacked' && state.mainTabId != null && state.focusedTabId !== state.mainTabId) {
    push(errors, 'stacked focus is not Main')
  }
  if (state.mode === 'tiled' && !state.secondaryTree) {
    push(errors, 'tiled mode without secondaryTree')
  }
  if (!state.secondaryTree && state.mode !== 'stacked') {
    push(errors, 'missing secondaryTree requires stacked mode')
  }

  if (state.secondaryTree) {
    const treeCheck = validateTree(state.secondaryTree)
    for (let i = 0; i < treeCheck.errors.length; i++) push(errors, treeCheck.errors[i])
    const leaves = collectLeafTabIds(state.secondaryTree)
    if (leaves.length > MAX_SECONDARY_LEAVES) {
      push(errors, 'pane cap exceeded ' + leaves.length)
    }
    for (let i = 0; i < leaves.length; i++) {
      const id = leaves[i]
      if (!findTab(state, id)) push(errors, 'secondary leaf missing tab ' + id)
      if (id === state.mainTabId) push(errors, 'Main is also a Secondary leaf ' + id)
    }
  }

  if (
    state.mode === 'tiled' &&
    state.focusedTabId != null &&
    state.focusedTabId !== state.mainTabId &&
    !containsTab(state.secondaryTree, state.focusedTabId)
  ) {
    push(errors, 'focus is neither Main nor a Secondary leaf')
  }

  return { ok: errors.length === 0, errors }
}
