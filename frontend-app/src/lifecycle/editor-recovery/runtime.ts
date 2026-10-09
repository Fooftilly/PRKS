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
  RESERVATION_KEY_PREFIX,
  entityKeyOf,
  isSupportedRecord,
  type DraftEntityType,
  type DraftKind,
  type DraftOwner,
  type DraftRecord,
} from './schema'
import { createRecoveryStore, forkedDraftId, type AdoptOutcome, type DeleteOutcome, type RecoveryStore, type RecoveryStoreOptions } from './store'
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
}

export interface EditorRecoveryRuntimeOptions {
  store?: RecoveryStoreOptions
  identity?: IdentityEnv
  writers?: Omit<WriterRegistryOptions, 'store' | 'identity'>
  emergencyStorage?: EmergencyStorage | null
}

export interface EditorRecoveryRuntime {
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
  dispose(): void
}

function keptFor(lineage: LineageClass, report: EntityCleanupReport): string[] {
  return lineage === 'unknown' ? report.unknown : report.live
}

export function createEditorRecoveryRuntime(options: EditorRecoveryRuntimeOptions = {}): EditorRecoveryRuntime {
  const store = createRecoveryStore(options.store)
  const identity = createPageIdentity(options.identity)
  const emergencyStorage =
    options.emergencyStorage !== undefined ? options.emergencyStorage : (options.writers?.emergencyStorage ?? defaultLocalStorage())
  const writerListeners = new Set<(event: WriterEvent) => void>()
  const ownEvent = options.writers?.onEvent
  const writers = createWriterRegistry({
    ...options.writers,
    store,
    identity,
    emergencyStorage,
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
    for (const key of removed) identity.announceReservationRemoved(key.slice(RESERVATION_KEY_PREFIX.length))
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
    const listing = emergencyStorage ? listingPage(emergencyStorage, reviewed.draftId) : null
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
    for (const stored of readEmergencyKeys(emergencyStorage)) {
      const payload = stored.payload
      if (!payload) {
        // A key a later read or a newer bundle may still recover could hold this entity: left alone and reported.
        if (mayHoldUnreadable(stored)) report.unsupported.push(stored.key)
        continue
      }
      for (const entry of payload.entries) {
        if (entry.committedGeneration !== 0 || entry.body === null || !ours(entry.kind, entry.entityType, entry.entityId)) continue
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
    const report: EntityCleanupReport = { removed: [], live: [], unknown: [], changed: [], unsupported: [], suppressed: [] }
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
      writerListeners.add(listener)
      return () => {
        writerListeners.delete(listener)
      }
    },
    dispose() {
      writerListeners.clear()
      writers.dispose()
      identity.dispose()
      store.close()
    },
  }
}

/** The page whose emergency key still lists `draftId` (or its fork), if any. */
function listingPage(storage: EmergencyStorage, draftId: string): string | null {
  for (const stored of readEmergencyKeys(storage)) {
    const entries = stored.payload ? stored.payload.entries : []
    if (entries.some((entry) => entry.draftId === draftId || forkedDraftId(entry.draftId, entry.generation) === draftId)) {
      return stored.pageInstanceId
    }
  }
  return null
}

function defaultLocalStorage(): EmergencyStorage | null {
  try {
    return typeof localStorage !== 'undefined' ? localStorage : null
  } catch {
    return null
  }
}
