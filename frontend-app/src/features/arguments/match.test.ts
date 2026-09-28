import { describe, expect, it } from 'vitest'
import {
  argumentIndexHash,
  argumentKindUi,
  defaultArgumentVerdict,
  filterArgumentIndexItems,
  matchArgumentIndexItem,
  normalizeArgumentKindFilter,
} from './match'
import type { ArgumentIndexItem } from './types'

function item(partial: Partial<ArgumentIndexItem> & { id: string }): ArgumentIndexItem {
  return {
    name: partial.id,
    kind: 'argument',
    main_text: '',
    response_count: 0,
    targets: [],
    sources: [],
    ...partial,
  }
}

describe('Argument index match', () => {
  const rows = [
    item({
      id: 'A1',
      name: 'Alienation',
      kind: 'stance',
      main_text: 'Labor becomes a commodity',
      targets: [{ id: 'P1', name: 'Historical materialism', type: 'position' }],
      sources: [{ work_id: 'W1', work_title: 'Economic Manuscripts' }],
    }),
    item({ id: 'A2', name: 'Unrelated Argument', main_text: 'Something else' }),
  ]

  it('matches name and main text and keeps kind, target, and source matches', () => {
    expect(matchArgumentIndexItem(rows[0], 'alien')).toBe(true)
    expect(matchArgumentIndexItem(rows[0], 'commodity')).toBe(true)
    expect(matchArgumentIndexItem(rows[0], 'stance')).toBe(true)
    expect(matchArgumentIndexItem(rows[0], 'materialism')).toBe(true)
    expect(matchArgumentIndexItem(rows[0], 'manuscripts')).toBe(true)
    expect(matchArgumentIndexItem(rows[1], 'commodity')).toBe(false)
    expect(filterArgumentIndexItems(rows, '  ')).toHaveLength(2)
  })

  it('builds kind-aware empty copy and canonical hashes', () => {
    expect(normalizeArgumentKindFilter('nope')).toBe('all')
    expect(argumentKindUi('argument').empty).toBe('No Arguments yet.')
    expect(argumentKindUi('stance').empty).toBe('No Stances yet.')
    expect(argumentKindUi('all').empty).toBe('No Arguments or Stances yet.')
    expect(argumentKindUi('stance').plural).toBe('Stances')
    expect(argumentIndexHash('all')).toBe('#/arguments')
    expect(argumentIndexHash('stance')).toBe('#/arguments?kind=stance')
    expect(defaultArgumentVerdict('stance')).toBe('holds')
    expect(defaultArgumentVerdict('argument')).toBe('supports')
  })
})
