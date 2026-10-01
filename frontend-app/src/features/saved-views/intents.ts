import type { SavedViewRecord } from './types'

/** Owning TabContext fields Saved View intents need. Not a second route model. */
export interface SavedViewIntentOwner {
  tabId?: string
  isCurrent?: (generation: number) => boolean
  getEntity?: (type: string) => { id?: unknown } | null
  lastResolvedRoute?: { name?: string } | null
  route?: { name?: string } | null
}

export interface SavedViewIntents {
  edit(view: SavedViewRecord): void
  remove(viewId: string): Promise<void>
}

export const SAVED_VIEW_ENTITY = 'savedView'

function ownsView(owner: SavedViewIntentOwner | null | undefined, generation: number, viewId: string): boolean {
  if (!owner || !viewId) return false
  const check = window.prksTabContextOwnsEntityRoute
  if (typeof check === 'function') {
    return check(owner, generation, SAVED_VIEW_ENTITY, viewId, 'saved-view-detail')
  }
  if (typeof owner.isCurrent !== 'function' || !owner.isCurrent(generation)) return false
  const live = owner.getEntity?.(SAVED_VIEW_ENTITY)
  return !!live && String(live.id) === String(viewId)
}

/**
 * Saved View writes stay on the canonical wrappers. Edit opens the shared
 * `#saved-view-modal` (`prksOpenSavedViewModal`), which calls
 * `updateSavedView`. Delete is `prksDeleteSavedViewFromDetail`: confirm,
 * `deleteSavedView`, then navigate this owner back to `#/views`. The `still`
 * fence is rechecked after confirm and before navigate. A stale owner does
 * not open the modal or delete.
 */
export function browserSavedViewIntents(
  owner: SavedViewIntentOwner | null,
  generation: number,
): SavedViewIntents {
  return {
    edit(view) {
      if (!ownsView(owner, generation, view.id)) return
      window.prksOpenSavedViewModal?.({ viewId: view.id, name: view.name, definition: view.search })
    },
    async remove(viewId) {
      if (!ownsView(owner, generation, viewId)) return
      const fn = window.prksDeleteSavedViewFromDetail
      if (typeof fn !== 'function') return
      await fn(viewId, () => ownsView(owner, generation, viewId), owner?.tabId)
    },
  }
}
