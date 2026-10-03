import { describe, expect, it } from 'vitest'
import { publisherRows, publisherSearchHash, publisherStatsLabel } from './projection'

describe('Publishers projection', () => {
  it('keeps server order and derives the row labels', () => {
    const rows = publisherRows([
      { id: 'b', name: 'Beta', work_count: 2, aliases: ['B'] },
      { id: 'a', name: 'Ada & Co', work_count: 1, aliases: [] },
    ])
    expect(rows.map((row) => row.id)).toEqual(['b', 'a'])
    expect(rows[0]).toMatchObject({ workCount: 2, aliases: ['B'], stats: '2 files · 1 alias' })
    expect(rows[1]).toMatchObject({
      stats: '1 file',
      searchHash: '#/search?publisher=Ada%20%26%20Co',
      encodedName: 'Ada%20%26%20Co',
    })
  })

  it('formats the legacy file and alias line', () => {
    expect(publisherStatsLabel(1, 0)).toBe('1 file')
    expect(publisherStatsLabel(2, 0)).toBe('2 files')
    expect(publisherStatsLabel(1, 1)).toBe('1 file · 1 alias')
    expect(publisherStatsLabel(3, 2)).toBe('3 files · 2 aliases')
    expect(publisherSearchHash('Oxford University Press')).toBe(
      '#/search?publisher=Oxford%20University%20Press',
    )
  })
})
