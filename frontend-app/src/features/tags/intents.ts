import type { TagsResume } from './projection'

/** Owning TabContext fields Tag intents need. Not a second route model. */
export interface TagsIntentOwner {
  tabId?: string
  isCurrent?: (generation: number) => boolean
  lastResolvedRoute?: { name?: string } | null
  route?: { name?: string } | null
}

export type TagsActionOutcome =
  | { status: 'success' }
  | { status: 'quiet' }
  | { status: 'error'; message: string }

export interface TagsIntents {
  openTag(name: string): void
  addAlias(tagId: string, alias: string): Promise<TagsActionOutcome>
  removeAlias(tagId: string, alias: string): Promise<TagsActionOutcome>
  remove(tagId: string, name: string): Promise<TagsActionOutcome>
  merge(sourceId: string, targetId: string): Promise<TagsActionOutcome>
}

const ADD_FAILURE = 'Could not add alias.'
const REMOVE_ALIAS_FAILURE = 'Could not remove alias.'
const MERGE_FAILURE = 'Could not merge tags.'
const DELETE_FAILURE = 'Could not delete tag.'

function quiet(): TagsActionOutcome {
  return { status: 'quiet' }
}

function success(): TagsActionOutcome {
  return { status: 'success' }
}

function failure(message: string): TagsActionOutcome {
  return { status: 'error', message }
}

function actionMessage(err: unknown, fallback: string): string {
  if (err instanceof Error && err.message.trim()) return err.message.trim()
  return fallback
}

export function ownsTags(owner: TagsIntentOwner | null | undefined, generation: number): boolean {
  if (!owner || typeof owner.isCurrent !== 'function' || !owner.isCurrent(generation)) return false
  const route = owner.lastResolvedRoute || owner.route
  return !!route && route.name === 'tags'
}

function deleteMessage(err: unknown): string {
  const fn = window.prksTagVocabularyMessage
  if (typeof fn === 'function') return fn(err, 'delete this tag')
  return actionMessage(err, DELETE_FAILURE)
}

async function reloadIfCurrent(
  owner: TagsIntentOwner | null,
  generation: number,
  resume: TagsResume | null,
): Promise<TagsActionOutcome> {
  if (!owner || !ownsTags(owner, generation)) return quiet()
  const reload = window.prksReloadTagsVocabulary
  if (typeof reload !== 'function') return quiet()
  const painted = await reload(owner, generation, resume)
  if (!ownsTags(owner, generation)) return quiet()
  if (!painted) return quiet()
  return success()
}

/**
 * Tag writes stay on the classic wrappers. Alias HTTP is online-only.
 * Delete and merge are the durable vocabulary operations. `still` is this
 * owner's generation: a confirm that outlives the pane does not write, and a
 * write that finishes late does not repaint a replaced owner.
 */
export function browserTagsIntents(owner: TagsIntentOwner | null, generation: number): TagsIntents {
  return {
    openTag(name) {
      if (!ownsTags(owner, generation) || !name) return
      window.prksNavigate?.(
        `#/search?tag=${encodeURIComponent(name)}`,
        owner?.tabId ? { tabId: owner.tabId } : undefined,
      )
    },

    async addAlias(tagId, alias) {
      const next = String(alias || '').trim()
      if (!ownsTags(owner, generation) || !tagId || !next) return quiet()
      const add = window.prksTagsAddAlias
      if (typeof add !== 'function') return quiet()
      try {
        const outcome = await add(tagId, next)
        if (!outcome || !outcome.ok) return quiet()
      } catch (err) {
        if (!ownsTags(owner, generation)) return quiet()
        return failure(actionMessage(err, ADD_FAILURE))
      }
      return reloadIfCurrent(owner, generation, { aliasTagId: tagId })
    },

    async removeAlias(tagId, alias) {
      if (!ownsTags(owner, generation) || !tagId || alias == null) return quiet()
      const remove = window.prksTagsRemoveAlias
      if (typeof remove !== 'function') return quiet()
      try {
        const outcome = await remove(tagId, alias)
        if (!outcome || !outcome.ok) return quiet()
      } catch (err) {
        if (!ownsTags(owner, generation)) return quiet()
        return failure(actionMessage(err, REMOVE_ALIAS_FAILURE))
      }
      return reloadIfCurrent(owner, generation, { aliasTagId: tagId })
    },

    async remove(tagId, name) {
      if (!ownsTags(owner, generation) || !tagId) return quiet()
      const confirm = window.prksConfirmDestructive
      if (typeof confirm !== 'function') return quiet()
      const label = name || tagId
      const confirmed = await confirm({
        title: `Delete tag “${label}”?`,
        message:
          'Files and folders will no longer have this tag. Alternate names (aliases) for this tag will be removed.',
        confirmLabel: 'Delete tag',
      })
      if (!confirmed || !ownsTags(owner, generation)) return quiet()
      const remove = window.prksTagsDelete
      if (typeof remove !== 'function') return quiet()
      try {
        await remove(tagId)
      } catch (err) {
        if (!ownsTags(owner, generation)) return quiet()
        return failure(deleteMessage(err))
      }
      if (!ownsTags(owner, generation)) return quiet()
      return reloadIfCurrent(owner, generation, null)
    },

    async merge(sourceId, targetId) {
      if (!ownsTags(owner, generation) || !sourceId || !targetId || sourceId === targetId) return quiet()
      const merge = window.prksTagsMerge
      if (typeof merge !== 'function') return quiet()
      try {
        await merge(sourceId, targetId)
      } catch (err) {
        if (!ownsTags(owner, generation)) return quiet()
        return failure(actionMessage(err, MERGE_FAILURE))
      }
      if (!ownsTags(owner, generation)) return quiet()
      return reloadIfCurrent(owner, generation, null)
    },
  }
}
