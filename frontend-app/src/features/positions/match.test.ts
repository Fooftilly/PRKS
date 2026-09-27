import { describe, expect, it } from 'vitest'
import {
  filterPositionIndexItems,
  matchPositionIndexItem,
  positionDescriptionExcerpt,
} from './match'
import type { PositionIndexItem } from './types'

function row(partial: Partial<PositionIndexItem> & { id: string }): PositionIndexItem {
  return { name: 'Position', description: '', ...partial }
}

describe('Position index match', () => {
  it('matches name or description locally and clips long excerpts', () => {
    const items = [
      row({ id: 'P1', name: 'Alpha', description: 'offline Position detail assertions' }),
      row({ id: 'P2', name: 'Beta', description: 'other' }),
    ]
    expect(matchPositionIndexItem(items[0]!, 'standard')).toBe(false)
    expect(filterPositionIndexItems(items, 'alpha')).toEqual([items[0]])
    expect(filterPositionIndexItems(items, 'detail assertions')).toEqual([items[0]])
    expect(filterPositionIndexItems(items, '  ')).toEqual(items)
    const long = `${'word '.repeat(40)}tail`
    const clip = positionDescriptionExcerpt(long)
    expect(clip.endsWith('…')).toBe(true)
    expect(clip.length).toBeLessThan(long.length)
  })
})
