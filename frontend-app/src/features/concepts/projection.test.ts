import { describe, expect, it } from 'vitest'
import {
  acceptConceptDetail,
  acceptConceptIndexItems,
  buildConceptDetailProjection,
  buildConceptIndexProjection,
} from './projection'

describe('Concept projections', () => {
  it('accepts effective index rows and drops invalid entries', () => {
    const items = acceptConceptIndexItems([
      { id: 'C1', name: 'One', aliases: ['a'], parents: [{ id: 'P', name: 'Parent' }], subconcept_count: 2, mention_count: 3 },
      { name: 'Missing id' },
      null,
    ])
    expect(items).toHaveLength(1)
    expect(items[0]).toMatchObject({ id: 'C1', name: 'One', subconcept_count: 2, mention_count: 3 })
    expect(buildConceptIndexProjection({ availability: 'unavailable', items, generation: 1 })).toEqual({
      availability: 'unavailable',
      items: [],
      generation: 1,
    })
  })

  it('accepts effective detail and not-found / unavailable states', () => {
    const detail = acceptConceptDetail({
      id: 'C9',
      name: 'Nine',
      description: 'def',
      aliases: ['n'],
      parents: [],
      children: [{ id: 'C10', name: 'Ten' }],
      mentions: [{ work_id: 'W1', title: 'Work', occurrences: [{ snippet: 'hi' }] }],
      mention_count: 1,
    })
    expect(detail?.children[0]?.name).toBe('Ten')
    expect(buildConceptDetailProjection({ availability: 'not-found', generation: 2 })).toEqual({
      availability: 'not-found',
      concept: null,
      generation: 2,
    })
    expect(buildConceptDetailProjection({ availability: 'ready', concept: detail, generation: 3 }).concept?.id).toBe(
      'C9',
    )
  })
})
