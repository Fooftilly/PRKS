import { describe, expect, it } from 'vitest'
import {
  filterRecentlyAddedWorks,
  recentlyAddedDateLabel,
  recentlyAddedWorkMatchesQuery,
} from './recently-added'
import type { FolderLibraryItem } from './types'

const folders: FolderLibraryItem[] = [
  { id: 'F1', title: 'Philosophy', parent_id: null, child_count: 0, work_count: 1 },
]

describe('Recently added helpers', () => {
  it('formats concise added dates and matches folder titles', () => {
    expect(recentlyAddedDateLabel('not-a-date')).toBe('')
    const label = recentlyAddedDateLabel('2026-09-05T12:00:00.000Z')
    expect(label).toMatch(/2026/)
    const byId = new Map(folders.map((f) => [f.id, f]))
    expect(
      recentlyAddedWorkMatchesQuery(
        { id: 'W1', title: 'Essay', folder_id: 'F1' },
        'philo',
        byId,
      ),
    ).toBe(true)
    expect(
      recentlyAddedWorkMatchesQuery({ id: 'W1', title: 'Essay', folder_id: 'F1' }, 'zzz', byId),
    ).toBe(false)
  })

  it('filters effective rows by query', () => {
    const works = [
      { id: 'W1', title: 'Alpha', folder_id: 'F1' },
      { id: 'W2', title: 'Beta', folder_id: null },
    ]
    expect(filterRecentlyAddedWorks(works, 'alp', folders).map((w) => w.id)).toEqual(['W1'])
    expect(filterRecentlyAddedWorks(works, '', folders)).toHaveLength(2)
  })
})
