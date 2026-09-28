import type { PrksRouteInstanceBase } from '../../route-surface/route-instance'

export interface PlaylistsIndexRouteInstance extends PrksRouteInstanceBase {
  readonly name: 'playlists'
  readonly params: Record<string, never>
}

export interface PlaylistDetailRouteInstance extends PrksRouteInstanceBase {
  readonly name: 'playlist-detail'
  readonly params: { readonly playlistId: string }
}
