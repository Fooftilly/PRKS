/**
 * Page-level writer registry: one coalescing writer per editor lineage.
 *
 * INV-DRAFT-1: while a writer reports a draft, its newest generation is either
 * committed to recovery storage, held by the emergency entry plan (only while
 * localStorage has shown it can take the planned payload), or covered
 * by an armed leave guard. A generation in none of those reads `unprotected`.
 *
 * Coalescer, per lineage: at most one write in flight; a newer generation
 * while one is in flight replaces the pending one, and the newest is written
 * when the write completes; older or equal generations are ignored.
 * - Ordinary bodies (<= 256 Ki chars): first write after 300 ms idle, at most
 *   1 s after the first unrecorded change.
 * - Large bodies: the write starts on the next task and the leave guard is
 *   armed from the first uncommitted generation until that generation commits.
 *
 * The registry, not TabContext timers, owns the writes, so releasing a writer
 * finishes its last write instead of cancelling it. A writer that finds its
 * lineage owned by another page or removed moves its newest generation to a
 * fresh lineage; it never overwrites or drops. `beforeunload` is registered
 * only while some writer needs the guard, and `pagehide` /
 * `visibilitychange` only while some writer is pending.
 */

import { jsonEscapeExtra, planEmergency, writeEmergency, type EmergencyStorage, type EmergencyWriteResult } from './emergency'
import type { PageIdentity } from './identity'
import type { DeleteOutcome, RecoveryStore, RecoveryStoreErrorCode } from './store'
import { RecoveryStoreError } from './store'
import {
  EMERGENCY_ENTRY_OVERHEAD_CHARS,
  EMERGENCY_VERSION,
  IDLE_WRITE_MS,
  LARGE_BODY_CHARS,
  MAX_WRITE_WAIT_MS,
  RETRY_FIRST_MS,
  RETRY_MAX_MS,
  UNKNOWN_BASE,
  emergencyKeyOf,
  reservationKeyOf,
  reservationValue,
  mintId,
  type DraftBase,
  type DraftEntityType,
  type DraftKind,
  type DraftOwner,
  type DraftRecord,
  type EmergencyEntry,
  type EmergencyPayload,
  type RandomSource,
} from './schema'

export type WriterState = 'clean' | 'pending' | 'protected' | 'unprotected'

export interface Scheduler {
  set(fn: () => void, ms: number): unknown
  clear(handle: unknown): void
}

export interface ListenerTarget {
  addEventListener(type: string, listener: (event: Event) => void): void
  removeEventListener(type: string, listener: (event: Event) => void): void
}

export interface VisibilityTarget extends ListenerTarget {
  readonly visibilityState: string
}

export type WriterEvent =
  | { type: 'ownership-lost'; sessionKey: string; oldDraftId: string; newDraftId: string; reason: string }
  | { type: 'unprotected'; sessionKey: string; draftId: string; code: RecoveryStoreErrorCode | 'unknown' }
  | { type: 'protected'; sessionKey: string; draftId: string; generation: number }

export interface WriterRegistryOptions {
  store: RecoveryStore
  identity: Pick<PageIdentity, 'pageInstanceId' | 'current' | 'setLineageResponder'>
  scheduler?: Scheduler
  now?: () => number
  random?: RandomSource
  /** `window`: beforeunload and pagehide. */
  window?: ListenerTarget | null
  /** `document`: visibilitychange. */
  document?: VisibilityTarget | null
  /** `localStorage` for the emergency entry. */
  emergencyStorage?: EmergencyStorage | null
  onEvent?: (event: WriterEvent) => void
}

export interface OpenWriterInput {
  kind: DraftKind
  entityType: DraftEntityType
  entityId: string
  paneId: string
  /** Distinguishes sessions for self-live vs other-live; defaults to a fresh id. */
  sessionKey?: string
  base?: DraftBase
}

export interface DraftWriter {
  readonly sessionKey: string
  draftId(): string | null
  state(): WriterState
  committedGeneration(): number
  needsLeaveGuard(): boolean
  heldByEmergency(): boolean
  setBase(base: DraftBase): void
  setPane(paneId: string): void
  /** Reports the editor's newest generation; never reads the editor itself. */
  edit(generation: number, body: string): void
  /** Starts the pending write now and resolves when the newest generation committed or failed. */
  flush(): Promise<void>
  /** Finishes the last write, then leaves the registry. The lineage stays for recovery. */
  release(): Promise<void>
  /** Clears the lineage only if the stored generation and exact body match the acknowledgement. */
  acknowledged(generation: number, body: string): Promise<DeleteOutcome | 'none'>
  /** Explicit user discard. */
  discard(): Promise<void>
}

export interface WriterRegistry {
  openWriter(input: OpenWriterInput): DraftWriter
  /** Compare-and-set adoption of an orphan; null when another page won. */
  adopt(record: DraftRecord, input: Omit<OpenWriterInput, 'kind' | 'entityType' | 'entityId' | 'base'>): Promise<DraftWriter | null>
  ownerOf(draftId: string): string | null
  writers(): DraftWriter[]
  leaveGuardActive(): boolean
  emergencyListenersActive(): boolean
  /** The synchronous unload write, also callable directly by tests. */
  writeEmergencyNow(): EmergencyWriteResult | 'nothing-pending' | 'unavailable'
  dispose(): void
}

interface Pending {
  generation: number
  body: string
}

/** Worst-case reservation char on every engine (see canHold). */
const RESERVATION_FILLER = '\u0101'
/** Most slack a reservation takes beyond the payload it covers. */
const RESERVATION_STEP_CHARS = 64 * 1024

const defaultScheduler: Scheduler = {
  set: (fn, ms) => setTimeout(fn, ms),
  clear: (handle) => clearTimeout(handle as ReturnType<typeof setTimeout>),
}

function codeOf(error: unknown): RecoveryStoreErrorCode | 'unknown' {
  return error instanceof RecoveryStoreError ? error.code : 'unknown'
}

export function createWriterRegistry(options: WriterRegistryOptions): WriterRegistry {
  const { store, identity } = options
  const scheduler = options.scheduler || defaultScheduler
  const now = options.now || Date.now
  const random = options.random || globalThis.crypto
  const win = options.window === undefined ? (typeof window !== 'undefined' ? window : null) : options.window
  const doc = options.document === undefined ? (typeof document !== 'undefined' ? document : null) : options.document
  const storage = options.emergencyStorage === undefined ? defaultLocalStorage() : options.emergencyStorage
  const emit = options.onEvent || (() => {})
  const emergencyKey = emergencyKeyOf(identity.pageInstanceId)
  const reservationKey = reservationKeyOf(identity.pageInstanceId)

  const live = new Set<WriterImpl>()
  /** Writers whose body the budget plan puts in the emergency entry. */
  let planned = new Set<WriterImpl>()
  /** `planned`, but only while emergency storage has shown it can keep those bodies; otherwise empty. */
  let held = new Set<WriterImpl>()
  /**
   * Chars of filler this page keeps written under its reservation key while
   * something is pending. A body counts as held only when the planned payload
   * fits within it. The quota stays occupied, so no other write, from this
   * page or another, can take it; at hide the reservation is swapped for the
   * real payload. Blocked, missing or too-full storage leaves the leave guard
   * armed before unload instead of discovering the failure at pagehide.
   */
  let reservedChars = 0
  /**
   * Set at pagehide: the page is going away (or into the back/forward cache),
   * so it does not reserve again after its emergency write; a reservation
   * left by a closed page would only hold quota until a later page cleans it.
   * Cleared at pageshow, which re-plans.
   */
  let hiding = false
  /** Set when a real emergency write could not keep every planned body; cleared by a full write. */
  let distrusted = false
  let guardOn = false
  let emergencyOn = false
  let emergencyWritten = false
  /** Draft ids in the emergency key as last written. */
  let emergencyIds = new Set<string>()
  /** Discarded lineages a stale emergency key still lists; each has a tombstone record. */
  const tombstoned = new Set<string>()
  let refreshingEmergency = false
  let disposed = false

  function onBeforeUnload(event: Event): void {
    event.preventDefault()
    ;(event as BeforeUnloadEvent).returnValue = ''
  }
  function onPageHide(): void {
    hiding = true
    writeEmergencyNow()
  }
  function onPageShow(): void {
    if (!hiding) return
    hiding = false
    changed()
  }
  function onVisibility(): void {
    if (doc && doc.visibilityState === 'hidden') writeEmergencyNow()
    else onResume()
  }
  /**
   * Back from hidden or frozen: write the reservation again before counting
   * on it. Without Web Locks a page that claimed this runtime meanwhile may
   * have removed it, taking a frozen page for a closed one.
   */
  function onResume(): void {
    if (hiding || reservedChars === 0) return
    reservedChars = 0
    changed()
  }
  identity.setLineageResponder((draftId) => ownerOf(draftId) !== null)

  function ownerOf(draftId: string): string | null {
    for (const w of live) if (w.currentDraftId() === draftId) return w.sessionKey
    return null
  }

  function pendingWriters(): WriterImpl[] {
    return [...live].filter((w) => w.pendingBody() !== null)
  }

  /** Recomputes the emergency plan, the listeners and the stored key after any writer state change. */
  function changed(): void {
    if (disposed) return
    const pending = pendingWriters()
    planHeld(pending)
    setGuard(pending.some((w) => w.needsLeaveGuard()))
    setEmergencyListeners(pending.length > 0)
    refreshEmergencyKey(pending)
  }

  function planHeld(pending: WriterImpl[]): void {
    const lengths = pending.map((w) => (w.pendingBody() as Pending).body.length)
    const plan = planEmergency(lengths)
    planned = new Set(pending.filter((_, i) => plan.has(i)))
    held = pending.length && !hiding && canHold(payloadChars(pending.length)) ? planned : new Set()
  }

  function setGuard(needGuard: boolean): void {
    if (win && needGuard !== guardOn) {
      if (needGuard) win.addEventListener('beforeunload', onBeforeUnload)
      else win.removeEventListener('beforeunload', onBeforeUnload)
    }
    guardOn = needGuard
  }

  function setEmergencyListeners(needEmergency: boolean): void {
    if (needEmergency !== emergencyOn) {
      const method = needEmergency ? 'addEventListener' : 'removeEventListener'
      if (win) {
        win[method]('pagehide', onPageHide)
        win[method]('pageshow', onPageShow)
      }
      if (doc) {
        doc[method]('visibilitychange', onVisibility)
        doc[method]('resume', onResume)
      }
      if (!needEmergency) {
        releaseReservation()
        hiding = false
      }
    }
    emergencyOn = needEmergency
  }

  /**
   * An entry whose writer committed, was discarded or moved lineage must not
   * outlive it: merged after a crash it could recreate a discarded draft.
   */
  function refreshEmergencyKey(pending: WriterImpl[]): void {
    if (!emergencyWritten || !storage || refreshingEmergency) return
    if (!pending.length) {
      removeEmergencyKey()
      return
    }
    const current = new Set(pending.map((w) => w.currentDraftId()))
    if (![...emergencyIds].some((id) => !current.has(id))) return
    refreshingEmergency = true
    try {
      if (writeEmergencyNow() === 'failed') removeEmergencyKey()
    } finally {
      refreshingEmergency = false
    }
  }

  function removeEmergencyKey(): void {
    if (!storage) return
    try {
      storage.removeItem(emergencyKey)
      emergencyWritten = false
      emergencyIds = new Set()
      releaseTombstones()
    } catch {
      // Removal refused: overwrite with no entries, so a stale first-generation
      // entry can never recreate a draft that was discarded or acknowledged.
      const claim = identity.current()
      const empty: EmergencyPayload = {
        v: EMERGENCY_VERSION,
        pageInstanceId: identity.pageInstanceId,
        runtimeId: claim ? claim.runtimeId : null,
        at: now(),
        entries: [],
      }
      if (writeEmergency(storage, emergencyKey, empty) !== 'failed') {
        emergencyWritten = false
        emergencyIds = new Set()
        releaseTombstones()
      }
    }
  }

  /** Drops the tombstones whose lineage the emergency key no longer lists. */
  function releaseTombstones(): void {
    for (const draftId of [...tombstoned]) {
      if (emergencyWritten && emergencyIds.has(draftId)) continue
      tombstoned.delete(draftId)
      store.clearTombstone(draftId, identity.pageInstanceId).catch(() => {
        /* a leftover tombstone only suppresses; it is never a candidate */
      })
    }
  }

  /**
   * Serialized size of the planned payload: each planned body with its exact
   * JSON escaping (counted once per generation), plus per-entry metadata.
   */
  function payloadChars(entries: number): number {
    let chars = entries * EMERGENCY_ENTRY_OVERHEAD_CHARS
    for (const w of planned) chars += w.serializedBodyChars()
    return chars
  }

  /**
   * Whether this page's reservation covers a payload of `chars`. Beyond what
   * is reserved it grows the reservation by up to RESERVATION_STEP_CHARS of
   * slack (then to the exact size), so typing does not rewrite it on every
   * keystroke while a large note never holds twice its size of other writes'
   * quota. Only a page whose runtime claim has settled reserves: its page
   * lock is held by then, so no live page's reservation reads as gone. The
   * value is tagged with the runtime id, so without Web Locks a later page
   * that claims this runtime removes it once this page is gone.
   */
  function canHold(chars: number): boolean {
    const claim = identity.current()
    if (!storage || distrusted || !claim) return false
    if (chars <= reservedChars) return true
    for (const size of [chars + Math.min(chars, RESERVATION_STEP_CHARS), chars]) {
      try {
        // A filler above U+00FF on purpose: Chromium stores an all-Latin-1
        // value at 1 byte per char and anything else at 2, and its quota counts
        // bytes, so an ASCII filler would reserve half of what a real note needs.
        storage.setItem(reservationKey, reservationValue(claim.runtimeId, RESERVATION_FILLER.repeat(size)))
        reservedChars = size
        return true
      } catch {
        /* blocked or over quota at this size */
      }
    }
    return false
  }

  function releaseReservation(): void {
    if (!storage || reservedChars === 0) return
    reservedChars = 0
    try {
      storage.removeItem(reservationKey)
    } catch {
      /* a later page removes it once Web Locks or a claim of this runtime show this page gone */
    }
  }

  function noteEmergencyResult(result: EmergencyWriteResult | 'unavailable'): void {
    if (result === 'written') {
      distrusted = false
    } else {
      // Bodies were dropped or nothing was stored: guard every pending body
      // until a full write succeeds again.
      distrusted = true
    }
    // The reservation was spent on this write: re-plan, which reserves again
    // for what is still pending or arms the guard.
    changed()
  }

  function writeEmergencyNow(): EmergencyWriteResult | 'nothing-pending' | 'unavailable' {
    const pending = pendingWriters()
    if (!pending.length) return 'nothing-pending'
    if (!storage) {
      noteEmergencyResult('unavailable')
      return 'unavailable'
    }
    const claim = identity.current()
    const payload: EmergencyPayload = {
      v: EMERGENCY_VERSION,
      pageInstanceId: identity.pageInstanceId,
      runtimeId: claim ? claim.runtimeId : null,
      at: now(),
      entries: pending.map((w) => w.emergencyEntry(planned.has(w))),
    }
    // The reserved quota becomes the payload's: free it in the same task, then write.
    releaseReservation()
    const result = writeEmergency(storage, emergencyKey, payload)
    if (result !== 'failed') {
      emergencyWritten = true
      emergencyIds = new Set(payload.entries.map((e) => e.draftId))
      releaseTombstones()
    }
    noteEmergencyResult(result)
    return result
  }

  class WriterImpl implements DraftWriter {
    readonly sessionKey: string
    private readonly kind: DraftKind
    private readonly entityType: DraftEntityType
    private readonly entityId: string
    private paneId: string
    private base: DraftBase
    private lineageId: string | null = null
    private lineageCreatedAt = 0
    /** A record for `lineageId` exists that this page owns (created or adopted). */
    private lineageStored = false
    private committed = 0
    private lastSeen = 0
    private latest: Pending | null = null
    private status: WriterState = 'clean'
    private inFlight: Promise<void> | null = null
    private writeRequested = false
    private failedGeneration = 0
    private idleTimer: unknown = null
    private maxWaitTimer: unknown = null
    private nextTaskTimer: unknown = null
    private retryTimer: unknown = null
    private retryDelay = 0
    private released = false
    private readonly cleared = new Set<string>()

    constructor(input: OpenWriterInput) {
      this.sessionKey = input.sessionKey || mintId('d', random).replace(/^d-/, 's-')
      this.kind = input.kind
      this.entityType = input.entityType
      this.entityId = input.entityId
      this.paneId = input.paneId
      this.base = input.base ? { ...input.base } : { ...UNKNOWN_BASE }
    }

    adoptRecord(record: DraftRecord): void {
      this.lineageId = record.draftId
      this.lineageCreatedAt = record.createdAt
      this.lineageStored = true
      this.committed = record.generation
      this.lastSeen = record.generation
      this.base = { ...record.base }
      this.status = 'protected'
    }

    currentDraftId(): string | null {
      return this.lineageId
    }
    pendingBody(): Pending | null {
      return this.latest
    }
    private escapeCache: { generation: number; chars: number } | null = null
    /** The pending body's length once JSON-serialized; recounted only when the generation changes. */
    serializedBodyChars(): number {
      const pending = this.latest
      if (!pending) return 0
      if (!this.escapeCache || this.escapeCache.generation !== pending.generation) {
        this.escapeCache = { generation: pending.generation, chars: pending.body.length + 2 + jsonEscapeExtra(pending.body) }
      }
      return this.escapeCache.chars
    }
    draftId(): string | null {
      return this.lineageId
    }
    state(): WriterState {
      return this.status
    }
    committedGeneration(): number {
      return this.committed
    }
    heldByEmergency(): boolean {
      return held.has(this)
    }
    needsLeaveGuard(): boolean {
      if (!this.latest) return false
      if (this.status === 'unprotected') return true
      return !held.has(this)
    }
    setBase(base: DraftBase): void {
      this.base = { ...base }
    }
    setPane(paneId: string): void {
      this.paneId = paneId
    }

    private owner(): DraftOwner {
      const claim = identity.current()
      return {
        runtimeId: claim ? claim.runtimeId : null,
        pageInstanceId: identity.pageInstanceId,
        paneId: this.paneId,
        claimedAt: now(),
      }
    }

    private startLineage(): void {
      this.lineageId = mintId('d', random)
      this.lineageCreatedAt = now()
      this.lineageStored = false
      this.committed = 0
    }

    private clearTimers(): void {
      for (const handle of [this.idleTimer, this.maxWaitTimer, this.nextTaskTimer]) {
        if (handle !== null) scheduler.clear(handle)
      }
      this.idleTimer = null
      this.maxWaitTimer = null
      this.nextTaskTimer = null
    }

    private clearRetry(): void {
      if (this.retryTimer !== null) scheduler.clear(this.retryTimer)
      this.retryTimer = null
    }

    edit(generation: number, body: string): void {
      if (this.released || disposed) return
      if (!(generation > this.lastSeen)) return
      this.lastSeen = generation
      if (!this.lineageId) this.startLineage()
      this.latest = { generation, body }
      if (this.status !== 'unprotected') this.status = 'pending'
      if (body.length > LARGE_BODY_CHARS) {
        if (this.idleTimer !== null) scheduler.clear(this.idleTimer)
        if (this.maxWaitTimer !== null) scheduler.clear(this.maxWaitTimer)
        this.idleTimer = null
        this.maxWaitTimer = null
        if (this.inFlight) this.writeRequested = true
        else if (this.nextTaskTimer === null) this.nextTaskTimer = scheduler.set(() => this.startWrite(), 0)
      } else {
        if (this.idleTimer !== null) scheduler.clear(this.idleTimer)
        this.idleTimer = scheduler.set(() => this.startWrite(), IDLE_WRITE_MS)
        if (this.maxWaitTimer === null) this.maxWaitTimer = scheduler.set(() => this.startWrite(), MAX_WRITE_WAIT_MS)
      }
      changed()
    }

    private startWrite(): void {
      this.clearTimers()
      if (this.inFlight) {
        this.writeRequested = true
        return
      }
      const pending = this.latest
      const draftId = this.lineageId
      if (!pending || !draftId) return
      const create = this.lineageStored
        ? undefined
        : { kind: this.kind, entityType: this.entityType, entityId: this.entityId, owner: this.owner(), base: this.base }
      const attempt = store
        .writeGeneration({
          draftId,
          pageInstanceId: identity.pageInstanceId,
          generation: pending.generation,
          body: pending.body,
          paneId: this.paneId,
          base: this.base,
          create,
        })
        .then(
          (outcome) => {
            if (this.lineageId !== draftId) {
              this.writeRequested = true
              return
            }
            if (outcome === 'ok') {
              this.lineageStored = true
              this.committed = pending.generation
              this.failedGeneration = 0
              this.retryDelay = 0
              this.clearRetry()
              if (this.latest && this.latest.generation === pending.generation) {
                this.latest = null
                this.status = 'protected'
                emit({ type: 'protected', sessionKey: this.sessionKey, draftId, generation: pending.generation })
              } else {
                // A newer generation arrived meanwhile. A large one is written
                // now; an ordinary one keeps its idle / max-wait timers.
                this.status = 'pending'
                if (this.latest && this.latest.body.length > LARGE_BODY_CHARS) this.writeRequested = true
              }
              return
            }
            // Owned by another page, removed, or newer elsewhere: never write into it again.
            this.startLineage()
            if (!this.cleared.has(draftId)) {
              emit({ type: 'ownership-lost', sessionKey: this.sessionKey, oldDraftId: draftId, newDraftId: this.lineageId as string, reason: outcome })
            }
            this.writeRequested = true
          },
          (error: unknown) => {
            if (this.lineageId !== draftId) {
              // Detached (discarded or acknowledged) meanwhile: write the newer lineage, if any.
              this.writeRequested = true
              return
            }
            this.failedGeneration = pending.generation
            if (this.latest) this.status = 'unprotected'
            emit({ type: 'unprotected', sessionKey: this.sessionKey, draftId, code: codeOf(error) })
            this.scheduleRetry()
          },
        )
        .finally(() => {
          this.inFlight = null
          const again = this.writeRequested && this.latest !== null
          this.writeRequested = false
          if (again) this.startWrite()
          changed()
          this.leaveIfDone()
        })
      this.inFlight = attempt
    }

    private scheduleRetry(): void {
      if (disposed) return
      this.clearRetry()
      this.retryDelay = this.retryDelay ? Math.min(this.retryDelay * 2, RETRY_MAX_MS) : RETRY_FIRST_MS
      this.retryTimer = scheduler.set(() => {
        this.retryTimer = null
        this.startWrite()
      }, this.retryDelay)
    }

    async flush(): Promise<void> {
      for (let round = 0; round < 16; round++) {
        if (this.inFlight) {
          await this.inFlight
          continue
        }
        if (!this.latest) return
        if (this.status === 'unprotected' && this.failedGeneration === this.latest.generation && round > 0) return
        this.startWrite()
        if (!this.inFlight) return
      }
    }

    async release(): Promise<void> {
      if (this.released) return
      await this.flush()
      this.released = true
      // A generation that still failed to commit stays registered (emergency
      // entry, leave guard, retry) until it commits; only then does it leave.
      this.leaveIfDone()
    }

    leaveIfDone(): void {
      if (!this.released || this.latest) return
      this.clearTimers()
      this.clearRetry()
      live.delete(this)
      changed()
    }

    async acknowledged(generation: number, body: string): Promise<DeleteOutcome | 'none'> {
      // A write of this (or a newer) generation may still be pending: let the
      // record reach it so the exact compare-and-delete can match.
      if (this.latest && this.latest.generation <= generation) await this.flush()
      else if (this.inFlight) await this.inFlight
      const draftId = this.lineageId
      if (!draftId) return 'none'
      const outcome = await store.deleteIfAcknowledged(draftId, generation, body)
      if (outcome === 'deleted' && this.lineageId === draftId) {
        this.cleared.add(draftId)
        if (this.latest) {
          this.startLineage()
        } else {
          this.lineageId = null
          this.lineageStored = false
          this.committed = 0
          this.status = 'clean'
        }
        changed()
      }
      if (outcome === 'deleted') await this.tombstoneIfListed(draftId)
      return outcome
    }

    async discard(): Promise<void> {
      this.clearTimers()
      this.clearRetry()
      this.latest = null
      // Detach before waiting: an edit that arrives during the wait starts a
      // fresh lineage, and the in-flight write's result no longer applies here.
      const draftId = this.lineageId
      if (draftId) this.cleared.add(draftId)
      this.lineageId = null
      this.lineageStored = false
      this.committed = 0
      this.failedGeneration = 0
      this.retryDelay = 0
      this.status = 'clean'
      changed()
      if (this.inFlight) await this.inFlight
      if (!this.latest && this.status !== 'clean') {
        this.status = 'clean'
        changed()
      }
      if (draftId && !(await this.tombstoneIfListed(draftId))) await store.discard(draftId)
    }

    /**
     * A key that could be neither removed nor emptied still lists this retired
     * lineage: leave a tombstone, so no later page recreates the draft from it.
     */
    private async tombstoneIfListed(draftId: string): Promise<boolean> {
      if (!emergencyWritten || !emergencyIds.has(draftId)) return false
      tombstoned.add(draftId)
      await store.discard(draftId, {
        kind: this.kind,
        entityType: this.entityType,
        entityId: this.entityId,
        generation: this.lastSeen,
        pageInstanceId: identity.pageInstanceId,
      })
      // Cleared while the tombstone was being written: drop it again.
      releaseTombstones()
      return true
    }

    emergencyEntry(holdBody: boolean): EmergencyEntry {
      const pending = this.latest as Pending
      const committedGeneration = this.lineageStored ? this.committed : 0
      const entry: EmergencyEntry = {
        draftId: this.lineageId as string,
        kind: this.kind,
        entityType: this.entityType,
        entityId: this.entityId,
        generation: pending.generation,
        committedGeneration,
        body: holdBody ? pending.body : null,
      }
      // Always present: it creates an uncommitted lineage, and a forked tail
      // keeps the base this text was written against.
      const claim = identity.current()
      entry.lineage = {
        createdAt: this.lineageCreatedAt,
        owner: { runtimeId: claim ? claim.runtimeId : null, pageInstanceId: identity.pageInstanceId, paneId: this.paneId },
        base: { ...this.base },
      }
      return entry
    }

    dispose(): void {
      this.released = true
      this.clearTimers()
      this.clearRetry()
    }
  }

  function openWriter(input: OpenWriterInput): DraftWriter {
    const writer = new WriterImpl(input)
    live.add(writer)
    return writer
  }

  /** Draft ids with an adoption in progress on this page. */
  const adopting = new Set<string>()

  async function adopt(
    record: DraftRecord,
    input: Omit<OpenWriterInput, 'kind' | 'entityType' | 'entityId' | 'base'>,
  ): Promise<DraftWriter | null> {
    // One live writer per lineage on this page: a second adoption of the same
    // draft, concurrent or after the first, is refused.
    if (disposed || adopting.has(record.draftId) || ownerOf(record.draftId) !== null) return null
    adopting.add(record.draftId)
    try {
      return await adoptReserved(record, input)
    } finally {
      adopting.delete(record.draftId)
    }
  }

  async function adoptReserved(
    record: DraftRecord,
    input: Omit<OpenWriterInput, 'kind' | 'entityType' | 'entityId' | 'base'>,
  ): Promise<DraftWriter | null> {
    const writer = new WriterImpl({
      ...input,
      kind: record.kind,
      entityType: record.entityType,
      entityId: record.entityId,
      base: record.base,
    })
    const claim = identity.current()
    const result = await store.adopt(record.draftId, record.owner.pageInstanceId, {
      runtimeId: claim ? claim.runtimeId : null,
      pageInstanceId: identity.pageInstanceId,
      paneId: input.paneId,
      claimedAt: now(),
    })
    if (result.outcome !== 'ok' || disposed) return null
    writer.adoptRecord(result.record)
    live.add(writer)
    return writer
  }

  return {
    openWriter,
    adopt,
    ownerOf,
    writers: () => [...live],
    leaveGuardActive: () => guardOn,
    emergencyListenersActive: () => emergencyOn,
    writeEmergencyNow,
    dispose() {
      // Stops timers and listeners. An emergency key already written stays:
      // it may hold the only copy of unsaved text.
      for (const w of live) w.dispose()
      live.clear()
      if (win) {
        win.removeEventListener('beforeunload', onBeforeUnload)
        win.removeEventListener('pagehide', onPageHide)
        win.removeEventListener('pageshow', onPageShow)
      }
      releaseReservation()
      if (doc) {
        doc.removeEventListener('visibilitychange', onVisibility)
        doc.removeEventListener('resume', onResume)
      }
      guardOn = false
      emergencyOn = false
      disposed = true
      identity.setLineageResponder(null)
    },
  }
}

function defaultLocalStorage(): EmergencyStorage | null {
  try {
    return typeof localStorage !== 'undefined' ? localStorage : null
  } catch {
    return null
  }
}
