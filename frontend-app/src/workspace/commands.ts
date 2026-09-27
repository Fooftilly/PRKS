/**
 * Command boundary. A command names a pure transition plus the preflight and
 * effects the coordinator must run around it. This module does not perform
 * those effects: no leave prompts, no history writes, no TabContext calls.
 *
 * Drag hover and preview are not commands. #234 may later emit `reorder-tab`
 * or `move-pane` on drop only.
 */
import { findTab, visualTiled } from './derive'
import { collectLeafTabIds, containsTab, type SplitIdFactory } from './tree'
import {
  planActivate,
  planCloseTab,
  planFocus,
  planHideLeaf,
  planMainSplitRatio,
  planMakeMain,
  planMovePane,
  planNestedSplitRatio,
  planReorder,
  planSetMode,
  planSplitLeaf,
} from './transitions'
import type { WorkspacePresentation, WorkspaceState, WorkspaceTab } from './types'

export type WorkspacePreflight =
  | { readonly type: 'none' }
  | { readonly type: 'leave-tab'; readonly tabId: string; readonly nextHash: string }
  | { readonly type: 'leave-tabs'; readonly tabIds: readonly string[]; readonly nextHash: string }
  | {
      readonly type: 'main-promotion'
      readonly oldMainId: string
      readonly requiresLeave: boolean
      readonly nextHash: string
    }

export type WorkspaceEffect =
  | { readonly type: 'paint' }
  | { readonly type: 'paint-focus' }
  | { readonly type: 'paint-and-restore'; readonly tabId: string }
  | { readonly type: 'refresh-focused-panel' }
  | { readonly type: 'cold-park'; readonly tabId: string }
  | { readonly type: 'warm-park'; readonly tabId: string }
  | { readonly type: 'commit-main-url'; readonly tabId: string; readonly history: 'replace' | 'push' }
  | { readonly type: 'publish-shell'; readonly tabId: string }
  | { readonly type: 'destroy-context'; readonly tabId: string }

export interface CommandContext extends WorkspacePresentation {
  readonly homeHash: string
  readonly oldMainSupportsTile?: boolean
  /** Coordinator-owned: the tab currently has a mounted TabContext. */
  readonly mounted?: boolean
  readonly mountedTabIds?: readonly string[]
  readonly fromPopstate?: boolean
  readonly nextSplitId?: SplitIdFactory
}

export type WorkspaceCommand =
  | { readonly type: 'focus'; readonly tabId: string }
  | { readonly type: 'reorder-tab'; readonly tabId: string; readonly beforeTabId: string | null }
  | { readonly type: 'make-main'; readonly tabId: string }
  | { readonly type: 'hide-leaf'; readonly tabId: string }
  | {
      readonly type: 'move-pane'
      readonly sourceTabId: string
      readonly targetTabId: string
      readonly axis: string
      readonly placement: string
    }
  | { readonly type: 'set-main-split-ratio'; readonly ratio: number }
  | { readonly type: 'set-nested-split-ratio'; readonly splitId: string; readonly ratio: number }
  | { readonly type: 'set-mode'; readonly mode: string }
  | { readonly type: 'close-tab'; readonly tabId: string; readonly homeTab: WorkspaceTab | null }
  | { readonly type: 'activate-tab'; readonly tabId: string }
  | {
      readonly type: 'split-leaf'
      readonly targetTabId: string
      readonly newTabId: string
      readonly axis: string
      readonly placement: string
    }

export interface CommandResult {
  readonly ok: boolean
  readonly changed: boolean
  readonly state: WorkspaceState
  readonly preflight: WorkspacePreflight
  readonly effects: readonly WorkspaceEffect[]
}

function none(state: WorkspaceState, ok = false): CommandResult {
  return { ok, changed: false, state, preflight: { type: 'none' }, effects: [] }
}

export function preflightHideLeaf(
  state: WorkspaceState,
  tabId: string,
  context: CommandContext,
): WorkspacePreflight {
  if (!containsTab(state.secondaryTree, tabId)) return { type: 'none' }
  if (visualTiled(state, context) && context.mounted) {
    return { type: 'leave-tab', tabId, nextHash: context.homeHash }
  }
  return { type: 'none' }
}

export function preflightMakeMain(state: WorkspaceState, tabId: string, context: CommandContext): WorkspacePreflight {
  const oldMain = findTab(state, state.mainTabId)
  if (!oldMain || oldMain.id === tabId) return { type: 'none' }
  const supports = !!context.oldMainSupportsTile
  return {
    type: 'main-promotion',
    oldMainId: oldMain.id,
    requiresLeave: !supports,
    nextHash: oldMain.route,
  }
}

function closeSuccessorRoute(state: WorkspaceState, tabId: string, homeHash: string): string {
  let idx = -1
  for (let i = 0; i < state.tabs.length; i++) {
    if (state.tabs[i].id === tabId) {
      idx = i
      break
    }
  }
  if (idx < 0) return homeHash
  const leaves = collectLeafTabIds(state.secondaryTree)
  const secId = leaves.length ? leaves[0] : null
  let successor = secId ? findTab(state, secId) : null
  if (!successor) successor = state.tabs[idx + 1] || state.tabs[idx - 1] || null
  if (successor && successor.id === tabId) successor = null
  return successor ? successor.route : homeHash
}

export function preflightCloseTab(state: WorkspaceState, tabId: string, context: CommandContext): WorkspacePreflight {
  const tab = findTab(state, tabId)
  if (!tab) return { type: 'none' }
  if (tab.id !== state.mainTabId) {
    if (containsTab(state.secondaryTree, tab.id) && visualTiled(state, context)) {
      return { type: 'leave-tab', tabId: tab.id, nextHash: context.homeHash }
    }
    return { type: 'none' }
  }
  return { type: 'leave-tab', tabId: tab.id, nextHash: closeSuccessorRoute(state, tab.id, context.homeHash) }
}

export function preflightSetMode(state: WorkspaceState, mode: string, context: CommandContext): WorkspacePreflight {
  if (mode !== 'stacked') return { type: 'none' }
  if (state.mode === 'stacked' && !visualTiled(state, context)) return { type: 'none' }
  const ids = visualTiled(state, context) ? (context.mountedTabIds || []) : []
  if (!ids.length) return { type: 'none' }
  return { type: 'leave-tabs', tabIds: ids, nextHash: context.homeHash }
}

function factory(context: CommandContext): SplitIdFactory {
  return context.nextSplitId || (() => {
    throw new Error('workspace command needs nextSplitId')
  })
}

/**
 * Decide the next canonical state. The coordinator runs `preflight` first
 * (a rejected leave commits nothing) and then the `effects` after commit.
 */
export function applyWorkspaceCommand(
  state: WorkspaceState,
  command: WorkspaceCommand,
  context: CommandContext,
): CommandResult {
  switch (command.type) {
    case 'focus': {
      const plan = planFocus(state, command.tabId, context)
      if (!plan.ok) return none(state)
      if (!plan.changed) return { ok: true, changed: false, state: plan.state, preflight: { type: 'none' }, effects: [] }
      return {
        ok: true,
        changed: true,
        state: plan.state,
        preflight: { type: 'none' },
        effects: [
          { type: 'paint-focus' },
          { type: 'refresh-focused-panel' },
        ],
      }
    }
    case 'reorder-tab': {
      const plan = planReorder(state, command.tabId, command.beforeTabId)
      if (!plan.ok) return none(state)
      return {
        ok: true,
        changed: true,
        state: plan.state,
        preflight: { type: 'none' },
        effects: [{ type: 'paint' }],
      }
    }
    case 'make-main': {
      const preflight = preflightMakeMain(state, command.tabId, context)
      const plan = planMakeMain(state, command.tabId, !!context.oldMainSupportsTile)
      if (!plan.ok) return none(state)
      const effects: WorkspaceEffect[] = []
      if (plan.coldParkTabId) effects.push({ type: 'cold-park', tabId: plan.coldParkTabId })
      const focusId = plan.state.focusedTabId || command.tabId
      effects.push({ type: 'commit-main-url', tabId: focusId, history: 'replace' })
      effects.push({ type: 'paint-and-restore', tabId: focusId })
      effects.push({ type: 'publish-shell', tabId: focusId })
      effects.push({ type: 'refresh-focused-panel' })
      return { ok: true, changed: plan.changed, state: plan.state, preflight, effects }
    }
    case 'hide-leaf': {
      const preflight = preflightHideLeaf(state, command.tabId, context)
      const plan = planHideLeaf(state, command.tabId)
      if (!plan.ok) return none(state)
      const effects: WorkspaceEffect[] = []
      if (context.mounted && visualTiled(state, context)) effects.push({ type: 'cold-park', tabId: command.tabId })
      const focusId = plan.state.focusedTabId || ''
      effects.push({ type: 'paint-and-restore', tabId: focusId })
      effects.push({ type: 'refresh-focused-panel' })
      return { ok: true, changed: true, state: plan.state, preflight, effects }
    }
    case 'move-pane': {
      const plan = planMovePane(
        state,
        command.sourceTabId,
        command.targetTabId,
        command.axis,
        command.placement,
        context,
        factory(context),
      )
      if (!plan.ok) return none(state)
      return {
        ok: true,
        changed: true,
        state: plan.state,
        preflight: { type: 'none' },
        effects: [{ type: 'paint' }],
      }
    }
    case 'set-main-split-ratio': {
      const plan = planMainSplitRatio(state, command.ratio)
      return {
        ok: true,
        changed: plan.state !== state,
        state: plan.state,
        preflight: { type: 'none' },
        effects: [{ type: 'paint' }],
      }
    }
    case 'set-nested-split-ratio': {
      const plan = planNestedSplitRatio(state, command.splitId, command.ratio)
      if (!plan.ok) return none(state)
      return {
        ok: true,
        changed: true,
        state: plan.state,
        preflight: { type: 'none' },
        effects: [{ type: 'paint' }],
      }
    }
    case 'set-mode': {
      const preflight = preflightSetMode(state, command.mode, context)
      const plan = planSetMode(state, command.mode, context)
      if (!plan.ok) return none(state)
      if (plan.kind === 'noop') return { ok: true, changed: false, state, preflight, effects: [] }
      const focusId = plan.state.focusedTabId || ''
      const effects: WorkspaceEffect[] = []
      if (plan.kind === 'hide' && context.mountedTabIds) {
        for (let i = 0; i < context.mountedTabIds.length; i++) {
          effects.push({ type: 'cold-park', tabId: context.mountedTabIds[i] })
        }
      }
      effects.push({ type: 'paint-and-restore', tabId: focusId })
      if (plan.kind === 'hide') effects.push({ type: 'refresh-focused-panel' })
      return { ok: true, changed: true, state: plan.state, preflight, effects }
    }
    case 'close-tab': {
      const preflight = preflightCloseTab(state, command.tabId, context)
      const plan = planCloseTab(state, command.tabId, command.homeTab)
      if (!plan.ok) return none(state)
      if (plan.needsHomeTab) {
        return { ok: true, changed: false, state, preflight, effects: [] }
      }
      const effects: WorkspaceEffect[] = [{ type: 'destroy-context', tabId: command.tabId }]
      const successor = plan.successorId
      if (successor) {
        effects.push({ type: 'commit-main-url', tabId: successor, history: 'replace' })
        effects.push({ type: 'paint-and-restore', tabId: successor })
      } else {
        const focusId = plan.state.focusedTabId || ''
        effects.push({ type: 'paint-and-restore', tabId: focusId })
      }
      return { ok: true, changed: true, state: plan.state, preflight, effects }
    }
    case 'activate-tab': {
      const plan = planActivate(state, command.tabId, context, {
        fromPopstate: !!context.fromPopstate,
        oldMainSupportsTile: !!context.oldMainSupportsTile,
      })
      if (!plan.ok) return none(state)
      if (plan.kind === 'focus-main' || plan.kind === 'focus-secondary') {
        const effects: WorkspaceEffect[] = plan.changed
          ? [{ type: 'paint-focus' }, { type: 'refresh-focused-panel' }]
          : plan.kind === 'focus-main'
            ? [{ type: 'paint' }, { type: 'refresh-focused-panel' }]
            : []
        return { ok: true, changed: plan.changed, state: plan.state, preflight: { type: 'none' }, effects }
      }
      const oldMain = findTab(state, plan.previousMainId)
      const preflight: WorkspacePreflight = oldMain && oldMain.id !== command.tabId
        ? { type: 'leave-tab', tabId: oldMain.id, nextHash: findTab(state, command.tabId)?.route || context.homeHash }
        : { type: 'none' }
      const effects: WorkspaceEffect[] = []
      if (plan.previousMainId && plan.previousMainId !== command.tabId) {
        effects.push({ type: 'warm-park', tabId: plan.previousMainId })
      }
      if (plan.kind === 'promote' && plan.coldParkTabId) effects.push({ type: 'cold-park', tabId: plan.coldParkTabId })
      effects.push({ type: 'commit-main-url', tabId: command.tabId, history: 'replace' })
      effects.push({ type: 'paint' })
      return { ok: true, changed: true, state: plan.state, preflight, effects }
    }
    case 'split-leaf': {
      if (!context.nextSplitId) return none(state)
      const plan = planSplitLeaf(
        state,
        command.targetTabId,
        command.newTabId,
        command.axis,
        command.placement,
        context.nextSplitId,
      )
      if (!plan.ok) return none(state)
      return {
        ok: true,
        changed: true,
        state: plan.state,
        preflight: { type: 'none' },
        effects: [{ type: 'paint' }],
      }
    }
    default:
      return none(state)
  }
}
