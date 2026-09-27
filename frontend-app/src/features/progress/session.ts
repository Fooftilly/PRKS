import { createVNode, render } from 'vue'
import ProgressView from './ProgressView.vue'
import { acceptEffectiveRows } from './rows'
import { progressSnapshot, type ProgressSnapshot } from './state'
import { canonicalProgressStatus, progressCanonicalHash, type ProgressStatus } from './status'

export interface ProgressPresentInput {
  host: HTMLElement
  status: string | null | undefined
  rows: unknown
  offlineCached?: boolean
  generation?: number
}

let mountedHost: HTMLElement | null = null
let paintedGeneration = -1
let closedGeneration = -1

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

function unmountHost(): void {
  if (!mountedHost) return
  const host = mountedHost
  mountedHost = null
  render(null, host)
}

/**
 * Paint the Vue Progress surface from an already-effective browse snapshot.
 * Older route generations cannot overwrite a newer paint.
 */
export function presentProgress(input: ProgressPresentInput): void {
  const generation =
    typeof input.generation === 'number' && Number.isFinite(input.generation)
      ? input.generation
      : paintedGeneration + 1
  if (generation <= closedGeneration || generation < paintedGeneration) return
  if (!input.host || !input.host.isConnected) return
  const status = canonicalProgressStatus(input.status)
  const next: ProgressSnapshot = {
    status,
    rows: acceptEffectiveRows(input.rows),
    offlineCached: input.offlineCached === true,
    generation,
  }
  paintedGeneration = generation
  progressSnapshot.value = next
  syncProgressSidebar(status)
  if (mountedHost !== input.host) {
    unmountHost()
    mountedHost = input.host
    render(createVNode(ProgressView), input.host)
  }
}

/** Drop the Vue Progress tree before the legacy route replaces its host. */
export function dismissProgress(): void {
  if (paintedGeneration >= 0) closedGeneration = paintedGeneration
  progressSnapshot.value = null
  unmountHost()
}

export function resetProgressSessionForTests(): void {
  progressSnapshot.value = null
  unmountHost()
  paintedGeneration = -1
  closedGeneration = -1
}

export function registerProgressBridge(target: Window = window): void {
  target.prksVuePresentProgress = presentProgress
  target.prksVueDismissProgress = dismissProgress
  const pending = target.__prksProgressPresentRequest
  if (pending && pending.host) {
    delete target.__prksProgressPresentRequest
    presentProgress(pending)
  }
}
