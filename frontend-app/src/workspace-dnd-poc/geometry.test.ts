import { describe, expect, it } from 'vitest'
import { computeEdgeZone, computeReorderIndex } from './geometry'

describe('workspace-dnd-poc geometry', () => {
  const RECT = { left: 100, top: 200, width: 300, height: 150 }

  it('treats the center as no-drop', () => {
    expect(computeEdgeZone(RECT, 250, 275)).toBeNull()
  })

  it('picks the nearest edge band', () => {
    expect(computeEdgeZone(RECT, 105, 275)).toBe('left')
    expect(computeEdgeZone(RECT, 395, 275)).toBe('right')
    expect(computeEdgeZone(RECT, 250, 205)).toBe('above')
    expect(computeEdgeZone(RECT, 250, 345)).toBe('below')
  })

  it('is deterministic at corners', () => {
    const square = { left: 0, top: 0, width: 200, height: 200 }
    expect(computeEdgeZone(square, 10, 30)).toBe('left')
    expect(computeEdgeZone(square, 30, 10)).toBe('above')
  })

  it('computes tab reorder insertion by midpoint', () => {
    const rects = [
      { id: 'A', left: 0, right: 60 },
      { id: 'B', left: 60, right: 120 },
      { id: 'C', left: 120, right: 180 },
    ]
    expect(computeReorderIndex(rects, 0)).toBe(0)
    expect(computeReorderIndex(rects, 31)).toBe(1)
    expect(computeReorderIndex(rects, 10000)).toBe(3)
    expect(computeReorderIndex([], 50)).toBe(0)
  })
})
