import { describe, expect, it } from 'vitest'
import { playlistItemCountLabel, playlistWorkSubtitle } from './format'
import { buildPlaylistDetailProjection, buildPlaylistIndexProjection } from './projection'

describe('Playlist projections', () => {
  it('keeps effective index rows, including a local create, and drops nothing the coordinator omitted', () => {
    const projection = buildPlaylistIndexProjection({
      availability: 'ready',
      items: [
        { id: 'local-1', title: 'Unsent', description: 'made here', item_count: 0 },
        { id: 'PL-2', title: 'Renamed', description: '', item_count: 2 },
      ],
      generation: 3,
    })
    expect(projection.availability).toBe('ready')
    expect(projection.items.map((item) => item.title)).toEqual(['Unsent', 'Renamed'])
    expect(projection.items[0]?.itemCount).toBe(0)
    expect(projection.items[1]?.itemCount).toBe(2)
    expect(projection.items.some((item) => item.id === 'deleted')).toBe(false)
  })

  it('distinguishes an unavailable catalogue from a cached empty one', () => {
    expect(buildPlaylistIndexProjection({ availability: 'unavailable', items: [] }).availability).toBe(
      'unavailable',
    )
    const empty = buildPlaylistIndexProjection({ availability: 'ready', items: [] })
    expect(empty.availability).toBe('ready')
    expect(empty.items).toEqual([])
  })

  it('formats work subtitles and item counts the way the legacy renderer did', () => {
    expect(playlistWorkSubtitle({ author_text: 'Channel', published_date: '2021-03-04' })).toBe(
      'Channel · 04/03/2021',
    )
    expect(playlistItemCountLabel(1)).toBe('1 item')
    expect(playlistItemCountLabel(2)).toBe('2 items')
  })

  it('preserves effective detail fields, membership, and order', () => {
    const projection = buildPlaylistDetailProjection({
      availability: 'ready',
      editing: true,
      playlist: {
        id: 'PL-1',
        title: 'Pending title',
        description: 'Pending description',
        original_url: 'https://example.test/list',
        items: [
          { id: 'W2', title: 'Second', author_text: 'Channel', published_date: '2021-03-04' },
          { id: 'W1', title: 'Overlay title', author_text: '', published_date: '' },
        ],
      },
      generation: 4,
    })
    expect(projection.availability).toBe('ready')
    expect(projection.editing).toBe(true)
    expect(projection.playlist?.title).toBe('Pending title')
    expect(projection.playlist?.description).toBe('Pending description')
    expect(projection.playlist?.originalUrl).toBe('https://example.test/list')
    expect(projection.playlist?.items.map((item) => item.id)).toEqual(['W2', 'W1'])
    expect(projection.playlist?.items[0]?.subtitle).toBe('Channel · 04/03/2021')
    expect(projection.playlist?.items[1]?.title).toBe('Overlay title')
  })

  it('splits offline-unavailable from a reachable-server miss', () => {
    expect(
      buildPlaylistDetailProjection({ availability: 'unavailable', playlistId: 'PL-1' }).availability,
    ).toBe('unavailable')
    expect(
      buildPlaylistDetailProjection({ availability: 'ready', playlist: null, playlistId: 'missing' })
        .availability,
    ).toBe('not-found')
    const local = buildPlaylistDetailProjection({
      availability: 'ready',
      playlist: { id: 'local-9', title: 'Unsent playlist', description: '', items: [] },
    })
    expect(local.availability).toBe('ready')
    expect(local.playlist?.id).toBe('local-9')
    expect(local.playlist?.items).toEqual([])
  })
})
