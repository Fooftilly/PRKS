import { createVNode } from 'vue'
import {
  dismissRouteSurface,
  presentRouteSurface,
  registerEarlyRoutePresenter,
  registerRouteWindowBridge,
  resetRouteSurfaceForTests,
  type RouteSurfaceOwner,
} from '../../route-surface/lifecycle'
import PlaylistDetailRoute from './PlaylistDetailRoute.vue'
import PlaylistIntentsProvider from './PlaylistIntentsProvider.vue'
import PlaylistsIndexRoute from './PlaylistsIndexRoute.vue'
import type { PlaylistIntentOwner } from './intents'
import {
  buildPlaylistDetailProjection,
  buildPlaylistIndexProjection,
  type PlaylistDetailProjection,
  type PlaylistIndexProjection,
} from './projection'
import type { PlaylistDetailRouteInstance, PlaylistsIndexRouteInstance } from './route'
import type { PlaylistDetailAvailability, PlaylistIndexAvailability } from './types'

const PLAYLISTS_FEATURE = 'playlists'
const PLAYLIST_DETAIL_FEATURE = 'playlist-detail'

/** Set by the route coordinator before beginRoute when staying on Playlists. */
export const PLAYLISTS_RETAIN_SURFACE_KEY = '__prksRetainPlaylistsSurface'
const PLAYLISTS_CLEANUP_ARMED_KEY = '__prksPlaylistsCleanupArmed'

type PlaylistsOwner = RouteSurfaceOwner &
  PlaylistIntentOwner & {
    [PLAYLISTS_RETAIN_SURFACE_KEY]?: boolean
    [PLAYLISTS_CLEANUP_ARMED_KEY]?: boolean
  }

/**
 * Playlists dismisses on leave/destroy, not on every beginRoute.
 * A retained refresh sets PLAYLISTS_RETAIN_SURFACE_KEY so this cleanup does
 * not unmount, then re-arms immediately: beginRoute already drained the set,
 * and a failed refresh never reaches present to register another callback.
 */
function armPlaylistsOwnerCleanup(owner: PlaylistsOwner): void {
  if (owner[PLAYLISTS_CLEANUP_ARMED_KEY] || typeof owner.registerCleanup !== 'function') return
  owner[PLAYLISTS_CLEANUP_ARMED_KEY] = true
  owner.registerCleanup(() => {
    owner[PLAYLISTS_CLEANUP_ARMED_KEY] = false
    if (owner[PLAYLISTS_RETAIN_SURFACE_KEY]) {
      armPlaylistsOwnerCleanup(owner)
      return
    }
    dismissRouteSurface(owner)
  })
}

export interface PlaylistsIndexPresentInput {
  owner: PlaylistsOwner
  host: HTMLElement
  availability?: PlaylistIndexAvailability
  items?: unknown
  generation?: number
  shell?: boolean
}

export interface PlaylistDetailPresentInput {
  owner: PlaylistsOwner
  host: HTMLElement
  availability?: PlaylistDetailAvailability
  playlist?: unknown
  playlistId?: string
  editing?: boolean
  renaming?: unknown
  generation?: number
  shell?: boolean
}

export function presentPlaylistsIndex(input: PlaylistsIndexPresentInput): void {
  if (!input || !input.owner || typeof input.owner !== 'object') return
  const availability: PlaylistIndexAvailability =
    input.availability === 'unavailable' ? 'unavailable' : 'ready'
  const items = input.items
  const route: Omit<PlaylistsIndexRouteInstance, 'generation'> & { generation?: number } = {
    name: 'playlists',
    canonicalHash: '#/playlists',
    params: {},
    ownsMainShell: input.shell !== false,
    generation: input.generation,
  }
  presentRouteSurface({
    owner: input.owner,
    host: input.host,
    route,
    armBeginRouteCleanup: false,
    render: (generation) => {
      const projection: PlaylistIndexProjection = buildPlaylistIndexProjection({
        availability,
        items,
        generation,
      })
      return createVNode(
        PlaylistIntentsProvider,
        { owner: input.owner, generation },
        { default: () => createVNode(PlaylistsIndexRoute, { projection }) },
      )
    },
  })
  armPlaylistsOwnerCleanup(input.owner)
}

export function presentPlaylistDetail(input: PlaylistDetailPresentInput): void {
  if (!input || !input.owner || typeof input.owner !== 'object') return
  const availability: PlaylistDetailAvailability =
    input.availability === 'unavailable' || input.availability === 'not-found'
      ? input.availability
      : 'ready'
  const playlist = input.playlist
  const playlistId =
    typeof input.playlistId === 'string' && input.playlistId
      ? input.playlistId
      : typeof playlist === 'object' && playlist && 'id' in playlist
        ? String((playlist as { id?: unknown }).id || '')
        : ''
  const editing = input.editing === true
  const renaming = input.renaming
  const route: Omit<PlaylistDetailRouteInstance, 'generation'> & { generation?: number } = {
    name: 'playlist-detail',
    canonicalHash: playlistId ? `#/playlists/${encodeURIComponent(playlistId)}` : '#/playlists',
    params: { playlistId },
    ownsMainShell: input.shell !== false,
    generation: input.generation,
  }
  presentRouteSurface({
    owner: input.owner,
    host: input.host,
    route,
    armBeginRouteCleanup: false,
    render: (generation) => {
      const projection: PlaylistDetailProjection = buildPlaylistDetailProjection({
        availability,
        playlist,
        playlistId,
        editing,
        renaming,
        generation,
      })
      return createVNode(
        PlaylistIntentsProvider,
        { owner: input.owner, generation },
        { default: () => createVNode(PlaylistDetailRoute, { projection }) },
      )
    },
  })
  armPlaylistsOwnerCleanup(input.owner)
}

export function resetPlaylistsSessionForTests(): void {
  resetRouteSurfaceForTests()
}

function isIndexEarlyRequest(
  value: unknown,
): value is Omit<PlaylistsIndexPresentInput, 'host'> & { feature: typeof PLAYLISTS_FEATURE } {
  if (!value || typeof value !== 'object') return false
  const record = value as Partial<PlaylistsIndexPresentInput> & { feature?: unknown }
  return record.feature === PLAYLISTS_FEATURE && !!record.owner
}

function isDetailEarlyRequest(
  value: unknown,
): value is Omit<PlaylistDetailPresentInput, 'host'> & { feature: typeof PLAYLIST_DETAIL_FEATURE } {
  if (!value || typeof value !== 'object') return false
  const record = value as Partial<PlaylistDetailPresentInput> & { feature?: unknown }
  return record.feature === PLAYLIST_DETAIL_FEATURE && !!record.owner
}

export function registerPlaylistsBridge(target: Window = window): void {
  registerRouteWindowBridge(target)
  registerEarlyRoutePresenter(
    PLAYLISTS_FEATURE,
    (request, host) => {
      if (!isIndexEarlyRequest(request)) return false
      const { owner, availability, items, generation, shell } = request
      presentPlaylistsIndex({ owner, host, availability, items, generation, shell })
      return true
    },
    target,
  )
  registerEarlyRoutePresenter(
    PLAYLIST_DETAIL_FEATURE,
    (request, host) => {
      if (!isDetailEarlyRequest(request)) return false
      const { owner, availability, playlist, playlistId, editing, renaming, generation, shell } = request
      presentPlaylistDetail({
        owner,
        host,
        availability,
        playlist,
        playlistId,
        editing,
        renaming,
        generation,
        shell,
      })
      return true
    },
    target,
  )
}
