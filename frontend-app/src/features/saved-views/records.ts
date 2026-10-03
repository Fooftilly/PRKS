import { MutationObserver, type QueryClient } from '@tanstack/vue-query'
import { PRKS_API_FALLBACK_ERROR, PrksApiError } from '../../api/http'
import {
  createSavedView,
  deleteSavedView,
  getSavedView,
  listSavedViews,
  updateSavedView,
  type SavedView,
  type SavedViewInput,
} from '../../api/saved-views'
import { prksQueryClient } from '../../query/client'
import { prksQueryKeys } from '../../query/keys'

const OFFLINE_MESSAGE = 'Saved Views require a connection to PRKS.'

/** The offline runtime refused a write and already told the user. Nothing was sent. */
export class SavedViewOfflineRefusal extends Error {
  readonly prksOfflineRefused = true

  constructor() {
    super('Requires a connection to PRKS.')
    this.name = 'SavedViewOfflineRefusal'
  }
}

/**
 * Saved View records as server state: the one owner of Saved View reads and
 * writes for every PRKS surface. The index route subscribes to the list
 * query; the classic coordinator, the shared modal, and the command palette
 * reach the same cache through `window.prksSavedViewRecords`.
 *
 * Reads always ask the server (`staleTime: 0`), the same freshness as before,
 * and concurrent reads of one key share a request. Writes are online-only
 * mutations without retry; every write that was sent invalidates the whole
 * Saved Views domain, failed or not, because a malformed or lost reply cannot
 * prove the server did not commit. Nothing is persisted or replayed.
 */
export interface SavedViewRecords {
  list(): Promise<SavedView[]>
  /** `null` when the server has no such view. */
  get(viewId: string): Promise<SavedView | null>
  create(input: SavedViewInput): Promise<SavedView>
  update(viewId: string, input: SavedViewInput): Promise<SavedView>
  remove(viewId: string): Promise<void>
  /** {@link savedViewActionMessage}, for classic callers. */
  actionMessage(err: unknown, fallback: string): string
}

/**
 * Text for a failed Saved View action: the server's refusal when it sent one,
 * the offline refusal, otherwise the action's own `fallback`.
 */
export function savedViewActionMessage(err: unknown, fallback: string): string {
  if (err instanceof SavedViewOfflineRefusal) return err.message
  if (err instanceof PrksApiError && err.message.trim() && err.message !== PRKS_API_FALLBACK_ERROR) {
    return err.message.trim()
  }
  return fallback
}

function guardOffline(): void {
  if (window.prksOfflineGuardMutation?.(OFFLINE_MESSAGE) === true) throw new SavedViewOfflineRefusal()
}

/** `queryClient` defaults to the page's shared client, resolved on each call. */
export function savedViewRecords(queryClient?: QueryClient): SavedViewRecords {
  const client = () => queryClient ?? prksQueryClient()

  async function write<T>(run: () => Promise<T>): Promise<T> {
    guardOffline()
    const shared = client()
    // A mutation, not a bare call, so the client's mutation defaults (no
    // retry) and its transport-failure reporting apply.
    const mutation = new MutationObserver(shared, { mutationFn: run })
    try {
      return await mutation.mutate()
    } finally {
      mutation.reset()
      await shared.invalidateQueries({ queryKey: prksQueryKeys.savedViews.all() })
    }
  }

  return {
    list() {
      return client().fetchQuery({
        queryKey: prksQueryKeys.savedViews.list(),
        queryFn: ({ signal }) => listSavedViews(signal),
        staleTime: 0,
      })
    },
    get(viewId) {
      return client().fetchQuery({
        queryKey: prksQueryKeys.savedViews.detail(viewId),
        queryFn: ({ signal }) => getSavedView(viewId, signal),
        staleTime: 0,
      })
    },
    create(input) {
      return write(() => createSavedView(input))
    },
    update(viewId, input) {
      return write(() => updateSavedView(viewId, input))
    },
    remove(viewId) {
      return write(() => deleteSavedView(viewId))
    },
    actionMessage: savedViewActionMessage,
  }
}
