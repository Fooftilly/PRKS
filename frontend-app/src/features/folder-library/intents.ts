import type { InjectionKey } from 'vue'
import {
  readFolderTreeChrome,
  writeFolderLibraryQuery,
  writeFolderTreeExpanded,
  type FolderTreeChromeOwner,
  type FolderTreeChromeRead,
} from './chrome'
import type { FolderLibraryTab, RecentlyAddedWork } from './types'
import {
  FOLDER_LIBRARY_FILES_FILTER_KEY,
  FOLDER_LIBRARY_FILTER_KEY,
  FOLDER_LIBRARY_TAB_KEY,
} from './types'

export interface FolderIntentOwner extends FolderTreeChromeOwner {
  tabId?: string
  generation?: number
  isCurrent?: (generation: number) => boolean
}

export interface RecentlyAddedLoadResult {
  works: RecentlyAddedWork[] | null
  offlineCached: boolean
  unavailable: boolean
  generation: unknown
  pendingGeneration: unknown
  reused: boolean
}

export interface RecentlyAddedCacheHint {
  works: RecentlyAddedWork[] | null
  generation: unknown
  pendingGeneration: unknown
  offlineCached: boolean
}

export interface FolderLibraryIntents {
  createFolder(initialTitle?: string): void
  openWorkModal(): void
  navigateFolder(folderId: string): void
  switchTab(tab: FolderLibraryTab): void
  readTreeChrome(): FolderTreeChromeRead
  writeExpanded(expandedIds: readonly string[]): void
  writeLibraryQuery(query: string): void
  loadRecentlyAdded(
    force: boolean | undefined,
    cache: RecentlyAddedCacheHint,
  ): Promise<RecentlyAddedLoadResult>
  bindFolderOfflineState(contentRoot: HTMLElement | null): () => void
  scheduleGlance(
    root: HTMLElement | null,
    options?: {
      folders?: readonly unknown[]
      recentlyAddedWorks?: RecentlyAddedWork[] | null
      onParts?: (parts: readonly unknown[]) => void
    },
  ): void
  subscribeMetadataOverlay(onChange: () => void): () => void
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

function normalizeWorks(works: unknown): RecentlyAddedWork[] {
  if (!Array.isArray(works)) return []
  return works
    .map((w) => {
      if (!w || typeof w !== 'object') return null
      const id = String((w as { id?: unknown }).id || '').trim()
      if (!id) return null
      return w as RecentlyAddedWork
    })
    .filter((w): w is RecentlyAddedWork => w != null)
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

/**
 * Session storage is one browser-tab value. The first Folder Library mount in
 * this page may restore it. Every later pane starts from its own TabContext
 * chrome, not from a query another pane already wrote.
 */
let sessionFolderFilterClaimed = false

export function claimInitialLibraryQuery(): string | null {
  if (sessionFolderFilterClaimed) return null
  sessionFolderFilterClaimed = true
  return readFolderFilterFromStorage()
}

export function resetSessionFolderFilterClaimForTests(): void {
  sessionFolderFilterClaimed = false
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

    readTreeChrome() {
      return readFolderTreeChrome(owner)
    },

    writeExpanded(expandedIds) {
      writeFolderTreeExpanded(owner, expandedIds)
    },

    writeLibraryQuery(query) {
      writeFolderLibraryQuery(owner, query)
    },

    async loadRecentlyAdded(force, cache) {
      if (!ownsSurface(owner, generation)) {
        return {
          works: null,
          offlineCached: false,
          unavailable: true,
          generation: null,
          pendingGeneration: null,
          reused: false,
        }
      }
      const domainGen =
        typeof window.prksOfflineDomainGeneration === 'function'
          ? window.prksOfflineDomainGeneration('recently-added')
          : null
      const pendingGeneration =
        typeof window.prksPendingWorkMetadataGeneration === 'function'
          ? window.prksPendingWorkMetadataGeneration()
          : null
      const shouldForce = !!force
      const memoryStale = cache.generation !== domainGen
      const overlayStale = cache.pendingGeneration !== pendingGeneration

      if (!shouldForce && !memoryStale && Array.isArray(cache.works)) {
        if (overlayStale && typeof window.prksRefreshPendingWorkMetadata === 'function') {
          await window.prksRefreshPendingWorkMetadata()
        }
        return {
          works: cache.works,
          offlineCached: cache.offlineCached,
          unavailable: false,
          generation: domainGen,
          pendingGeneration:
            typeof window.prksPendingWorkMetadataGeneration === 'function'
              ? window.prksPendingWorkMetadataGeneration()
              : pendingGeneration,
          reused: true,
        }
      }

      if (typeof window.prksRefreshPendingWorkMetadata === 'function') {
        await window.prksRefreshPendingWorkMetadata()
      }
      if (!ownsSurface(owner, generation)) {
        return {
          works: null,
          offlineCached: false,
          unavailable: true,
          generation: null,
          pendingGeneration: null,
          reused: false,
        }
      }
      const offlineRecentlyAdded =
        typeof window.prksOfflineRecentlyAddedFetch === 'function'
          ? await window.prksOfflineRecentlyAddedFetch()
          : null
      if (!ownsSurface(owner, generation)) {
        return {
          works: null,
          offlineCached: false,
          unavailable: true,
          generation: null,
          pendingGeneration: null,
          reused: false,
        }
      }
      const works =
        typeof window.prksResolveOfflineRecentlyAdded === 'function'
          ? window.prksResolveOfflineRecentlyAdded(offlineRecentlyAdded)
          : null
      if (!works) {
        return {
          works: null,
          offlineCached: false,
          unavailable: true,
          generation: null,
          pendingGeneration: null,
          reused: false,
        }
      }
      const offlineCached = !!(offlineRecentlyAdded && offlineRecentlyAdded.source === 'cache')
      const rows = normalizeWorks(works)
      return {
        works: rows,
        offlineCached,
        unavailable: false,
        generation:
          typeof window.prksOfflineDomainGeneration === 'function'
            ? window.prksOfflineDomainGeneration('recently-added')
            : domainGen,
        pendingGeneration:
          typeof window.prksPendingWorkMetadataGeneration === 'function'
            ? window.prksPendingWorkMetadataGeneration()
            : pendingGeneration,
        reused: false,
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

    scheduleGlance(root, options) {
      const fn = window.prksScheduleFolderLibraryGlance
      if (typeof fn !== 'function' || !root) return
      if (!options) {
        fn(root)
        return
      }
      fn(root, {
        folders: options.folders ? [...options.folders] : undefined,
        recentlyAddedWorks: Array.isArray(options.recentlyAddedWorks)
          ? [...options.recentlyAddedWorks]
          : (options.recentlyAddedWorks ?? null),
        onParts: options.onParts,
      })
    },

    subscribeMetadataOverlay(onChange) {
      const sync = window.prksSync
      if (!sync || typeof sync.subscribe !== 'function') return () => {}
      return sync.subscribe(() => {
        void (async () => {
          try {
            if (typeof window.prksRefreshPendingWorkMetadata === 'function') {
              await window.prksRefreshPendingWorkMetadata()
            }
          } finally {
            onChange()
          }
        })().catch(() => {})
      })
    },
  }
}
