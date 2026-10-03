import { render, type VNode } from 'vue'
import type { PrksRouteInstance, PrksRouteInstanceInput } from './routes'

/**
 * Narrow owner the shared lifecycle needs.
 * This is not the legacy TabContext. Callers pass the owning context and
 * keep every other field to themselves.
 */
export interface RouteSurfaceOwner {
  registerCleanup?: (fn: () => void) => void
}

const SESSION_KEY = '__prksRouteSurface'

/**
 * Hosts that may carry a Vue route tree. Early presentation is stored on the
 * host element, never on `window`, so two owners cannot overwrite each other.
 */
export const VUE_ROUTE_HOST_ATTR = 'data-prks-vue-route-host'
export const VUE_ROUTE_PENDING_KEY = '__prksVueRouteRequest'

interface OwnerSession {
  mountedHost: HTMLElement | null
  paintedGeneration: number
  closedGeneration: number
  cleanupArmed: boolean
  route: PrksRouteInstance | null
}

interface SessionCarrier {
  [SESSION_KEY]?: OwnerSession
}

interface PendingHost extends HTMLElement {
  [VUE_ROUTE_PENDING_KEY]?: unknown
}

/**
 * One presenter per route feature. The dispatcher calls only the presenter
 * registered for the pending request's `feature`. Registration does not need
 * the other features' presenters.
 */
export type EarlyRoutePresenter = (request: unknown, host: HTMLElement) => boolean

const earlyPresenters = new Map<string, EarlyRoutePresenter>()

export interface RouteSurfacePresent {
  owner: RouteSurfaceOwner
  host: HTMLElement
  route: PrksRouteInstanceInput
  /** Called only after this owner's generation is accepted. */
  render: (generation: number) => VNode
  /**
   * When false, skip TabContext beginRoute cleanup registration.
   * The caller must dismiss explicitly on leave/destroy (Concepts in-place refresh).
   */
  armBeginRouteCleanup?: boolean
}

/** This owner's accepted route, plus whether its host is still mounted. */
export type RouteSurfaceState = PrksRouteInstance & {
  readonly mounted: boolean
}

function isOwner(value: unknown): value is RouteSurfaceOwner {
  return !!value && typeof value === 'object'
}

function carrier(owner: object): SessionCarrier {
  return owner as SessionCarrier
}

function readSession(owner: object | null | undefined): OwnerSession | null {
  if (!isOwner(owner)) return null
  return carrier(owner)[SESSION_KEY] ?? null
}

function sessionFor(owner: RouteSurfaceOwner): OwnerSession {
  const box = carrier(owner)
  if (!box[SESSION_KEY]) {
    box[SESSION_KEY] = {
      mountedHost: null,
      paintedGeneration: -1,
      closedGeneration: -1,
      cleanupArmed: false,
      route: null,
    }
  }
  return box[SESSION_KEY]
}

function finiteGeneration(value: number | undefined): number | null {
  if (typeof value !== 'number' || !Number.isFinite(value)) return null
  return value
}

function unmountHost(host: HTMLElement | null): void {
  if (!host) return
  render(null, host)
}

function dismissSession(session: OwnerSession): void {
  if (session.paintedGeneration >= 0) session.closedGeneration = session.paintedGeneration
  const host = session.mountedHost
  session.mountedHost = null
  if (session.route) {
    session.route = { ...session.route, generation: session.paintedGeneration }
  }
  unmountHost(host)
}

/**
 * Drop this owner's Vue tree when its context begins another route, unmounts,
 * or is destroyed. Other owners keep their own cleanup.
 */
function armOwnerCleanup(owner: RouteSurfaceOwner, session: OwnerSession): void {
  if (session.cleanupArmed || typeof owner.registerCleanup !== 'function') return
  session.cleanupArmed = true
  owner.registerCleanup(() => {
    session.cleanupArmed = false
    dismissSession(session)
  })
}

/**
 * Paint one owner's Vue tree. Generations are compared only with that owner's
 * previous paints. The stored record is that owner's `PrksRouteInstance`,
 * including params. Main/Secondary identity is not published to the shell.
 */
export function presentRouteSurface(input: RouteSurfacePresent): boolean {
  if (!isOwner(input.owner)) return false
  const session = sessionFor(input.owner)
  const requested = finiteGeneration(input.route.generation)
  const generation = requested ?? session.paintedGeneration + 1
  if (generation <= session.closedGeneration || generation < session.paintedGeneration) return false
  if (!input.host || !input.host.isConnected) return false
  const route: PrksRouteInstance = { ...input.route, generation }
  session.paintedGeneration = generation
  session.route = route
  if (input.armBeginRouteCleanup !== false) {
    armOwnerCleanup(input.owner, session)
  }
  if (session.mountedHost !== input.host) {
    unmountHost(session.mountedHost)
    session.mountedHost = input.host
  }
  input.host.setAttribute(VUE_ROUTE_HOST_ATTR, 'true')
  render(input.render(generation), input.host)
  return true
}

/** Drop the Vue tree owned by this context. Other owners stay mounted. */
export function dismissRouteSurface(owner: object | null | undefined): void {
  const session = readSession(owner)
  if (!session) return
  dismissSession(session)
}

/**
 * True when `generation` is the tree currently mounted for this owner.
 * A generation from another owner is never current here.
 */
export function routeSurfaceGenerationCurrent(
  owner: object | null | undefined,
  generation: number,
): boolean {
  const session = readSession(owner)
  if (!session || !session.mountedHost || !session.mountedHost.isConnected) return false
  if (!Number.isFinite(generation)) return false
  return generation === session.paintedGeneration && generation > session.closedGeneration
}

/** Last accepted identity for this owner. Null before the first accepted paint. */
export function readRouteSurface(owner: object | null | undefined): RouteSurfaceState | null {
  const session = readSession(owner)
  if (!session || !session.route) return null
  return {
    ...session.route,
    mounted: session.mountedHost != null && session.mountedHost.isConnected,
  }
}

function earlyFeatureName(pending: unknown): string | null {
  if (!pending || typeof pending !== 'object') return null
  const feature = (pending as { feature?: unknown }).feature
  if (typeof feature !== 'string' || feature.length === 0) return null
  return feature
}

/**
 * Bind the storage host onto a copy of the payload. The stored slot is left
 * unchanged until a presenter claims it, so a rejected request stays as the
 * producer wrote it. The payload host is not a second paint target.
 */
function requestOnStorageHost(pending: object, host: HTMLElement): unknown {
  return { ...(pending as Record<string, unknown>), host }
}

/**
 * Register the presenter for one route feature and claim that feature's
 * pending hosts. A request for another feature stays on its host until that
 * feature registers. The same feature name replaces its previous presenter.
 */
export function registerEarlyRoutePresenter(
  feature: string,
  present: EarlyRoutePresenter,
  target: { document?: Document | null } = globalThis,
): void {
  if (typeof feature !== 'string' || feature.length === 0 || typeof present !== 'function') return
  earlyPresenters.set(feature, present)
  publishEarlyRouteRequests(target)
}

export interface RouteDispatchTarget {
  prksVuePresentRoute?: (request: unknown) => boolean
}

/**
 * Deliver one host-local request to the presenter registered for its feature.
 * That presenter records the owner's `PrksRouteInstance`. A request for an
 * unregistered feature is not claimed, so the coordinator can leave it on the host.
 */
export function presentRegisteredRoute(request: unknown): boolean {
  if (!request || typeof request !== 'object') return false
  const host = (request as { host?: unknown }).host
  if (!host || typeof (host as HTMLElement).setAttribute !== 'function') return false
  const feature = earlyFeatureName(request)
  const present = feature ? earlyPresenters.get(feature) : undefined
  if (!feature || !present) return false
  return present(request, host as HTMLElement) === true
}

/** The only window entry for route presentation. Feature presenters stay off `window`. */
export function registerRouteWindowBridge(target: RouteDispatchTarget = window): void {
  target.prksVuePresentRoute = presentRegisteredRoute
}

/**
 * Deliver host-local early requests to the presenter registered for each
 * request's feature. The host that stored the request is the host that paints.
 * A slot is deleted only when that presenter claims it and the host still
 * holds that same request, or when the host is already disconnected. A
 * follow-up written onto the host during delivery stays queued. An unmatched
 * request stays for a later registration.
 */
export function publishEarlyRouteRequests(target: { document?: Document | null }): void {
  const doc = target.document
  if (!doc?.querySelectorAll) return
  const hosts = Array.from(doc.querySelectorAll<HTMLElement>(`[${VUE_ROUTE_HOST_ATTR}]`))
  hosts.forEach((node) => {
    const host = node as PendingHost
    if (!(VUE_ROUTE_PENDING_KEY in host)) return
    const pending = host[VUE_ROUTE_PENDING_KEY]
    if (!host.isConnected || pending == null) {
      delete host[VUE_ROUTE_PENDING_KEY]
      return
    }
    const feature = earlyFeatureName(pending)
    const present = feature ? earlyPresenters.get(feature) : undefined
    if (!feature || !present) return
    const claimed = present(requestOnStorageHost(pending, host), host) === true
    if (claimed && host[VUE_ROUTE_PENDING_KEY] === pending) {
      delete host[VUE_ROUTE_PENDING_KEY]
    }
  })
}

export function resetRouteSurfaceForTests(root: ParentNode = document): void {
  earlyPresenters.clear()
  root.querySelectorAll<HTMLElement>(`[${VUE_ROUTE_HOST_ATTR}]`).forEach((host) => {
    render(null, host)
  })
}
