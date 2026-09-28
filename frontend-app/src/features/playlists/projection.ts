import { playlistItemCountLabel, playlistWorkSubtitle } from './format'
import type {
  PlaylistDetail,
  PlaylistDetailAvailability,
  PlaylistIndexAvailability,
  PlaylistIndexItem,
  PlaylistWorkItem,
} from './types'

export interface PlaylistIndexProjection {
  readonly availability: PlaylistIndexAvailability
  readonly items: readonly PlaylistIndexItem[]
  readonly generation: number
}

export interface PlaylistDetailProjection {
  readonly availability: PlaylistDetailAvailability
  readonly playlist: PlaylistDetail | null
  readonly playlistId: string
  readonly editing: boolean
  readonly renamingIds: readonly string[]
  readonly generation: number
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  return value as Record<string, unknown>
}

function text(value: unknown, fallback = ''): string {
  if (value == null) return fallback
  return String(value)
}

export function playlistIndexItemFromRow(row: unknown): PlaylistIndexItem | null {
  const record = asRecord(row)
  if (!record) return null
  const id = text(record.id).trim()
  if (!id) return null
  const count = Number(record.item_count ?? 0)
  return {
    id,
    title: text(record.title).trim() || 'Playlist',
    description: text(record.description),
    itemCount: Number.isFinite(count) && count >= 0 ? count : 0,
  }
}

export function playlistWorkFromRow(row: unknown): PlaylistWorkItem | null {
  const record = asRecord(row)
  if (!record) return null
  const id = text(record.id).trim()
  if (!id) return null
  return {
    id,
    title: text(record.title).trim() || 'Untitled',
    subtitle: playlistWorkSubtitle(record),
  }
}

export function buildPlaylistIndexProjection(input: {
  availability?: PlaylistIndexAvailability
  items?: unknown
  generation?: number
}): PlaylistIndexProjection {
  const availability: PlaylistIndexAvailability =
    input.availability === 'unavailable' ? 'unavailable' : 'ready'
  const items = Array.isArray(input.items)
    ? input.items.map(playlistIndexItemFromRow).filter((row): row is PlaylistIndexItem => !!row)
    : []
  return {
    availability,
    items,
    generation: typeof input.generation === 'number' ? input.generation : 0,
  }
}

export function buildPlaylistDetailProjection(input: {
  availability?: PlaylistDetailAvailability
  playlist?: unknown
  playlistId?: string
  editing?: boolean
  renaming?: unknown
  generation?: number
}): PlaylistDetailProjection {
  const availability: PlaylistDetailAvailability =
    input.availability === 'unavailable' || input.availability === 'not-found'
      ? input.availability
      : 'ready'
  const record = asRecord(input.playlist)
  const playlistId =
    text(input.playlistId).trim() || (record ? text(record.id).trim() : '')
  let playlist: PlaylistDetail | null = null
  if (availability === 'ready' && record && playlistId) {
    const items = Array.isArray(record.items)
      ? record.items.map(playlistWorkFromRow).filter((row): row is PlaylistWorkItem => !!row)
      : []
    playlist = {
      id: playlistId,
      title: text(record.title).trim() || 'Playlist',
      description: text(record.description),
      originalUrl: text(record.original_url).trim(),
      items,
    }
  }
  const renamingIds: string[] = []
  const renaming = asRecord(input.renaming)
  if (renaming) {
    for (const [id, on] of Object.entries(renaming)) {
      if (on === true && id) renamingIds.push(id)
    }
  }
  return {
    availability: playlist || availability !== 'ready' ? availability : 'not-found',
    playlist,
    playlistId,
    editing: input.editing === true && !!playlist,
    renamingIds,
    generation: typeof input.generation === 'number' ? input.generation : 0,
  }
}

export { playlistItemCountLabel }
