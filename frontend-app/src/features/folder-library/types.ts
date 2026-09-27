/**
 * Typed Folder Library shapes for Vue route projections.
 * Already-effective rows from the legacy coordinator — not a second
 * hierarchy or durable-operation store.
 */

export interface FolderLibraryItem {
  readonly id: string
  readonly title: string
  readonly parent_id: string | null
  readonly child_count: number
  readonly work_count: number | null
  readonly description?: string
}

export type FolderLibraryAvailability = 'ready' | 'unavailable'

export type FolderLibraryTab = 'folders' | 'recently-added'

/** Ephemeral Recently-added work row used only for card paint + local filter. */
export interface RecentlyAddedWork {
  readonly id: string
  readonly title?: string
  readonly created_at?: string
  readonly folder_id?: string | null
  readonly author_text?: string
  readonly linked_authors?: string
  readonly primary_author?: string
  readonly primary_editor?: string
  readonly year?: string | number
  readonly published_date?: string
  readonly publisher?: string
  readonly status?: string
  readonly doc_type?: string
  readonly abstract_excerpt?: string
  readonly abstract?: string
  readonly [key: string]: unknown
}
