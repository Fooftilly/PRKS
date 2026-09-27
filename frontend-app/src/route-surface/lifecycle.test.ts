import { defineComponent, h, onUnmounted } from 'vue'
import { afterEach, describe, expect, it } from 'vitest'
import {
  VUE_ROUTE_HOST_ATTR,
  VUE_ROUTE_PENDING_KEY,
  dismissRouteSurface,
  presentRouteSurface,
  publishEarlyRouteRequests,
  readRouteSurface,
  registerEarlyRoutePresenter,
  resetRouteSurfaceForTests,
  routeSurfaceGenerationCurrent,
  type RouteSurfaceOwner,
} from './lifecycle'

afterEach(() => {
  resetRouteSurfaceForTests()
  document.body.innerHTML = ''
})

interface CleanupOwner extends RouteSurfaceOwner {
  beginRoute(): void
}

interface PendingHost extends HTMLElement {
  [VUE_ROUTE_PENDING_KEY]?: unknown
}

function host(): PendingHost {
  const el = document.createElement('div') as PendingHost
  el.setAttribute(VUE_ROUTE_HOST_ATTR, 'true')
  document.body.appendChild(el)
  return el
}

function cleanupOwner(): CleanupOwner {
  const cleanups = new Set<() => void>()
  return {
    registerCleanup(fn: () => void) {
      cleanups.add(fn)
    },
    beginRoute() {
      const fns = Array.from(cleanups)
      cleanups.clear()
      fns.forEach((fn) => fn())
    },
  }
}

function probe(label: string, unmounted?: (label: string) => void) {
  return defineComponent({
    name: `Probe${label}`,
    setup() {
      onUnmounted(() => unmounted?.(label))
      return () => h('p', { 'data-probe': label }, label)
    },
  })
}

function paint(
  owner: RouteSurfaceOwner,
  el: HTMLElement,
  label: string,
  generation: number,
  ownsMainShell = false,
  unmounted?: (label: string) => void,
): boolean {
  const Comp = probe(label, unmounted)
  return presentRouteSurface({
    owner,
    host: el,
    route: {
      name: 'probe',
      canonicalHash: `#/${label}`,
      ownsMainShell,
      generation,
    },
    render: () => h(Comp),
  })
}

function stash(el: PendingHost, request: unknown): void {
  el[VUE_ROUTE_PENDING_KEY] = request
}

function claimProbe(feature: string): void {
  registerEarlyRoutePresenter(feature, (request, storageHost) => {
    const row = request as {
      owner: RouteSurfaceOwner
      host: HTMLElement
      label: string
      generation: number
      ownsMainShell?: boolean
    }
    paint(row.owner, storageHost, row.label, row.generation, row.ownsMainShell === true)
    return true
  })
}

describe('route surface lifecycle', () => {
  it('mounts two owners independently', () => {
    const a = cleanupOwner()
    const b = cleanupOwner()
    const hostA = host()
    const hostB = host()
    expect(paint(a, hostA, 'alpha', 1)).toBe(true)
    expect(paint(b, hostB, 'beta', 1)).toBe(true)
    expect(hostA.querySelector('[data-probe="alpha"]')?.textContent).toBe('alpha')
    expect(hostB.querySelector('[data-probe="beta"]')?.textContent).toBe('beta')
    expect(hostA.querySelector('[data-probe="beta"]')).toBeNull()
    expect(hostB.querySelector('[data-probe="alpha"]')).toBeNull()
  })

  it('dismissing owner B does not affect owner A', () => {
    const a = cleanupOwner()
    const b = cleanupOwner()
    const hostA = host()
    const hostB = host()
    paint(a, hostA, 'alpha', 2)
    paint(b, hostB, 'beta', 2)
    dismissRouteSurface(b)
    expect(hostB.querySelector('[data-probe]')).toBeNull()
    expect(hostA.querySelector('[data-probe="alpha"]')).not.toBeNull()
    expect(routeSurfaceGenerationCurrent(a, 2)).toBe(true)
    expect(routeSurfaceGenerationCurrent(b, 2)).toBe(false)
    dismissRouteSurface(a)
    expect(hostA.querySelector('[data-probe]')).toBeNull()
  })

  it('scopes generations to an owner', () => {
    const a = cleanupOwner()
    const b = cleanupOwner()
    const hostA = host()
    const hostB = host()
    paint(a, hostA, 'alpha', 9)
    expect(paint(b, hostB, 'stale', 4)).toBe(true)
    expect(hostA.querySelector('[data-probe="alpha"]')).not.toBeNull()
    expect(hostB.querySelector('[data-probe="stale"]')?.textContent).toBe('stale')
    expect(paint(b, hostB, 'fresh', 6)).toBe(true)
    expect(hostB.querySelector('[data-probe="fresh"]')).not.toBeNull()
    expect(hostA.querySelector('[data-probe="alpha"]')).not.toBeNull()
    expect(readRouteSurface(a)?.generation).toBe(9)
    expect(readRouteSurface(b)?.generation).toBe(6)
  })

  it('accepts generation 1 on a new owner after another owner reached a high generation', () => {
    const older = cleanupOwner()
    const newer = cleanupOwner()
    const olderHost = host()
    const newerHost = host()
    paint(older, olderHost, 'old', 8)
    dismissRouteSurface(older)
    expect(paint(newer, newerHost, 'new', 1)).toBe(true)
    expect(newerHost.querySelector('[data-probe="new"]')).not.toBeNull()
    expect(olderHost.querySelector('[data-probe]')).toBeNull()
    expect(readRouteSurface(newer)?.generation).toBe(1)
  })

  it('rejects stale generations within the same owner', () => {
    const pane = cleanupOwner()
    const el = host()
    expect(paint(pane, el, 'current', 3)).toBe(true)
    expect(paint(pane, el, 'stale', 2)).toBe(false)
    expect(el.querySelector('[data-probe="current"]')).not.toBeNull()
    expect(el.querySelector('[data-probe="stale"]')).toBeNull()
    dismissRouteSurface(pane)
    expect(paint(pane, el, 'closed', 3)).toBe(false)
    expect(el.querySelector('[data-probe]')).toBeNull()
    expect(paint(pane, el, 'next', 4)).toBe(true)
    expect(el.querySelector('[data-probe="next"]')).not.toBeNull()
  })

  it('unmounts only the owner whose cleanup runs', () => {
    const unmounted: string[] = []
    const main = cleanupOwner()
    const secondary = cleanupOwner()
    const mainHost = host()
    const secondaryHost = host()
    paint(main, mainHost, 'main', 4, true, (label) => unmounted.push(label))
    paint(secondary, secondaryHost, 'side', 1, false, (label) => unmounted.push(label))
    secondary.beginRoute()
    expect(unmounted).toEqual(['side'])
    expect(mainHost.querySelector('[data-probe="main"]')).not.toBeNull()
    expect(secondaryHost.querySelector('[data-probe]')).toBeNull()
    expect(paint(secondary, secondaryHost, 'side-again', 1)).toBe(false)
    main.beginRoute()
    expect(unmounted).toEqual(['side', 'main'])
    expect(mainHost.querySelector('[data-probe]')).toBeNull()
    expect(paint(main, mainHost, 'rewound', 1)).toBe(false)
  })

  it('does not leak the previous Vue tree when the host is replaced', () => {
    const unmounted: string[] = []
    const pane = cleanupOwner()
    const first = host()
    const second = host()
    paint(pane, first, 'first', 1, false, (label) => unmounted.push(label))
    expect(paint(pane, second, 'second', 2, false, (label) => unmounted.push(label))).toBe(true)
    expect(unmounted).toEqual(['first'])
    expect(first.querySelector('[data-probe]')).toBeNull()
    expect(second.querySelector('[data-probe="second"]')).not.toBeNull()
    expect(readRouteSurface(pane)?.mounted).toBe(true)
    expect(routeSurfaceGenerationCurrent(pane, 2)).toBe(true)
    expect(routeSurfaceGenerationCurrent(pane, 1)).toBe(false)
  })

  it('keeps an early pending presentation on its own host', () => {
    const a = cleanupOwner()
    const b = cleanupOwner()
    const hostA = host()
    const hostB = host()
    stash(hostA, { feature: 'probe', owner: a, label: 'early-a', generation: 1, ownsMainShell: true })
    stash(hostB, { feature: 'probe', owner: b, label: 'early-b', generation: 1, ownsMainShell: false })
    claimProbe('probe')
    expect(hostA[VUE_ROUTE_PENDING_KEY]).toBeUndefined()
    expect(hostB[VUE_ROUTE_PENDING_KEY]).toBeUndefined()
    expect(hostA.querySelector('[data-probe="early-a"]')).not.toBeNull()
    expect(hostB.querySelector('[data-probe="early-b"]')).not.toBeNull()
    expect(hostA.querySelector('[data-probe="early-b"]')).toBeNull()
  })

  it.each([
    ['alpha', 'beta'],
    ['beta', 'alpha'],
  ])('registering %s first leaves the other feature pending', (first, second) => {
    const firstOwner = cleanupOwner()
    const secondOwner = cleanupOwner()
    const hostA = host()
    const hostB = host()
    const firstRequest = {
      feature: first,
      owner: firstOwner,
      label: first,
      generation: 1,
      ownsMainShell: false,
    }
    const secondRequest = {
      feature: second,
      owner: secondOwner,
      label: second,
      generation: 1,
      ownsMainShell: false,
    }
    stash(hostA, firstRequest)
    stash(hostB, secondRequest)
    let calls = 0
    registerEarlyRoutePresenter(first, (request, storageHost) => {
      calls += 1
      const row = request as { feature: string; owner: RouteSurfaceOwner; label: string; generation: number }
      expect(row.feature).toBe(first)
      paint(row.owner, storageHost, row.label, row.generation)
      return true
    })
    expect(calls).toBe(1)
    expect(hostA[VUE_ROUTE_PENDING_KEY]).toBeUndefined()
    expect(hostA.querySelector(`[data-probe="${first}"]`)).not.toBeNull()
    expect(hostB[VUE_ROUTE_PENDING_KEY]).toBe(secondRequest)
    expect(hostB.querySelector('[data-probe]')).toBeNull()
    registerEarlyRoutePresenter(second, (request, storageHost) => {
      const row = request as { owner: RouteSurfaceOwner; label: string; generation: number }
      paint(row.owner, storageHost, row.label, row.generation)
      return true
    })
    expect(hostB[VUE_ROUTE_PENDING_KEY]).toBeUndefined()
    expect(hostB.querySelector(`[data-probe="${second}"]`)).not.toBeNull()
    expect(hostA.querySelector(`[data-probe="${first}"]`)).not.toBeNull()
    expect(hostA.querySelector(`[data-probe="${second}"]`)).toBeNull()
  })

  it('paints the storage host when the payload names another host', () => {
    const hostA = host()
    const hostB = host()
    const pane = cleanupOwner()
    stash(hostA, {
      feature: 'probe',
      owner: pane,
      host: hostB,
      label: 'affinity',
      generation: 1,
      ownsMainShell: false,
    })
    registerEarlyRoutePresenter('probe', (request, storageHost) => {
      const row = request as { host: HTMLElement; owner: RouteSurfaceOwner; label: string; generation: number }
      expect(storageHost).toBe(hostA)
      expect(row.host).toBe(hostA)
      paint(row.owner, row.host, row.label, row.generation)
      return true
    })
    expect(hostA.querySelector('[data-probe="affinity"]')).not.toBeNull()
    expect(hostB.querySelector('[data-probe]')).toBeNull()
    expect(hostB[VUE_ROUTE_PENDING_KEY]).toBeUndefined()
  })

  it('does not delete an early request an unrelated presenter did not claim', () => {
    const pane = cleanupOwner()
    const el = host()
    const pending = { feature: 'alpha', owner: pane, label: 'alpha', generation: 1 }
    stash(el, pending)
    let calls = 0
    registerEarlyRoutePresenter('gamma', () => {
      calls += 1
      return true
    })
    expect(calls).toBe(0)
    expect(el[VUE_ROUTE_PENDING_KEY]).toBe(pending)
    expect(el.querySelector('[data-probe]')).toBeNull()
    registerEarlyRoutePresenter('alpha', () => false)
    expect(el[VUE_ROUTE_PENDING_KEY]).toBe(pending)
  })

  it('clears a claimed slot once and leaves every other slot', () => {
    const alpha = cleanupOwner()
    const beta = cleanupOwner()
    const hostA = host()
    const hostB = host()
    const betaRequest = { feature: 'beta', owner: beta, label: 'beta', generation: 1 }
    stash(hostA, { feature: 'alpha', owner: alpha, label: 'alpha', generation: 1 })
    stash(hostB, betaRequest)
    let calls = 0
    registerEarlyRoutePresenter('alpha', (request, storageHost) => {
      calls += 1
      const row = request as { owner: RouteSurfaceOwner; label: string; generation: number }
      paint(row.owner, storageHost, row.label, row.generation)
      return true
    })
    publishEarlyRouteRequests(window)
    expect(calls).toBe(1)
    expect(hostA[VUE_ROUTE_PENDING_KEY]).toBeUndefined()
    expect(hostA.querySelector('[data-probe="alpha"]')).not.toBeNull()
    expect(hostB[VUE_ROUTE_PENDING_KEY]).toBe(betaRequest)
    expect(hostB.querySelector('[data-probe]')).toBeNull()
  })

  it('keeps a follow-up request the presenter queued during delivery', () => {
    const el = host()
    const delivered = { feature: 'alpha', owner: cleanupOwner(), label: 'first' }
    const followUp = { feature: 'beta', owner: cleanupOwner(), label: 'next' }
    stash(el, delivered)
    registerEarlyRoutePresenter('alpha', (request, storageHost) => {
      expect(request).not.toBe(followUp)
      ;(storageHost as PendingHost)[VUE_ROUTE_PENDING_KEY] = followUp
      return true
    })
    expect(el[VUE_ROUTE_PENDING_KEY]).toBe(followUp)
  })

  it('does not paint a stale early request after its owner has left', () => {
    const left = cleanupOwner()
    const staying = cleanupOwner()
    const leftHost = host()
    const stayingHost = host()
    paint(left, leftHost, 'gone', 3)
    dismissRouteSurface(left)
    stash(leftHost, { feature: 'probe', owner: left, label: 'late', generation: 3, ownsMainShell: true })
    stash(stayingHost, {
      feature: 'probe',
      owner: staying,
      label: 'stay',
      generation: 1,
      ownsMainShell: false,
    })
    claimProbe('probe')
    expect(leftHost.querySelector('[data-probe]')).toBeNull()
    expect(leftHost[VUE_ROUTE_PENDING_KEY]).toBeUndefined()
    expect(stayingHost.querySelector('[data-probe="stay"]')).not.toBeNull()
    expect(stayingHost[VUE_ROUTE_PENDING_KEY]).toBeUndefined()
  })

  it('discards a disconnected host and does not paint it after reattach', () => {
    const detached = document.createElement('div') as PendingHost
    detached.setAttribute(VUE_ROUTE_HOST_ATTR, 'true')
    const pane = cleanupOwner()
    stash(detached, { feature: 'probe', owner: pane, label: 'detached', generation: 1 })
    let calls = 0
    registerEarlyRoutePresenter('probe', () => {
      calls += 1
      return true
    })
    publishEarlyRouteRequests({
      document: { querySelectorAll: () => [detached] } as unknown as Document,
    })
    expect(calls).toBe(0)
    expect(detached[VUE_ROUTE_PENDING_KEY]).toBeUndefined()
    document.body.appendChild(detached)
    publishEarlyRouteRequests(window)
    expect(calls).toBe(0)
    expect(detached.querySelector('[data-probe]')).toBeNull()
  })

  it('records Main and Secondary identity as per-owner data', () => {
    const shellCalls: string[] = []
    const target = window as Window & { prksSyncSidebarActive?: () => void }
    target.prksSyncSidebarActive = () => {
      shellCalls.push('sidebar')
    }
    const main = cleanupOwner()
    const secondary = cleanupOwner()
    const mainHost = host()
    const secondaryHost = host()
    paint(main, mainHost, 'main', 2, true)
    paint(secondary, secondaryHost, 'side', 1, false)
    expect(readRouteSurface(main)).toMatchObject({
      name: 'probe',
      canonicalHash: '#/main',
      ownsMainShell: true,
      generation: 2,
      mounted: true,
    })
    expect(readRouteSurface(secondary)).toMatchObject({
      ownsMainShell: false,
      generation: 1,
      mounted: true,
    })
    paint(secondary, secondaryHost, 'side', 2, false)
    expect(readRouteSurface(main)?.ownsMainShell).toBe(true)
    expect(readRouteSurface(secondary)?.ownsMainShell).toBe(false)
    expect(shellCalls).toEqual([])
    delete target.prksSyncSidebarActive
  })
})
