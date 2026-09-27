/**
 * Pure workspace transitions. Each function returns the next canonical state
 * and does not touch DOM, history, TabContext, or its inputs.
 *
 * Leave/dirty preflight, URL updates, paint, and mount/park/destroy stay in
 * the coordinator. Tile eligibility is an input (`oldMainSupportsTile`), not
 * a route parse.
 */
import { DEFAULT_MAIN_SPLIT_RATIO } from './constants'
import { findTab, isTabVisible, secondaryLeafCapReached, visualTiled } from './derive'
import {
  collectLeafTabIds,
  containsTab,
  findNodeById,
  findSiblingLeafTabId,
  moveLeafRelativeToTarget,
  normalizeTree,
  removeLeaf,
  replaceTabId,
  setSplitRatio,
  splitLeaf,
  validateTree,
  type SplitIdFactory,
} from './tree'
import { asTabId, type WorkspaceMode, type WorkspacePaneNode, type WorkspacePresentation, type WorkspaceState, type WorkspaceTab } from './types'

export interface StatePatch {
  mode?: WorkspaceMode
  mainTabId?: WorkspaceState['mainTabId']
  focusedTabId?: WorkspaceState['focusedTabId']
  secondaryTree?: WorkspacePaneNode | null
  tabs?: readonly WorkspaceTab[]
  mainSplitRatio?: number
}

export function patchState(state: WorkspaceState, patch: StatePatch): WorkspaceState {
  return {
    version: state.version,
    mode: patch.mode !== undefined ? patch.mode : state.mode,
    mainTabId: patch.mainTabId !== undefined ? patch.mainTabId : state.mainTabId,
    focusedTabId: patch.focusedTabId !== undefined ? patch.focusedTabId : state.focusedTabId,
    secondaryTree: patch.secondaryTree !== undefined ? patch.secondaryTree : state.secondaryTree,
    tabs: patch.tabs !== undefined ? patch.tabs : state.tabs,
    mainSplitRatio: patch.mainSplitRatio !== undefined ? patch.mainSplitRatio : state.mainSplitRatio,
  }
}

function withoutTab(tabs: readonly WorkspaceTab[], tabId: string): readonly WorkspaceTab[] {
  const next: WorkspaceTab[] = []
  for (let i = 0; i < tabs.length; i++) {
    if (tabs[i].id !== tabId) next.push(tabs[i])
  }
  return next
}

export interface MakeMainPlan {
  ok: boolean
  changed: boolean
  state: WorkspaceState
  /** Set when the old Main is not demoted into the tree and must be cold-parked. */
  coldParkTabId: string | null
}

/** In-place role swap. Does not paint, touch the URL, or run leave checks. */
export function planMakeMain(state: WorkspaceState, tabId: string, oldMainSupportsTile: boolean): MakeMainPlan {
  const tab = findTab(state, tabId)
  if (!tab) return { ok: false, changed: false, state, coldParkTabId: null }
  if (tab.id === state.mainTabId) {
    if (state.focusedTabId === tab.id) return { ok: true, changed: false, state, coldParkTabId: null }
    return {
      ok: true,
      changed: true,
      state: patchState(state, { focusedTabId: tab.id }),
      coldParkTabId: null,
    }
  }
  if (!containsTab(state.secondaryTree, tab.id)) return { ok: false, changed: false, state, coldParkTabId: null }
  const oldMain = findTab(state, state.mainTabId)
  let secondaryTree = state.secondaryTree
  let coldParkTabId: string | null = null
  if (oldMain && oldMainSupportsTile) {
    secondaryTree = replaceTabId(state.secondaryTree, tab.id, oldMain.id)
  } else {
    secondaryTree = normalizeTree(removeLeaf(state.secondaryTree, tab.id))
    if (oldMain && oldMain.id !== tab.id) coldParkTabId = oldMain.id
  }
  const mode: WorkspaceMode = secondaryTree ? state.mode : 'stacked'
  return {
    ok: true,
    changed: true,
    coldParkTabId,
    state: patchState(state, {
      secondaryTree,
      mainTabId: tab.id,
      focusedTabId: tab.id,
      mode,
    }),
  }
}

export function planHideLeaf(state: WorkspaceState, tabId: string): { ok: boolean; state: WorkspaceState } {
  if (!containsTab(state.secondaryTree, tabId)) return { ok: false, state }
  const sibling = findSiblingLeafTabId(state.secondaryTree, tabId)
  const secondaryTree = normalizeTree(removeLeaf(state.secondaryTree, tabId))
  const mode: WorkspaceMode = secondaryTree ? state.mode : 'stacked'
  let focusedTabId = state.focusedTabId
  if (focusedTabId === tabId) {
    const nextLeaves = collectLeafTabIds(secondaryTree)
    const preferred = sibling && nextLeaves.indexOf(sibling) !== -1 ? sibling : nextLeaves[0] || null
    focusedTabId = (preferred || state.mainTabId) as WorkspaceState['focusedTabId']
  }
  return { ok: true, state: patchState(state, { secondaryTree, mode, focusedTabId }) }
}

export function planFocus(
  state: WorkspaceState,
  tabId: string,
  presentation: WorkspacePresentation,
): { ok: boolean; changed: boolean; state: WorkspaceState } {
  if (!findTab(state, tabId)) return { ok: false, changed: false, state }
  if (!isTabVisible(state, tabId, presentation)) return { ok: false, changed: false, state }
  if (state.mode === 'stacked' && tabId !== state.mainTabId) return { ok: false, changed: false, state }
  if (state.focusedTabId === tabId) return { ok: true, changed: false, state }
  return { ok: true, changed: true, state: patchState(state, { focusedTabId: asTabId(tabId) }) }
}

export function planReorder(
  state: WorkspaceState,
  tabId: string,
  beforeTabId: string | null,
): { ok: boolean; state: WorkspaceState } {
  let idx = -1
  for (let i = 0; i < state.tabs.length; i++) {
    if (state.tabs[i].id === tabId) {
      idx = i
      break
    }
  }
  if (idx < 0 || tabId === beforeTabId) return { ok: false, state }
  const tabs = state.tabs.slice()
  const removed = tabs.splice(idx, 1)
  const tab = removed[0]
  let insertAt = tabs.length
  if (beforeTabId) {
    for (let i = 0; i < tabs.length; i++) {
      if (tabs[i].id === beforeTabId) {
        insertAt = i
        break
      }
    }
  }
  tabs.splice(insertAt, 0, tab)
  return { ok: true, state: patchState(state, { tabs }) }
}

export function planMovePane(
  state: WorkspaceState,
  sourceTabId: string,
  targetTabId: string,
  axis: string,
  placement: string,
  presentation: WorkspacePresentation,
  nextSplitId: SplitIdFactory,
): { ok: boolean; state: WorkspaceState } {
  if (!sourceTabId || !targetTabId || sourceTabId === targetTabId) return { ok: false, state }
  if (sourceTabId === state.mainTabId || targetTabId === state.mainTabId) return { ok: false, state }
  if (!visualTiled(state, presentation)) return { ok: false, state }
  if (!containsTab(state.secondaryTree, sourceTabId) || !containsTab(state.secondaryTree, targetTabId)) {
    return { ok: false, state }
  }
  const useAxis = axis === 'top-bottom' ? 'top-bottom' : 'left-right'
  const usePlacement = placement === 'first' ? 'first' : 'second'
  const secondaryTree = moveLeafRelativeToTarget(
    state.secondaryTree,
    sourceTabId,
    targetTabId,
    { axis: useAxis, placement: usePlacement },
    nextSplitId,
  )
  if (secondaryTree === state.secondaryTree) return { ok: false, state }
  return { ok: true, state: patchState(state, { secondaryTree }) }
}

export function clampMainSplitRatio(value: unknown): number {
  const n = Number(value)
  if (!Number.isFinite(n)) return DEFAULT_MAIN_SPLIT_RATIO
  return Math.max(0, Math.min(1, n))
}

export function planMainSplitRatio(state: WorkspaceState, ratio: unknown): { state: WorkspaceState; ratio: number } {
  const next = clampMainSplitRatio(ratio)
  if (next === state.mainSplitRatio) return { state, ratio: next }
  return { state: patchState(state, { mainSplitRatio: next }), ratio: next }
}

export function planNestedSplitRatio(
  state: WorkspaceState,
  splitId: string,
  ratio: unknown,
): { ok: boolean; state: WorkspaceState; ratio: number | null } {
  if (!findNodeById(state.secondaryTree, splitId)) return { ok: false, state, ratio: null }
  const secondaryTree = setSplitRatio(state.secondaryTree, splitId, ratio)
  const updated = findNodeById(secondaryTree, splitId)
  return {
    ok: true,
    state: patchState(state, { secondaryTree }),
    ratio: updated ? updated.ratio : null,
  }
}

export type SetModePlan =
  | { ok: false; state: WorkspaceState }
  | { ok: true; kind: 'noop'; state: WorkspaceState }
  | { ok: true; kind: 'show'; state: WorkspaceState }
  | { ok: true; kind: 'hide'; state: WorkspaceState; leafIds: readonly string[] }

export function planSetMode(state: WorkspaceState, mode: string, presentation: WorkspacePresentation): SetModePlan {
  if (mode !== 'stacked' && mode !== 'tiled') return { ok: false, state }
  if (mode === 'tiled') {
    if (!state.secondaryTree) return { ok: false, state }
    return {
      ok: true,
      kind: 'show',
      state: patchState(state, { mode: 'tiled', focusedTabId: state.mainTabId }),
    }
  }
  if (state.mode === 'stacked' && !visualTiled(state, presentation)) return { ok: true, kind: 'noop', state }
  return {
    ok: true,
    kind: 'hide',
    leafIds: collectLeafTabIds(state.secondaryTree),
    state: patchState(state, { mode: 'stacked', focusedTabId: state.mainTabId }),
  }
}

export interface CloseTabPlan {
  ok: boolean
  /** True when Main was the last tab. The adapter mints a Home tab and calls again. */
  needsHomeTab: boolean
  state: WorkspaceState
  successorId: string | null
  promotedLeaf: boolean
}

export function planCloseTab(state: WorkspaceState, tabId: string, homeTab: WorkspaceTab | null): CloseTabPlan {
  let idx = -1
  for (let i = 0; i < state.tabs.length; i++) {
    if (state.tabs[i].id === tabId) {
      idx = i
      break
    }
  }
  if (idx < 0) return { ok: false, needsHomeTab: false, state, successorId: null, promotedLeaf: false }
  const closing = state.tabs[idx]
  const closingMain = closing.id === state.mainTabId
  const closingLeaf = containsTab(state.secondaryTree, closing.id)
  if (!closingMain) {
    let secondaryTree = state.secondaryTree
    let mode = state.mode
    let focusedTabId = state.focusedTabId
    if (closingLeaf && state.secondaryTree) {
      const sibling = findSiblingLeafTabId(state.secondaryTree, closing.id)
      secondaryTree = normalizeTree(removeLeaf(state.secondaryTree, closing.id))
      if (!secondaryTree) mode = 'stacked'
      if (focusedTabId === closing.id) {
        const nextLeaves = collectLeafTabIds(secondaryTree)
        const preferred = sibling && nextLeaves.indexOf(sibling) !== -1 ? sibling : nextLeaves[0] || null
        focusedTabId = (preferred || state.mainTabId) as WorkspaceState['focusedTabId']
      }
    }
    if (mode === 'stacked') focusedTabId = state.mainTabId
    return {
      ok: true,
      needsHomeTab: false,
      successorId: null,
      promotedLeaf: false,
      state: patchState(state, {
        tabs: withoutTab(state.tabs, closing.id),
        secondaryTree,
        mode,
        focusedTabId,
      }),
    }
  }

  const treeLeaves = collectLeafTabIds(state.secondaryTree)
  const secId = treeLeaves.length ? treeLeaves[0] : null
  const promotingLeaf = !!secId
  let successor = secId ? findTab(state, secId) : null
  if (!successor) successor = state.tabs[idx + 1] || state.tabs[idx - 1] || null
  if (successor && successor.id === closing.id) successor = null
  if (!successor) {
    if (!homeTab) return { ok: false, needsHomeTab: true, state, successorId: null, promotedLeaf: false }
    return {
      ok: true,
      needsHomeTab: false,
      successorId: homeTab.id,
      promotedLeaf: false,
      state: patchState(state, {
        tabs: withoutTab(state.tabs, closing.id).concat([homeTab]),
        secondaryTree: null,
        mode: 'stacked',
        mainTabId: homeTab.id,
        focusedTabId: homeTab.id,
      }),
    }
  }

  let secondaryTree = state.secondaryTree
  let mode: WorkspaceMode = state.mode
  if (promotingLeaf) {
    secondaryTree = normalizeTree(removeLeaf(state.secondaryTree, successor.id))
    if (!secondaryTree) mode = 'stacked'
  } else {
    secondaryTree = null
    mode = 'stacked'
  }
  return {
    ok: true,
    needsHomeTab: false,
    successorId: successor.id,
    promotedLeaf: promotingLeaf,
    state: patchState(state, {
      tabs: withoutTab(state.tabs, closing.id),
      secondaryTree,
      mode,
      mainTabId: successor.id,
      focusedTabId: successor.id,
    }),
  }
}

export type ActivatePlan =
  | { ok: false; state: WorkspaceState }
  | { ok: true; kind: 'focus-main'; changed: boolean; state: WorkspaceState }
  | { ok: true; kind: 'focus-secondary'; changed: boolean; state: WorkspaceState }
  | {
      ok: true
      kind: 'promote'
      state: WorkspaceState
      previousMainId: string | null
      coldParkTabId: string | null
    }
  | { ok: true; kind: 'set-main'; state: WorkspaceState; previousMainId: string | null }

export function planActivate(
  state: WorkspaceState,
  tabId: string,
  presentation: WorkspacePresentation,
  options: { fromPopstate?: boolean; oldMainSupportsTile: boolean },
): ActivatePlan {
  const tab = findTab(state, tabId)
  if (!tab) return { ok: false, state }
  if (tab.id === state.mainTabId && !options.fromPopstate) {
    const focus = planFocus(state, tab.id, presentation)
    return { ok: true, kind: 'focus-main', changed: focus.changed, state: focus.ok ? focus.state : state }
  }
  if (visualTiled(state, presentation) && containsTab(state.secondaryTree, tab.id) && !options.fromPopstate) {
    const focus = planFocus(state, tab.id, presentation)
    if (!focus.ok) return { ok: false, state }
    return { ok: true, kind: 'focus-secondary', changed: focus.changed, state: focus.state }
  }
  const previousMainId = state.mainTabId
  if (containsTab(state.secondaryTree, tab.id)) {
    const made = planMakeMain(state, tab.id, options.oldMainSupportsTile)
    if (!made.ok) return { ok: false, state }
    return {
      ok: true,
      kind: 'promote',
      state: made.state,
      previousMainId,
      coldParkTabId: made.coldParkTabId,
    }
  }
  return {
    ok: true,
    kind: 'set-main',
    previousMainId,
    state: patchState(state, { mainTabId: tab.id, focusedTabId: tab.id }),
  }
}

export interface TabHistoryPresentation {
  title: string
  icon: string
}

/** Next logical tab after a route change. Does not write history or the URL. */
export function planTabHistory(
  tab: WorkspaceTab,
  hash: string,
  replace: boolean,
  presentation: TabHistoryPresentation,
): WorkspaceTab {
  const title = presentation.title
  const icon = presentation.icon
  if (replace) {
    const history = tab.history.slice()
    history[tab.historyIndex] = hash
    return { ...tab, route: hash, title, icon, history, historyIndex: tab.historyIndex }
  }
  if (tab.route === hash) return { ...tab, route: hash, title, icon }
  const history = tab.history.slice(0, tab.historyIndex + 1)
  if (history[history.length - 1] === hash) {
    return { ...tab, route: hash, title, icon, history, historyIndex: history.length - 1 }
  }
  history.push(hash)
  return { ...tab, route: hash, title, icon, history, historyIndex: history.length - 1 }
}

export function planSplitLeaf(
  state: WorkspaceState,
  targetTabId: string,
  newTabId: string,
  axis: string,
  placement: string,
  nextSplitId: SplitIdFactory,
): { ok: boolean; state: WorkspaceState } {
  if (!containsTab(state.secondaryTree, targetTabId)) return { ok: false, state }
  if (secondaryLeafCapReached(state.secondaryTree)) return { ok: false, state }
  if (!findTab(state, newTabId) || newTabId === state.mainTabId) return { ok: false, state }
  if (containsTab(state.secondaryTree, newTabId)) return { ok: false, state }
  const secondaryTree = splitLeaf(
    state.secondaryTree,
    targetTabId,
    { axis, newTabId, placement },
    nextSplitId,
  )
  if (secondaryTree === state.secondaryTree) return { ok: false, state }
  return { ok: true, state: patchState(state, { secondaryTree, mode: 'tiled' }) }
}

/**
 * Defensive repair used by paint. Drops Secondary leaves that are missing or
 * are Main, then forces stacked focus back to Main. Throws on a tree that is
 * still structurally invalid after that drop — same failure as the legacy
 * coordinator.
 */
export function repairWorkspaceState(state: WorkspaceState): WorkspaceState {
  let mainTabId = state.mainTabId
  if (!mainTabId && state.tabs.length) mainTabId = state.tabs[0].id
  let secondaryTree = state.secondaryTree
  if (secondaryTree) {
    const leafIds = collectLeafTabIds(secondaryTree)
    for (let i = 0; i < leafIds.length; i++) {
      const id = leafIds[i]
      if (!findTab(state, id) || id === mainTabId) {
        secondaryTree = normalizeTree(removeLeaf(secondaryTree, id))
      }
    }
    if (secondaryTree) {
      const check = validateTree(secondaryTree)
      if (!check.ok) {
        throw new Error('workspace secondaryTree invariant violation: ' + check.errors.join('; '))
      }
    }
  }
  let mode = state.mode
  if (mode === 'tiled' && !secondaryTree) mode = 'stacked'
  let focusedTabId = state.focusedTabId
  if (mode === 'stacked') focusedTabId = mainTabId
  else if (focusedTabId !== mainTabId && !containsTab(secondaryTree, focusedTabId)) focusedTabId = mainTabId
  if (
    mainTabId === state.mainTabId &&
    focusedTabId === state.focusedTabId &&
    secondaryTree === state.secondaryTree &&
    mode === state.mode
  ) {
    return state
  }
  return patchState(state, { mainTabId, focusedTabId, secondaryTree, mode })
}
