import type { FolderLibraryTab } from './types'

export const FOLDER_LIBRARY_TAB_KEY = 'prks-folder-library-tab'
export const FOLDER_LIBRARY_FILTER_KEY = 'prks-folder-library-filter'
export const FOLDER_LIBRARY_FILES_FILTER_KEY = 'prks-folder-library-files-filter'

export function readFolderLibraryTab(): FolderLibraryTab {
  try {
    const saved = sessionStorage.getItem(FOLDER_LIBRARY_TAB_KEY)
    return saved === 'recently-added' ? 'recently-added' : 'folders'
  } catch {
    return 'folders'
  }
}

export function writeFolderLibraryTab(tab: FolderLibraryTab): void {
  try {
    sessionStorage.setItem(FOLDER_LIBRARY_TAB_KEY, tab)
  } catch {
    /* ignore */
  }
}

export function readFolderLibraryFilter(): string {
  try {
    return sessionStorage.getItem(FOLDER_LIBRARY_FILTER_KEY) || ''
  } catch {
    return ''
  }
}

export function writeFolderLibraryFilter(query: string): void {
  try {
    sessionStorage.setItem(FOLDER_LIBRARY_FILTER_KEY, query)
  } catch {
    /* ignore */
  }
}

export function readFolderLibraryFilesFilter(): string {
  try {
    return sessionStorage.getItem(FOLDER_LIBRARY_FILES_FILTER_KEY) || ''
  } catch {
    return ''
  }
}

export function writeFolderLibraryFilesFilter(query: string): void {
  try {
    sessionStorage.setItem(FOLDER_LIBRARY_FILES_FILTER_KEY, query)
  } catch {
    /* ignore */
  }
}
