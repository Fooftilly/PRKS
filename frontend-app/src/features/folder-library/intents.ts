import type { InjectionKey } from 'vue'
import type { FolderLibraryTab, RecentlyAddedWork } from './types'
import {
  FOLDER_LIBRARY_FILES_FILTER_KEY,
  FOLDER_LIBRARY_FILTER_KEY,
  FOLDER_LIBRARY_TAB_KEY,
} from './types'

export interface FolderIntentOwner {
  tabId?: string
  generation?: number
  isCurrent?: (generation: number) => boolean
}

export interface RecentlyAddedLoadResult {
  works: RecentlyAddedWork[] | null
  offlineCached: boolean
  unavailable: boolean
}

export interface FolderLibraryIntents {
  createFolder(initialTitle?: string): void
  openWorkModal(): void
  navigateFolder(folderId: string): void
  switchTab(tab: FolderLibraryTab): void
  loadRecentlyAdded(force?: boolean): Promise<RecentlyAddedLoadResult>
  toggleExpand(folderId: string): void
  toggleExpandAll(): void
  bindFolderOfflineState(contentRoot: HTMLElement | null): () => void
  scheduleGlance(root: HTMLElement | null): void
}

export const folderLibraryIntentsKey: InjectionKey<FolderLibraryIntents> = Symbol(
  'prks-folder-library-intents',
)

function ownsSurface(
  owner: FolderIntentOwner | null | undefined,
  generation: number,
): boolean {
  return !!(owner && typeof owner.isCurrent === 'function' && owner.isCurrent(generation))
}

export function readFolderLibraryTabFromStorage(): FolderLibraryTab {
  try {
    const saved = sessionStorage.getItem(FOLDER_LIBRARY_TAB_KEY)
    return saved === 'recently-added' ? 'recently-added' : 'folders'
  } catch {
    return 'folders'
  }
}

export function persistFolderLibraryTab(tab: FolderLibraryTab): void {
  try {
    sessionStorage.setItem(FOLDER_LIBRARY_TAB_KEY, tab)
  } catch {
    /* ignore */
  }
}

export function readFolderFilterFromStorage(): string {
  try {
    return sessionStorage.getItem(FOLDER_LIBRARY_FILTER_KEY) || ''
  } catch {
    return ''
  }
}

export function persistFolderFilter(query: string): void {
  try {
    sessionStorage.setItem(FOLDER_LIBRARY_FILTER_KEY, query)
  } catch {
    /* ignore */
  }
}

export function readRecentlyAddedFilterFromStorage(): string {
  try {
    return sessionStorage.getItem(FOLDER_LIBRARY_FILES_FILTER_KEY) || ''
  } catch {
    return ''
  }
}

export function persistRecentlyAddedFilter(query: string): void {
  try {
    sessionStorage.setItem(FOLDER_LIBRARY_FILES_FILTER_KEY, query)
  } catch {
    /* ignore */
  }
}

/**
 * Typed intents → existing durable / navigation / offline helpers on window.
 */
export function browserFolderLibraryIntents(
  owner: FolderIntentOwner | null | undefined,
  generation: number,
): FolderLibraryIntents {
  return {
    createFolder(initialTitle) {
      const fn = window.prksOpenFolderModalFromLibrarySearch
      if (typeof fn === 'function') fn(initialTitle)
    },

    openWorkModal() {
      if (typeof window.openModal === 'function') window.openModal('work-modal')
    },

    navigateFolder(folderId) {
      const id = String(folderId || '').trim()
      if (!id) return
      const hash = `#/folders/${encodeURIComponent(id)}`
      if (typeof window.prksNavigate === 'function') {
        window.prksNavigate(hash, { tabId: owner?.tabId })
      }
    },

    switchTab(tab) {
      const want = tab === 'recently-added' ? 'recently-added' : 'folders'
      persistFolderLibraryTab(want)
    },

    async loadRecentlyAdded(force) {
      if (!ownsSurface(owner, generation)) {
        return { works: null, offlineCached: false, unavailable: true }
      }
      const shouldForce = !!force || window.__prksRecentlyAddedDirty === true
      const domainGen =
        typeof window.prksOfflineDomainGeneration === 'function'
          ? window.prksOfflineDomainGeneration('recently-added')
          : null
      if (typeof window.prksRefreshPendingWorkMetadata === 'function') {
        await window.prksRefreshPendingWorkMetadata()
      }
      const offlineRecentlyAdded =
        typeof window.prksOfflineRecentlyAddedFetch === 'function'
          ? await window.prksOfflineRecentlyAddedFetch()
          : null
      const works =
        typeof window.prksResolveOfflineRecentlyAdded === 'function'
          ? window.prksResolveOfflineRecentlyAdded(offlineRecentlyAdded)
          : null
      if (!works) {
        return { works: null, offlineCached: false, unavailable: true }
      }
      const offlineCached = !!(offlineRecentlyAdded && offlineRecentlyAdded.source === 'cache')
      void domainGen
      void shouldForce
      const rows = Array.isArray(works)
        ? works
            .map((w) => {
              if (!w || typeof w !== 'object') return null
              const id = String((w as { id?: unknown }).id || '').trim()
              if (!id) return null
              return w as RecentlyAddedWork
            })
            .filter((w): w is RecentlyAddedWork => w != null)
        : []
      window.__prksRecentlyAddedDirty = false
      return { works: rows, offlineCached, unavailable: false }
    },

    toggleExpand(folderId) {
      if (typeof window.prksToggleFolderNode === 'function') {
        window.prksToggleFolderNode(folderId)
      }
    },

    toggleExpandAll() {
      if (typeof window.prksToggleAllFolderNodes === 'function') {
        window.prksToggleAllFolderNodes()
      }
    },

    bindFolderOfflineState(contentRoot) {
      const fn = window.prksBindFolderOfflineState
      if (typeof fn !== 'function' || !contentRoot) return () => {}
      fn(owner, contentRoot)
      return () => {
        const dispose = (contentRoot as HTMLElement & { __prksFolderOfflineDispose?: () => void })
          .__prksFolderOfflineDispose
        if (typeof dispose === 'function') dispose()
      }
    },

    scheduleGlance(root) {
      const fn = window.prksScheduleFolderLibraryGlance
      if (typeof fn === 'function' && root) fn(root)
    },
  }
}
