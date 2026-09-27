import { describe, expect, it } from 'vitest'
import { filterConceptIndexItems, matchConceptIndexItem, normalizeConceptSearchQuery } from './match'
import type { ConceptIndexItem } from './types'

function item(partial: Partial<ConceptIndexItem> & { id: string; name: string }): ConceptIndexItem {
  return {
    aliases: [],
    parents: [],
    subconcept_count: 0,
    mention_count: 0,
    ...partial,
  }
}

describe('Concept index search', () => {
  it('normalizes and matches name, aliases, and parents', () => {
    expect(normalizeConceptSearchQuery('  Foo ')).toBe('foo')
    const row = item({
      id: 'C1',
      name: 'Justice',
      aliases: ['Fairness'],
      parents: [{ id: 'P1', name: 'Ethics' }],
    })
    expect(matchConceptIndexItem(row, 'just')).toBe(true)
    expect(matchConceptIndexItem(row, 'fair')).toBe(true)
    expect(matchConceptIndexItem(row, 'eth')).toBe(true)
    expect(matchConceptIndexItem(row, 'xyz')).toBe(false)
    expect(filterConceptIndexItems([row], 'fair')).toHaveLength(1)
    expect(filterConceptIndexItems([row], 'xyz')).toHaveLength(0)
  })
})
