import { MutationObserver, type QueryClient } from '@tanstack/vue-query'
import { PRKS_API_FALLBACK_ERROR, PrksApiError } from '../../api/http'
import {
  addPublisherAlias,
  createPublisher,
  deletePublisher,
  removePublisherAlias,
} from '../../api/publishers'
import { prksQueryClient } from '../../query/client'
import { prksQueryKeys } from '../../query/keys'

/** Owning TabContext fields Publisher intents need. Not a second route model. */
export interface PublishersIntentOwner {
  tabId?: string
  isCurrent?: (generation: number) => boolean
  lastResolvedRoute?: { name?: string } | null
  route?: { name?: string } | null
}

export type PublishersActionOutcome =
  | { status: 'success' }
  | { status: 'quiet' }
  | { status: 'error'; message: string }

export interface PublishersIntents {
  openPublisher(name: string): void
  create(name: string): Promise<PublishersActionOutcome>
  addAlias(publisherId: string, alias: string): Promise<PublishersActionOutcome>
  removeAlias(publisherId: string, alias: string): Promise<PublishersActionOutcome>
  remove(publisherId: string, name: string): Promise<PublishersActionOutcome>
}

const CREATE_FAILURE = 'Could not add publisher.'
const ADD_FAILURE = 'Could not add alias.'
const REMOVE_ALIAS_FAILURE = 'Could not remove alias.'
const DELETE_FAILURE = 'Could not delete publisher.'
const OFFLINE_MESSAGE = 'Publishers require a connection to PRKS.'

function quiet(): PublishersActionOutcome {
  return { status: 'quiet' }
}

function success(): PublishersActionOutcome {
  return { status: 'success' }
}

function failure(message: string): PublishersActionOutcome {
  return { status: 'error', message }
}

/** The server's refusal text when it sent one; otherwise the action's own failure. */
function actionMessage(err: unknown, fallback: string): string {
  if (err instanceof PrksApiError && err.message.trim() && err.message !== PRKS_API_FALLBACK_ERROR) {
    return err.message.trim()
  }
  return fallback
}

/** True when the offline runtime refused the write and already told the user. */
function offlineBlocked(): boolean {
  return window.prksOfflineGuardMutation?.(OFFLINE_MESSAGE) === true
}

export function ownsPublishers(
  owner: PublishersIntentOwner | null | undefined,
  generation: number,
): boolean {
  if (!owner || typeof owner.isCurrent !== 'function' || !owner.isCurrent(generation)) return false
  const route = owner.lastResolvedRoute || owner.route
  return !!route && route.name === 'publishers'
}

/**
 * Publisher writes go through the typed client. Publishers are online-only:
 * there is no durable publisher queue and nothing is retried. `generation` is
 * this owner's: a confirm that outlives the pane does not write, and a write
 * that finishes late reports nothing to a replaced owner. A write that landed
 * invalidates every Publishers read, whichever pane it came from.
 */
export function browserPublishersIntents(
  owner: PublishersIntentOwner | null,
  generation: number,
  queryClient: QueryClient = prksQueryClient(),
): PublishersIntents {
  async function write(run: () => Promise<unknown>, fallback: string): Promise<PublishersActionOutcome> {
    if (offlineBlocked()) return quiet()
    // A mutation, not a bare call, so the client's mutation defaults (no
    // retry) and its transport-failure reporting apply.
    const mutation = new MutationObserver(queryClient, { mutationFn: run })
    try {
      await mutation.mutate()
    } catch (err) {
      if (!ownsPublishers(owner, generation)) return quiet()
      return failure(actionMessage(err, fallback))
    } finally {
      mutation.reset()
    }
    // A failed refetch keeps the list on screen and shows on the page. It is
    // not a failed create, alias change, or delete.
    await queryClient.invalidateQueries({ queryKey: prksQueryKeys.publishers.all() })
    return ownsPublishers(owner, generation) ? success() : quiet()
  }

  return {
    openPublisher(name) {
      if (!ownsPublishers(owner, generation)) return
      window.prksNavigate?.(
        `#/search?publisher=${encodeURIComponent(name)}`,
        owner?.tabId ? { tabId: owner.tabId } : undefined,
      )
    },

    async create(name) {
      const next = String(name || '').trim()
      if (!ownsPublishers(owner, generation) || !next) return quiet()
      return write(() => createPublisher(next), CREATE_FAILURE)
    },

    async addAlias(publisherId, alias) {
      const next = String(alias || '').trim()
      if (!ownsPublishers(owner, generation) || !publisherId || !next) return quiet()
      return write(() => addPublisherAlias(publisherId, next), ADD_FAILURE)
    },

    async removeAlias(publisherId, alias) {
      if (!ownsPublishers(owner, generation) || !publisherId || !alias) return quiet()
      return write(() => removePublisherAlias(publisherId, alias), REMOVE_ALIAS_FAILURE)
    },

    async remove(publisherId, name) {
      if (!ownsPublishers(owner, generation) || !publisherId) return quiet()
      const confirm = window.prksConfirmDestructive
      if (typeof confirm !== 'function') return quiet()
      const label = name || publisherId
      const confirmed = await confirm({
        title: `Delete publisher “${label}”?`,
        message: 'Works are not changed. Alternate spellings (aliases) for this publisher group will be removed.',
        confirmLabel: 'Delete publisher',
      })
      if (!confirmed || !ownsPublishers(owner, generation)) return quiet()
      return write(() => deletePublisher(publisherId), DELETE_FAILURE)
    },
  }
}
