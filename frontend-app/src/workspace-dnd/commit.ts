/**
 * Confirmed-drop bridge for workspace DnD (#256).
 * Hover never calls these. Only a completed drag session may invoke commit.
 */
import type { DragSource, WorkspaceDropIntent } from './drop-intent'
import { dropIntentToCommand } from './drop-intent'
import type { WorkspaceCommand } from '../workspace/commands'

export interface WorkspaceDndCommitHandlers {
  reorderTab(tabId: string, beforeTabId: string | null): boolean | void
  hideLeaf(tabId: string): boolean | Promise<boolean> | void
  tileTab(tabId: string): boolean | Promise<boolean> | void
  movePane(
    sourceTabId: string,
    targetTabId: string,
    axis: string,
    placement: string,
  ): boolean | void
  splitLeaf(
    targetTabId: string,
    axis: string,
    options: { tabId: string; placement: string },
  ): boolean | Promise<boolean> | void
  /** Optional typed command sink for pure-path tests. */
  onCommand?(command: WorkspaceCommand): void
}

function asOk(result: boolean | void | undefined): boolean {
  return result !== false
}

export async function commitDropIntent(
  source: DragSource,
  intent: WorkspaceDropIntent | null,
  secondaryLeafTabIds: readonly string[],
  handlers: WorkspaceDndCommitHandlers,
): Promise<{ ok: boolean; command: WorkspaceCommand | null }> {
  if (!intent) return { ok: false, command: null }

  if (intent.kind === 'secondary-empty') {
    if (source.kind !== 'tab') return { ok: false, command: null }
    const ok = asOk(await Promise.resolve(handlers.tileTab(source.tabId)))
    return { ok, command: null }
  }

  const command = dropIntentToCommand(source, intent, secondaryLeafTabIds)
  if (!command) return { ok: false, command: null }
  handlers.onCommand?.(command)

  if (command.type === 'reorder-tab') {
    return { ok: asOk(handlers.reorderTab(command.tabId, command.beforeTabId)), command }
  }
  if (command.type === 'hide-leaf') {
    return { ok: asOk(await Promise.resolve(handlers.hideLeaf(command.tabId))), command }
  }
  if (command.type === 'move-pane') {
    return {
      ok: asOk(
        handlers.movePane(
          command.sourceTabId,
          command.targetTabId,
          command.axis,
          command.placement,
        ),
      ),
      command,
    }
  }
  if (command.type === 'split-leaf') {
    return {
      ok: asOk(
        await Promise.resolve(
          handlers.splitLeaf(command.targetTabId, command.axis, {
            tabId: command.newTabId,
            placement: command.placement,
          }),
        ),
      ),
      command,
    }
  }
  return { ok: false, command }
}

type CoordinatorWindow = Window & {
  prksWorkspaceReorderTab?: (tabId: string, beforeTabId: string | null) => boolean
  prksWorkspaceHideLeaf?: (tabId: string) => boolean | Promise<boolean>
  prksWorkspaceTileTab?: (tabId: string) => boolean | Promise<boolean> | void
  prksWorkspaceMovePane?: (
    sourceTabId: string,
    targetTabId: string,
    axis: string,
    placement: string,
  ) => boolean
  prksWorkspaceSplitLeaf?: (
    targetTabId: string,
    axis: string,
    options: { tabId: string; placement: string },
  ) => boolean | Promise<boolean>
}

/** Browser handlers that call the same coordinator APIs as production drag. */
export function browserCommitHandlers(): WorkspaceDndCommitHandlers {
  const win = window as CoordinatorWindow
  return {
    reorderTab(tabId, beforeTabId) {
      const fn = win.prksWorkspaceReorderTab
      if (typeof fn !== 'function') return false
      return fn(tabId, beforeTabId) !== false
    },
    hideLeaf(tabId) {
      const fn = win.prksWorkspaceHideLeaf
      if (typeof fn !== 'function') return false
      return fn(tabId)
    },
    tileTab(tabId) {
      const fn = win.prksWorkspaceTileTab
      if (typeof fn !== 'function') return false
      return fn(tabId)
    },
    movePane(sourceTabId, targetTabId, axis, placement) {
      const fn = win.prksWorkspaceMovePane
      if (typeof fn !== 'function') return false
      return fn(sourceTabId, targetTabId, axis, placement) !== false
    },
    splitLeaf(targetTabId, axis, options) {
      const fn = win.prksWorkspaceSplitLeaf
      if (typeof fn !== 'function') return false
      return fn(targetTabId, axis, options)
    },
  }
}
