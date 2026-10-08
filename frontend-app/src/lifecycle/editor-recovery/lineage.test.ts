import { describe, expect, it } from 'vitest'
import type { PageIdentity, RuntimeClaim } from './identity'
import { classifyLineage, isAdoptable, type LineageProbe } from './lineage'
import type { DraftOwner } from './schema'

interface World {
  claim: RuntimeClaim | null
  alivePages: Set<string>
  aliveRuntimes: Set<string>
  liveElsewhere: Set<string>
  local: Map<string, string>
  canProbe: boolean
}

function probeFor(world: World): LineageProbe {
  const identity: Pick<PageIdentity, 'pageInstanceId' | 'current' | 'isPageAlive' | 'isRuntimeAlive' | 'isLineageLiveElsewhere'> = {
    pageInstanceId: 'p-me',
    current: () => world.claim,
    isPageAlive: async (id) => (world.canProbe ? world.alivePages.has(id) : null),
    isRuntimeAlive: async (id) => (world.canProbe ? world.aliveRuntimes.has(id) : null),
    isLineageLiveElsewhere: async (id) => (world.canProbe ? world.liveElsewhere.has(id) : null),
  }
  return { identity, localOwner: (id) => world.local.get(id) ?? null }
}

function world(partial: Partial<World> = {}): World {
  return {
    claim: { runtimeId: 'r-me', verified: 'lock' },
    alivePages: new Set(['p-me']),
    aliveRuntimes: new Set(['r-me']),
    liveElsewhere: new Set(),
    local: new Map(),
    canProbe: true,
    ...partial,
  }
}

const record = (owner: Partial<DraftOwner>) => ({
  draftId: 'd-1',
  owner: { runtimeId: 'r-other', pageInstanceId: 'p-other', paneId: 'tab-2', claimedAt: 1, ...owner },
})

describe('classifyLineage', () => {
  it('self-live and other-live for writers in this page', async () => {
    const w = world({ local: new Map([['d-1', 's-a']]) })
    expect(await classifyLineage(record({ pageInstanceId: 'p-me' }), probeFor(w), 's-a')).toBe('self-live')
    expect(await classifyLineage(record({ pageInstanceId: 'p-me' }), probeFor(w), 's-b')).toBe('other-live')
  })

  it('same-runtime orphan after a reload, even when the old pane id was remapped or removed', async () => {
    // The previous load of this tab wrote it from pane tab-2; after reload the
    // workspace mapped tab-2 to another route (or dropped it). The pane id is
    // never consulted, so the draft stays discoverable and adoptable.
    for (const paneId of ['tab-2', 'tab-9', '']) {
      const lineageClass = await classifyLineage(record({ runtimeId: 'r-me', pageInstanceId: 'p-before-reload', paneId }), probeFor(world()))
      expect(lineageClass).toBe('same-runtime-orphan')
      expect(isAdoptable(lineageClass)).toBe(true)
    }
  })

  it('same-runtime orphan for a released session of this page', async () => {
    expect(await classifyLineage(record({ pageInstanceId: 'p-me', runtimeId: 'r-me' }), probeFor(world()))).toBe('same-runtime-orphan')
  })

  it('other-live when another page answers for the lineage', async () => {
    const w = world({ liveElsewhere: new Set(['d-1']), alivePages: new Set(['p-me', 'p-other']) })
    expect(await classifyLineage(record({}), probeFor(w))).toBe('other-live')
  })

  it('dead-runtime when neither the owner page nor runtime is alive', async () => {
    const lineageClass = await classifyLineage(record({}), probeFor(world()))
    expect(lineageClass).toBe('dead-runtime')
    expect(isAdoptable(lineageClass)).toBe(true)
  })

  it('unknown when the owner runtime lives on without a writer, or nothing can be probed', async () => {
    const aliveRuntime = world({ aliveRuntimes: new Set(['r-me', 'r-other']) })
    expect(await classifyLineage(record({}), probeFor(aliveRuntime))).toBe('unknown')
    expect(await classifyLineage(record({}), probeFor(world({ canProbe: false })))).toBe('unknown')
    expect(isAdoptable('unknown')).toBe(false)
  })

  it('does not trust an unverified runtime id for same-runtime orphans', async () => {
    const w = world({ claim: { runtimeId: 'r-me', verified: 'unverified' } })
    expect(await classifyLineage(record({ runtimeId: 'r-me' }), probeFor(w))).toBe('unknown')
    w.liveElsewhere.add('d-1')
    expect(await classifyLineage(record({ runtimeId: 'r-me' }), probeFor(w))).toBe('other-live')
  })

  it('asks for a live writer before treating a channel-claimed runtime as this tab\'s', async () => {
    const w = world({ claim: { runtimeId: 'r-me', verified: 'channel' } })
    expect(await classifyLineage(record({ runtimeId: 'r-me' }), probeFor(w))).toBe('same-runtime-orphan')
    // The tab this one was duplicated from still edits it.
    w.liveElsewhere.add('d-1')
    expect(await classifyLineage(record({ runtimeId: 'r-me' }), probeFor(w))).toBe('other-live')
  })
})
