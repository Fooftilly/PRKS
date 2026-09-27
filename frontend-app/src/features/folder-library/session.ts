import { createVNode } from 'vue'
import {
  dismissRouteSurface,
  presentRouteSurface,
  registerEarlyRoutePresenter,
  resetRouteSurfaceForTests,
  type RouteSurfaceOwner,
} from '../../route-surface/lifecycle'
import FolderLibraryIntentsProvider from './FolderLibraryIntentsProvider.vue'
import FolderLibraryRoute from './FolderLibraryRoute.vue'
import type { FolderLibraryIntentOwner } from './intents'
import {
  buildFolderLibraryProjection,
  type FolderLibraryProjection,
} from './projection'
import type { FolderLibraryRouteInstance } from './route'
import { releaseFolderLibraryBrowseResources } from './resources'
import type { FolderLibraryAvailability } from './types'

const FOLDERS_FEATURE = 'folders'

/** Set by the route coordinator before beginRoute when staying on Folder Library. */
export const FOLDER_LIBRARY_RETAIN_SURFACE_KEY = '__prksRetainFolderLibrarySurface'
const FOLDER_LIBRARY_CLEANUP_ARMED_KEY = '__prksFolderLibraryCleanupArmed'

type FolderLibraryOwner = RouteSurfaceOwner &
  FolderLibraryIntentOwner & {
    [FOLDER_LIBRARY_RETAIN_SURFACE_KEY]?: boolean
    [FOLDER_LIBRARY_CLEANUP_ARMED_KEY]?: boolean
    root?: HTMLElement | null
  }

/**
 * Folder Library dismisses on leave/destroy, not on every beginRoute.
 * Retained refreshes set FOLDER_LIBRARY_RETAIN_SURFACE_KEY so cleanup is a no-op.
 */
function armFolderLibraryOwnerCleanup(owner: FolderLibraryOwner): void {
  if (owner[FOLDER_LIBRARY_CLEANUP_ARMED_KEY] || typeof owner.registerCleanup !== 'function') return
  owner[FOLDER_LIBRARY_CLEANUP_ARMED_KEY] = true
  owner.registerCleanup(() => {
    owner[FOLDER_LIBRARY_CLEANUP_ARMED_KEY] = false
    if (owner[FOLDER_LIBRARY_RETAIN_SURFACE_KEY]) return
    dismissFolderLibrary(owner)
  })
}

export interface FolderLibraryPresentInput {
  owner: FolderLibraryOwner
  host: HTMLElement
  availability?: FolderLibraryAvailability
  folders?: unknown
  offlineCached?: boolean
  generation?: number
  shell?: boolean
}

/**
 * Paint one owner's Folder Library from an already-effective projection.
 * Owner session bookkeeping lives in the shared route-surface lifecycle.
 */
export function presentFolderLibrary(input: FolderLibraryPresentInput): void {
  if (!input || !input.owner || typeof input.owner !== 'object') return
  const availability: FolderLibraryAvailability =
    input.availability === 'unavailable' ? 'unavailable' : 'ready'
  const folders = input.folders
  const route: Omit<FolderLibraryRouteInstance, 'generation'> & { generation?: number } = {
    name: 'folders',
    canonicalHash: '#/folders',
    params: {},
    ownsMainShell: input.shell !== false,
    generation: input.generation,
  }
  presentRouteSurface({
    owner: input.owner,
    host: input.host,
    route,
    // Coordinator retains the host across Folders→Folders beginRoute; dismiss
    // is explicit on leave (and via retain-aware owner cleanup on destroy).
    armBeginRouteCleanup: false,
    render: (generation) => {
      const projection: FolderLibraryProjection = buildFolderLibraryProjection({
        availability,
        folders,
        offlineCached: input.offlineCached,
        generation,
      })
      return createVNode(
        FolderLibraryIntentsProvider,
        { owner: input.owner, generation },
        { default: () => createVNode(FolderLibraryRoute, { projection }) },
      )
    },
  })
  armFolderLibraryOwnerCleanup(input.owner)
}

/** Drop the Vue Folder Library tree owned by this pane. Other owners stay mounted. */
export function dismissFolderLibrary(owner: object | null | undefined): void {
  const pane = owner as FolderLibraryOwner | null | undefined
  const root = pane?.root || null
  if (root) releaseFolderLibraryBrowseResources(root)
  dismissRouteSurface(owner)
}

export function resetFolderLibrarySessionForTests(): void {
  resetRouteSurfaceForTests()
}

function isFolderLibraryEarlyRequest(
  value: unknown,
): value is Omit<FolderLibraryPresentInput, 'host'> & { feature: typeof FOLDERS_FEATURE } {
  if (!value || typeof value !== 'object') return false
  const record = value as Partial<FolderLibraryPresentInput> & { feature?: unknown }
  return record.feature === FOLDERS_FEATURE && !!record.owner
}

export function registerFolderLibraryBridge(target: Window = window): void {
  target.prksVuePresentFolderLibrary = presentFolderLibrary
  target.prksVueDismissFolderLibrary = dismissFolderLibrary
  registerEarlyRoutePresenter(
    FOLDERS_FEATURE,
    (request, host) => {
      if (!isFolderLibraryEarlyRequest(request)) return false
      const { owner, availability, folders, offlineCached, generation, shell } = request
      presentFolderLibrary({
        owner,
        host,
        availability,
        folders,
        offlineCached,
        generation,
        shell,
      })
      return true
    },
    target,
  )
}
