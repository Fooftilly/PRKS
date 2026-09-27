import { describe, expect, it } from 'vitest'
import {
  acceptPositionDetail,
  acceptPositionIndexItems,
  buildPositionDetailProjection,
  buildPositionIndexProjection,
} from './projection'

describe('Position projections', () => {
  it('accepts effective index rows and drops invalid entries', () => {
    const items = acceptPositionIndexItems([
      { id: 'P1', name: 'Renamed', description: 'body' },
      { name: 'Missing id' },
      null,
    ])
    expect(items).toHaveLength(1)
    expect(items[0]).toMatchObject({ id: 'P1', name: 'Renamed', description: 'body' })
    expect(buildPositionIndexProjection({ availability: 'unavailable', items, generation: 1 })).toEqual({
      availability: 'unavailable',
      items: [],
      generation: 1,
    })
  })

  it('accepts a pending create with no arguments array and no server snapshot fields', () => {
    const detail = acceptPositionDetail({ id: 'P-local', name: 'Unsent', description: '' })
    expect(detail).toEqual({
      id: 'P-local',
      name: 'Unsent',
      description: '',
      arguments: [],
    })
  })

  it('keeps already-overlaid argument names and not-found / unavailable states', () => {
    const detail = acceptPositionDetail({
      id: 'P9',
      name: 'Nine',
      description: 'def',
      arguments: [
        {
          id: 'A1',
          name: 'Pending argument name',
          kind: 'stance',
          verdict_id: 'v1',
          verdict_label: 'Supports',
        },
      ],
    })
    expect(detail?.arguments[0]?.name).toBe('Pending argument name')
    expect(buildPositionDetailProjection({ availability: 'not-found', generation: 2 })).toEqual({
      availability: 'not-found',
      position: null,
      generation: 2,
    })
    expect(
      buildPositionDetailProjection({
        availability: 'unavailable',
        position: { id: 'P9', name: 'Hidden by deletion' },
        generation: 3,
      }),
    ).toEqual({
      availability: 'unavailable',
      position: null,
      generation: 3,
    })
  })
})
