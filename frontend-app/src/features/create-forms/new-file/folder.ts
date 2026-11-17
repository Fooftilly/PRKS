import type { FolderRow, NewFileWindow } from './runtime'

/** Same copy for the early (blur) check and the Create check. */
export const FOLDER_NOT_PICKED = 'Choose a folder from the list, create this folder, or select Uncategorized.'
export const FOLDER_GONE = 'This folder no longer exists. Choose another folder.'

/**
 * The real top-level folder titled "Uncategorized", once it exists. The
 * picker then never shows a synthetic default row next to it.
 */
export function canonicalUncategorizedFolder(folders: readonly FolderRow[]): FolderRow | null {
  return (
    folders.find(
      (folder) =>
        !folder.parent_id &&
        String(folder.title || '')
          .trim()
          .toLowerCase() === 'uncategorized',
    ) || null
  )
}

function folderIdFromHash(w: NewFileWindow, hash: string): string {
  if (typeof w.prksParseRoute === 'function') {
    const route = w.prksParseRoute(hash)
    if (route && route.name === 'folder-detail' && route.params && route.params.folderId) {
      return String(route.params.folderId)
    }
  }
  if (hash.startsWith('#/folders/')) {
    const part = hash.split('/')[2] || ''
    // A malformed hash must not stop New File opening: no default then.
    try {
      return decodeURIComponent(part.split('?')[0] || '')
    } catch {
      return ''
    }
  }
  return ''
}

/**
 * The Folder New File defaults to. The focused pane wins over Main's URL: a
 * focused tab that is not a Folder means Uncategorized, not "inherit Main".
 * The location hash is only a fallback when there is no focused context.
 */
export function defaultFolderId(w: NewFileWindow, hash: string): string {
  if (typeof w.prksFocusedRouteRecord === 'function') {
    const route = w.prksFocusedRouteRecord()
    if (route) {
      if (route.name === 'folder-detail' && route.params && route.params.folderId) {
        return String(route.params.folderId)
      }
      return ''
    }
  }
  return folderIdFromHash(w, String(hash || ''))
}

export interface FolderChoice {
  /** The transport id, or '' for the server's default. */
  id: string
  label: string
  /** True for the committed empty default (no real Uncategorized yet). */
  isDefault: boolean
}

/**
 * What the Folder field shows for an id. With no id: the real Uncategorized
 * when it exists (so display and stored destination agree), else the
 * committed empty default the server files as Uncategorized.
 */
export function folderChoiceFor(
  folderId: string,
  folders: readonly FolderRow[],
  label: (folder: FolderRow) => string,
): FolderChoice {
  const id = String(folderId || '').trim()
  if (!id) {
    const canonical = canonicalUncategorizedFolder(folders)
    if (canonical) return { id: canonical.id, label: label(canonical), isDefault: false }
    return { id: '', label: 'Uncategorized', isDefault: true }
  }
  const row = folders.find((folder) => String(folder.id) === id)
  return { id, label: row ? label(row) : id, isDefault: false }
}
