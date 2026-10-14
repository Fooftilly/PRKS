import { describe, expect, it } from 'vitest'
import { applyWorkspaceCommand, preflightCloseTab, preflightHideLeaf, preflightMakeMain, preflightSetMode } from './commands'
import { MAX_SECONDARY_LEAVES } from './constants'
import { hiddenSecondaryTabIds, logicalTabRole, parkedTabIds, visibleTabIds } from './derive'
import { validateWorkspace } from './invariants'
import { workspaceSnapshot } from './snapshot'
import {
  collectLeafTabIds,
  findSiblingLeafTabId,
  makeLeaf,
  makeSplit,
  moveLeafRelativeToTarget,
  normalizeTree,
  removeLeaf,
  replaceTabId,
  splitLeaf,
  validateTree,
  type SplitIdFactory,
} from './tree'
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
  planSplitLeaf,
  planTabHistory,
  repairWorkspaceState,
} from './transitions'
import { asSplitId, asTabId, type WorkspacePaneNode, type WorkspaceState, type WorkspaceTab } from './types'

const wide = { narrowFallback: false }
const narrow = { narrowFallback: true }

function ids(): SplitIdFactory {
  let n = 0
  return () => asSplitId('split-' + ++n)
}

function tab(id: string, route = '#/works/' + id): WorkspaceTab {
  return {
    id: asTabId(id),
    route,
    title: id,
    icon: 'file',
    history: [route],
    historyIndex: 0,
  }
}

function state(partial: Partial<WorkspaceState> & Pick<WorkspaceState, 'tabs'>): WorkspaceState {
  return {
    version: 1,
    mode: 'stacked',
    mainTabId: partial.tabs[0] ? partial.tabs[0].id : null,
    focusedTabId: partial.tabs[0] ? partial.tabs[0].id : null,
    secondaryTree: null,
    mainSplitRatio: 0.58,
    ...partial,
  }
}

function freezeDeep(value: unknown): void {
  if (!value || typeof value !== 'object') return
  Object.freeze(value)
  for (const child of Object.values(value as Record<string, unknown>)) freezeDeep(child)
}

function json(value: unknown): string {
  return JSON.stringify(value)
}

describe('secondary tree', () => {
  it('keeps a single secondary as a bare leaf', () => {
    const leaf = makeLeaf('tab-2')
    expect(leaf).toEqual({ type: 'leaf', tabId: 'tab-2' })
    expect(validateTree(leaf).ok).toBe(true)
    expect(validateTree(null).ok).toBe(true)
    expect(collectLeafTabIds(leaf)).toEqual(['tab-2'])
  })

  it('splits, nests, and shares untouched subtrees', () => {
    const next = ids()
    const right = makeSplit('top-bottom', makeLeaf('tab-3'), makeLeaf('tab-4'), 0.25, next())
    const root = makeSplit('left-right', makeLeaf('tab-2'), right, 0.5, next())
    freezeDeep(root)
    const moved = moveLeafRelativeToTarget(root, 'tab-4', 'tab-2', { axis: 'top-bottom', placement: 'first' }, next)
    expect(collectLeafTabIds(moved)).toEqual(['tab-4', 'tab-2', 'tab-3'])
    expect(json(root)).toBe(json(JSON.parse(json(root))))
    expect(validateTree(moved).ok).toBe(true)
  })

  it('collapses a deep split when a leaf is removed', () => {
    const next = ids()
    const inner = makeSplit('left-right', makeLeaf('tab-2'), makeLeaf('tab-3'), 0.4, next())
    const root = makeSplit('top-bottom', inner, makeSplit('left-right', makeLeaf('tab-4'), makeLeaf('tab-5'), 0.6, next()), 0.5, next())
    const right = root.second
    const after = normalizeTree(removeLeaf(root, 'tab-2'))
    expect(after).toMatchObject({
      type: 'split',
      axis: 'top-bottom',
      first: { type: 'leaf', tabId: 'tab-3' },
    })
    expect(after && after.type === 'split' ? after.second : null).toBe(right)
    const flat = normalizeTree(removeLeaf(normalizeTree(removeLeaf(after, 'tab-4')), 'tab-5'))
    expect(flat).toEqual({ type: 'leaf', tabId: 'tab-3' })
    expect(normalizeTree(removeLeaf(flat, 'tab-3'))).toBeNull()
  })

  it('rejects duplicate leaves, duplicate split ids, and bad ratios', () => {
    const badLeaf = makeSplit('left-right', makeLeaf('tab-2'), makeLeaf('tab-2'), 0.5, asSplitId('split-1'))
    expect(validateTree(badLeaf).ok).toBe(false)
    const badId = {
      type: 'split' as const,
      id: asSplitId('split-1'),
      axis: 'left-right' as const,
      ratio: 0.5,
      first: makeSplit('left-right', makeLeaf('tab-2'), makeLeaf('tab-3'), 0.5, asSplitId('split-1')),
      second: makeLeaf('tab-4'),
    }
    expect(validateTree(badId).errors.some((error) => error.includes('duplicate split id'))).toBe(true)
    const badRatio = makeSplit('nope', makeLeaf('tab-2'), makeLeaf('tab-3'), 4, asSplitId('split-9'))
    expect(badRatio.axis).toBe('left-right')
    expect(badRatio.ratio).toBe(1)
    expect(validateTree({ ...badRatio, ratio: 2 }).ok).toBe(false)
  })

  it('does not mutate inputs when splitting or replacing', () => {
    const next = ids()
    const original = makeLeaf('tab-2')
    freezeDeep(original)
    const split = splitLeaf(original, 'tab-2', { newTabId: 'tab-3', axis: 'top-bottom', placement: 'first' }, next)
    expect(original).toEqual({ type: 'leaf', tabId: 'tab-2' })
    expect(collectLeafTabIds(split)).toEqual(['tab-3', 'tab-2'])
    const swapped = replaceTabId(split, 'tab-3', 'tab-9')
    expect(collectLeafTabIds(split)).toEqual(['tab-3', 'tab-2'])
    expect(collectLeafTabIds(swapped)).toEqual(['tab-9', 'tab-2'])
    expect(splitLeaf(original, 'missing', { newTabId: 'tab-3' }, next)).toBe(original)
  })

  it('picks the closest surviving sibling', () => {
    const next = ids()
    const tree = makeSplit(
      'left-right',
      makeLeaf('tab-2'),
      makeSplit('top-bottom', makeLeaf('tab-3'), makeLeaf('tab-4'), 0.5, next()),
      0.5,
      next(),
    )
    expect(findSiblingLeafTabId(tree, 'tab-2')).toBe('tab-3')
    expect(findSiblingLeafTabId(tree, 'tab-3')).toBe('tab-4')
    expect(findSiblingLeafTabId(makeLeaf('tab-2'), 'tab-2')).toBeNull()
  })
})

describe('workspace transitions', () => {
  const main = tab('tab-1', '#/folders')
  const second = tab('tab-2', '#/works/W2')
  const third = tab('tab-3', '#/people/P3')
  const fourth = tab('tab-4', '#/concepts/C4')
  const parked = tab('tab-5', '#/works/W5')

  function tiled(): WorkspaceState {
    const next = ids()
    return state({
      mode: 'tiled',
      tabs: [main, second, third, fourth, parked],
      mainTabId: main.id,
      focusedTabId: third.id,
      secondaryTree: makeSplit(
        'left-right',
        makeLeaf(second.id),
        makeSplit('top-bottom', makeLeaf(third.id), makeLeaf(fourth.id), 0.5, next()),
        0.5,
        next(),
      ),
    })
  }

  it('reorders tabs without touching main, focus, or the tree', () => {
    const current = tiled()
    freezeDeep(current)
    const moved = planReorder(current, 'tab-5', 'tab-2')
    expect(moved.ok).toBe(true)
    expect(moved.state.tabs.map((item) => item.id)).toEqual(['tab-1', 'tab-5', 'tab-2', 'tab-3', 'tab-4'])
    expect(moved.state.mainTabId).toBe(current.mainTabId)
    expect(moved.state.focusedTabId).toBe(current.focusedTabId)
    expect(moved.state.secondaryTree).toBe(current.secondaryTree)
    expect(current.tabs.map((item) => item.id)).toEqual(['tab-1', 'tab-2', 'tab-3', 'tab-4', 'tab-5'])
    expect(planReorder(current, 'missing', null).ok).toBe(false)
    expect(planReorder(current, 'tab-1', 'tab-1').ok).toBe(false)
  })

  it('focuses a visible secondary without promoting it to main', () => {
    const current = tiled()
    const focused = planFocus(current, 'tab-4', wide)
    expect(focused.ok).toBe(true)
    expect(focused.state.focusedTabId).toBe('tab-4')
    expect(focused.state.mainTabId).toBe('tab-1')
    expect(planFocus(current, 'tab-5', wide).ok).toBe(false)
    expect(planFocus(current, 'tab-4', narrow).ok).toBe(false)
    const hidden = { ...current, mode: 'stacked' as const, focusedTabId: main.id }
    expect(planFocus(hidden, 'tab-2', wide).ok).toBe(false)
    expect(planFocus(current, 'nope', wide).ok).toBe(false)
  })

  it('swaps main in place and cold-parks a main that cannot tile', () => {
    const current = tiled()
    freezeDeep(current)
    const swapped = planMakeMain(current, 'tab-4', true)
    expect(swapped.ok).toBe(true)
    expect(swapped.coldParkTabId).toBeNull()
    expect(swapped.state.mainTabId).toBe('tab-4')
    expect(swapped.state.focusedTabId).toBe('tab-4')
    expect(collectLeafTabIds(swapped.state.secondaryTree)).toEqual(['tab-2', 'tab-3', 'tab-1'])
    expect(collectLeafTabIds(current.secondaryTree)).toEqual(['tab-2', 'tab-3', 'tab-4'])
    const parkedMain = planMakeMain(current, 'tab-3', false)
    expect(parkedMain.coldParkTabId).toBe('tab-1')
    expect(collectLeafTabIds(parkedMain.state.secondaryTree)).toEqual(['tab-2', 'tab-4'])
    expect(parkedMain.state.mainTabId).toBe('tab-3')
    expect(planMakeMain(current, 'tab-5', true).ok).toBe(false)
    expect(planMakeMain(current, 'missing', true).ok).toBe(false)
  })

  it('hides one leaf, collapses, and keeps the logical tab', () => {
    const current = tiled()
    const hidden = planHideLeaf(current, 'tab-3')
    expect(hidden.ok).toBe(true)
    expect(collectLeafTabIds(hidden.state.secondaryTree)).toEqual(['tab-2', 'tab-4'])
    expect(hidden.state.focusedTabId).toBe('tab-4')
    expect(hidden.state.tabs.map((item) => item.id)).toContain('tab-3')
    expect(logicalTabRole(hidden.state, 'tab-3')).toBe('parked')
    const last = planHideLeaf(
      planHideLeaf(hidden.state, 'tab-2').state,
      'tab-4',
    )
    expect(last.state.secondaryTree).toBeNull()
    expect(last.state.mode).toBe('stacked')
    expect(last.state.focusedTabId).toBe('tab-1')
    expect(last.state.tabs).toHaveLength(5)
  })

  it('moves a leaf between branches and refuses main or a hidden layout', () => {
    const current = tiled()
    const next = ids()
    const moved = planMovePane(current, 'tab-4', 'tab-2', 'top-bottom', 'first', wide, next)
    expect(moved.ok).toBe(true)
    expect(collectLeafTabIds(moved.state.secondaryTree)).toEqual(['tab-4', 'tab-2', 'tab-3'])
    expect(moved.state.focusedTabId).toBe(current.focusedTabId)
    expect(planMovePane(current, 'tab-1', 'tab-2', 'left-right', 'second', wide, next).ok).toBe(false)
    expect(planMovePane(current, 'tab-4', 'tab-2', 'left-right', 'second', narrow, next).ok).toBe(false)
    expect(planMovePane(current, 'tab-4', 'tab-4', 'left-right', 'second', wide, next).ok).toBe(false)
  })

  it('refuses split insertion at the pane cap and for unknown ids', () => {
    const current = tiled()
    expect(collectLeafTabIds(current.secondaryTree)).toHaveLength(MAX_SECONDARY_LEAVES)
    expect(planSplitLeaf(current, 'tab-2', 'tab-5', 'left-right', 'second', ids()).ok).toBe(false)
    const room = planHideLeaf(current, 'tab-4').state
    expect(planSplitLeaf(room, 'missing', 'tab-5', 'left-right', 'second', ids()).ok).toBe(false)
    const inserted = planSplitLeaf(room, 'tab-2', 'tab-5', 'top-bottom', 'first', ids())
    expect(inserted.ok).toBe(true)
    expect(inserted.state.mode).toBe('tiled')
    expect(collectLeafTabIds(inserted.state.secondaryTree)).toEqual(['tab-5', 'tab-2', 'tab-3'])
    expect(planSplitLeaf(room, 'tab-2', 'tab-1', 'left-right', 'second', ids()).ok).toBe(false)
  })

  it('clamps root and nested ratios at the boundaries', () => {
    const current = tiled()
    expect(planMainSplitRatio(current, 2).ratio).toBe(1)
    expect(planMainSplitRatio(current, -1).ratio).toBe(0)
    expect(planMainSplitRatio(current, Number.NaN).ratio).toBe(0.58)
    expect(planMainSplitRatio(current, 0.25).state.mainSplitRatio).toBe(0.25)
    const splitId = current.secondaryTree && current.secondaryTree.type === 'split' ? current.secondaryTree.id : ''
    expect(planNestedSplitRatio(current, splitId, 9).ratio).toBe(1)
    expect(planNestedSplitRatio(current, splitId, -3).ratio).toBe(0)
    expect(planNestedSplitRatio(current, 'missing', 0.2).ok).toBe(false)
    const nested = planNestedSplitRatio(current, splitId, 0.1)
    expect(nested.state.secondaryTree).not.toBe(current.secondaryTree)
    expect(current.mainSplitRatio).toBe(0.58)
  })

  it('keeps a hidden split logical while only main is visible', () => {
    const hidden = { ...tiled(), mode: 'stacked' as const, focusedTabId: main.id }
    expect(validateWorkspace(hidden).ok).toBe(true)
    expect(visibleTabIds(hidden, wide)).toEqual(['tab-1'])
    expect(hiddenSecondaryTabIds(hidden, wide)).toEqual(['tab-2', 'tab-3', 'tab-4'])
    expect(parkedTabIds(hidden)).toEqual(['tab-5'])
    expect(logicalTabRole(hidden, 'tab-2')).toBe('secondary')
    expect(visibleTabIds(tiled(), wide)).toEqual(['tab-1', 'tab-2', 'tab-3', 'tab-4'])
    expect(visibleTabIds(tiled(), narrow)).toEqual(['tab-1'])
  })

  it('closes a secondary without promoting and closes main by role swap', () => {
    const current = tiled()
    const closed = planCloseTab(current, 'tab-2', null)
    expect(closed.ok).toBe(true)
    expect(closed.state.mainTabId).toBe('tab-1')
    expect(closed.state.focusedTabId).toBe('tab-3')
    expect(collectLeafTabIds(closed.state.secondaryTree)).toEqual(['tab-3', 'tab-4'])
    expect(closed.state.tabs.map((item) => item.id)).not.toContain('tab-2')
    expect(validateWorkspace(closed.state).ok).toBe(true)
    const promoted = planCloseTab(current, 'tab-1', null)
    expect(promoted.promotedLeaf).toBe(true)
    expect(promoted.state.mainTabId).toBe('tab-2')
    expect(collectLeafTabIds(promoted.state.secondaryTree)).toEqual(['tab-3', 'tab-4'])
    const only = state({ tabs: [main], mainTabId: main.id, focusedTabId: main.id })
    const last = planCloseTab(only, 'tab-1', null)
    expect(last.needsHomeTab).toBe(true)
    expect(last.ok).toBe(false)
    expect(last.state).toBe(only)
    const home = tab('tab-9', '#/folders')
    const replaced = planCloseTab(only, 'tab-1', home)
    expect(replaced.ok).toBe(true)
    expect(replaced.needsHomeTab).toBe(false)
    expect(replaced.state.mainTabId).toBe('tab-9')
    expect(replaced.state.tabs).toEqual([home])
  })

  it('keeps focus when closing an unfocused secondary and forces main in stacked mode', () => {
    const current = tiled()
    const unfocused = planCloseTab(current, 'tab-4', null)
    expect(unfocused.ok).toBe(true)
    expect(unfocused.state.mode).toBe('tiled')
    expect(unfocused.state.focusedTabId).toBe('tab-3')
    expect(validateWorkspace(unfocused.state).ok).toBe(true)
    const owned = planCloseTab(current, 'tab-3', null)
    expect(owned.state.focusedTabId).toBe('tab-4')
    expect(validateWorkspace(owned.state).ok).toBe(true)
    const hidden = { ...tiled(), mode: 'stacked' as const, focusedTabId: main.id }
    const closedHidden = planCloseTab(hidden, 'tab-2', null)
    expect(closedHidden.ok).toBe(true)
    expect(closedHidden.state.mode).toBe('stacked')
    expect(closedHidden.state.focusedTabId).toBe(main.id)
    expect(collectLeafTabIds(closedHidden.state.secondaryTree)).toEqual(['tab-3', 'tab-4'])
    expect(validateWorkspace(closedHidden.state).ok).toBe(true)
  })

  it('activates a visible secondary by focus and a parked tab by making it main', () => {
    const current = tiled()
    const focused = planActivate(current, 'tab-2', wide, { oldMainSupportsTile: true })
    expect(focused.ok && focused.kind).toBe('focus-secondary')
    if (focused.ok && focused.kind === 'focus-secondary') {
      expect(focused.state.mainTabId).toBe('tab-1')
      expect(focused.state.focusedTabId).toBe('tab-2')
    }
    const parkedActivate = planActivate(current, 'tab-5', wide, { oldMainSupportsTile: true })
    expect(parkedActivate.ok && parkedActivate.kind).toBe('set-main')
    if (parkedActivate.ok && parkedActivate.kind === 'set-main') {
      expect(parkedActivate.state.mainTabId).toBe('tab-5')
      expect(parkedActivate.state.secondaryTree).toBe(current.secondaryTree)
    }
  })

  it('updates tab history without mutating the input tab', () => {
    const current = tab('tab-1', '#/folders')
    freezeDeep(current)
    const next = planTabHistory(current, '#/works/W1', false, { title: 'Work', icon: 'book' })
    expect(current.route).toBe('#/folders')
    expect(next.history).toEqual(['#/folders', '#/works/W1'])
    expect(next.historyIndex).toBe(1)
    const replaced = planTabHistory(next, '#/people/P1', true, { title: 'Person', icon: 'user' })
    expect(replaced.history).toEqual(['#/folders', '#/people/P1'])
    expect(next.history[1]).toBe('#/works/W1')
  })

  it('copies the external snapshot off the live objects', () => {
    const current = tiled()
    const snap = workspaceSnapshot(current)
    expect(snap.tabs[0]).not.toBe(current.tabs[0])
    expect(snap.tabs[0].history).not.toBe(current.tabs[0].history)
    expect(snap.secondaryTree).not.toBe(current.secondaryTree)
    expect(snap.secondaryTree).toEqual(current.secondaryTree)
    expect(validateWorkspace(current).ok).toBe(true)
  })

  it('rejects main inside the tree, a missing focus, and a tiled workspace with no tree', () => {
    const current = tiled()
    const illegal = {
      ...current,
      secondaryTree: makeLeaf(current.mainTabId || 'tab-1'),
    }
    expect(validateWorkspace(illegal).ok).toBe(false)
    expect(validateWorkspace({ ...current, focusedTabId: asTabId('tab-missing') }).ok).toBe(false)
    expect(validateWorkspace({ ...current, mode: 'tiled', secondaryTree: null }).ok).toBe(false)
    const repaired = repairWorkspaceState({
      ...current,
      secondaryTree: makeSplit('left-right', makeLeaf('tab-1'), makeLeaf('tab-2'), 0.5, asSplitId('split-1')),
    })
    expect(collectLeafTabIds(repaired.secondaryTree)).toEqual(['tab-2'])
  })
})

describe('command boundary', () => {
  const main = tab('tab-1', '#/folders')
  const second = tab('tab-2', '#/works/W2')

  function base(): WorkspaceState {
    return state({
      mode: 'tiled',
      tabs: [main, second],
      mainTabId: main.id,
      focusedTabId: second.id,
      secondaryTree: makeLeaf(second.id),
    })
  }

  it('does not treat focus as a main promotion and names no leave preflight', () => {
    const result = applyWorkspaceCommand(base(), { type: 'focus', tabId: 'tab-2' }, {
      narrowFallback: false,
      homeHash: '#/folders',
    })
    expect(result.ok).toBe(true)
    expect(result.state.mainTabId).toBe('tab-1')
    expect(result.preflight).toEqual({ type: 'none' })
    expect(result.effects.some((effect) => effect.type === 'commit-main-url')).toBe(false)
  })

  it('requires leave before hiding a mounted secondary and before demoting an untileable main', () => {
    const current = base()
    expect(preflightHideLeaf(current, 'tab-2', {
      narrowFallback: false,
      homeHash: '#/folders',
      mounted: true,
    })).toEqual({ type: 'leave-tab', tabId: 'tab-2', nextHash: '#/folders' })
    expect(preflightHideLeaf(current, 'tab-2', {
      narrowFallback: true,
      homeHash: '#/folders',
      mounted: false,
    })).toEqual({ type: 'none' })
    expect(preflightMakeMain(current, 'tab-2', {
      narrowFallback: false,
      homeHash: '#/folders',
      oldMainSupportsTile: false,
    })).toMatchObject({ type: 'main-promotion', requiresLeave: true, oldMainId: 'tab-1' })
    expect(preflightMakeMain(current, 'tab-2', {
      narrowFallback: false,
      homeHash: '#/folders',
      oldMainSupportsTile: true,
    })).toMatchObject({ requiresLeave: false })
    const hidden = applyWorkspaceCommand(current, { type: 'move-pane', sourceTabId: 'tab-2', targetTabId: 'tab-2', axis: 'left-right', placement: 'second' }, {
      narrowFallback: false,
      homeHash: '#/folders',
      nextSplitId: ids(),
    })
    expect(hidden.ok).toBe(false)
    expect(hidden.state).toBe(current)
  })

  it('preflights and parks only mounted secondary leaves when hiding split', () => {
    const current = base()
    const context = {
      narrowFallback: false,
      homeHash: '#/folders',
      mountedTabIds: [main.id, second.id, 'tab-parked'],
    }
    expect(preflightSetMode(current, 'stacked', context)).toEqual({
      type: 'leave-tabs',
      tabIds: [second.id],
      nextHash: '#/folders',
    })
    const hidden = applyWorkspaceCommand(current, { type: 'set-mode', mode: 'stacked' }, context)
    expect(hidden.ok).toBe(true)
    expect(hidden.preflight).toEqual({ type: 'leave-tabs', tabIds: ['tab-2'], nextHash: '#/folders' })
    expect(hidden.effects.filter((effect) => effect.type === 'cold-park')).toEqual([
      { type: 'cold-park', tabId: 'tab-2' },
    ])
  })

  it('reports last-tab close as an explicit non-success', () => {
    const only = state({ tabs: [main], mainTabId: main.id, focusedTabId: main.id })
    const result = applyWorkspaceCommand(only, { type: 'close-tab', tabId: main.id, homeTab: null }, {
      narrowFallback: false,
      homeHash: '#/folders',
    })
    expect(result.ok).toBe(false)
    expect(result.needsHomeTab).toBe(true)
    expect(result.changed).toBe(false)
    expect(result.state).toBe(only)
    expect(result.effects).toEqual([])
  })

  it('routes main close to a real successor but leaves parked and hidden tabs alone', () => {
    const current = base()
    const context = { narrowFallback: false, homeHash: '#/folders' }
    expect(preflightCloseTab(current, main.id, context)).toEqual({
      type: 'leave-tab', tabId: main.id, nextHash: second.route,
    })
    expect(preflightCloseTab(current, second.id, context)).toEqual({
      type: 'leave-tab', tabId: second.id, nextHash: '#/folders',
    })
    const parked = tab('tab-3')
    const withParked = { ...current, tabs: [...current.tabs, parked] }
    expect(preflightCloseTab(withParked, parked.id, context)).toEqual({ type: 'none' })
    expect(preflightCloseTab(current, 'missing', context)).toEqual({ type: 'none' })
    const hidden = { ...current, mode: 'stacked' as const, focusedTabId: main.id }
    expect(preflightCloseTab(hidden, second.id, context)).toEqual({ type: 'none' })
    expect(preflightCloseTab(current, second.id, { ...context, narrowFallback: true })).toEqual({ type: 'none' })
  })

  it('rejects invisible focus and skips effects on an already-focused tab', () => {
    const current = base()
    const context = { narrowFallback: false, homeHash: '#/folders' }
    const already = applyWorkspaceCommand(current, { type: 'focus', tabId: second.id }, context)
    expect(already).toMatchObject({ ok: true, changed: false, state: current, effects: [] })
    for (const [tabId, presentation] of [
      ['missing', context],
      [second.id, { ...context, narrowFallback: true }],
    ] as const) {
      const rejected = applyWorkspaceCommand(current, { type: 'focus', tabId }, presentation)
      expect(rejected).toMatchObject({ ok: false, changed: false, state: current, effects: [] })
    }
    const mainFocus = applyWorkspaceCommand(current, { type: 'focus', tabId: main.id }, context)
    expect(mainFocus.state.mainTabId).toBe(main.id)
    expect(mainFocus.state.focusedTabId).toBe(main.id)
    expect(mainFocus.effects).toEqual([{ type: 'paint-focus' }, { type: 'refresh-focused-panel' }])
    expect(mainFocus.preflight).toEqual({ type: 'none' })
  })

  it('distinguishes a role swap from cold-parking an untileable Main', () => {
    const current = base()
    const command = { type: 'make-main' as const, tabId: second.id }
    const context = { narrowFallback: false, homeHash: '#/folders' }
    const swapped = applyWorkspaceCommand(current, command, { ...context, oldMainSupportsTile: true })
    expect(swapped).toMatchObject({ ok: true, changed: true })
    expect(swapped.preflight).toEqual({
      type: 'main-promotion', oldMainId: main.id, requiresLeave: false, nextHash: main.route,
    })
    expect(swapped.state.mainTabId).toBe(second.id)
    expect(swapped.state.mode).toBe('tiled')
    expect(swapped.state.secondaryTree).toEqual(makeLeaf(main.id))
    expect(swapped.effects.some((effect) => effect.type === 'cold-park')).toBe(false)

    const parked = applyWorkspaceCommand(current, command, { ...context, oldMainSupportsTile: false })
    expect(parked.preflight).toEqual({
      type: 'main-promotion', oldMainId: main.id, requiresLeave: true, nextHash: main.route,
    })
    expect(parked.state.mainTabId).toBe(second.id)
    expect(parked.state.mode).toBe('stacked')
    expect(parked.state.secondaryTree).toBeNull()
    expect(parked.effects).toContainEqual({ type: 'cold-park', tabId: main.id })
    expect(parked.effects).toContainEqual({
      type: 'commit-main-url', tabId: second.id, history: 'replace',
    })
    expect(current.mainTabId).toBe(main.id)
    expect(validateWorkspace(swapped.state).ok).toBe(true)
    expect(validateWorkspace(parked.state).ok).toBe(true)
  })

  it('hides an already-hidden secondary without prompting or parking an unmounted pane', () => {
    const current = { ...base(), mode: 'stacked' as const, focusedTabId: main.id }
    const context = { narrowFallback: false, homeHash: '#/folders', mounted: true }
    const removed = applyWorkspaceCommand(current, { type: 'hide-leaf', tabId: second.id }, context)
    expect(removed).toMatchObject({ ok: true, changed: true })
    expect(removed.preflight).toEqual({ type: 'none' })
    expect(removed.effects.some((effect) => effect.type === 'cold-park')).toBe(false)
    expect(removed.state.secondaryTree).toBeNull()
    expect(removed.state.focusedTabId).toBe(main.id)
    expect(validateWorkspace(removed.state).ok).toBe(true)
    expect(current.secondaryTree).toEqual(makeLeaf(second.id))
    expect(applyWorkspaceCommand(current, { type: 'hide-leaf', tabId: 'missing' }, context))
      .toMatchObject({ ok: false, changed: false, state: current, effects: [] })
  })

  it('preserves command failure and no-op contracts before performing effects', () => {
    const current = base()
    const context = { narrowFallback: false, homeHash: '#/folders' }
    const attempts = [
      { type: 'set-mode' as const, mode: 'invalid' },
      { type: 'move-pane' as const, sourceTabId: second.id, targetTabId: second.id, axis: 'left-right', placement: 'first' },
      { type: 'close-tab' as const, tabId: 'missing', homeTab: null },
    ]
    for (const command of attempts) {
      const result = applyWorkspaceCommand(current, command, context)
      expect(result).toMatchObject({ ok: false, changed: false, state: current, effects: [] })
      expect(result.preflight).toEqual({ type: 'none' })
    }

    // The new tab is valid and in state.tabs: only the missing ID factory blocks this split.
    const splitReady = { ...current, tabs: [...current.tabs, tab('tab-3')] }
    const splitCommand = {
      type: 'split-leaf' as const,
      targetTabId: second.id,
      newTabId: 'tab-3',
      axis: 'left-right',
      placement: 'first',
    }
    const noFactory = applyWorkspaceCommand(splitReady, splitCommand, context)
    expect(noFactory).toMatchObject({ ok: false, changed: false, state: splitReady, effects: [] })
    expect(noFactory.preflight).toEqual({ type: 'none' })
    const withFactory = applyWorkspaceCommand(splitReady, splitCommand, { ...context, nextSplitId: ids() })
    expect(withFactory.ok).toBe(true)
    expect(withFactory.changed).toBe(true)
    expect(collectLeafTabIds(withFactory.state.secondaryTree)).toEqual(['tab-3', second.id])
    expect(validateWorkspace(withFactory.state).ok).toBe(true)

    const hidden = { ...current, mode: 'stacked' as const, focusedTabId: main.id }
    const unchanged = applyWorkspaceCommand(hidden, { type: 'set-mode', mode: 'stacked' }, context)
    expect(unchanged).toMatchObject({ ok: true, changed: false, state: hidden, effects: [] })
    expect(preflightSetMode(current, 'stacked', { ...context, narrowFallback: true, mountedTabIds: [second.id] }))
      .toEqual({ type: 'none' })
  })

  it('keeps close effects scoped to the destroyed tab and the new Main', () => {
    const current = base()
    const context = { narrowFallback: false, homeHash: '#/folders' }
    const promoted = applyWorkspaceCommand(current, { type: 'close-tab', tabId: main.id, homeTab: null }, context)
    expect(promoted.ok).toBe(true)
    expect(promoted.preflight).toEqual({ type: 'leave-tab', tabId: main.id, nextHash: second.route })
    expect(promoted.state.mainTabId).toBe(second.id)
    expect(promoted.effects).toEqual([
      { type: 'destroy-context', tabId: main.id },
      { type: 'commit-main-url', tabId: second.id, history: 'replace' },
      { type: 'paint-and-restore', tabId: second.id },
    ])
    const secondary = applyWorkspaceCommand(current, { type: 'close-tab', tabId: second.id, homeTab: null }, context)
    expect(secondary.state.mainTabId).toBe(main.id)
    expect(secondary.effects).toEqual([
      { type: 'destroy-context', tabId: second.id },
      { type: 'paint-and-restore', tabId: main.id },
    ])
    const only = state({ tabs: [main] })
    const home = tab('home', '#/folders')
    const replacement = applyWorkspaceCommand(only, { type: 'close-tab', tabId: main.id, homeTab: home }, context)
    expect(replacement.ok).toBe(true)
    expect(replacement.state.mainTabId).toBe(home.id)
    expect(replacement.effects).toContainEqual({ type: 'commit-main-url', tabId: home.id, history: 'replace' })
    expect(validateWorkspace(replacement.state).ok).toBe(true)
  })

  it('preflights only mounted visible secondaries when hiding a split', () => {
    const current = base()
    const context = {
      narrowFallback: false,
      homeHash: '#/folders',
      mountedTabIds: [main.id, second.id, 'not-a-leaf'],
    }
    const hiding = applyWorkspaceCommand(current, { type: 'set-mode', mode: 'stacked' }, context)
    expect(hiding.preflight).toEqual({
      type: 'leave-tabs', tabIds: [second.id], nextHash: '#/folders',
    })
    expect(hiding.effects.filter((effect) => effect.type === 'cold-park')).toEqual([
      { type: 'cold-park', tabId: second.id },
    ])
    const hidden = { ...current, mode: 'stacked' as const, focusedTabId: main.id }
    const showing = applyWorkspaceCommand(hidden, { type: 'set-mode', mode: 'tiled' }, context)
    expect(showing.ok).toBe(true)
    expect(showing.state.mode).toBe('tiled')
    expect(showing.preflight).toEqual({ type: 'none' })
    expect(showing.effects.some((effect) => effect.type === 'cold-park')).toBe(false)
  })

  it('splits and moves secondary leaves without ever relocating Main', () => {
    const third = tab('tab-3')
    const current = { ...base(), tabs: [main, second, third] }
    const context = { narrowFallback: false, homeHash: '#/folders', nextSplitId: ids() }
    const split = applyWorkspaceCommand(current, {
      type: 'split-leaf', targetTabId: second.id, newTabId: third.id,
      axis: 'top-bottom', placement: 'first',
    }, context)
    expect(split.ok).toBe(true)
    expect(collectLeafTabIds(split.state.secondaryTree)).toEqual([third.id, second.id])
    expect(split.state.mainTabId).toBe(main.id)
    expect(validateWorkspace(split.state).ok).toBe(true)
    const moved = applyWorkspaceCommand(split.state, {
      type: 'move-pane', sourceTabId: third.id, targetTabId: second.id,
      axis: 'left-right', placement: 'second',
    }, context)
    expect(moved.ok).toBe(true)
    expect(collectLeafTabIds(moved.state.secondaryTree)).toEqual([second.id, third.id])
    expect(moved.state.mainTabId).toBe(main.id)
    expect(validateWorkspace(moved.state).ok).toBe(true)
    expect(current.secondaryTree).toEqual(makeLeaf(second.id))
  })
})

describe('workspace state recovery and snapshot boundaries', () => {
  const main = tab('main', '#/folders')
  const secondary = tab('secondary')
  const parked = tab('parked')

  function tiledState(): WorkspaceState {
    return state({
      mode: 'tiled',
      tabs: [main, secondary, parked],
      mainTabId: main.id,
      focusedTabId: secondary.id,
      secondaryTree: makeLeaf(secondary.id),
    })
  }

  it('recovers an absent Main from the first tab and restores stacked focus', () => {
    const current = state({ tabs: [main, secondary], mainTabId: null, focusedTabId: null })
    const repaired = repairWorkspaceState(current)
    expect(repaired).not.toBe(current)
    expect(repaired.mainTabId).toBe(main.id)
    expect(repaired.focusedTabId).toBe(main.id)
    expect(repaired.mode).toBe('stacked')
    expect(validateWorkspace(repaired)).toEqual({ ok: true, errors: [] })
    expect(current.mainTabId).toBeNull()
    expect(current.focusedTabId).toBeNull()
  })

  it('preserves a hidden Secondary tree while repairing stacked-mode focus', () => {
    const current = state({
      tabs: [main, secondary],
      mainTabId: main.id,
      focusedTabId: secondary.id,
      mode: 'stacked',
      secondaryTree: makeLeaf(secondary.id),
    })
    freezeDeep(current)
    const repaired = repairWorkspaceState(current)
    expect(repaired.focusedTabId).toBe(main.id)
    expect(repaired.secondaryTree).toBe(current.secondaryTree)
    expect(repaired.mode).toBe('stacked')
    expect(validateWorkspace(repaired).ok).toBe(true)
    expect(repairWorkspaceState(repaired)).toBe(repaired)
  })

  it('repairs focus on a parked tab without promoting or displaying it', () => {
    const current = { ...tiledState(), focusedTabId: parked.id }
    const repaired = repairWorkspaceState(current)
    expect(repaired.mainTabId).toBe(main.id)
    expect(repaired.focusedTabId).toBe(main.id)
    expect(repaired.mode).toBe('tiled')
    expect(repaired.secondaryTree).toBe(current.secondaryTree)
    expect(repaired.tabs).toBe(current.tabs)
    expect(validateWorkspace(repaired).ok).toBe(true)
  })

  it('removes nonexistent and Main leaves without discarding a surviving Secondary', () => {
    const nested = makeSplit(
      'top-bottom', makeLeaf('deleted'), makeLeaf(secondary.id), 0.4, asSplitId('inner'),
    )
    const root = makeSplit('left-right', makeLeaf(main.id), nested, 0.5, asSplitId('outer'))
    const current = { ...tiledState(), secondaryTree: root }
    freezeDeep(current)
    const repaired = repairWorkspaceState(current)
    expect(repaired.secondaryTree).toEqual(makeLeaf(secondary.id))
    expect(repaired.mode).toBe('tiled')
    expect(repaired.focusedTabId).toBe(secondary.id)
    expect(validateWorkspace(repaired).ok).toBe(true)
    expect(current.secondaryTree).toBe(root)
  })

  it('collapses an emptied Secondary tree and falls back from tiled to stacked', () => {
    const badTree = makeSplit(
      'left-right', makeLeaf('deleted'), makeLeaf(main.id), 0.5, asSplitId('gone'),
    )
    const current = state({
      mode: 'tiled',
      tabs: [main],
      mainTabId: main.id,
      focusedTabId: asTabId('deleted'),
      secondaryTree: badTree,
    })
    const repaired = repairWorkspaceState(current)
    expect(repaired.secondaryTree).toBeNull()
    expect(repaired.mode).toBe('stacked')
    expect(repaired.mainTabId).toBe(main.id)
    expect(repaired.focusedTabId).toBe(main.id)
    expect(validateWorkspace(repaired).ok).toBe(true)
  })

  it('rejects malformed split geometry that survives stale-leaf repair', () => {
    const valid = makeSplit(
      'left-right', makeLeaf(secondary.id), makeLeaf(parked.id), 0.5, asSplitId('split'),
    )
    const current = { ...tiledState(), secondaryTree: { ...valid, ratio: Number.NaN } }
    // The corrupt split survives unchanged. If repair prunes a stale leaf below
    // it, rewritePath currently rebuilds the ancestor and clamps its ratio.
    // This assertion must not be read as a universal fail-closed guarantee.
    expect(() => repairWorkspaceState(current)).toThrow(
      /workspace secondaryTree invariant violation: invalid ratio NaN/,
    )
    expect(Number.isNaN(current.secondaryTree.ratio)).toBe(true)
  })

  it('keeps the exact state reference when all structural invariants already hold', () => {
    const current = tiledState()
    freezeDeep(current)
    expect(validateWorkspace(current)).toEqual({ ok: true, errors: [] })
    expect(repairWorkspaceState(current)).toBe(current)
    expect(repairWorkspaceState(repairWorkspaceState(current))).toBe(current)
  })

  it('copies nested trees and tab histories without aliasing original state', () => {
    const longHistory = { ...main, history: ['#/folders', '#/works/W1'], historyIndex: 1 }
    const tertiary = tab('tertiary')
    const current = state({
      tabs: [longHistory, secondary, parked, tertiary],
      mainTabId: main.id,
      focusedTabId: secondary.id,
      mode: 'tiled',
      secondaryTree: makeSplit(
        'top-bottom',
        makeLeaf(secondary.id),
        makeSplit(
          'left-right', makeLeaf(parked.id), makeLeaf(tertiary.id), 0.65, asSplitId('inner'),
        ),
        0.3,
        asSplitId('outer'),
      ),
    })
    freezeDeep(current)
    const snap = workspaceSnapshot(current)
    expect(snap).toEqual(current)
    expect(snap).not.toBe(current)
    expect(snap.tabs).not.toBe(current.tabs)
    expect(snap.tabs[0]).not.toBe(current.tabs[0])
    expect(snap.tabs[0].history).not.toBe(current.tabs[0].history)
    expect(snap.secondaryTree).not.toBe(current.secondaryTree)
    expect(snap.secondaryTree?.type).toBe('split')
    if (snap.secondaryTree?.type !== 'split' || current.secondaryTree?.type !== 'split') {
      throw new Error('test requires a nested Secondary split')
    }
    expect(snap.secondaryTree.first).not.toBe(current.secondaryTree.first)
    expect(snap.secondaryTree.second).not.toBe(current.secondaryTree.second)
    const copiedInner = snap.secondaryTree.second
    const sourceInner = current.secondaryTree.second
    if (copiedInner.type !== 'split' || sourceInner.type !== 'split') {
      throw new Error('test requires a second-level Secondary split')
    }
    expect(copiedInner.first).not.toBe(sourceInner.first)
    expect(copiedInner.second).not.toBe(sourceInner.second)
    ;(snap.tabs[0].history as string[]).push('#/unsaved')
    expect(current.tabs[0].history).toEqual(['#/folders', '#/works/W1'])
    expect(workspaceSnapshot(current)).toEqual(current)
  })

  it('rejects duplicate tabs, impossible ratios, and excess Secondary leaves', () => {
    const current = tiledState()
    const duplicate = validateWorkspace({ ...current, tabs: [...current.tabs, main] })
    expect(duplicate.ok).toBe(false)
    expect(duplicate.errors).toContain('duplicate tab id ' + main.id)

    const badRatio = validateWorkspace({ ...current, mainSplitRatio: Number.POSITIVE_INFINITY })
    expect(badRatio.ok).toBe(false)
    expect(badRatio.errors.some((error) => error.includes('invalid mainSplitRatio'))).toBe(true)

    const ids = Array.from({ length: MAX_SECONDARY_LEAVES + 1 }, (_, i) => 'leaf-' + i)
    const overflow = ids.slice(1).reduce<WorkspacePaneNode>(
      (tree, id, i) => makeSplit('left-right', tree, makeLeaf(id), 0.5, asSplitId('over-' + i)),
      makeLeaf(ids[0]),
    )
    const tooMany = validateWorkspace(state({
      tabs: [main, ...ids.map((id) => tab(id))],
      mainTabId: main.id,
      focusedTabId: main.id,
      mode: 'tiled',
      secondaryTree: overflow,
    }))
    expect(tooMany.ok).toBe(false)
    expect(tooMany.errors).toContain('pane cap exceeded ' + ids.length)
  })
})
