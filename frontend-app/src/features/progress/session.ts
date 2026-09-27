import { createVNode, render } from 'vue'
import ProgressView from './ProgressView.vue'
import { acceptEffectiveRows } from './rows'
import type { ProgressSnapshot } from './state'
import { canonicalProgressStatus, progressCanonicalHash, type ProgressStatus } from './status'

/**
 * Bookkeeping for one TabContext. Lives on that object, not on `window`.
 * `ctx.beginRoute()` does not clear unknown fields, so generations survive
 * the resource teardown that starts the next route on the same owner.
 */
interface ProgressOwnerSession {
  mountedHost: HTMLElement | null
  paintedGeneration: number
  closedGeneration: number
  cleanupArmed: boolean
}

export interface ProgressOwner {
  registerCleanup?: (fn: () => void) => void
  __prksProgressSession?: ProgressOwnerSession
}

export interface ProgressPresentInput {
  owner: ProgressOwner
  host: HTMLElement
  status: string | null | undefined
  rows: unknown
  offlineCached?: boolean
  generation?: number
  /**
   * True when this owner is the main shell. Only that paint may update the
   * shared sidebar. Omitted means the caller is the shell (unit tests).
   */
  shell?: boolean
}

interface ProgressHost extends HTMLElement {
  __prksProgressPresentRequest?: ProgressPresentInput
}

function readSession(owner: object | null | undefined): ProgressOwnerSession | null {
  if (!owner || typeof owner !== 'object') return null
  return (owner as ProgressOwner).__prksProgressSession ?? null
}

function sessionFor(owner: ProgressOwner): ProgressOwnerSession {
  if (!owner.__prksProgressSession) {
    owner.__prksProgressSession = {
      mountedHost: null,
      paintedGeneration: -1,
      closedGeneration: -1,
      cleanupArmed: false,
    }
  }
  return owner.__prksProgressSession
}

/**
 * Ask the existing navigation model to mark the Progress filter.
 * This file does not choose sidebar classes.
 */
function syncProgressSidebar(status: ProgressStatus): void {
  const sync = window.prksSyncSidebarActive
  if (typeof sync !== 'function') return
  const canonicalHash = progressCanonicalHash(status)
  sync({
    name: 'progress',
    hash: canonicalHash,
    canonicalHash,
    params: { status },
  })
}

function unmountHost(host: HTMLElement | null): void {
  if (!host) return
  render(null, host)
}

function dismissSession(session: ProgressOwnerSession): void {
  if (session.paintedGeneration >= 0) session.closedGeneration = session.paintedGeneration
  const host = session.mountedHost
  session.mountedHost = null
  unmountHost(host)
}

/**
 * Drop this owner's Vue tree when its TabContext begins another route,
 * unmounts, or is destroyed. Other owners keep their own cleanup.
 */
function armOwnerCleanup(owner: ProgressOwner, session: ProgressOwnerSession): void {
  if (session.cleanupArmed || typeof owner.registerCleanup !== 'function') return
  session.cleanupArmed = true
  owner.registerCleanup(() => {
    session.cleanupArmed = false
    dismissSession(session)
  })
}

/**
 * Paint one owner's Progress surface from an already-effective browse snapshot.
 * Generations are compared only with that owner's previous paints.
 */
export function presentProgress(input: ProgressPresentInput): void {
  if (!input.owner || typeof input.owner !== 'object') return
  const session = sessionFor(input.owner)
  const generation =
    typeof input.generation === 'number' && Number.isFinite(input.generation)
      ? input.generation
      : session.paintedGeneration + 1
  if (generation <= session.closedGeneration || generation < session.paintedGeneration) return
  if (!input.host || !input.host.isConnected) return
  const status = canonicalProgressStatus(input.status)
  const snapshot: ProgressSnapshot = {
    status,
    rows: acceptEffectiveRows(input.rows),
    offlineCached: input.offlineCached === true,
    generation,
  }
  session.paintedGeneration = generation
  armOwnerCleanup(input.owner, session)
  if (input.shell !== false) syncProgressSidebar(status)
  if (session.mountedHost !== input.host) {
    unmountHost(session.mountedHost)
    session.mountedHost = input.host
  }
  render(createVNode(ProgressView, { snapshot }), input.host)
}

/** Drop the Vue Progress tree owned by this pane. Other owners stay mounted. */
export function dismissProgress(owner: object | null | undefined): void {
  const session = readSession(owner)
  if (!session) return
  dismissSession(session)
}

export function resetProgressSessionForTests(): void {
  document.querySelectorAll<HTMLElement>('[data-prks-progress-host]').forEach((host) => {
    render(null, host)
  })
}

export function registerProgressBridge(target: Window = window): void {
  target.prksVuePresentProgress = presentProgress
  target.prksVueDismissProgress = dismissProgress
  const hosts = target.document?.querySelectorAll<HTMLElement>('[data-prks-progress-host]')
  if (!hosts) return
  hosts.forEach((node) => {
    const host = node as ProgressHost
    const pending = host.__prksProgressPresentRequest
    if (!pending) return
    delete host.__prksProgressPresentRequest
    presentProgress(pending)
  })
}
