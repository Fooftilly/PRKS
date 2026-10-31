export interface FolderTitleRow {
  id?: string
  title?: string | null
  parent_id?: string | null
}

export function folderTitleNormKey(value: string): string {
  return (value || '').trim().toLowerCase()
}

export function folderParentNormKey(parentId: string): string {
  return String(parentId || '').trim()
}

/** Empty titles are stored as Untitled Folder, so the duplicate check uses that name. */
export function effectiveFolderTitle(raw: string): string {
  const title = (raw || '').trim()
  return title || 'Untitled Folder'
}

export function folderTitleConflicts(
  raw: string,
  folders: readonly FolderTitleRow[],
  parentId: string,
): boolean {
  const key = folderTitleNormKey(effectiveFolderTitle(raw))
  const parentKey = folderParentNormKey(parentId)
  return folders.some(
    (folder) =>
      folderTitleNormKey(String(folder.title || '')) === key &&
      folderParentNormKey(String(folder.parent_id || '')) === parentKey,
  )
}

export const FOLDER_TITLE_CONFLICT = 'A folder with this name already exists in this location.'
