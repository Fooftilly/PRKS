/**
 * One editor-recovery runtime per page: store, identity and writer registry.
 *
 * `start()` claims the runtime id and then merges emergency entries left by
 * pages that are no longer alive. `scanEmergency()` repeats that merge for a
 * page that is already running, so a tab that closes later is picked up
 * without a reload. Nothing starts at script load; slice 1 has
 * no consumer, so the page holds no lock, channel or listener until a
 * consumer asks for the runtime.
 */

import { mayHoldUnreadable, mergeEmergencyEntries, readEmergencyKeys, releaseDeadReservations, type EmergencyStorage, type MergeReport } from './emergency'
import { createPageIdentity, type IdentityEnv, type PageIdentity, type RuntimeClaim } from './identity'
import { classifyLineage, isAdoptable, type LineageClass } from './lineage'
import {
  entityKeyOf,
  isEarlierLibrary,
  isSupportedRecord,
  type DraftEntityType,
  type DraftKind,
  type DraftOwner,
  type DraftRecord,
  type RecoveryNames,
} from './schema'
import { createRecoveryStore, forkedDraftId, RecoveryStoreError, type AdoptOutcome, type DeleteOutcome, type RecoveryStore, type RecoveryStoreOptions } from './store'
import { createWriterRegistry, type WriterEvent, type WriterRegistry, type WriterRegistryOptions } from './writer'

/** What a reviewer saw of one record; an action on it applies only while it still holds. */
export interface ReviewedRecord {
  draftId: string
  pageInstanceId: string
  generation: number
  status: DraftRecord['status']
}

/** What one cleanup of a deleted entity's drafts did (#533). */
export interface EntityCleanupReport {
  /** Removed, or replaced by a tombstone that a stale emergency key still needs. */
  removed: string[]
  /** Kept: a live editor in this or another page owns the lineage. */
  live: string[]
  /**
   * Kept: its owner page is not proven gone (it holds its page lock, or is
   * frozen, busy or unreachable without one), so it may still be editing.
   */
  unknown: string[]
  /** Kept: its owner page, generation or status changed after it was read. */
  changed: string[]
  /**
   * Kept untouched: a record of this entity this code cannot read (a newer
   * schema), or one whose entity cannot be told. Only a reader that
   * understands it may remove it.
   */
  unsupported: string[]
  /** Lineages only an emergency key held, tombstoned so no merge creates them. */
  suppressed: string[]
  /**
   * Kept: typed against an earlier Library (#629, `isEarlierLibrary`), so the
   * deletion this page saw says nothing about it; review-only.
   */
  restored: string[]
}

export interface EditorRecoveryRuntimeOptions {
  /**
   * The signed-in browser scope's names (`recoveryNames(scopeKey)`). Required:
   * a page without a confirmed scope has no recovery runtime at all.
   */
  names: RecoveryNames
  store?: Omit<RecoveryStoreOptions, 'name'>
  identity?: Omit<IdentityEnv, 'names'>
  writers?: Omit<WriterRegistryOptions, 'store' | 'identity'>
  emergencyStorage?: EmergencyStorage | null
  /** How long `retire()` waits before reporting writes still pending. Default 10 s. */
  retireWaitMs?: number
  /**
   * The Library epoch the page loaded (#629, `prksBrowserScope.pageEpoch()`).
   * Writers stamp it on every base; drafts of any other epoch are review-only.
   */
  epoch?: string | null
}

/**
 * How a retirement ended: `closed` once every text typed before it is in this
 * scope's database and the database is closed; `pending` when some write had
 * not committed in time. A pending retirement keeps those writers, their
 * retries and the leave guard until the text commits, then closes.
 */
export type RetireOutcome = 'closed' | 'pending'

export interface EditorRecoveryRuntime {
  /** The browser scope whose recovery storage this runtime reads and writes. */
  readonly names: RecoveryNames
  /** The Library epoch the page loaded; null when it is not known (every draft is then review-only). */
  readonly epoch: string | null
  readonly store: RecoveryStore
  readonly identity: PageIdentity
  readonly writers: WriterRegistry
  /** Claims the runtime id, then merges dead pages' emergency entries. Idempotent. */
  start(): Promise<{ claim: RuntimeClaim; merged: MergeReport[] }>
  /**
   * Starts if needed, then merges emergency entries of pages that are dead now.
   * Live pages' keys are left alone, and so are pages whose liveness cannot be
   * established: only a later start() treats those as dead. Idempotent; scans
   * run one at a time.
   */
  scanEmergency(): Promise<MergeReport[]>
  classify(record: Pick<DraftRecord, 'draftId' | 'owner'>, askingSession?: string | null): Promise<LineageClass>
  /**
   * Takes ownership of a reviewed record for this page without a writer
   * (compare-and-set on its owner page and generation), so no other page can
   * adopt it while this page applies the user's choice. The record then
   * reads as this page's orphan until `discardReviewed` removes it.
   */
  claimReviewed(reviewed: ReviewedRecord, paneId: string): Promise<AdoptOutcome>
  /**
   * Explicit user discard of a reviewed record, only while it is unchanged.
   * When an emergency key that this page cannot clear still lists the
   * lineage, a tombstone stays in its place so a later merge never brings it back.
   */
  discardReviewed(reviewed: ReviewedRecord & Pick<DraftRecord, 'kind' | 'entityType' | 'entityId'>): Promise<DeleteOutcome>
  /**
   * Removes the drafts of an entity the server has confirmed deleted (#533),
   * for each of `kinds`. Merges dead pages' emergency entries first, then
   * removes only lineages whose owner is this page without a writer or a page
   * proven gone (`isAdoptable`), each by compare-and-set on the owner page,
   * generation and status it was classified with: a lineage adopted or
   * written since is kept. A live or unknown owner keeps its lineage, since
   * a frozen editor still holding the text cannot be told from a dead one; a
   * later run removes it once its page is proven gone. A stale emergency
   * key's lineages are tombstoned so no later merge brings them back.
   * Idempotent: run it again to retry.
   */
  cleanupDeletedEntity(entity: { entityType: DraftEntityType; entityId: string }, kinds: readonly DraftKind[]): Promise<EntityCleanupReport>
  /** Writer protection events (`unprotected`, `protected`, `ownership-lost`) for this page. */
  onWriterEvent(listener: (event: WriterEvent) => void): () => void
  /**
   * This runtime's view of localStorage: every recovery key it and its
   * diagnostics read or write goes through it, and it refuses every access
   * once the runtime is retired.
   */
  readonly emergencyStorage: EmergencyStorage | null
  /**
   * The browser scope was signed out or replaced (#629). At once, `store`,
   * `emergencyStorage` and every method of this runtime refuse, also through
   * handles taken before, and a read already in flight is refused when it
   * completes. Text typed before stays this account's: the writers take no
   * new edit, write the pending text once more to this scope's emergency
   * key and finish every pending write to this scope's database (a body too
   * large for the emergency key included); then they, the listeners, the
   * channel and the locks stop and the database closes for good. Nothing
   * stored is removed, and a write that does not commit is never dropped:
   * its writer keeps retrying and keeps the leave guard until it does.
   * Idempotent; resolves `closed`, or `pending` after `retireWaitMs` while a
   * write is still uncommitted.
   */
  retire(): Promise<RetireOutcome>
  isRetired(): boolean
  dispose(): void
}

function keptFor(lineage: LineageClass, report: EntityCleanupReport): string[] {
  return lineage === 'unknown' ? report.unknown : report.live
}

export function createEditorRecoveryRuntime(options: EditorRecoveryRuntimeOptions): EditorRecoveryRuntime {
  const names = options.names
  if (!names) throw new Error('recovery storage needs a signed-in scope')
  const epoch = options.epoch ?? null
  const rawStore = createRecoveryStore({ ...options.store, name: names.dbName })
  // What everything but the writers uses: refused from the moment of retirement.
  const store = retirableStore(rawStore, () => retired)
  const identity = createPageIdentity({ ...options.identity, names })
  /** Retired: nothing outside the writers' last writes reads or writes this scope. */
  let retired = false
  /** The writers' last writes are done: nothing reads or writes this scope. */
  let closed = false
  let retiring: Promise<RetireOutcome> | null = null
  const rawEmergencyStorage =
    options.emergencyStorage !== undefined ? options.emergencyStorage : (options.writers?.emergencyStorage ?? defaultLocalStorage())
  const emergencyStorage = rawEmergencyStorage ? retirableStorage(rawEmergencyStorage, () => retired) : null
  const writerStorage = rawEmergencyStorage ? retirableStorage(rawEmergencyStorage, () => closed) : null
  const writerListeners = new Set<(event: WriterEvent) => void>()
  const ownEvent = options.writers?.onEvent
  const writers = createWriterRegistry({
    ...options.writers,
    // The writers finish the text typed before a retirement, so they keep the
    // store until then; they take no new edit once it begins.
    store: rawStore,
    identity,
    emergencyStorage: writerStorage,
    epoch,
    onEvent(event) {
      if (ownEvent) ownEvent(event)
      for (const listener of [...writerListeners]) {
        try {
          listener(event)
        } catch {
          /* one listener never stops the others */
        }
      }
    },
  })
  let started: Promise<{ claim: RuntimeClaim; merged: MergeReport[] }> | null = null
  let lastScan: Promise<unknown> = Promise.resolve()

  // Serialized so two scans never apply the same key's entries concurrently.
  function merge(definiteOnly: boolean): Promise<MergeReport[]> {
    const run = lastScan.then(() => runMerge(definiteOnly))
    lastScan = run.catch(() => undefined)
    return run
  }

  async function runMerge(definiteOnly: boolean): Promise<MergeReport[]> {
    if (!emergencyStorage) return []
    const env = {
      storage: emergencyStorage,
      names,
      store,
      pageInstanceId: identity.pageInstanceId,
      isPageAlive: (id: string) => identity.isPageAlive(id),
      wasPageClosed: (id: string) => identity.wasPageClosed(id),
      definiteOnly,
    }
    const reports = await mergeEmergencyEntries(env)
    const claim = identity.current()
    const removed = await releaseDeadReservations({
      ...env,
      isPageGone: (id: string) => identity.isPageGone(id),
      // Only right after this load's claim: a channel claim is the absence of
      // an answer, and a later late answer demotes it anyway.
      verifiedRuntimeId: !definiteOnly && claim && claim.verified !== 'unverified' ? claim.runtimeId : null,
    })
    // A page taken for gone that is only busy or frozen writes it again.
    for (const key of removed) identity.announceReservationRemoved(key.slice(names.reservationPrefix.length))
    return reports
  }

  function start(): Promise<{ claim: RuntimeClaim; merged: MergeReport[] }> {
    if (started) return started
    started = identity.claim().then(async (claim) => ({ claim, merged: await merge(false) }))
    return started
  }

  function scanEmergency(): Promise<MergeReport[]> {
    if (!started) return start().then((result) => result.merged)
    return started.then(() => merge(true))
  }

  function classify(record: Pick<DraftRecord, 'draftId' | 'owner'>, askingSession: string | null = null): Promise<LineageClass> {
    return classifyLineage(record, { identity, localOwner: (id) => writers.ownerOf(id) }, askingSession)
  }

  function discardReviewed(reviewed: ReviewedRecord & Pick<DraftRecord, 'kind' | 'entityType' | 'entityId'>): Promise<DeleteOutcome> {
    const expected = { pageInstanceId: reviewed.pageInstanceId, generation: reviewed.generation, status: reviewed.status }
    const listing = emergencyStorage ? listingPage(emergencyStorage, names, reviewed.draftId) : null
    const tombstone = listing
      ? { kind: reviewed.kind, entityType: reviewed.entityType, entityId: reviewed.entityId, generation: reviewed.generation, pageInstanceId: listing }
      : undefined
    return store.discard(reviewed.draftId, tombstone, expected)
  }

  /**
   * A first generation that never reached IndexedDB exists only in a key
   * that no scan merged: tombstoned only when its page is proven gone.
   */
  async function suppressEmergencyOnly(
    ours: (kind: DraftKind, entityType: DraftEntityType, entityId: string) => boolean,
    report: EntityCleanupReport,
  ): Promise<void> {
    if (!emergencyStorage) return
    for (const stored of readEmergencyKeys(emergencyStorage, names)) {
      const payload = stored.payload
      if (!payload) {
        // A key a later read or a newer bundle may still recover could hold this entity: left alone and reported.
        if (mayHoldUnreadable(stored)) report.unsupported.push(stored.key)
        continue
      }
      for (const entry of payload.entries) {
        if (entry.committedGeneration !== 0 || entry.body === null || !ours(entry.kind, entry.entityType, entry.entityId)) continue
        if (isEarlierLibrary(entry.lineage ? entry.lineage.base : null, epoch)) {
          report.restored.push(entry.draftId)
          continue
        }
        const owner: DraftOwner = { runtimeId: payload.runtimeId, pageInstanceId: stored.pageInstanceId, paneId: '', claimedAt: payload.at }
        const lineage = await classify({ draftId: entry.draftId, owner })
        if (!isAdoptable(lineage)) {
          keptFor(lineage, report).push(entry.draftId)
          continue
        }
        const stone = { kind: entry.kind, entityType: entry.entityType, entityId: entry.entityId, generation: entry.generation, pageInstanceId: stored.pageInstanceId }
        if ((await store.tombstoneIfAbsent(entry.draftId, stone)) === 'suppressed') {
          report.suppressed.push(entry.draftId)
          continue
        }
        // A record of it landed after the listing (another tab merged the key): one more pass classifies it.
        const landed = await store.get(entry.draftId)
        if (landed && !isSupportedRecord(landed)) report.unsupported.push(entry.draftId)
        else if (landed && landed.status !== 'discarded') report.changed.push(entry.draftId)
      }
    }
  }

  async function cleanupDeletedEntity(
    entity: { entityType: DraftEntityType; entityId: string },
    kinds: readonly DraftKind[],
  ): Promise<EntityCleanupReport> {
    const report: EntityCleanupReport = { removed: [], live: [], unknown: [], changed: [], unsupported: [], suppressed: [], restored: [] }
    // A dead page's tail becomes a record first, so it is removed like any other.
    await scanEmergency()
    const keys = new Set(kinds.map((kind) => entityKeyOf(kind, entity.entityId)))
    const ours = (kind: DraftKind, entityType: DraftEntityType, entityId: string) =>
      entityType === entity.entityType && entityId === entity.entityId && keys.has(entityKeyOf(kind, entityId))
    const records: DraftRecord[] = []
    for (const row of await store.listAll()) {
      if (isSupportedRecord(row)) {
        if (row.status !== 'discarded' && ours(row.kind, row.entityType, row.entityId)) records.push(row)
        continue
      }
      const r = (row || {}) as Partial<Record<'draftId' | 'entityType' | 'entityId', unknown>>
      const told = typeof r.entityType === 'string' && typeof r.entityId === 'string'
      if (!told || (r.entityType === entity.entityType && r.entityId === entity.entityId)) {
        report.unsupported.push(typeof r.draftId === 'string' ? r.draftId : '')
      }
    }
    for (const record of records) {
      if (isEarlierLibrary(record.base, epoch)) {
        report.restored.push(record.draftId)
        continue
      }
      const lineage = await classify(record)
      if (!isAdoptable(lineage)) {
        keptFor(lineage, report).push(record.draftId)
        continue
      }
      const outcome = await discardReviewed({
        draftId: record.draftId,
        pageInstanceId: record.owner.pageInstanceId,
        generation: record.generation,
        status: record.status,
        kind: record.kind,
        entityType: record.entityType,
        entityId: record.entityId,
      })
      if (outcome === 'deleted' || outcome === 'missing') report.removed.push(record.draftId)
      else if (outcome === 'kept') report.changed.push(record.draftId)
      // Rewritten meanwhile by a newer schema: left alone and reported like any unreadable row.
      else report.unsupported.push(record.draftId)
    }
    await suppressEmergencyOnly(ours, report)
    return report
  }

  return {
    names,
    epoch,
    store,
    identity,
    writers,
    start,
    scanEmergency,
    classify,
    claimReviewed(reviewed, paneId) {
      if (reviewed.status !== 'active') return Promise.resolve({ outcome: 'conflict' as const })
      const claim = identity.current()
      const owner: DraftOwner = {
        runtimeId: claim ? claim.runtimeId : null,
        pageInstanceId: identity.pageInstanceId,
        paneId,
        claimedAt: Date.now(),
      }
      return store.adopt(reviewed.draftId, reviewed.pageInstanceId, owner, reviewed.generation)
    },
    discardReviewed,
    cleanupDeletedEntity,
    onWriterEvent(listener) {
      if (retired) return () => undefined
      writerListeners.add(listener)
      return () => {
        writerListeners.delete(listener)
      }
    },
    emergencyStorage,
    retire() {
      if (retiring) return retiring
      retired = true
      writerListeners.clear()
      // Only once every text typed before is committed: until then the
      // writers keep their retries, the leave guard and this page's identity.
      const closing = writers.retire().then(() => {
        writers.dispose()
        identity.dispose()
        closed = true
        rawStore.retire()
      })
      retiring = settledWithin(closing, options.retireWaitMs ?? RETIRE_WAIT_MS)
      return retiring
    },
    isRetired: () => retired,
    dispose() {
      writerListeners.clear()
      writers.dispose()
      identity.dispose()
      rawStore.close()
    },
  }
}

/** The page whose emergency key still lists `draftId` (or its fork), if any. */
function listingPage(storage: EmergencyStorage, names: RecoveryNames, draftId: string): string | null {
  for (const stored of readEmergencyKeys(storage, names)) {
    const entries = stored.payload ? stored.payload.entries : []
    if (entries.some((entry) => entry.draftId === draftId || forkedDraftId(entry.draftId, entry.generation) === draftId)) {
      return stored.pageInstanceId
    }
  }
  return null
}

/** How long a retirement waits for the writers' last writes before reporting them pending. */
const RETIRE_WAIT_MS = 10000

function settledWithin(work: Promise<void>, ms: number): Promise<RetireOutcome> {
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve('pending'), ms)
    void work.then(() => {
      clearTimeout(timer)
      resolve('closed')
    })
  })
}

function retiredError(): RecoveryStoreError {
  return new RecoveryStoreError('retired', 'This account is signed out of recovery storage.')
}

/**
 * `store`, refusing every operation once `isRetired()` holds, and refusing
 * the result of one that was in flight then: nothing read before a
 * retirement is handed out after it.
 */
function retirableStore(store: RecoveryStore, isRetired: () => boolean): RecoveryStore {
  function guard<A extends unknown[], R>(fn: (...args: A) => Promise<R>): (...args: A) => Promise<R> {
    return (...args: A) => {
      if (isRetired()) return Promise.reject(retiredError())
      return fn(...args).then((value) => {
        if (isRetired()) throw retiredError()
        return value
      })
    }
  }
  return {
    writeGeneration: guard(store.writeGeneration),
    updateLineage: guard(store.updateLineage),
    adopt: guard(store.adopt),
    get: guard(store.get),
    getBody: guard(store.getBody),
    listByEntity: guard(store.listByEntity),
    listAll: guard(store.listAll),
    deleteIfAcknowledged: guard(store.deleteIfAcknowledged),
    deleteIfEqual: guard(store.deleteIfEqual),
    discard: guard(store.discard),
    clearTombstone: guard(store.clearTombstone),
    tombstoneIfAbsent: guard(store.tombstoneIfAbsent),
    applyEmergencyEntry: guard(store.applyEmergencyEntry),
    lastDurability: () => store.lastDurability(),
    close: () => store.close(),
    retire: () => store.retire(),
    isRetired: () => isRetired() || store.isRetired(),
  }
}

/** `storage`, refusing every access once `isRetired()` holds. */
function retirableStorage(storage: EmergencyStorage, isRetired: () => boolean): EmergencyStorage {
  function guard(): EmergencyStorage {
    if (isRetired()) throw new Error('This account is signed out of recovery storage.')
    return storage
  }
  return {
    get length() {
      return guard().length
    },
    key: (index: number) => guard().key(index),
    getItem: (key: string) => guard().getItem(key),
    setItem: (key: string, value: string) => guard().setItem(key, value),
    removeItem: (key: string) => guard().removeItem(key),
  }
}

function defaultLocalStorage(): EmergencyStorage | null {
  try {
    return typeof localStorage !== 'undefined' ? localStorage : null
  } catch {
    return null
  }
}
