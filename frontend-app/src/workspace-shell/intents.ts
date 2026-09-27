import type { InjectionKey } from 'vue'
import type { WorkspaceIntents } from './types'

export const workspaceIntentsKey: InjectionKey<WorkspaceIntents> = Symbol('prks-workspace-intents')

/** Coordinator operations. The shell does not mutate the snapshot. */
export function browserWorkspaceIntents(): WorkspaceIntents {
  return {
    activate(tabId) {
      window.prksWorkspaceActivateTab?.(tabId)
    },
    close(tabId) {
      window.prksWorkspaceCloseTab?.(tabId)
    },
    focus(tabId) {
      window.prksWorkspaceFocusTab?.(tabId)
    },
    tile(tabId) {
      window.prksWorkspaceTileTab?.(tabId)
    },
    openTabMenu(tabId, event, anchor) {
      window.prksWorkspaceOpenTabMenu?.(tabId, event, anchor)
    },
    setMainRatio(ratio) {
      window.prksWorkspaceSetMainSplitRatio?.(ratio)
    },
    setNestedRatio(splitId, ratio) {
      window.prksWorkspaceSetNestedSplitRatio?.(splitId, ratio)
    },
  }
}
