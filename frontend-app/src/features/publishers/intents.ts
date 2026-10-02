import type { PublishersResume } from './projection'

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

function quiet(): PublishersActionOutcome {
  return { status: 'quiet' }
}

function success(): PublishersActionOutcome {
  return { status: 'success' }
}

function failure(message: string): PublishersActionOutcome {
  return { status: 'error', message }
}

function actionMessage(err: unknown, fallback: string): string {
  if (err instanceof Error && err.message.trim()) return err.message.trim()
  return fallback
}

export function ownsPublishers(
  owner: PublishersIntentOwner | null | undefined,
  generation: number,
): boolean {
  if (!owner || typeof owner.isCurrent !== 'function' || !owner.isCurrent(generation)) return false
  const route = owner.lastResolvedRoute || owner.route
  return !!route && route.name === 'publishers'
}

async function reloadIfCurrent(
  owner: PublishersIntentOwner | null,
  generation: number,
  resume: PublishersResume | null,
): Promise<PublishersActionOutcome> {
  if (!owner || !ownsPublishers(owner, generation)) return quiet()
  const reload = window.prksReloadPublishersPage
  if (typeof reload !== 'function') return quiet()
  const painted = await reload(owner, generation, resume)
  if (!ownsPublishers(owner, generation)) return quiet()
  if (!painted) return quiet()
  return success()
}

/**
 * Publisher writes stay on the classic online wrappers. There is no durable
 * publisher queue. `still` is this owner's generation: a confirm that outlives
 * the pane does not write, and a write that finishes late does not repaint a
 * replaced owner.
 */
export function browserPublishersIntents(
  owner: PublishersIntentOwner | null,
  generation: number,
): PublishersIntents {
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
      const create = window.prksPublishersCreate
      if (typeof create !== 'function') return quiet()
      try {
        const outcome = await create(next)
        if (!outcome || !outcome.ok) return quiet()
      } catch (err) {
        if (!ownsPublishers(owner, generation)) return quiet()
        return failure(actionMessage(err, CREATE_FAILURE))
      }
      return reloadIfCurrent(owner, generation, null)
    },

    async addAlias(publisherId, alias) {
      const next = String(alias || '').trim()
      if (!ownsPublishers(owner, generation) || !publisherId || !next) return quiet()
      const add = window.prksPublishersAddAlias
      if (typeof add !== 'function') return quiet()
      try {
        const outcome = await add(publisherId, next)
        if (!outcome || !outcome.ok) return quiet()
      } catch (err) {
        if (!ownsPublishers(owner, generation)) return quiet()
        return failure(actionMessage(err, ADD_FAILURE))
      }
      return reloadIfCurrent(owner, generation, { aliasPublisherId: publisherId })
    },

    async removeAlias(publisherId, alias) {
      if (!ownsPublishers(owner, generation) || !publisherId || alias == null) return quiet()
      const remove = window.prksPublishersRemoveAlias
      if (typeof remove !== 'function') return quiet()
      try {
        const outcome = await remove(publisherId, alias)
        if (!outcome || !outcome.ok) return quiet()
      } catch (err) {
        if (!ownsPublishers(owner, generation)) return quiet()
        return failure(actionMessage(err, REMOVE_ALIAS_FAILURE))
      }
      return reloadIfCurrent(owner, generation, { aliasPublisherId: publisherId })
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
      const remove = window.prksPublishersDelete
      if (typeof remove !== 'function') return quiet()
      try {
        const outcome = await remove(publisherId)
        if (!outcome || !outcome.ok) return quiet()
      } catch (err) {
        if (!ownsPublishers(owner, generation)) return quiet()
        return failure(actionMessage(err, DELETE_FAILURE))
      }
      if (!ownsPublishers(owner, generation)) return quiet()
      return reloadIfCurrent(owner, generation, null)
    },
  }
}
