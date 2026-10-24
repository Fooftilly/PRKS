import {
  savedViewActionMessage as actionMessage,
  savedViewRecords,
  SavedViewOfflineRefusal,
  type SavedViewRecords,
} from './records'
import type { SavedViewRecord } from './types'

/** Owning TabContext fields Saved View intents need. Not a second route model. */
export interface SavedViewIntentOwner {
  tabId?: string
  isCurrent?: (generation: number) => boolean
  getEntity?: (type: string) => { id?: unknown } | null
  lastResolvedRoute?: { name?: string } | null
  route?: { name?: string } | null
}

/** Edit/Delete outcome. Cancel, a stale owner, and an offline refusal stay quiet. */
export type SavedViewActionOutcome =
  | { status: 'success' }
  | { status: 'quiet' }
  | { status: 'error'; message: string }

export interface SavedViewIntents {
  /** Detail. Opens the shared modal with this owner's record. */
  edit(view: SavedViewRecord): void
  /** Detail. Confirms, deletes, then sends this owner back to `#/views`. */
  remove(viewId: string): Promise<SavedViewActionOutcome>
  /** Index row. Reads that view, then opens the shared modal. */
  editById(viewId: string): Promise<SavedViewActionOutcome>
  /** Index row. Confirms and deletes. The shared list query refreshes the index. */
  removeFromIndex(viewId: string): Promise<SavedViewActionOutcome>
  openSearch(): void
}

const EDIT_FAILURE = 'Could not open Saved View.'
const DELETE_FAILURE = 'Could not delete Saved View.'

function quiet(): SavedViewActionOutcome {
  return { status: 'quiet' }
}

function success(): SavedViewActionOutcome {
  return { status: 'success' }
}

function failure(message: string): SavedViewActionOutcome {
  return { status: 'error', message }
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

function confirmDelete(): Promise<boolean> {
  const confirm = window.prksConfirmDestructive
  if (typeof confirm !== 'function') return Promise.resolve(false)
  return confirm({
    title: 'Delete Saved View?',
    message: 'Deleting this Saved View will not delete any files.',
    confirmLabel: 'Delete Saved View',
  })
}

/**
 * Saved View intents for one owner and generation. Reads and writes go
 * through the Saved View records service (typed client + shared QueryClient).
 * Edit still opens the shared classic `#saved-view-modal` until #303 B4.
 * `still` is this owner: a confirm that outlives the pane does not delete,
 * and a write that finishes late reports nothing to a replaced owner.
 */
export function browserSavedViewIntents(
  owner: SavedViewIntentOwner | null,
  generation: number,
  records: SavedViewRecords = savedViewRecords(),
): SavedViewIntents {
  async function removeView(viewId: string, still: () => boolean): Promise<SavedViewActionOutcome> {
    if (!(await confirmDelete()) || !still()) return quiet()
    try {
      await records.remove(viewId)
    } catch (err) {
      if (err instanceof SavedViewOfflineRefusal || !still()) return quiet()
      return failure(actionMessage(err, DELETE_FAILURE))
    }
    return still() ? success() : quiet()
  }

  return {
    edit(view) {
      if (!ownsView(owner, generation, view.id)) return
      window.prksOpenSavedViewModal?.({ viewId: view.id, name: view.name, definition: view.search })
    },
    async remove(viewId) {
      const still = () => ownsView(owner, generation, viewId)
      if (!still()) return quiet()
      const outcome = await removeView(viewId, still)
      if (outcome.status === 'success') {
        window.prksNavigate?.('#/views', owner?.tabId ? { replace: true, tabId: owner.tabId } : { replace: true })
      }
      return outcome
    },
    async editById(viewId) {
      const still = () => ownsIndex(owner, generation)
      if (!still() || !viewId) return quiet()
      let view: SavedViewRecord | null
      try {
        view = await records.get(viewId)
      } catch (err) {
        return still() ? failure(actionMessage(err, EDIT_FAILURE)) : quiet()
      }
      if (!still()) return quiet()
      if (!view) return failure(EDIT_FAILURE)
      window.prksOpenSavedViewModal?.({ viewId: view.id, name: view.name, definition: view.search })
      return success()
    },
    async removeFromIndex(viewId) {
      const still = () => ownsIndex(owner, generation)
      if (!still() || !viewId) return quiet()
      return removeView(viewId, still)
    },
    openSearch() {
      if (!ownsIndex(owner, generation)) return
      window.prksOpenCommandPalette?.()
    },
  }
}
