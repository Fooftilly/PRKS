/**
 * The one ordering rule every browser sync family uses to merge an
 * acknowledgement (ACK) into what this page already holds.
 *
 * Classic code reaches it as `window.prksSyncReconcile`
 * (`frontend/js/sync-reconcile.js`, built from `reconcile-entry.ts`).
 *
 * The rule (#1425): an ACK may replace a held value only when that value is
 * provably not newer than the ACK.
 *
 * - A value that carries its own revision for the acknowledged target (a
 *   revision projection such as `work-tag-options`) is proven by comparing
 *   revisions: an older ACK is `superseded`; an equal or newer one applies.
 * - A value without a revision (a Work or Folder detail, a list row) is proven
 *   by WHEN it was read. A value whose server read finished before the
 *   operation was created cannot hold any state committed after that
 *   operation, and every revision that could supersede the ACK was committed
 *   after it. Otherwise the ACK is `unproven` for that value: the caller drops
 *   or re-reads it instead of writing the ACK over what may be newer data.
 * - A value that already shows what the ACK says needs no proof.
 *
 * Pending operations never enter the disposable cache. The acknowledged value
 * is the base, and `overlay` folds the durable queue over it at read time, so a
 * newer pending operation for the same target stays visible after an older ACK
 * lands.
 *
 * Revision semantics are the server ledger's (`docs/local-first-sync.md`):
 * non-negative safe integers, compared numerically. Nothing here changes the
 * sync protocol.
 */

export type Revision = number

export type AckDecision = 'apply' | 'superseded' | 'unproven'

export interface AckEvidence {
  /** The revision the server acknowledged. */
  ackRevision: unknown
  /** The revision this value is known to hold for the acknowledged target, if it carries one. */
  heldRevision?: unknown
  /** When this value's server read finished (epoch ms), if known. */
  observedAt?: unknown
  /** When the acknowledged operation was created (epoch ms or ISO string), if known. */
  operationCreatedAt?: unknown
  /** True when the value already shows what the ACK says. */
  agrees?: boolean
}

/** A durable operation as the local queue stores it; only the fields the core reads. */
export interface QueuedOperation {
  op_id?: string
  status?: string
  sequence?: number
  created_at?: string
}

export function isRevision(value: unknown): value is Revision {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0
}

/** Negative when `a` is older than `b`, zero when equal, positive when newer. */
export function compareRevisions(a: Revision, b: Revision): number {
  if (!isRevision(a) || !isRevision(b)) throw new TypeError('revisions are non-negative safe integers')
  return a === b ? 0 : a < b ? -1 : 1
}

/** One key per cached entity; the same encoding `offline-runtime.js` uses. */
export function entityKey(kind: string, id: string): string {
  return String(kind) + '\u0000' + String(id)
}

/** Epoch ms from a number or an ISO timestamp; null when it is neither. */
export function timeOf(value: unknown): number | null {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null
  if (typeof value !== 'string' || value === '') return null
  const parsed = Date.parse(value)
  return Number.isFinite(parsed) ? parsed : null
}

/** True when a value read at `observedAt` provably predates an operation created at `createdAt`. */
export function readBefore(observedAt: unknown, createdAt: unknown): boolean {
  const read = timeOf(observedAt)
  const created = timeOf(createdAt)
  return read !== null && created !== null && read < created
}

export function decideAck(evidence: AckEvidence): AckDecision {
  if (!isRevision(evidence.ackRevision)) throw new TypeError('an ACK carries a revision')
  if (evidence.heldRevision !== undefined && evidence.heldRevision !== null) {
    if (!isRevision(evidence.heldRevision)) return 'unproven'
    return compareRevisions(evidence.ackRevision, evidence.heldRevision) < 0 ? 'superseded' : 'apply'
  }
  if (evidence.agrees === true) return 'apply'
  return readBefore(evidence.observedAt, evidence.operationCreatedAt) ? 'apply' : 'unproven'
}

/**
 * What to do with one held value when an ACK arrives.
 *
 * - `superseded`: the held revision is newer; leave everything.
 * - `unchanged`: the value already shows the ACK.
 * - `apply`: write the ACK into the value.
 * - `unproven`: the value may be newer. A disposable cache drops it; a live
 *   owner writes the ACK (so the user's own change does not vanish) and then
 *   re-reads, letting the authoritative read correct it (`adoptFresh`).
 *
 * `heldRevision` may come from a sibling projection (the Tag-options revision
 * for a Work detail): it can prove an ACK superseded but never proves the
 * value itself current, because the value may have been read later.
 */
export type AckPlan = 'superseded' | 'unchanged' | 'apply' | 'unproven'

export function planAck(evidence: AckEvidence): AckPlan {
  if (evidence.heldRevision !== undefined && evidence.heldRevision !== null &&
    decideAck({ ackRevision: evidence.ackRevision, heldRevision: evidence.heldRevision }) === 'superseded') {
    return 'superseded'
  }
  if (evidence.agrees === true) return 'unchanged'
  return decideAck({
    ackRevision: evidence.ackRevision,
    observedAt: evidence.observedAt,
    operationCreatedAt: evidence.operationCreatedAt,
  })
}

export interface FreshRead<T> {
  value: T | null
  source: string
}

/**
 * Re-read a live owner's value after an `unproven` ACK and adopt the server's
 * answer, but only while the owner still holds `expected`. Anything that
 * replaced it meanwhile brought its own ordering decision, and a read that
 * started before that change must not undo it. A cached or unavailable answer
 * is not authoritative and is ignored. Resolves true when adopted.
 */
export async function adoptFresh<T>(
  read: () => Promise<FreshRead<T> | null | undefined>,
  current: () => unknown,
  expected: unknown,
  adopt: (value: T) => void,
): Promise<boolean> {
  let fresh: FreshRead<T> | null | undefined
  try {
    fresh = await read()
  } catch {
    return false
  }
  if (!fresh || fresh.source !== 'server' || fresh.value === null || fresh.value === undefined) return false
  if (current() !== expected) return false
  adopt(fresh.value)
  return true
}

function unsettled(op: QueuedOperation): boolean {
  return op.status !== 'acknowledged'
}

/**
 * The operations still to be applied over an acknowledged value, in queue
 * order. `exceptOpId` leaves out the operation whose ACK is being merged.
 */
export function pendingFor<Op extends QueuedOperation>(
  operations: readonly Op[] | null | undefined,
  match: (op: Op) => boolean,
  exceptOpId?: string | null,
): Op[] {
  if (!Array.isArray(operations)) return []
  const rows = operations.filter((op) => !!op && unsettled(op) && op.op_id !== exceptOpId && match(op))
  return rows
    .map((op, index) => ({ op, index }))
    .sort((a, b) => {
      const left = a.op.sequence
      const right = b.op.sequence
      if (Number.isSafeInteger(left) && Number.isSafeInteger(right) && left !== right) {
        return (left as number) - (right as number)
      }
      return a.index - b.index
    })
    .map((row) => row.op)
}

/** The acknowledged base with every matching pending operation folded over it, oldest first. */
export function overlay<T, Op extends QueuedOperation>(
  base: T,
  operations: readonly Op[] | null | undefined,
  match: (op: Op) => boolean,
  apply: (value: T, op: Op) => T,
): T {
  return pendingFor(operations, match).reduce((value, op) => apply(value, op), base)
}

/* When each in-memory value was read. Keyed by object identity, so a value
 * that is copied or replaced loses its proof unless the copier carries it. */
const observations = new WeakMap<object, number>()

/** Record that `value` came from a read that finished no later than `at`. */
export function observe<T>(value: T, at: unknown): T {
  const time = timeOf(at)
  if (value !== null && typeof value === 'object' && time !== null) observations.set(value, time)
  return value
}

/** When `value` was read, or null when this page cannot prove it. */
export function observedAt(value: unknown): number | null {
  if (value === null || typeof value !== 'object') return null
  return observations.get(value) ?? null
}

/**
 * Keep the read time of `from` on `to`. Use it when `to` is `from` with an
 * ACK or local edit applied: the rest of `to` is still only as new as `from`.
 */
export function carryObservation<T>(from: unknown, to: T): T {
  const time = observedAt(from)
  if (time !== null && to !== null && typeof to === 'object') observations.set(to, time)
  return to
}

/** A live owner's held value (a TabContext entity), as `applyOwnerAck` reaches it. */
export interface OwnerValue<T extends object> {
  get(): T | null
  set(value: T): void
  /** True when `value` already shows the ACK. */
  agrees(value: T): boolean
  /** `value` with the ACK applied; a new object. */
  patch(value: T): T
  /** An authoritative re-read of the whole value. */
  read(): Promise<FreshRead<T> | null | undefined>
  /** Called after a re-read replaced the value, to repaint. */
  adopted?(value: T): void
}

/**
 * Merge one ACK into a live owner's value under the shared rule. The value's
 * read time comes from `observe` (stamped by the offline read path) and is
 * carried onto the patched copy. `evidence.heldRevision` is an optional
 * sibling revision (an editor's Tag-options base). Returns the plan used.
 */
export function applyOwnerAck<T extends object>(
  evidence: Pick<AckEvidence, 'ackRevision' | 'heldRevision' | 'operationCreatedAt'>,
  owner: OwnerValue<T>,
): AckPlan {
  const value = owner.get()
  if (!value) return 'unchanged'
  const plan = planAck({
    ackRevision: evidence.ackRevision,
    heldRevision: evidence.heldRevision,
    operationCreatedAt: evidence.operationCreatedAt,
    agrees: owner.agrees(value),
    observedAt: observedAt(value),
  })
  if (plan !== 'apply' && plan !== 'unproven') return plan
  const next = carryObservation(value, owner.patch(value))
  owner.set(next)
  if (plan === 'unproven') {
    adoptFresh(owner.read, owner.get, next, (fresh) => {
      owner.set(fresh)
      if (owner.adopted) owner.adopted(fresh)
    }).catch(() => false)
  }
  return plan
}

