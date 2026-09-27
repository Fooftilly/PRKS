import { defineComponent, h, onUnmounted } from 'vue'
import { afterEach, describe, expect, it } from 'vitest'
import {
  VUE_ROUTE_HOST_ATTR,
  VUE_ROUTE_PENDING_KEY,
  dismissRouteSurface,
  presentRouteSurface,
  publishEarlyRouteRequests,
  readRouteSurface,
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
    stash(hostA, { owner: a, host: hostA, label: 'early-a', generation: 1, ownsMainShell: true })
    stash(hostB, { owner: b, host: hostB, label: 'early-b', generation: 1, ownsMainShell: false })
    publishEarlyRouteRequests(window, [
      (request) => {
        const row = request as {
          owner: RouteSurfaceOwner
          host: HTMLElement
          label: string
          generation: number
          ownsMainShell: boolean
        }
        paint(row.owner, row.host, row.label, row.generation, row.ownsMainShell)
      },
    ])
    expect(hostA[VUE_ROUTE_PENDING_KEY]).toBeUndefined()
    expect(hostB[VUE_ROUTE_PENDING_KEY]).toBeUndefined()
    expect(hostA.querySelector('[data-probe="early-a"]')).not.toBeNull()
    expect(hostB.querySelector('[data-probe="early-b"]')).not.toBeNull()
    expect(hostA.querySelector('[data-probe="early-b"]')).toBeNull()
  })

  it('does not paint a stale early request after its owner has left', () => {
    const left = cleanupOwner()
    const staying = cleanupOwner()
    const leftHost = host()
    const stayingHost = host()
    paint(left, leftHost, 'gone', 3)
    dismissRouteSurface(left)
    stash(leftHost, { owner: left, host: leftHost, label: 'late', generation: 3, ownsMainShell: true })
    stash(stayingHost, {
      owner: staying,
      host: stayingHost,
      label: 'stay',
      generation: 1,
      ownsMainShell: false,
    })
    publishEarlyRouteRequests(window, [
      (request) => {
        const row = request as {
          owner: RouteSurfaceOwner
          host: HTMLElement
          label: string
          generation: number
          ownsMainShell: boolean
        }
        paint(row.owner, row.host, row.label, row.generation, row.ownsMainShell)
      },
    ])
    expect(leftHost.querySelector('[data-probe]')).toBeNull()
    expect(stayingHost.querySelector('[data-probe="stay"]')).not.toBeNull()

    const detached = host()
    const owner = cleanupOwner()
    stash(detached, { owner, host: detached, label: 'detached', generation: 1, ownsMainShell: false })
    detached.remove()
    const pending = detached[VUE_ROUTE_PENDING_KEY]
    delete detached[VUE_ROUTE_PENDING_KEY]
    const row = pending as {
      owner: RouteSurfaceOwner
      host: HTMLElement
      label: string
      generation: number
      ownsMainShell: boolean
    }
    expect(paint(row.owner, row.host, row.label, row.generation, row.ownsMainShell)).toBe(false)
    document.body.appendChild(detached)
    expect(detached.querySelector('[data-probe]')).toBeNull()
    expect(detached[VUE_ROUTE_PENDING_KEY]).toBeUndefined()
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
