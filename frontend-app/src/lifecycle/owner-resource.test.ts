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
  mounted: boolean
  suspended: boolean
  epoch: number
}

function hostOf(state: HostState): OwnerResourceHost {
  return {
    ownerId: state.ownerId,
    ownerToken: state.token,
    generation: () => state.generation,
    alive: () => state.alive && (state.mounted || state.suspended),
    epoch: () => state.epoch,
    advanceEpoch: () => {
      state.epoch += 1
    },
  }
}

function openHost(ownerId = 'tab-a'): { state: HostState; host: OwnerResourceHost; registry: OwnerResourceRegistry } {
  const state: HostState = {
    ownerId,
    token: {},
    generation: 1,
    alive: true,
    mounted: true,
    suspended: false,
    epoch: 0,
  }
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

  it('warm park keeps a pdf that declares no suspend or resume hook', () => {
    const { host, registry } = openHost('pdf-no-hook')
    const ticket = resourceTicket(host)
    const pdf = { viewer: 1 }
    let disposed = 0
    registry.register(ticket, {
      kind: 'pdf',
      value: pdf,
      suspendable: true,
      dispose: () => {
        disposed += 1
      },
    })
    registry.warmSuspend()
    expect(registry.get('pdf')).toBe(pdf)
    expect(disposed).toBe(0)
    registry.resume()
    expect(registry.get('pdf')).toBe(pdf)
    expect(disposed).toBe(0)
    const next = { viewer: 2 }
    expect(registry.register(ticket, {
      kind: 'pdf',
      value: next,
      suspendable: true,
      dispose: () => {
        disposed += 1
      },
    })).toBe('replaced')
    expect(disposed).toBe(1)
    expect(registry.get('pdf')).toBe(next)
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

  it('rejects a non-suspendable registration while warm-parked without staling the ticket', () => {
    const parked = openHost('parked-graph')
    const ticket = resourceTicket(parked.host)
    parked.registry.warmSuspend()
    let disposed = 0
    expect(parked.registry.register(ticket, {
      kind: 'researchGraph',
      value: 'late-graph',
      suspendable: false,
      dispose: () => {
        disposed += 1
      },
    })).toBe('rejected')
    expect(disposed).toBe(0)
    expect(parked.registry.get('researchGraph')).toBeUndefined()
    expect(parked.registry.accepts(ticket)).toBe(true)

    parked.registry.resume()
    expect(parked.registry.register(ticket, {
      kind: 'researchGraph',
      value: 'after-resume',
      dispose: () => {
        disposed += 1
      },
    })).toBe('attached')
    expect(parked.registry.get('researchGraph')).toBe('after-resume')
    expect(disposed).toBe(0)
    parked.registry.releaseAll()
    expect(disposed).toBe(1)
    expect(parked.registry.get('researchGraph')).toBeUndefined()

    const cold = openHost('cold-graph')
    const coldTicket = resourceTicket(cold.host)
    cold.registry.warmSuspend()
    let coldDisposed = 0
    expect(cold.registry.register(coldTicket, {
      kind: 'researchGraph',
      value: 'during-park',
      dispose: () => {
        coldDisposed += 1
      },
    })).toBe('rejected')
    expect(coldDisposed).toBe(0)
    cold.registry.releaseAll()
    expect(cold.state.generation).toBe(1)
    expect(cold.registry.accepts(coldTicket)).toBe(false)
    expect(cold.registry.register(coldTicket, {
      kind: 'researchGraph',
      value: 'after-cold',
      dispose: () => {},
    })).toBe('rejected')
    expect(cold.registry.get('researchGraph')).toBeUndefined()
    expect(cold.registry.register(resourceTicket(cold.host), {
      kind: 'researchGraph',
      value: 'fresh-after-cold',
      dispose: () => {},
    })).toBe('attached')
  })

  it('attaches a suspendable registration during warm park and resumes or cold-releases it', () => {
    const parked = openHost('parked-pdf')
    const ticket = resourceTicket(parked.host)
    parked.registry.warmSuspend()
    const pdf = { viewer: 1 }
    let disposed = 0
    let suspended = 0
    let resumed = 0
    expect(parked.registry.register(ticket, {
      kind: 'pdf',
      value: pdf,
      suspendable: true,
      dispose: () => {
        disposed += 1
      },
      suspend: () => {
        suspended += 1
      },
      resume: () => {
        resumed += 1
      },
    })).toBe('attached')
    expect(parked.registry.get('pdf')).toBe(pdf)
    expect(suspended).toBe(1)
    expect(disposed).toBe(0)
    expect(parked.registry.accepts(ticket)).toBe(true)
    parked.registry.warmSuspend()
    expect(suspended).toBe(1)
    parked.registry.resume()
    parked.registry.resume()
    expect(resumed).toBe(1)
    expect(disposed).toBe(0)
    expect(parked.registry.get('pdf')).toBe(pdf)

    const cold = openHost('cold-pdf')
    const coldTicket = resourceTicket(cold.host)
    cold.registry.warmSuspend()
    let coldDisposed = 0
    let coldResumed = 0
    expect(cold.registry.register(coldTicket, {
      kind: 'pdf',
      value: { viewer: 2 },
      suspendable: true,
      dispose: () => {
        coldDisposed += 1
      },
      suspend: () => {},
      resume: () => {
        coldResumed += 1
      },
    })).toBe('attached')
    cold.registry.releaseAll()
    cold.registry.resume()
    expect(coldDisposed).toBe(1)
    expect(coldResumed).toBe(0)
    expect(cold.registry.get('pdf')).toBeUndefined()
    expect(cold.state.generation).toBe(1)
    expect(cold.registry.accepts(coldTicket)).toBe(false)
    expect(cold.registry.register(coldTicket, {
      kind: 'researchGraph',
      value: 'after-cold',
      dispose: () => {},
    })).toBe('rejected')
    expect(cold.registry.get('researchGraph')).toBeUndefined()
    expect(cold.registry.register(resourceTicket(cold.host), {
      kind: 'researchGraph',
      value: 'fresh-after-cold',
      dispose: () => {},
    })).toBe('attached')
  })

  it('cold release invalidates a captured ticket, including after the owner remounts', () => {
    const { state, host, registry } = openHost('cold-epoch')
    const ticket = resourceTicket(host)
    const generation = state.generation
    const epoch = state.epoch
    let disposed = 0
    registry.register(ticket, {
      kind: 'researchGraph',
      value: 'mounted',
      dispose: () => {
        disposed += 1
      },
    })
    registry.releaseAll()
    state.mounted = false
    expect(state.generation).toBe(generation)
    expect(state.epoch).toBe(epoch + 1)
    expect(registry.accepts(ticket)).toBe(false)
    expect(registry.register(ticket, {
      kind: 'researchGraph',
      value: 'late',
      dispose: () => {
        disposed += 1
      },
    })).toBe('rejected')
    expect(disposed).toBe(1)
    expect(registry.get('researchGraph')).toBeUndefined()

    state.mounted = true
    expect(registry.accepts(ticket)).toBe(false)
    expect(registry.register(ticket, {
      kind: 'researchGraph',
      value: 'remounted-old',
      dispose: () => {
        disposed += 1
      },
    })).toBe('rejected')
    expect(disposed).toBe(1)
    expect(registry.get('researchGraph')).toBeUndefined()

    const fresh = resourceTicket(host)
    expect(fresh.generation).toBe(generation)
    expect(fresh.epoch).toBe(epoch + 1)
    expect(registry.register(fresh, {
      kind: 'researchGraph',
      value: 'after-remount',
      dispose: () => {
        disposed += 1
      },
    })).toBe('attached')
    expect(registry.get('researchGraph')).toBe('after-remount')
  })

  it('warm park does not advance the resource epoch', () => {
    const { state, host, registry } = openHost('warm-epoch')
    const ticket = resourceTicket(host)
    const epoch = state.epoch
    state.mounted = false
    state.suspended = true
    registry.warmSuspend()
    expect(state.epoch).toBe(epoch)
    expect(registry.accepts(ticket)).toBe(true)
    expect(registry.register(ticket, {
      kind: 'researchGraph',
      value: 'still-rejected',
      suspendable: false,
      dispose: () => {},
    })).toBe('rejected')
    expect(registry.register(ticket, {
      kind: 'pdf',
      value: 'parked-pdf',
      suspendable: true,
      dispose: () => {},
      suspend: () => {},
    })).toBe('attached')
    expect(registry.get('pdf')).toBe('parked-pdf')
  })

  it('beginRoute on a mounted owner accepts a ticket for the new generation', () => {
    const { state, host, registry } = openHost('route-epoch')
    const oldTicket = resourceTicket(host)
    let disposed = 0
    registry.register(oldTicket, {
      kind: 'researchGraph',
      value: 'before',
      dispose: () => {
        disposed += 1
      },
    })
    registry.releaseAll()
    state.generation += 1
    expect(state.mounted).toBe(true)
    expect(disposed).toBe(1)
    expect(registry.accepts(oldTicket)).toBe(false)
    const next = resourceTicket(host)
    expect(registry.register(next, {
      kind: 'researchGraph',
      value: 'next',
      dispose: () => {
        disposed += 1
      },
    })).toBe('attached')
    expect(registry.get('researchGraph')).toBe('next')
    expect(registry.register(oldTicket, {
      kind: 'researchGraph',
      value: 'stale',
      dispose: () => {
        disposed += 1
      },
    })).toBe('rejected')
    expect(disposed).toBe(1)
    expect(registry.get('researchGraph')).toBe('next')
  })

  it('disposes a slot installed by a reentrant register and keeps the outer value', () => {
    const { host, registry } = openHost()
    const ticket = resourceTicket(host)
    let previousDisposed = 0
    let loserDisposed = 0
    let winnerDisposed = 0
    registry.register(ticket, {
      kind: 'researchGraph',
      value: 'previous',
      dispose: () => {
        previousDisposed += 1
        registry.register(ticket, {
          kind: 'researchGraph',
          value: 'loser',
          dispose: () => {
            loserDisposed += 1
          },
        })
      },
    })
    const result = registry.register(ticket, {
      kind: 'researchGraph',
      value: 'winner',
      dispose: () => {
        winnerDisposed += 1
      },
    })
    expect(result).toBe('replaced')
    expect(previousDisposed).toBe(1)
    expect(loserDisposed).toBe(1)
    expect(winnerDisposed).toBe(0)
    expect(registry.get('researchGraph')).toBe('winner')
  })

  it('keeps the editor sessions non-suspendable and beside the PDF runtime', () => {
    const editorKinds = [
      'workRoleEditor',
      'workTagEditor',
      'workSourceEditor',
      'workMetadataEditor',
      'folderTagEditor',
      'privateNotesEditor',
    ] as const
    const { host, registry } = openHost('editors')
    const ticket = resourceTicket(host)
    const pdf = { id: 'pdf' }
    let pdfDisposed = 0
    expect(registry.register(ticket, {
      kind: 'pdf',
      value: pdf,
      suspendable: true,
      dispose: () => { pdfDisposed += 1 },
    })).toBe('attached')
    const disposed: Record<string, number> = {}
    for (const kind of editorKinds) {
      disposed[kind] = 0
      const session = { id: kind }
      expect(registry.register(ticket, {
        kind,
        value: session,
        suspendable: false,
        dispose: () => { disposed[kind] += 1 },
      })).toBe('attached')
      expect(registry.get(kind)).toBe(session)
      const forced = { id: kind + '-forced' }
      expect(registry.register(ticket, {
        kind,
        value: forced,
        suspendable: true,
        dispose: () => { disposed[kind] += 1 },
      })).toBe('rejected')
      expect(registry.get(kind)).toBe(session)
    }
    expect(registry.register(ticket, {
      kind: 'notAKind' as 'pdf',
      value: 'nope',
      dispose: () => {},
    })).toBe('rejected')
    registry.warmSuspend()
    for (const kind of editorKinds) {
      expect(registry.get(kind)).toBeUndefined()
      expect(disposed[kind]).toBe(1)
    }
    expect(registry.get('pdf')).toBe(pdf)
    expect(pdfDisposed).toBe(0)
    registry.resume()
    expect(registry.get('pdf')).toBe(pdf)
    for (const kind of editorKinds) expect(registry.get(kind)).toBeUndefined()
    const replaced = { id: 'workRoleEditor-2' }
    let secondDisposed = 0
    expect(registry.register(resourceTicket(host), {
      kind: 'workRoleEditor',
      value: replaced,
      suspendable: false,
      dispose: () => { secondDisposed += 1 },
    })).toBe('attached')
    expect(registry.register(resourceTicket(host), {
      kind: 'workRoleEditor',
      value: { id: 'workRoleEditor-3' },
      suspendable: false,
      dispose: () => {},
    })).toBe('replaced')
    expect(secondDisposed).toBe(1)
    expect(pdfDisposed).toBe(0)
  })

  it('keeps Research Notes beside the PDF on warm park and releases both cold', () => {
    const main = openHost('main')
    const side = openHost('side')
    const disposed = { main: 0, side: 0, pdf: 0 }
    const mainNotes = { id: 'main-notes' }
    const sideNotes = { id: 'side-notes' }
    const mainTicket = resourceTicket(main.host)
    expect(main.registry.register(mainTicket, {
      kind: 'pdf',
      value: { id: 'pdf' },
      suspendable: true,
      dispose: () => { disposed.pdf += 1 },
    })).toBe('attached')
    expect(main.registry.register(mainTicket, {
      kind: 'workNotes',
      value: mainNotes,
      suspendable: true,
      dispose: () => { disposed.main += 1 },
    })).toBe('attached')
    expect(side.registry.register(resourceTicket(side.host), {
      kind: 'workNotes',
      value: sideNotes,
      suspendable: true,
      dispose: () => { disposed.side += 1 },
    })).toBe('attached')

    main.state.mounted = false
    main.state.suspended = true
    main.registry.warmSuspend()
    expect(main.registry.get('workNotes')).toBe(mainNotes)
    expect(disposed).toEqual({ main: 0, side: 0, pdf: 0 })
    expect(main.registry.accepts(mainTicket)).toBe(true)
    main.state.mounted = true
    main.state.suspended = false
    main.registry.resume()
    expect(main.registry.get('workNotes')).toBe(mainNotes)

    main.registry.releaseAll()
    expect(main.registry.get('workNotes')).toBeUndefined()
    expect(disposed).toEqual({ main: 1, side: 0, pdf: 1 })
    expect(side.registry.get('workNotes')).toBe(sideNotes)
    expect(main.registry.register(mainTicket, {
      kind: 'workNotes',
      value: { id: 'late' },
      suspendable: true,
      dispose: () => { disposed.main += 1 },
    })).toBe('rejected')
    expect(main.registry.get('workNotes')).toBeUndefined()
  })
})
