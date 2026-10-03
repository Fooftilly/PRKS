import { MutationObserver, type QueryClient } from '@tanstack/vue-query'
import { isAbortError, PRKS_API_FALLBACK_ERROR, PrksApiError } from '../../api/http'
import {
  createSavedView,
  deleteSavedView,
  getSavedView,
  listSavedViews,
  updateSavedView,
  type SavedView,
  type SavedViewInput,
} from '../../api/saved-views'
import { prksQueryClient, type PrksQueryMeta } from '../../query/client'
import { prksQueryKeys } from '../../query/keys'

const OFFLINE_MESSAGE = 'Saved Views require a connection to PRKS.'

/** Every Saved View read reports its final failure under the classic source. */
export const SAVED_VIEWS_READ_META: PrksQueryMeta = { clientErrorSource: 'saved-views.fetch' }

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
  /**
   * `null` when the server has no such view. `signal` is the reader's
   * lifetime (a route's abort signal): when it aborts, this call rejects with
   * an AbortError, and the request itself is cancelled only if no other
   * reader of that view is still waiting on it.
   */
  get(viewId: string, signal?: AbortSignal): Promise<SavedView | null>
  create(input: SavedViewInput): Promise<SavedView>
  update(viewId: string, input: SavedViewInput): Promise<SavedView>
  remove(viewId: string): Promise<void>
  /** {@link savedViewActionMessage}, for classic callers. */
  actionMessage(err: unknown, fallback: string): string
  /**
   * Call `listener` after every sent Saved View write has invalidated the
   * domain, from any surface. For imperative readers that hold a list outside
   * a query observer (the open command palette). Returns the unsubscribe.
   */
  onWrite(listener: () => void): () => void
  /**
   * Keep one painted record honest for as long as `signal` lives (a route's
   * abort signal). After each Saved View write from any surface it re-reads
   * `view`. If the server's record is no longer the one painted, because it
   * was renamed, redefined or deleted, or if it can no longer be read, it
   * calls `onChange` once and stops. An unchanged record stays quiet. When
   * `signal` aborts it stops listening and drops any re-read in flight.
   */
  follow(view: SavedView, signal: AbortSignal, onChange: () => void): void
}

/** Readers still waiting on each detail key, page-wide. */
const pendingReads = new Map<string, number>()

function abortError(): DOMException {
  return new DOMException('Saved View read aborted.', 'AbortError')
}

/** Page-wide: writes from any records instance reach every subscriber. */
const writeListeners = new Set<() => void>()

function notifyWrite(): void {
  for (const listener of [...writeListeners]) {
    try {
      listener()
    } catch {
      /* One reader's failure does not stop the others. */
    }
  }
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
      notifyWrite()
    }
  }

  const records: SavedViewRecords = {
    list() {
      return client().fetchQuery({
        queryKey: prksQueryKeys.savedViews.list(),
        queryFn: ({ signal }) => listSavedViews(signal),
        staleTime: 0,
        meta: SAVED_VIEWS_READ_META,
      })
    },
    get(viewId, signal) {
      if (signal?.aborted) return Promise.reject(abortError())
      const shared = client()
      const queryKey = prksQueryKeys.savedViews.detail(viewId)
      const slot = JSON.stringify(queryKey)
      pendingReads.set(slot, (pendingReads.get(slot) ?? 0) + 1)
      let held = true
      const release = (): boolean => {
        if (!held) return false
        held = false
        const left = (pendingReads.get(slot) ?? 1) - 1
        if (left > 0) pendingReads.set(slot, left)
        else pendingReads.delete(slot)
        return left <= 0
      }
      const read = shared.fetchQuery({
        queryKey,
        queryFn: ({ signal: requestSignal }) => getSavedView(viewId, requestSignal),
        staleTime: 0,
        meta: SAVED_VIEWS_READ_META,
      })
      if (!signal) return read.finally(release)
      return new Promise<SavedView | null>((resolve, reject) => {
        const onAbort = () => {
          // The last reader leaving stops the request and its retries.
          if (release()) void shared.cancelQueries({ queryKey, exact: true })
          reject(abortError())
        }
        signal.addEventListener('abort', onAbort, { once: true })
        read.then(resolve, reject).finally(() => {
          signal.removeEventListener('abort', onAbort)
          release()
        })
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
    onWrite(listener) {
      writeListeners.add(listener)
      return () => {
        writeListeners.delete(listener)
      }
    },
    follow(view, signal, onChange) {
      if (signal.aborted) return
      const painted = JSON.stringify(view)
      let done = false
      let checking = false
      let again = false
      const stop = () => {
        if (done) return
        done = true
        unsubscribe()
        signal.removeEventListener('abort', stop)
      }
      // Writes that land while a re-read is in flight are folded into one
      // more re-read, so the last write is always checked.
      const check = async (): Promise<void> => {
        if (checking) {
          again = true
          return
        }
        checking = true
        try {
          do {
            again = false
            let changed: boolean
            try {
              changed = JSON.stringify(await records.get(view.id, signal)) !== painted
            } catch (err) {
              if (done || isAbortError(err)) return
              changed = true
            }
            if (done) return
            if (changed) {
              stop()
              onChange()
              return
            }
          } while (again)
        } finally {
          checking = false
        }
      }
      const unsubscribe = records.onWrite(() => {
        void check()
      })
      signal.addEventListener('abort', stop, { once: true })
    },
  }
  return records
}
