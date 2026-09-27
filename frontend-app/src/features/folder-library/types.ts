/**
 * Typed Folder Library shapes for Vue route projections.
 * Already-effective folder rows from the legacy coordinator — not a second store.
 */

export type FolderLibraryAvailability = 'ready' | 'unavailable'

/** One effective folder hierarchy row for the library index. */
export interface FolderRow {
  readonly id: string
  readonly title: string
  readonly parent_id: string | null
  readonly work_count: number
  readonly child_count: number
}

/** One effective recently-added work row (optional until loaded). */
export interface RecentlyAddedWork {
  readonly id: string
  readonly title?: string
  readonly author_text?: string
  readonly linked_authors?: string
  readonly primary_author?: string
  readonly primary_editor?: string
  readonly year?: string | number
  readonly published_date?: string
  readonly publisher?: string
  readonly status?: string
  readonly doc_type?: string
  readonly folder_id?: string | null
  readonly created_at?: string
}

export type FolderLibraryTab = 'folders' | 'recently-added'

export const FOLDER_LIBRARY_TAB_KEY = 'prks-folder-library-tab'
export const FOLDER_LIBRARY_FILTER_KEY = 'prks-folder-library-filter'
export const FOLDER_LIBRARY_FILES_FILTER_KEY = 'prks-folder-library-files-filter'
