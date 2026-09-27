import { describe, expect, it } from 'vitest'
import {
  acceptFolderLibraryItem,
  acceptFolderLibraryItems,
  buildFolderLibraryProjection,
  folderLibraryCatalogGlanceParts,
} from './projection'

describe('Folder Library projection', () => {
  it('accepts effective folder rows and drops invalid entries', () => {
    expect(acceptFolderLibraryItem(null)).toBeNull()
    expect(acceptFolderLibraryItem({ title: 'No id' })).toBeNull()
    expect(
      acceptFolderLibraryItem({
        id: 'F1',
        title: ' Root ',
        parent_id: null,
        child_count: 2,
        work_count: 3,
      }),
    ).toEqual({
      id: 'F1',
      title: 'Root',
      parent_id: null,
      child_count: 2,
      work_count: 3,
      description: '',
    })
    expect(acceptFolderLibraryItems([{ id: 'a' }, { title: 'x' }, null])).toHaveLength(1)
  })

  it('clears folders when unavailable', () => {
    const projection = buildFolderLibraryProjection({
      availability: 'unavailable',
      folders: [{ id: 'F1', title: 'A' }],
      generation: 4,
    })
    expect(projection.availability).toBe('unavailable')
    expect(projection.folders).toEqual([])
    expect(projection.generation).toBe(4)
  })

  it('builds catalog glance parts without inventing zero work counts', () => {
    expect(
      folderLibraryCatalogGlanceParts([
        { id: '1', title: 'A', parent_id: null, child_count: 0, work_count: 1 },
        { id: '2', title: 'B', parent_id: null, child_count: 0, work_count: 2 },
      ]),
    ).toEqual(['2 folders', '3 files in folders'])
    expect(
      folderLibraryCatalogGlanceParts([
        { id: '1', title: 'A', parent_id: null, child_count: 0, work_count: null },
      ]),
    ).toEqual(['1 folder', null])
  })
})
