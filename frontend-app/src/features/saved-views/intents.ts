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
  /** Index row. Fetches that view, then opens the shared modal. */
  editById(viewId: string): Promise<void>
  /**
   * Index row. Confirms and deletes through `prksDeleteSavedViewFromIndex`.
   * That wrapper refreshes this index in place. It does not navigate away.
   */
  removeFromIndex(viewId: string): Promise<void>
  openSearch(): void
}

export const SAVED_VIEW_ENTITY = 'savedView'

function ownsIndex(owner: SavedViewIntentOwner | null | undefined, generation: number): boolean {
  if (!owner || typeof owner.isCurrent !== 'function' || !owner.isCurrent(generation)) return false
  const route = owner.lastResolvedRoute || owner.route
  return !!route && route.name === 'saved-views'
}

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
    async editById(viewId) {
      if (!ownsIndex(owner, generation) || !viewId) return
      const fetchView = window.fetchSavedView
      if (typeof fetchView !== 'function') return
      let view: { id?: unknown; name?: unknown; search?: SavedViewRecord['search'] } | null
      try {
        view = await fetchView(viewId)
      } catch {
        return
      }
      if (!ownsIndex(owner, generation) || !view || view.id == null || !String(view.id)) return
      window.prksOpenSavedViewModal?.({
        viewId: String(view.id),
        name: String(view.name || ''),
        definition: view.search || { mode: '', q: '', tag: '', author: '', publisher: '' },
      })
    },
    async removeFromIndex(viewId) {
      if (!ownsIndex(owner, generation) || !viewId) return
      const fn = window.prksDeleteSavedViewFromIndex
      if (typeof fn !== 'function') return
      await fn(viewId, () => ownsIndex(owner, generation), owner?.tabId)
    },
    openSearch() {
      if (!ownsIndex(owner, generation)) return
      window.prksOpenCommandPalette?.()
    },
  }
}
