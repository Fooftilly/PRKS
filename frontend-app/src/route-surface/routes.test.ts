import { describe, expect, it } from 'vitest'
import type { PrksRouteInstance, PrksRouteInstanceInput } from './routes'

function acceptRouteInput(route: PrksRouteInstanceInput): PrksRouteInstanceInput {
  return route
}

/**
 * Fails typecheck when a mounted route is added to `PrksRouteInstance` and
 * this switch is not updated. The legacy parser is not involved.
 */
function routeName(route: PrksRouteInstance): PrksRouteInstance['name'] {
  switch (route.name) {
    case 'folders':
    case 'folder-detail':
    case 'recent':
    case 'saved-views':
    case 'saved-view-detail':
    case 'types':
    case 'type-detail':
    case 'playlists':
    case 'playlist-detail':
    case 'tags':
    case 'publishers':
    case 'concepts':
    case 'concept-detail':
    case 'positions':
    case 'position-detail':
    case 'arguments':
    case 'argument-detail':
    case 'processing-files':
    case 'search':
    case 'progress':
    case 'people':
    case 'person':
    case 'people-groups':
    case 'person-group-detail':
    case 'research-graph':
      return route.name
    default: {
      const unreachable: never = route
      return unreachable
    }
  }
}

describe('PrksRouteInstance', () => {
  it('narrows params from the mounted route name', () => {
    const search: PrksRouteInstance = {
      name: 'search',
      canonicalHash: '#/search?q=adorno',
      params: { q: 'adorno', tag: '', author: '', publisher: '', any: false },
      ownsMainShell: true,
      generation: 1,
    }
    const detail: PrksRouteInstance = {
      name: 'concept-detail',
      canonicalHash: '#/concepts/C-1',
      params: { conceptId: 'C-1' },
      ownsMainShell: false,
      generation: 2,
    }
    expect(routeName(search)).toBe('search')
    expect(routeName(detail)).toBe('concept-detail')
    if (search.name === 'search') expect(search.params.q).toBe('adorno')
    if (detail.name === 'concept-detail') expect(detail.params.conceptId).toBe('C-1')
  })

  it('rejects a route name paired with another member’s params', () => {
    const recent = acceptRouteInput({
      name: 'recent',
      canonicalHash: '#/recent',
      params: {},
      ownsMainShell: true,
    })
    expect(recent.name).toBe('recent')
    // @ts-expect-error recent cannot carry concept-detail params
    acceptRouteInput({
      name: 'recent',
      canonicalHash: '#/recent',
      params: { conceptId: 'C-1' },
      ownsMainShell: false,
    })
  })
})
