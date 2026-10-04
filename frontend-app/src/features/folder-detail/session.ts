import { createVNode } from 'vue'
import {
  dismissRouteSurface,
  presentRouteSurface,
  readRouteSurface,
  registerEarlyRoutePresenter,
  registerRouteWindowBridge,
  resetRouteSurfaceForTests,
  type RouteSurfaceOwner,
} from '../../route-surface/lifecycle'
import FolderDetailIntentsProvider from './FolderDetailIntentsProvider.vue'
import FolderDetailRoute from './FolderDetailRoute.vue'
import type { FolderDetailIntentOwner } from './intents'
import { buildFolderDetailProjection, type FolderDetailProjection } from './projection'
import type { FolderDetailRouteInstance } from './route'
import type { FolderDetailAvailability } from './types'

const FOLDER_DETAIL_FEATURE = 'folder-detail'

/** Set by the route coordinator before beginRoute when staying on Folder detail. */
export const FOLDER_DETAIL_RETAIN_SURFACE_KEY = '__prksRetainFolderDetailSurface'
const FOLDER_DETAIL_CLEANUP_ARMED_KEY = '__prksFolderDetailCleanupArmed'

type FolderDetailOwner = RouteSurfaceOwner &
  FolderDetailIntentOwner & {
    [FOLDER_DETAIL_RETAIN_SURFACE_KEY]?: boolean
    [FOLDER_DETAIL_CLEANUP_ARMED_KEY]?: boolean
  }

/**
 * Folder detail dismisses on leave/destroy, not on every beginRoute.
 * Folder→Folder sets FOLDER_DETAIL_RETAIN_SURFACE_KEY so the hierarchy shell
 * stays mounted. #303 B2 removes this retain bridge when TabContext resource
 * lifetime no longer depends on a side-effect tree writer.
 */
function armFolderDetailOwnerCleanup(owner: FolderDetailOwner): void {
  if (owner[FOLDER_DETAIL_CLEANUP_ARMED_KEY] || typeof owner.registerCleanup !== 'function') return
  owner[FOLDER_DETAIL_CLEANUP_ARMED_KEY] = true
  owner.registerCleanup(() => {
    owner[FOLDER_DETAIL_CLEANUP_ARMED_KEY] = false
    if (owner[FOLDER_DETAIL_RETAIN_SURFACE_KEY]) {
      armFolderDetailOwnerCleanup(owner)
      return
    }
    dismissRouteSurface(owner)
  })
}

export interface FolderDetailPresentInput {
  owner: FolderDetailOwner
  host: HTMLElement
  availability?: FolderDetailAvailability
  folder?: unknown
  folderId?: string
  offlineCached?: boolean
  preserveWorkspace?: boolean
  generation?: number
  shell?: boolean
}

function folderIdOf(folder: unknown, explicit: string | undefined): string {
  if (explicit) return explicit
  if (folder && typeof folder === 'object' && 'id' in folder) {
    return String((folder as { id?: unknown }).id || '')
  }
  return ''
}

export function presentFolderDetail(input: FolderDetailPresentInput): void {
  if (!input || !input.owner || typeof input.owner !== 'object') return
  const folder = input.folder
  const folderId = folderIdOf(folder, input.folderId)
  const offlineCached = input.offlineCached === true
  const preserveWorkspace = input.preserveWorkspace === true
  const availability = input.availability
  const route: Omit<FolderDetailRouteInstance, 'generation'> & { generation?: number } = {
    name: 'folder-detail',
    canonicalHash: folderId ? `#/folders/${encodeURIComponent(folderId)}` : '#/folders',
    params: { folderId },
    ownsMainShell: input.shell !== false,
    generation: input.generation,
  }
  // Folder→Folder retain keeps this host. Release this pane's body-mounted
  // preview while the previous thumb is still connected, before the next
  // Folder paint replaces it. Scoped to input.host so another pane's
  // connected preview stays up.
  const previous = readRouteSurface(input.owner)
  if (
    previous?.name === 'folder-detail' &&
    String(previous.params.folderId || '') !== folderId
  ) {
    window.prksReleaseWorkThumbPreview?.(input.host)
  }
  presentRouteSurface({
    owner: input.owner,
    host: input.host,
    route,
    armBeginRouteCleanup: false,
    render: (generation) => {
      const projection: FolderDetailProjection = buildFolderDetailProjection({
        availability,
        folder,
        folderId,
        offlineCached,
        preserveWorkspace,
        generation,
      })
      return createVNode(
        FolderDetailIntentsProvider,
        { owner: input.owner, generation },
        { default: () => createVNode(FolderDetailRoute, { projection }) },
      )
    },
  })
  armFolderDetailOwnerCleanup(input.owner)
}

export function resetFolderDetailSessionForTests(): void {
  resetRouteSurfaceForTests()
}

function isFolderDetailEarlyRequest(
  value: unknown,
): value is Omit<FolderDetailPresentInput, 'host'> & { feature: typeof FOLDER_DETAIL_FEATURE } {
  if (!value || typeof value !== 'object') return false
  const record = value as Partial<FolderDetailPresentInput> & { feature?: unknown }
  return record.feature === FOLDER_DETAIL_FEATURE && !!record.owner
}

export function registerFolderDetailBridge(target: Window = window): void {
  registerRouteWindowBridge(target)
  registerEarlyRoutePresenter(
    FOLDER_DETAIL_FEATURE,
    (request, host) => {
      if (!isFolderDetailEarlyRequest(request)) return false
      const {
        owner,
        availability,
        folder,
        folderId,
        offlineCached,
        preserveWorkspace,
        generation,
        shell,
      } = request
      presentFolderDetail({
        owner,
        host,
        availability,
        folder,
        folderId,
        offlineCached,
        preserveWorkspace,
        generation,
        shell,
      })
      return true
    },
    target,
  )
}
