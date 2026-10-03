import { describe, expect, it } from 'vitest'
import {
  createOwnerResourceRegistry,
  resourceTicket,
  type OwnerResourceHost,
  type OwnerResourceRegistry,
  type ResourceRegistration,
} from './owner-resource'

interface HostState {
  ownerId: string
  token: object
  generation: number
  alive: boolean
}

function hostOf(state: HostState): OwnerResourceHost {
  return {
    ownerId: state.ownerId,
    ownerToken: state.token,
    generation: () => state.generation,
    alive: () => state.alive,
  }
}

function openHost(ownerId = 'tab-a'): { state: HostState; host: OwnerResourceHost; registry: OwnerResourceRegistry } {
  const state: HostState = { ownerId, token: {}, generation: 1, alive: true }
  const host = hostOf(state)
  return { state, host, registry: createOwnerResourceRegistry(host) }
}

describe('owner resource lifetime', () => {
  it('replacing the same owner disposes the previous value once', () => {
    const { host, registry } = openHost()
    const ticket = resourceTicket(host)
    let disposed = 0
    registry.register(ticket, {
      kind: 'researchGraph',
      value: 'A',
      dispose: () => {
        disposed += 1
        registry.dispose('researchGraph')
      },
    })
    const result = registry.register(ticket, {
      kind: 'researchGraph',
      value: 'B',
      dispose: () => {
        disposed += 10
      },
    })
    expect(result).toBe('replaced')
    expect(disposed).toBe(1)
    expect(registry.get('researchGraph')).toBe('B')
  })

  it('releasing an owner disposes its graph once', () => {
    const { state, host, registry } = openHost()
    let disposed = 0
    registry.register(resourceTicket(host), {
      kind: 'researchGraph',
      value: { id: 'graph' },
      dispose: () => {
        disposed += 1
      },
    })
    state.alive = false
    registry.releaseAll()
    registry.releaseAll()
    registry.dispose('researchGraph')
    expect(disposed).toBe(1)
    expect(registry.get('researchGraph')).toBeUndefined()
  })

  it('keeps two owners independent', () => {
    const main = openHost('main')
    const side = openHost('side')
    let mainDisposed = 0
    let sideDisposed = 0
    main.registry.register(resourceTicket(main.host), {
      kind: 'researchGraph',
      value: 'main-graph',
      dispose: () => {
        mainDisposed += 1
      },
    })
    side.registry.register(resourceTicket(side.host), {
      kind: 'researchGraph',
      value: 'side-graph',
      dispose: () => {
        sideDisposed += 1
      },
    })
    main.registry.releaseAll()
    expect(mainDisposed).toBe(1)
    expect(sideDisposed).toBe(0)
    expect(side.registry.get('researchGraph')).toBe('side-graph')
  })

  it('warm park keeps only a suspendable resource and does not recreate it', () => {
    const { host, registry } = openHost()
    const ticket = resourceTicket(host)
    const graph = { cy: 1 }
    const pdf = { viewer: 1 }
    let graphDisposed = 0
    let pdfDisposed = 0
    let pdfSuspended = 0
    let pdfResumed = 0
    registry.register(ticket, {
      kind: 'researchGraph',
      value: graph,
      suspendable: false,
      dispose: () => {
        graphDisposed += 1
      },
    })
    registry.register(ticket, {
      kind: 'pdf',
      value: pdf,
      suspendable: true,
      dispose: () => {
        pdfDisposed += 1
      },
      suspend: () => {
        pdfSuspended += 1
      },
      resume: () => {
        pdfResumed += 1
      },
    })
    registry.warmSuspend()
    registry.warmSuspend()
    expect(graphDisposed).toBe(1)
    expect(registry.get('researchGraph')).toBeUndefined()
    expect(pdfDisposed).toBe(0)
    expect(pdfSuspended).toBe(1)
    expect(registry.get('pdf')).toBe(pdf)
    registry.resume()
    registry.resume()
    expect(pdfResumed).toBe(1)
    expect(pdfDisposed).toBe(0)
    expect(registry.get('pdf')).toBe(pdf)
    expect(registry.get('researchGraph')).toBeUndefined()
  })

  it('cold release disposes a graph and leaves nothing to resume', () => {
    const { host, registry } = openHost()
    let disposed = 0
    registry.register(resourceTicket(host), {
      kind: 'researchGraph',
      value: { cy: 1 },
      dispose: () => {
        disposed += 1
      },
    })
    registry.releaseAll()
    registry.resume()
    expect(disposed).toBe(1)
    expect(registry.get('researchGraph')).toBeUndefined()
    expect(registry.kinds()).toEqual([])
  })

  it('rejects a stale generation after the owner has moved on', () => {
    const { state, host, registry } = openHost()
    const original = resourceTicket(host, 1)
    registry.register(original, {
      kind: 'researchGraph',
      value: 'route-a',
      dispose: () => {},
    })
    registry.releaseAll()
    state.generation = 2
    registry.register(resourceTicket(host, 2), {
      kind: 'researchGraph',
      value: 'route-b',
      dispose: () => {},
    })
    const result = registry.register(original, {
      kind: 'researchGraph',
      value: 'stale',
      dispose: () => {},
    })
    expect(result).toBe('rejected')
    expect(registry.accepts(original)).toBe(false)
    expect(registry.get('researchGraph')).toBe('route-b')
  })

  it('does not treat a role change as a resource event', () => {
    const { host, registry } = openHost('leaf')
    const graph = { cy: 1 }
    let disposed = 0
    registry.register(resourceTicket(host), {
      kind: 'researchGraph',
      value: graph,
      dispose: () => {
        disposed += 1
      },
    })
    const role = { main: false }
    role.main = true
    expect(registry.get('researchGraph')).toBe(graph)
    expect(disposed).toBe(0)
    expect(registry.kinds()).toEqual(['researchGraph'])
  })

  it('rejects a ticket from a replaced owner with the same id', () => {
    const first = openHost('tab-1')
    const ticket = resourceTicket(first.host, 1)
    first.registry.register(ticket, {
      kind: 'researchGraph',
      value: 'old',
      dispose: () => {},
    })
    first.state.alive = false
    const next = openHost('tab-1')
    next.state.generation = 1
    const registration: ResourceRegistration<string> = {
      kind: 'researchGraph',
      value: 'from-old-work',
      dispose: () => {},
    }
    expect(next.registry.register(ticket, registration)).toBe('rejected')
    expect(next.registry.get('researchGraph')).toBeUndefined()
    expect(next.registry.register(resourceTicket(next.host), {
      kind: 'researchGraph',
      value: 'fresh',
      dispose: () => {},
    })).toBe('attached')
    expect(next.registry.get('researchGraph')).toBe('fresh')
  })
})
