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
  /**
   * The value to adopt from `fresh` while the owner holds `held`. Without it
   * the whole fresh value replaces the held one. An owner whose held value
   * carries more than this family (pending edits of other families, another
   * family's newer ACK) takes only this family's part from the re-read.
   */
  merge?(held: T, fresh: T): T
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
      // A merged value is stamped with the re-read's time: part of it is
      // that new, so a later ACK must not prove itself against an older one.
      const value = owner.merge ? carryObservation(fresh, owner.merge(next, fresh)) : fresh
      owner.set(value)
      if (owner.adopted) owner.adopted(value)
    }).catch(() => false)
  }
  return plan
}


/* ---- Edit-session bases ----
 *
 * An editor's Save is measured against what the form showed when it opened,
 * not against whatever PRKS holds by the time Save is pressed. Re-reading the
 * base at Save hands the server the newest revision, and a draft typed over an
 * older value then silently replaces a change the user never saw. */

/** One field's acknowledged value and the revision it carried. */
export interface FieldBase {
  value: string
  revision: number
  /**
   * This device's own unsent edit the form showed when the base was pinned.
   * Returning to `value` cancels it only while it is still unsent; once it has
   * left the queue the server holds it, and returning is a real change.
   */
  pending?: string
}

/** Field -> pinned base, or null where the base is not known. */
export type PinnedBase = Record<string, FieldBase | null>

function isFieldBase(value: unknown): value is FieldBase {
  if (!value || typeof value !== 'object') return false
  const entry = value as Partial<FieldBase>
  return typeof entry.value === 'string' && isRevision(entry.revision)
}

/**
 * Pin each field's base when an editor opens. A field keeps the revision that
 * was read only where that read holds the value the form shows: a read that
 * already holds another value is newer than the form, so the revision the form
 * was shown at is unknown and that field is null. Null when nothing was read.
 *
 * `pending` holds this device's own unsent edit per field (its value and the
 * revision it was measured against). The form shows that value, and the field
 * keeps that revision, so editing it again replaces the unsent edit rather
 * than measuring it against a base the form never showed.
 */
export function pinEditBase(
  shown: Readonly<Record<string, unknown>>,
  read: Readonly<Record<string, unknown>> | null | undefined,
  pending?: Readonly<Record<string, unknown>> | null,
): PinnedBase | null {
  if (!read || typeof read !== 'object') return null
  const pinned: PinnedBase = {}
  for (const field of Object.keys(shown)) {
    const entry = read[field]
    const own = pending ? pending[field] : undefined
    const value = shown[field] == null ? '' : String(shown[field])
    if (!isFieldBase(entry)) {
      pinned[field] = null
    } else if (own !== undefined) {
      // A read that already holds the unsent value has seen it applied (or
      // the same value written): the server is at that read's revision, so
      // the next write is measured from it, never from the older base the
      // edit replaced, which would conflict with this device's own write.
      // Otherwise the read value is the base the edit replaced only when it is
      // at the edit's own revision; a read at any other revision holds a value
      // nobody can pair with that revision, so the base is unknown.
      if (!isFieldBase(own) || own.value !== value) {
        pinned[field] = null
      } else if (own.value === entry.value) {
        pinned[field] = { value: entry.value, revision: Math.max(entry.revision, own.revision) }
      } else {
        pinned[field] = entry.revision === own.revision
          ? { value: entry.value, revision: own.revision, pending: own.value }
          : null
      }
    } else {
      pinned[field] = entry.value === value ? { value: entry.value, revision: entry.revision } : null
    }
  }
  return pinned
}

/**
 * The entry an edit-base snapshot keeps when a read finishes: a read that
 * started before a newer acknowledgement must not roll the pair back.
 */
export function newerEditBase(held: unknown, read: FieldBase | null): FieldBase | null {
  return isFieldBase(held) && isFieldBase(read) && held.revision > read.revision ? held : read
}

/**
 * An edit base, read as one value/revision pair, after this device's own
 * write of that field is acknowledged. `undefined` keeps the entry: it is at
 * or past the ACK. The write's value at the ACK revision replaces an entry the
 * write was measured against. Any other entry is null: a revision between the
 * two holds a value nobody here has read.
 */
export function acknowledgeEditBase(
  entry: unknown,
  write: { value: string; baseRevision: unknown; ackRevision: number },
): FieldBase | null | undefined {
  if (!isFieldBase(entry)) return null
  if (entry.revision >= write.ackRevision) return undefined
  return entry.revision === write.baseRevision ? { value: write.value, revision: write.ackRevision } : null
}

/** What a Save sends: the changed fields, their pinned bases, and any changed field whose base is unknown. */
export interface PinnedEdits {
  changes: Record<string, string>
  base: Record<string, FieldBase>
  unknown: string[]
}

/**
 * The fields this session changed, each measured against its pinned base. A
 * changed field whose base is unknown is listed in `unknown` and must not be
 * sent: the caller tells the user it changed elsewhere and keeps the draft.
 */
export function pinnedEdits(
  shown: Readonly<Record<string, unknown>>,
  draft: Readonly<Record<string, unknown>>,
  pinned: PinnedBase,
): PinnedEdits {
  const changes: Record<string, string> = {}
  const base: Record<string, FieldBase> = {}
  const unknown: string[] = []
  for (const field of Object.keys(shown)) {
    const entry = pinned[field]
    if (entry) base[field] = entry
    if (!Object.prototype.hasOwnProperty.call(draft, field)) continue
    const desired = draft[field] == null ? '' : String(draft[field])
    const was = shown[field] == null ? '' : String(shown[field])
    if (desired === was) continue
    changes[field] = desired
    if (!entry) unknown.push(field)
  }
  return { changes, base, unknown }
}
