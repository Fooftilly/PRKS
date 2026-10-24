import { describe, expect, it } from 'vitest'
import { recentlyAddedDateLabel, recentlyAddedMatchesQuery } from './recently-added'
import type { FolderRow, RecentlyAddedWork } from './types'

const folders: FolderRow[] = [
  { id: 'f1', title: 'Critical Theory', parent_id: null, work_count: 1, child_count: 0 },
]

describe('recently added helpers', () => {
  it('matches bibliographic fields, status, type, and folder title', () => {
    const work: RecentlyAddedWork = {
      id: 'w1',
      title: 'Minima Moralia',
      linked_authors: 'Adorno',
      year: 1951,
      publisher: 'Suhrkamp',
      doc_type: 'book',
      folder_id: 'f1',
    }
    expect(recentlyAddedMatchesQuery(work, '', folders)).toBe(true)
    expect(recentlyAddedMatchesQuery(work, 'adorno', folders)).toBe(true)
    expect(recentlyAddedMatchesQuery(work, '1951', folders)).toBe(true)
    expect(recentlyAddedMatchesQuery(work, 'suhrkamp', folders)).toBe(true)
    expect(recentlyAddedMatchesQuery(work, 'critical', folders)).toBe(true)
    expect(recentlyAddedMatchesQuery(work, 'benjamin', folders)).toBe(false)
    expect(recentlyAddedMatchesQuery({ ...work, folder_id: 'gone' }, 'critical', folders)).toBe(false)
  })

  it('formats a concise date and omits invalid ones', () => {
    expect(recentlyAddedDateLabel('')).toBe('')
    expect(recentlyAddedDateLabel('not a date')).toBe('')
    expect(recentlyAddedDateLabel('2026-09-05T10:00:00Z')).toMatch(/2026/)
  })
})
