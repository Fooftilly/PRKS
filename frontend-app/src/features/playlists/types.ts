/** Playlist index/detail types. Effective values arrive already overlaid. */

export type PlaylistIndexAvailability = 'ready' | 'unavailable'

export type PlaylistDetailAvailability = 'ready' | 'unavailable' | 'not-found'

export interface PlaylistIndexItem {
  readonly id: string
  readonly title: string
  readonly description: string
  readonly itemCount: number
}

export interface PlaylistWorkItem {
  readonly id: string
  readonly title: string
  readonly subtitle: string
}

export interface PlaylistDetail {
  readonly id: string
  readonly title: string
  readonly description: string
  readonly originalUrl: string
  readonly items: readonly PlaylistWorkItem[]
}

/** Fields the metadata editor may send. Only dirty keys are passed to updatePlaylist. */
export interface PlaylistFieldDraft {
  title: string
  description: string
  original_url: string
}
