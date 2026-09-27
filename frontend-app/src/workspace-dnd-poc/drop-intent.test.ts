import { describe, expect, it } from 'vitest'
import {
  dropIntentToCommand,
  pickNestedLeafHit,
  resolveDropIntent,
  type ResolveDropIntentInput,
} from './drop-intent'

function base(overrides: Partial<ResolveDropIntentInput> = {}): ResolveDropIntentInput {
  return {
    source: { kind: 'tab', tabId: 'B' },
    mainTabId: 'A',
    secondaryLeafTabIds: ['C'],
    hasSecondaryTree: true,
    narrowFallback: false,
    canAddSecondaryLeaf: true,
    sourceRouteSupportsTile: true,
    hit: null,
    ...overrides,
  }
}

describe('resolveDropIntent', () => {
  it('resolves tab reorder on the strip', () => {
    // Full order [A, B, C]; source B at index 1. Drop near start → before A (idx 0 ≠ 1).
    const intent = resolveDropIntent(
      base({
        hit: {
          kind: 'strip',
          x: 20,
          sourceIndex: 1,
          otherTabRects: [
            { id: 'A', left: 0, right: 60 },
            { id: 'C', left: 60, right: 120 },
          ],
        },
      }),
    )
    expect(intent).toEqual({ kind: 'tab-reorder', beforeTabId: 'A', index: 0 })
    expect(dropIntentToCommand({ kind: 'tab', tabId: 'B' }, intent, ['C'])).toEqual({
      type: 'reorder-tab',
      tabId: 'B',
      beforeTabId: 'A',
    })
  })

  it('rejects strip self-drop at the source insertion slot', () => {
    // Full order [A, B, C]; source B at 1. Drop between A and C → idx 1 === sourceIndex.
    expect(
      resolveDropIntent(
        base({
          hit: {
            kind: 'strip',
            x: 70,
            sourceIndex: 1,
            otherTabRects: [
              { id: 'A', left: 0, right: 60 },
              { id: 'C', left: 60, right: 120 },
            ],
          },
        }),
      ),
    ).toBeNull()
  })

  it('keeps valid Main tab reorders that change order', () => {
    // Full order [A, B, C]; source Main A at 0. Drop mid-C → idx 1 (before C) ≠ 0.
    const intent = resolveDropIntent(
      base({
        source: { kind: 'tab', tabId: 'A' },
        mainTabId: 'A',
        hit: {
          kind: 'strip',
          x: 70,
          sourceIndex: 0,
          otherTabRects: [
            { id: 'B', left: 0, right: 60 },
            { id: 'C', left: 60, right: 120 },
          ],
        },
      }),
    )
    expect(intent).toEqual({ kind: 'tab-reorder', beforeTabId: 'C', index: 1 })
  })

  it('parks a pane dropped on the strip', () => {
    const intent = resolveDropIntent(
      base({
        source: { kind: 'pane', tabId: 'C' },
        hit: { kind: 'strip', x: 10, sourceIndex: 0, otherTabRects: [] },
      }),
    )
    expect(intent).toEqual({ kind: 'park' })
    expect(dropIntentToCommand({ kind: 'pane', tabId: 'C' }, intent, ['C'])).toEqual({
      type: 'hide-leaf',
      tabId: 'C',
    })
  })

  it('resolves nested secondary-edge move without mutating state', () => {
    const rect = { left: 0, top: 0, width: 200, height: 200 }
    const intent = resolveDropIntent(
      base({
        source: { kind: 'pane', tabId: 'D' },
        secondaryLeafTabIds: ['C', 'D'],
        hit: { kind: 'leaf', tabId: 'C', rect, x: 10, y: 100 },
      }),
    )
    expect(intent).toMatchObject({
      kind: 'secondary-edge',
      tabId: 'C',
      zone: 'left',
      valid: true,
      axis: 'left-right',
      placement: 'first',
    })
    expect(dropIntentToCommand({ kind: 'pane', tabId: 'D' }, intent, ['C', 'D'])).toEqual({
      type: 'move-pane',
      sourceTabId: 'D',
      targetTabId: 'C',
      axis: 'left-right',
      placement: 'first',
    })
  })

  it('maps parked tab edge drop to split-leaf when under the pane cap', () => {
    const rect = { left: 0, top: 0, width: 200, height: 200 }
    const intent = resolveDropIntent(
      base({
        source: { kind: 'tab', tabId: 'B' },
        secondaryLeafTabIds: ['C'],
        hit: { kind: 'leaf', tabId: 'C', rect, x: 190, y: 100 },
      }),
    )
    expect(intent).toMatchObject({ kind: 'secondary-edge', zone: 'right', valid: true })
    expect(dropIntentToCommand({ kind: 'tab', tabId: 'B' }, intent, ['C'])).toEqual({
      type: 'split-leaf',
      targetTabId: 'C',
      newTabId: 'B',
      axis: 'left-right',
      placement: 'second',
    })
  })

  it('marks edge targets invalid at the pane cap', () => {
    const rect = { left: 0, top: 0, width: 200, height: 200 }
    const intent = resolveDropIntent(
      base({
        canAddSecondaryLeaf: false,
        hit: { kind: 'leaf', tabId: 'C', rect, x: 10, y: 100 },
      }),
    )
    expect(intent).toMatchObject({ kind: 'secondary-edge', valid: false, reason: 'cap' })
    expect(dropIntentToCommand({ kind: 'tab', tabId: 'B' }, intent, ['C'])).toBeNull()
  })

  it('rejects Main as a spatial source and leaf self-drop', () => {
    const rect = { left: 0, top: 0, width: 200, height: 200 }
    expect(
      resolveDropIntent(
        base({
          source: { kind: 'tab', tabId: 'A' },
          mainTabId: 'A',
          hit: { kind: 'leaf', tabId: 'C', rect, x: 10, y: 100 },
        }),
      ),
    ).toBeNull()
    expect(
      resolveDropIntent(
        base({
          source: { kind: 'pane', tabId: 'C' },
          hit: { kind: 'leaf', tabId: 'C', rect, x: 10, y: 100 },
        }),
      ),
    ).toBeNull()
  })

  it('offers empty-secondary only for parked tileable tabs', () => {
    const canvas = { left: 0, top: 0, width: 1000, height: 400 }
    expect(
      resolveDropIntent(
        base({
          hasSecondaryTree: false,
          secondaryLeafTabIds: [],
          hit: { kind: 'empty-secondary', canvasRect: canvas, x: 700, y: 100 },
        }),
      ),
    ).toEqual({ kind: 'secondary-empty', valid: true })
    expect(
      resolveDropIntent(
        base({
          hasSecondaryTree: false,
          sourceRouteSupportsTile: false,
          hit: { kind: 'empty-secondary', canvasRect: canvas, x: 700, y: 100 },
        }),
      ),
    ).toBeNull()
  })

  it('picks nested leaf hits in caller order and ignores center bands via resolve', () => {
    const leaves = [
      { tabId: 'C', rect: { left: 0, top: 0, width: 100, height: 100 } },
      { tabId: 'D', rect: { left: 100, top: 0, width: 100, height: 100 } },
    ]
    expect(pickNestedLeafHit(leaves, 150, 50, 'B')).toEqual({
      kind: 'leaf',
      tabId: 'D',
      rect: leaves[1].rect,
      x: 150,
      y: 50,
    })
    const center = resolveDropIntent(
      base({
        hit: {
          kind: 'leaf',
          tabId: 'C',
          rect: { left: 0, top: 0, width: 200, height: 200 },
          x: 100,
          y: 100,
        },
      }),
    )
    expect(center).toBeNull()
  })
})
