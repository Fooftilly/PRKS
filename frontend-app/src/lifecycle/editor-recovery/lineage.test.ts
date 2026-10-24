import { describe, expect, it } from 'vitest'
import type { RuntimeClaim } from './identity'
import { classifyLineage, isAdoptable, type LineageProbe } from './lineage'
import type { DraftOwner } from './schema'

interface World {
  claim: RuntimeClaim | null
  alivePages: Set<string>
  aliveRuntimes: Set<string>
  liveElsewhere: Set<string>
  local: Map<string, string>
  canProbe: boolean
  closedInTab: Set<string>
  /** Pages that recorded a final pagehide in localStorage. */
  closed: Set<string>
  /** Web Locks prove the page gone (no page lock held); false without locks. */
  locksProveGone: boolean
}

function probeFor(world: World): LineageProbe {
  const identity: LineageProbe['identity'] = {
    pageInstanceId: 'p-me',
    current: () => world.claim,
    isPageAlive: async (id) => (world.canProbe ? world.alivePages.has(id) : null),
    isRuntimeAlive: async (id) => (world.canProbe ? world.aliveRuntimes.has(id) : null),
    isLineageLiveElsewhere: async (id) => (world.canProbe ? world.liveElsewhere.has(id) : null),
    wasClosedInThisTab: (id) => world.closedInTab.has(id),
    wasPageClosed: (id) => world.closed.has(id),
    isPageGone: async (id) => world.locksProveGone && !world.alivePages.has(id),
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
    closedInTab: new Set(),
    closed: new Set(),
    locksProveGone: true,
    ...partial,
  }
}

const record = (owner: Partial<DraftOwner>) => ({
  draftId: 'd-1',
  owner: { runtimeId: 'r-other', pageInstanceId: 'p-other', paneId: 'tab-2', claimedAt: 1, ...owner },
})

describe('classifyLineage', () => {
  it('sends the page and runtime pings only with local proof the page is gone, and together', async () => {
    const sent: string[] = []
    const pending: Array<() => void> = []
    const w = world({ locksProveGone: false })
    const probe = probeFor(w)
    const ping = (name: string) => () => {
      sent.push(name)
      return new Promise<boolean>((resolve) => pending.push(() => resolve(false)))
    }
    probe.identity.isPageAlive = ping('page')
    probe.identity.isRuntimeAlive = ping('runtime')
    probe.identity.isLineageLiveElsewhere = ping('lineage')
    // No proof: unknown after the lineage ping alone.
    const unproven = classifyLineage(record({}), probe)
    await Promise.resolve()
    expect(sent).toEqual(['lineage'])
    pending.splice(0).forEach((resolve) => resolve())
    expect(await unproven).toBe('unknown')
    // A recorded final pagehide: all three pings are in flight at once.
    sent.length = 0
    w.closed.add('p-other')
    const proven = classifyLineage(record({}), probe)
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(sent.sort()).toEqual(['lineage', 'page', 'runtime'])
    pending.splice(0).forEach((resolve) => resolve())
    expect(await proven).toBe('dead-runtime')
  })

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

  it('dead-runtime when neither the owner page nor runtime is alive and Web Locks prove the page gone', async () => {
    const lineageClass = await classifyLineage(record({}), probeFor(world()))
    expect(lineageClass).toBe('dead-runtime')
    expect(isAdoptable(lineageClass)).toBe(true)
  })

  it('without Web Locks, silence is not proof: dead-runtime only when the page recorded its final pagehide', async () => {
    // A tab closed on the LAN: no locks, nobody answers the pings.
    const w = world({ claim: { runtimeId: 'r-me', verified: 'channel' }, locksProveGone: false })
    expect(await classifyLineage(record({}), probeFor(w))).toBe('unknown')
    w.closed.add('p-other')
    expect(await classifyLineage(record({}), probeFor(w))).toBe('dead-runtime')
    // A recorded close never outweighs an answer.
    w.alivePages.add('p-other')
    expect(await classifyLineage(record({}), probeFor(w))).toBe('unknown')
    w.alivePages.delete('p-other')
    w.aliveRuntimes.add('r-other')
    expect(await classifyLineage(record({}), probeFor(w))).toBe('unknown')
    w.aliveRuntimes.delete('r-other')
    w.liveElsewhere.add('d-1')
    expect(await classifyLineage(record({}), probeFor(w))).toBe('other-live')
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

  it('adopts a channel-claimed runtime\'s lineage only when its page recorded its close in this tab', async () => {
    const w = world({ claim: { runtimeId: 'r-me', verified: 'channel' } })
    // Silence from the tab this one was duplicated from proves nothing.
    expect(await classifyLineage(record({ runtimeId: 'r-me' }), probeFor(w))).toBe('unknown')
    // An earlier load of this tab ran pagehide.
    w.closedInTab.add('p-other')
    expect(await classifyLineage(record({ runtimeId: 'r-me' }), probeFor(w))).toBe('same-runtime-orphan')
    // A live writer elsewhere still wins.
    w.liveElsewhere.add('d-1')
    expect(await classifyLineage(record({ runtimeId: 'r-me' }), probeFor(w))).toBe('other-live')
  })
})
