import { describe, expect, it } from 'vitest'
import {
  effectiveFolderTitle,
  folderTitleConflicts,
  folderTitleNormKey,
} from './folder-rules'

describe('folder title rules', () => {
  it('treats a blank title as Untitled Folder', () => {
    expect(effectiveFolderTitle('  ')).toBe('Untitled Folder')
    expect(effectiveFolderTitle('Notes')).toBe('Notes')
  })

  it('matches titles within one parent only', () => {
    const folders = [
      { title: 'Notes', parent_id: '' },
      { title: 'Notes', parent_id: 'parent-1' },
    ]
    expect(folderTitleConflicts(' notes ', folders, '')).toBe(true)
    expect(folderTitleConflicts('Notes', folders, 'parent-1')).toBe(true)
    expect(folderTitleConflicts('Notes', folders, 'other')).toBe(false)
    expect(folderTitleConflicts('', folders, '')).toBe(false)
    expect(folderTitleConflicts('', [{ title: 'Untitled Folder', parent_id: '' }], '')).toBe(true)
    expect(folderTitleConflicts('', folders, 'parent-1')).toBe(false)
    expect(folderTitleNormKey('  A  ')).toBe('a')
  })
})
