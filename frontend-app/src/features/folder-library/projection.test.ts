import { describe, expect, it } from 'vitest'
import { acceptFolderRows, buildFolderLibraryProjection } from './projection'

describe('Folder Library projections', () => {
  it('accepts effective folder rows and drops invalid entries', () => {
    const rows = acceptFolderRows([
      { id: 'F1', title: 'Root', parent_id: null, work_count: 2, child_count: 1 },
      { title: 'Missing id' },
      null,
    ])
    expect(rows).toHaveLength(1)
    expect(rows[0]).toMatchObject({ id: 'F1', title: 'Root', work_count: 2 })
    expect(
      buildFolderLibraryProjection({ availability: 'unavailable', folders: rows, generation: 1 }),
    ).toEqual({
      availability: 'unavailable',
      folders: [],
      offlineCached: false,
      generation: 1,
    })
  })

  it('builds ready projection with offline flag', () => {
    const projection = buildFolderLibraryProjection({
      availability: 'ready',
      folders: [{ id: 'F1', title: 'A' }],
      offlineCached: true,
      generation: 3,
    })
    expect(projection.folders).toHaveLength(1)
    expect(projection.offlineCached).toBe(true)
  })
})
