import type {
  EffectiveWork,
  WorkRouteAvailability,
  WorkRouteLifecycle,
  WorkRouteOwnerContext,
  WorkRouteProjectInput,
  WorkRouteProjection,
  WorkRouteProvenance,
} from './types'

const RESOURCE = 'workRouteProjection'

function cloneWork(work: EffectiveWork | null): EffectiveWork | null {
  if (!work) return null
  if (typeof structuredClone === 'function') return structuredClone(work)
  return JSON.parse(JSON.stringify(work)) as EffectiveWork
}

function freezeProjection(projection: WorkRouteProjection): WorkRouteProjection {
  return Object.freeze(projection)
}

/**
 * Shape a route result the coordinator has already resolved.
 * Pending deletion never publishes the cached row.
 * A ready result without a Work is not-found, not a fake Work.
 */
export function projectWorkRoute(input: WorkRouteProjectInput): WorkRouteProjection {
  const workId = String(input.workId)
  let availability: WorkRouteAvailability = input.availability
  let lifecycle: WorkRouteLifecycle = input.lifecycle
  let provenance: WorkRouteProvenance = input.provenance
  let work = input.work

  if (lifecycle === 'pending-delete') {
    availability = 'unavailable'
    work = null
  }
  if (lifecycle === 'unsent-create') {
    provenance = 'local-unsent'
  }
  if (work && String(work.id ?? '') !== workId) {
    work = null
    if (availability === 'ready') availability = 'not-found'
  }
  if (availability !== 'ready') work = null
  else if (!work) availability = 'not-found'

  return freezeProjection({
    workId,
    availability,
    lifecycle,
    provenance,
    ownerTabId: String(input.owner.tabId),
    ownerGeneration: input.owner.generation,
    work: availability === 'ready' ? cloneWork(work) : null,
    recordOpen: input.recordOpen === true && availability === 'ready',
  })
}

function sameOwner(ctx: WorkRouteOwnerContext, projection: WorkRouteProjection, generation: number): boolean {
  return (
    !ctx.destroyed &&
    projection.ownerTabId === String(ctx.tabId) &&
    projection.ownerGeneration === generation &&
    ctx.isCurrent(generation)
  )
}

/** Publish onto the originating TabContext. A stale generation publishes nothing. */
export function publishWorkRouteProjection(
  ctx: WorkRouteOwnerContext,
  generation: number,
  projection: WorkRouteProjection,
): WorkRouteProjection | null {
  if (!sameOwner(ctx, projection, generation)) return null
  const stored = projectWorkRoute({
    workId: projection.workId,
    owner: { tabId: projection.ownerTabId, generation: projection.ownerGeneration },
    availability: projection.availability,
    lifecycle: projection.lifecycle,
    provenance: projection.provenance,
    work: projection.work,
    recordOpen: projection.recordOpen,
  })
  ctx.setResource(RESOURCE, stored)
  if (stored.availability === 'ready' && stored.work) ctx.setEntity('work', stored.work)
  else ctx.setEntity('work', null)
  return stored
}

/**
 * A later folder/playlist placement may replace the Work on the same owner.
 * It does not record another open and does not touch any other pane.
 */
export function replaceWorkRoutePlacement(
  ctx: WorkRouteOwnerContext,
  generation: number,
  work: EffectiveWork | null,
): WorkRouteProjection | null {
  if (!ctx || ctx.destroyed || !ctx.isCurrent(generation)) return null
  const current = ctx.getResource(RESOURCE) as WorkRouteProjection | null
  if (!current || !sameOwner(ctx, current, generation)) return null
  if (!work || String(work.id ?? '') !== current.workId) return null
  if (current.availability !== 'ready') return null
  return publishWorkRouteProjection(
    ctx,
    generation,
    projectWorkRoute({
      workId: current.workId,
      owner: { tabId: current.ownerTabId, generation: current.ownerGeneration },
      availability: 'ready',
      lifecycle: current.lifecycle,
      provenance: current.provenance,
      work,
      recordOpen: false,
    }),
  )
}

/**
 * After the legacy painter publishes its Work, keep this owner’s projection
 * on that same object. A stale owner does not adopt it.
 */
export function adoptPaintedWorkRoute(
  ctx: WorkRouteOwnerContext,
  generation: number,
  workId: string,
): WorkRouteProjection | null {
  if (!ctx || ctx.destroyed || !ctx.isCurrent(generation)) return null
  const current = ctx.getResource(RESOURCE) as WorkRouteProjection | null
  if (!current || !sameOwner(ctx, current, generation)) return null
  if (current.workId !== String(workId) || current.availability !== 'ready') return null
  const painted = ctx.getEntity('work') as EffectiveWork | null
  if (!painted || String(painted.id ?? '') !== current.workId) return null
  if (current.work === painted) return current
  const stored = freezeProjection({
    workId: current.workId,
    availability: current.availability,
    lifecycle: current.lifecycle,
    provenance: current.provenance,
    ownerTabId: current.ownerTabId,
    ownerGeneration: current.ownerGeneration,
    work: painted,
    recordOpen: current.recordOpen,
  })
  ctx.setResource(RESOURCE, stored)
  return stored
}

/** Genuine foreground open only. internalRefresh must not record another open. */
export function workOpenShouldRecord(internalRefresh: boolean, workValue: unknown): boolean {
  return !internalRefresh && !!workValue
}
