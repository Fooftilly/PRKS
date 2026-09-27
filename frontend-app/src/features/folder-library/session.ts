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
import type { FolderIntentOwner } from './intents'
import {
  buildFolderLibraryProjection,
  type FolderLibraryProjection,
} from './projection'
import type { FolderLibraryRouteInstance } from './route'
import type { FolderLibraryAvailability } from './types'

const FOLDER_LIBRARY_FEATURE = 'folder-library'

/** Set by the route coordinator before beginRoute when staying on Folder Library. */
export const FOLDER_LIBRARY_RETAIN_SURFACE_KEY = '__prksRetainFolderLibrarySurface'
const FOLDER_LIBRARY_CLEANUP_ARMED_KEY = '__prksFolderLibraryCleanupArmed'

type FolderLibraryOwner = RouteSurfaceOwner &
  FolderIntentOwner & {
    [FOLDER_LIBRARY_RETAIN_SURFACE_KEY]?: boolean
    [FOLDER_LIBRARY_CLEANUP_ARMED_KEY]?: boolean
  }

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
  contentRoot?: HTMLElement | null
  availability?: FolderLibraryAvailability
  folders?: unknown
  offlineCached?: boolean
  generation?: number
  shell?: boolean
}

export function presentFolderLibrary(input: FolderLibraryPresentInput): void {
  if (!input || !input.owner || typeof input.owner !== 'object') return
  const availability: FolderLibraryAvailability =
    input.availability === 'unavailable' ? 'unavailable' : 'ready'
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
    armBeginRouteCleanup: false,
    render: (generation) => {
      const projection: FolderLibraryProjection = buildFolderLibraryProjection({
        availability,
        folders: input.folders,
        offlineCached: input.offlineCached,
        generation,
      })
      return createVNode(
        FolderLibraryIntentsProvider,
        { owner: input.owner, generation },
        {
          default: () =>
            createVNode(FolderLibraryRoute, {
              projection,
              contentRoot: input.contentRoot ?? input.host.parentElement,
            }),
        },
      )
    },
  })
  armFolderLibraryOwnerCleanup(input.owner)
}

export function dismissFolderLibrary(owner: object | null | undefined): void {
  dismissRouteSurface(owner)
}

export function resetFolderLibrarySessionForTests(): void {
  resetRouteSurfaceForTests()
}

function isFolderLibraryEarlyRequest(
  value: unknown,
): value is Omit<FolderLibraryPresentInput, 'host'> & { feature: typeof FOLDER_LIBRARY_FEATURE } {
  if (!value || typeof value !== 'object') return false
  const record = value as Partial<FolderLibraryPresentInput> & { feature?: unknown }
  return record.feature === FOLDER_LIBRARY_FEATURE && !!record.owner
}

export function registerFolderLibraryBridge(target: Window = window): void {
  target.prksVuePresentFolderLibrary = presentFolderLibrary
  target.prksVueDismissFolderLibrary = dismissFolderLibrary
  registerEarlyRoutePresenter(
    FOLDER_LIBRARY_FEATURE,
    (request, host) => {
      if (!isFolderLibraryEarlyRequest(request)) return false
      const { owner, contentRoot, availability, folders, offlineCached, generation, shell } = request
      presentFolderLibrary({
        owner,
        host,
        contentRoot,
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
