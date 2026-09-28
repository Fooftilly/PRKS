import type { InjectionKey } from 'vue'
import type { PlaylistFieldDraft } from './types'

/** Owning TabContext fields Playlist intents need. Not a second route model. */
export interface PlaylistIntentOwner {
  tabId?: string
  generation?: number
  isCurrent?: (generation: number) => boolean
  lastResolvedRoute?: { name?: string; params?: { playlistId?: string } } | null
  route?: { name?: string; params?: { playlistId?: string } } | null
  root?: HTMLElement | null
  abortController?: { signal?: AbortSignal } | null
  ui?: {
    playlistEditing?: boolean
    playlistRename?: Record<string, boolean>
    workPlaylistEditing?: boolean
  }
  getEntity?: (type: string) => { id?: string } | null
  setEntity?: (type: string, value: unknown) => void
  routeSidebar?: { playlistTitle?: string; itemCount?: number }
}

export interface PlaylistSaveResult {
  ok: boolean
  message: string
}

export interface PlaylistVideoChoice {
  id: string
  title: string
}

export interface PlaylistIntents {
  create(): void
  cancelEdit(playlistId: string): void
  saveFields(
    playlistId: string,
    draft: PlaylistFieldDraft,
    shown: PlaylistFieldDraft,
  ): Promise<PlaylistSaveResult>
  reorder(playlistId: string, workIds: readonly string[]): Promise<PlaylistSaveResult>
  removeWork(playlistId: string, workId: string): Promise<PlaylistSaveResult>
  addWork(playlistId: string, workId: string): Promise<PlaylistSaveResult>
  loadAddableVideos(playlistId: string, presentIds: readonly string[]): Promise<PlaylistVideoChoice[]>
  beginRename(playlistId: string, workId: string): void
  cancelRename(playlistId: string, workId: string): void
  saveWorkTitle(playlistId: string, workId: string, title: string): Promise<PlaylistSaveResult>
  remove(playlistId: string, playlist: { id: string; title: string; items: readonly { id: string }[] }): Promise<void>
}

export const playlistIntentsKey: InjectionKey<PlaylistIntents> = Symbol('prks-playlist-intents')

const FIELDS = ['title', 'description', 'original_url'] as const

function ownsDetail(
  owner: PlaylistIntentOwner | null | undefined,
  generation: number,
  playlistId: string,
): boolean {
  if (!owner || !playlistId) return false
  const check = window.prksTabContextOwnsEntityRoute
  if (typeof check === 'function') {
    return check(owner, generation, 'playlist', playlistId, 'playlist-detail')
  }
  if (typeof owner.isCurrent !== 'function' || !owner.isCurrent(generation)) return false
  const route = owner.lastResolvedRoute || owner.route
  return !!(route && route.name === 'playlist-detail')
}

function messageOf(err: unknown, fallback: string): string {
  if (err && typeof err === 'object' && 'message' in err && typeof err.message === 'string' && err.message) {
    return err.message
  }
  return fallback
}

function dirtyFields(draft: PlaylistFieldDraft, shown: PlaylistFieldDraft): Record<string, string> {
  const changes: Record<string, string> = {}
  for (const field of FIELDS) {
    const desired = String(draft[field] ?? '')
    const baseline = String(shown[field] ?? '')
    if (desired !== baseline) changes[field] = desired
  }
  return changes
}

async function reloadIfOwned(
  owner: PlaylistIntentOwner | null | undefined,
  generation: number,
  playlistId: string,
): Promise<boolean> {
  if (!ownsDetail(owner, generation, playlistId)) return false
  const reload = window.prksReloadPlaylistDetail
  if (typeof reload !== 'function' || !owner) return false
  try {
    await reload(owner, playlistId)
  } catch {
    /* The write already landed. A failed repaint is not a rejected save. */
  }
  return ownsDetail(owner, generation, playlistId)
}

function renameMap(owner: PlaylistIntentOwner): Record<string, boolean> {
  if (!owner.ui) owner.ui = {}
  if (!owner.ui.playlistRename || typeof owner.ui.playlistRename !== 'object') {
    owner.ui.playlistRename = {}
  }
  return owner.ui.playlistRename
}

function repaint(owner: PlaylistIntentOwner, playlistId: string): void {
  if (!owner.root || typeof window.renderPlaylistDetail !== 'function') return
  const entity = owner.getEntity ? owner.getEntity('playlist') : null
  window.renderPlaylistDetail(owner, entity, owner.root)
  void playlistId
}

/**
 * Typed intents call the existing public Playlist wrappers.
 * They do not read the durable operation store or call prks*Durably.
 */
export function browserPlaylistIntents(
  owner: PlaylistIntentOwner | null | undefined,
  generation: number,
): PlaylistIntents {
  return {
    create() {
      const open = window.prksOpenNewPlaylistModalFromPlaylistsPage
      if (typeof open === 'function') open()
    },

    cancelEdit(playlistId) {
      if (!ownsDetail(owner, generation, playlistId) || !owner) return
      if (owner.ui) {
        owner.ui.playlistEditing = false
        owner.ui.playlistRename = {}
      }
      repaint(owner, playlistId)
      if (typeof window.updatePanelContent === 'function') window.updatePanelContent('details')
    },

    async saveFields(playlistId, draft, shown) {
      if (!ownsDetail(owner, generation, playlistId)) {
        return { ok: false, message: '' }
      }
      const changes = dirtyFields(draft, shown)
      if (!Object.keys(changes).length) {
        if (owner?.ui) owner.ui.playlistEditing = false
        if (owner) repaint(owner, playlistId)
        return { ok: true, message: '' }
      }
      const update = window.updatePlaylist
      if (typeof update !== 'function') {
        return { ok: false, message: 'Could not save this playlist.' }
      }
      try {
        await update(playlistId, changes, {})
      } catch (err) {
        return { ok: false, message: messageOf(err, 'Could not save this playlist.') }
      }
      if (!ownsDetail(owner, generation, playlistId) || !owner) {
        return { ok: true, message: '' }
      }
      if (owner.ui) {
        owner.ui.playlistEditing = false
        owner.ui.playlistRename = {}
      }
      await reloadIfOwned(owner, generation, playlistId)
      return { ok: true, message: '' }
    },

    async reorder(playlistId, workIds) {
      if (!ownsDetail(owner, generation, playlistId)) return { ok: false, message: '' }
      const reorder = window.reorderPlaylist
      if (typeof reorder !== 'function') return { ok: false, message: 'Could not reorder playlist.' }
      try {
        await reorder(playlistId, workIds.slice())
      } catch (err) {
        return { ok: false, message: messageOf(err, 'Could not reorder playlist.') }
      }
      if (!owner) return { ok: true, message: '' }
      await reloadIfOwned(owner, generation, playlistId)
      return { ok: true, message: '' }
    },

    async removeWork(playlistId, workId) {
      if (!ownsDetail(owner, generation, playlistId)) return { ok: false, message: '' }
      const remove = window.removeWorkFromPlaylist
      if (typeof remove !== 'function') return { ok: false, message: 'Could not remove item.' }
      try {
        await remove(playlistId, workId)
      } catch (err) {
        return { ok: false, message: messageOf(err, 'Could not remove item.') }
      }
      if (!owner) return { ok: true, message: '' }
      await reloadIfOwned(owner, generation, playlistId)
      return { ok: true, message: '' }
    },

    async addWork(playlistId, workId) {
      if (!ownsDetail(owner, generation, playlistId)) return { ok: false, message: '' }
      const add = window.addWorkToPlaylist
      if (typeof add !== 'function') return { ok: false, message: 'Could not add.' }
      try {
        await add(playlistId, workId)
      } catch (err) {
        return { ok: false, message: messageOf(err, 'Could not add.') }
      }
      if (!owner) return { ok: true, message: '' }
      await reloadIfOwned(owner, generation, playlistId)
      return { ok: true, message: '' }
    },

    async loadAddableVideos(playlistId, presentIds) {
      if (!ownsDetail(owner, generation, playlistId)) return []
      const online =
        typeof window.prksOfflineRuntimeState !== 'function' ||
        window.prksOfflineRuntimeState() === 'online'
      if (!online) return []
      const fetchWorks = window.fetchWorks
      if (typeof fetchWorks !== 'function') return []
      const signal = owner?.abortController?.signal
      let works: Array<{ id?: string; title?: string }> = []
      try {
        works = (await fetchWorks(signal ? { signal } : undefined)) || []
      } catch {
        return []
      }
      if (!ownsDetail(owner, generation, playlistId)) return []
      const present = new Set(presentIds)
      const infer = window.prksInferWorkSourceKind
      return works
        .filter((work) => {
          if (!work || !work.id || present.has(String(work.id))) return false
          if (typeof infer !== 'function') return true
          return infer(work) === 'video'
        })
        .map((work) => ({ id: String(work.id), title: String(work.title || 'Untitled') }))
        .sort((a, b) => a.title.localeCompare(b.title, undefined, { sensitivity: 'base' }))
    },

    beginRename(playlistId, workId) {
      if (!ownsDetail(owner, generation, playlistId) || !owner || !workId) return
      renameMap(owner)[workId] = true
    },

    cancelRename(playlistId, workId) {
      if (!ownsDetail(owner, generation, playlistId) || !owner) return
      delete renameMap(owner)[workId]
    },

    async saveWorkTitle(playlistId, workId, title) {
      if (!ownsDetail(owner, generation, playlistId) || !owner) {
        return { ok: false, message: '' }
      }
      const next = String(title || '').trim()
      if (!workId || !next) return { ok: false, message: 'Title is required.' }
      const save = window.prksSaveWorkFieldDurably
      if (typeof save !== 'function') return { ok: false, message: 'Could not rename video.' }
      try {
        const result = await save(workId, 'title', next, { label: 'Title' })
        const code = result && typeof result === 'object' && 'code' in result ? String(result.code || '') : ''
        if (code === 'unavailable') {
          return {
            ok: false,
            message:
              'This video’s title cannot be renamed right now. Open it once while connected to PRKS so its details are prepared.',
          }
        }
        if (code === 'too-long') {
          const error =
            result && typeof result === 'object' && 'error' in result ? String(result.error || '') : ''
          return { ok: false, message: error || 'Title too long' }
        }
        if (code === 'failed') return { ok: false, message: 'Could not rename video.' }
      } catch {
        return { ok: false, message: 'Could not rename video.' }
      }
      if (!ownsDetail(owner, generation, playlistId)) return { ok: true, message: '' }
      delete renameMap(owner)[workId]
      if (typeof window.prksRefreshPendingWorkMetadata === 'function') {
        await window.prksRefreshPendingWorkMetadata()
      }
      if (!ownsDetail(owner, generation, playlistId)) return { ok: true, message: '' }
      repaint(owner, playlistId)
      return { ok: true, message: '' }
    },

    async remove(playlistId, playlist) {
      if (!ownsDetail(owner, generation, playlistId) || !owner) return
      const deleteFromDetail = window.deletePlaylistFromDetail
      if (typeof deleteFromDetail !== 'function') return
      await deleteFromDetail(owner, {
        id: playlist.id,
        title: playlist.title,
        items: playlist.items.map((item) => ({ id: item.id })),
      })
    },
  }
}
