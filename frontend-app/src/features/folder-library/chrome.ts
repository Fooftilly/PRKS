/**
 * Per-pane Folder tree chrome. The owning TabContext (`ctx.ui.folderTree`) is
 * the expand/collapse and Folder Library filter owner. Main and Secondary do
 * not share it. It survives navigation inside the pane and dies with the tab.
 * Session storage remains the brand-home clear channel and the fallback when
 * a pane has not written chrome yet.
 */

export interface FolderTreeChrome {
  expandedIds?: string[]
  libraryQuery?: string
}

export interface FolderTreeChromeOwner {
  ui?: {
    folderTree?: FolderTreeChrome
  }
}

export interface FolderTreeChromeRead {
  readonly expandedIds: readonly string[]
  /** Null when this pane has not stored a query yet. */
  readonly libraryQuery: string | null
}

export function readFolderTreeChrome(
  owner: FolderTreeChromeOwner | null | undefined,
): FolderTreeChromeRead {
  const stored = owner?.ui?.folderTree
  if (!stored) return { expandedIds: [], libraryQuery: null }
  const expandedIds = Array.isArray(stored.expandedIds)
    ? stored.expandedIds.map((id) => String(id))
    : []
  const libraryQuery = typeof stored.libraryQuery === 'string' ? stored.libraryQuery : null
  return { expandedIds, libraryQuery }
}

function ensureChrome(owner: FolderTreeChromeOwner | null | undefined): FolderTreeChrome | null {
  if (!owner?.ui) return null
  if (!owner.ui.folderTree) owner.ui.folderTree = {}
  return owner.ui.folderTree
}

export function writeFolderTreeExpanded(
  owner: FolderTreeChromeOwner | null | undefined,
  expandedIds: readonly string[],
): void {
  const chrome = ensureChrome(owner)
  if (!chrome) return
  chrome.expandedIds = [...expandedIds].map((id) => String(id))
}

export function writeFolderLibraryQuery(
  owner: FolderTreeChromeOwner | null | undefined,
  query: string,
): void {
  const chrome = ensureChrome(owner)
  if (!chrome) return
  chrome.libraryQuery = String(query ?? '')
}
