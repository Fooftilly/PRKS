import type { InjectionKey } from 'vue'
import type { FolderLibraryTab } from './types'

/** Owning TabContext fields Folder Library intents need. */
export interface FolderLibraryIntentOwner {
  tabId?: string
  generation?: number
  isCurrent?: (generation: number) => boolean
  root?: HTMLElement | null
}

export interface FolderLibraryIntents {
  createFolder(query?: string): void
  createWork(): void
  openFolder(folderId: string): void
  toggleFolderNode(folderId: string): void
  toggleAllFolderNodes(): void
  persistTab(tab: FolderLibraryTab): void
  persistFolderFilter(query: string): void
  persistFilesFilter(query: string): void
}

export const folderLibraryIntentsKey: InjectionKey<FolderLibraryIntents> = Symbol(
  'prks-folder-library-intents',
)

function ownerCurrent(
  owner: FolderLibraryIntentOwner | null | undefined,
  generation: number,
): boolean {
  return !!(owner && typeof owner.isCurrent === 'function' && owner.isCurrent(generation))
}

/**
 * Typed intents → existing Folder Library helpers / navigation.
 * No fetch() of folders:index; Recently-added load stays in the route view
 * via window offline helpers (same durable/effective path as legacy).
 */
export function browserFolderLibraryIntents(
  owner: FolderLibraryIntentOwner | null | undefined,
  generation: number,
): FolderLibraryIntents {
  return {
    createFolder(query) {
      if (!ownerCurrent(owner, generation)) return
      const open = window.prksOpenFolderModalFromLibrarySearch
      if (typeof open === 'function') open(String(query || ''))
      else if (typeof window.openModal === 'function') window.openModal('folder-modal')
    },

    createWork() {
      if (!ownerCurrent(owner, generation)) return
      if (typeof window.openModal === 'function') window.openModal('work-modal')
    },

    openFolder(folderId) {
      if (!ownerCurrent(owner, generation)) return
      const id = String(folderId || '').trim()
      if (!id) return
      const nav = window.prksNavigate
      if (typeof nav !== 'function') return
      nav(`#/folders/${encodeURIComponent(id)}`, { tabId: owner?.tabId })
    },

    toggleFolderNode(folderId) {
      if (!ownerCurrent(owner, generation)) return
      const fn = window.prksToggleFolderNode
      if (typeof fn === 'function') fn(folderId)
    },

    toggleAllFolderNodes() {
      if (!ownerCurrent(owner, generation)) return
      const fn = window.prksToggleAllFolderNodes
      if (typeof fn === 'function') fn()
    },

    persistTab(tab) {
      if (!ownerCurrent(owner, generation)) return
      void tab
    },

    persistFolderFilter(query) {
      if (!ownerCurrent(owner, generation)) return
      void query
    },

    persistFilesFilter(query) {
      if (!ownerCurrent(owner, generation)) return
      void query
    },
  }
}
