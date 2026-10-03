/**
 * Canonical TabContext leave preflight.
 *
 * One serialized decision for whether owner X may undergo transition Y to
 * destination Z. The workspace effect coordinator and direct route replacement
 * both call this. It does not know Person drafts, Work drafts, PDF sync,
 * dialogs, or DOM. Feature probes register those answers.
 *
 * Generic browser listener, observer, and timer cleanup stays with #233.
 * This module does not own that lifetime.
 *
 * Probe order is part of the contract: pdf-sync 10, person-profile 20,
 * work-metadata 30. The PDF probe stays synchronous and runs before any
 * async draft confirmation. Adapters that call classic dirty/flush functions
 * retire when that feature registers its own probe from Vue-owned state.
 */

export type LeaveStatus =
  | 'approved'
  | 'rejected-unsaved-edit'
  | 'rejected-pending-pdf-sync'
  | 'stale-owner'
  | 'cancelled'

export type LeaveTransition =
  | 'route-replace'
  | 'cold-park'
  | 'destroy'
  | 'hide-secondary'
  | 'promote-main'
  | 'close'
  | 'history'
  | 'batch'

export interface LeaveOwnerSnapshot {
  ownerId: string
  generation: number | null
  token: unknown
}

export interface LeaveDecision<T = unknown> {
  status: LeaveStatus
  ownerId: string
  destination: string | null
  transition: LeaveTransition
  feature?: string
  value?: T
}

export interface LeaveBlocker {
  status: Exclude<LeaveStatus, 'approved'>
  feature?: string
}

export type LeaveAssessResult =
  | boolean
  | LeaveStatus
  | (LeaveBlocker & { confirm?: () => boolean | Promise<boolean> })
  | null
  | undefined

export interface LeaveAttempt<T = unknown> {
  ownerId: string
  destination: string | null
  transition: LeaveTransition
  /** Read when the attempt is enqueued, before it waits. */
  capture: () => LeaveOwnerSnapshot | null
  still: (snapshot: LeaveOwnerSnapshot) => boolean
  assess?: (snapshot: LeaveOwnerSnapshot) => LeaveAssessResult | Promise<LeaveAssessResult>
  /** Called at most once, and only after every affected owner has approved. */
  flushNotes?: (snapshot: LeaveOwnerSnapshot) => void
  commit?: () => T | Promise<T>
}

export interface LeaveBatch<T = unknown> {
  transition: LeaveTransition
  attempts: LeaveAttempt<unknown>[]
  commit: () => T | Promise<T>
}

export interface LeaveProbe {
  id: string
  order: number
  assess: (ctx: unknown, destination: string | null) => LeaveAssessResult | Promise<LeaveAssessResult>
}

export interface TabLeaveApi {
  run<T>(attempt: LeaveAttempt<T>): Promise<LeaveDecision<T>>
  runBatch<T>(batch: LeaveBatch<T>): Promise<LeaveDecision<T>>
  registerProbe(probe: LeaveProbe): void
  registerFlush(flush: (ctx: unknown) => void): void
  assessOwner(ctx: unknown, destination: string | null): Promise<LeaveAssessResult>
  flushOwner(ctx: unknown): void
}

interface LegacyRoot {
  prksFlushPendingWorkResearchNotes?: (ctx: unknown) => void
  prksFlushPendingPrivateNotes?: (ctx: unknown) => void
}

interface LockSlot {
  done: boolean
  promise: Promise<void>
}

const BLOCKING: ReadonlySet<LeaveStatus> = new Set([
  'rejected-unsaved-edit',
  'rejected-pending-pdf-sync',
  'stale-owner',
  'cancelled',
])

function blockerFrom(result: LeaveAssessResult): LeaveBlocker | null {
  if (result == null || result === true || result === 'approved') return null
  if (result === false) return { status: 'rejected-unsaved-edit' }
  if (typeof result === 'string' && BLOCKING.has(result)) return { status: result }
  if (typeof result === 'object' && BLOCKING.has(result.status)) {
    return { status: result.status, feature: result.feature }
  }
  return { status: 'cancelled' }
}

function decisionFor<T>(
  attempt: { ownerId: string; destination: string | null; transition: LeaveTransition },
  status: LeaveStatus,
  extra?: { feature?: string; value?: T },
): LeaveDecision<T> {
  return {
    status,
    ownerId: String(attempt.ownerId),
    destination: attempt.destination,
    transition: attempt.transition,
    feature: extra && extra.feature,
    value: extra && extra.value,
  }
}

export function createTabLeave(): TabLeaveApi {
  const probes: LeaveProbe[] = []
  let flushHook: ((ctx: unknown) => void) | null = null
  const slots = new Map<string, LockSlot>()

  function registerProbe(probe: LeaveProbe) {
    const index = probes.findIndex((item) => item.id === probe.id)
    if (index >= 0) probes[index] = probe
    else probes.push(probe)
  }

  function registerFlush(flush: (ctx: unknown) => void) {
    flushHook = flush
  }

  function flushOwner(ctx: unknown) {
    if (flushHook) {
      flushHook(ctx)
      return
    }
    const root = globalThis as LegacyRoot
    if (typeof root.prksFlushPendingWorkResearchNotes === 'function') {
      root.prksFlushPendingWorkResearchNotes(ctx)
    }
    if (typeof root.prksFlushPendingPrivateNotes === 'function') {
      root.prksFlushPendingPrivateNotes(ctx)
    }
  }

  async function assessOwner(ctx: unknown, destination: string | null): Promise<LeaveAssessResult> {
    const ordered = probes.slice().sort((a, b) => a.order - b.order || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0))
    for (const probe of ordered) {
      const result = await probe.assess(ctx, destination)
      const blocker = blockerFrom(result)
      if (!blocker) continue
      if (result && typeof result === 'object' && typeof result.confirm === 'function') {
        const ok = await result.confirm()
        if (!ok) return { status: blocker.status, feature: blocker.feature || probe.id }
        continue
      }
      return { status: blocker.status, feature: blocker.feature || probe.id }
    }
    return null
  }

  function enqueue<T>(ownerIds: string[], job: () => T | Promise<T>): Promise<T> {
    const ids = [...new Set(ownerIds.map((id) => String(id)))].sort()
    const keys = ids.length ? ids : ['']
    const previous = keys.map((id) => slots.get(id)).filter((slot): slot is LockSlot => !!slot)
    const idle = previous.every((slot) => slot.done)
    let release!: () => void
    const slot: LockSlot = {
      done: false,
      promise: new Promise<void>((resolve) => {
        release = () => {
          slot.done = true
          resolve()
        }
      }),
    }
    for (const id of keys) slots.set(id, slot)
    const finish = (result: Promise<T>) => {
      /* Handled callbacks release the slot. A detached finally would reject
       * on its own when `result` rejects, even if the caller catches it. */
      void result.then(
        () => {
          release()
        },
        () => {
          release()
        },
      )
      return result
    }
    if (idle) {
      try {
        return finish(Promise.resolve(job()))
      } catch (error) {
        release()
        return Promise.reject(error)
      }
    }
    return finish(Promise.all(previous.map((item) => item.promise)).then(() => job()))
  }

  async function execute<T>(
    attempt: LeaveAttempt<T>,
    snapshot: LeaveOwnerSnapshot | null,
  ): Promise<LeaveDecision<T>> {
    if (!snapshot || !attempt.still(snapshot)) return decisionFor(attempt, 'stale-owner')
    let assessed: LeaveAssessResult = null
    try {
      assessed = attempt.assess ? await attempt.assess(snapshot) : null
    } catch {
      return decisionFor(attempt, 'cancelled')
    }
    if (!attempt.still(snapshot)) return decisionFor(attempt, 'stale-owner')
    const blocker = blockerFrom(assessed)
    if (blocker) return decisionFor(attempt, blocker.status, { feature: blocker.feature })
    return finishApproved(attempt, snapshot)
  }

  async function finishApproved<T>(
    attempt: LeaveAttempt<T>,
    snapshot: LeaveOwnerSnapshot,
  ): Promise<LeaveDecision<T>> {
    if (attempt.flushNotes) attempt.flushNotes(snapshot)
    if (!attempt.still(snapshot)) return decisionFor(attempt, 'stale-owner')
    const value = attempt.commit ? await attempt.commit() : undefined
    return decisionFor(attempt, 'approved', { value })
  }

  function run<T>(attempt: LeaveAttempt<T>): Promise<LeaveDecision<T>> {
    const snapshot = attempt.capture()
    return enqueue([attempt.ownerId], () => execute(attempt, snapshot))
  }

  function runBatch<T>(batch: LeaveBatch<T>): Promise<LeaveDecision<T>> {
    const attempts = batch.attempts || []
    const captured = attempts.map((attempt) => ({ attempt, snapshot: attempt.capture() }))
    const ids = attempts.map((attempt) => attempt.ownerId)
    const label = {
      ownerId: ids.join(','),
      destination: null,
      transition: batch.transition,
    }
    return enqueue(ids, async () => {
      const approved: { attempt: LeaveAttempt<unknown>; snapshot: LeaveOwnerSnapshot }[] = []
      for (const row of captured) {
        if (!row.snapshot || !row.attempt.still(row.snapshot)) return decisionFor<T>(label, 'stale-owner')
        let assessed: LeaveAssessResult = null
        try {
          assessed = row.attempt.assess ? await row.attempt.assess(row.snapshot) : null
        } catch {
          return decisionFor<T>(label, 'cancelled')
        }
        if (!row.attempt.still(row.snapshot)) return decisionFor<T>(label, 'stale-owner')
        const blocker = blockerFrom(assessed)
        if (blocker) return decisionFor<T>(label, blocker.status, { feature: blocker.feature })
        approved.push({ attempt: row.attempt, snapshot: row.snapshot })
      }
      /* An earlier owner can go stale while a later assessment is still
       * pending. Flush only after every approved owner is still current. */
      for (const row of approved) {
        if (!row.attempt.still(row.snapshot)) return decisionFor<T>(label, 'stale-owner')
      }
      for (const row of approved) {
        if (row.attempt.flushNotes) row.attempt.flushNotes(row.snapshot)
      }
      for (const row of approved) {
        if (!row.attempt.still(row.snapshot)) return decisionFor<T>(label, 'stale-owner')
      }
      const value = await batch.commit()
      return decisionFor<T>(label, 'approved', { value })
    })
  }

  return {
    run,
    runBatch,
    registerProbe,
    registerFlush,
    assessOwner,
    flushOwner,
  }
}
