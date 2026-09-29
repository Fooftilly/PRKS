/**
 * Typed Work route state. The legacy coordinator decides availability,
 * lifecycle, and provenance. This module does not read durable operations.
 */

export type WorkRouteAvailability = 'ready' | 'unavailable' | 'not-found'

/** Ordinary library Work, a locally-created unsent Work, or a pending deletion. */
export type WorkRouteLifecycle = 'ordinary' | 'unsent-create' | 'pending-delete'

/**
 * Where a ready Work came from. Not a second cache.
 * Unavailable and not-found publish no Work.
 */
export type WorkRouteProvenance = 'server' | 'cache' | 'local-unsent'

export interface WorkRouteOwner {
  readonly tabId: string
  readonly generation: number
}

/** Effective Work already overlaid by the coordinator's canonical helpers. */
export type EffectiveWork = Record<string, unknown> & { id?: unknown }

export interface WorkRouteProjection {
  readonly workId: string
  readonly availability: WorkRouteAvailability
  readonly lifecycle: WorkRouteLifecycle
  readonly provenance: WorkRouteProvenance
  readonly ownerTabId: string
  readonly ownerGeneration: number
  /** Null unless availability is ready. Never a shared object across panes. */
  readonly work: EffectiveWork | null
  /** True only for a genuine foreground open the coordinator already decided. */
  readonly recordOpen: boolean
}

export interface WorkRouteProjectInput {
  readonly workId: string
  readonly owner: WorkRouteOwner
  readonly availability: WorkRouteAvailability
  readonly lifecycle: WorkRouteLifecycle
  readonly provenance: WorkRouteProvenance
  readonly work: EffectiveWork | null
  readonly recordOpen: boolean
}

export interface WorkRouteOwnerContext {
  readonly tabId: string
  readonly generation: number
  readonly destroyed?: boolean
  isCurrent(generation: number): boolean
  setEntity(type: string, value: unknown): void
  getEntity(type: string): unknown
  setResource(name: string, value: unknown): void
  getResource(name: string): unknown
}
