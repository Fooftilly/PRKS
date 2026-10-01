import type { InjectionKey } from 'vue'

/** Owning TabContext fields Folder detail intents need. Not a second route model. */
export interface FolderDetailIntentOwner {
  tabId?: string
  generation?: number
  isCurrent?: (generation: number) => boolean
  root?: HTMLElement | null
  getEntity?: (type: string) => { id?: string } | null
  registerCleanup?: (fn: () => void) => void
}

export interface FolderDetailIntents {
  owner(): FolderDetailIntentOwner | null
  remove(folderId: string): Promise<void>
  createChild(folder: Record<string, unknown>): void
}

export const folderDetailIntentsKey: InjectionKey<FolderDetailIntents> = Symbol(
  'prks-folder-detail-intents',
)

function ownsFolder(
  owner: FolderDetailIntentOwner | null | undefined,
  generation: number,
  folderId: string,
): boolean {
  if (!owner || !folderId) return false
  if (typeof owner.isCurrent !== 'function' || !owner.isCurrent(generation)) return false
  const entity = owner.getEntity?.('folder')
  if (entity && entity.id != null && String(entity.id) !== String(folderId)) return false
  return true
}

/**
 * Mutations stay on the folder-domain wrappers.
 * `prksDeleteFolderFromDetail` confirms, then calls `deleteFolderCanonical`.
 * `prksOpenNewFolderFromDetail` opens the shared folder modal with this folder as parent.
 * A stale owner does not delete or open that modal.
 */
export function browserFolderDetailIntents(
  owner: FolderDetailIntentOwner | null,
  generation: number,
): FolderDetailIntents {
  return {
    owner: () => {
      if (!owner || typeof owner.isCurrent !== 'function' || !owner.isCurrent(generation)) return null
      return owner
    },
    async remove(folderId: string) {
      if (!ownsFolder(owner, generation, folderId)) return
      const fn = window.prksDeleteFolderFromDetail
      if (typeof fn !== 'function') return
      await fn(folderId, () => ownsFolder(owner, generation, folderId))
    },
    createChild(folder) {
      const id = String(folder?.id ?? '').trim()
      if (!ownsFolder(owner, generation, id)) return
      window.prksOpenNewFolderFromDetail?.(folder)
    },
  }
}
