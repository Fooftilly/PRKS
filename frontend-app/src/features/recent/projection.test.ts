import { describe, expect, it } from 'vitest'
import { acceptRecentRows, buildRecentProjection, recentOpenedSubtitle } from './projection'

describe('Recent projection', () => {
  it('keeps coordinator order and drops non-rows', () => {
    const rows = acceptRecentRows([
      { id: 'b', last_opened_at: '2020-01-02T00:00:00.000Z' },
      null,
      'nope',
      { id: 'a', last_opened_at: '2020-01-03T00:00:00.000Z' },
    ])
    expect(rows.map((row) => row.id)).toEqual(['b', 'a'])
  })

  it('formats the legacy last-opened subtitle', () => {
    expect(recentOpenedSubtitle('')).toBe('Last opened: Unknown')
    expect(recentOpenedSubtitle(null)).toBe('Last opened: Unknown')
    const stamped = recentOpenedSubtitle('2020-01-02T03:04:05.000Z')
    expect(stamped.startsWith('Last opened: ')).toBe(true)
    expect(stamped).not.toBe('Last opened: Unknown')
  })

  it('records offline provenance without sorting', () => {
    const projection = buildRecentProjection({
      rows: [{ id: '2' }, { id: '1' }],
      offlineCached: true,
      generation: 4,
    })
    expect(projection.rows.map((row) => row.id)).toEqual(['2', '1'])
    expect(projection.offlineCached).toBe(true)
    expect(projection.generation).toBe(4)
  })
})
