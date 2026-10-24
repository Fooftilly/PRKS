/** Bounded generated invariants for canonical workspace state (#854).
 *
 * These use only pure transforms: no DOM, random browser clicking, or second
 * workspace model. Vitest's failure output includes fast-check seed/path so
 * a minimized counterexample can be reproduced with fc.assert(..., { seed, path }).
 */
import fc from 'fast-check'
import { describe, expect, it } from 'vitest'
import { planReorder } from './transitions'
import {
  collectLeafTabIds,
  makeLeaf,
  moveLeafRelativeToTarget,
  normalizeTree,
  removeLeaf,
  splitLeaf,
  validateTree,
  type SplitIdFactory,
} from './tree'
import { asSplitId, asTabId, type WorkspacePaneNode, type WorkspaceState, type WorkspaceTab } from './types'

const LIMIT = { numRuns: 30 }
const distinctIds = fc.uniqueArray(fc.integer({ min: 2, max: 50 }), { minLength: 2, maxLength: 6 })
const steps = fc.array(
  fc.record({
    from: fc.nat({ max: 30 }),
    to: fc.nat({ max: 30 }),
    vertical: fc.boolean(),
    first: fc.boolean(),
  }),
  { maxLength: 8 },
)

function idsFactory(): SplitIdFactory {
  let next = 0
  return () => asSplitId('generated-split-' + ++next)
}

function buildTree(numbers: readonly number[], next: SplitIdFactory): WorkspacePaneNode {
  let tree: WorkspacePaneNode | null = makeLeaf('tab-' + numbers[0])
  for (let i = 1; i < numbers.length; i++) {
    tree = splitLeaf(tree, 'tab-' + numbers[i - 1], {
      newTabId: 'tab-' + numbers[i],
      axis: i % 2 ? 'left-right' : 'top-bottom',
      placement: i % 3 ? 'first' : 'second',
    }, next)
  }
  if (tree === null) throw new Error('Generated tree unexpectedly empty')
  return tree
}

function leafIds(numbers: readonly number[]): string[] {
  return numbers.map((value) => 'tab-' + value).sort()
}

function tab(number: number): WorkspaceTab {
  const id = asTabId('tab-' + number)
  const route = '#/works/W-' + number
  return { id, route, title: 'Work ' + number, icon: 'file', history: [route], historyIndex: 0 }
}

describe('fast-check workspace invariant pilot', () => {
  it('preserves unique Secondary leaves through bounded move sequences', () => {
    fc.assert(
      fc.property(distinctIds, steps, (numbers, moves) => {
        const next = idsFactory()
        const all = numbers.map((number) => 'tab-' + number)
        const expected = leafIds(numbers)
        let tree: WorkspacePaneNode | null = buildTree(numbers, next)
        expect(validateTree(tree).ok).toBe(true)
        for (const move of moves) {
          const source = all[move.from % all.length]
          const target = all[move.to % all.length]
          const original = tree
          tree = moveLeafRelativeToTarget(tree, source, target, {
            axis: move.vertical ? 'top-bottom' : 'left-right',
            placement: move.first ? 'first' : 'second',
          }, next)
          expect(validateTree(tree).errors).toEqual([])
          expect(collectLeafTabIds(tree).sort()).toEqual(expected)
          if (source === target) expect(tree).toBe(original)
        }
      }),
      LIMIT,
    )
  })

  it('removing leaves never removes unrelated tabs or leaves empty split nodes', () => {
    fc.assert(
      fc.property(
        distinctIds,
        fc.array(fc.nat({ max: 30 }), { maxLength: 12 }),
        (numbers, removals) => {
          const all = numbers.map((number) => 'tab-' + number)
          const expected = new Set(all)
          let tree: WorkspacePaneNode | null = buildTree(numbers, idsFactory())
          for (const index of removals) {
            const removed = all[index % all.length]
            expected.delete(removed)
            tree = normalizeTree(removeLeaf(tree, removed))
            expect(validateTree(tree).errors).toEqual([])
            expect(collectLeafTabIds(tree).sort()).toEqual([...expected].sort())
          }
          for (const remaining of expected) tree = normalizeTree(removeLeaf(tree, remaining))
          expect(tree).toBeNull()
        },
      ),
      LIMIT,
    )
  })

  it('tab reordering preserves Main, focus, tab identity and the Secondary tree', () => {
    fc.assert(
      fc.property(
        distinctIds,
        fc.nat({ max: 30 }),
        fc.nat({ max: 30 }),
        fc.boolean(),
        (numbers, fromIndex, beforeIndex, moveToEnd) => {
          const tabs = numbers.map(tab)
          const main = tabs[0].id
          const original: WorkspaceState = {
            version: 1,
            mode: 'stacked',
            tabs,
            mainTabId: main,
            focusedTabId: main,
            secondaryTree: null,
            mainSplitRatio: 0.58,
          }
          const selected = tabs[fromIndex % tabs.length].id
          const before = moveToEnd ? null : tabs[beforeIndex % tabs.length].id
          const result = planReorder(original, selected, before)
          if (selected === before) {
            expect(result.ok).toBe(false)
            expect(result.state).toBe(original)
            return
          }
          expect(result.ok).toBe(true)
          expect(result.state.mainTabId).toBe(main)
          expect(result.state.focusedTabId).toBe(main)
          expect(result.state.secondaryTree).toBe(original.secondaryTree)
          expect(result.state.tabs).toHaveLength(tabs.length)
          expect(new Set(result.state.tabs.map((item) => item.id))).toEqual(new Set(tabs.map((item) => item.id)))
          const expected = tabs.filter((item) => item.id !== selected)
          const insertion = before === null ? expected.length : expected.findIndex((item) => item.id === before)
          expected.splice(insertion, 0, tabs.find((item) => item.id === selected)!)
          expect(result.state.tabs).toEqual(expected)
          expect(original.tabs).toEqual(tabs)
        },
      ),
      LIMIT,
    )
  })
})
