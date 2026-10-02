import { describe, expect, it } from 'vitest'
import {
  acceptPublisherRows,
  buildPublishersProjection,
  publisherSearchHash,
  publisherStatsLabel,
} from './projection'

describe('Publishers projection', () => {
  it('keeps coordinator order and drops rows without an id', () => {
    const rows = acceptPublisherRows([
      { id: 'b', name: 'Beta', work_count: 2, aliases: ['B'] },
      null,
      { name: 'Missing' },
      { id: '', name: 'Blank' },
      { id: 'a', name: 'Ada', work_count: 1, aliases: ['A', 4, 'Ada Press'] },
    ])
    expect(rows.map((row) => row.id)).toEqual(['b', 'a'])
    expect(rows[1]?.aliases).toEqual(['A', 'Ada Press'])
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

  it('reopens the alias dialog only for a publisher that is still in the list', () => {
    const open = buildPublishersProjection({
      publishers: [{ id: 'p1', name: 'OUP', work_count: 1, aliases: [] }],
      generation: 2,
      resume: { aliasPublisherId: 'p1' },
    })
    expect(open.openAliasPublisherId).toBe('p1')
    const closed = buildPublishersProjection({
      publishers: [{ id: 'p1', name: 'OUP', work_count: 1, aliases: [] }],
      generation: 2,
      resume: { aliasPublisherId: 'missing' },
    })
    expect(closed.openAliasPublisherId).toBeNull()
  })
})
