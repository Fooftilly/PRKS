import { describe, expect, it } from 'vitest'
import {
  adoptPaintedWorkRoute,
  projectWorkRoute,
  publishWorkRouteProjection,
  replaceWorkRoutePlacement,
  workOpenShouldRecord,
} from './projection'
import type { WorkRouteOwnerContext, WorkRouteProjectInput, WorkRouteProjection } from './types'

function owner(tabId: string, generation: number) {
  return { tabId, generation }
}

function input(partial: Partial<WorkRouteProjectInput> & Pick<WorkRouteProjectInput, 'workId'>): WorkRouteProjectInput {
  return {
    owner: owner('main', 1),
    availability: 'ready',
    lifecycle: 'ordinary',
    provenance: 'server',
    work: { id: partial.workId, title: 'File' },
    recordOpen: false,
    ...partial,
  }
}

function ctxFor(tabId: string, generation: number): WorkRouteOwnerContext & {
  entities: Map<string, unknown>
  resources: Map<string, unknown>
  entityCalls: unknown[]
} {
  const entities = new Map<string, unknown>()
  const resources = new Map<string, unknown>()
  const entityCalls: unknown[] = []
  return {
    tabId,
    generation,
    entities,
    resources,
    entityCalls,
    isCurrent(token: number) {
      return token === generation
    },
    setEntity(type: string, value: unknown) {
      entityCalls.push(value)
      entities.set(type, value)
    },
    getEntity(type: string) {
      return entities.get(type) ?? null
    },
    setResource(name: string, value: unknown) {
      resources.set(name, value)
    },
    getResource(name: string) {
      return resources.get(name)
    },
  }
}

describe('projectWorkRoute', () => {
  it('projects an ordinary server Work as ready', () => {
    const projection = projectWorkRoute(input({ workId: 'w1', provenance: 'server', work: { id: 'w1', title: 'Server' } }))
    expect(projection.availability).toBe('ready')
    expect(projection.lifecycle).toBe('ordinary')
    expect(projection.provenance).toBe('server')
    expect(projection.work?.title).toBe('Server')
  })

  it('projects a cached Work as ready with cached provenance', () => {
    const projection = projectWorkRoute(
      input({ workId: 'w1', provenance: 'cache', work: { id: 'w1', title: 'Cached' } }),
    )
    expect(projection.availability).toBe('ready')
    expect(projection.provenance).toBe('cache')
    expect(projection.work?.title).toBe('Cached')
  })

  it('keeps unavailable distinct and publishes no Work', () => {
    const projection = projectWorkRoute(
      input({
        workId: 'w1',
        availability: 'unavailable',
        provenance: 'server',
        work: { id: 'w1', title: 'Should not publish' },
      }),
    )
    expect(projection.availability).toBe('unavailable')
    expect(projection.availability).not.toBe('not-found')
    expect(projection.work).toBeNull()
  })

  it('keeps not-found distinct from unavailable', () => {
    const projection = projectWorkRoute(
      input({ workId: 'missing', availability: 'not-found', provenance: 'server', work: null }),
    )
    expect(projection.availability).toBe('not-found')
    expect(projection.work).toBeNull()
  })

  it('projects a pending unsent create as a local ready Work', () => {
    const projection = projectWorkRoute(
      input({
        workId: 'local-1',
        lifecycle: 'unsent-create',
        provenance: 'server',
        work: { id: 'local-1', title: 'Unsent' },
      }),
    )
    expect(projection.availability).toBe('ready')
    expect(projection.lifecycle).toBe('unsent-create')
    expect(projection.provenance).toBe('local-unsent')
    expect(projection.work?.title).toBe('Unsent')
  })

  it('does not publish a cached row when the Work is pending deletion', () => {
    const projection = projectWorkRoute(
      input({
        workId: 'w1',
        lifecycle: 'pending-delete',
        availability: 'ready',
        provenance: 'cache',
        work: { id: 'w1', title: 'Cached but deleted' },
      }),
    )
    expect(projection.availability).toBe('unavailable')
    expect(projection.lifecycle).toBe('pending-delete')
    expect(projection.work).toBeNull()
  })

  it('keeps effective pending metadata on the projected Work', () => {
    const projection = projectWorkRoute(
      input({
        workId: 'w1',
        work: { id: 'w1', title: 'Pending title', status: 'reading' },
      }),
    )
    expect(projection.work?.title).toBe('Pending title')
    expect(projection.work?.status).toBe('reading')
  })

  it('keeps an effective video source as one identity', () => {
    const source = {
      id: 'w1',
      source_kind: 'video',
      source_url: 'https://www.youtube.com/watch?v=abcdefghijk',
      provider: 'youtube',
      provider_id: 'abcdefghijk',
    }
    const projection = projectWorkRoute(input({ workId: 'w1', work: source }))
    expect(projection.work).toMatchObject({
      source_kind: 'video',
      source_url: source.source_url,
      provider: 'youtube',
      provider_id: 'abcdefghijk',
    })
    expect(projection.work).not.toBe(source)
  })

  it('keeps the effective folder placement', () => {
    const projection = projectWorkRoute(
      input({ workId: 'w1', work: { id: 'w1', title: 'Filed', folder_id: 'folder-2', folder_title: 'Notes' } }),
    )
    expect(projection.work?.folder_id).toBe('folder-2')
    expect(projection.work?.folder_title).toBe('Notes')
  })

  it('keeps the effective playlist placement', () => {
    const projection = projectWorkRoute(
      input({ workId: 'w1', work: { id: 'w1', title: 'Queued', playlist_id: 'playlist-9' } }),
    )
    expect(projection.work?.playlist_id).toBe('playlist-9')
  })

  it('gives Main and Secondary independent projection objects', () => {
    const shared = { id: 'w1', title: 'Shared title' }
    const main = projectWorkRoute(input({ workId: 'w1', owner: owner('main', 3), work: { ...shared, title: 'Main' } }))
    const secondary = projectWorkRoute(
      input({ workId: 'w2', owner: owner('secondary', 4), work: { id: 'w2', title: 'Secondary' } }),
    )
    expect(main.work).not.toBe(secondary.work)
    expect(main.ownerTabId).toBe('main')
    expect(secondary.ownerTabId).toBe('secondary')
    if (main.work) main.work.title = 'Mutated'
    expect(secondary.work?.title).toBe('Secondary')
  })

  it('does not let a stale generation publish', () => {
    const ctx = ctxFor('main', 2)
    const projection = projectWorkRoute(input({ workId: 'w1', owner: owner('main', 1) }))
    expect(publishWorkRouteProjection(ctx, 1, projection)).toBeNull()
    expect(ctx.entityCalls).toEqual([])
    expect(ctx.getResource('workRouteProjection')).toBeUndefined()
  })

  it('does not publish Work A after the generation has moved to Work B', () => {
    const ctx = ctxFor('main', 2)
    const staleA = projectWorkRoute(
      input({ workId: 'A', owner: owner('main', 1), work: { id: 'A', title: 'A' } }),
    )
    expect(publishWorkRouteProjection(ctx, 1, staleA)).toBeNull()
    const currentB = projectWorkRoute(
      input({ workId: 'B', owner: owner('main', 2), work: { id: 'B', title: 'B' } }),
    )
    const published = publishWorkRouteProjection(ctx, 2, currentB)
    expect(published?.workId).toBe('B')
    expect(ctx.getEntity('work')).toMatchObject({ id: 'B', title: 'B' })
    expect(ctx.entityCalls).toHaveLength(1)
  })
})

describe('work open recording', () => {
  it('does not record another open for internalRefresh', () => {
    expect(workOpenShouldRecord(true, { id: 'w1' })).toBe(false)
    const projection = projectWorkRoute(input({ workId: 'w1', recordOpen: false }))
    expect(projection.recordOpen).toBe(false)
  })

  it('records a genuine foreground open', () => {
    expect(workOpenShouldRecord(false, { id: 'w1', title: 'File' })).toBe(true)
    expect(workOpenShouldRecord(false, null)).toBe(false)
    const projection = projectWorkRoute(input({ workId: 'w1', recordOpen: true }))
    expect(projection.recordOpen).toBe(true)
  })
})

describe('owner publication', () => {
  it('replaces folder placement only for the same still-owned Work', () => {
    const main = ctxFor('main', 1)
    const secondary = ctxFor('secondary', 1)
    const mainProjection = projectWorkRoute(
      input({ workId: 'w1', owner: owner('main', 1), work: { id: 'w1', folder_id: 'old' } }),
    )
    const secondaryProjection = projectWorkRoute(
      input({
        workId: 'w2',
        owner: owner('secondary', 1),
        work: { id: 'w2', folder_id: 'other' },
      }),
    )
    publishWorkRouteProjection(main, 1, mainProjection)
    publishWorkRouteProjection(secondary, 1, secondaryProjection)
    const replaced = replaceWorkRoutePlacement(main, 1, { id: 'w1', folder_id: 'new', playlist_id: 'p' })
    expect(replaced?.work?.folder_id).toBe('new')
    expect(replaced?.recordOpen).toBe(false)
    expect((secondary.getResource('workRouteProjection') as WorkRouteProjection).work?.folder_id).toBe('other')
    expect(replaceWorkRoutePlacement(main, 1, { id: 'w2', folder_id: 'nope' })).toBeNull()
  })

  it('drops a ready cached projection when a later publish is pending deletion', () => {
    const ctx = ctxFor('main', 1)
    publishWorkRouteProjection(
      ctx,
      1,
      projectWorkRoute(input({ workId: 'w1', provenance: 'cache', work: { id: 'w1', title: 'Cached' } })),
    )
    const deleted = publishWorkRouteProjection(
      ctx,
      1,
      projectWorkRoute(
        input({
          workId: 'w1',
          availability: 'unavailable',
          lifecycle: 'pending-delete',
          provenance: 'cache',
          work: { id: 'w1', title: 'Cached' },
          recordOpen: false,
        }),
      ),
    )
    expect(deleted?.availability).toBe('unavailable')
    expect(deleted?.lifecycle).toBe('pending-delete')
    expect(deleted?.work).toBeNull()
    expect(ctx.getEntity('work')).toBeNull()
    expect(replaceWorkRoutePlacement(ctx, 1, { id: 'w1', title: 'Cached', folder_id: 'later' })).toBeNull()
    expect(ctx.getEntity('work')).toBeNull()
    expect((ctx.getResource('workRouteProjection') as WorkRouteProjection).work).toBeNull()
  })

  it('does not adopt a painted Work after the generation moves', () => {
    let currentGeneration = 1
    const ctx = ctxFor('main', 1)
    ctx.isCurrent = (token: number) => token === currentGeneration
    publishWorkRouteProjection(
      ctx,
      1,
      projectWorkRoute(input({ workId: 'w1', owner: owner('main', 1), work: { id: 'w1', title: 'A' } })),
    )
    currentGeneration = 2
    ctx.setEntity('work', { id: 'w1', title: 'Painted later' })
    expect(adoptPaintedWorkRoute(ctx, 1, 'w1')).toBeNull()
    const stored = ctx.getResource('workRouteProjection') as WorkRouteProjection
    expect(stored.work?.title).toBe('A')
  })
})
